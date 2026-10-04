"""The YouTube on TV integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import YouTubeOnTvCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TODO,
]

type YouTubeOnTvConfigEntry = ConfigEntry[YouTubeOnTvCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: YouTubeOnTvConfigEntry) -> bool:
    """Set up YouTube on TV from a config entry."""
    coordinator = YouTubeOnTvCoordinator(hass, entry)
    await coordinator.async_start()
    entry.runtime_data = coordinator
    # New open actions change the media player's features at once.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: YouTubeOnTvConfigEntry
) -> None:
    """Redraw the entities after the options change."""
    entry.runtime_data.async_update_listeners()


async def async_unload_entry(
    hass: HomeAssistant, entry: YouTubeOnTvConfigEntry
) -> bool:
    """Unload a config entry; the coordinator shuts down with it."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
