"""Switches for YouTube on TV."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import YouTubeOnTvConfigEntry
from .const import DOMAIN
from .coordinator import SUBTITLES_OFF, YouTubeOnTvCoordinator
from .entity import YouTubeOnTvEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YouTubeOnTvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switches."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            YouTubeOnTvAutoplaySwitch(coordinator),
            YouTubeOnTvSubtitlesSwitch(coordinator),
        ]
    )


class YouTubeOnTvAutoplaySwitch(YouTubeOnTvEntity, SwitchEntity):
    """YouTube's autoplay setting on the TV."""

    _attr_translation_key = "autoplay"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: YouTubeOnTvCoordinator) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, "autoplay")

    @property
    def available(self) -> bool:
        """Return False if the TV reports autoplay as unsupported."""
        return super().available and self.coordinator.data.autoplay_supported

    @property
    def is_on(self) -> bool | None:
        """Return True if autoplay is on, None until the TV reports it."""
        return self.coordinator.data.autoplay

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn autoplay on."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn autoplay off."""
        await self._async_set(False)

    async def _async_set(self, enabled: bool) -> None:
        await self.coordinator.async_command(
            self.coordinator.api.set_auto_play_mode, enabled
        )
        # The TV takes a few seconds to apply and report it back.
        self.coordinator.handle_autoplay(
            enabled, self.coordinator.data.autoplay_supported
        )


class YouTubeOnTvSubtitlesSwitch(YouTubeOnTvEntity, SwitchEntity):
    """Subtitles of the video playing on the TV."""

    _attr_translation_key = "subtitles"

    def __init__(self, coordinator: YouTubeOnTvCoordinator) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, "subtitles")

    @property
    def is_on(self) -> bool | None:
        """Return True if subtitles are showing, None until the TV reports."""
        subtitles = self.coordinator.data.subtitles
        if subtitles is None:
            return None
        return subtitles != SUBTITLES_OFF

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Show subtitles in the last language used."""
        data = self.coordinator.data
        if not data.last_subtitles_code:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_subtitles_language"
            )
        await self._async_set(data.last_subtitles_code, data.last_subtitles_name)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Hide subtitles."""
        await self._async_set(None, None)

    async def _async_set(self, code: str | None, name: str | None) -> None:
        video_id = self.coordinator.data.video_id
        if video_id is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_video"
            )
        await self.coordinator.async_command(
            self.coordinator.api.set_closed_captions, code, video_id
        )
        self.coordinator.note_subtitles(code, name)
