"""Sensors for YouTube on TV."""

from __future__ import annotations

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
    # The app state comes from DIAL, unknown for TVs added with a TV code.
    if coordinator.has_app_state:
        async_add_entities([YouTubeOnTvAppStateSensor(coordinator)])


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
