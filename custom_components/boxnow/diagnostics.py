"""Diagnostics support for the BoxNow parcel tracker integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import BoxNowConfigEntry
from .const import CONF_COUNTRY, normalize_country

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address or a specific parcel. Over-redacting is
# cheap; under-redacting leaks a user's home address into a GitHub thread.
#
# BoxNow specifics: the confirmed payload carries
# payment and service-related fields alongside origin/destination data, but
# normalize_parcel()'s ``raw`` is already an allowlist that excludes all of
# them (see parcels.py) — this list is defence in depth, not the only guard.
# Exact payment field names were never captured (the six retained samples are
# schema-only), so this also redacts by plausible name; keep it generous
# rather than precise.
TO_REDACT = {
    # canonical fields we publish ourselves
    "tracking_code",
    "barcode",
    "sender",
    "receiver",
    "url",
    # the tracking code itself, as it appears in the raw payload
    "parcelId",
    "id",
    # locker/branch identifiers — kept conservatively redacted until confirmed
    # non-identifying
    "locationDisplayName",
    "postalCode",
    "postal_code",
    # payment / service-related fields (names unconfirmed — over-redact)
    "payment",
    "paymentAmount",
    "paymentMethod",
    "cod",
    "codAmount",
    "amount",
    "price",
    "cost",
    "currency",
    # recipient/sender fields, should one ever appear
    "recipient",
    "deliveryAddress",
    "address",
    "city",
    "street",
    "email",
    "phone",
    "name",
    "driver",
    "signature",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BoxNowConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the BoxNow config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "country": normalize_country(entry.data.get(CONF_COUNTRY)),
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
        },
        "polling": {
            "tier_minutes": coordinator.current_tier_minutes,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "suspended": coordinator.update_interval is None,
        },
        "incoming": async_redact_data(coordinator.data or [], TO_REDACT),
        "delivered": async_redact_data(coordinator.delivered or [], TO_REDACT),
    }
