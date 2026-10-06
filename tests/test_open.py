"""Tests for opening YouTube on a TV added with a code (open actions)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pyytlounge import NowPlayingEvent, State

from custom_components.youtube_on_tv.const import (
    CONF_OPEN_ACTIONS,
    CONF_SCREEN_ID,
    DOMAIN,
)
from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    DOMAIN as MP_DOMAIN,
    SERVICE_PLAY_MEDIA,
    MediaPlayerEntityFeature,
)
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError

from .conftest import SCREEN_ID, FakeLounge

A, B = "aqz-KE-bpKQ", "eRsGyueVLvQ"
CODE_PLAYER = "media_player.youtube_on_kitchen_tv"
OPEN_ACTIONS = [{"action": "test.open_youtube", "data": {"source": "YouTube"}}]
POLL = "custom_components.youtube_on_tv.coordinator.OPEN_POLL_SECONDS"


async def _setup_code_tv(
    hass: HomeAssistant, mock_lounge: list[FakeLounge], options: dict | None = None
) -> tuple[MockConfigEntry, FakeLounge]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Kitchen TV",
        unique_id="kitchen2",
        data={CONF_SCREEN_ID: SCREEN_ID},
        options=options or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    api = mock_lounge[0]
    await api.subscribed.wait()
    return entry, api


def _register_opener(hass: HomeAssistant, api: FakeLounge, *, online: bool) -> list:
    """Register test.open_youtube; with online, the TV reports in after it."""
    calls: list[ServiceCall] = []

    async def open_youtube(call: ServiceCall) -> None:
        calls.append(call)
        if online:
            # The app comes up on its home screen: nothing playing.
            hass.async_create_task(
                api.listener.now_playing_changed(NowPlayingEvent({}))
            )

    hass.services.async_register("test", "open_youtube", open_youtube)
    return calls


async def _play(hass: HomeAssistant, entity_id: str, media_id: str) -> None:
    await hass.services.async_call(
        MP_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: entity_id,
            ATTR_MEDIA_CONTENT_ID: media_id,
            ATTR_MEDIA_CONTENT_TYPE: "video",
        },
        blocking=True,
    )


async def test_no_open_while_online(
    hass: HomeAssistant, mock_lounge: list[FakeLounge], mock_app_state: AsyncMock
) -> None:
    """A TV already online is not opened again."""
    _, api = await _setup_code_tv(hass, mock_lounge, {CONF_OPEN_ACTIONS: OPEN_ACTIONS})
    calls = _register_opener(hass, api, online=True)
    await api.listener.now_playing_changed(
        NowPlayingEvent(
            {
                "videoId": A,
                "currentTime": "100",
                "duration": "600",
                "state": str(State.Playing.value),
            }
        )
    )
    await hass.async_block_till_done()
    await _play(hass, CODE_PLAYER, B)
    assert calls == []
    api.play_video.assert_awaited_once_with(B)


async def test_play_media_opens_the_app(
    hass: HomeAssistant, mock_lounge: list[FakeLounge], mock_app_state: AsyncMock
) -> None:
    """play_media on an offline TV opens it, then plays."""
    _, api = await _setup_code_tv(hass, mock_lounge, {CONF_OPEN_ACTIONS: OPEN_ACTIONS})
    calls = _register_opener(hass, api, online=True)
    assert hass.states.get(CODE_PLAYER).state == "off"
    with patch(POLL, 0.01):
        await _play(hass, CODE_PLAYER, B)
    assert len(calls) == 1
    assert calls[0].data == {"source": "YouTube"}
    api.play_video.assert_awaited_once_with(B)


async def test_play_list_opens_the_app(
    hass: HomeAssistant, mock_lounge: list[FakeLounge], mock_app_state: AsyncMock
) -> None:
    """A list played from the coordinator opens an offline TV first."""
    entry, api = await _setup_code_tv(
        hass, mock_lounge, {CONF_OPEN_ACTIONS: OPEN_ACTIONS}
    )
    calls = _register_opener(hass, api, online=True)
    with patch(POLL, 0.01):
        await entry.runtime_data.async_play_list([A, B], 1, 30)
    assert len(calls) == 1
    api.set_playlist.assert_awaited_once_with([A, B], 1, 30, "")


async def test_open_times_out(
    hass: HomeAssistant, mock_lounge: list[FakeLounge], mock_app_state: AsyncMock
) -> None:
    """If the app never comes online, the call fails and nothing is sent."""
    _, api = await _setup_code_tv(hass, mock_lounge, {CONF_OPEN_ACTIONS: OPEN_ACTIONS})
    _register_opener(hass, api, online=False)
    with (
        patch(POLL, 0.01),
        patch("custom_components.youtube_on_tv.coordinator.OPEN_TIMEOUT", 0.1),
        pytest.raises(HomeAssistantError) as err,
    ):
        await _play(hass, CODE_PLAYER, B)
    assert err.value.translation_key == "open_timeout"
    api.play_video.assert_not_awaited()


async def test_no_open_actions_sends_anyway(
    hass: HomeAssistant, mock_lounge: list[FakeLounge], mock_app_state: AsyncMock
) -> None:
    """Without open actions the command goes out as before."""
    _, api = await _setup_code_tv(hass, mock_lounge)
    await _play(hass, CODE_PLAYER, B)
    api.play_video.assert_awaited_once_with(B)


async def test_turn_on_runs_open_actions(
    hass: HomeAssistant, mock_lounge: list[FakeLounge], mock_app_state: AsyncMock
) -> None:
    """Open actions make the player turn on-able; turn on runs them."""
    _, api = await _setup_code_tv(hass, mock_lounge, {CONF_OPEN_ACTIONS: OPEN_ACTIONS})
    calls = _register_opener(hass, api, online=True)
    features = hass.states.get(CODE_PLAYER).attributes["supported_features"]
    assert features & MediaPlayerEntityFeature.TURN_ON
    assert not features & MediaPlayerEntityFeature.TURN_OFF
    with patch(POLL, 0.01):
        await hass.services.async_call(
            MP_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: CODE_PLAYER}, blocking=True
        )
    assert len(calls) == 1
    assert hass.states.get(CODE_PLAYER).state == "idle"


async def test_options_flow_sets_open_actions(
    hass: HomeAssistant, mock_lounge: list[FakeLounge], mock_app_state: AsyncMock
) -> None:
    """The TV's options take the open actions; the player can turn on at once."""
    entry, _ = await _setup_code_tv(hass, mock_lounge)
    features = hass.states.get(CODE_PLAYER).attributes["supported_features"]
    assert not features & MediaPlayerEntityFeature.TURN_ON

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_OPEN_ACTIONS: [{"not": "an action"}]}
    )
    assert result["errors"] == {CONF_OPEN_ACTIONS: "invalid_actions"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_OPEN_ACTIONS: OPEN_ACTIONS}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {CONF_OPEN_ACTIONS: OPEN_ACTIONS}
    features = hass.states.get(CODE_PLAYER).attributes["supported_features"]
    assert features & MediaPlayerEntityFeature.TURN_ON


async def test_dial_tv_has_no_options(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A TV with DIAL opens the app itself, so it has no open actions."""
    assert not mock_config_entry.supports_options


async def test_reconnect_wait_cut_short_after_opening(
    hass: HomeAssistant, mock_lounge: list[FakeLounge], mock_app_state: AsyncMock
) -> None:
    """After the open actions, the session retries at once, not after backoff."""
    entry, _ = await _setup_code_tv(hass, mock_lounge)
    coordinator = entry.runtime_data
    wait = hass.async_create_task(coordinator._async_reconnect_wait(300))
    await asyncio.sleep(0)
    assert not wait.done()
    coordinator._reconnect_now.set()
    async with asyncio.timeout(1):
        await wait
