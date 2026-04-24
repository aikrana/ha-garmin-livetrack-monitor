"""Device tracker platform for Garmin LiveTrack Monitor.

One GPS device_tracker per tracked person (when enabled globally).
"""
from __future__ import annotations

import logging

from homeassistant.components.device_tracker import (
    DOMAIN as DEVICE_TRACKER_DOMAIN,
    SourceType,
)
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ACTIVITY_ICON_MAP,
    ATTR_ACTIVITY_TYPE,
    ATTR_ALTITUDE,
    ATTR_DISTANCE_KM,
    ATTR_DURATION,
    ATTR_HEARTRATE,
    ATTR_SPEED_KMH,
    DEFAULT_ICON,
    DOMAIN,
    STATE_ACTIVE,
)
from .hub import SIGNAL_SESSION_CHANGE, SIGNAL_UPDATE, LiveTrackHub

_LOGGER = logging.getLogger(__name__)


def _unique_id(person_id: str) -> str:
    return f"{DOMAIN}_tracker_{person_id}"


def _expected_entity_id(person_id: str) -> str:
    return f"{DEVICE_TRACKER_DOMAIN}.{DOMAIN}_tracker_{person_id}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub: LiveTrackHub = hass.data[DOMAIN][entry.entry_id]

    if not hub.enable_device_tracker:
        return

    entities = [LiveTrackDeviceTracker(hub, pid) for pid in hub.persons]
    async_add_entities(entities)

    @callback
    def _on_session_change() -> None:
        if not hub.enable_device_tracker:
            return
        existing = {e.person_id for e in entities}
        new_entities = [
            LiveTrackDeviceTracker(hub, pid)
            for pid in hub.persons
            if pid not in existing
        ]
        if new_entities:
            entities.extend(new_entities)
            async_add_entities(new_entities)

    async_dispatcher_connect(hass, SIGNAL_SESSION_CHANGE, _on_session_change)


class LiveTrackDeviceTracker(TrackerEntity):
    """GPS device tracker for a person's LiveTrack session."""

    _attr_has_entity_name = False
    _attr_should_poll = False

    def __init__(self, hub: LiveTrackHub, person_id: str) -> None:
        self._hub = hub
        self._person_id = person_id
        cfg = hub.persons[person_id]
        self._attr_unique_id = _unique_id(person_id)
        # Display name — clearly labelled as the "location" entity so it
        # can be visually distinguished from the sibling sensor.
        self._attr_name = f"LiveTrack {cfg.name} Location"
        self._attr_suggested_object_id = f"{DOMAIN}_tracker_{person_id}"
        self.entity_id = _expected_entity_id(person_id)
        # Share the Device with the sensor so they group together in UI.
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
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        state = self._hub.get_state(self._person_id)
        if state and state.state == STATE_ACTIVE and state.has_location:
            return state.latitude
        return None

    @property
    def longitude(self) -> float | None:
        state = self._hub.get_state(self._person_id)
        if state and state.state == STATE_ACTIVE and state.has_location:
            return state.longitude
        return None

    @property
    def location_name(self) -> str | None:
        state = self._hub.get_state(self._person_id)
        if not state or state.state != STATE_ACTIVE:
            return None
        activity_names = {
            "hiking": "Hiking",
            "walking": "Walking",
            "cycling": "Cycling",
            "running": "Running",
            "kayak": "Kayaking",
        }
        return activity_names.get(state.activity_type, "Activity")

    @property
    def extra_state_attributes(self) -> dict:
        state = self._hub.get_state(self._person_id)
        if not state or state.state != STATE_ACTIVE:
            return {}
        return {
            ATTR_SPEED_KMH: round(state.speed * 3.6, 2) if state.speed else None,
            ATTR_ALTITUDE: state.altitude,
            ATTR_HEARTRATE: state.heartrate,
            ATTR_DISTANCE_KM: state.distance_km,
            ATTR_DURATION: state.duration_str,
            ATTR_ACTIVITY_TYPE: state.activity_type,
        }

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
