"""Garmin LiveTrack Monitor for Home Assistant.

Follows Garmin LiveTrack sessions — found in email over IMAP, or started with
the ``start_tracking`` service — scrapes live activity data, and exposes
sensors, device trackers and events for each tracked person.
"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_PERSON,
    ATTR_URL,
    DOMAIN,
    LIVETRACK_BASE_URL,
    SERVICE_START_TRACKING,
    SERVICE_STOP_TRACKING,
)
from .hub import LiveTrackHub
from .imap_listener import parse_livetrack_link

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "device_tracker"]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

START_TRACKING_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_PERSON): cv.string,
        vol.Required(ATTR_URL): cv.string,
    }
)
STOP_TRACKING_SCHEMA = vol.Schema({vol.Required(ATTR_PERSON): cv.string})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services; they act on whichever entry has the person."""

    def _resolve(person: str) -> tuple[LiveTrackHub, str]:
        for hub in hass.data.get(DOMAIN, {}).values():
            if (person_id := hub.find_person(person)) is not None:
                return hub, person_id
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_person",
            translation_placeholders={"person": person},
        )

    async def _start_tracking(call: ServiceCall) -> None:
        hub, person_id = _resolve(call.data[ATTR_PERSON])
        session_id, token = parse_livetrack_link(call.data[ATTR_URL])
        if not session_id or not token:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_url"
            )
        url = f"{LIVETRACK_BASE_URL}/session/{session_id}/token/{token}"
        await hub.async_start_tracking(person_id, session_id, token, url)

    async def _stop_tracking(call: ServiceCall) -> None:
        hub, person_id = _resolve(call.data[ATTR_PERSON])
        await hub.async_stop_tracking(person_id)

    hass.services.async_register(
        DOMAIN, SERVICE_START_TRACKING, _start_tracking,
        schema=START_TRACKING_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_STOP_TRACKING, _stop_tracking,
        schema=STOP_TRACKING_SCHEMA,
    )
    return True


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
