"""Diagnostics for YouTube on TV.

Meant for reports from TVs other than the ones this was built on: how the TV
was added, what the Lounge and DIAL sides report, and the playback state.

It leaves out what identifies a user or would let someone control the TV.
The screen id (which grants control of the TV's YouTube app), the unique id
and SSDP discovery keys (the TV's UDN), and the TV's address are redacted.
Of the playback state, what is being watched is left out: the video ids,
titles and channel are redacted when set, and the queue is reduced to its
length.
"""

from __future__ import annotations

from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from . import YouTubeOnTvConfigEntry
from .const import CONF_APP_URL, CONF_SCREEN_ID, DOMAIN
from .coordinator import TvState

TO_REDACT = {CONF_SCREEN_ID, "unique_id", "discovery_keys", CONF_HOST, CONF_APP_URL}

#: What is being watched; redacted only when set, so its presence still shows.
WATCHING = {"video_id", "title", "channel", "up_next_video_id", "up_next_title"}


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
        "state": _playback(coordinator.data),
        "pending_state": _playback(coordinator.working_state),
    }


def _playback(state: TvState) -> dict[str, Any]:
    """Return a playback state without what is being watched."""
    data = asdict(state)
    data["queue_length"] = len(data.pop("queue"))
    return async_redact_data(data, WATCHING)
