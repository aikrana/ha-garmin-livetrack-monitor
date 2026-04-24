"""Diagnostics platform for Garmin LiveTrack Monitor.

Home Assistant auto-discovers this module and exposes a "Download Diagnostics"
button on the integration card (Settings → Devices & Services → ⋮).  The
resulting JSON is what users typically attach to GitHub issues.

Design:
- Credentials (IMAP password) are fully redacted.
- Username/email is kept: it's useful to diagnose login errors and can be
  manually removed by the reporter if they want.
- Session-level secrets (token, full livetrack_url, live coordinates) are
  redacted — these grant access to the live activity during its window.
- Person names are replaced with a length indicator (`name_length`) — the
  real name lives in the email body and PII should not leak to a public
  issue tracker.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    ATTR_LATITUDE,
    ATTR_LIVETRACK_URL,
    ATTR_LONGITUDE,
    ATTR_SESSION_ID,
    ATTR_TOKEN,
    CONF_IMAP_PASSWORD,
    DOMAIN,
)
from .hub import LiveTrackHub

# Keys removed from any nested dict found in the diagnostics payload.
# `async_redact_data` walks the structure and replaces each value with
# "**REDACTED**" regardless of depth.
TO_REDACT = {
    CONF_IMAP_PASSWORD,
    "password",
    ATTR_TOKEN,
    ATTR_LIVETRACK_URL,
    ATTR_SESSION_ID,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    # Common aliases that might appear in the raw config/options dicts:
    "latitude",
    "longitude",
    "lat",
    "lon",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Called by HA when the user clicks "Download Diagnostics".  The result
    is serialized to JSON and offered as a download.
    """
    hub: LiveTrackHub = hass.data[DOMAIN][entry.entry_id]

    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "hub": async_redact_data(hub.diagnostics(), TO_REDACT),
    }
