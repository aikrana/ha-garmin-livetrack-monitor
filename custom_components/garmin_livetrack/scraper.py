"""LiveTrack scraper that bypasses Cloudflare using cloudscraper.

Garmin periodically changes how their LiveTrack web application fetches
and serves data, making it impractical to rely on standard HTTP clients
or fixed API endpoints.  This module uses cloudscraper to handle
Cloudflare challenges and dynamically discovers the CSRF token needed
to access the data API.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import quote

_LOGGER = logging.getLogger(__name__)

_BASE = "https://livetrack.garmin.com"
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_SESSION_RE = re.compile(
    r'start.{3,5}(20\d{2}[^"\\]+).*?end.{3,5}(20\d{2}[^"\\]+)'
)


class LiveTrackScraper:
    """Stateful scraper for a single LiveTrack session."""

    def __init__(self, session_id: str, token: str) -> None:
        self.session_id = session_id
        self.token = token
        self._scraper = None
        self._csrf: str | None = None

    def fetch(self, begin: str | None = None) -> dict:
        """Fetch session info + latest track-point.

        Args:
            begin: ISO-8601 datetime.  When set, only points *after* this
                   instant are returned (keeps payloads tiny).

        Returns::
            {
                "session": {"start": "…", "end": "…", "in_progress": bool},
                "last_point": { … } | None,
                "points_count": int,
            }
        """
        self._ensure_scraper()

        page_text = self._fetch_page()
        session = self._parse_session(page_text)

        if self._csrf is None:
            self._discover_csrf(page_text)

        last_point = None
        points_count = 0
        if self._csrf:
            pts = self._fetch_track_points(begin)
            if pts is None:
                self._csrf = None
                page_text = self._fetch_page()
                session = self._parse_session(page_text)
                self._discover_csrf(page_text)
                if self._csrf:
                    pts = self._fetch_track_points(begin)

            if pts:
                points_count = len(pts)
                last_point = pts[-1] if pts else None

        return {
            "session": session,
            "last_point": last_point,
            "points_count": points_count,
        }

    def close(self) -> None:
        self._scraper = None
        self._csrf = None

    def _ensure_scraper(self) -> None:
        if self._scraper is None:
            import cloudscraper
            self._scraper = cloudscraper.create_scraper()

    def _fetch_page(self) -> str:
        url = f"{_BASE}/session/{self.session_id}/token/{self.token}"
        r = self._scraper.get(url, stream=True, timeout=30)
        buf = b""
        for chunk in r.iter_content(chunk_size=4096):
            buf += chunk
            if b"trackPoints" in buf:
                break
            if len(buf) > 80_000:
                break
        r.close()
        return buf.decode("utf-8", errors="ignore")

    @staticmethod
    def _parse_session(text: str) -> dict:
        m = _SESSION_RE.search(text)
        if not m:
            return {}
        start_str, end_str = m.group(1), m.group(2)
        in_progress = False
        try:
            s_ts = datetime.fromisoformat(
                start_str.replace("Z", "+00:00")
            ).timestamp()
            e_ts = datetime.fromisoformat(
                end_str.replace("Z", "+00:00")
            ).timestamp()
            in_progress = abs(e_ts - s_ts - 86400) < 2
        except (ValueError, TypeError):
            pass
        return {
            "start": start_str,
            "end": end_str,
            "in_progress": in_progress,
        }

    def _discover_csrf(self, text: str) -> None:
        uuids = set(_UUID_RE.findall(text))
        candidates = [u for u in uuids if u != self.session_id]
        url = self._points_url()
        for candidate in candidates:
            try:
                r = self._scraper.get(
                    url,
                    headers=self._headers(candidate),
                    timeout=15,
                )
                if r.status_code == 200:
                    self._csrf = candidate
                    _LOGGER.debug("Discovered CSRF token")
                    return
            except Exception:  # noqa: BLE001
                continue
        _LOGGER.warning("Could not discover a valid CSRF token")

    def _fetch_track_points(self, begin: str | None) -> list | None:
        url = self._points_url(begin)
        try:
            r = self._scraper.get(
                url, headers=self._headers(self._csrf), timeout=15
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Track-points request error: %s", err)
            return []

        if r.status_code == 200:
            return r.json().get("trackPoints", [])
        if r.status_code == 403:
            _LOGGER.debug("Track-points 403 — CSRF likely expired")
            return None
        _LOGGER.warning("Track-points returned HTTP %s", r.status_code)
        return []

    def _points_url(self, begin: str | None = None) -> str:
        url = (
            f"{_BASE}/api/sessions/{self.session_id}"
            f"/track-points/common?token={self.token}"
        )
        if begin:
            val = begin.replace(".000Z", ".001Z")
            url += f"&begin={quote(val)}"
        return url

    @staticmethod
    def _headers(csrf: str) -> dict:
        return {
            "accept": "application/json",
            "referer": f"{_BASE}/",
            "livetrack-csrf-token": csrf or "",
        }
