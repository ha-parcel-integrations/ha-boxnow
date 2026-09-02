"""Sample BoxNow API payloads shared by the test modules.

Shapes mirror the confirmed live envelope: one ``data[]`` item per requested parcel, each carrying ``id``,
``state`` and an ``events[]`` list. Field values below are invented — the
retained research samples had their actual values redacted, only their shape
kept — but the *keys* are the confirmed ones. ``missing`` and ``delivered``
top-level states are live-confirmed; the ``event.type`` values
(``new``/``in-depot``/``final-destination``/``accepted-to-locker``/``delivered``/``missing``)
are all live-confirmed too (2026-09 sample batch).
"""
from __future__ import annotations

ACTIVE_CODE = "EXAMPLE999999"
DELIVERED_CODE = "EXAMPLE123456"
MISSING_CODE = "EXAMPLE555555"


def event(event_type: str, create_time: str) -> dict:
    """One entry of the carrier's own event timeline."""
    return {
        "createTime": create_time,
        "locationDisplayName": "Example Locker Point",
        "postalCode": "12345",
        "type": event_type,
    }


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A representative tracking response for a delivered parcel."""
    return {
        "id": code,
        "state": "delivered",
        "payment": {"amount": 0, "method": "none"},
        "recipient": "Jane Doe",
        "events": [
            event("delivered", "2026-04-29T13:12:42Z"),
            event("in-depot", "2026-04-28T15:52:17Z"),
            event("new", "2026-04-27T23:03:58Z"),
        ],
    }


def missing_sample(code: str = MISSING_CODE) -> dict:
    """A representative tracking response for a missing (terminal problem) parcel.

    Mirrors the real flow: no ``in-depot`` step, ``accepted-to-locker`` in
    its place, then the terminal ``missing`` event — matching a live-captured
    sample exactly.
    """
    return {
        "id": code,
        "state": "missing",
        "payment": {"amount": 0, "method": "none"},
        "recipient": "Jane Doe",
        "events": [
            event("missing", "2026-04-29T09:00:00Z"),
            event("accepted-to-locker", "2026-04-28T15:52:17Z"),
            event("new", "2026-04-27T23:03:58Z"),
        ],
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel still in transit — an unmapped top-level state (unconfirmed)."""
    return {
        "id": code,
        "state": "in-depot",
        "payment": {"amount": 0, "method": "none"},
        "recipient": "Jane Doe",
        "events": [
            event("in-depot", "2026-04-28T15:52:17Z"),
            event("new", "2026-04-27T23:03:58Z"),
        ],
    }


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel whose newest *event* is the unconfirmed pickup-ready one.

    The top-level ``state`` here is deliberately left at an unmapped value —
    no live sample has ever shown what ``state`` a locker-ready parcel
    reports, so this fixture cannot claim one. It exists to exercise the
    ``final-destination`` event-type mapping inside ``history`` only.
    """
    sample = active_sample(code)
    sample["events"] = [
        event("final-destination", "2026-04-29T09:00:00Z"),
        *sample["events"],
    ]
    return sample
