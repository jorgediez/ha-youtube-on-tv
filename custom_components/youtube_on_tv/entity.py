"""Base entity for YouTube on TV."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MANUFACTURER, CONF_MODEL, DOMAIN
from .coordinator import YouTubeOnTvCoordinator


class YouTubeOnTvEntity(CoordinatorEntity[YouTubeOnTvCoordinator]):
    """Entity attached to the TV's YouTube app device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: YouTubeOnTvCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        assert entry.unique_id is not None
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        # One device per TV, named "YouTube on <TV>" so it and its entity ids
        # (media_player.youtube_on_<tv>) don't clash with the TV's own device.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            manufacturer=entry.data.get(CONF_MANUFACTURER),
            model=entry.data.get(CONF_MODEL),
            **_device_name(entry.title),
        )


def _device_name(tv_name: str) -> dict[str, Any]:
    """Return the DeviceInfo naming keys for a TV."""
    if tv_name.lower().startswith("youtube"):
        # Names from a TV code are often already "YouTube on TV".
        return {"name": tv_name}
    return {
        "translation_key": "youtube_on_tv",
        "translation_placeholders": {"tv_name": tv_name},
    }
