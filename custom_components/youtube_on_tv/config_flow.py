"""Config flow for YouTube on TV."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
from http import HTTPStatus
from typing import Any
from urllib.parse import urlparse

import aiohttp
from pyytlounge import YtLoungeApi
import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import ActionSelector
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo

from .const import (
    CONF_APP_URL,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_OPEN_ACTIONS,
    CONF_PAIRING_CODE,
    CONF_SCREEN_ID,
    DOMAIN,
    LOGGER,
    LOUNGE_DEVICE_NAME,
)
from .dial import (
    DialConnectionError,
    DialNoScreenError,
    DialScreen,
    async_get_screen_from_host,
    async_get_screen_from_location,
)

DEFAULT_TITLE = "YouTube on TV"

HOST_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})
PAIRING_CODE_SCHEMA = vol.Schema({vol.Required(CONF_PAIRING_CODE): str})


class InvalidPairingCode(Exception):
    """The TV code was not accepted."""


def screen_unique_id(screen_id: str) -> str:
    """Return a unique id derived from, but not revealing, a screen id."""
    return hashlib.sha256(screen_id.encode()).hexdigest()[:16]


async def _async_pair_with_code(code: str) -> tuple[str, str | None]:
    """Exchange a TV code for a screen id and screen name."""
    code = "".join(ch for ch in code if ch.isdigit())
    if not code:
        raise InvalidPairingCode
    async with YtLoungeApi(LOUNGE_DEVICE_NAME) as api:
        try:
            await api.pair(code)
        except aiohttp.ClientResponseError as err:
            # YouTube answers a wrong or expired code with 400 or 404, which
            # the library surfaces while decoding the reply.
            if err.status not in (HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND):
                raise
            raise InvalidPairingCode from err
        except (KeyError, TypeError, ValueError) as err:
            raise InvalidPairingCode from err
        return api.auth.screen_id, api.screen_name


class YouTubeOnTvConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for YouTube on TV."""

    VERSION = 1
    MINOR_VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TvOptionsFlow:
        """Return the options flow of a TV added with a code."""
        return TvOptionsFlow()

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry: ConfigEntry) -> bool:
        """Offer options only for TVs added with a code: they have no DIAL."""
        return CONF_APP_URL not in config_entry.data

    def __init__(self) -> None:
        """Initialize the flow."""
        self._host: str | None = None
        self._screen: DialScreen | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose how to add the TV."""
        return self.async_show_menu(
            step_id="user", menu_options=["host", "pairing_code"]
        )

    async def async_step_host(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a TV by host, reading its screen id through DIAL."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                screen = await async_get_screen_from_host(self.hass, host)
            except DialConnectionError:
                errors["base"] = "cannot_connect"
            except DialNoScreenError:
                errors["base"] = "no_screen_id"
            except Exception:
                LOGGER.exception("Unexpected error reading DIAL from %s", host)
                errors["base"] = "unknown"
            else:
                self._host = host
                self._screen = screen
                await self._async_set_unique_id_from_screen(screen)
                self._abort_if_unique_id_configured(
                    updates=self._host_data(host, screen)
                )
                self._async_abort_entries_match({CONF_SCREEN_ID: screen.screen_id})
                return self._async_create_from_screen()
        return self.async_show_form(
            step_id="host",
            data_schema=self.add_suggested_values_to_schema(HOST_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_pairing_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a TV with the code from the YouTube app's "Link with TV code"."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                screen_id, name = await _async_pair_with_code(
                    user_input[CONF_PAIRING_CODE]
                )
            except InvalidPairingCode:
                errors["base"] = "invalid_pairing_code"
            except aiohttp.ClientError, TimeoutError:
                errors["base"] = "cannot_connect"
            except Exception:
                LOGGER.exception("Unexpected error pairing with TV code")
                errors["base"] = "unknown"
            else:
                if self.source == SOURCE_REAUTH:
                    return self._async_update_reauth_entry({CONF_SCREEN_ID: screen_id})
                await self.async_set_unique_id(screen_unique_id(screen_id))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=name or DEFAULT_TITLE, data={CONF_SCREEN_ID: screen_id}
                )
        return self.async_show_form(
            step_id="pairing_code", data_schema=PAIRING_CODE_SCHEMA, errors=errors
        )

    async def async_step_ssdp(
        self, discovery_info: SsdpServiceInfo
    ) -> ConfigFlowResult:
        """Handle a DIAL server found by SSDP."""
        location = discovery_info.ssdp_location
        if not location:
            return self.async_abort(reason="not_supported")
        try:
            screen = await async_get_screen_from_location(self.hass, location)
        except DialNoScreenError:
            return self.async_abort(reason="not_supported")
        except DialConnectionError:
            return self.async_abort(reason="cannot_connect")

        host = urlparse(location).hostname
        await self._async_set_unique_id_from_screen(screen)
        self._abort_if_unique_id_configured(updates=self._host_data(host, screen))
        # A TV added with a pairing code has a different unique id.
        self._async_abort_entries_match({CONF_SCREEN_ID: screen.screen_id})

        self._host = host
        self._screen = screen
        self.context["title_placeholders"] = {"name": screen.name or host}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding a discovered TV."""
        assert self._screen is not None
        if user_input is not None:
            return self._async_create_from_screen()
        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={"name": self._screen.name or str(self._host)},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle YouTube rejecting the stored screen id."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Read a fresh screen id from the TV, or fall back to a TV code."""
        entry = self._get_reauth_entry()
        host = entry.data.get(CONF_HOST)
        if host is None:
            return await self.async_step_pairing_code()

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                screen = await async_get_screen_from_host(self.hass, host)
            except DialConnectionError:
                errors["base"] = "cannot_connect"
            except DialNoScreenError:
                errors["base"] = "no_screen_id"
            else:
                return self._async_update_reauth_entry(self._host_data(host, screen))
        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={"name": entry.title},
            errors=errors,
        )

    async def _async_set_unique_id_from_screen(self, screen: DialScreen) -> None:
        await self.async_set_unique_id(
            screen.udn or screen_unique_id(screen.screen_id), raise_on_progress=False
        )

    @staticmethod
    def _host_data(host: str | None, screen: DialScreen) -> dict[str, Any]:
        data: dict[str, Any] = {
            CONF_SCREEN_ID: screen.screen_id,
            CONF_APP_URL: screen.app_url,
        }
        if host:
            data[CONF_HOST] = host
        if screen.manufacturer:
            data[CONF_MANUFACTURER] = screen.manufacturer
        if screen.model:
            data[CONF_MODEL] = screen.model
        return data

    def _async_create_from_screen(self) -> ConfigFlowResult:
        assert self._screen is not None
        return self.async_create_entry(
            title=self._screen.name or DEFAULT_TITLE,
            data=self._host_data(self._host, self._screen),
        )

    def _async_update_reauth_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        # The screen id legitimately changes when YouTube rotates it, so the
        # entry's unique id is kept rather than compared.
        return self.async_update_reload_and_abort(
            self._get_reauth_entry(), data_updates=data
        )


class TvOptionsFlow(OptionsFlow):
    """How to open YouTube on a TV added with a code.

    The Lounge protocol can't start the app, and such a TV has no DIAL
    address. The actions run before playing a video or turning on when the
    TV's Lounge screen is offline, e.g. wake a streaming box and select the
    YouTube app on it.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the open actions."""
        errors: dict[str, str] = {}
        if user_input is not None:
            actions = user_input.get(CONF_OPEN_ACTIONS) or []
            try:
                cv.SCRIPT_SCHEMA(actions)
            except vol.Invalid:
                errors[CONF_OPEN_ACTIONS] = "invalid_actions"
            else:
                return self.async_create_entry(data={CONF_OPEN_ACTIONS: actions})
        current = self.config_entry.options.get(CONF_OPEN_ACTIONS) or []
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_OPEN_ACTIONS,
                        description={"suggested_value": current},
                    ): ActionSelector()
                }
            ),
            errors=errors,
        )
