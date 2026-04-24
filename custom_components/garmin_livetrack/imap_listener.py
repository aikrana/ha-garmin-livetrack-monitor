"""IMAP listener for incoming Garmin LiveTrack emails.

Provider-agnostic IMAP monitoring.  Design choices:

- **UID-based SEARCH / FETCH** (not sequence numbers).  UIDs are stable
  across sessions and don't shift when messages are expunged or moved;
  sequence numbers do.  This makes watermark tracking safe across
  reconnects and concurrent access from other IMAP clients.
- **Watermark (max UID seen)** instead of an unbounded set of processed
  UIDs.  Lower memory, O(1) checks, naturally monotonic.
- **UIDVALIDITY detection on SELECT**.  If the server rotates it (rare
  but possible on server rebuilds), the watermark is reset.
- **IDLE capability detection**.  Falls back to periodic NOOP polling
  on servers that don't advertise IDLE (some self-hosted stacks, some
  ISP mailboxes).
- **Only IMAP4rev1 primitives**: `FROM`, `SINCE`, `UID X:Y`, `BODY.PEEK[]`,
  `INTERNALDATE`.  No Gmail-specific `X-GM-RAW` or other vendor extensions
  — works on Outlook, Yahoo, Office 365, Dovecot, Courier, Gmail, etc.
- **SSL context pre-created in an executor** so the first connect doesn't
  block the event loop.
"""
from __future__ import annotations

import asyncio
import email
import email.policy
import logging
import re
import ssl
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine

import aioimaplib

from homeassistant.core import HomeAssistant

from .const import LIVETRACK_URL_REGEX

_LOGGER = logging.getLogger(__name__)

_IDLE_TIMEOUT = 25 * 60        # seconds (RFC-recommended max is ~29 min)
_POLL_INTERVAL = 60            # seconds (when the server has no IDLE)
_RECONNECT_DELAY_BASE = 5
_RECONNECT_DELAY_MAX = 300

OnSessionCallback = Callable[[str, str, str, str], Coroutine[Any, Any, None]]


async def _async_create_ssl_context(hass: HomeAssistant) -> ssl.SSLContext:
    """Build a default SSL context off the event loop (blocking I/O)."""
    return await hass.async_add_executor_job(ssl.create_default_context)


def _normalize_for_match(text: str) -> str:
    """Normalize text for robust person-name matching across locales.

    Applies Unicode NFKD decomposition, strips combining marks (accents,
    diacritics), and lowercases.  Non-Latin base characters (CJK, Cyrillic,
    Greek, Arabic, Hebrew, etc.) pass through unchanged since they have no
    combining marks in most orthographies.

    Examples:
        "José María"  -> "jose maria"
        "Müller"      -> "muller"
        "Αθήνα"       -> "αθηνα"
        "北京"        -> "北京"
    """
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return stripped.lower()


class IMAPListener:
    """Watches an IMAP mailbox for Garmin LiveTrack emails."""

    def __init__(
        self,
        hass: HomeAssistant,
        server: str,
        port: int,
        username: str,
        password: str,
        folder: str,
        sender: str,
        person_names: list[str],
        max_age_minutes: int,
        callback: OnSessionCallback,
    ) -> None:
        self._hass = hass
        self._server = server
        self._port = port
        self._username = username
        self._password = password
        self._folder = folder
        self._sender = sender.lower()
        self._person_names_original = list(person_names)
        # Normalized (accent-stripped, lowercased) variants used for matching
        # against the email body.  Keeps non-Latin scripts (CJK, Cyrillic, …)
        # intact while making "José" match "Jose" and "Müller" match "Muller".
        self._person_names_normalized = [
            _normalize_for_match(n) for n in person_names
        ]
        self._max_age = timedelta(minutes=max_age_minutes)
        self._callback = callback
        self._client: aioimaplib.IMAP4_SSL | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._ssl_context: ssl.SSLContext | None = None
        # UID tracking
        self._max_uid: int = 0
        self._uidvalidity: int | None = None
        self._supports_idle: bool = True  # assumed unless CAPABILITY says otherwise

    # ── Public lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._ssl_context = await _async_create_ssl_context(self._hass)
        self._task = asyncio.create_task(self._run_loop())
        _LOGGER.info(
            "IMAP listener starting for %s@%s:%s/%s (sender=%s, names=%s)",
            self._username, self._server, self._port, self._folder,
            self._sender, self._person_names_original,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._disconnect()

    def update_person_names(self, names: list[str]) -> None:
        self._person_names_original = list(names)
        self._person_names_normalized = [
            _normalize_for_match(n) for n in names
        ]
        _LOGGER.debug("Updated person names: %s", self._person_names_original)

    def diagnostics(self) -> dict[str, Any]:
        """Return a sanitized snapshot for the diagnostics platform.
        Credentials are NOT included; email and server are kept in the
        clear because they're useful for debugging connectivity issues
        and HA's diagnostics helper redacts them at the caller's option.
        """
        return {
            "server": self._server,
            "port": self._port,
            "folder": self._folder,
            "sender": self._sender,
            "username": self._username,
            "configured_person_count": len(self._person_names_original),
            "max_age_minutes": self._max_age.total_seconds() / 60,
            "running": self._running,
            "supports_idle": self._supports_idle,
            "uidvalidity": self._uidvalidity,
            "watermark_max_uid": self._max_uid,
            "connected": self._client is not None,
        }

    # ── Connection ──────────────────────────────────────────────────────

    async def _connect(self) -> bool:
        try:
            self._client = aioimaplib.IMAP4_SSL(
                host=self._server,
                port=self._port,
                timeout=30,
                ssl_context=self._ssl_context,
            )
            await self._client.wait_hello_from_server()

            resp = await self._client.login(self._username, self._password)
            if resp.result != "OK":
                _LOGGER.error("IMAP login failed: %s", resp.lines)
                return False

            # Check IDLE capability.  aioimaplib's idle implementation will
            # happily attempt IDLE even if unsupported, so this guard saves
            # us from a disconnect storm on non-IDLE servers.
            try:
                cap_resp = await self._client.capability()
                caps = self._flatten_lines(cap_resp.lines).upper()
                self._supports_idle = " IDLE " in f" {caps} "
                _LOGGER.debug(
                    "Capabilities: IDLE=%s, advertised=%s",
                    self._supports_idle, caps[:200],
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("CAPABILITY failed (%s) — assuming IDLE", err)
                self._supports_idle = True

            resp = await self._client.select(self._folder)
            if resp.result != "OK":
                _LOGGER.error("IMAP select failed: %s", resp.lines)
                return False

            # Track UIDVALIDITY.  If it changes between sessions, all
            # previously-seen UIDs are meaningless and we must rebaseline.
            new_uidvalidity = self._parse_uidvalidity(resp.lines)
            if new_uidvalidity is not None:
                if self._uidvalidity is None:
                    self._uidvalidity = new_uidvalidity
                    _LOGGER.debug("UIDVALIDITY=%d", new_uidvalidity)
                elif new_uidvalidity != self._uidvalidity:
                    _LOGGER.warning(
                        "UIDVALIDITY changed (%d → %d); resetting watermark",
                        self._uidvalidity, new_uidvalidity,
                    )
                    self._uidvalidity = new_uidvalidity
                    self._max_uid = 0

            _LOGGER.debug(
                "IMAP connected & selected %s (idle=%s, uidvalidity=%s, watermark=%d)",
                self._folder, self._supports_idle, self._uidvalidity, self._max_uid,
            )
            return True
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("IMAP connection error: %s", err)
            return False

    async def _disconnect(self) -> None:
        if self._client:
            try:
                await self._client.logout()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    # ── Main loop ───────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        reconnect_delay = _RECONNECT_DELAY_BASE
        while self._running:
            if not await self._connect():
                _LOGGER.warning("IMAP reconnecting in %ds", reconnect_delay)
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, _RECONNECT_DELAY_MAX)
                continue
            reconnect_delay = _RECONNECT_DELAY_BASE

            # First-ever connect for this session object: set watermark to
            # current max UID without processing pre-existing messages.
            # On reconnect: process anything newer than the stored watermark.
            if self._max_uid == 0:
                await self._establish_watermark()
            else:
                await self._check_new()

            try:
                if self._supports_idle:
                    await self._idle_loop()
                else:
                    await self._poll_loop()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("IMAP loop error: %s — reconnecting", err)

            await self._disconnect()

    async def _idle_loop(self) -> None:
        while self._running and self._client:
            try:
                idle_task = await self._client.idle_start(timeout=_IDLE_TIMEOUT)
                push = await self._client.wait_server_push()
                self._client.idle_done()
                await asyncio.wait_for(idle_task, timeout=10)
            except asyncio.TimeoutError:
                continue
            except (aioimaplib.Abort, ConnectionError, OSError):
                return

            _LOGGER.debug("IMAP IDLE push: %r", push)
            # Any mailbox change is a trigger to re-query — EXISTS, EXPUNGE,
            # FLAGS, etc.  The SEARCH criteria + watermark filters down to
            # actually-new mail so false wakes are cheap.
            await self._check_new()

    async def _poll_loop(self) -> None:
        while self._running and self._client:
            try:
                await asyncio.sleep(_POLL_INTERVAL)
                await self._client.noop()
            except (aioimaplib.Abort, ConnectionError, OSError):
                return
            await self._check_new()

    # ── Watermark + new-mail processing ─────────────────────────────────

    async def _establish_watermark(self) -> None:
        """On first connect, set watermark = current max UID matching sender.

        Pre-existing emails are intentionally NOT processed: the INTERNALDATE
        age filter would catch them anyway, but this is cheaper and
        definitive.  Only messages that arrive AFTER the listener is up
        will fire sessions.
        """
        try:
            resp = await self._client.uid_search(f'FROM "{self._sender}"')
            if resp.result != "OK":
                _LOGGER.warning(
                    "Watermark init: SEARCH non-OK (%s). Starting from 0.",
                    resp.result,
                )
                return
            uids = self._parse_uids(resp.lines)
            if uids:
                self._max_uid = max(uids)
                _LOGGER.info(
                    "Watermark set at UID=%d (%d pre-existing emails from %s skipped)",
                    self._max_uid, len(uids), self._sender,
                )
            else:
                _LOGGER.info(
                    "No pre-existing emails from %s; watermark=0", self._sender
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Watermark init error: %s", err)

    async def _check_new(self) -> None:
        if not self._client:
            return
        try:
            low = self._max_uid + 1
            criteria = f'UID {low}:* FROM "{self._sender}"'
            _LOGGER.debug("UID SEARCH: %s", criteria)
            resp = await self._client.uid_search(criteria)
            if resp.result != "OK":
                _LOGGER.debug("UID SEARCH non-OK: %s", resp.result)
                return
            uids = self._parse_uids(resp.lines)
            # Defensive: `UID N:*` is inclusive-of-N but "*" means "current
            # max".  If the current max is < N, servers typically return the
            # single largest UID or nothing — either way, filter strictly.
            new_uids = sorted(u for u in uids if u > self._max_uid)
            _LOGGER.debug(
                "UID SEARCH result: %d match(es), %d new after watermark=%d",
                len(uids), len(new_uids), self._max_uid,
            )
            for uid in new_uids:
                await self._process_message(uid)
                # Advance watermark even on processing failure so we don't
                # retry forever.  The body-parsing branch already logs the
                # specific failure.
                self._max_uid = max(self._max_uid, uid)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Error checking inbox: %s", err)

    # ── Per-message processing ──────────────────────────────────────────

    async def _process_message(self, uid: int) -> None:
        uid_s = str(uid)
        try:
            resp = await self._client.uid(
                "fetch", uid_s, "(BODY.PEEK[] INTERNALDATE)"
            )
            _LOGGER.debug(
                "UID FETCH %s: result=%s lines=%d sizes=%s",
                uid_s, resp.result, len(resp.lines),
                [len(l) if isinstance(l, (bytes, bytearray)) else "n/a"
                 for l in resp.lines],
            )
            if resp.result != "OK":
                return

            raw_email, internal_date = self._extract_fetch_payload(resp.lines)
            if raw_email is None or len(raw_email) < 200:
                _LOGGER.warning(
                    "UID FETCH %s: no usable body (biggest=%d)",
                    uid_s, len(raw_email) if raw_email else 0,
                )
                return

            if not self._is_recent(internal_date):
                _LOGGER.debug(
                    "Skipping uid=%s: older than %s (internal_date=%s)",
                    uid_s, self._max_age, internal_date,
                )
                return

            msg = email.message_from_bytes(raw_email, policy=email.policy.default)
            subject = msg.get("Subject", "")
            body = self._extract_body(msg)
            _LOGGER.debug(
                "uid=%s subject=%r body_len=%d", uid_s, subject, len(body)
            )
            if not body:
                return

            session_id, token = self._extract_livetrack_link(body)
            if not session_id:
                _LOGGER.debug(
                    "uid=%s: no LiveTrack URL in body (first 200 chars: %r)",
                    uid_s, body[:200],
                )
                return

            person_name = self._match_person(body)
            if not person_name:
                _LOGGER.info(
                    "LiveTrack link found but no configured person matched "
                    "(uid=%s, configured=%s)",
                    uid_s, self._person_names_original,
                )
                return

            livetrack_url = (
                f"https://livetrack.garmin.com/session/{session_id}/token/{token}"
            )
            _LOGGER.info(
                "LiveTrack session detected: person=%s session=%s (uid=%s)",
                person_name, session_id, uid_s,
            )
            await self._callback(session_id, token, person_name, livetrack_url)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Error processing uid=%s: %s", uid_s, err)

    # ── Parsers / helpers ───────────────────────────────────────────────

    @staticmethod
    def _flatten_lines(lines) -> str:
        out: list[str] = []
        for l in lines:
            if isinstance(l, (bytes, bytearray)):
                out.append(bytes(l).decode("utf-8", errors="ignore"))
            elif l is not None:
                out.append(str(l))
        return " ".join(out)

    @staticmethod
    def _parse_uids(lines) -> list[int]:
        """Extract integer UIDs from a SEARCH response.

        Tolerant of different formats: raw space-separated, with/without
        leading 'SEARCH' keyword, split across multiple lines, etc.
        """
        out: list[int] = []
        for l in lines:
            if isinstance(l, (bytes, bytearray)):
                text = bytes(l).decode("utf-8", errors="ignore").strip()
            elif isinstance(l, str):
                text = l.strip()
            else:
                continue
            for tok in text.split():
                if tok.isdigit():
                    out.append(int(tok))
        return out

    @staticmethod
    def _parse_uidvalidity(lines) -> int | None:
        for l in lines:
            if isinstance(l, (bytes, bytearray)):
                text = bytes(l).decode("utf-8", errors="ignore")
            elif isinstance(l, str):
                text = l
            else:
                continue
            m = re.search(r"UIDVALIDITY (\d+)", text)
            if m:
                return int(m.group(1))
        return None

    @staticmethod
    def _extract_fetch_payload(lines) -> tuple[bytes | None, str | None]:
        """Pick the largest bytes chunk as body; scan all chunks for INTERNALDATE."""
        raw_email: bytes | None = None
        internal_date: str | None = None
        for line in lines:
            if isinstance(line, (bytes, bytearray)):
                b = bytes(line)
                if raw_email is None or len(b) > len(raw_email):
                    raw_email = b
                try:
                    decoded = b.decode("utf-8", errors="ignore")
                except Exception:  # noqa: BLE001
                    decoded = ""
                if internal_date is None and "INTERNALDATE" in decoded:
                    m = re.search(r'INTERNALDATE "([^"]+)"', decoded)
                    if m:
                        internal_date = m.group(1)
            elif isinstance(line, str):
                if internal_date is None and "INTERNALDATE" in line:
                    m = re.search(r'INTERNALDATE "([^"]+)"', line)
                    if m:
                        internal_date = m.group(1)
        return raw_email, internal_date

    def _is_recent(self, internal_date: str | None) -> bool:
        if not internal_date:
            return True
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(internal_date)
            age = datetime.now(timezone.utc) - dt
            return age < self._max_age
        except (ValueError, TypeError):
            return True

    @staticmethod
    def _extract_body(msg: email.message.Message) -> str:
        body_parts: list[str] = []
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct in ("text/plain", "text/html"):
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body_parts.append(payload.decode(charset, errors="ignore"))
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                body_parts.append(payload.decode(charset, errors="ignore"))
        return "\n".join(body_parts)

    @staticmethod
    def _extract_livetrack_link(body: str) -> tuple[str | None, str | None]:
        cleaned = body.replace("=\n", "").replace("=\r\n", "").replace("=\r", "")
        m = re.search(LIVETRACK_URL_REGEX, cleaned, re.IGNORECASE)
        if not m:
            return None, None
        session_id = (
            m.group(1).replace("\n", "").replace("\r", "").replace("=", "")
        )
        token = (
            m.group(2).replace("\n", "").replace("\r", "").replace("=", "")
        )
        return session_id, token

    def _match_person(self, body: str) -> str | None:
        body_normalized = _normalize_for_match(body)
        for i, name_normalized in enumerate(self._person_names_normalized):
            if name_normalized and name_normalized in body_normalized:
                return self._person_names_original[i]
        return None


async def test_imap_connection(
    hass: HomeAssistant,
    server: str,
    port: int,
    username: str,
    password: str,
    folder: str,
) -> str | None:
    """Test IMAP connectivity for the config flow.

    Returns None on success, or a classified error key on failure.  Keys
    match entries under ``config.error`` in translations/*.json, so the
    user sees a meaningful message for each failure mode instead of a
    generic "couldn't connect".

    Error key catalogue:

    - ``imap_dns_error``       — host doesn't resolve (typo in server)
    - ``imap_connection_error``— resolves but connect fails (timeout, refused)
    - ``imap_tls_error``       — SSL/TLS handshake fails (wrong port, self-signed)
    - ``imap_auth_error``      — login rejected (wrong user/pass, needs app-pass)
    - ``imap_folder_error``    — folder doesn't exist / select fails
    - ``imap_unknown_error``   — catch-all for anything unclassified
    """
    import socket

    ssl_context = await _async_create_ssl_context(hass)

    # Phase 1: connect + greet.  Failures here are network/TLS.
    try:
        client = aioimaplib.IMAP4_SSL(
            host=server, port=port, timeout=15, ssl_context=ssl_context
        )
        await client.wait_hello_from_server()
    except socket.gaierror as err:
        _LOGGER.info("IMAP DNS error for %s:%s: %s", server, port, err)
        return "imap_dns_error"
    except ssl.SSLError as err:
        _LOGGER.info("IMAP TLS error for %s:%s: %s", server, port, err)
        return "imap_tls_error"
    except (ConnectionRefusedError, asyncio.TimeoutError, TimeoutError) as err:
        _LOGGER.info("IMAP connection error for %s:%s: %s", server, port, err)
        return "imap_connection_error"
    except (OSError, aioimaplib.Abort) as err:
        # Disambiguate from the error message — some of these wrap lower-level
        # socket / SSL problems without preserving the original exception type.
        msg = str(err).lower()
        if "ssl" in msg or "certificate" in msg or "handshake" in msg:
            _LOGGER.info("IMAP TLS error (%s): %s", type(err).__name__, err)
            return "imap_tls_error"
        if (
            "name or service not known" in msg
            or "no address associated" in msg
            or "temporary failure in name resolution" in msg
            or "nodename nor servname" in msg
        ):
            _LOGGER.info("IMAP DNS error (%s): %s", type(err).__name__, err)
            return "imap_dns_error"
        _LOGGER.info("IMAP connection error (%s): %s", type(err).__name__, err)
        return "imap_connection_error"
    except Exception as err:  # noqa: BLE001
        _LOGGER.info("IMAP unknown error: %s", err)
        return "imap_unknown_error"

    # Phase 2: login.
    try:
        resp = await client.login(username, password)
    except Exception as err:  # noqa: BLE001
        _LOGGER.info("IMAP login exception: %s", err)
        await _safe_logout(client)
        return "imap_auth_error"
    if resp.result != "OK":
        _LOGGER.info("IMAP login rejected: %s", resp.lines)
        await _safe_logout(client)
        return "imap_auth_error"

    # Phase 3: select folder.
    try:
        resp = await client.select(folder)
    except Exception as err:  # noqa: BLE001
        _LOGGER.info("IMAP folder select exception: %s", err)
        await _safe_logout(client)
        return "imap_folder_error"
    if resp.result != "OK":
        _LOGGER.info(
            "IMAP folder '%s' not selectable: %s", folder, resp.lines
        )
        await _safe_logout(client)
        return "imap_folder_error"

    _safe_logout(client)
    return None


async def _safe_logout(client: aioimaplib.IMAP4_SSL) -> None:
    """Logout swallowing any exception — used in test cleanup only."""
    try:
        await client.logout()
    except Exception:  # noqa: BLE001
        pass
