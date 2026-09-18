"""Tests for the diagnostic sensors, the skip ad button and play_media."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pyytlounge import NowPlayingEvent, State

from custom_components.youtube_on_tv.const import (
    APP_STATE_INTERVAL,
    CONF_SCREEN_ID,
    DOMAIN,
)
from custom_components.youtube_on_tv.media_player import parse_video_id
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    DOMAIN as MP_DOMAIN,
    SERVICE_PLAY_MEDIA,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .conftest import SCREEN_ID, FakeLounge, wait_for

PREFIX = "youtube_on_samsung_neo_qled"
CONNECTED_ID = f"binary_sensor.{PREFIX}_connectivity"
APP_STATE_ID = f"sensor.{PREFIX}_app_state"
SKIP_AD_ID = f"button.{PREFIX}_skip_ad"
PLAYER_ID = f"media_player.{PREFIX}"
VIDEO_ID = "Yeke1krzPFM"


async def test_connected_sensor(
    hass: HomeAssistant, init_integration: FakeLounge
) -> None:
    """The connectivity sensor stays available and reports the session."""
    assert hass.states.get(CONNECTED_ID).state == STATE_ON
    entry = er.async_get(hass).async_get(CONNECTED_ID)
    assert entry.entity_category == "diagnostic"

    init_integration.connect.side_effect = TimeoutError
    init_integration.drop_connection()
    await wait_for(lambda: hass.states.get(CONNECTED_ID).state == STATE_OFF)
    assert hass.states.get(PLAYER_ID).state == STATE_UNAVAILABLE


async def test_app_state_sensor_disabled_by_default(
    hass: HomeAssistant, init_integration: FakeLounge
) -> None:
    """The app state sensor is created disabled."""
    entry = er.async_get(hass).async_get(APP_STATE_ID)
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entry.entity_category == "diagnostic"


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_app_state_sensor(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_app_state: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The app state follows the DIAL poll, including an unreachable TV."""
    assert hass.states.get(APP_STATE_ID).state == "running"

    for dial_state, expected in (
        ("stopped", "stopped"),
        (None, "unreachable"),
        ("hidden", "hidden"),
        ("installable", "unknown"),
    ):
        mock_app_state.return_value = dial_state
        freezer.tick(APP_STATE_INTERVAL)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert hass.states.get(APP_STATE_ID).state == expected


async def test_no_app_state_sensor_without_dial(
    hass: HomeAssistant,
    mock_lounge: list[FakeLounge],
    mock_app_state: AsyncMock,
) -> None:
    """TVs added with a TV code have no DIAL endpoint to poll."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Kitchen TV",
        unique_id="kitchen",
        data={CONF_SCREEN_ID: SCREEN_ID},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert registry.async_get("sensor.youtube_on_kitchen_tv_app_state") is None
    assert registry.async_get("media_player.youtube_on_kitchen_tv") is not None
    mock_app_state.assert_not_called()


async def test_skip_ad_button(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The button is only available while the ad can be skipped."""
    assert hass.states.get(SKIP_AD_ID).state == STATE_UNAVAILABLE

    coordinator = mock_config_entry.runtime_data
    coordinator.handle_now_playing(
        NowPlayingEvent(
            {"videoId": VIDEO_ID, "state": "1081", "currentTime": "0", "duration": "6"}
        )
    )
    coordinator.handle_ad_state(State.Playing, False)
    await hass.async_block_till_done()
    assert hass.states.get(SKIP_AD_ID).state == STATE_UNAVAILABLE

    coordinator.handle_ad_state(State.Playing, True)
    await hass.async_block_till_done()
    assert hass.states.get(SKIP_AD_ID).state != STATE_UNAVAILABLE

    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: SKIP_AD_ID}, blocking=True
    )
    init_integration.skip_ad.assert_awaited_once()

    coordinator.handle_ad_state(State.AdSkipped, False)
    await hass.async_block_till_done()
    assert hass.states.get(SKIP_AD_ID).state == STATE_UNAVAILABLE


@pytest.mark.parametrize(
    "media_id",
    [
        VIDEO_ID,
        f"https://www.youtube.com/watch?v={VIDEO_ID}&t=42s",
        f"https://youtu.be/{VIDEO_ID}?si=abc",
        f"youtube.com/shorts/{VIDEO_ID}",
        f"https://m.youtube.com/watch?feature=share&v={VIDEO_ID}",
        f"https://www.youtube.com/live/{VIDEO_ID}",
        f"https://music.youtube.com/watch?v={VIDEO_ID}",
    ],
)
async def test_play_media(
    hass: HomeAssistant, init_integration: FakeLounge, media_id: str
) -> None:
    """Videos are played by id or YouTube URL."""
    await hass.services.async_call(
        MP_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: PLAYER_ID,
            ATTR_MEDIA_CONTENT_ID: media_id,
            ATTR_MEDIA_CONTENT_TYPE: "video",
        },
        blocking=True,
    )
    init_integration.play_video.assert_awaited_once_with(VIDEO_ID)


@pytest.mark.parametrize(
    "media_id",
    [
        "not a video",
        "https://vimeo.com/watch?v=Yeke1krzPFM",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/playlist?list=PL123",
    ],
)
async def test_play_media_invalid(
    hass: HomeAssistant, init_integration: FakeLounge, media_id: str
) -> None:
    """Anything that isn't a YouTube video is rejected before contacting the TV."""
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            MP_DOMAIN,
            SERVICE_PLAY_MEDIA,
            {
                ATTR_ENTITY_ID: PLAYER_ID,
                ATTR_MEDIA_CONTENT_ID: media_id,
                ATTR_MEDIA_CONTENT_TYPE: "video",
            },
            blocking=True,
        )
    assert err.value.translation_key == "invalid_video"
    init_integration.play_video.assert_not_awaited()


def test_parse_video_id_edge_cases() -> None:
    """Pasted ids with whitespace work; URLs without an id don't."""
    assert parse_video_id(f"  {VIDEO_ID}\n") == VIDEO_ID
    assert parse_video_id("https://youtu.be/") is None
    assert parse_video_id("https://www.youtube.com/shorts/") is None


async def test_unchanged_app_state_does_not_write(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_app_state: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Identical DIAL answers don't write entity state."""
    before = hass.states.get(PLAYER_ID).last_updated
    for _ in range(3):
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert hass.states.get(PLAYER_ID).last_updated == before
