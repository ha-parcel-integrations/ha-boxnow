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

**Structure, options flow, dynamic polling and module layout are suite-wide**
and identical in every carrier — the authoritative spec is
[`ha-carrier-template/scaffold/CLAUDE.md`](https://github.com/ha-parcel-integrations/ha-carrier-template/blob/main/scaffold/CLAUDE.md).
This repo follows it exactly.

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).
- **The pickup-point summaries are a pair.** A carrier that can deliver to a
  pickup point or locker exposes both `en_route_to_pickup_point` and
  `awaiting_pickup`, even before a real parcel has shown the status or flag
  (they read 0 until then; record it in `PENDING_*`). A carrier without pickup
  points exposes neither and keeps `pickup` constantly False.
  See *Parcel contract* in `CONVENTIONS.md`. Say "pickup point", not
  "ServicePoint"/"parcel shop"/"locker", for the generic concept; the
  example carrier demonstrates both canonical sensors.
  `boxnow` here has both pickup sensors.

## Carrier-specific notes

BoxNow is a keyless, code-based locker network: a single `POST` to a
public JSON tracking endpoint, no account/API key/cookie. Full API mechanics
(endpoint, headers, envelope, status vocabulary) live in the private research
repo at `carrier-research/boxnow/api/` — this section is the
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
its data; every read site runs the value through `normalize_country()`, which
falls back to `DEFAULT_COUNTRY` ("gr", the originally-hardcoded backend)
rather than crashing. Keys are **lowercase** — hassfest rejects a selector
option key that isn't `[a-z0-9-_]+`, which is what 0.10.0's uppercase ISO
codes tripped over; `normalize_country()` also folds those stored uppercase
values so an entry written by 0.10.0 keeps its backend.

**Tracking-link paths differ per country and are maintainer-sourced, not
independently captured** (same Cloudflare 403-on-everything caveat as
before): `boxnow.bg/homepage?track=`, `boxnow.gr/homepage-gr?track=`,
`boxnow.hr/?track=`, `boxnow.cy/?track=`.

**Status vocabulary.** `delivered` → `delivered`, `missing` → `problem`
(a terminal non-delivery outcome, deliberately not `unknown`),
`in-transit` → `in_transit` and `in-final-destination` → `at_pickup_point`
(waiting in the destination locker). An unmapped `state` takes its
status from the newest event's `type` via `_EVENT_TYPE_MAP`
(`resolve_status`); only when neither maps does it read `unknown`, with a
one-shot WARNING.

**Two separate vocabularies — do not merge them.** `parcels.py` keeps
`_STATE_MAP` (the top-level `state` field, drives `status` first) and
`_EVENT_TYPE_MAP` (each event's `type`: the `status` fallback and every
history entry) apart on purpose. They overlap in spelling, not always in
meaning:
- `_EVENT_TYPE_MAP`: `new`→`registered`, `accepted-to-locker`→`in_transit`,
  `in-depot`/`in-transit`→`in_transit`,
  `final-destination`/`in-final-destination`→`at_pickup_point`,
  `delivered`→`delivered`, `missing`→`problem`.
- The two locker events mean different things and must never share a mapping:
  `accepted-to-locker` is the **sender** handing the parcel in at a locker or
  store (it sits between `new` and `in-depot`), while only `final-destination`
  is the parcel waiting in the **recipient's** locker. An earlier build mapped
  `accepted-to-locker` to `at_pickup_point` — a wrong claim, not a stale
  comment.
- Still unmapped on purpose: `wait-for-load`, `returned`, `expired` and the
  cancellations. What they mean for the recipient is not established.

**`pickup` is constant `True`.** BoxNow only delivers to lockers, so every
parcel is a pickup parcel from registration, regardless of status.
`pickup_point` is the `locationDisplayName` of the arrival event (the
destination locker), `None` until the parcel reaches it, and kept once
delivered. `en_route_to_pickup_point` is the standard template sensor (`pickup`
true, status not `at_pickup_point`/`returning`); `awaiting_pickup` filters on
`status`. `delivered_at` is the `delivered` event's timestamp, else `None`.

**ETA is a whole day.** `etaDate` is a UTC timestamp late in the evening
before the expected day; its time of day is no estimate. `delivery_window()`
converts it to the **country's own** time zone (Croatia is CET, the rest EET)
and publishes 00:00–23:59:59 of that day. Emptied at `at_pickup_point` and once delivered.

**`raw` is an allowlist, not the full payload.** The confirmed response
carries payment and service-related fields (exact names never captured —
only their presence was noted) alongside origin/destination data.
`normalize_parcel` copies through only `id`, `state`, `etaDate` and a
redacted event list (`createTime`, `type`, `postalCode`, `locationDisplayName`) — never the
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

**No weight, dimensions, sender or receiver field.** Those stay `None`;
`CAPABILITIES` declares `delivery_window`, `pickup_point`, `url` and `history`.

**`url` deep-link format: see the country-selector section above** —
`BOXNOW_TRACKING_URLS` replaced the earlier single `TRACKING_URL` constant
(`boxnow.gr/en?track=`), which was itself already maintainer-sourced rather
than captured. Treat all four templates the same way until a real browser
round-trip is recorded in `carrier-research/boxnow/api/`.

**Tracking-code format is unconfirmed.** `config_flow.py` accepts any
non-empty free text and only trims surrounding whitespace — no case-folding,
no separator-stripping, no length/charset check. Do not add one without a
confirmed sample; a false negative here is worse than forwarding a bad code
and letting the next poll report it as not found.

**Do not build** (binding, not just historical): no scraping `boxnow.gr`'s website, no batch-querying or enumerating `parcelId` values (only
user-registered codes are ever polled), no surfacing payment/recipient
fields, no calling any endpoint beyond `parcels:track`.

## Running tests

```
python -m pytest tests/ --cov=custom_components.boxnow
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in your own private research notes, never in
this repo.
