"""The parcel contract's checks, run over every normalised sample."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.boxnow.const import ParcelStatus
from custom_components.boxnow.parcels import normalize_parcel, parse_iso, planned_window

from .payloads import active_sample, delivered_sample, event, pickup_sample

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
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


def _collected() -> dict:
    sample = pickup_sample()
    sample["state"] = "delivered"
    sample["events"] = [
        event("delivered", "2026-04-29T16:00:00Z"),
        event("final-destination", "2026-04-29T09:00:00Z"),
        event("in-depot", "2026-04-28T15:52:17Z"),
    ]
    return sample


def _missing() -> dict:
    sample = active_sample()
    sample["state"] = "missing"
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "at_pickup_point": pickup_sample,
    "collected": _collected,
    "problem": _missing,
    "bare": lambda: {"id": "EXAMPLE000001"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(_collected())
    assert parcel["delivered"] is True
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Example Locker Point"


def test_delivered_at_is_the_delivery_event_not_the_newest_event():
    sample = _collected()
    sample["events"].insert(0, event("in-depot", "2026-04-30T08:00:00Z"))
    assert normalize_parcel(sample)["delivered_at"] == "2026-04-29T16:00:00Z"


def test_every_parcel_is_pickup_but_the_locker_is_named_only_when_known():
    parcel = normalize_parcel(active_sample())
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None


def test_a_moment_ends_at_the_end_of_that_day():
    zone = ZoneInfo("Europe/Athens")
    start, end = planned_window("2099-04-29T13:00:00Z", None, zone=zone)
    stop = datetime.fromisoformat(end)
    assert (stop.hour, stop.minute, stop.second, stop.microsecond) == (23, 59, 59, 0)
    assert stop.date() == parse_iso(start).astimezone(zone).date()


def test_the_expected_day_covers_the_whole_day():
    parcel = normalize_parcel(active_sample())
    start = datetime.fromisoformat(parcel["planned_from"])
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert start.date() == end.date()
