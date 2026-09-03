# Working in this repository

Home Assistant custom integration for **BoxNow** parcel tracking — a locker
network live in Bulgaria (.bg), Greece (.gr), Croatia (.hr) and Cyprus (.cy).
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
No DTO layer.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| change which optional field this carrier populates vs. always returns `None` | Update `const.py`'s `CAPABILITIES` in the same commit — it feeds the comparison table on the docs site, so a field that starts (or stops) coming back non-null and isn't reflected there is a wrong claim on the website, not just a stale comment. If this carrier has more than one backend (a country-specific transport, not just a config option) with genuinely different field support, `CAPABILITIES` should be a `CAPABILITIES_BY_VARIANT` dict instead — one frozenset per backend, so a field only some backends populate doesn't get silently intersected away or overclaimed for the rest |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).
- **If this carrier can reach `ParcelStatus.AT_PICKUP_POINT` from a real raw
  status/code**, it needs an `awaiting_pickup` sensor — see *Parcel contract*
  in `CONVENTIONS.md`. Say "pickup point", not "ServicePoint"/"parcel
  shop"/"locker", for the generic concept. `ha-dhl-nl`, `ha-dpd`, `ha-gls`,
  `ha-inpost` are reference implementations; `boxnow` here has the
  sensor wired (filtering on `status`, not the `pickup` bool — see
  *Carrier-specific notes*) even though no live `state` value reaches
  `AT_PICKUP_POINT` yet.

## Carrier-specific notes

BoxNow is a keyless, code-based locker network: a single `POST` to a
public JSON tracking endpoint, no account/API key/cookie. Full API mechanics
(endpoint, headers, envelope, status vocabulary) live in the private research
repo at `carrier-research/api/boxnow/` — this section is the
HA-integration decisions that survived the build.

**Country selector — one backend per country, identical payload shape.**
BoxNow runs the same tracking API per country, keyed only by domain:
`api-production.boxnow.{bg,gr,hr,cy}`. Confirmed 2026-09 with one live
tracking code (`4090533096`) queried against all four hosts directly — the
response bodies were byte-identical. `const.py`'s `BOXNOW_API_URLS` /
`BOXNOW_TRACKING_URLS` hold one entry per country; `CONF_COUNTRY` is chosen
once in the config flow's `user` step and stored in the entry's **`data`**
(not `options` — it selects the backend, not a display preference, and
`single_config_entry` means there is only ever one to pick). `__init__.py`
resolves it to an API URL for `BoxNowApiClient`; `coordinator.py` passes it
through to `normalize_parcel(..., country=...)` so `url` uses the right
country's deep-link template. Because the payload shape is identical
everywhere, `CAPABILITIES` stays a flat frozenset — **do not** switch to
`CAPABILITIES_BY_VARIANT` for this carrier; that pattern is for carriers
whose backends genuinely differ in what they return, which BoxNow's four
don't. An entry created before this selector existed has no `CONF_COUNTRY` in
its data; every read site falls back to `DEFAULT_COUNTRY` ("GR", the
originally-hardcoded backend) rather than crashing.

**Tracking-link paths differ per country and are maintainer-sourced, not
independently captured** (same Cloudflare 403-on-everything caveat as
before): `boxnow.bg/homepage?track=`, `boxnow.gr/homepage-gr?track=`,
`boxnow.hr/?track=`, `boxnow.cy/?track=`.

**Pre-1.0 status vocabulary — the central caveat of this repo.** Six
consented live lookups only ever confirmed two top-level `state` values:
`delivered` → `ParcelStatus.DELIVERED` and `missing` → `ParcelStatus.PROBLEM`
(a terminal non-delivery outcome, deliberately not `unknown`). Everything
else a real parcel could report — `registered`, `in_transit`,
`at_pickup_point`, `out_for_delivery`, `returning` — has **no confirmed
top-level `state` value** and normalizes to `unknown` with a one-shot
WARNING. Ship at `0.x.y`/a `bN` pre-release only; do not bump to `1.0.0`
until a locker-ready (or in-transit) sample confirms more of the vocabulary.

**Two separate vocabularies — do not merge them.** `parcels.py` keeps
`_STATE_MAP` (the top-level `state` field, drives `status`) and
`_EVENT_TYPE_MAP` (each history entry's `type` field) apart on purpose:
- `_STATE_MAP` only has the two live-confirmed entries above.
- `_EVENT_TYPE_MAP` is now fully live-confirmed too (2026-09, real households'
  own event logs, pulled by the maintainer directly): `new`→`registered`,
  `in-depot`→`in_transit`, `final-destination`→`at_pickup_point`,
  `accepted-to-locker`→`at_pickup_point`, `delivered`→`delivered`,
  `missing`→`problem`. `accepted-to-locker` is a live-confirmed code but its
  *meaning* is inferred, not documented anywhere: on a parcel that ended up
  `missing`, it stood exactly where `final-destination` normally sits (no
  `in-depot` step, straight to the terminal event), so it is treated as an
  alternate "reached a locker" event and mapped identically. These all still
  feed **only** `history` entries, never the top-level `status`.

This means a parcel's top-level `status` can currently only ever come back as
`delivered`, `problem`, or `unknown` — never `at_pickup_point`, even though
BoxNow is a locker network and a real parcel obviously does reach one; no
live sample has yet shown what `state` value a still-uncollected, locker-ready
parcel reports. The `awaiting_pickup` sensor is wired anyway (filtering on
`status == ParcelStatus.AT_PICKUP_POINT`, not on the `pickup` bool) so it
lights up the moment `_STATE_MAP` gains a confirmed entry for the
locker-ready state — no other code change needed then.

**`pickup`/`pickup_point` are gated on `status is AT_PICKUP_POINT`**,
matching GLS/DHL-NL/Slovenská Pošta — not hardcoded off. In practice they
still stay `False`/`None` today: no top-level `state` value has ever mapped
to `AT_PICKUP_POINT` (only the separate `event.type` vocabulary reaches it,
and that only feeds `history`). When `is_pickup` is true, `pickup_point`
reads the newest event's `locationDisplayName` (live-confirmed field, 2026-09
sample batch — a locker/hub branch name). This mirrors the `awaiting_pickup`
sensor's forward-wiring: no further code change needed once `_STATE_MAP`
gains a locker-ready entry.

**`raw` is an allowlist, not the full payload.** The confirmed response
carries payment and service-related fields (exact names never captured —
only their presence was noted) alongside origin/destination data.
`normalize_parcel` copies through only `id`, `state`, and a redacted event
list (`createTime`, `type`, `postalCode`, `locationDisplayName`) — never the
full raw dict. `locationDisplayName` is a locker/hub branch name
(e.g. "BOX NOW - Sofia"), not recipient PII, but stays in
`diagnostics.py`'s `TO_REDACT` out of caution — that redaction is defence in
depth on top of the allowlist, not the only guard, and diagnostics staying
conservative doesn't require the entity's own `raw` attribute to hide a
non-identifying field too.

**Barcode resolution.** The coordinator tags every fetched response with the
tracking code it was queried with (`raw["parcelId"]`), and `normalize_parcel`
prefers that over the response's own `id` — corroborating, never overriding
— logging a one-shot warning on disagreement. The response never legitimately
carries its own `parcelId` key, so this can't collide with a real field.

**No confirmed ETA, weight, dimensions, sender or receiver field.**
`planned_from`/`planned_to`/`weight`/`dimensions`/`sender`/`receiver` are
always `None`; `CAPABILITIES` in `const.py` only declares `url` and
`history`. The `_parcel_delivery_time_changed` event and the **Deliveries**
calendar are wired (suite parity) but can never fire/populate until a
confirmed ETA field turns up.

**`url` deep-link format: see the country-selector section above** —
`BOXNOW_TRACKING_URLS` replaced the earlier single `TRACKING_URL` constant
(`boxnow.gr/en?track=`), which was itself already maintainer-sourced rather
than captured. Treat all four templates the same way until a real browser
round-trip is recorded in `carrier-research/api/boxnow/`.

**Tracking-code format is unconfirmed.** `config_flow.py` accepts any
non-empty free text and only trims surrounding whitespace — no case-folding,
no separator-stripping, no length/charset check. Do not add one without a
confirmed sample; a false negative here is worse than forwarding a bad code
and letting the next poll report it as not found.

**Do not build** (binding, not just historical): no scraping `boxnow.gr`'s own
web bundle, no batch-querying or enumerating `parcelId` values (only
user-registered codes are ever polled), no surfacing payment/recipient
fields, no calling any endpoint beyond `parcels:track`.

## Options and reloads

For code-based carriers, the options flow starts with exactly `Pakketten` and
`Instellingen`. `Pakketten` is one editable multi-code list; `Instellingen` is
a flat form. Changes apply without a restart. Two models, **do not mix them**:
- **Account-less carriers** (the default) apply changes live: an update listener
  calls `async_request_refresh()`, so added/removed parcel sensors appear
  immediately (this is also the resume path after polling has fully
  suspended — see "Dynamic polling" below).
- **Account-based carriers** call `async_schedule_reload` on submit and register
  **no** update listener. Combining a listener with a reload-on-update flow is
  deprecated, an error in HA 2026.12+.

## Dynamic polling

There is no user-facing polling interval — this is a deliberate suite-wide
choice, not a gap. `coordinator.py` recomputes `update_interval` at the end of
every refresh:

- **Quiet window:** no polling 00:00–06:00 local time, except two daily
  anchors (~00:00 and ~06:00) for overnight / end-of-day catch-up.
- **Tiers while polling:** *hot* (15 min) when a tracked, not-yet-delivered
  parcel is `out_for_delivery` within an hour of its `planned_from` (or has no
  `planned_from` at all); *mid* (45 min) for anything else still in flight —
  `problem`/`returning` included, deliberately not hot. Account-based carriers
  never fully stop even with nothing hot or in transit: the mid-tier poll is
  also how a new shipment gets discovered.
- **Full stop (account-less carriers only):** `update_interval = None` when
  nothing is tracked or every tracked parcel is delivered. Resumes the moment
  a parcel is added back, via the options-flow refresh above.
- **Stagger:** a small, stable per-install offset (hash of the config entry
  id) is added to every computed interval so installs don't all hit an anchor
  or tier boundary at the same second.
- **429 backoff:** a 429 anywhere in a poll raises `UpdateFailed` with
  `retry_after` — the carrier's own `Retry-After` header if present, otherwise
  an exponential backoff tracked per-coordinator. `api.py`'s
  `…ApiError.status_code` / `.retry_after` carry this from the HTTP layer.

A carrier that genuinely throttles or soft-bans traffic harder than the 429
backoff handles is a documented, local divergence from this in that one
repo's own `CLAUDE.md` — not a generator flag.

## Module layout

| File | Carrier-specific? |
|---|---|
| `api.py` (HTTP client, error types) | **yes** |
| `const.py` (domain, URLs, `ParcelStatus`, option keys) | partly (URLs) |
| `parcels.py` (status map, `normalize_parcel`, history, sort, filters — pure, no I/O) | partly (`_STATUS_MAP`, `normalize_parcel`) |
| `coordinator.py` (fetch, cache, event firing) | mostly not |
| `config_flow.py` | partly (code validation) |
| `sensor.py` / `button.py` / `calendar.py` / `device_trigger.py` | no |
| `diagnostics.py` | partly (`TO_REDACT`) |
| `services.py` (`track_parcel` / `untrack_parcel`, account-less only) | no |

`parcels.py` is deliberately free of I/O and HA objects so the per-carrier part
stays unit-testable without Home Assistant. Config: `ConfigEntry.runtime_data`
(typed, no `hass.data`), `PARALLEL_UPDATES = 0`, coordinator takes
`config_entry=entry`. `aiohttp.ClientError` is caught **per parcel** in the gather
loop (one bad parcel doesn't fail the poll) but **not** around the whole update
(the coordinator wraps that). Entities: `has_entity_name` + `translation_key`,
`icons.json`, translated units, `_attr_attribution`, `_unrecorded_attributes` on
anything with a parcel list or `raw`. Over-redact diagnostics — they get pasted
into public issues.

## Running tests

```
python -m pytest tests/ --cov=custom_components.boxnow
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in your own private research notes, never in
this repo.
