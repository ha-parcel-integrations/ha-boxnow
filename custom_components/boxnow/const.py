"""Constants for the BoxNow parcel tracker integration."""
from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "boxnow"


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status.

    **Do not extend or rename these members.** Every integration in the parcel
    suite publishes exactly this vocabulary on the ``status`` field of each
    normalised parcel, so cross-carrier automations and the aggregator can
    target ``status: out_for_delivery`` regardless of carrier. Listed in
    roughly the order a parcel moves through.
    """

    REGISTERED = "registered"               # Sender announced the parcel; not handed over yet
    IN_TRANSIT = "in_transit"               # In the carrier's network
    OUT_FOR_DELIVERY = "out_for_delivery"   # On a delivery vehicle today
    AT_PICKUP_POINT = "at_pickup_point"     # Ready to collect at a pickup location
    DELIVERED = "delivered"                 # Handed over
    RETURNING = "returning"                 # Failed delivery, going back to sender
    PROBLEM = "problem"                     # Carrier reports an exception/issue
    UNKNOWN = "unknown"                     # Raw status we have not mapped yet


PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

# Every optional key the parcel contract defines. CAPABILITIES below must be a
# subset of this — it exists so a typo in CAPABILITIES fails a test instead of
# silently dropping a carrier off a table on the docs site.
KNOWN_CAPABILITIES = frozenset(
    {"weight", "dimensions", "delivery_window", "pickup_point", "url", "history"}
)

# No confirmed field supplies weight, dimensions or an ETA window.
# ``pickup_point`` stays out too: the field names that would carry it only
# appear on the unconfirmed `final-destination` event, and we must not
# populate it from a guess (see parcels.py's ``normalize_parcel``). ``url``
# and ``history`` are both real. Revisit this the moment a locker-ready
# sample confirms the pickup-point field names.
CAPABILITIES = frozenset({"url", "history"})

# If this carrier ever grows a second backend with a genuinely different
# payload shape (a country-specific API, not just a config option), replace
# the single CAPABILITIES above with a CAPABILITIES_BY_VARIANT dict instead:
#
#   CAPABILITIES_BY_VARIANT = {
#       "Germany": frozenset({"pickup_point", "url", "history"}),
#       "Other": frozenset({"weight", "dimensions", "delivery_window",
#                            "pickup_point", "url", "history"}),
#   }
#
# Key order is display order on the docs site's comparison table; label each
# key exactly as the carrier's own country/backend selector does. The docs
# site's generator accepts either shape — don't declare both. Do not add this
# preemptively: a single-backend carrier (the common case) keeps the flat
# CAPABILITIES above.

# BoxNow runs the identical backend per country, keyed only by the domain
# (verified 2026-09: the same live tracking code, queried against
# api-production.boxnow.{bg,gr,hr,cy}, returned byte-identical bodies) — one
# ``POST`` for every request, the tracking code goes in the JSON body
# (``{"parcelId": "<code>"}``), not the URL — see api.py. No auth: no cookie,
# API key or bearer token is sent. Response envelope is ``{"data": [...]}``,
# JSON, HTTP 200 on success. The exact body for an unknown parcelId was never
# captured (six consented live lookups only ever returned known parcels).
# Chosen once at setup (``CONF_COUNTRY`` in the config entry's ``data``, not
# ``options`` — it selects the backend, not a display preference) and never
# guessed: a single-backend fallback would silently poll the wrong country.
CONF_COUNTRY = "country"

BOXNOW_API_URLS: dict[str, str] = {
    "bg": "https://api-production.boxnow.bg/api/v1/parcels:track",
    "gr": "https://api-production.boxnow.gr/api/v1/parcels:track",
    "hr": "https://api-production.boxnow.hr/api/v1/parcels:track",
    "cy": "https://api-production.boxnow.cy/api/v1/parcels:track",
}

# Human-facing deep link surfaced on each parcel's ``url`` field, one per
# country. Maintainer-sourced (not independently captured — Cloudflare
# bot-protection 403s every path on these hosts regardless of parameter, real
# code or not), each the country site's own homepage tracking path.
BOXNOW_TRACKING_URLS: dict[str, str] = {
    "bg": "https://boxnow.bg/homepage?track={tracking_code}",
    "gr": "https://boxnow.gr/homepage-gr?track={tracking_code}",
    "hr": "https://boxnow.hr/?track={tracking_code}",
    "cy": "https://boxnow.cy/?track={tracking_code}",
}

DEFAULT_COUNTRY = "gr"


def normalize_country(value: str | None) -> str:
    """Return a supported country key for a stored config-entry value.

    Home Assistant requires selector option keys to be lowercase, so entries
    written by 0.10.0 (which stored uppercase ISO codes) are folded here
    instead of being silently pointed at the default backend.
    """
    key = (value or DEFAULT_COUNTRY).lower()
    return key if key in BOXNOW_API_URLS else DEFAULT_COUNTRY

# Tracked parcels live in the config entry options as a list of
# ``{tracking_code}`` dicts — this carrier has no account or parcel feed, so the
# user enters the codes themselves. Kept as dicts so future per-parcel fields
# slot in without an options migration.
CONF_PARCELS = "parcels"
CONF_TRACKING_CODE = "tracking_code"

# Delivered-parcels retention: keep delivered parcels visible for the last N
# days, or keep only the N most recent — identical across the suite.
CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Dynamic, status-driven polling — unconditional across the suite, no
# user-facing interval option (see scaffold/CLAUDE.md's "Dynamic polling"
# section for the full algorithm and the reasoning behind it).
#
# Quiet window: no polling between these local hours except the two anchors
# below, for overnight / end-of-day catch-up.
QUIET_WINDOW_START_HOUR = 0
QUIET_WINDOW_END_HOUR = 6

# Cadence while polling is active (minutes). Hot = at least one tracked,
# not-yet-delivered parcel is out_for_delivery within HOT_LOOKAHEAD_HOURS of
# its planned_from (or has no planned_from at all); mid = anything else still
# in flight (registered, in_transit, at_pickup_point, unknown, problem,
# returning).
HOT_INTERVAL_MINUTES = 15
MID_INTERVAL_MINUTES = 45
HOT_LOOKAHEAD_HOURS = 1

# Small, stable per-install offset added to every computed interval so
# different installs don't all hit an anchor or tier boundary at the same
# second. Deterministic (hash of the config entry id), not random.
STAGGER_MINUTES = 7

# Per-parcel status history is opt-in and off by default, identical across the
# suite. Keep it off by default even when — as here — the timeline arrives in
# the same response and costs no extra request: it is a large attribute, and on
# carriers that need a second call per parcel the cost is real.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20
