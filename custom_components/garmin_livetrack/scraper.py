"""LiveTrack scraper that bypasses Cloudflare using cloudscraper.

The current Garmin LiveTrack web app is a Next.js SSR site behind Cloudflare,
serving its data over a CSRF-protected REST API.  Two endpoints are used:

- ``GET /api/v2/sessions/{sessionId}?token={token}`` — session metadata
  (start, end, viewable, post-track-point frequency, display name, …).
  The v1 route ``/api/sessions/{sessionId}`` answers 405 to GET since
  November 2026 (it only accepts DELETE now).
- ``GET /api/sessions/{sessionId}/track-points/common?token={token}&begin=…``
  — incremental track-points (there is no v2 route for these).

Both require:

- The Cloudflare cookies that ``cloudscraper`` obtains transparently.
- A ``livetrack-csrf-token`` UUID extracted from the ``<meta name="csrf-token">``
  tag of any HTML page on the domain.  We GET ``/`` once per scraper instance
  to discover it, and refresh it on HTTP 403.
- The ``livetrack_csrf`` cookie set by that same GET.

The CSRF token is not tied to a sessionId (a token obtained from ``/`` works
for API calls referencing any sessionId), but it **is tied to the
``livetrack_csrf`` cookie** issued with it: header without cookie, cookie
without header, or a header from another page load all get 403.  It remains
valid for the lifetime of the scraper instance unless the server invalidates
it (which surfaces as 403).

A request that fails (network error, HTTP error, unreadable JSON) raises
``LiveTrackRequestError`` carrying the HTTP status, so the caller can back
off according to it.

Garmin has changed this API several times (legacy REST → GraphQL → REST +
CSRF → v2 session metadata); expect more.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from urllib.parse import quote

_LOGGER = logging.getLogger(__name__)

_BASE = "https://livetrack.garmin.com"

# Matches <meta name="csrf-token" content="…uuid…"> in the home page HTML.
_CSRF_META_RE = re.compile(rb'name="csrf-token"[^>]*content="([0-9a-f-]+)"')


class LiveTrackRequestError(Exception):
    """A LiveTrack request failed: network error, HTTP error or unreadable body.

    ``status`` is the HTTP status (``None`` when no response came back) and
    ``retry_after`` the server's ``Retry-After`` in seconds, if it sent one.
    """

    def __init__(
        self,
        message: str,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _retry_after(r) -> float | None:
    """``Retry-After`` in seconds, when sent in its delta-seconds form."""
    try:
        return max(0.0, float(r.headers.get("Retry-After")))
    except (TypeError, ValueError):
        return None


class LiveTrackScraper:
    """Stateful scraper for a single LiveTrack session.

    Holds Cloudflare cookies + CSRF token across calls.  Both ``fetch_session``
    and ``fetch_track_points`` transparently refresh the CSRF on a single 403,
    so callers get either valid data or a ``LiveTrackRequestError`` without
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
        - ``in_progress`` (bool) — True until ``end`` is in the past, the same
          rule Garmin's web client uses.  No parseable ``end`` → True.
        - ``post_track_point_frequency`` (int, seconds) — how often the device
          posts new points to Garmin's servers.  Used by callers to throttle
          ``fetch_track_points`` calls.
        - ``display_name`` (str) — the publishing user's display name.
        - ``session_name`` (str) — Garmin's auto-generated session name.
        - ``viewable`` (bool) — whether Garmin still serves the track.  It
          turns False as soon as the session ends; track-points then answer
          403.
        - ``privacy_level`` (str) — first entry of ``privacyLevels``, e.g.
          ``EVERYONE``.

        On 403 the CSRF is refreshed and the call is retried once.  Raises
        ``LiveTrackRequestError`` when the call (or the CSRF discovery) still
        fails: network error, HTTP error, malformed JSON.
        """
        self._ensure_scraper()
        if self._csrf is None:
            self._fetch_csrf()

        result = self._do_fetch_session()
        if result is None:  # 403 sentinel
            _LOGGER.debug("Session 403 → refreshing CSRF and retrying")
            self._fetch_csrf()
            result = self._do_fetch_session()
            if result is None:
                raise LiveTrackRequestError(
                    "Session still 403 after CSRF refresh", 403
                )
        return result

    def fetch_track_points(self, begin: str | None = None) -> list:
        """Fetch track-points (optionally only those after ``begin``).

        Returns a list of point dicts, empty when there are no new points.
        On 403 the CSRF is refreshed and the call is retried once.  Raises
        ``LiveTrackRequestError`` when the call (or the CSRF discovery) still
        fails: network error, HTTP error, malformed JSON.
        """
        self._ensure_scraper()
        if self._csrf is None:
            self._fetch_csrf()

        points = self._do_fetch_track_points(begin)
        if points is None:  # 403 sentinel
            _LOGGER.debug("Track-points 403 → refreshing CSRF and retrying")
            self._fetch_csrf()
            points = self._do_fetch_track_points(begin)
            if points is None:
                raise LiveTrackRequestError(
                    "Track-points still 403 after CSRF refresh", 403
                )
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

        Side effects: sets ``self._csrf``; on failure it is cleared and
        ``LiveTrackRequestError`` is raised.  Also stores the
        ``livetrack_csrf`` cookie the token is bound to, plus the Cloudflare
        cookies, in the cloudscraper session used for subsequent ``/api/...``
        calls.

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
            self._csrf = None
            raise self._network_error("CSRF discovery", err) from err

        if r.status_code != 200:
            self._csrf = None
            raise LiveTrackRequestError(
                f"CSRF discovery returned HTTP {r.status_code} (expected 200)",
                r.status_code,
                _retry_after(r),
            )

        m = _CSRF_META_RE.search(r.content)
        if not m:
            self._csrf = None
            raise LiveTrackRequestError(
                f"CSRF token meta tag not found at {_BASE}/ — Garmin may "
                "have changed the page layout",
                r.status_code,
            )

        self._csrf = m.group(1).decode("ascii")
        _LOGGER.debug("Discovered CSRF token from /")

    def _do_fetch_session(self) -> dict | None:
        """Single GET to /api/v2/sessions/{id}.

        Returns parsed dict on 200 and ``None`` on 403 (callers use the
        sentinel to trigger a CSRF refresh); raises
        ``LiveTrackRequestError`` on any other failure.

        Asks for an uncompressed body: Garmin labels this route's JSON error
        bodies ``Content-Encoding: gzip`` without compressing them, and the
        decoding error would hide the HTTP status.
        """
        url = f"{_BASE}/api/v2/sessions/{self.session_id}?token={self.token}"
        headers = {**self._headers(), "accept-encoding": "identity"}
        try:
            r = self._scraper.get(url, headers=headers, timeout=15)
        except Exception as err:  # noqa: BLE001
            raise self._network_error("Session", err) from err

        if r.status_code == 200:
            try:
                data = r.json()
            except ValueError as err:
                raise LiveTrackRequestError(
                    f"Session JSON parse error: {err}", r.status_code
                ) from err
            return self._parse_session(data)
        if r.status_code == 403:
            return None
        raise LiveTrackRequestError(
            f"Session returned HTTP {r.status_code}",
            r.status_code,
            _retry_after(r),
        )

    def _do_fetch_track_points(self, begin: str | None) -> list | None:
        url = self._points_url(begin)
        try:
            r = self._scraper.get(url, headers=self._headers(), timeout=15)
        except Exception as err:  # noqa: BLE001
            raise self._network_error("Track-points", err) from err

        if r.status_code == 200:
            try:
                return r.json().get("trackPoints", [])
            except ValueError as err:
                raise LiveTrackRequestError(
                    f"Track-points JSON parse error: {err}", r.status_code
                ) from err
        if r.status_code == 403:
            return None
        raise LiveTrackRequestError(
            f"Track-points returned HTTP {r.status_code}",
            r.status_code,
            _retry_after(r),
        )

    def _network_error(self, what: str, err: Exception) -> LiveTrackRequestError:
        """Wrap a request that got no response.

        The exception text often embeds the request URL, so only its type
        goes into the error and the full text, minus the token, to DEBUG.
        """
        _LOGGER.debug(
            "%s request error: %s", what, str(err).replace(self.token, "<token>")
        )
        return LiveTrackRequestError(f"{what} request error: {type(err).__name__}")

    @staticmethod
    def _parse_session(data: dict) -> dict:
        start = data.get("start")
        end = data.get("end")
        # Same rule as Garmin's web client: the session has ended once `end`
        # is in the past.  While live, Garmin keeps `end` in the future
        # (start + 24h placeholder).  An unparseable `end` never ends it.
        in_progress = True
        if end:
            try:
                e_ts = datetime.fromisoformat(
                    end.replace("Z", "+00:00")
                ).timestamp()
                in_progress = e_ts > time.time()
            except (ValueError, TypeError, AttributeError):
                pass

        post_freq = data.get("postTrackPointFrequency")
        try:
            post_freq = int(post_freq) if post_freq is not None else None
        except (ValueError, TypeError):
            post_freq = None

        levels = data.get("privacyLevels")
        privacy_level = (
            levels[0] if isinstance(levels, list) and levels
            else data.get("privacyLevel")
        )

        return {
            "start": start,
            "end": end,
            "in_progress": in_progress,
            "post_track_point_frequency": post_freq,
            "display_name": data.get("userDisplayName"),
            "session_name": data.get("sessionName"),
            "viewable": data.get("viewable"),
            "privacy_level": privacy_level,
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
        # The CSRF header is only accepted together with the ``livetrack_csrf``
        # cookie set by the same GET that served the token (kept in
        # ``self._scraper``'s cookie jar).  Header alone → 403, so never send it
        # from a different HTTP session or a stateless client.
        return {
            "accept": "application/json",
            "referer": f"{_BASE}/",
            "livetrack-csrf-token": self._csrf or "",
        }
