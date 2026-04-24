"""Sensor platform for Garmin LiveTrack Monitor.

One sensor per tracked person.  State is idle / active / finished.
Attributes contain all session and track-point data.
"""
from __future__ import annotations

import logging

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ACTIVITY_ICON_MAP, DEFAULT_ICON, DOMAIN, STATE_IDLE
from .hub import SIGNAL_SESSION_CHANGE, SIGNAL_UPDATE, LiveTrackHub

_LOGGER = logging.getLogger(__name__)


def _unique_id(person_id: str) -> str:
    return f"{DOMAIN}_{person_id}"


def _expected_entity_id(person_id: str) -> str:
    return f"{SENSOR_DOMAIN}.{DOMAIN}_{person_id}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub: LiveTrackHub = hass.data[DOMAIN][entry.entry_id]

    entities = [LiveTrackSensor(hub, pid) for pid in hub.persons]
    async_add_entities(entities)

    @callback
    def _on_session_change() -> None:
        existing = {e.person_id for e in entities}
        new_entities = [
            LiveTrackSensor(hub, pid)
            for pid in hub.persons
            if pid not in existing
        ]
        if new_entities:
            entities.extend(new_entities)
            async_add_entities(new_entities)

    async_dispatcher_connect(hass, SIGNAL_SESSION_CHANGE, _on_session_change)


class LiveTrackSensor(SensorEntity):
    """Sensor for a tracked person's LiveTrack session."""

    _attr_has_entity_name = False
    _attr_should_poll = False

    def __init__(self, hub: LiveTrackHub, person_id: str) -> None:
        self._hub = hub
        self._person_id = person_id
        cfg = hub.persons[person_id]
        self._attr_unique_id = _unique_id(person_id)
        # Display name — clearly labelled as the "activity" entity so it
        # can be visually distinguished from the sibling device_tracker.
        self._attr_name = f"LiveTrack {cfg.name} Activity"
        # Force the object_id suffix from the configured prefix, not from
        # the slugified display name, when the entity is first registered.
        self._attr_suggested_object_id = f"{DOMAIN}_{person_id}"
        self.entity_id = _expected_entity_id(person_id)
        # Group sensor + tracker for the same person under one HA Device.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, person_id)},
            name=f"LiveTrack {cfg.name}",
            manufacturer="Garmin",
            model="LiveTrack Session",
        )

    @property
    def person_id(self) -> str:
        return self._person_id

    @property
    def native_value(self) -> str:
        state = self._hub.get_state(self._person_id)
        return state.state if state else STATE_IDLE

    @property
    def extra_state_attributes(self) -> dict:
        state = self._hub.get_state(self._person_id)
        return state.attributes if state else {}

    @property
    def icon(self) -> str:
        state = self._hub.get_state(self._person_id)
        if state and state.activity_type:
            return ACTIVITY_ICON_MAP.get(state.activity_type, DEFAULT_ICON)
        return DEFAULT_ICON

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_UPDATE, self._on_update
            )
        )

    @callback
    def _on_update(self, person_id: str) -> None:
        if person_id == self._person_id:
            self.async_write_ha_state()
