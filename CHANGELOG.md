# Changelog

All notable changes to this integration will be documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.3] — 2026-04-30

### Fixed

- IMAP listener no longer silently stops detecting LiveTrack emails after a network blip or long quiet period. The IDLE loop now has a 5-minute watchdog that detects half-open connections and other silent hangs, recovering automatically without needing a Home Assistant restart.

## [1.0.2] — 2026-04-25

### Changed

- Scraper now uses dedicated JSON endpoints instead of parsing the full LiveTrack HTML page on every poll. Drastically lower bandwidth and more resilient to Garmin UI changes.
- Track-point fetches are throttled to the device's own posting frequency, avoiding redundant requests.

### Added

- Final track-points fetch on session end so the END marker and last coordinates are never missed.
- Standalone health-check script (`scripts/garmin-livetrack-check.sh`) to verify Garmin endpoints from the command line.

## [1.0.1] — 2026-04-24

### Fixed

- The device tracker now preserves the last known GPS coordinates after a session ends, so Home Assistant's zone matching resolves correctly (e.g. to `home`) once the activity finishes.

## [1.0.0] — 2026-04-24

First public release.

### Features

- Automatic session detection via IMAP IDLE push.
- Real-time activity data: position, speed, altitude, heart rate, power, cadence, distance, duration, activity type.
- Multi-person support with concurrent session tracking.
- One sensor per person (`idle` / `active` / `finished`) plus an optional device tracker.
- Four events for automations covering the full activity lifecycle.
- Config flow UI — no YAML required.
- Provider-agnostic IMAP (Gmail, Outlook, Office 365, Yahoo, iCloud, Dovecot, etc.).
- Unicode-safe person matching across accents and non-Latin scripts.
- Diagnostics platform with sanitized runtime state for bug reports.
- English and Spanish translations.
