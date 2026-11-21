"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

The carrier-specific parts are :data:`_STATE_MAP`, :data:`_EVENT_TYPE_MAP` and
:func:`normalize_parcel`. Everything else — the timestamp parsing, the
history builder, the sort contract, the delivered filter, the one-shot
warning for unmapped statuses — is suite-wide machinery and should be left
alone.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo
import logging
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    BOXNOW_TRACKING_URLS,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_COUNTRY,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    ParcelStatus,
    normalize_country,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-boxnow/issues/new"
    "?template=unrecognised_status.yml"
)

# BoxNow carries *two* separate vocabularies — never merge them into
# one map:
#
# * ``_STATE_MAP`` — the parcel's top-level ``state`` field, which drives
#   ``status`` first. ``in-transit`` and ``in-final-destination`` (waiting in
#   the destination locker) are the in-flight values; ``delivered`` and
#   ``missing`` the terminal ones.
# * ``_EVENT_TYPE_MAP`` — each history entry's ``type`` field: every
#   ``history`` entry, and the ``status`` fallback when ``state`` is unmapped.
#   ``accepted-to-locker`` is the *sender* dropping the parcel into a locker or
#   store to start its journey, so it is ``in_transit`` — not a parcel waiting
#   for the recipient. Only ``final-destination`` means ready for collection
#   at the recipient's locker.
_STATE_MAP: dict[str, ParcelStatus] = {
    "in-transit": ParcelStatus.IN_TRANSIT,
    "in-final-destination": ParcelStatus.AT_PICKUP_POINT,
    "delivered": ParcelStatus.DELIVERED,
    "missing": ParcelStatus.PROBLEM,  # terminal non-delivery outcome, not "unknown"
}

_EVENT_TYPE_MAP: dict[str, ParcelStatus] = {
    "new": ParcelStatus.REGISTERED,
    "in-depot": ParcelStatus.IN_TRANSIT,
    "in-transit": ParcelStatus.IN_TRANSIT,
    "final-destination": ParcelStatus.AT_PICKUP_POINT,
    "in-final-destination": ParcelStatus.AT_PICKUP_POINT,
    "accepted-to-locker": ParcelStatus.IN_TRANSIT,
    "delivered": ParcelStatus.DELIVERED,
    "missing": ParcelStatus.PROBLEM,
}

# (vocabulary, raw value) pairs we have already warned about, so each is
# logged only once per HA session instead of on every poll. Prefixed by
# vocabulary so a value that is unmapped in one table but mapped in the other
# (they overlap in spelling, not in meaning) doesn't get logged past the
# first one-shot in error.
_unmapped_logged: set[tuple[str, str]] = set()


def _warn_unmapped(vocabulary: str, code: str) -> None:
    """Log an unmapped carrier value once, with a copy-paste issue link."""
    key = (vocabulary, code)
    if key in _unmapped_logged:
        return
    _unmapped_logged.add(key)
    _LOGGER.warning(
        "Unrecognised BoxNow %s — help us map it. Open an issue "
        "and paste this line: %s\n  %s=%s → reported as 'unknown'",
        vocabulary,
        NEW_ISSUE_URL,
        vocabulary,
        code,
    )


def map_parcel_status(state: str | None) -> ParcelStatus:
    """Map the parcel's top-level ``state`` to a canonical :class:`ParcelStatus`.

    ``None`` (a not-yet-scanned parcel) reports ``unknown`` silently; an
    unrecognised value reports ``unknown`` with a one-shot warning.
    """
    if not state:
        return ParcelStatus.UNKNOWN
    mapped = _STATE_MAP.get(state)
    if mapped is not None:
        return mapped
    _warn_unmapped("state", state)
    return ParcelStatus.UNKNOWN


def resolve_status(state: str | None, events: list | None) -> ParcelStatus:
    """Return the parcel status: ``state``, else the newest event, else ``unknown``.

    The newest event's type is the fallback; only when neither maps does an
    unrecognised ``state`` warn.
    """
    mapped = _STATE_MAP.get(state) if state else None
    if mapped is not None:
        return mapped
    newest = _newest_event(events)
    from_event = _EVENT_TYPE_MAP.get((newest or {}).get("type") or "")
    if from_event is not None:
        return from_event
    return map_parcel_status(state)


def map_event_status(event_type: str | None) -> ParcelStatus | None:
    """Map one history event's ``type`` to a canonical status, or ``None``.

    Unmapped values keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to
    unknown") and warn once, using the event-vocabulary one-shot set.
    """
    if not event_type:
        return None
    mapped = _EVENT_TYPE_MAP.get(event_type)
    if mapped is not None:
        return mapped
    _warn_unmapped("event.type", event_type)
    return None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — the common case for the
    consumer APIs in this suite. Strings pass through untouched; their
    consumers are guarded by :func:`parse_iso`. Adjust the numeric branch if
    your carrier stamps in seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{round(length)} x {round(width)} x {round(height)} cm",
    }


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from the carrier's event list.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. ``raw_status`` is the literal ``event.type``
    (BoxNow has no separate human-readable event text). Sorted oldest →
    newest and capped to the most recent ``max_events``.
    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("createTime"))
        if not timestamp:
            continue
        entry = {
            "timestamp": timestamp,
            "status": map_event_status(event.get("type")),
            "raw_status": event.get("type"),
        }
        parsed = parse_iso(timestamp)
        if parsed is None:
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


def _newest_event(events: list | None) -> dict | None:
    """Return the newest event by ``createTime``, or ``None`` if none parse."""
    dated: list[tuple[datetime, dict]] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("createTime"))
        if not timestamp:
            continue
        moment = parse_iso(timestamp)
        if moment is not None:
            dated.append((moment, event))
    if not dated:
        return None
    return max(dated, key=lambda item: item[0])[1]


# etaDate is UTC and lands late in the evening before the delivery day, so the
# day only comes out right in the country's own time zone.
_COUNTRY_TZ = {
    "gr": ZoneInfo("Europe/Athens"),
    "cy": ZoneInfo("Asia/Nicosia"),
    "bg": ZoneInfo("Europe/Sofia"),
    "hr": ZoneInfo("Europe/Zagreb"),
}


def delivery_window(
    eta: Any, country: str = DEFAULT_COUNTRY
) -> tuple[str | None, str | None]:
    """Return the whole day ``etaDate`` names; its time of day is no estimate."""
    parsed = parse_iso(eta) if isinstance(eta, str) else None
    if parsed is None:
        return None, None
    zone = _COUNTRY_TZ[normalize_country(country)]
    return planned_window(parsed.astimezone(zone).date(), None, zone=zone)


def tracking_url(
    tracking_code: str | None, country: str = DEFAULT_COUNTRY
) -> str | None:
    """Construct the consumer tracking deep-link for a parcel.

    Every country in ``BOXNOW_TRACKING_URLS`` was confirmed to serve the
    identical API payload shape (see const.py), so the deep-link template is
    the only thing that varies by country.
    """
    if not tracking_code:
        return None
    template = BOXNOW_TRACKING_URLS[normalize_country(country)]
    return template.format(tracking_code=tracking_code)


# Barcode mismatches we've already warned about, so a persistently
# disagreeing parcel doesn't flood the log on every poll.
_barcode_mismatches_logged: set[tuple[str, str]] = set()


def _warn_barcode_mismatch(queried: str, reported: str) -> None:
    """Warn once when the API's own ``id`` disagrees with the queried code."""
    key = (queried, reported)
    if key in _barcode_mismatches_logged:
        return
    _barcode_mismatches_logged.add(key)
    _LOGGER.warning(
        "BoxNow parcel id %s in the response does not match the "
        "tracking code %s it was requested with",
        reported,
        queried,
    )


def _moment(value: Any, zone: tzinfo) -> tuple[datetime, bool, str] | None:
    """Return ``(aware datetime, date_only, text)`` for a carrier time, or ``None``.

    A naive time or a bare date gets ``zone``; an aware time keeps its offset
    and, when it came in as a string, its own spelling.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
        return moment, True, moment.isoformat()
    elif isinstance(value, str) and value:
        try:
            if len(value) == 10:
                moment = datetime.combine(
                    date.fromisoformat(value), time.min, tzinfo=zone
                )
                return moment, True, moment.isoformat()
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed, False, value
    else:
        return None
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return moment, False, moment.isoformat()


def _end_of_day(moment: datetime, zone: tzinfo) -> datetime:
    local = moment.astimezone(zone)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)


def planned_window(
    start: Any, end: Any, *, zone: tzinfo
) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)`` in the canonical window shape.

    ``start`` and ``end`` are ISO strings, dates or datetimes, or ``None``; an
    epoch number must be converted first (see ``to_iso_timestamp``). A window
    keeps both ends. One moment with a time ends at ``23:59:59`` the same local
    day; a date covers ``00:00:00`` to ``23:59:59``; two dates run from the
    first day's start to the last day's end. Equal values are one moment,
    reversed values are swapped, and ``planned_to`` is never set without
    ``planned_from``. ``zone`` is the carrier's own zone: it dates naive times
    and bare dates and decides which day a moment ends on, so a ``Z`` moment
    still ends on the carrier's local day. When nothing is left of that day
    after the moment, there is no ``planned_to``.
    """
    first = _moment(start, zone)
    last = _moment(end, zone)
    if first is None:
        first, last = last, None
    if first is None:
        return None, None

    def closing(moment: tuple[datetime, bool, str]) -> datetime:
        return _end_of_day(moment[0], zone) if moment[1] else moment[0]

    if last is not None and closing(last) < first[0]:
        first, last = last, first
    if last is not None and closing(last) == first[0]:
        last = None

    if last is None:
        end_of_day = _end_of_day(first[0], zone)
        return first[2], end_of_day.isoformat() if end_of_day > first[0] else None
    return first[2], closing(last).isoformat() if last[1] else last[2]


_NO_WINDOW_STATUSES = frozenset(
    {ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED}
)


def planned_for_status(
    status: ParcelStatus, planned_from: str | None, planned_to: str | None
) -> tuple[str | None, str | None]:
    """Empty the delivery window once the parcel is at its destination or done."""
    if status in _NO_WINDOW_STATUSES:
        return None, None
    return planned_from, planned_to


def _newest_event_with_status(events: list, status: ParcelStatus) -> dict | None:
    """Return the newest event whose type maps to ``status``, or ``None``."""
    return _newest_event(
        [
            event
            for event in events
            if isinstance(event, dict)
            and _EVENT_TYPE_MAP.get(event.get("type") or "") is status
        ]
    )


def normalize_parcel(
    raw: dict, *, country: str = DEFAULT_COUNTRY, include_history: bool = False
) -> dict:
    """Return a carrier-agnostic parcel dict with a redacted payload under ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order, and the aggregator and
    cross-carrier dashboards depend on it. Set a key to ``None`` when the
    carrier does not expose it — never omit it.

    BoxNow specifics:

    * ``status`` comes from the top-level ``state``, and from the newest
      event's ``type`` when ``state`` is unmapped (:func:`resolve_status`).
    * ``barcode`` is the user-supplied ``parcelId`` (tagged onto ``raw`` by the
      coordinator before this call), corroborated — never overridden — by the
      response's own ``id`` when present; a disagreement is logged once, not
      raised.
    * ``pickup`` is always true: BoxNow only delivers to lockers.
      ``pickup_point`` is the destination locker's ``locationDisplayName``
      from the arrival event, ``None`` until the parcel has reached it.
    * ``delivered_at`` is the ``delivered`` event's timestamp.
    * ``planned_from``/``planned_to`` are the whole day ``etaDate`` names, in
      the country's time zone, until the parcel is at the locker or delivered.
    * ``sender``/``receiver``/``weight``/``dimensions`` have no source field and
      stay ``None``.
    * ``url`` uses the deep-link template for ``country`` (the config entry's
      selected backend); every supported country was confirmed to return the
      identical payload shape, so nothing else in this function varies by
      country.
    * ``raw`` is an **allowlist**, not the full payload — the confirmed
      payload carries payment and service-related fields this integration
      must never surface. Only ``id``, ``state``, ``etaDate`` and a redacted
      event list
      (``createTime``, ``type``, ``postalCode``, ``locationDisplayName``)
      are retained. ``locationDisplayName`` is a locker/hub branch name
      (e.g. "BOX NOW - Sofia"), not recipient PII — still listed in
      ``diagnostics.TO_REDACT`` out of caution, but safe to surface on the
      entity's own attribute.
    """
    queried_code = raw.get("parcelId")
    reported_id = raw.get("id")
    if queried_code and reported_id and queried_code != reported_id:
        _warn_barcode_mismatch(queried_code, reported_id)
    barcode = queried_code or reported_id

    state = raw.get("state")
    events = raw.get("events") or []
    status = resolve_status(state, events)
    delivered = status is ParcelStatus.DELIVERED

    planned_from, planned_to = planned_for_status(
        status, *delivery_window(raw.get("etaDate"), country)
    )

    arrival = _newest_event_with_status(events, ParcelStatus.AT_PICKUP_POINT)
    pickup_point = (arrival or {}).get("locationDisplayName") or None
    delivered_at = None
    if delivered:
        delivery = _newest_event_with_status(events, ParcelStatus.DELIVERED)
        delivered_at = to_iso_timestamp((delivery or {}).get("createTime"))

    return {
        "carrier": "BoxNow",
        "barcode": barcode,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": state,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": True,
        "pickup_point": pickup_point,
        "url": tracking_url(barcode, country),
        "weight": None,
        "dimensions": None,
        "history": build_history(events) if include_history else None,
        "raw": {
            "id": reported_id,
            "state": state,
            "etaDate": raw.get("etaDate"),
            "events": [
                {
                    "createTime": event.get("createTime"),
                    "type": event.get("type"),
                    "postalCode": event.get("postalCode"),
                    "locationDisplayName": event.get("locationDisplayName"),
                }
                for event in events
                if isinstance(event, dict)
            ],
        },
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(UTC) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
