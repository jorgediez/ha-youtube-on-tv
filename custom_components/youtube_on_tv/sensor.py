"""Sensors for YouTube on TV."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import YouTubeOnTvConfigEntry
from .coordinator import YouTubeOnTvCoordinator
from .entity import YouTubeOnTvEntity

PARALLEL_UPDATES = 0

APP_STATES = ["running", "stopped", "hidden", "unreachable"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YouTubeOnTvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        YouTubeOnTvUpNextSensor(coordinator),
        YouTubeOnTvSubtitlesSensor(coordinator),
        YouTubeOnTvVideoQualitySensor(coordinator),
    ]
    # The app state comes from DIAL, unknown for TVs added with a TV code.
    if coordinator.has_app_state:
        entities.append(YouTubeOnTvAppStateSensor(coordinator))
    async_add_entities(entities)


class YouTubeOnTvAppStateSensor(YouTubeOnTvEntity, SensorEntity):
    """State of the YouTube app as reported by the TV over DIAL."""

    _attr_translation_key = "app_state"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = APP_STATES
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: YouTubeOnTvCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "app_state")

    @property
    def available(self) -> bool:
        """Stay available: DIAL is polled locally, independently of YouTube."""
        return True

    @property
    def native_value(self) -> str | None:
        """Return the app state."""
        state = self.coordinator.app_state
        if state is None:
            return "unreachable"
        return state if state in APP_STATES else None


class YouTubeOnTvUpNextSensor(YouTubeOnTvEntity, SensorEntity):
    """The video autoplay will play next."""

    _attr_translation_key = "up_next"

    def __init__(self, coordinator: YouTubeOnTvCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "up_next")

    @property
    def native_value(self) -> str | None:
        """Return the next video's title, or its id until the title is known."""
        data = self.coordinator.data
        return data.up_next_title or data.up_next_video_id

    @property
    def entity_picture(self) -> str | None:
        """Return the next video's thumbnail."""
        return self.coordinator.data.up_next_thumbnail_url

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Return the next video's id."""
        return {"video_id": self.coordinator.data.up_next_video_id}


class YouTubeOnTvSubtitlesSensor(YouTubeOnTvEntity, SensorEntity):
    """Subtitles language of the current video, or "off"."""

    _attr_translation_key = "subtitles"

    def __init__(self, coordinator: YouTubeOnTvCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "subtitles")

    @property
    def native_value(self) -> str | None:
        """Return the subtitles language."""
        return self.coordinator.data.subtitles

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the track details and how the TV displays them."""
        data = self.coordinator.data
        return {
            "language_code": data.subtitles_code,
            "track_name": data.subtitles_track,
            # "asr" means automatically generated.
            "kind": data.subtitles_kind,
            "style": data.subtitles_style,
        }


class YouTubeOnTvVideoQualitySensor(YouTubeOnTvEntity, SensorEntity):
    """Resolution the TV is playing, as it reports it."""

    _attr_translation_key = "video_quality"
    _attr_native_unit_of_measurement = "p"

    def __init__(self, coordinator: YouTubeOnTvCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "video_quality")

    @property
    def native_value(self) -> str | None:
        """Return the current resolution, e.g. 1080."""
        return self.coordinator.data.video_quality

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the resolutions the video offers."""
        levels = self.coordinator.data.video_quality_levels
        return {"available_levels": list(levels) if levels else None}
