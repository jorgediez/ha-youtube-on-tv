"""Tests for autoplay, playback speed, up next and subtitles."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pyytlounge import (
    AutoplayModeChangedEvent,
    AutoplayUpNextEvent,
    NowPlayingEvent,
    PlaybackSpeedEvent,
    PlaybackStateEvent,
    SubtitlesTrackEvent,
)

from custom_components.youtube_on_tv.const import SETTLE_DELAY
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_ENTITY_PICTURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .conftest import FakeLounge

PREFIX = "youtube_on_samsung_neo_qled"
AUTOPLAY_ID = f"switch.{PREFIX}_autoplay"
SPEED_ID = f"select.{PREFIX}_playback_speed"
UP_NEXT_ID = f"sensor.{PREFIX}_up_next"
SUBTITLES_ID = f"sensor.{PREFIX}_subtitles"
PLAYER_ID = f"media_player.{PREFIX}"
VIDEO_ID = "Yeke1krzPFM"
NEXT_ID = "SJZe9WkKcVc"


def _now_playing(video_id: str | None, state: str = "1") -> NowPlayingEvent:
    data = {"state": state}
    if video_id:
        data |= {"videoId": video_id, "currentTime": "10", "duration": "200"}
    return NowPlayingEvent(data)


async def _settle(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(seconds=SETTLE_DELAY + 0.1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_autoplay_switch(
    hass: HomeAssistant, init_integration: FakeLounge, freezer: FrozenDateTimeFactory
) -> None:
    """The switch follows the TV's autoplay setting and changes it."""
    assert hass.states.get(AUTOPLAY_ID).state == STATE_UNKNOWN
    entry = er.async_get(hass).async_get(AUTOPLAY_ID)
    assert entry.entity_category == "config"

    listener = init_integration.listener
    await listener.autoplay_changed(
        AutoplayModeChangedEvent({"autoplayMode": "ENABLED"})
    )
    await hass.async_block_till_done()
    assert hass.states.get(AUTOPLAY_ID).state == STATE_ON

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: AUTOPLAY_ID}, blocking=True
    )
    init_integration.set_auto_play_mode.assert_awaited_once_with(False)
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: AUTOPLAY_ID}, blocking=True
    )
    init_integration.set_auto_play_mode.assert_awaited_with(True)

    await listener.autoplay_changed(
        AutoplayModeChangedEvent({"autoplayMode": "DISABLED"})
    )
    await hass.async_block_till_done()
    assert hass.states.get(AUTOPLAY_ID).state == STATE_OFF

    await listener.autoplay_changed(
        AutoplayModeChangedEvent({"autoplayMode": "UNSUPPORTED"})
    )
    await _settle(hass, freezer)
    assert hass.states.get(AUTOPLAY_ID).state == STATE_UNAVAILABLE


async def test_settings_survive_playback_stopping(
    hass: HomeAssistant, init_integration: FakeLounge
) -> None:
    """Autoplay and speed are app settings, kept when the player goes off."""
    listener = init_integration.listener
    await listener.autoplay_changed(
        AutoplayModeChangedEvent({"autoplayMode": "ENABLED"})
    )
    await listener.playback_speed_changed(PlaybackSpeedEvent({"playbackSpeed": "1.5"}))
    await listener.now_playing_changed(_now_playing(VIDEO_ID))
    await listener.disconnected(None)
    await hass.async_block_till_done()

    assert hass.states.get(PLAYER_ID).state == STATE_OFF
    assert hass.states.get(AUTOPLAY_ID).state == STATE_ON
    assert hass.states.get(SPEED_ID).state == "1_5"


@pytest.mark.parametrize(
    ("speed", "expected"),
    [
        ("1", "1"),
        ("1.0", "1"),
        ("0.25", "0_25"),
        ("1.75", "1_75"),
        ("1.1", STATE_UNKNOWN),
    ],
)
async def test_playback_speed_state(
    hass: HomeAssistant, init_integration: FakeLounge, speed: str, expected: str
) -> None:
    """Reported speeds map to the select's options."""
    assert hass.states.get(SPEED_ID).state == STATE_UNKNOWN
    await init_integration.listener.playback_speed_changed(
        PlaybackSpeedEvent({"playbackSpeed": speed})
    )
    await hass.async_block_till_done()
    assert hass.states.get(SPEED_ID).state == expected


async def test_playback_speed_select(
    hass: HomeAssistant, init_integration: FakeLounge
) -> None:
    """Selecting a speed sends it to the TV."""
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: SPEED_ID, ATTR_OPTION: "1_25"},
        blocking=True,
    )
    init_integration.set_playback_speed.assert_awaited_once_with(1.25)


async def test_up_next(hass: HomeAssistant, init_integration: FakeLounge) -> None:
    """Up next shows the queued video, and clears once it starts playing."""
    listener = init_integration.listener
    await listener.now_playing_changed(_now_playing(VIDEO_ID))
    await listener.autoplay_up_next_changed(AutoplayUpNextEvent({"videoId": NEXT_ID}))
    await hass.async_block_till_done()

    state = hass.states.get(UP_NEXT_ID)
    assert state.state == "Dolor y Gloria"
    assert state.attributes["video_id"] == NEXT_ID
    assert state.attributes[ATTR_ENTITY_PICTURE] == (
        f"https://i.ytimg.com/vi/{NEXT_ID}/hqdefault.jpg"
    )

    await listener.now_playing_changed(_now_playing(NEXT_ID))
    await hass.async_block_till_done()
    assert hass.states.get(UP_NEXT_ID).state == STATE_UNKNOWN


async def test_up_next_cleared_when_idle(
    hass: HomeAssistant, init_integration: FakeLounge
) -> None:
    """Up next and subtitles belong to the video and clear with it."""
    listener = init_integration.listener
    await listener.now_playing_changed(_now_playing(VIDEO_ID))
    await listener.autoplay_up_next_changed(AutoplayUpNextEvent({"videoId": NEXT_ID}))
    await listener.subtitles_track_changed(
        SubtitlesTrackEvent({"videoId": VIDEO_ID, "languageName": "English"})
    )
    await listener.now_playing_changed(_now_playing(None, "-1"))
    await hass.async_block_till_done()

    assert hass.states.get(UP_NEXT_ID).state == STATE_UNKNOWN
    # Subtitles are an app setting, kept until the TV reports a change.
    assert hass.states.get(SUBTITLES_ID).state == "English"


async def test_subtitles(hass: HomeAssistant, init_integration: FakeLounge) -> None:
    """Subtitles show the language, "off", and ignore other videos' tracks."""
    listener = init_integration.listener
    await listener.now_playing_changed(_now_playing(VIDEO_ID))
    await listener.subtitles_track_changed(
        SubtitlesTrackEvent(
            {"videoId": VIDEO_ID, "languageCode": "es", "languageName": "Spanish"}
        )
    )
    await hass.async_block_till_done()
    assert hass.states.get(SUBTITLES_ID).state == "Spanish"

    await listener.subtitles_track_changed(
        SubtitlesTrackEvent({"videoId": "otherVideo1", "languageName": "German"})
    )
    await hass.async_block_till_done()
    assert hass.states.get(SUBTITLES_ID).state == "Spanish"

    await listener.subtitles_track_changed(SubtitlesTrackEvent({"videoId": VIDEO_ID}))
    await hass.async_block_till_done()
    assert hass.states.get(SUBTITLES_ID).state == "off"

    # A new video keeps the setting; the TV reports it again if it changes.
    await listener.now_playing_changed(_now_playing(NEXT_ID))
    await hass.async_block_till_done()
    assert hass.states.get(SUBTITLES_ID).state == "off"


async def test_setting_events_do_not_cut_settling(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A speed event during a seek doesn't publish the seek's brief pause."""
    listener = init_integration.listener
    await listener.now_playing_changed(_now_playing(VIDEO_ID))
    await hass.async_block_till_done()
    states: list[str] = []
    hass.bus.async_listen(
        "state_changed",
        lambda event: (
            states.append(event.data["new_state"].state)
            if event.data["entity_id"] == PLAYER_ID
            else None
        ),
    )

    await listener.playback_state_changed(
        PlaybackStateEvent({"state": "2", "currentTime": "50", "duration": "200"})
    )
    await listener.playback_speed_changed(PlaybackSpeedEvent({"playbackSpeed": "1"}))
    await listener.playback_state_changed(
        PlaybackStateEvent({"state": "1", "currentTime": "50", "duration": "200"})
    )
    freezer.tick(timedelta(seconds=SETTLE_DELAY + 0.1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert STATE_PAUSED not in states
    assert hass.states.get(PLAYER_ID).state == STATE_PLAYING
    assert hass.states.get(SPEED_ID).state == "1"


async def test_commands_update_state_immediately(
    hass: HomeAssistant, init_integration: FakeLounge
) -> None:
    """HA shows the new setting at once; the TV confirms it seconds later."""
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: SPEED_ID, ATTR_OPTION: "1_5"},
        blocking=True,
    )
    assert hass.states.get(SPEED_ID).state == "1_5"

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: AUTOPLAY_ID}, blocking=True
    )
    assert hass.states.get(AUTOPLAY_ID).state == STATE_ON

    # The TV's own report still wins.
    await init_integration.listener.autoplay_changed(
        AutoplayModeChangedEvent({"autoplayMode": "DISABLED"})
    )
    await hass.async_block_till_done()
    assert hass.states.get(AUTOPLAY_ID).state == STATE_OFF


async def test_failed_command_does_not_change_state(
    hass: HomeAssistant, init_integration: FakeLounge
) -> None:
    """A rejected command leaves the state alone."""
    init_integration.set_playback_speed.return_value = False
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: SPEED_ID, ATTR_OPTION: "2"},
            blocking=True,
        )
    assert hass.states.get(SPEED_ID).state == STATE_UNKNOWN


async def test_autoplay_unsupported_flicker(
    hass: HomeAssistant, init_integration: FakeLounge, freezer: FrozenDateTimeFactory
) -> None:
    """The TV briefly reports autoplay as unsupported when connecting."""
    listener = init_integration.listener
    await listener.autoplay_changed(
        AutoplayModeChangedEvent({"autoplayMode": "UNSUPPORTED"})
    )
    await listener.autoplay_changed(
        AutoplayModeChangedEvent({"autoplayMode": "ENABLED"})
    )
    await _settle(hass, freezer)
    assert hass.states.get(AUTOPLAY_ID).state == STATE_ON

    # A TV that really doesn't support it still ends up unavailable.
    await listener.autoplay_changed(
        AutoplayModeChangedEvent({"autoplayMode": "UNSUPPORTED"})
    )
    await hass.async_block_till_done()
    assert hass.states.get(AUTOPLAY_ID).state == STATE_ON
    await _settle(hass, freezer)
    assert hass.states.get(AUTOPLAY_ID).state == STATE_UNAVAILABLE
