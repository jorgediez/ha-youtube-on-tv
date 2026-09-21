"""Selects for YouTube on TV."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import YouTubeOnTvConfigEntry
from .coordinator import YouTubeOnTvCoordinator
from .entity import YouTubeOnTvEntity

PARALLEL_UPDATES = 1

# Speeds offered by the YouTube TV app, keyed by option (translation keys
# can't contain dots).
PLAYBACK_SPEEDS = {
    "0_25": 0.25,
    "0_5": 0.5,
    "0_75": 0.75,
    "1": 1.0,
    "1_25": 1.25,
    "1_5": 1.5,
    "1_75": 1.75,
    "2": 2.0,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YouTubeOnTvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the selects."""
    async_add_entities([YouTubeOnTvPlaybackSpeedSelect(entry.runtime_data)])


class YouTubeOnTvPlaybackSpeedSelect(YouTubeOnTvEntity, SelectEntity):
    """Playback speed of the YouTube app."""

    _attr_translation_key = "playback_speed"

    def __init__(self, coordinator: YouTubeOnTvCoordinator) -> None:
        """Initialize the select."""
        super().__init__(coordinator, "playback_speed")
        self._attr_options = list(PLAYBACK_SPEEDS)

    @property
    def current_option(self) -> str | None:
        """Return the current speed, None until the TV reports it."""
        speed = self.coordinator.data.playback_speed
        if speed is None:
            return None
        for option, value in PLAYBACK_SPEEDS.items():
            if abs(value - speed) < 0.01:
                return option
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the playback speed."""
        speed = PLAYBACK_SPEEDS[option]
        await self.coordinator.async_command(
            self.coordinator.api.set_playback_speed, speed
        )
        # The TV takes a few seconds to apply and report it back.
        self.coordinator.handle_playback_speed(speed)
