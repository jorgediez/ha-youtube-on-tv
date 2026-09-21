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
]

type YouTubeOnTvConfigEntry = ConfigEntry[YouTubeOnTvCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: YouTubeOnTvConfigEntry) -> bool:
    """Set up YouTube on TV from a config entry."""
    coordinator = YouTubeOnTvCoordinator(hass, entry)
    await coordinator.async_start()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: YouTubeOnTvConfigEntry
) -> bool:
    """Unload a config entry; the coordinator shuts down with it."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
