# Garmin LiveTrack Monitor

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Validate](https://github.com/aikrana/ha-garmin-livetrack-monitor/actions/workflows/validate.yml/badge.svg)](https://github.com/aikrana/ha-garmin-livetrack-monitor/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/github/license/aikrana/ha-garmin-livetrack-monitor)](LICENSE)
[![GitHub Release](https://img.shields.io/github/v/release/aikrana/ha-garmin-livetrack-monitor)](https://github.com/aikrana/ha-garmin-livetrack-monitor/releases)

A Home Assistant custom integration that automatically detects and monitors Garmin LiveTrack sessions in real-time, with multi-person support.

> **Disclaimer:** This integration was developed with the assistance of AI tools, primarily for personal use. It is provided as-is, with no warranty of any kind. The author is not responsible for any issues arising from its use. Use at your own risk.

## Why this integration?

Garmin periodically changes how its LiveTrack service delivers data to the web — the underlying protocols, data formats and access protections have shifted multiple times over the years. Each change breaks approaches that rely on standard Home Assistant mechanisms such as REST sensors or the Scrape integration, requiring users to reverse-engineer the new system and rebuild their configuration from scratch.

This integration encapsulates all of that complexity internally. When Garmin makes changes, only this integration needs updating — your automations, dashboards and scripts remain untouched.

## Features

- **Automatic session detection** — monitors your email inbox via IMAP IDLE push for LiveTrack notification emails; no manual input or polling needed.
- **Real-time activity data** — position, speed, altitude, heart rate, distance, duration, cadence, power and more, updated every few seconds during an active session.
- **Multi-person support** — configure several people and monitor concurrent sessions independently.
- **Sensor per person** — state (`idle` / `active` / `finished`) with rich attributes containing all available activity data.
- **Optional device tracker** — creates a `device_tracker` entity per person that can be associated with a Home Assistant [Person](https://www.home-assistant.io/integrations/person/) to provide location awareness during activities. Like any device tracker in HA, the entity reports coordinates that are matched against your configured zones, so the person's state will reflect whether they are `home`, `not_home`, or in a named zone. This can be enabled or disabled globally from the integration settings.
- **Events** — fires automatable events at key moments in the activity lifecycle.
- **Config flow UI** — full setup from the Home Assistant interface, no YAML editing required.

## Installation

### HACS (Recommended)

1. Open HACS → Integrations → ⋮ → **Custom repositories**
2. Add `https://github.com/aikrana/ha-garmin-livetrack-monitor` with category **Integration**
3. Search for and install **Garmin LiveTrack Monitor**
4. Restart Home Assistant

### Manual

1. Copy `custom_components/garmin_livetrack/` into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Prerequisites

This integration monitors an email inbox for Garmin LiveTrack notification emails. For this to work:

- You need an email account accessible via IMAP (Gmail, Outlook, or any provider that supports IMAP with IDLE).
- The people you want to monitor must have configured Garmin LiveTrack to send notification emails **to that email address** when they start an activity. This is done from the Garmin Connect app on their phone or watch, where they add your email as a LiveTrack recipient.
- For Gmail, you will need to generate an [App Password](https://myaccount.google.com/apppasswords) rather than using your regular password.

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Garmin LiveTrack Monitor**

### Step 1 — IMAP Connection

| Field | Example |
|-------|---------|
| IMAP Server | `imap.gmail.com` |
| Port | `993` |
| Username | `your.email@gmail.com` |
| Password | Your app password |
| Ignore emails older than | `5` minutes |

The "ignore emails older than" setting prevents the integration from reacting to old LiveTrack emails that may already be in your inbox when Home Assistant starts up.

### Step 2 — First Person

| Field | Description |
|-------|-------------|
| Full name | The person's name exactly as it appears in the LiveTrack email body (e.g. `John Smith`) |
| Entity prefix | Short identifier used in entity names (e.g. `john` creates `sensor.garmin_livetrack_john`) |
| Enable device tracker | Creates a `device_tracker` entity for use with HA's Person integration and the map |
| Poll interval | Seconds between data updates (default: 6) |

You can add more people later from the integration's **Options** menu.

## Entities

### Sensor: `sensor.garmin_livetrack_{prefix}`

**States:**

| State | Meaning |
|-------|---------|
| `idle` | No active session |
| `active` | LiveTrack session in progress |
| `finished` | Session ended — last known data preserved |

**Attributes:**

The sensor exposes a rich set of attributes during `active` and `finished` states. Not all attributes will be populated for every activity — availability depends on the Garmin device, the activity type and whether the device has acquired a GPS fix.

| Attribute | Description |
|-----------|-------------|
| `person_name` / `person_id` | Tracked person's name and entity prefix |
| `session_id` | Garmin session identifier |
| `livetrack_url` | Direct URL to view the session on Garmin |
| `session_start` / `session_end` | Session timestamps (ISO 8601) |
| `latitude` / `longitude` | Last known coordinates |
| `has_location` | `true` when coordinates are available |
| `speed` / `speed_kmh` / `pace` | Speed in m/s, km/h, and min/km |
| `altitude` | Altitude in meters |
| `distance_km` | Total distance in kilometers |
| `duration` / `duration_secs` | Elapsed time as `HH:MM:SS` and raw seconds |
| `heartrate` | Heart rate in bpm |
| `power_watts` | Power output in watts |
| `cadence` | Cadence in cycles/min |
| `activity_type` | `cycling`, `running`, `hiking`, `walking`, `kayak`, etc. |
| `event_types` | Garmin event flags (e.g. `["END"]`) |
| `has_point_end` | `true` when Garmin signals the activity has ended |

### Device Tracker: `device_tracker.garmin_livetrack_tracker_{prefix}`

Only created when the device tracker option is enabled in the integration settings.

In Home Assistant, device tracker entities represent the location of a device or person. They can be associated with a [Person](https://www.home-assistant.io/integrations/person/) entity under **Settings → People**, alongside other trackers like your phone's companion app or router-based presence detection. HA combines all associated trackers to determine the person's overall location, prioritizing the most recently updated source.

During active sessions, the LiveTrack device tracker reports the activity coordinates and will reflect zone names (e.g. `home`, `not_home`, or a custom zone) just like any other tracker. It also includes speed, altitude, heart rate, distance, duration and activity type as attributes.

> **Tip — avoiding location conflicts with your phone:** If both your phone and the LiveTrack device tracker are associated with the same Person, your phone's less accurate location updates (especially when it's in a pocket without navigation active) can interfere, causing the person's position to jump back and forth. A practical solution is to temporarily swap the trackers during an active session using the `person.add_device_tracker` and `person.remove_device_tracker` services provided by [Spook](https://spook.boo/person/). You can automate this by listening for `garmin_livetrack_activity_started` and `garmin_livetrack_activity_ended` events.

## Events

All events include `person_id` and `person_name` in the payload.

| Event | Fired when | Additional payload |
|-------|------------|-------------------|
| `garmin_livetrack_activity_started` | LiveTrack email detected, monitoring begins | `session_id`, `livetrack_url` |
| `garmin_livetrack_activity_detected` | First data point reveals the activity type | `activity_type`, `datetime` |
| `garmin_livetrack_point_received` | A new data point arrives | Full point data: coordinates, speed, altitude, heart rate, distance, duration, activity type, session ID, and all other available fields |
| `garmin_livetrack_activity_ended` | Session finishes | `activity_type`, `duration`, `distance_km`, last known coordinates, `session_id` |

### Example automation

```yaml
automation:
  - alias: "Notify when someone starts a Garmin activity"
    trigger:
      - platform: event
        event_type: garmin_livetrack_activity_started
    action:
      - service: notify.mobile_app
        data:
          title: "LiveTrack"
          message: >
            {{ trigger.event.data.person_name }} started a LiveTrack session!
          data:
            url: "{{ trigger.event.data.livetrack_url }}"

  - alias: "Log every new activity point"
    trigger:
      - platform: event
        event_type: garmin_livetrack_point_received
    condition:
      - condition: template
        value_template: "{{ trigger.event.data.has_location }}"
    action:
      - service: logbook.log
        data:
          name: "{{ trigger.event.data.person_name }}"
          message: >
            {{ trigger.event.data.activity_type }} —
            {{ trigger.event.data.distance_km }} km,
            {{ trigger.event.data.duration }},
            ❤️ {{ trigger.event.data.heartrate }} bpm
```

## State behavior

| Situation | Sensor state | Attributes |
|-----------|-------------|------------|
| Home Assistant starts up | `idle` | Empty |
| LiveTrack email arrives | `active` | Cleared — no stale data from previous sessions |
| First data point with coordinates | `active` | Populated, `has_location: true` |
| Active session, no GPS fix yet | `active` | Partial data, `has_location: false` |
| Session ends | `finished` | Last known values preserved |
| New session starts for same person | `active` | Cleared and repopulated |

Session state is not persisted across Home Assistant restarts. Every entity returns to `idle` on startup, and an **in-progress** activity that was being tracked before the restart is **not resumed** — the integration only reacts to new notification emails that arrive after it has started. This is a deliberate trade-off: the IMAP listener establishes a UID watermark on startup and skips every message that was already in the inbox, which prevents old LiveTrack emails (from previous sessions or HA downtime) from accidentally triggering fresh tracking.

## Managing tracked people

Go to the integration entry in **Settings → Devices & Services**, click **Configure**, and choose:

- **Add tracked person** — provide their full name as it appears in the Garmin email and choose an entity prefix
- **Remove tracked person** — select from the configured list
- **General settings** — toggle device tracker, adjust poll interval, change email age threshold

## Troubleshooting

**Integration doesn't detect emails:**
- Verify your IMAP credentials work with a regular email client
- For Gmail, ensure you're using an [App Password](https://myaccount.google.com/apppasswords)
- Confirm that the person's full name is spelled exactly as Garmin sends it in the email body
- Make sure your email account is listed as a LiveTrack recipient in the person's Garmin Connect settings

**Sensors stay in `idle`:**
- Check logs at **Settings → System → Logs**, filter by `garmin_livetrack`
- Look for "IMAP listener started" to confirm the email monitor is running

**Data stops updating during a session:**
- The integration automatically retries and recovers from transient errors
- Check logs for persistent error messages

## License

This project is licensed under the MIT License.

Copyright (c) 2026 Garmin LiveTrack Monitor Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
