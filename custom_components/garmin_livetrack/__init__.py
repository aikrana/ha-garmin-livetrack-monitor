"""Garmin LiveTrack Monitor for Home Assistant.

Monitors IMAP for Garmin LiveTrack emails, scrapes live activity data,
and exposes sensors, device trackers and events for each tracked person.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .hub import LiveTrackHub

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "device_tracker"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Garmin LiveTrack Monitor from a config entry."""
    hub = LiveTrackHub(hass, entry)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = hub

    await hub.async_setup()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.info("Garmin LiveTrack Monitor set up (entry=%s)", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hub: LiveTrackHub = hass.data[DOMAIN].pop(entry.entry_id)
        await hub.async_shutdown()
    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    hub: LiveTrackHub = hass.data[DOMAIN][entry.entry_id]
    await hub.async_update_options()
