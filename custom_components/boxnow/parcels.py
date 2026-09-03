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

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

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
# * ``_STATE_MAP`` — the parcel's top-level ``state`` field. ``status`` on the
#   normalised parcel is keyed off this alone, never off the newest event.
#   Only ``delivered``/``missing`` are live-confirmed; every other value
#   (including a hypothetical pickup-ready one) falls to ``unknown`` with a
#   warning until a real sample confirms it.
# * ``_EVENT_TYPE_MAP`` — each history entry's ``type`` field. These feed only
#   the per-event ``history`` status, not the top-level parcel ``status``.
#   ``new``, ``in-depot``, ``final-destination``, ``delivered`` and ``missing``
#   are live-confirmed (real households' event logs, 2026-09); ``missing`` as
#   an event.type mirrors the top-level "missing" state 1:1 — the same
#   parcel's final event carried it. ``accepted-to-locker`` is also
#   live-confirmed but its meaning is inferred, not documented anywhere: on a
#   parcel that ended up "missing", it stood in the position ``final-destination``
#   normally occupies (right before the terminal event, no ``in-depot`` step),
#   so it is treated as an alternate "reached a locker" event and mapped the
#   same as ``final-destination``.
_STATE_MAP: dict[str, ParcelStatus] = {
    "delivered": ParcelStatus.DELIVERED,
    "missing": ParcelStatus.PROBLEM,  # terminal non-delivery outcome, not "unknown"
}

_EVENT_TYPE_MAP: dict[str, ParcelStatus] = {
    "new": ParcelStatus.REGISTERED,
    "in-depot": ParcelStatus.IN_TRANSIT,
    "final-destination": ParcelStatus.AT_PICKUP_POINT,
    "accepted-to-locker": ParcelStatus.AT_PICKUP_POINT,
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
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
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
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
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
        "text": f"{int(length)} x {int(width)} x {int(height)} cm",
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


def _newest_event_timestamp(events: list | None) -> str | None:
    """Return the newest parseable ``createTime`` among ``events``, if any."""
    parsed: list[tuple[datetime, str]] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("createTime"))
        if not timestamp:
            continue
        moment = parse_iso(timestamp)
        if moment is not None:
            parsed.append((moment, timestamp))
    if not parsed:
        return None
    return max(parsed, key=lambda item: item[0])[1]


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


def tracking_url(tracking_code: str | None, country: str = DEFAULT_COUNTRY) -> str | None:
    """Construct the consumer tracking deep-link for a parcel.

    Every country in ``BOXNOW_TRACKING_URLS`` was confirmed to serve the
    identical API payload shape (see const.py), so the deep-link template is
    the only thing that varies by country.
    """
    if not tracking_code:
        return None
    template = BOXNOW_TRACKING_URLS.get(country, BOXNOW_TRACKING_URLS[DEFAULT_COUNTRY])
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


def normalize_parcel(
    raw: dict, *, country: str = DEFAULT_COUNTRY, include_history: bool = False
) -> dict:
    """Return a carrier-agnostic parcel dict with a redacted payload under ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order, and the aggregator and
    cross-carrier dashboards depend on it. Set a key to ``None`` when the
    carrier does not expose it — never omit it.

    BoxNow specifics:

    * ``status`` comes from the top-level ``state`` only (:func:`map_parcel_status`),
      never from the newest event — the event vocabulary is unconfirmed and
      feeds ``history`` alone.
    * ``barcode`` is the user-supplied ``parcelId`` (tagged onto ``raw`` by the
      coordinator before this call), corroborated — never overridden — by the
      response's own ``id`` when present; a disagreement is logged once, not
      raised.
    * ``pickup``/``pickup_point`` are gated on ``status is AT_PICKUP_POINT``
      (suite convention, matching GLS/DHL-NL/Slovenská Pošta): when true,
      ``pickup_point`` reads the newest event's ``locationDisplayName``
      (live-confirmed field, 2026-09 sample batch). In practice this stays
      ``None`` today — no top-level ``state`` value has ever been observed
      to map to ``AT_PICKUP_POINT`` (see ``_STATE_MAP``), only the separate
      ``event.type`` vocabulary reaches it, and that only feeds ``history``
      — but this wiring needs no further change the moment a real
      locker-ready ``state`` value is confirmed, mirroring how the
      ``awaiting_pickup`` sensor is already wired ahead of that.
    * ``sender``/``receiver``/``weight``/``dimensions``/``planned_from``/
      ``planned_to`` have no confirmed source field and stay ``None``.
    * ``url`` uses the deep-link template for ``country`` (the config entry's
      selected backend); every supported country was confirmed to return the
      identical payload shape, so nothing else in this function varies by
      country.
    * ``raw`` is an **allowlist**, not the full payload — the confirmed
      payload carries payment and service-related fields this integration
      must never surface. Only ``id``, ``state`` and a redacted event list
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
    status = map_parcel_status(state)
    delivered = status is ParcelStatus.DELIVERED

    events = raw.get("events") or []

    is_pickup = status is ParcelStatus.AT_PICKUP_POINT
    pickup_point = None
    if is_pickup:
        newest = _newest_event(events)
        pickup_point = (newest or {}).get("locationDisplayName") or None

    return {
        "carrier": "BoxNow",
        "barcode": barcode,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": state,
        "delivered": delivered,
        "delivered_at": _newest_event_timestamp(events) if delivered else None,
        "planned_from": None,
        "planned_to": None,
        "pickup": is_pickup,
        "pickup_point": pickup_point,
        "url": tracking_url(barcode, country),
        "weight": None,
        "dimensions": None,
        "history": build_history(events) if include_history else None,
        "raw": {
            "id": reported_id,
            "state": state,
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
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
