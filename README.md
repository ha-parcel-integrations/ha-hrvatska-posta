# Hrvatska pošta Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-hrvatska-posta.svg)](https://github.com/ha-parcel-integrations/ha-hrvatska-posta/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-hrvatska-posta/total.svg)](https://github.com/ha-parcel-integrations/ha-hrvatska-posta/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 📦 Part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/), a family of package-tracking integrations for Home Assistant that all publish the same parcel format, statuses and events, so they plug into cross-carrier automations and the optional [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator). Browse the other carriers on the website.
>
> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [Hrvatska pošta](https://www.posta.hr) parcels in Croatia. It reads Hrvatska pošta's public tracking page, so no account is needed — you enter the tracking code yourself, just like on the Hrvatska pošta website.

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

- Track any number of Hrvatska pošta parcels by tracking code — no account needed
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `delivered` / …), the carrier's own status text, the pickup post office or parcel locker, the weight and a tracking deep-link
- Summary sensors: incoming parcels, next delivery, recently delivered parcels
- `hrvatska_posta.track_parcel` / `hrvatska_posta.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2025.12 or newer
- A Hrvatska pošta parcel and its tracking code (from the shipping
  confirmation email or the missed-delivery card) — no account needed
- The tracking code in one of the shapes Hrvatska pošta's tracking page accepts, such as `RR123456789HR` (a Croatian or foreign S10 code), a 12-digit number, a `3S…` or `BAT…` number

## Installation

### HACS (recommended)

Click the button below to open this repository in HACS, then install **Hrvatska pošta**:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ha-parcel-integrations&repository=ha-hrvatska-posta&category=integration)

Or add it by hand: in HACS, choose the three-dot menu → **Custom repositories**, add `https://github.com/ha-parcel-integrations/ha-hrvatska-posta` as an **Integration** and install **Hrvatska pošta**.

Restart Home Assistant if HACS asks you to.

### Manual

Copy `custom_components/hrvatska_posta` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hrvatska_posta)

Add the integration via **Settings → Devices & Services → Add Integration → Hrvatska pošta**. There is nothing to fill in: the hub is created immediately (Hrvatska pošta tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`hrvatska_posta.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation email or the missed-delivery card.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule with nothing to configure.

## Dynamic polling

Polling isn't a setting here — the integration adjusts its own cadence to
what your tracked parcels are actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM), so an overnight update is never missed.
- **Hot (every 15 minutes)** — while any tracked parcel is out for delivery
  today, starting an hour before its delivery window opens (or immediately if
  no window is known yet).
- **Normal (every 45 minutes)** — for anything else still on its way.
- **Fully paused** — once every tracked parcel has been delivered, or nothing
  is tracked at all, polling stops until you add a parcel back (adding one
  always triggers an immediate check, regardless of the pause).
- A small, fixed per-hub offset is added on top, so not every Hrvatska pošta
  hub out there polls at exactly the same second.

Hrvatska pošta publishes no delivery window, so a parcel that is out for delivery is always checked at the 15-minute pace.

## Removal

Standard HA removal applies: **Settings → Devices & Services → Hrvatska pošta → ⋮ → Delete**. Nothing is stored on Hrvatska pošta's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.hrvatska_posta_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.hrvatska_posta_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.hrvatska_posta_next_delivery` | Earliest expected delivery moment across all active parcels (stays empty: Hrvatska pošta publishes no delivery window) |
| `sensor.hrvatska_posta_en_route_to_pickup_point` | Incoming parcels redirected to a parcel locker that have not arrived yet |
| `sensor.hrvatska_posta_awaiting_pickup` | Incoming parcels that are ready to collect at a post office or parcel locker |
| `sensor.hrvatska_posta_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `calendar.hrvatska_posta_deliveries` | Read-only delivery calendar (stays empty: Hrvatska pošta publishes no delivery window) |
| `sensor.hrvatska_posta_last_successful_update` | Diagnostic: when Hrvatska pošta was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | The sender announced the parcel, or Hrvatska pošta accepted it |
| `in_transit` | On its way: arrived at or sent from a facility, or redirected to a parcel locker |
| `out_for_delivery` | Out for delivery to your address |
| `at_pickup_point` | Waiting at a post office, or handed over to a parcel locker |
| `delivered` | Delivered |
| `returning` | Going back to the sender, or already delivered back to the sender |
| `problem` | A delivery attempt failed, or the parcel was not collected |
| `unknown` | Not yet known to Hrvatska pošta, or a status we have not mapped yet |

The carrier's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the Hrvatska pošta device):

| Event | When |
|---|---|
| `hrvatska_posta_parcel_registered` | A new parcel appears in the active list |
| `hrvatska_posta_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `hrvatska_posta_parcel_delivered` | A parcel is delivered |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `hrvatska_posta.track_parcel` | `tracking_code` | Start tracking a parcel |
| `hrvatska_posta.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

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
    custom_components.hrvatska_posta: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — Hrvatska pošta has no data for the code yet, or the code is wrong. It picks up automatically once the parcel is scanned.
- **A parcel stopped updating** — Hrvatska pošta drops the history of old parcels. The last known status stays visible.
- **A parcel delivered, then sent back** — once a parcel is delivered it is not checked again, so a return that happens afterwards is not picked up.
- **A status logs "Unrecognised Hrvatska pošta status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-hrvatska-posta/issues/new?template=unrecognised_status.yml) with the logged line so the mapping can be extended. Customs, loss, damage, hold and cash-on-delivery scans have not been seen yet, so those are the likeliest to show up.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by Hrvatska pošta, Home Assistant, or any other third party referenced in this project. Please don't contact Hrvatska pošta for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed with your own account or API key where required. These may change or be withdrawn without notice and may be subject to Hrvatska pošta's terms. Data is sent only to Hrvatska pošta's own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration reads the same public tracking page that Hrvatska pošta offers for each tracking code.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
