"""Binary sensors for YouTube on TV."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import YouTubeOnTvConfigEntry
from .coordinator import YouTubeOnTvCoordinator
from .entity import YouTubeOnTvEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YouTubeOnTvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            YouTubeOnTvAdPlayingSensor(coordinator),
            YouTubeOnTvConnectedSensor(coordinator),
        ]
    )


class YouTubeOnTvAdPlayingSensor(YouTubeOnTvEntity, BinarySensorEntity):
    """On while an ad is playing; exposes whether it can be skipped."""

    _attr_translation_key = "ad_playing"

    def __init__(self, coordinator: YouTubeOnTvCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "ad_playing")

    @property
    def is_on(self) -> bool:
        """Return True while an ad is playing."""
        return self.coordinator.data.ad_playing

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        """Return whether the ad can be skipped."""
        return {"skippable": self.coordinator.data.ad_skippable}


class YouTubeOnTvConnectedSensor(YouTubeOnTvEntity, BinarySensorEntity):
    """On while the session with YouTube's servers is up.

    This is not a connection to the TV: it stays up while the TV is off.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "session"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: YouTubeOnTvCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "connected")

    @property
    def available(self) -> bool:
        """Stay available: being disconnected is what this entity reports."""
        return True

    @property
    def is_on(self) -> bool:
        """Return True while connected."""
        return self.coordinator.last_update_success
