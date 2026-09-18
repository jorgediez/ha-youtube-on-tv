"""Media player showing what the TV's YouTube app is playing."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import YouTubeOnTvConfigEntry
from .const import DOMAIN
from .coordinator import PlayerStatus, YouTubeOnTvCoordinator
from .entity import YouTubeOnTvEntity

PARALLEL_UPDATES = 1

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}

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
        | MediaPlayerEntityFeature.PLAY_MEDIA
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

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs: Any
    ) -> None:
        """Play a YouTube video, given its id or URL."""
        video_id = parse_video_id(media_id)
        if video_id is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_video",
                translation_placeholders={"media_id": media_id},
            )
        await self.coordinator.async_command(self.coordinator.api.play_video, video_id)


def parse_video_id(media_id: str) -> str | None:
    """Return the video id from a YouTube video id or URL."""
    media_id = media_id.strip()
    if _VIDEO_ID_RE.match(media_id):
        return media_id
    url = urlparse(media_id if "://" in media_id else f"https://{media_id}")
    host = (url.hostname or "").removeprefix("www.")
    if host not in _YOUTUBE_HOSTS:
        return None
    if host == "youtu.be":
        candidate = url.path.strip("/")
    elif url.path.startswith(("/shorts/", "/live/", "/embed/")):
        candidate = url.path.split("/")[2]
    else:
        candidate = parse_qs(url.query).get("v", [""])[0]
    return candidate if _VIDEO_ID_RE.match(candidate) else None
