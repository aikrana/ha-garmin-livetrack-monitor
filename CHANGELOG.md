# Changelog

All notable changes to this integration will be documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] — 2026-04-24

### Fixed

- **Device tracker coordinates are now preserved after the session ends.** Previously, `latitude` and `longitude` would return `None` once the sensor moved to `finished`, which prevented Home Assistant's zone-matching from resolving the tracker's state (e.g. to `home` or a named zone) after an activity completed. The tracker now keeps reporting the last known GPS point for as long as the session has ever acquired a fix, and `location_name` remains active-only so zone-matching takes over naturally once the activity is over.

## [1.0.0] — 2026-04-24

First public release.

### Features

- **Automatic session detection** via IMAP IDLE push. Monitors your mailbox for Garmin LiveTrack notification emails and starts tracking within seconds of delivery.
- **Real-time activity data**: position, speed, altitude, heart rate, power, cadence, distance, duration, activity type — updated every few seconds for the duration of the session.
- **Multi-person support**: configure several people and monitor concurrent sessions independently.
- **One sensor per person** with states `idle` / `active` / `finished`.
- **Optional device tracker** per person, grouped with the sensor under a single Home Assistant device.
- **Four events** for automation: `garmin_livetrack_activity_started`, `garmin_livetrack_activity_detected`, `garmin_livetrack_point_received`, `garmin_livetrack_activity_ended`.
- **Config flow UI** with two-step setup (IMAP connection → first person) and an options flow to add/remove people and tweak settings later.
- **Provider-agnostic IMAP**: UID-based SEARCH/FETCH, `UIDVALIDITY` handling, IDLE capability detection with NOOP polling fallback. Works with Gmail, Outlook, Office 365, Yahoo, iCloud, Dovecot, Courier, and any other RFC 3501 server.
- **Unicode-safe person matching**: names with accents (`José`, `Müller`), non-Latin scripts (CJK, Cyrillic, Greek) and typographic variants are matched correctly.
- **Diagnostics platform**: one-click download of sanitized runtime state for bug reports, with credentials, tokens and coordinates redacted.
- **Exponential backoff** on scraper errors, capped at 5 minutes, with automatic recovery notice when the first successful fetch comes through.
- **English and Spanish translations** for the config flow and error messages.
