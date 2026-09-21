"""Tests for setting up and unloading YouTube on TV."""

from __future__ import annotations

from unittest.mock import AsyncMock

import aiohttp
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.youtube_on_tv.const import CONF_MODEL, CONF_SCREEN_ID, DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import ENTRY_DATA, SCREEN_ID, FakeLounge, wait_for


async def test_setup_and_unload(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Unloading disconnects from the TV and stops listening."""
    assert mock_config_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    init_integration.disconnect.assert_awaited_once()
    init_integration.close.assert_awaited_once()


async def test_setup_not_ready(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_lounge: list[FakeLounge],
    mock_app_state: AsyncMock,
) -> None:
    """Network errors while linking retry setup later."""
    mock_config_entry.add_to_hass(hass)
    with_error = FakeLounge.__init__

    def failing_init(self, *args, **kwargs) -> None:
        with_error(self, *args, **kwargs)
        self.pair_with_screen_id.side_effect = aiohttp.ClientError("offline")

    FakeLounge.__init__ = failing_init
    try:
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    finally:
        FakeLounge.__init__ = with_error

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_lounge[0].close.assert_awaited_once()


async def test_setup_revoked_screen_id(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_lounge: list[FakeLounge],
    mock_app_state: AsyncMock,
) -> None:
    """A screen id YouTube no longer accepts starts a reauth flow."""
    mock_config_entry.add_to_hass(hass)
    original = FakeLounge.__init__

    def revoked_init(self, *args, **kwargs) -> None:
        original(self, *args, **kwargs)
        self.pair_with_screen_id.side_effect = IndexError("no screens")

    FakeLounge.__init__ = revoked_init
    try:
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    finally:
        FakeLounge.__init__ = original

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH


async def test_revoked_while_running(
    hass: HomeAssistant,
    init_integration: FakeLounge,
) -> None:
    """Repeated link failures in the background start a reauth flow."""
    init_integration.pair_with_screen_id.side_effect = KeyError("loungeToken")
    init_integration._linked = False
    init_integration.drop_connection()

    await wait_for(
        lambda: len(hass.config_entries.flow.async_progress_by_handler(DOMAIN)) == 1
    )
    assert init_integration.pair_with_screen_id.await_count == 4


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Diagnostics don't leak the screen id."""
    from pytest_homeassistant_custom_component.components.diagnostics import (
        get_diagnostics_for_config_entry,
    )

    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )
    assert diagnostics["entry"]["data"]["screen_id"] == "**REDACTED**"
    assert diagnostics["entry"]["unique_id"] == "**REDACTED**"
    assert diagnostics["lounge"] == {
        "linked": True,
        "connected": True,
        "screen_name": "YouTube on TV",
        "screen_device_name": "Samsung TQ75QN900FTXXC",
    }
    assert diagnostics["dial"] == {"polled": True, "app_state": "running"}
    assert diagnostics["versions"]["integration"] == "0.1.0"
    assert diagnostics["state"]["status"] == "off"
    assert diagnostics["pending_state"]["status"] == "off"
    assert SCREEN_ID not in str(diagnostics)


async def test_one_device_per_tv(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_lounge: list[FakeLounge],
    mock_app_state: AsyncMock,
) -> None:
    """Each TV gets its own "YouTube on <TV>" device holding its entities."""
    bedroom = MockConfigEntry(
        domain=DOMAIN,
        title="Bedroom TV",
        unique_id="uuid:bedroom",
        data={**ENTRY_DATA, CONF_SCREEN_ID: "b" * 64, CONF_MODEL: "UE43"},
    )
    for entry in (mock_config_entry, bedroom):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    expected = {
        mock_config_entry: (
            "YouTube on Samsung Neo QLED",
            "TQ75QN900FTXXC",
            {
                "media_player.youtube_on_samsung_neo_qled",
                "binary_sensor.youtube_on_samsung_neo_qled_ad_playing",
                "binary_sensor.youtube_on_samsung_neo_qled_connectivity",
                "button.youtube_on_samsung_neo_qled_skip_ad",
                "sensor.youtube_on_samsung_neo_qled_app_state",
                "sensor.youtube_on_samsung_neo_qled_up_next",
                "sensor.youtube_on_samsung_neo_qled_subtitles",
                "sensor.youtube_on_samsung_neo_qled_video_quality",
                "switch.youtube_on_samsung_neo_qled_autoplay",
                "switch.youtube_on_samsung_neo_qled_subtitles",
                "select.youtube_on_samsung_neo_qled_playback_speed",
            },
        ),
        bedroom: (
            "YouTube on Bedroom TV",
            "UE43",
            {
                "media_player.youtube_on_bedroom_tv",
                "binary_sensor.youtube_on_bedroom_tv_ad_playing",
                "binary_sensor.youtube_on_bedroom_tv_connectivity",
                "button.youtube_on_bedroom_tv_skip_ad",
                "sensor.youtube_on_bedroom_tv_app_state",
                "sensor.youtube_on_bedroom_tv_up_next",
                "sensor.youtube_on_bedroom_tv_subtitles",
                "sensor.youtube_on_bedroom_tv_video_quality",
                "switch.youtube_on_bedroom_tv_autoplay",
                "switch.youtube_on_bedroom_tv_subtitles",
                "select.youtube_on_bedroom_tv_playback_speed",
            },
        ),
    }
    for entry, (name, model, entity_ids) in expected.items():
        (device,) = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
        assert device.name == name
        assert device.manufacturer == "Samsung"
        assert device.model == model
        assert device.identifiers == {(DOMAIN, entry.unique_id)}
        entities = er.async_entries_for_device(
            entity_registry, device.id, include_disabled_entities=True
        )
        assert {entity.entity_id for entity in entities} == entity_ids


async def test_device_named_by_youtube(
    hass: HomeAssistant,
    mock_lounge: list[FakeLounge],
    mock_app_state: AsyncMock,
) -> None:
    """A TV added by code keeps YouTube's "YouTube on TV" name as is."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="YouTube on TV",
        unique_id="0123456789abcdef",
        data={CONF_SCREEN_ID: SCREEN_ID},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.name == "YouTube on TV"
    assert hass.states.get("media_player.youtube_on_tv") is not None
