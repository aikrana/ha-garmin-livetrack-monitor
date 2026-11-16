# Changelog

All notable changes to this integration will be documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] — 2026-11-16

### Added

- Manual mode: set the integration up without an email account and start each session with the new `garmin_livetrack.start_tracking` action, which takes a person and a LiveTrack link (#5).
- `garmin_livetrack.stop_tracking` ends a person's running session as if Garmin had ended it.
- The IMAP folder can be chosen at setup and changed later under **Configure → Email settings**, so emails that a filter moves to a subfolder are found (#4).

### Changed

- The device tracker's state is now always the zone (`home`, `not_home` or a zone name), also during a session. It used to be the activity name, which Home Assistant stops supporting in 2027.7 (#3); automations that checked for it should use the `activity_type` attribute or the sensor's `active` state.
- The email age threshold moves from General settings to the new Email settings.

### Fixed

- When Garmin keeps failing (errors, rate limits, a retired endpoint), the integration waits longer between attempts, up to 5 minutes (15 when Garmin asks it to slow down), instead of retrying every few seconds. The failure is logged once instead of on every poll.
- Failed requests no longer write the LiveTrack token to the log.
- The device tracker uses the current `TrackerEntity` import; the old alias stops working in Home Assistant 2027.6 (thanks @narjekdjcusbe, #2).
- The IMAP check during setup closes its test connection again.

## [1.0.4] — 2026-11-14

### Fixed

- Sessions finish again when the activity ends. Garmin retired the session endpoint the integration polled, so the end was never detected and the sensor stayed `active` (and kept polling Garmin) until Home Assistant restarted or the person's next session started.
- Track points are no longer requested once Garmin stops serving them, which it now does right after an activity ends. This removes a stream of 403 errors and retries.

### Changed

- The health-check script uses the new session endpoint and skips the track-points step for sessions that are no longer viewable.

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
