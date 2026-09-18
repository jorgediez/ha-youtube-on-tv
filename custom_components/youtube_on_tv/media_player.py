"""Media player showing what the TV's YouTube app is playing."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import YouTubeOnTvConfigEntry
from .coordinator import PlayerStatus, YouTubeOnTvCoordinator
from .entity import YouTubeOnTvEntity

PARALLEL_UPDATES = 1

_STATES = {
    PlayerStatus.OFF: MediaPlayerState.OFF,
    PlayerStatus.IDLE: MediaPlayerState.IDLE,
    PlayerStatus.PLAYING: MediaPlayerState.PLAYING,
    PlayerStatus.PAUSED: MediaPlayerState.PAUSED,
    PlayerStatus.BUFFERING: MediaPlayerState.BUFFERING,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YouTubeOnTvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the media player."""
    async_add_entities([YouTubeOnTvMediaPlayer(entry.runtime_data)])


class YouTubeOnTvMediaPlayer(YouTubeOnTvEntity, MediaPlayerEntity):
    """The TV's YouTube app."""

    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_media_content_type = MediaType.VIDEO
    _attr_media_image_remotely_accessible = True
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.SEEK
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
    )

    def __init__(self, coordinator: YouTubeOnTvCoordinator) -> None:
        """Initialize the media player."""
        super().__init__(coordinator, "media_player")

    @property
    def state(self) -> MediaPlayerState:
        """Return the playback state."""
        return _STATES[self.coordinator.data.status]

    @property
    def media_content_id(self) -> str | None:
        """Return the YouTube video id."""
        return self.coordinator.data.video_id

    @property
    def media_title(self) -> str | None:
        """Return the video title."""
        return self.coordinator.data.title

    @property
    def media_artist(self) -> str | None:
        """Return the channel name."""
        return self.coordinator.data.channel

    @property
    def media_image_url(self) -> str | None:
        """Return the video thumbnail."""
        return self.coordinator.data.thumbnail_url

    @property
    def media_duration(self) -> int | None:
        """Return the video duration in seconds."""
        duration = self.coordinator.data.duration
        return round(duration) if duration else None

    @property
    def media_position(self) -> int | None:
        """Return the playback position in seconds."""
        position = self.coordinator.data.position
        return round(position) if position is not None else None

    @property
    def media_position_updated_at(self) -> datetime | None:
        """Return when the position was last reported."""
        return self.coordinator.data.position_updated_at

    async def async_media_play(self) -> None:
        """Resume playback."""
        await self.coordinator.async_command(self.coordinator.api.play)

    async def async_media_pause(self) -> None:
        """Pause playback."""
        await self.coordinator.async_command(self.coordinator.api.pause)

    async def async_media_seek(self, position: float) -> None:
        """Seek to a position in seconds."""
        await self.coordinator.async_command(self.coordinator.api.seek_to, position)

    async def async_media_next_track(self) -> None:
        """Play the next video."""
        await self.coordinator.async_command(self.coordinator.api.next)

    async def async_media_previous_track(self) -> None:
        """Play the previous video."""
        await self.coordinator.async_command(self.coordinator.api.previous)
