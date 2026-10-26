"""Tests for the diagnostic sensors, the skip ad button and play_media."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_fire_time_changed_exact,
)
from pyytlounge import AdPlayingEvent, AdStateEvent, NowPlayingEvent, State

from custom_components.youtube_on_tv.const import (
    AD_END_GRACE,
    AD_MAX_AD_SECONDS,
    AD_OVERRUN,
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
    MediaPlayerEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .conftest import APP_URL, SCREEN_ID, FakeLounge, wait_for


def _ad_playing(
    state: str = "-1",
    *,
    skip_enabled: str = "false",
    skippable: str = "true",
    bumper: str = "false",
    duration: str = "15",
) -> AdPlayingEvent:
    """Build the event a TV sends at the start of each ad."""
    return AdPlayingEvent(
        {
            "adState": state,
            "isSkipEnabled": skip_enabled,
            "isSkippable": skippable,
            "isBumper": bumper,
            "adTitle": "",
            "clickThroughUrl": "",
            "adSystem": "",
            "adNextParams": "",
            "contentVideoId": VIDEO_ID,
            "duration": duration,
            "currentTime": "0",
        }
    )


def _ad_state(state: str, skip_enabled: str = "false") -> AdStateEvent:
    """Build an ad state change."""
    return AdStateEvent(
        {"adState": state, "currentTime": "0", "isSkipEnabled": skip_enabled}
    )


def _playing(position: str, duration: str) -> NowPlayingEvent:
    """Build a now playing event for a video that is playing."""
    return NowPlayingEvent(
        {
            "videoId": VIDEO_ID,
            "state": "1",
            "currentTime": position,
            "duration": duration,
        }
    )


async def _wait(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float = AD_END_GRACE
) -> None:
    """Let the wait for another ad of the same break run out."""
    freezer.tick(timedelta(seconds=seconds + 0.1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _gap(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    """Pass the time between two events without any wait running out.

    async_fire_time_changed fires whatever is due within the next half
    second, which is more slack than the gaps being replayed here.
    """
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed_exact(hass)
    await hass.async_block_till_done()


PREFIX = "youtube_on_samsung_neo_qled"
CONNECTED_ID = f"binary_sensor.{PREFIX}_youtube_session"
APP_STATE_ID = f"sensor.{PREFIX}_app_state"
SKIP_AD_ID = f"button.{PREFIX}_skip_ad"
AD_SENSOR_ID = f"binary_sensor.{PREFIX}_ad_playing"
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


async def test_turn_on_and_off(
    hass: HomeAssistant, init_integration: FakeLounge
) -> None:
    """Turning the player on and off opens and closes YouTube over DIAL."""
    state = hass.states.get(PLAYER_ID)
    features = state.attributes["supported_features"]
    assert features & MediaPlayerEntityFeature.TURN_ON
    assert features & MediaPlayerEntityFeature.TURN_OFF

    module = "custom_components.youtube_on_tv.coordinator"
    with patch(f"{module}.async_launch_app") as launch:
        await hass.services.async_call(
            MP_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: PLAYER_ID}, blocking=True
        )
    launch.assert_awaited_once_with(hass, APP_URL, None)

    with (
        patch(f"{module}.async_get_run_url", return_value=f"{APP_URL}/run") as run_url,
        patch(f"{module}.async_stop_app") as stop,
    ):
        await hass.services.async_call(
            MP_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: PLAYER_ID}, blocking=True
        )
    run_url.assert_awaited_once()
    stop.assert_awaited_once_with(hass, f"{APP_URL}/run")


async def test_turn_off_without_running_app(
    hass: HomeAssistant, init_integration: FakeLounge
) -> None:
    """Closing an app the TV doesn't report as running fails clearly."""
    module = "custom_components.youtube_on_tv.coordinator"
    with (
        patch(f"{module}.async_get_run_url", return_value=None),
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(
            MP_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: PLAYER_ID}, blocking=True
        )
    assert err.value.translation_key == "launch_failed"


async def test_play_media_launches_closed_app(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_app_state: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """With YouTube closed, the video is sent with the launch request."""
    mock_app_state.return_value = "stopped"
    freezer.tick(APP_STATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    module = "custom_components.youtube_on_tv.coordinator"
    with patch(f"{module}.async_launch_app") as launch:
        await hass.services.async_call(
            MP_DOMAIN,
            SERVICE_PLAY_MEDIA,
            {
                ATTR_ENTITY_ID: PLAYER_ID,
                ATTR_MEDIA_CONTENT_ID: VIDEO_ID,
                ATTR_MEDIA_CONTENT_TYPE: "video",
            },
            blocking=True,
        )
    launch.assert_awaited_once_with(hass, APP_URL, VIDEO_ID)
    init_integration.play_video.assert_not_awaited()


async def test_no_turn_on_without_dial(
    hass: HomeAssistant,
    mock_lounge: list[FakeLounge],
    mock_app_state: AsyncMock,
) -> None:
    """A TV added with a code has no address to launch the app on."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Kitchen TV",
        unique_id="kitchen2",
        data={CONF_SCREEN_ID: SCREEN_ID},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    features = hass.states.get("media_player.youtube_on_kitchen_tv").attributes[
        "supported_features"
    ]
    assert not features & MediaPlayerEntityFeature.TURN_ON
    assert not features & MediaPlayerEntityFeature.TURN_OFF


@pytest.mark.parametrize("ad_state", ["1", "1081", "3", "0", "99"])
async def test_skip_ad_available_whatever_the_ad_state(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    ad_state: str,
) -> None:
    """TVs label the ad state differently; the skip flag is what counts."""
    coordinator = mock_config_entry.runtime_data
    coordinator.handle_now_playing(
        NowPlayingEvent(
            {"videoId": VIDEO_ID, "state": "1081", "currentTime": "0", "duration": "6"}
        )
    )
    await init_integration.listener.ad_state_changed(
        AdStateEvent({"adState": ad_state, "currentTime": "5", "isSkipEnabled": "true"})
    )
    await hass.async_block_till_done()

    assert hass.states.get(AD_SENSOR_ID).state == STATE_ON
    assert hass.states.get(SKIP_AD_ID).state != STATE_UNAVAILABLE


async def test_skip_ad_unavailable_after_skipping(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A skipped ad takes the button away at once and the sensor shortly after."""
    coordinator = mock_config_entry.runtime_data
    coordinator.handle_now_playing(
        NowPlayingEvent(
            {"videoId": VIDEO_ID, "state": "1081", "currentTime": "0", "duration": "6"}
        )
    )
    coordinator.handle_ad_state(State.Playing, True)
    await hass.async_block_till_done()
    assert hass.states.get(SKIP_AD_ID).state != STATE_UNAVAILABLE

    coordinator.handle_ad_state(State.AdSkipped, False)
    await hass.async_block_till_done()
    assert hass.states.get(SKIP_AD_ID).state == STATE_UNAVAILABLE

    await _wait(hass, freezer)
    assert hass.states.get(AD_SENSOR_ID).state == STATE_OFF


async def test_ad_skipped_in_the_middle_of_a_break(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An ad skipped with another one behind it keeps the sensor on.

    A Samsung QN900F announced the next ad 0.65 seconds after the skip. The
    sensor used to drop in that gap, and an automation muting the TV from it
    ran its unmute instead of muting the ad that had just started.
    """
    coordinator = mock_config_entry.runtime_data
    listener = init_integration.listener
    coordinator.handle_now_playing(_playing("298.269", "721.561"))
    states = _record_ad_sensor(hass)
    await listener.ad_playing_changed(_ad_playing(duration="10.021"))
    coordinator.handle_ad_state(State.Playing, True)
    await hass.async_block_till_done()
    assert hass.states.get(AD_SENSOR_ID).state == STATE_ON

    coordinator.handle_ad_state(State.AdSkipped, False)
    await hass.async_block_till_done()
    await _gap(hass, freezer, 0.65)
    await listener.ad_playing_changed(_ad_playing(duration="18.061"))
    await hass.async_block_till_done()
    await _gap(hass, freezer, 2)
    assert hass.states.get(AD_SENSOR_ID).state == STATE_ON
    assert states == [STATE_ON]

    # The second ad is skipped too, and the video it interrupted comes back.
    coordinator.handle_ad_state(State.AdSkipped, False)
    coordinator.handle_playback_state(State.Starting, 298.269, 721.561)
    await hass.async_block_till_done()
    assert hass.states.get(AD_SENSOR_ID).state == STATE_OFF
    assert states == [STATE_ON, STATE_OFF]


async def test_the_video_coming_back_turns_the_sensor_off_at_once(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A video reported as "starting" again publishes the end of the ad.

    That state is short-lived and otherwise settles before being published,
    which left the sensor on for another second and a half.
    """
    coordinator = mock_config_entry.runtime_data
    coordinator.handle_now_playing(_playing("933.982", "1721.761"))
    await init_integration.listener.ad_playing_changed(_ad_playing(duration="20.021"))
    await hass.async_block_till_done()
    assert hass.states.get(AD_SENSOR_ID).state == STATE_ON

    coordinator.handle_ad_state(State.Buffering, False)
    coordinator.handle_playback_state(State.Starting, 933.982, 1721.761)
    await hass.async_block_till_done()
    assert hass.states.get(AD_SENSOR_ID).state == STATE_OFF


async def test_trailing_ad_event_does_not_flip_sensor_back(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An ad's closing events arrive after playback resumes; one Off is enough."""
    coordinator = mock_config_entry.runtime_data
    coordinator.handle_now_playing(
        NowPlayingEvent(
            {"videoId": VIDEO_ID, "state": "1081", "currentTime": "0", "duration": "6"}
        )
    )
    coordinator.handle_ad_state(State.Playing, True)
    await hass.async_block_till_done()
    assert hass.states.get(AD_SENSOR_ID).state == STATE_ON

    # The video comes back where the ad interrupted it, which ends the ad at
    # once: another ad of the same break would have started from zero.
    coordinator.handle_playback_state(State.Playing, 120, 237)
    await hass.async_block_till_done()
    assert hass.states.get(AD_SENSOR_ID).state == STATE_OFF
    off_at = hass.states.get(AD_SENSOR_ID).last_changed

    # The TV reports the ad finishing just after, which must change nothing.
    coordinator.handle_ad_state(State.Buffering, False)
    coordinator.handle_ad_state(State.Stopped, False)
    await _wait(hass, freezer)
    assert hass.states.get(AD_SENSOR_ID).state == STATE_OFF
    assert hass.states.get(AD_SENSOR_ID).last_changed == off_at
    assert hass.states.get(SKIP_AD_ID).state == STATE_UNAVAILABLE


async def test_ad_pod_from_a_real_tv(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Replay a captured ad break: one On, one Off, nothing in between.

    The TV sends a last "Advertisement" playback state 4 ms after the ad is
    skipped, carrying the video's duration, which used to turn the sensor
    back on for a second. The video then starts from the beginning, which is
    what another ad of the same break would look like, so the end of the
    break is published once the wait for one runs out.
    """
    coordinator = mock_config_entry.runtime_data
    listener = init_integration.listener
    states: list[str] = []

    @callback
    def record(event) -> None:
        """Record state changes, ignoring attribute-only updates."""
        if event.data["entity_id"] != AD_SENSOR_ID:
            return
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        if old_state is None or old_state.state != new_state.state:
            states.append(new_state.state)

    hass.bus.async_listen("state_changed", record)

    def ad_playing(state: str, skippable: str = "false") -> AdPlayingEvent:
        return _ad_playing(state, skip_enabled=skippable)

    def ad_state(state: str, skippable: str = "false") -> AdStateEvent:
        return _ad_state(state, skippable)

    coordinator.handle_now_playing(
        NowPlayingEvent(
            {"videoId": VIDEO_ID, "state": "3", "currentTime": "0", "duration": "0"}
        )
    )
    # A bumper, then a skippable ad.
    await listener.ad_playing_changed(ad_playing("-1"))
    coordinator.handle_playback_state(State.Advertisement, 0.07, 6.041)
    await listener.ad_state_changed(ad_state("1"))
    coordinator.handle_playback_state(State.Advertisement, 0.0, 15.0)
    await listener.ad_state_changed(ad_state("0"))
    await listener.ad_playing_changed(ad_playing("1"))
    await hass.async_block_till_done()
    assert hass.states.get(AD_SENSOR_ID).state == STATE_ON

    # Skip becomes available, then the ad is skipped.
    await listener.ad_state_changed(ad_state("1", "true"))
    await hass.async_block_till_done()
    assert hass.states.get(SKIP_AD_ID).state != STATE_UNAVAILABLE

    await listener.ad_state_changed(ad_state("1082"))
    # Four milliseconds later the TV sends one more "Advertisement" state,
    # carrying the video's duration, followed by the ad buffering away.
    coordinator.handle_playback_state(State.Advertisement, 0.0, 225.0)
    await listener.ad_state_changed(ad_state("0"))
    coordinator.handle_playback_state(State.Stopped, 0.0, 224.861)
    coordinator.handle_playback_state(State.Playing, 0.091, 224.861)
    await hass.async_block_till_done()
    assert hass.states.get(SKIP_AD_ID).state == STATE_UNAVAILABLE

    await _wait(hass, freezer)
    assert hass.states.get(AD_SENSOR_ID).state == STATE_OFF
    assert hass.states.get(SKIP_AD_ID).state == STATE_UNAVAILABLE
    assert states == [STATE_ON, STATE_OFF]


async def test_an_ad_is_on_screen_when_the_tv_announces_it(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The announcement means an ad, whatever ad state it carries.

    A Samsung QN900F sends "stopped" (-1) with it at the start of every single
    ad, and a running state only sometimes, and late.
    """
    coordinator = mock_config_entry.runtime_data
    coordinator.handle_now_playing(_playing("347", "873.601"))
    await hass.async_block_till_done()
    assert hass.states.get(AD_SENSOR_ID).state == STATE_OFF

    await init_integration.listener.ad_playing_changed(_ad_playing("-1"))
    await hass.async_block_till_done()
    assert hass.states.get(AD_SENSOR_ID).state == STATE_ON


async def test_ad_break_of_five_from_a_samsung(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Replay a captured break of five ads: one On, one Off, nothing between.

    Each ad is announced as "playing" with its own duration, then as an ad
    with no running state, then labelled as an advertisement; the end of each
    arrives a fraction of a second before the next is announced. Reading those
    as the video coming back turned the sensor off for 34 seconds in the
    middle of this break.
    """
    coordinator = mock_config_entry.runtime_data
    listener = init_integration.listener
    states = _record_ad_sensor(hass)

    coordinator.handle_now_playing(_playing("347", "873.601"))
    await hass.async_block_till_done()

    # The first of the five.
    coordinator.handle_playback_state(State.Playing, 0.0, 15.021)
    await listener.ad_playing_changed(_ad_playing(duration="15.021"))
    coordinator.handle_playback_state(State.Advertisement, 0.109, 15.021)
    await hass.async_block_till_done()
    assert hass.states.get(AD_SENSOR_ID).state == STATE_ON

    # Three more, none of them skippable: two bumpers and a twenty second ad.
    for duration in ("20.061", "6.061", "6.041"):
        await listener.ad_state_changed(_ad_state("0"))
        coordinator.handle_playback_state(State.Playing, 0.0, float(duration))
        await listener.ad_playing_changed(_ad_playing(duration=duration))
        coordinator.handle_playback_state(State.Advertisement, 0.05, float(duration))
        await hass.async_block_till_done()
        assert hass.states.get(AD_SENSOR_ID).state == STATE_ON

    # The last one can be skipped five seconds in, and is.
    await listener.ad_state_changed(_ad_state("0"))
    coordinator.handle_playback_state(State.Playing, 0.0, 15.041)
    await listener.ad_playing_changed(_ad_playing(duration="15.041"))
    coordinator.handle_playback_state(State.Advertisement, 0.084, 15.041)
    await listener.ad_state_changed(_ad_state("1", "true"))
    await hass.async_block_till_done()
    assert hass.states.get(SKIP_AD_ID).state != STATE_UNAVAILABLE

    await listener.ad_state_changed(_ad_state("1082"))
    # One more "Advertisement" state follows, carrying the video's own
    # position and duration, and then the video is back.
    coordinator.handle_playback_state(State.Advertisement, 348.268, 873.601)
    coordinator.handle_playback_state(State.Starting, 348.268, 873.601)
    coordinator.handle_playback_state(State.Playing, 348.363, 873.601)
    await _wait(hass, freezer)

    assert hass.states.get(AD_SENSOR_ID).state == STATE_OFF
    assert hass.states.get(SKIP_AD_ID).state == STATE_UNAVAILABLE
    assert states == [STATE_ON, STATE_OFF]


async def test_ad_break_the_tv_never_reports_as_running(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Replay a break of two unskippable ads with no running ad state at all.

    Captured from a Samsung QN900F, where this break went undetected from
    start to finish.
    """
    coordinator = mock_config_entry.runtime_data
    listener = init_integration.listener
    states = _record_ad_sensor(hass)

    coordinator.handle_now_playing(_playing("1189", "1206.161"))
    await hass.async_block_till_done()

    coordinator.handle_playback_state(State.Playing, 0.0, 15.861)
    await listener.ad_playing_changed(_ad_playing(duration="15.861"))
    coordinator.handle_playback_state(State.Advertisement, 0.078, 15.861)
    await hass.async_block_till_done()
    assert hass.states.get(AD_SENSOR_ID).state == STATE_ON

    await listener.ad_state_changed(_ad_state("0"))
    await listener.ad_playing_changed(_ad_playing(duration="15.021"))
    coordinator.handle_playback_state(State.Advertisement, 0.028, 15.021)
    await hass.async_block_till_done()
    assert hass.states.get(AD_SENSOR_ID).state == STATE_ON

    # The video had reached its end, so nothing comes back after the break.
    coordinator.handle_playback_state(State.Advertisement, 1206.161, 1206.161)
    await listener.ad_state_changed(_ad_state("0"))
    await _wait(hass, freezer)

    assert hass.states.get(AD_SENSOR_ID).state == STATE_OFF
    assert states == [STATE_ON, STATE_OFF]


async def test_an_ad_the_tv_never_ends_clears_itself(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An ad with no end reported is over once its own duration has passed.

    Otherwise the sensor would stay on for good, and an automation following
    it would leave the TV muted.
    """
    coordinator = mock_config_entry.runtime_data
    coordinator.handle_now_playing(_playing("347", "873.601"))
    await init_integration.listener.ad_playing_changed(_ad_playing(duration="15"))
    await hass.async_block_till_done()
    assert hass.states.get(AD_SENSOR_ID).state == STATE_ON

    await _wait(hass, freezer, AD_END_GRACE)
    assert hass.states.get(AD_SENSOR_ID).state == STATE_ON

    await _wait(hass, freezer, 15 + AD_OVERRUN)
    assert hass.states.get(AD_SENSOR_ID).state == STATE_OFF


async def test_an_ad_of_unknown_length_clears_itself(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An ad reported as running, and never ended, is bounded as well.

    Such a TV never announces the ad, so its length isn't known and the
    longest an ad is given stands in for it.
    """
    coordinator = mock_config_entry.runtime_data
    coordinator.handle_now_playing(_playing("0", "873.601"))
    coordinator.handle_ad_state(State.Playing, False)
    await hass.async_block_till_done()
    assert hass.states.get(AD_SENSOR_ID).state == STATE_ON

    await _wait(hass, freezer, AD_MAX_AD_SECONDS)
    assert hass.states.get(AD_SENSOR_ID).state == STATE_OFF


@callback
def _record_ad_sensor(hass: HomeAssistant) -> list[str]:
    """Record the ad sensor's state changes, ignoring attribute updates."""
    states: list[str] = []

    @callback
    def record(event) -> None:
        if event.data["entity_id"] != AD_SENSOR_ID:
            return
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        if old_state is None or old_state.state != new_state.state:
            states.append(new_state.state)

    hass.bus.async_listen("state_changed", record)
    return states


async def test_ad_state_only_in_playback_state(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A TV that never sends ad events still reports ads."""
    coordinator = mock_config_entry.runtime_data
    coordinator.handle_now_playing(
        NowPlayingEvent(
            {"videoId": VIDEO_ID, "state": "1", "currentTime": "5", "duration": "200"}
        )
    )
    coordinator.handle_playback_state(State.Advertisement, 0.0, 15.0)
    await hass.async_block_till_done()
    assert hass.states.get(AD_SENSOR_ID).state == STATE_ON

    coordinator.handle_playback_state(State.Playing, 0.0, 200)
    await hass.async_block_till_done()
    assert hass.states.get(AD_SENSOR_ID).state == STATE_OFF
