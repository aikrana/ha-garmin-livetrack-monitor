"""Config flow for Garmin LiveTrack Monitor."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
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
    DEFAULT_IMAP_FOLDER,
    DEFAULT_IMAP_PORT,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_SENDER,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class GarminLiveTrackConfigFlow(
    config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle a config flow for Garmin LiveTrack Monitor."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: find sessions in email (IMAP) or start them manually."""
        return self.async_show_menu(
            step_id="user", menu_options=["imap", "manual"]
        )

    async def async_step_imap(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2a: IMAP connection settings."""
        errors = {}

        if user_input is not None:
            from .imap_listener import test_imap_connection

            folder = (
                user_input.get(CONF_IMAP_FOLDER, "").strip() or DEFAULT_IMAP_FOLDER
            )
            error_key = await test_imap_connection(
                hass=self.hass,
                server=user_input[CONF_IMAP_SERVER],
                port=user_input[CONF_IMAP_PORT],
                username=user_input[CONF_IMAP_USERNAME],
                password=user_input[CONF_IMAP_PASSWORD],
                folder=folder,
            )
            if error_key:
                # `error_key` is already a translation key (imap_dns_error,
                # imap_auth_error, …) from test_imap_connection.  It maps to
                # a human-readable message in translations/*.json.
                _LOGGER.warning(
                    "IMAP connection test failed: %s (server=%s:%s, user=%s, folder=%s)",
                    error_key,
                    user_input[CONF_IMAP_SERVER],
                    user_input[CONF_IMAP_PORT],
                    user_input[CONF_IMAP_USERNAME],
                    folder,
                )
                errors["base"] = error_key
            else:
                self._data.update(user_input)
                self._data[CONF_IMAP_FOLDER] = folder
                return await self.async_step_person()

        return self.async_show_form(
            step_id="imap",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_IMAP_SERVER): str,
                    vol.Required(
                        CONF_IMAP_PORT, default=DEFAULT_IMAP_PORT
                    ): int,
                    vol.Required(CONF_IMAP_USERNAME): str,
                    vol.Required(CONF_IMAP_PASSWORD): str,
                    vol.Optional(
                        CONF_IMAP_FOLDER, default=DEFAULT_IMAP_FOLDER
                    ): str,
                    vol.Optional(
                        CONF_EMAIL_MAX_AGE, default=DEFAULT_EMAIL_MAX_AGE
                    ): int,
                }
            ),
            errors=errors,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2b: no email account; sessions start with the
        ``start_tracking`` service."""
        return await self.async_step_person()

    async def async_step_person(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: Add the first tracked person."""
        errors = {}

        if user_input is not None:
            entity_prefix = user_input[CONF_PERSON_ID].strip().lower()
            if not entity_prefix.replace("_", "").isalnum():
                errors[CONF_PERSON_ID] = "invalid_prefix"
            else:
                self._data[CONF_PERSONS] = [
                    {
                        CONF_PERSON_NAME: user_input[CONF_PERSON_NAME].strip(),
                        CONF_PERSON_ID: entity_prefix,
                    }
                ]
                self._data[CONF_ENABLE_DEVICE_TRACKER] = user_input.get(
                    CONF_ENABLE_DEVICE_TRACKER, False
                )
                self._data[CONF_POLL_INTERVAL] = user_input.get(
                    CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                )

                if CONF_IMAP_SERVER in self._data:
                    title = f"LiveTrack Monitor ({self._data[CONF_IMAP_USERNAME]})"
                    data = {
                        CONF_IMAP_SERVER: self._data[CONF_IMAP_SERVER],
                        CONF_IMAP_PORT: self._data[CONF_IMAP_PORT],
                        CONF_IMAP_USERNAME: self._data[CONF_IMAP_USERNAME],
                        CONF_IMAP_PASSWORD: self._data[CONF_IMAP_PASSWORD],
                        CONF_IMAP_FOLDER: self._data[CONF_IMAP_FOLDER],
                        CONF_SENDER: DEFAULT_SENDER,
                        CONF_EMAIL_MAX_AGE: self._data.get(
                            CONF_EMAIL_MAX_AGE, DEFAULT_EMAIL_MAX_AGE
                        ),
                    }
                else:
                    title = "LiveTrack Monitor (manual)"
                    data = {}

                return self.async_create_entry(
                    title=title,
                    data=data,
                    options={
                        CONF_PERSONS: self._data[CONF_PERSONS],
                        CONF_ENABLE_DEVICE_TRACKER: self._data[
                            CONF_ENABLE_DEVICE_TRACKER
                        ],
                        CONF_POLL_INTERVAL: self._data[CONF_POLL_INTERVAL],
                    },
                )

        return self.async_show_form(
            step_id="person",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PERSON_NAME): str,
                    vol.Required(CONF_PERSON_ID): str,
                    vol.Optional(
                        CONF_ENABLE_DEVICE_TRACKER, default=False
                    ): bool,
                    vol.Optional(
                        CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL
                    ): int,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> GarminLiveTrackOptionsFlow:
        return GarminLiveTrackOptionsFlow(config_entry)


class GarminLiveTrackOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Garmin LiveTrack Monitor."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        menu_options = ["add_person", "remove_person", "settings"]
        if CONF_IMAP_SERVER in self._config_entry.data:
            menu_options.append("email")
        return self.async_show_menu(step_id="init", menu_options=menu_options)

    async def async_step_add_person(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors = {}

        if user_input is not None:
            entity_prefix = user_input[CONF_PERSON_ID].strip().lower()
            if not entity_prefix.replace("_", "").isalnum():
                errors[CONF_PERSON_ID] = "invalid_prefix"
            else:
                persons = list(
                    self._config_entry.options.get(CONF_PERSONS, [])
                )
                if any(p[CONF_PERSON_ID] == entity_prefix for p in persons):
                    errors[CONF_PERSON_ID] = "duplicate_prefix"
                else:
                    persons.append(
                        {
                            CONF_PERSON_NAME: user_input[
                                CONF_PERSON_NAME
                            ].strip(),
                            CONF_PERSON_ID: entity_prefix,
                        }
                    )
                    return self.async_create_entry(
                        title="",
                        data={
                            **self._config_entry.options,
                            CONF_PERSONS: persons,
                        },
                    )

        return self.async_show_form(
            step_id="add_person",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PERSON_NAME): str,
                    vol.Required(CONF_PERSON_ID): str,
                }
            ),
            errors=errors,
        )

    async def async_step_remove_person(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        persons = list(self._config_entry.options.get(CONF_PERSONS, []))

        if not persons:
            return self.async_abort(reason="no_persons")

        if user_input is not None:
            remove_id = user_input["person_to_remove"]
            persons = [p for p in persons if p[CONF_PERSON_ID] != remove_id]
            return self.async_create_entry(
                title="",
                data={**self._config_entry.options, CONF_PERSONS: persons},
            )

        person_options = {
            p[CONF_PERSON_ID]: f"{p[CONF_PERSON_NAME]} ({p[CONF_PERSON_ID]})"
            for p in persons
        }
        return self.async_show_form(
            step_id="remove_person",
            data_schema=vol.Schema(
                {
                    vol.Required("person_to_remove"): vol.In(person_options),
                }
            ),
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    **self._config_entry.options,
                    CONF_ENABLE_DEVICE_TRACKER: user_input[
                        CONF_ENABLE_DEVICE_TRACKER
                    ],
                    CONF_POLL_INTERVAL: user_input[CONF_POLL_INTERVAL],
                },
            )

        current = self._config_entry.options
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ENABLE_DEVICE_TRACKER,
                        default=current.get(CONF_ENABLE_DEVICE_TRACKER, False),
                    ): bool,
                    vol.Optional(
                        CONF_POLL_INTERVAL,
                        default=current.get(
                            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                        ),
                    ): int,
                }
            ),
        )

    async def async_step_email(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Email settings: the IMAP folder to watch and the max email age.

        A new folder is checked against the server before it is saved.
        """
        errors = {}
        data = self._config_entry.data
        current = self._config_entry.options

        if user_input is not None:
            from .imap_listener import test_imap_connection

            folder = user_input[CONF_IMAP_FOLDER].strip() or DEFAULT_IMAP_FOLDER
            error_key = await test_imap_connection(
                hass=self.hass,
                server=data[CONF_IMAP_SERVER],
                port=data[CONF_IMAP_PORT],
                username=data[CONF_IMAP_USERNAME],
                password=data[CONF_IMAP_PASSWORD],
                folder=folder,
            )
            if error_key:
                errors["base"] = error_key
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        **current,
                        CONF_IMAP_FOLDER: folder,
                        CONF_EMAIL_MAX_AGE: user_input[CONF_EMAIL_MAX_AGE],
                    },
                )

        return self.async_show_form(
            step_id="email",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_IMAP_FOLDER,
                        default=current.get(
                            CONF_IMAP_FOLDER,
                            data.get(CONF_IMAP_FOLDER, DEFAULT_IMAP_FOLDER),
                        ),
                    ): str,
                    vol.Optional(
                        CONF_EMAIL_MAX_AGE,
                        default=current.get(
                            CONF_EMAIL_MAX_AGE,
                            data.get(CONF_EMAIL_MAX_AGE, DEFAULT_EMAIL_MAX_AGE),
                        ),
                    ): int,
                }
            ),
            errors=errors,
        )
