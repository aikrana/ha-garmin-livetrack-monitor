"""LiveTrack scraper that bypasses Cloudflare using cloudscraper.

The current Garmin LiveTrack web app is a Next.js SSR site behind Cloudflare,
serving its data over a CSRF-protected REST API.  Two endpoints are used:

- ``GET /api/sessions/{sessionId}?token={token}`` — session metadata
  (start, end, post-track-point frequency, display name, …).
- ``GET /api/sessions/{sessionId}/track-points/common?token={token}&begin=…``
  — incremental track-points.

Both require:

- The Cloudflare cookies that ``cloudscraper`` obtains transparently.
- A ``livetrack-csrf-token`` UUID extracted from the ``<meta name="csrf-token">``
  tag of any HTML page on the domain.  We GET ``/`` once per scraper instance
  to discover it, and refresh it on HTTP 403.

The CSRF is **not session-bound**: a token obtained from ``/`` works for API
calls referencing any sessionId, and remains valid for the lifetime of the
scraper instance unless the server invalidates it (which surfaces as 403).

See ``docs/CONTEXT.md`` → "History of API Changes" for the historical phases
(Legacy REST → GraphQL → current REST + CSRF).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import quote

_LOGGER = logging.getLogger(__name__)

_BASE = "https://livetrack.garmin.com"

# Matches <meta name="csrf-token" content="…uuid…"> in the home page HTML.
_CSRF_META_RE = re.compile(rb'name="csrf-token"[^>]*content="([0-9a-f-]+)"')


class LiveTrackScraper:
    """Stateful scraper for a single LiveTrack session.

    Holds Cloudflare cookies + CSRF token across calls.  Both ``fetch_session``
    and ``fetch_track_points`` transparently refresh the CSRF on a single 403,
    so callers get either valid data or an empty/sentinel response without
    having to handle CSRF rotation themselves.
    """

    def __init__(self, session_id: str, token: str) -> None:
        self.session_id = session_id
        self.token = token
        self._scraper = None
        self._csrf: str | None = None

    # ── Public API ──────────────────────────────────────────────────────

    def fetch_session(self) -> dict:
        """Fetch session metadata.

        Returns a dict with the following keys (all optional except where
        noted):

        - ``start`` (ISO 8601 string) — activity start.
        - ``end`` (ISO 8601 string) — activity end (or start + 24h while live).
        - ``in_progress`` (bool) — True while ``end == start + 86400`` (Garmin's
          placeholder for in-flight sessions).
        - ``post_track_point_frequency`` (int, seconds) — how often the device
          posts new points to Garmin's servers.  Used by callers to throttle
          ``fetch_track_points`` calls.
        - ``display_name`` (str) — the publishing user's display name.
        - ``session_name`` (str) — Garmin's auto-generated session name.
        - ``viewable`` (bool) — whether the session is publicly viewable.
        - ``privacy_level`` (str) — e.g. ``PUBLIC``.

        Returns an empty dict on hard failure (network error, 4xx other than
        403, malformed JSON).  On 403 the CSRF is refreshed and the call is
        retried once before giving up.
        """
        self._ensure_scraper()
        if self._csrf is None:
            self._fetch_csrf()
            if self._csrf is None:
                return {}

        result = self._do_fetch_session()
        if result is None:  # 403 sentinel
            _LOGGER.debug("Session 403 → refreshing CSRF and retrying")
            self._fetch_csrf()
            if self._csrf is None:
                return {}
            result = self._do_fetch_session()
            if result is None:
                _LOGGER.warning("Session still 403 after CSRF refresh")
                return {}
        return result

    def fetch_track_points(self, begin: str | None = None) -> list:
        """Fetch track-points (optionally only those after ``begin``).

        Returns a list of point dicts.  Empty list on any failure mode
        (network error, 4xx other than 403, malformed JSON, no points yet).
        On 403 the CSRF is refreshed and the call is retried once.
        """
        self._ensure_scraper()
        if self._csrf is None:
            self._fetch_csrf()
            if self._csrf is None:
                return []

        points = self._do_fetch_track_points(begin)
        if points is None:  # 403 sentinel
            _LOGGER.debug("Track-points 403 → refreshing CSRF and retrying")
            self._fetch_csrf()
            if self._csrf is None:
                return []
            points = self._do_fetch_track_points(begin)
            if points is None:
                _LOGGER.warning("Track-points still 403 after CSRF refresh")
                return []
        return points

    def close(self) -> None:
        """Drop the cloudscraper session and CSRF token."""
        self._scraper = None
        self._csrf = None

    # ── Internals ───────────────────────────────────────────────────────

    def _ensure_scraper(self) -> None:
        if self._scraper is None:
            import cloudscraper
            self._scraper = cloudscraper.create_scraper()

    def _fetch_csrf(self) -> None:
        """GET ``/`` and extract the CSRF token from the ``<meta>`` tag.

        Side effects: sets ``self._csrf`` (or ``None`` on failure).  Also
        primes the cloudscraper session with the Cloudflare cookies needed
        for subsequent ``/api/...`` calls.

        Why ``/`` and not the session URL: the home page is small (~37 KB),
        constant in size (doesn't grow with activity duration), and the
        token it returns works for *any* sessionId.  Pre-1.0.2 versions
        downloaded the session-specific page on every poll, which grew to
        ~48 KB+ for long activities and required brittle SSR-HTML regex
        parsing.
        """
        try:
            r = self._scraper.get(f"{_BASE}/", timeout=15)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("CSRF discovery request error: %s", err)
            self._csrf = None
            return

        if r.status_code != 200:
            _LOGGER.warning(
                "CSRF discovery returned HTTP %s (expected 200)", r.status_code
            )
            self._csrf = None
            return

        m = _CSRF_META_RE.search(r.content)
        if not m:
            _LOGGER.warning(
                "CSRF token meta tag not found at %s/  — Garmin may have "
                "changed the page layout",
                _BASE,
            )
            self._csrf = None
            return

        self._csrf = m.group(1).decode("ascii")
        _LOGGER.debug("Discovered CSRF token from /")

    def _do_fetch_session(self) -> dict | None:
        """Single GET to /api/sessions/{id}.

        Returns parsed dict on 200, ``None`` on 403, empty dict on any other
        failure.  Callers use the ``None`` sentinel to trigger CSRF refresh.
        """
        url = f"{_BASE}/api/sessions/{self.session_id}?token={self.token}"
        try:
            r = self._scraper.get(url, headers=self._headers(), timeout=15)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Session request error: %s", err)
            return {}

        if r.status_code == 200:
            try:
                data = r.json()
            except ValueError as err:
                _LOGGER.warning("Session JSON parse error: %s", err)
                return {}
            return self._parse_session(data)
        if r.status_code == 403:
            return None
        _LOGGER.warning("Session returned HTTP %s", r.status_code)
        return {}

    def _do_fetch_track_points(self, begin: str | None) -> list | None:
        url = self._points_url(begin)
        try:
            r = self._scraper.get(url, headers=self._headers(), timeout=15)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Track-points request error: %s", err)
            return []

        if r.status_code == 200:
            try:
                return r.json().get("trackPoints", [])
            except ValueError as err:
                _LOGGER.warning("Track-points JSON parse error: %s", err)
                return []
        if r.status_code == 403:
            return None
        _LOGGER.warning("Track-points returned HTTP %s", r.status_code)
        return []

    @staticmethod
    def _parse_session(data: dict) -> dict:
        start = data.get("start")
        end = data.get("end")
        in_progress = False
        if start and end:
            try:
                s_ts = datetime.fromisoformat(
                    start.replace("Z", "+00:00")
                ).timestamp()
                e_ts = datetime.fromisoformat(
                    end.replace("Z", "+00:00")
                ).timestamp()
                # Garmin sets end = start + 24h while the session is live.
                # 2-second tolerance absorbs minor server-side rounding.
                in_progress = abs(e_ts - s_ts - 86400) < 2
            except (ValueError, TypeError):
                pass

        post_freq = data.get("postTrackPointFrequency")
        try:
            post_freq = int(post_freq) if post_freq is not None else None
        except (ValueError, TypeError):
            post_freq = None

        return {
            "start": start,
            "end": end,
            "in_progress": in_progress,
            "post_track_point_frequency": post_freq,
            "display_name": data.get("userDisplayName"),
            "session_name": data.get("sessionName"),
            "viewable": data.get("viewable"),
            "privacy_level": data.get("privacyLevel"),
        }

    def _points_url(self, begin: str | None = None) -> str:
        url = (
            f"{_BASE}/api/sessions/{self.session_id}"
            f"/track-points/common?token={self.token}"
        )
        if begin:
            # Garmin treats `begin` as exclusive lower bound; bumping the
            # millisecond makes it strictly greater than the last seen point
            # and avoids duplicates without missing anything.
            val = begin.replace(".000Z", ".001Z")
            url += f"&begin={quote(val)}"
        return url

    def _headers(self) -> dict:
        return {
            "accept": "application/json",
            "referer": f"{_BASE}/",
            "livetrack-csrf-token": self._csrf or "",
        }
