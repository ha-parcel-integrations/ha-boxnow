"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping (the part you
rewrite per carrier) can be tested as plain functions.
"""
from datetime import datetime, timedelta, timezone

from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.boxnow.parcels as parcels_module
from custom_components.boxnow.const import (
    BOXNOW_TRACKING_URLS,
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.boxnow.parcels import (
    apply_delivered_filter,
    build_history,
    format_dimensions,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
    tracking_url,
)

from .payloads import (
    active_sample,
    delivered_sample,
    event,
    missing_sample,
    pickup_sample,
)

# ---------------------------------------------------------------------------
# map_parcel_status (top-level `state` vocabulary)
# ---------------------------------------------------------------------------


def test_map_parcel_status_delivered_and_missing_are_live_confirmed():
    assert map_parcel_status("delivered") == ParcelStatus.DELIVERED
    assert map_parcel_status("missing") == ParcelStatus.PROBLEM


def test_map_parcel_status_missing_is_unknown():
    assert map_parcel_status(None) == ParcelStatus.UNKNOWN
    assert map_parcel_status("") == ParcelStatus.UNKNOWN


def test_map_parcel_status_unconfirmed_event_type_values_are_unknown():
    """The `state` and `event.type` vocabularies are separate — `state` never

    inherits the reconstructed event.type mappings."""
    assert map_parcel_status("new") == ParcelStatus.UNKNOWN
    assert map_parcel_status("in-depot") == ParcelStatus.UNKNOWN
    assert map_parcel_status("final-destination") == ParcelStatus.UNKNOWN


def test_map_parcel_status_unmapped_is_unknown():
    assert map_parcel_status("TELEPORTED") == ParcelStatus.UNKNOWN


def test_map_parcel_status_unmapped_warns_only_once(caplog):
    assert map_parcel_status("teleported-state") == ParcelStatus.UNKNOWN
    assert map_parcel_status("teleported-state") == ParcelStatus.UNKNOWN
    assert caplog.text.count("teleported-state") == 1
    assert "issues/new" in caplog.text


# ---------------------------------------------------------------------------
# map_event_status (`event.type` vocabulary — live-confirmed 2026-09)
# ---------------------------------------------------------------------------


def test_map_event_status_confirmed_values():
    assert map_event_status("new") == ParcelStatus.REGISTERED
    assert map_event_status("in-depot") == ParcelStatus.IN_TRANSIT
    assert map_event_status("final-destination") == ParcelStatus.AT_PICKUP_POINT
    assert map_event_status("accepted-to-locker") == ParcelStatus.AT_PICKUP_POINT
    assert map_event_status("delivered") == ParcelStatus.DELIVERED
    assert map_event_status("missing") == ParcelStatus.PROBLEM


def test_map_event_status_none_and_unmapped_are_none():
    """History keeps ``null`` rather than ``unknown`` so consumers can tell
    "no mapping" from "mapped to unknown"."""
    assert map_event_status(None) is None
    assert map_event_status("teleported-event") is None


def test_map_event_status_unmapped_warns_only_once(caplog):
    assert map_event_status("teleported-event") is None
    assert map_event_status("teleported-event") is None
    assert caplog.text.count("teleported-event") == 1
    assert "issues/new" in caplog.text


def test_state_and_event_vocabularies_warn_independently(caplog):
    """A value unmapped in both tables is logged once per table, not once total."""
    assert map_parcel_status("shared-unmapped") == ParcelStatus.UNKNOWN
    assert map_event_status("shared-unmapped") is None
    assert caplog.text.count("shared-unmapped") == 2


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_converts_epoch_milliseconds():
    assert to_iso_timestamp(1784203767167) == "2026-07-16T12:09:27.167000+00:00"
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42Z"
    assert to_iso_timestamp(None) is None
    assert to_iso_timestamp(10**20) is None  # out of range -> None, never raises


def test_format_dimensions_needs_all_three_axes():
    assert format_dimensions(30, 20, 10) == {
        "length": 30,
        "width": 20,
        "height": 10,
        "text": "30 x 20 x 10 cm",
    }
    assert format_dimensions(30, None, 10) is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_orders_oldest_to_newest():
    history = build_history(delivered_sample()["events"])
    assert len(history) == 3
    assert history[0]["raw_status"] == "new"
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[-1]["status"] == ParcelStatus.DELIVERED
    assert history[-1]["raw_status"] == "delivered"


def test_build_history_caps_to_max_events():
    events = [
        event("in-depot", f"2026-04-{day:02d}T10:00:00Z") for day in range(1, 26)
    ]
    assert len(build_history(events, max_events=20)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"type": "in-depot"}]) == []  # no createTime
    assert build_history(["not-a-dict"]) == []


def test_build_history_keeps_unparseable_timestamp_last():
    history = build_history(
        [
            event("new", "2026-04-24T10:00:00Z"),
            {"createTime": "not-a-date", "type": "in-depot"},
        ]
    )
    assert [entry["raw_status"] for entry in history] == ["new", "in-depot"]


def test_build_history_out_of_order_input_still_sorts():
    """Events are not guaranteed to arrive in timeline order."""
    history = build_history(
        [
            event("delivered", "2026-04-29T13:12:42Z"),
            event("new", "2026-04-27T23:03:58Z"),
            event("in-depot", "2026-04-28T15:52:17Z"),
        ]
    )
    assert [entry["raw_status"] for entry in history] == ["new", "in-depot", "delivered"]


def test_build_history_absent_optional_keys_do_not_crash():
    """A history event missing locationDisplayName/postalCode is still usable."""
    history = build_history([{"createTime": "2026-04-24T10:00:00Z", "type": "new"}])
    assert history == [
        {
            "timestamp": "2026-04-24T10:00:00Z",
            "status": ParcelStatus.REGISTERED,
            "raw_status": "new",
        }
    ]


def test_build_history_unmapped_event_type_keeps_status_null():
    history = build_history([event("teleported", "2026-04-24T10:00:00Z")])
    assert history[0]["status"] is None
    assert history[0]["raw_status"] == "teleported"


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    """Every declared CAPABILITIES entry must come true somewhere in a sample.

    Copy this test into a real carrier's own test_parcels.py verbatim — it
    stays correct for whatever subset of CAPABILITIES that carrier declares.
    """
    delivered = normalize_parcel(delivered_sample())
    active = normalize_parcel(active_sample())
    pickup = normalize_parcel(pickup_sample())
    with_history = normalize_parcel(delivered_sample(), include_history=True)

    if "weight" in CAPABILITIES:
        assert delivered["weight"] is not None
    if "dimensions" in CAPABILITIES:
        assert delivered["dimensions"] is not None
    if "delivery_window" in CAPABILITIES:
        assert active["planned_from"] is not None or active["planned_to"] is not None
    if "pickup_point" in CAPABILITIES:
        assert pickup["pickup_point"] is not None
    if "url" in CAPABILITIES:
        assert delivered["url"] is not None
    if "history" in CAPABILITIES:
        assert with_history["history"] is not None


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "BoxNow"
    assert parcel["barcode"] == "EXAMPLE123456"
    # No confirmed field supplies these — never fabricate values.
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "delivered"
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-04-29T13:12:42Z"
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["url"] == "https://boxnow.gr/homepage-gr?track=EXAMPLE123456"
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_missing_parcel_maps_to_problem():
    """`missing` is a terminal non-delivery outcome, never `unknown`."""
    parcel = normalize_parcel(missing_sample())
    assert parcel["status"] == ParcelStatus.PROBLEM
    assert parcel["raw_status"] == "missing"
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_normalize_missing_parcel_history_maps_accepted_to_locker_and_missing():
    """Mirrors a live-captured sample: no `in-depot`, `accepted-to-locker`
    stands in its place, then the terminal `missing` event — both event
    types are live-confirmed and mapped in `history`."""
    parcel = normalize_parcel(missing_sample(), include_history=True)
    statuses = {entry["raw_status"]: entry["status"] for entry in parcel["history"]}
    assert statuses["accepted-to-locker"] == ParcelStatus.AT_PICKUP_POINT
    assert statuses["missing"] == ParcelStatus.PROBLEM


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert len(parcel["history"]) == 3
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED


def test_normalize_unconfirmed_state_falls_back_to_unknown_with_warning(caplog):
    """`registered`/`in_transit`/`at_pickup_point` are not evidence-backed yet —

    an in-transit-looking sample still normalizes to `unknown` because the
    top-level `state` vocabulary that would reach those statuses is
    unconfirmed."""
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert "in-depot" in caplog.text


def test_normalize_pickup_flags_stay_false_while_state_stays_unmapped():
    """`pickup`/`pickup_point` follow the top-level `status`, which stays

    `unknown` here — no live sample has ever shown a `state` value that maps
    to `AT_PICKUP_POINT` — even though the parcel's *events* already contain
    the live-confirmed `final-destination` mapping."""
    parcel = normalize_parcel(pickup_sample(), include_history=True)
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None
    # ... while the event-type mapping still shows up in history.
    assert parcel["history"][-1]["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["history"][-1]["raw_status"] == "final-destination"


def test_normalize_pickup_point_populates_once_state_confirms_at_pickup_point(
    monkeypatch,
):
    """Forward-wiring check: the moment `_STATE_MAP` gains a real locker-ready

    entry, `pickup`/`pickup_point` populate with no further code change —
    mirroring how the `awaiting_pickup` sensor is already wired ahead of
    that confirmation. `pickup_point` reads the newest event's
    `locationDisplayName`."""
    monkeypatch.setitem(parcels_module._STATE_MAP, "at-locker", ParcelStatus.AT_PICKUP_POINT)
    raw = pickup_sample()
    raw["state"] = "at-locker"
    parcel = normalize_parcel(raw)
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Example Locker Point"


def test_normalize_barcode_prefers_queried_code_and_warns_on_mismatch(caplog):
    raw = delivered_sample()
    raw["parcelId"] = "QUERIED000"
    raw["id"] = "REPORTED999"
    parcel = normalize_parcel(raw)
    assert parcel["barcode"] == "QUERIED000"
    assert "QUERIED000" in caplog.text
    assert "REPORTED999" in caplog.text


def test_normalize_barcode_falls_back_to_reported_id_without_queried_code():
    raw = delivered_sample()
    parcel = normalize_parcel(raw)
    assert parcel["barcode"] == "EXAMPLE123456"


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-scanned code still yields a full parcel dict."""
    parcel = normalize_parcel({"parcelId": "EXAMPLE000001"})
    assert parcel["barcode"] == "EXAMPLE000001"
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None


def test_tracking_url_uses_the_selected_countrys_template():
    """Every country was confirmed to serve the identical payload shape

    (see const.py) — only the deep-link template varies."""
    for country, template in BOXNOW_TRACKING_URLS.items():
        assert tracking_url("EXAMPLE123456", country) == template.format(
            tracking_code="EXAMPLE123456"
        )


def test_normalize_parcel_url_follows_the_country_argument():
    parcel = normalize_parcel(delivered_sample(), country="hr")
    assert parcel["url"] == "https://boxnow.hr/?track=EXAMPLE123456"


def test_normalize_never_leaks_payment_or_recipient_fields_into_raw():
    raw = delivered_sample()
    parcel = normalize_parcel(raw)
    assert "payment" not in parcel["raw"]
    assert "recipient" not in parcel["raw"]
    assert set(parcel["raw"]) == {"id", "state", "events"}
    for entry in parcel["raw"]["events"]:
        assert set(entry) == {"createTime", "type", "postalCode", "locationDisplayName"}


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels


def test_tracking_url_accepts_a_legacy_uppercase_country():
    """Entries written by 0.10.0 stored uppercase ISO codes."""
    assert tracking_url("EXAMPLE123456", "HR") == "https://boxnow.hr/?track=EXAMPLE123456"
