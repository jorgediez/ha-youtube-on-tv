"""Diagnostics for YouTube on TV."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import YouTubeOnTvConfigEntry
from .const import CONF_APP_URL, CONF_SCREEN_ID

# The screen id grants control of the TV and the app URL embeds its address.
TO_REDACT = {CONF_SCREEN_ID, CONF_APP_URL, "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: YouTubeOnTvConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    api = coordinator.api
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "lounge": {
            "linked": api.linked(),
            "connected": api.connected(),
            "screen_device_name": api.screen_device_name if api.connected() else None,
        },
        "available": coordinator.last_update_success,
        "state": asdict(coordinator.data),
    }
