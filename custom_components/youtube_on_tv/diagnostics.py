"""Diagnostics for YouTube on TV."""

from __future__ import annotations

from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from . import YouTubeOnTvConfigEntry
from .const import CONF_SCREEN_ID, DOMAIN

# The screen id grants control of the TV's YouTube app.
TO_REDACT = {CONF_SCREEN_ID, "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: YouTubeOnTvConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    api = coordinator.api
    integration = await async_get_integration(hass, DOMAIN)
    try:
        library_version = version("pyytlounge")
    except PackageNotFoundError:
        library_version = None
    connected = api.connected()
    return {
        "versions": {
            "integration": str(integration.version),
            "pyytlounge": library_version,
        },
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "lounge": {
            "linked": api.linked(),
            "connected": connected,
            "screen_name": api.screen_name if api.linked() else None,
            "screen_device_name": api.screen_device_name if connected else None,
        },
        "dial": {
            "polled": coordinator.has_app_state,
            "app_state": coordinator.app_state,
        },
        "available": coordinator.last_update_success,
        "state": asdict(coordinator.data),
        "pending_state": asdict(coordinator.working_state),
    }
