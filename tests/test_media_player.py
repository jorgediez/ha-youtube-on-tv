"""Tests for the media player and the playback state it reflects."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from pyytlounge import (
    AdPlayingEvent,
    AdStateEvent,
    DisconnectedEvent,
    NowPlayingEvent,
    PlaybackStateEvent,
    State,
)

from custom_components.youtube_on_tv.const import APP_STATE_INTERVAL, SETTLE_DELAY
from custom_components.youtube_on_tv.coordinator import YouTubeOnTvCoordinator
from homeassistant.components.media_player import (
    ATTR_MEDIA_ARTIST,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_DURATION,
    ATTR_MEDIA_POSITION,
    ATTR_MEDIA_SEEK_POSITION,
    ATTR_MEDIA_TITLE,
    DOMAIN as MP_DOMAIN,
    SERVICE_MEDIA_NEXT_TRACK,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_PLAY,
    SERVICE_MEDIA_PREVIOUS_TRACK,
    SERVICE_MEDIA_SEEK,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_ENTITY_PICTURE,
    STATE_IDLE,
    STATE_OFF,
    STATE_ON,
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import FakeLounge, wait_for

ENTITY_ID = "media_player.youtube_on_samsung_neo_qled"
AD_SENSOR_ID = "binary_sensor.youtube_on_samsung_neo_qled_ad_playing"
VIDEO_ID = "Yeke1krzPFM"
OEMBED = {"title": "Dolor y Gloria", "author_name": "VivaSueciaVEVO"}


@pytest.fixture(autouse=True)
def mock_oembed(aioclient_mock: AiohttpClientMocker) -> None:
    """Answer title lookups."""
    aioclient_mock.get("https://www.youtube.com/oembed", json=OEMBED)


def _coordinator(entry: MockConfigEntry) -> YouTubeOnTvCoordinator:
    return entry.runtime_data


def _now_playing(
    video_id: str | None, state: State, position: float = 0, duration: float = 0
) -> NowPlayingEvent:
    data = {"state": str(state.value)}
    if video_id:
        data |= {
            "videoId": video_id,
            "currentTime": str(position),
            "duration": str(duration),
        }
    return NowPlayingEvent(data)


async def _settle(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(seconds=SETTLE_DELAY + 0.1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_initial_state(hass: HomeAssistant, init_integration: FakeLounge) -> None:
    """The player is idle until something plays and linked once."""
    assert hass.states.get(ENTITY_ID).state == STATE_OFF
    assert hass.states.get(AD_SENSOR_ID).state == "off"
    init_integration.pair_with_screen_id.assert_awaited_once()
    init_integration.get_now_playing.assert_awaited()


async def test_now_playing(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A playing video is shown with title, channel and thumbnail."""
    coordinator = _coordinator(mock_config_entry)
    coordinator.handle_now_playing(_now_playing(VIDEO_ID, State.Playing, 86, 237))
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_PLAYING
    assert state.attributes[ATTR_MEDIA_CONTENT_ID] == VIDEO_ID
    assert state.attributes[ATTR_MEDIA_TITLE] == "Dolor y Gloria"
    assert state.attributes[ATTR_MEDIA_ARTIST] == "VivaSueciaVEVO"
    assert state.attributes[ATTR_MEDIA_DURATION] == 237
    assert state.attributes[ATTR_MEDIA_POSITION] == 86
    assert (
        state.attributes[ATTR_ENTITY_PICTURE]
        == f"https://i.ytimg.com/vi/{VIDEO_ID}/hqdefault.jpg"
    )


async def test_event_burst_is_deduplicated(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Repeated events that only advance the position don't write state."""
    coordinator = _coordinator(mock_config_entry)
    coordinator.handle_now_playing(_now_playing(VIDEO_ID, State.Playing, 86, 237))
    await hass.async_block_till_done()
    first = hass.states.get(ENTITY_ID)

    for second in range(1, 4):
        freezer.tick(timedelta(seconds=1))
        coordinator.handle_now_playing(
            _now_playing(VIDEO_ID, State.Playing, 86 + second, 237)
        )
        await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).last_updated == first.last_updated
    assert hass.states.get(ENTITY_ID).attributes[ATTR_MEDIA_POSITION] == 86

    # A seek changes the position beyond the tolerance.
    coordinator.handle_playback_state(State.Playing, 200, 237)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).attributes[ATTR_MEDIA_POSITION] == 200


async def test_seek_does_not_flicker(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The short pause and buffering during a seek are not published."""
    coordinator = _coordinator(mock_config_entry)
    coordinator.handle_now_playing(_now_playing(VIDEO_ID, State.Playing, 100, 237))
    await hass.async_block_till_done()
    states: list[str] = []
    hass.bus.async_listen(
        "state_changed",
        lambda event: (
            states.append(event.data["new_state"].state)
            if event.data["entity_id"] == ENTITY_ID
            else None
        ),
    )

    coordinator.handle_playback_state(State.Paused, 193, 237)
    coordinator.handle_playback_state(State.Starting, 172, 237)
    coordinator.handle_playback_state(State.Playing, 172, 237)
    await _settle(hass, freezer)

    assert states == [STATE_PLAYING]
    assert hass.states.get(ENTITY_ID).attributes[ATTR_MEDIA_POSITION] == 172


async def test_pause_is_published_after_settling(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A real pause shows up once it has held for the settle delay."""
    coordinator = _coordinator(mock_config_entry)
    coordinator.handle_now_playing(_now_playing(VIDEO_ID, State.Playing, 100, 237))
    await hass.async_block_till_done()

    coordinator.handle_playback_state(State.Paused, 101, 237)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_PLAYING

    await _settle(hass, freezer)
    assert hass.states.get(ENTITY_ID).state == STATE_PAUSED
    assert hass.states.get(ENTITY_ID).attributes[ATTR_MEDIA_POSITION] == 101


async def test_ads(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Ad durations are not reported as the video's; the ad sensor turns on."""
    coordinator = _coordinator(mock_config_entry)
    coordinator.handle_now_playing(_now_playing(VIDEO_ID, State.Starting))
    coordinator.handle_now_playing(_now_playing(VIDEO_ID, State.Advertisement, 0, 6))
    coordinator.handle_ad_state(State.Playing, False)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_PLAYING
    assert ATTR_MEDIA_DURATION not in state.attributes
    ad = hass.states.get(AD_SENSOR_ID)
    assert ad.state == STATE_ON
    assert ad.attributes["skippable"] is False

    coordinator.handle_ad_state(State.Playing, True)
    await hass.async_block_till_done()
    assert hass.states.get(AD_SENSOR_ID).attributes["skippable"] is True

    # Skipped: brief stopped state, then the video plays.
    coordinator.handle_ad_state(State.AdSkipped, False)
    coordinator.handle_playback_state(State.Stopped, 0, 163)
    coordinator.handle_playback_state(State.Playing, 0, 163)
    await _settle(hass, freezer)

    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_PLAYING
    assert state.attributes[ATTR_MEDIA_DURATION] == 163
    assert hass.states.get(AD_SENSOR_ID).state == "off"


async def test_video_ends(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """No video means idle, even if a Cued state follows."""
    coordinator = _coordinator(mock_config_entry)
    coordinator.handle_now_playing(_now_playing(VIDEO_ID, State.Playing, 230, 237))
    coordinator.handle_now_playing(_now_playing(None, State.Stopped))
    coordinator.handle_playback_state(State.Cued, 0, 0)
    await _settle(hass, freezer)

    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_IDLE
    assert ATTR_MEDIA_CONTENT_ID not in state.attributes


async def test_disconnect_and_reconnect(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The TV ending the session (Home button) turns the player off; it reconnects."""
    coordinator = _coordinator(mock_config_entry)
    coordinator.handle_now_playing(_now_playing(VIDEO_ID, State.Playing, 10, 237))
    await hass.async_block_till_done()

    init_integration.subscribed.clear()
    coordinator.handle_disconnected()
    init_integration.drop_connection()
    await init_integration.subscribed.wait()
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == STATE_OFF
    assert init_integration.connect.await_count == 2


async def test_app_closed_detected_by_dial(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    mock_app_state: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The DIAL poll turns the player off when YouTube stops running."""
    coordinator = _coordinator(mock_config_entry)
    coordinator.handle_now_playing(_now_playing(None, State.Stopped))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_IDLE

    mock_app_state.return_value = "stopped"
    freezer.tick(APP_STATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_OFF

    # Lounge events while the app is stopped don't bring it back to idle.
    coordinator.handle_now_playing(_now_playing(None, State.Stopped))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_OFF

    init_integration.get_now_playing.reset_mock()
    mock_app_state.return_value = "running"
    freezer.tick(APP_STATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    init_integration.get_now_playing.assert_awaited_once()


async def test_connection_errors_make_entities_unavailable(
    hass: HomeAssistant, init_integration: FakeLounge
) -> None:
    """Failing to reconnect marks entities unavailable until it succeeds."""
    init_integration.connect.side_effect = TimeoutError
    init_integration.drop_connection()
    await wait_for(lambda: hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE)

    init_integration.connect.side_effect = init_integration._connect
    await wait_for(lambda: hass.states.get(ENTITY_ID).state == STATE_OFF)


@pytest.mark.parametrize(
    ("service", "data", "method", "args"),
    [
        (SERVICE_MEDIA_PLAY, {}, "play", ()),
        (SERVICE_MEDIA_PAUSE, {}, "pause", ()),
        (SERVICE_MEDIA_NEXT_TRACK, {}, "next", ()),
        (SERVICE_MEDIA_PREVIOUS_TRACK, {}, "previous", ()),
        (SERVICE_MEDIA_SEEK, {ATTR_MEDIA_SEEK_POSITION: 42}, "seek_to", (42,)),
    ],
)
async def test_commands(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    service: str,
    data: dict,
    method: str,
    args: tuple,
) -> None:
    """Media player services are sent to the TV."""
    await hass.services.async_call(
        MP_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY_ID, **data}, blocking=True
    )
    getattr(init_integration, method).assert_awaited_once_with(*args)


async def test_command_failure(
    hass: HomeAssistant, init_integration: FakeLounge
) -> None:
    """Command errors are raised as translated Home Assistant errors."""
    init_integration.pause.return_value = False
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            MP_DOMAIN, SERVICE_MEDIA_PAUSE, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
        )
    assert err.value.translation_key == "command_failed"


async def test_library_events_are_forwarded(
    hass: HomeAssistant, init_integration: FakeLounge
) -> None:
    """Events built by pyytlounge from raw Lounge payloads reach the entities."""
    listener = init_integration.listener
    await listener.now_playing_changed(
        NowPlayingEvent(
            {
                "videoId": VIDEO_ID,
                "currentTime": "0",
                "duration": "0",
                "state": "3",
            }
        )
    )
    await listener.ad_playing_changed(
        AdPlayingEvent(
            {
                "adVideoId": "ad",
                "adTitle": "",
                "isBumper": "false",
                "isSkippable": "true",
                "isSkipEnabled": "false",
                "clickThroughUrl": "",
                "adSystem": "",
                "adNextParams": "",
                "adState": "1",
                "contentVideoId": VIDEO_ID,
                "duration": "15",
                "currentTime": "0",
            }
        )
    )
    await listener.ad_state_changed(
        AdStateEvent({"adState": "1", "currentTime": "5", "isSkipEnabled": "true"})
    )
    await listener.playback_state_changed(
        PlaybackStateEvent({"currentTime": "0", "duration": "184", "state": "1082"})
    )
    await listener.playback_state_changed(
        PlaybackStateEvent({"currentTime": "1", "duration": "184", "state": "1"})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_PLAYING
    assert state.attributes[ATTR_MEDIA_DURATION] == 184

    await listener.disconnected(DisconnectedEvent({"reason": "disconnectedByUser"}))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_OFF
