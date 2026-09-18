"""Buttons for YouTube on TV."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import YouTubeOnTvConfigEntry
from .coordinator import YouTubeOnTvCoordinator
from .entity import YouTubeOnTvEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YouTubeOnTvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    async_add_entities([YouTubeOnTvSkipAdButton(entry.runtime_data)])


class YouTubeOnTvSkipAdButton(YouTubeOnTvEntity, ButtonEntity):
    """Skips the playing ad; available only once the ad can be skipped."""

    _attr_translation_key = "skip_ad"

    def __init__(self, coordinator: YouTubeOnTvCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "skip_ad")

    @property
    def available(self) -> bool:
        """Return True while a skippable ad is playing."""
        return super().available and self.coordinator.data.ad_skippable

    async def async_press(self) -> None:
        """Skip the ad."""
        await self.coordinator.async_command(self.coordinator.api.skip_ad)
