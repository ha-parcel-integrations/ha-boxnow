# BoxNow Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-boxnow.svg)](https://github.com/ha-parcel-integrations/ha-boxnow/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

> ⚠️ **Pre-release.** BoxNow's status vocabulary is only partially
> confirmed — see [Parcel status reference](#parcel-status-reference) and
> [Troubleshooting](#troubleshooting). Everything else (the endpoint, the
> request/response shape, the `delivered`/`missing` statuses) is
> live-confirmed.

A custom Home Assistant integration that tracks your [BoxNow](https://boxnow.gr/) parcels — a locker/pickup-point network running in Bulgaria, Greece, Croatia and Cyprus. No account is needed — you enter the tracking code yourself, just like on the BoxNow website.

Part of the [ha-parcel-integrations](https://github.com/ha-parcel-integrations) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Bulgaria, Greece, Croatia and Cyprus backends — pick your country at setup
- Track any number of BoxNow parcels by tracking code — no account needed
- Per-parcel sensor with the canonical status (`delivered` / `problem` / `unknown` for now — see below), the carrier's own status text and a tracking deep-link
- Summary sensors: incoming parcels, next delivery, awaiting pickup, recently delivered parcels
- Read-only **Deliveries** calendar (present for parity with every carrier in the family; BoxNow has no confirmed ETA field yet, so it stays empty for now — see [Parcel status reference](#parcel-status-reference))
- `boxnow.track_parcel` / `boxnow.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2024.12 or newer
- A BoxNow parcel and its tracking code (from the shipping
  confirmation email or the missed-delivery card) — no account needed

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-boxnow` as an **Integration**.
3. Install **BoxNow** and restart Home Assistant.

### Manual

Copy `custom_components/boxnow` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → BoxNow**, and pick your country (Bulgaria, Greece, Croatia or Cyprus) — BoxNow tracking needs no account, so that's the only setup step. All four countries run the identical backend and normalise the same way; the country only picks which host is polled and which tracking link is shown.

Then add parcels via the integration's **Configure** dialog, the [`boxnow.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation email or the missed-delivery card.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule (quiet overnight window, faster when a parcel is out
for delivery, stopped entirely once nothing is left to track) with nothing to
configure. See [CLAUDE.md](CLAUDE.md) for the details.

## Removal

Standard HA removal applies: **Settings → Devices & Services → BoxNow → ⋮ → Delete**. Nothing is stored on BoxNow's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.boxnow_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.boxnow_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.boxnow_next_delivery` | Earliest expected delivery moment across all active parcels (currently always empty — no confirmed ETA field yet) |
| `sensor.boxnow_awaiting_pickup` | Parcels ready to collect at a BoxNow locker (currently always `0` — see [Parcel status reference](#parcel-status-reference)) |
| `sensor.boxnow_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.boxnow_last_successful_update` | Diagnostic: when BoxNow was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family. BoxNow's own status vocabulary is only **partially confirmed** right now:

| Status | Meaning | Confirmation |
|---|---|---|
| `delivered` | Delivered | live-confirmed |
| `problem` | BoxNow reports the parcel as missing (a terminal non-delivery outcome) | live-confirmed |
| `unknown` | Not yet scanned, or a status we have not mapped yet | — |
| `registered` / `in_transit` / `at_pickup_point` / `out_for_delivery` / `returning` | Not yet reachable | **not evidence-backed** — see below |

BoxNow is a locker network, so a `registered` → `in_transit` → `at_pickup_point` → `delivered` journey is *expected*, but no live sample has yet shown what the carrier's top-level status reports for those intermediate steps. Until one does, everything but `delivered`/`missing` normalizes to `unknown` and logs a one-shot warning — please [open an issue](https://github.com/ha-parcel-integrations/ha-boxnow/issues/new?template=unrecognised_status.yml) with the logged value if you see one, it's exactly the data needed to complete the mapping.

The carrier's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the BoxNow device):

| Event | When |
|---|---|
| `boxnow_parcel_registered` | A new parcel appears in the active list |
| `boxnow_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `boxnow_parcel_delivered` | A parcel is delivered |
| `boxnow_parcel_delivery_time_changed` | The expected delivery window changes (present for parity; cannot fire yet — no confirmed ETA field) |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `boxnow.track_parcel` | `tracking_code` | Start tracking a parcel |
| `boxnow.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.boxnow: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — either BoxNow hasn't scanned it yet, the code is wrong, or (very likely right now) it's genuinely in transit and BoxNow's top-level status for that isn't confirmed yet — see [Parcel status reference](#parcel-status-reference). It will pick up automatically once the mapping is extended.
- **A status logs "Unrecognised BoxNow state" or "... event.type"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-boxnow/issues/new?template=unrecognised_status.yml) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://github.com/ha-parcel-integrations) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://github.com/ha-parcel-integrations) for the current list of supported carriers.

## Disclaimer

This integration uses the same public tracking endpoint as the BoxNow consumer website. It is not affiliated with, endorsed by, or supported by BoxNow. It only ever looks up tracking codes you enter yourself — it never scrapes the BoxNow website, batch-queries, or enumerates parcel IDs.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
