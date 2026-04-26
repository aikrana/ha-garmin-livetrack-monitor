"""Central hub for Garmin LiveTrack Monitor.

Ties together:
  • IMAP listener  → detects new sessions via email
  • Scraper         → fetches live data from Garmin
  • Entity updates  → pushes data into HA sensors / device-trackers
  • Events          → fires HA events at key moments
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    ATTR_ACTIVITY_TYPE,
    ATTR_ALTITUDE,
    ATTR_CADENCE,
    ATTR_DATETIME,
    ATTR_DISTANCE_KM,
    ATTR_DURATION,
    ATTR_DURATION_SECS,
    ATTR_ELEVATION_SOURCE,
    ATTR_EVENT_TYPES,
    ATTR_HAS_LOCATION,
    ATTR_HAS_POINT_END,
    ATTR_HEARTRATE,
    ATTR_LATITUDE,
    ATTR_LIVETRACK_URL,
    ATTR_LONGITUDE,
    ATTR_PACE,
    ATTR_PERSON_ID,
    ATTR_PERSON_NAME,
    ATTR_POINT_STATUS,
    ATTR_POWER_WATTS,
    ATTR_SESSION_END,
    ATTR_SESSION_ID,
    ATTR_SESSION_START,
    ATTR_SPEED,
    ATTR_SPEED_KMH,
    ATTR_TOKEN,
    CONF_EMAIL_MAX_AGE,
    CONF_ENABLE_DEVICE_TRACKER,
    CONF_IMAP_FOLDER,
    CONF_IMAP_PASSWORD,
    CONF_IMAP_PORT,
    CONF_IMAP_SERVER,
    CONF_IMAP_USERNAME,
    CONF_PERSON_ID,
    CONF_PERSON_NAME,
    CONF_PERSONS,
    CONF_POLL_INTERVAL,
    CONF_SENDER,
    DEFAULT_EMAIL_MAX_AGE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POST_TRACK_POINT_FREQUENCY,
    DOMAIN,
    EVENT_ACTIVITY_DETECTED,
    EVENT_ACTIVITY_ENDED,
    EVENT_ACTIVITY_STARTED,
    EVENT_POINT_RECEIVED,
    STATE_ACTIVE,
    STATE_FINISHED,
    STATE_IDLE,
)
from .imap_listener import IMAPListener
from .scraper import LiveTrackScraper

_LOGGER = logging.getLogger(__name__)

SIGNAL_UPDATE = f"{DOMAIN}_update"
SIGNAL_SESSION_CHANGE = f"{DOMAIN}_session_change"

# Upper bound for scraper error backoff (seconds).  With a default
# poll_interval of 6 s, this is reached after 6 consecutive failures
# (6 → 12 → 24 → 48 → 96 → 192 → 300).  A very long ride with a flaky
# connection will retry at 5-minute intervals until the session ends.
SCRAPER_BACKOFF_MAX = 300


@dataclass
class PersonConfig:
    """A tracked person's configuration."""

    name: str
    entity_prefix: str


@dataclass
class PersonState:
    """Runtime state for a tracked person."""

    config: PersonConfig
    state: str = STATE_IDLE
    # Session
    session_id: str | None = None
    token: str | None = None
    livetrack_url: str | None = None
    session_start: str | None = None
    session_end: str | None = None
    # Last track-point
    last_datetime: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    speed: float | None = None
    altitude: float | None = None
    distance_km: float | None = None
    duration_secs: float | None = None
    duration_str: str | None = None
    heartrate: int | None = None
    power_watts: float | None = None
    cadence: float | None = None
    activity_type: str | None = None
    event_types: list[str] = field(default_factory=list)
    point_status: str | None = None
    elevation_source: str | None = None
    has_location: bool = False
    has_point_end: bool = False
    # Internal
    _activity_event_emitted: bool = False

    @property
    def attributes(self) -> dict[str, Any]:
        """Return all attributes for the sensor entity."""
        return {
            ATTR_PERSON_NAME: self.config.name,
            ATTR_PERSON_ID: self.config.entity_prefix,
            ATTR_SESSION_ID: self.session_id,
            ATTR_TOKEN: self.token,
            ATTR_LIVETRACK_URL: self.livetrack_url,
            ATTR_SESSION_START: self.session_start,
            ATTR_SESSION_END: self.session_end,
            ATTR_DATETIME: self.last_datetime,
            ATTR_LATITUDE: self.latitude,
            ATTR_LONGITUDE: self.longitude,
            ATTR_SPEED: self.speed,
            ATTR_SPEED_KMH: round(self.speed * 3.6, 2) if self.speed else None,
            ATTR_PACE: (
                round(16.666666667 / self.speed, 2)
                if self.speed and self.speed > 0
                else None
            ),
            ATTR_ALTITUDE: self.altitude,
            ATTR_DISTANCE_KM: self.distance_km,
            ATTR_DURATION: self.duration_str,
            ATTR_DURATION_SECS: self.duration_secs,
            ATTR_HEARTRATE: self.heartrate,
            ATTR_POWER_WATTS: self.power_watts,
            ATTR_CADENCE: self.cadence,
            ATTR_ACTIVITY_TYPE: self.activity_type,
            ATTR_EVENT_TYPES: self.event_types,
            ATTR_POINT_STATUS: self.point_status,
            ATTR_ELEVATION_SOURCE: self.elevation_source,
            ATTR_HAS_LOCATION: self.has_location,
            ATTR_HAS_POINT_END: self.has_point_end,
        }

    @property
    def point_attributes(self) -> dict[str, Any]:
        """Return point data for the point_received event."""
        return {
            ATTR_PERSON_ID: self.config.entity_prefix,
            ATTR_PERSON_NAME: self.config.name,
            ATTR_DATETIME: self.last_datetime,
            ATTR_LATITUDE: self.latitude,
            ATTR_LONGITUDE: self.longitude,
            ATTR_SPEED: self.speed,
            ATTR_SPEED_KMH: round(self.speed * 3.6, 2) if self.speed else None,
            ATTR_ALTITUDE: self.altitude,
            ATTR_DISTANCE_KM: self.distance_km,
            ATTR_DURATION: self.duration_str,
            ATTR_DURATION_SECS: self.duration_secs,
            ATTR_HEARTRATE: self.heartrate,
            ATTR_POWER_WATTS: self.power_watts,
            ATTR_CADENCE: self.cadence,
            ATTR_ACTIVITY_TYPE: self.activity_type,
            ATTR_EVENT_TYPES: self.event_types,
            ATTR_HAS_LOCATION: self.has_location,
            ATTR_SESSION_ID: self.session_id,
        }

    def reset_for_new_session(
        self, session_id: str, token: str, livetrack_url: str
    ) -> None:
        """Clear point data and set up for a new session."""
        self.session_id = session_id
        self.token = token
        self.livetrack_url = livetrack_url
        self.session_start = None
        self.session_end = None
        self.last_datetime = None
        self.latitude = None
        self.longitude = None
        self.speed = None
        self.altitude = None
        self.distance_km = None
        self.duration_secs = None
        self.duration_str = None
        self.heartrate = None
        self.power_watts = None
        self.cadence = None
        self.activity_type = None
        self.event_types = []
        self.point_status = None
        self.elevation_source = None
        self.has_location = False
        self.has_point_end = False
        self._activity_event_emitted = False

    def apply_point(self, pt: dict) -> bool:
        """Apply a track-point dict.  Returns True if this is a new point."""
        new_dt = pt.get("dateTime")
        if new_dt == self.last_datetime:
            return False  # Duplicate point, no change

        pos = pt.get("position") or {}
        lat = pos.get("lat")
        lon = pos.get("lon")

        self.last_datetime = new_dt
        self.speed = pt.get("speed")
        self.altitude = pt.get("altitude")
        self.activity_type = (pt.get("activityType") or "").lower() or None
        self.event_types = pt.get("eventTypes", [])
        self.point_status = (pt.get("pointStatus") or "").lower() or None
        self.elevation_source = pt.get("elevationSource")
        self.heartrate = pt.get("heartRateBeatsPerMin")
        self.power_watts = pt.get("powerWatts")
        self.cadence = pt.get("cadenceCyclesPerMin")

        total_dist = pt.get("totalDistanceMeters")
        self.distance_km = (
            round(total_dist / 1000, 2) if total_dist is not None else None
        )

        dur = pt.get("totalDurationSecs")
        self.duration_secs = dur
        if dur is not None:
            try:
                h, remainder = divmod(int(dur), 3600)
                m, s = divmod(remainder, 60)
                self.duration_str = f"{h:02d}:{m:02d}:{s:02d}"
            except (ValueError, TypeError):
                self.duration_str = None

        self.has_point_end = "END" in self.event_types

        if lat is not None and lon is not None:
            self.latitude = lat
            self.longitude = lon
            self.has_location = True

        return True


class LiveTrackHub:
    """Central manager for all LiveTrack operations."""

    def __init__(self, hass: HomeAssistant, config_entry) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self._imap: IMAPListener | None = None
        self._persons: dict[str, PersonConfig] = {}
        self._states: dict[str, PersonState] = {}
        self._scrapers: dict[str, LiveTrackScraper] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._enable_device_tracker: bool = False

    @property
    def persons(self) -> dict[str, PersonConfig]:
        return self._persons

    @property
    def enable_device_tracker(self) -> bool:
        return self._enable_device_tracker

    def get_state(self, person_id: str) -> PersonState | None:
        return self._states.get(person_id)

    def diagnostics(self) -> dict[str, Any]:
        """Return a sanitized snapshot of runtime state for the diagnostics
        platform.  Secrets (token, coordinates, full URL) are NOT included."""
        persons: dict[str, dict[str, Any]] = {}
        for pid, cfg in self._persons.items():
            state = self._states.get(pid)
            persons[pid] = {
                "entity_prefix": cfg.entity_prefix,
                "name_length": len(cfg.name),  # avoid leaking the real name
                "state": state.state if state else None,
                "session_active": bool(state and state.session_id) if state else False,
                "activity_type": state.activity_type if state else None,
                "has_location": state.has_location if state else None,
                "has_point_end": state.has_point_end if state else None,
                "session_start": state.session_start if state else None,
                "session_end": state.session_end if state else None,
                "last_datetime": state.last_datetime if state else None,
                "duration_secs": state.duration_secs if state else None,
                "distance_km": state.distance_km if state else None,
                "tracking_task_running": pid in self._tasks
                and not self._tasks[pid].done(),
                "scraper_active": pid in self._scrapers,
            }
        return {
            "device_tracker_enabled": self._enable_device_tracker,
            "person_count": len(self._persons),
            "active_sessions": sum(
                1 for s in self._states.values() if s.session_id is not None
            ),
            "persons": persons,
            "imap": self._imap.diagnostics() if self._imap else None,
        }

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def async_setup(self) -> None:
        data = self.config_entry.data
        options = self.config_entry.options

        persons_raw = options.get(CONF_PERSONS) or data.get(CONF_PERSONS, [])
        for p in persons_raw:
            pid = p[CONF_PERSON_ID]
            self._persons[pid] = PersonConfig(
                name=p[CONF_PERSON_NAME], entity_prefix=pid
            )
            self._states[pid] = PersonState(config=self._persons[pid])

        self._enable_device_tracker = options.get(
            CONF_ENABLE_DEVICE_TRACKER,
            data.get(CONF_ENABLE_DEVICE_TRACKER, False),
        )

        person_names = [p.name for p in self._persons.values()]
        self._imap = IMAPListener(
            hass=self.hass,
            server=data[CONF_IMAP_SERVER],
            port=data[CONF_IMAP_PORT],
            username=data[CONF_IMAP_USERNAME],
            password=data[CONF_IMAP_PASSWORD],
            folder=data.get(CONF_IMAP_FOLDER, "INBOX"),
            sender=data.get(CONF_SENDER, "noreply@garmin.com"),
            person_names=person_names,
            max_age_minutes=options.get(
                CONF_EMAIL_MAX_AGE,
                data.get(CONF_EMAIL_MAX_AGE, DEFAULT_EMAIL_MAX_AGE),
            ),
            callback=self._on_livetrack_email,
        )
        await self._imap.start()

    async def async_shutdown(self) -> None:
        if self._imap:
            await self._imap.stop()
        for task in self._tasks.values():
            task.cancel()
        for scraper in self._scrapers.values():
            scraper.close()
        self._tasks.clear()
        self._scrapers.clear()

    async def async_update_options(self) -> None:
        options = self.config_entry.options

        persons_raw = options.get(CONF_PERSONS, [])
        new_ids = {p[CONF_PERSON_ID] for p in persons_raw}
        old_ids = set(self._persons.keys())

        for pid in old_ids - new_ids:
            await self._stop_tracking(pid)
            del self._persons[pid]
            del self._states[pid]

        for p in persons_raw:
            pid = p[CONF_PERSON_ID]
            self._persons[pid] = PersonConfig(
                name=p[CONF_PERSON_NAME], entity_prefix=pid
            )
            if pid not in self._states:
                self._states[pid] = PersonState(config=self._persons[pid])
            else:
                self._states[pid].config = self._persons[pid]

        self._enable_device_tracker = options.get(
            CONF_ENABLE_DEVICE_TRACKER, False
        )

        if self._imap:
            self._imap.update_person_names(
                [p.name for p in self._persons.values()]
            )

        async_dispatcher_send(self.hass, SIGNAL_SESSION_CHANGE)

    # ── IMAP callback ────────────────────────────────────────────────────

    async def _on_livetrack_email(
        self,
        session_id: str,
        token: str,
        person_name: str,
        livetrack_url: str,
    ) -> None:
        person_id = None
        for pid, cfg in self._persons.items():
            if cfg.name.lower() == person_name.lower():
                person_id = pid
                break
        if person_id is None:
            _LOGGER.warning(
                "Matched person name '%s' but no config found", person_name
            )
            return

        _LOGGER.info(
            "Starting session for %s (session=%s)", person_name, session_id
        )

        await self._stop_tracking(person_id)

        state = self._states[person_id]
        state.state = STATE_ACTIVE
        state.reset_for_new_session(session_id, token, livetrack_url)

        async_dispatcher_send(self.hass, SIGNAL_UPDATE, person_id)
        async_dispatcher_send(self.hass, SIGNAL_SESSION_CHANGE)

        self._scrapers[person_id] = LiveTrackScraper(session_id, token)
        self._tasks[person_id] = asyncio.create_task(
            self._tracking_loop(person_id)
        )

        self.hass.bus.async_fire(
            EVENT_ACTIVITY_STARTED,
            {
                ATTR_PERSON_ID: person_id,
                ATTR_PERSON_NAME: person_name,
                ATTR_SESSION_ID: session_id,
                ATTR_LIVETRACK_URL: livetrack_url,
            },
        )

    # ── Tracking loop ────────────────────────────────────────────────────

    def _compute_begin(self, state: PersonState) -> str | None:
        """Compute the 'begin' parameter for incremental fetching.

        Logic matches the original GraphQL integration:
          begin = max(last_point_datetime, session_start)
        This ensures we always get at least the latest point, and on the
        first fetch of a session we get all points since session start.
        Returns None if no reference time is available (fetch all).
        """
        candidates = []
        if state.last_datetime:
            candidates.append(state.last_datetime)
        if state.session_start:
            candidates.append(state.session_start)
        return max(candidates) if candidates else None

    async def _tracking_loop(self, person_id: str) -> None:
        state = self._states[person_id]
        scraper = self._scrapers[person_id]
        poll_interval = self.config_entry.options.get(
            CONF_POLL_INTERVAL,
            self.config_entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
        )

        # Exponential backoff state for scraper failures.  The first error
        # waits `poll_interval` (same as a normal tick), each subsequent
        # consecutive error doubles the wait up to SCRAPER_BACKOFF_MAX.  A
        # successful fetch resets the counter.  Rationale: transient network
        # blips shouldn't pound Garmin for no reason, but we must not give up
        # entirely either — a long ride can outlast any reasonable cap.
        consecutive_failures = 0

        # Throttle window for the (more expensive) track-points request.
        # The session metadata request runs every poll_interval regardless;
        # track-points is gated by the device's posting frequency reported in
        # the session payload (`postTrackPointFrequency`, seconds).
        # next_track_points_allowed_at is an epoch-seconds timestamp.  0
        # means "no point seen yet, fire-at-will".
        next_track_points_allowed_at = 0.0
        post_freq = DEFAULT_POST_TRACK_POINT_FREQUENCY

        _LOGGER.debug("Tracking loop started for %s", person_id)

        try:
            while state.state == STATE_ACTIVE:
                # ── 1. Session metadata (always, every iteration) ──────────
                # Cheap (~500 B), drives end-of-activity detection.
                try:
                    session = await self.hass.async_add_executor_job(
                        scraper.fetch_session
                    )
                except Exception as err:  # noqa: BLE001
                    consecutive_failures += 1
                    backoff = min(
                        poll_interval * (2 ** (consecutive_failures - 1)),
                        SCRAPER_BACKOFF_MAX,
                    )
                    log_fn = (
                        _LOGGER.warning
                        if consecutive_failures <= 3
                        else _LOGGER.error
                    )
                    log_fn(
                        "Session fetch error for %s "
                        "(attempt %d, retry in %ds): %s",
                        person_id, consecutive_failures, backoff, err,
                    )
                    await asyncio.sleep(backoff)
                    continue

                # Apply session info
                if session.get("start"):
                    state.session_start = session["start"]
                    state.session_end = session.get("end")
                if session.get("post_track_point_frequency"):
                    post_freq = session["post_track_point_frequency"]

                # Has the session ended on the server side?  Don't break yet —
                # we still want one final track-points fetch to capture the
                # last position (and the END marker if it exists).  That fetch
                # bypasses the throttle below.
                session_ended = bool(session) and not session.get(
                    "in_progress", True
                )

                # ── 2. Track-points (throttled by device posting frequency) ─
                now = time.time()
                should_fetch_points = (
                    session_ended  # final fetch on session-end, ignore throttle
                    or now >= next_track_points_allowed_at
                )

                if should_fetch_points:
                    begin = self._compute_begin(state)
                    try:
                        points = await self.hass.async_add_executor_job(
                            scraper.fetch_track_points, begin
                        )
                    except Exception as err:  # noqa: BLE001
                        consecutive_failures += 1
                        backoff = min(
                            poll_interval * (2 ** (consecutive_failures - 1)),
                            SCRAPER_BACKOFF_MAX,
                        )
                        log_fn = (
                            _LOGGER.warning
                            if consecutive_failures <= 3
                            else _LOGGER.error
                        )
                        log_fn(
                            "Track-points fetch error for %s "
                            "(attempt %d, retry in %ds): %s",
                            person_id, consecutive_failures, backoff, err,
                        )
                        await asyncio.sleep(backoff)
                        continue

                    # Both halves of this iteration succeeded — reset backoff.
                    if consecutive_failures:
                        _LOGGER.info(
                            "Scraper recovered for %s after %d failure(s)",
                            person_id, consecutive_failures,
                        )
                        consecutive_failures = 0

                    if points:
                        last_point = points[-1]
                        is_new = state.apply_point(last_point)

                        # Activity detected event (once per session)
                        if (
                            not state._activity_event_emitted
                            and state.activity_type
                            and state.last_datetime
                            and state.session_start
                        ):
                            state._activity_event_emitted = True
                            self.hass.bus.async_fire(
                                EVENT_ACTIVITY_DETECTED,
                                {
                                    ATTR_PERSON_ID: person_id,
                                    ATTR_PERSON_NAME: state.config.name,
                                    ATTR_ACTIVITY_TYPE: state.activity_type,
                                    ATTR_DATETIME: state.last_datetime,
                                },
                            )

                        # Point-received event (every genuinely new point)
                        if is_new:
                            self.hass.bus.async_fire(
                                EVENT_POINT_RECEIVED,
                                state.point_attributes,
                            )

                        # Schedule next allowed track-points fetch.  Anchor on
                        # the dateTime of the last received point + the
                        # device's posting frequency + a 2-second grace buffer
                        # for round-trip + clock drift.
                        if state.last_datetime:
                            try:
                                last_ts = datetime.fromisoformat(
                                    state.last_datetime.replace("Z", "+00:00")
                                ).timestamp()
                                next_track_points_allowed_at = (
                                    last_ts + post_freq + 2
                                )
                            except (ValueError, TypeError):
                                pass

                        # END flag inside the point itself terminates the
                        # session (Garmin only sends this when the user
                        # actually pressed STOP+SAVE on the watch, not always).
                        if state.has_point_end and state.state == STATE_ACTIVE:
                            _LOGGER.info(
                                "Session finished for %s (END event)",
                                person_id,
                            )
                            state.state = STATE_FINISHED
                else:
                    # Session-only iteration succeeded; clear backoff.
                    if consecutive_failures:
                        _LOGGER.info(
                            "Scraper recovered for %s after %d failure(s)",
                            person_id, consecutive_failures,
                        )
                        consecutive_failures = 0

                # Honour server-side end detection if the END marker didn't.
                if session_ended and state.state == STATE_ACTIVE:
                    _LOGGER.info(
                        "Session finished for %s (server-side end timestamp)",
                        person_id,
                    )
                    state.state = STATE_FINISHED

                async_dispatcher_send(self.hass, SIGNAL_UPDATE, person_id)

                if state.state == STATE_FINISHED:
                    break

                await asyncio.sleep(poll_interval)

        except asyncio.CancelledError:
            _LOGGER.debug("Tracking loop cancelled for %s", person_id)
            return

        self.hass.bus.async_fire(
            EVENT_ACTIVITY_ENDED,
            {
                ATTR_PERSON_ID: person_id,
                ATTR_PERSON_NAME: state.config.name,
                ATTR_ACTIVITY_TYPE: state.activity_type,
                ATTR_DURATION: state.duration_str,
                ATTR_DISTANCE_KM: state.distance_km,
                ATTR_LATITUDE: state.latitude,
                ATTR_LONGITUDE: state.longitude,
                ATTR_SESSION_ID: state.session_id,
            },
        )

        async_dispatcher_send(self.hass, SIGNAL_UPDATE, person_id)
        async_dispatcher_send(self.hass, SIGNAL_SESSION_CHANGE)

        if person_id in self._scrapers:
            self._scrapers[person_id].close()
            del self._scrapers[person_id]
        self._tasks.pop(person_id, None)

        _LOGGER.info("Tracking loop ended for %s", person_id)

    async def _stop_tracking(self, person_id: str) -> None:
        task = self._tasks.pop(person_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        scraper = self._scrapers.pop(person_id, None)
        if scraper:
            scraper.close()
