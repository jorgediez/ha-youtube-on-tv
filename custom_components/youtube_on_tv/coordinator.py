"""Lounge session and playback state for YouTube on TV."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import aiohttp
from pyytlounge import (
    AdPlayingEvent,
    AdStateEvent,
    AutoplayModeChangedEvent,
    AutoplayUpNextEvent,
    DisconnectedEvent,
    EventListener,
    NowPlayingEvent,
    PlaybackSpeedEvent,
    PlaybackStateEvent,
    State,
    SubtitlesTrackEvent,
    YtLoungeApi,
)
from pyytlounge.exceptions import NotConnectedException, NotLinkedException
import voluptuous as vol

from homeassistant.const import CONF_HOST
from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.script import Script, async_validate_actions_config
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    APP_STATE_INTERVAL,
    CONF_APP_URL,
    CONF_OPEN_ACTIONS,
    CONF_SCREEN_ID,
    CONF_SESSION_ENABLED,
    DOMAIN,
    LOGGER,
    LOUNGE_DEVICE_NAME,
    OPEN_TIMEOUT,
    OUTAGE_WARNING_DELAY,
    POSITION_OVERRUN,
    POSITION_TOLERANCE,
    RECONNECT_MAX_DELAY,
    RECONNECT_MIN_DELAY,
    SETTLE_DELAY,
    STALE_CHECK_INTERVAL,
    STALE_REPLY_TIMEOUT,
)
from .dial import (
    DialError,
    async_get_app_state,
    async_get_run_url,
    async_launch_app,
    async_stop_app,
)

if TYPE_CHECKING:
    from . import YouTubeOnTvConfigEntry

OEMBED_URL = "https://www.youtube.com/oembed"
THUMBNAIL_URL = "https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
MAX_TITLE_CACHE = 200
# oEmbed requests at once; a long playlist would otherwise send one per video.
MAX_TITLE_FETCHES = 4
SUBTITLES_OFF = "off"

# Errors from pyytlounge when YouTube doesn't return a lounge token for the
# screen id, i.e. the TV revoked or rotated it.
_AUTH_ERRORS = (KeyError, IndexError, TypeError, ValueError)
_CONNECTION_ERRORS = (aiohttp.ClientError, TimeoutError)
# Consecutive link failures in the background before asking to re-pair, so
# a transient bad reply from YouTube doesn't trigger a reauth.
MAX_AUTH_FAILURES = 3
# A subscribe call shorter than this is followed by a pause, to avoid a
# tight loop if the server keeps ending requests immediately.
MIN_SUBSCRIBE_SECONDS = 1.0
# Ad states that mean an ad is on screen. Other labels arrive as an ad ends.
_AD_RUNNING_STATES = (State.Playing, State.Advertisement, State.Starting)
# While waiting for the app to come online after the open actions, the TV is
# asked what's playing this often: a screen that just came online answers.
OPEN_POLL_SECONDS = 2.0

# After a queue change from Home Assistant, the TV keeps reporting the old
# queue for a moment; reports that don't match the change are ignored for up
# to this many seconds, so the list doesn't jump back and forth.
QUEUE_SETTLE_SECONDS = 5.0
# Seconds to wait for YouTube to start on the TV after opening it.
LAUNCH_TIMEOUT = 20


class PlayerStatus(StrEnum):
    """Simplified state of the YouTube app."""

    OFF = "off"
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    BUFFERING = "buffering"


@dataclass(frozen=True, slots=True)
class TvState:
    """Published playback state."""

    status: PlayerStatus = PlayerStatus.OFF
    video_id: str | None = None
    title: str | None = None
    channel: str | None = None
    duration: float | None = None
    position: float | None = None
    position_updated_at: datetime | None = None
    ad_playing: bool = False
    ad_skippable: bool = False
    # App settings, kept across videos and when playback stops.
    autoplay: bool | None = None
    autoplay_supported: bool = True
    playback_speed: float | None = None
    subtitles: str | None = None
    subtitles_code: str | None = None
    subtitles_track: str | None = None
    subtitles_kind: str | None = None
    subtitles_style: dict[str, Any] | None = None
    # Last language actually used, kept while subtitles are off.
    last_subtitles_code: str | None = None
    last_subtitles_name: str | None = None
    # Tied to the current video, cleared when playback stops.
    video_quality: str | None = None
    video_quality_levels: tuple[int, ...] | None = None
    up_next_video_id: str | None = None
    up_next_title: str | None = None
    # The TV's play queue, in order, and the index of the playing video in it.
    # Kept while idle, cleared when the app closes.
    queue: tuple[str, ...] = ()
    queue_index: int | None = None

    @property
    def thumbnail_url(self) -> str | None:
        """Return the video thumbnail URL."""
        if self.video_id is None:
            return None
        return THUMBNAIL_URL.format(video_id=self.video_id)

    @property
    def up_next_thumbnail_url(self) -> str | None:
        """Return the thumbnail URL of the next video."""
        if self.up_next_video_id is None:
            return None
        return THUMBNAIL_URL.format(video_id=self.up_next_video_id)


class _LoungeApi(YtLoungeApi):
    """Adds what pyytlounge doesn't cover: video quality and the play queue.

    The TV reports the resolution through "onVideoQualityChanged" and the
    queue in "nowPlaying"; the library ignores both, so they are picked up
    here. The queue commands are the ones the YouTube phone app sends.
    """

    def __init__(self, coordinator: YouTubeOnTvCoordinator, *args: Any) -> None:
        super().__init__(*args)
        self._coordinator = coordinator

    async def _process_event(self, event_type: str, args: list[Any]) -> None:
        if event_type == "onVideoQualityChanged" and args:
            self._coordinator.handle_video_quality(args[0])
        await super()._process_event(event_type, args)
        # After the now playing event, so the queue index uses its video.
        if event_type == "nowPlaying" and args and isinstance(args[0], dict):
            self._coordinator.handle_queue(args[0])

    async def add_video(self, video_id: str) -> bool:
        """Append a video to the end of the queue."""
        return await self._command("addVideo", {"videoId": video_id})

    async def insert_video(self, video_id: str) -> bool:
        """Queue a video to play after the current one."""
        return await self._command("insertVideo", {"videoId": video_id})

    async def set_playlist(
        self,
        video_ids: list[str],
        index: int,
        current_time: float = 0,
        list_id: str = "",
    ) -> bool:
        """Replace the queue, playing video_ids[index] from current_time."""
        return await self._command(
            "setPlaylist",
            {
                "videoIds": ",".join(video_ids),
                "videoId": video_ids[index],
                "currentIndex": index,
                "currentTime": current_time,
                "listId": list_id,
            },
        )


class _Listener(EventListener):
    """Forwards pyytlounge events to the coordinator."""

    def __init__(self, coordinator: YouTubeOnTvCoordinator) -> None:
        super().__init__()
        self._coordinator = coordinator

    async def now_playing_changed(self, event: NowPlayingEvent) -> None:
        LOGGER.debug(
            "nowPlaying: video=%s state=%s position=%s duration=%s",
            event.video_id,
            event.state.name,
            event.current_time,
            event.duration,
        )
        self._coordinator.handle_now_playing(event)

    async def playback_state_changed(self, event: PlaybackStateEvent) -> None:
        LOGGER.debug(
            "onStateChange: state=%s position=%s duration=%s",
            event.state.name,
            event.current_time,
            event.duration,
        )
        self._coordinator.handle_playback_state(
            event.state, event.current_time, event.duration
        )

    async def ad_state_changed(self, event: AdStateEvent) -> None:
        LOGGER.debug(
            "onAdStateChange: state=%s skip_enabled=%s position=%s",
            event.ad_state.name,
            event.is_skip_enabled,
            event.current_time,
        )
        self._coordinator.handle_ad_state(event.ad_state, event.is_skip_enabled)

    async def ad_playing_changed(self, event: AdPlayingEvent) -> None:
        LOGGER.debug(
            "adPlaying: state=%s skip_enabled=%s skippable=%s bumper=%s",
            event.ad_state.name,
            event.is_skip_enabled,
            event.is_skippable,
            event.is_bumper,
        )
        self._coordinator.handle_ad_state(event.ad_state, event.is_skip_enabled)

    async def disconnected(self, event: DisconnectedEvent) -> None:
        self._coordinator.handle_disconnected()

    async def autoplay_changed(self, event: AutoplayModeChangedEvent) -> None:
        self._coordinator.handle_autoplay(event.enabled, event.supported)

    async def autoplay_up_next_changed(self, event: AutoplayUpNextEvent) -> None:
        self._coordinator.handle_up_next(event.video_id)

    async def subtitles_track_changed(self, event: SubtitlesTrackEvent) -> None:
        self._coordinator.handle_subtitles(event)

    async def playback_speed_changed(self, event: PlaybackSpeedEvent) -> None:
        self._coordinator.handle_playback_speed(event.playback_speed)


class YouTubeOnTvCoordinator(DataUpdateCoordinator[TvState]):
    """Keeps a Lounge session open and publishes the TV's playback state.

    Updates are pushed by the TV; there is no polling of YouTube.
    """

    config_entry: YouTubeOnTvConfigEntry

    def __init__(self, hass: HomeAssistant, entry: YouTubeOnTvConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass, LOGGER, config_entry=entry, name=DOMAIN, always_update=False
        )
        self.data = TvState()
        self.api = _LoungeApi(
            self, LOUNGE_DEVICE_NAME, _Listener(self), logging.getLogger("pyytlounge")
        )
        self._screen_id: str = entry.data[CONF_SCREEN_ID]
        self._app_url: str | None = entry.data.get(CONF_APP_URL)
        # Working state; published to self.data immediately or after settling.
        self._state = TvState()
        self._app_running: bool | None = None
        # Some TVs may only label ads in the playback state; that is trusted
        # until one sends a real ad event.
        self._seen_ad_event = False
        # When the session went down, and whether that was reported.
        self._offline_since: datetime | None = None
        self._offline_warned = False
        # Last YouTube app state reported by DIAL; None if the TV didn't answer.
        self.app_state: str | None = None
        self._unsub_settle: CALLBACK_TYPE | None = None
        self._task: asyncio.Task[None] | None = None
        self._titles: dict[str, tuple[str | None, str | None]] = {}
        # Titles being fetched, so a repeated queue report doesn't fetch again.
        self._titles_pending: set[str] = set()
        self._title_slots = asyncio.Semaphore(MAX_TITLE_FETCHES)
        # When the TV last sent a playback event, and when it was last asked
        # what's playing by the staleness check.
        self._last_event_at: datetime | None = None
        self._stale_asked_at: datetime | None = None
        self._unsub_stale_reply: CALLBACK_TYPE | None = None
        # Id of the TV's queue, and a queue change sent but not yet reported.
        self._list_id: str = ""
        self._pending_queue: tuple[tuple[str, ...], float] | None = None
        # Set to cut a reconnect wait short, after the app was opened.
        self._reconnect_now = asyncio.Event()

    @property
    def app_running(self) -> bool | None:
        """Return whether YouTube runs on the TV, None if not known."""
        return self._app_running

    @property
    def has_app_state(self) -> bool:
        """Return True if the TV's DIAL endpoint is known and polled."""
        return self._app_url is not None

    @property
    def open_actions(self) -> list[dict[str, Any]]:
        """Return the actions that open YouTube on a TV without DIAL."""
        return self.config_entry.options.get(CONF_OPEN_ACTIONS) or []

    @property
    def working_state(self) -> TvState:
        """Return the latest state, including changes not yet published."""
        return self._state

    @property
    def host(self) -> str | None:
        """Return the TV's host, if known."""
        return self.config_entry.data.get(CONF_HOST)

    @property
    def available(self) -> bool:
        """Return whether the entities have live data.

        Publishing state marks the coordinator successful, so a disabled
        session is tracked separately rather than through that flag.
        """
        return self.session_enabled and self.last_update_success

    @property
    def session_enabled(self) -> bool:
        """Return whether the TV should see Home Assistant as connected.

        A connected remote stops the TV playing Shorts, so this can be turned
        off without removing the integration.
        """
        return self.config_entry.options.get(CONF_SESSION_ENABLED, True)

    async def async_start(self) -> None:
        """Link to the screen and start listening in the background.

        The session is closed by async_shutdown, which Home Assistant runs when
        the entry unloads or its setup fails.
        """
        await self.api.__aenter__()
        entry = self.config_entry
        if self.session_enabled:
            try:
                await self._async_link()
            except _CONNECTION_ERRORS as err:
                raise ConfigEntryNotReady(
                    translation_domain=DOMAIN,
                    translation_key="cannot_connect",
                    translation_placeholders={"error": str(err)},
                ) from err
            self._start_listening()
        else:
            self._set_available(False)

        entry.async_on_unload(
            async_track_time_interval(
                self.hass, self._async_check_stale, STALE_CHECK_INTERVAL
            )
        )
        if self._app_url:
            entry.async_on_unload(
                async_track_time_interval(
                    self.hass, self._async_check_app_state, APP_STATE_INTERVAL
                )
            )
            await self._async_check_app_state()

    @callback
    def _start_listening(self) -> None:
        """Run the session in the background until it is stopped."""
        entry = self.config_entry
        self._task = entry.async_create_background_task(
            self.hass, self._async_run(), f"{DOMAIN} lounge {entry.entry_id}"
        )

    async def _async_stop_listening(self) -> None:
        """End the session, so the TV no longer sees a connected remote."""
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self.api.session is None or self.api.session.closed:
            return
        if self.api.connected():
            try:
                async with asyncio.timeout(5):
                    await self.api.disconnect()
            except (*_CONNECTION_ERRORS, NotConnectedException) as err:
                LOGGER.debug("Error disconnecting: %s", err)

    async def async_set_session_enabled(self, enabled: bool) -> None:
        """Connect to or disconnect from the TV, and remember the choice."""
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options={**self.config_entry.options, CONF_SESSION_ENABLED: enabled},
        )
        if enabled:
            self._start_listening()
            return

        await self._async_stop_listening()
        self._cancel_settle()
        self._state = TvState()
        self.async_set_updated_data(self._state)
        self._set_available(False)

    async def async_shutdown(self) -> None:
        """Disconnect from the screen."""
        await super().async_shutdown()
        self._cancel_settle()
        if self._unsub_stale_reply is not None:
            self._unsub_stale_reply()
            self._unsub_stale_reply = None
        await self._async_stop_listening()
        if self.api.session is not None and not self.api.session.closed:
            await self.api.close()

    # Connection handling

    async def _async_link(self) -> None:
        """Get a lounge token for the stored screen id."""
        try:
            linked = await self.api.pair_with_screen_id(
                self._screen_id, self.config_entry.title
            )
        except _AUTH_ERRORS as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="screen_revoked"
            ) from err
        if not linked:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="screen_revoked"
            )

    async def _async_run(self) -> None:
        """Keep the session connected and consume events until unloaded."""
        delay = RECONNECT_MIN_DELAY
        auth_failures = 0
        while True:
            try:
                await self._async_connect_and_subscribe()
            except ConfigEntryAuthFailed:
                auth_failures += 1
                self._set_available(False)
                if auth_failures < MAX_AUTH_FAILURES:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, RECONNECT_MAX_DELAY)
                    continue
                LOGGER.warning(
                    "YouTube no longer accepts the screen id of %s; re-pairing needed",
                    self.config_entry.title,
                )
                self._set_available(False)
                self.config_entry.async_start_reauth(self.hass)
                return
            except (
                *_CONNECTION_ERRORS,
                NotConnectedException,
                NotLinkedException,
            ) as err:
                auth_failures = 0
                LOGGER.debug("Lounge connection error: %s", err)
                self._set_available(False)
                self._warn_if_offline_for_long(err)
                await self._async_reconnect_wait(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY)
            else:
                delay = RECONNECT_MIN_DELAY
                auth_failures = 0

    async def _async_reconnect_wait(self, delay: float) -> None:
        """Wait before reconnecting, or less if the app was just opened."""
        self._reconnect_now.clear()
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(delay):
                await self._reconnect_now.wait()

    async def _async_connect_and_subscribe(self) -> None:
        if not self.api.linked():
            await self._async_link()
        if not self.api.connected():
            if not await self.api.connect():
                if self.api.linked():
                    raise NotConnectedException("Lounge refused the connection")
                return  # token expired; relink on next iteration
            LOGGER.debug("Connected to %s", self.config_entry.title)
            await self.api.get_now_playing()

        # Also covers resuming a session that was turned off and on again,
        # where the client is still connected and no connect is needed.
        self._set_available(True)
        started = time.monotonic()
        await self.api.subscribe()
        if time.monotonic() - started < MIN_SUBSCRIBE_SECONDS:
            # Guard against a tight loop if the server ends requests at once.
            await asyncio.sleep(MIN_SUBSCRIBE_SECONDS)

    @callback
    def _set_available(self, available: bool) -> None:
        if available:
            if self._offline_warned:
                LOGGER.warning(
                    "Reconnected to %s after %s",
                    self.config_entry.title,
                    dt_util.utcnow() - (self._offline_since or dt_util.utcnow()),
                )
            self._offline_since = None
            self._offline_warned = False
        elif self._offline_since is None:
            self._offline_since = dt_util.utcnow()
        if self.last_update_success == available:
            return
        self.last_update_success = available
        self.async_update_listeners()

    @callback
    def _warn_if_offline_for_long(self, error: Exception) -> None:
        """Report a session that has stayed down, once."""
        if self._offline_warned or self._offline_since is None:
            return
        if dt_util.utcnow() - self._offline_since < OUTAGE_WARNING_DELAY:
            return
        self._offline_warned = True
        LOGGER.warning(
            "Not connected to %s since %s, still retrying: %s",
            self.config_entry.title,
            self._offline_since.isoformat(timespec="seconds"),
            error or type(error).__name__,
        )

    async def _async_check_app_state(self, _now: datetime | None = None) -> None:
        """Poll DIAL to detect the YouTube app closing or the TV turning off."""
        assert self._app_url is not None
        app_state = await async_get_app_state(self.hass, self._app_url)
        if app_state != self.app_state:
            self.app_state = app_state
            self.async_update_listeners()
        running = app_state == "running"
        if running == self._app_running:
            return
        self._app_running = running
        if not running:
            self._update(self._cleared(PlayerStatus.OFF), immediate=True)
        elif self.api.connected():
            try:
                await self.api.get_now_playing()
            except (*_CONNECTION_ERRORS, NotConnectedException) as err:
                LOGGER.debug("Error requesting now playing: %s", err)
        elif self._state.status is PlayerStatus.OFF:
            self._update(replace(self._state, status=PlayerStatus.IDLE), immediate=True)

    # Commands

    async def async_launch(self, video_id: str | None = None) -> None:
        """Open YouTube on the TV, optionally playing a video."""
        assert self._app_url is not None
        try:
            await async_launch_app(self.hass, self._app_url, video_id)
        except DialError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="launch_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await self._async_check_app_state()

    async def async_stop(self) -> None:
        """Close YouTube on the TV."""
        assert self._app_url is not None
        run_url = await async_get_run_url(self.hass, self._app_url)
        if run_url is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="launch_failed",
                translation_placeholders={
                    "error": "the TV didn't report a running app"
                },
            )
        try:
            await async_stop_app(self.hass, run_url)
        except DialError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="stop_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await self._async_check_app_state()

    async def async_open_if_closed(self) -> None:
        """Open YouTube on a TV without DIAL if its Lounge screen is offline.

        Without DIAL the app state is not known; a TV whose Lounge session
        ended (the app closed or went to the background) shows as off. Does
        nothing when no open actions are set: the command is sent anyway.
        """
        if self.has_app_state or not self.open_actions:
            return
        if self._state.status is not PlayerStatus.OFF and self.api.connected():
            return
        await self.async_open()

    async def async_open(self) -> None:
        """Run the open actions, then wait for the app to come online.

        Online means the Lounge session is back and the TV answered what's
        playing (the player is no longer off).
        """
        try:
            sequence = await async_validate_actions_config(
                self.hass, cv.SCRIPT_SCHEMA(self.open_actions)
            )
            script = Script(
                self.hass,
                sequence,
                f"{self.config_entry.title} open",
                DOMAIN,
                running_description="open YouTube",
            )
            await script.async_run(context=Context())
        except (vol.Invalid, HomeAssistantError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="launch_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        # The session waits out its backoff while the app is closed; retry now.
        self._reconnect_now.set()
        try:
            async with asyncio.timeout(OPEN_TIMEOUT):
                while (
                    self._state.status is PlayerStatus.OFF or not self.api.connected()
                ):
                    if self.api.connected():
                        with contextlib.suppress(
                            *_CONNECTION_ERRORS, NotConnectedException
                        ):
                            await self.api.get_now_playing()
                    await asyncio.sleep(OPEN_POLL_SECONDS)
        except TimeoutError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="open_timeout",
                translation_placeholders={"seconds": str(OPEN_TIMEOUT)},
            ) from err

    async def async_command(
        self, command: Callable[..., Awaitable[bool]], *args: Any
    ) -> None:
        """Send a command to the TV."""
        try:
            ok = await command(*args)
        except (*_CONNECTION_ERRORS, NotConnectedException) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err) or type(err).__name__},
            ) from err
        if not ok:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": "not connected"},
            )

    # Queue

    def title(self, video_id: str) -> tuple[str | None, str | None]:
        """Return the known title and channel of a video."""
        return self._titles.get(video_id, (None, None))

    async def async_queue_add(self, video_id: str) -> None:
        """Append a video to the queue, or play it if nothing plays."""
        state = self._state
        if state.video_id is None:
            await self.async_command(self.api.play_video, video_id)
            return
        await self.async_command(self.api.add_video, video_id)
        if not state.queue or state.queue_index is None:
            await self._async_request_queue()
            return
        self._note_queue((*state.queue, video_id), state.queue_index)

    async def async_queue_next(self, video_id: str) -> None:
        """Queue a video after the playing one, or play it if nothing plays.

        A video can play with no queue known here: started on the TV itself,
        or playing on after the session dropped and came back. The TV still
        keeps its own queue, so the video is inserted there; playing it
        would replace the video on screen.
        """
        state = self._state
        if state.video_id is None:
            await self.async_command(self.api.play_video, video_id)
            return
        await self.async_command(self.api.insert_video, video_id)
        if not state.queue or state.queue_index is None:
            await self._async_request_queue()
            return
        queue = list(state.queue)
        queue.insert(state.queue_index + 1, video_id)
        self._note_queue(tuple(queue), state.queue_index)

    async def async_queue_replace(self, video_ids: list[str]) -> None:
        """Replace the whole queue and play its first video."""
        await self.async_play_list(video_ids)

    async def async_play_list(
        self,
        video_ids: list[str],
        index: int = 0,
        position: float = 0,
        list_id: str = "",
    ) -> None:
        """Replace the queue and play video_ids[index] from position.

        With a Mix's list id the TV carries on with the Mix after the list,
        as it does when the phone app casts one.
        """
        await self.async_ensure_running(video_ids[index])
        await self.async_command(
            self.api.set_playlist, video_ids, index, round(position, 1), list_id
        )
        self._note_queue(tuple(video_ids), index)

    async def async_ensure_running(self, video_id: str) -> None:
        """Open YouTube on the TV if it's closed, so a command reaches it.

        A command sent while the app is closed is accepted by YouTube's
        servers and never reaches the TV. A TV added with a code opens through
        its open actions, if it has any; otherwise the command is sent anyway.
        """
        if not self.has_app_state:
            await self.async_open_if_closed()
            return
        if self._app_running is not False:
            return
        await self.async_launch(video_id)
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(LAUNCH_TIMEOUT):
                while self._state.video_id is None:
                    await asyncio.sleep(0.5)

    async def async_queue_set(self, queue: list[str], index: int) -> None:
        """Reorder or trim the queue without interrupting the playing video.

        queue[index] must be the playing video. The TV reloads the queue and
        carries on from the current position; tested without a visible gap.
        """
        state = self._state
        if state.video_id is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="no_video"
            )
        if not 0 <= index < len(queue) or queue[index] != state.video_id:
            # The TV moved on since the caller read the queue.
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="queue_changed"
            )
        await self.async_command(
            self.api.set_playlist,
            queue,
            index,
            round(current_position(state), 1),
            # A list id from an older queue would not match this one.
            self._list_id if state.queue_index is not None else "",
        )
        self._note_queue(tuple(queue), index)

    async def _async_request_queue(self) -> None:
        """Ask the TV what's playing, which reports its queue too."""
        with contextlib.suppress(*_CONNECTION_ERRORS, NotConnectedException):
            await self.api.get_now_playing()

    @callback
    def _note_queue(self, queue: tuple[str, ...], index: int) -> None:
        """Publish a queue change sent to the TV before the TV reports it."""
        self._pending_queue = (queue, time.monotonic() + QUEUE_SETTLE_SECONDS)
        self._set_queue(queue, index)

    @callback
    def handle_queue(self, data: dict[str, Any]) -> None:
        """Handle the queue the TV reports along with what's playing."""
        if list_id := data.get("listId"):
            self._list_id = list_id
        raw = data.get("mdxExpandedReceiverVideoIdList")
        if raw is None:
            return
        queue = tuple(video_id for video_id in raw.split(",") if video_id)
        if self._pending_queue is not None:
            expected, deadline = self._pending_queue
            if queue != expected and time.monotonic() < deadline:
                return
            self._pending_queue = None
        self._set_queue(queue, _queue_index(queue, self._state))

    @callback
    def _set_queue(self, queue: tuple[str, ...], index: int | None) -> None:
        state = self._state
        if (queue, index) == (state.queue, state.queue_index):
            return
        self._update_setting(replace(state, queue=queue, queue_index=index))
        # Nearest to the playing video first: those are shown and played
        # soonest, and the fetches run a few at a time.
        start = index or 0
        order = sorted(range(len(queue)), key=lambda i: abs(i - start))
        for video_id in dict.fromkeys(queue[i] for i in order):
            if video_id not in self._titles:
                self._async_request_title(video_id)

    # Event handling

    @callback
    def handle_now_playing(self, event: NowPlayingEvent) -> None:
        """Handle a now playing event."""
        self._last_event_at = dt_util.utcnow()
        if not event.video_id:
            self._update(self._idle_state(), immediate=True)
            return
        self._app_running = True
        if event.video_id != self._state.video_id:
            title, channel = self._titles.get(event.video_id, (None, None))
            up_next = self._state.up_next_video_id
            self._state = replace(
                self._state,
                video_id=event.video_id,
                title=title,
                channel=channel,
                duration=None,
                position=None,
                position_updated_at=None,
                # The queued video is now playing; the TV announces the next.
                up_next_video_id=None if up_next == event.video_id else up_next,
                up_next_title=(
                    None if up_next == event.video_id else self._state.up_next_title
                ),
            )
            if self._state.queue:
                self._state = replace(
                    self._state,
                    queue_index=_queue_index(self._state.queue, self._state),
                )
            if event.video_id not in self._titles:
                self._async_request_title(event.video_id)
        self.handle_playback_state(event.state, event.current_time, event.duration)

    @callback
    def handle_playback_state(
        self, state: State, current_time: float | None, duration: float | None
    ) -> None:
        """Handle a playback state change."""
        self._last_event_at = dt_util.utcnow()
        current = self._state
        if state is State.Advertisement:
            # Position and duration refer to the ad, not the video. Whether an
            # ad is on screen comes from the ad events: a TV sends one last
            # "Advertisement" state just after an ad is skipped, which would
            # otherwise turn the ad sensor back on for a second.
            self._update(
                replace(
                    current,
                    status=PlayerStatus.PLAYING,
                    ad_playing=current.ad_playing or not self._seen_ad_event,
                    position=None,
                    duration=None,
                    position_updated_at=None,
                ),
                immediate=True,
            )
            return
        if current.video_id is None:
            return
        if state in (State.Playing, State.Paused):
            new = replace(
                current,
                status=(
                    PlayerStatus.PLAYING
                    if state is State.Playing
                    else PlayerStatus.PAUSED
                ),
                position=current_time,
                position_updated_at=dt_util.utcnow(),
                duration=duration or current.duration,
                ad_playing=False,
                ad_skippable=False,
            )
            # A seek shows up as a short pause, so let pauses settle.
            self._update(new, immediate=state is State.Playing)
            return
        # Stopped, Starting, Buffering, Cued, AdSkipped: short-lived states seen
        # while loading, seeking and between ads and content.
        new = replace(current, status=PlayerStatus.BUFFERING)
        if state is State.AdSkipped:
            new = replace(new, ad_playing=False, ad_skippable=False)
        self._update(new, immediate=False)

    @callback
    def handle_ad_state(self, ad_state: State, skip_enabled: bool) -> None:
        """Handle an ad state change."""
        self._last_event_at = dt_util.utcnow()
        self._seen_ad_event = True
        if ad_state is State.AdSkipped:
            new = replace(self._state, ad_playing=False, ad_skippable=False)
        elif ad_state in _AD_RUNNING_STATES:
            # Whether the skip button is up is reported separately from the ad
            # state, so it is trusted as sent.
            new = replace(self._state, ad_playing=True, ad_skippable=skip_enabled)
        else:
            # Trailing events as an ad finishes arrive after playback has
            # resumed, so they must not turn "ad playing" back on.
            new = replace(self._state, ad_skippable=skip_enabled)
        self._update(new, immediate=True)

    @callback
    def handle_autoplay(self, enabled: bool, supported: bool) -> None:
        """Handle the autoplay setting being reported."""
        new = replace(self._state, autoplay=enabled, autoplay_supported=supported)
        if not supported:
            # The TV reports autoplay as unsupported for a moment right after
            # connecting, which would make the switch flicker to unavailable.
            self._update(new, immediate=False)
            return
        self._update_setting(new)

    @callback
    def handle_up_next(self, video_id: str | None) -> None:
        """Handle the video autoplay will play next."""
        title = self._titles.get(video_id, (None, None))[0] if video_id else None
        self._update_setting(
            replace(self._state, up_next_video_id=video_id, up_next_title=title)
        )
        if video_id and video_id not in self._titles:
            self._async_request_title(video_id)

    @callback
    def handle_subtitles(self, event: SubtitlesTrackEvent) -> None:
        """Handle a subtitles track change; no language means off."""
        if (
            event.video_id
            and self._state.video_id
            and event.video_id != self._state.video_id
        ):
            return
        style = None
        if event.style:
            with contextlib.suppress(ValueError, TypeError):
                style = json.loads(event.style)
        language = event.language_name or event.language_code
        self._update_setting(
            replace(
                self._state,
                subtitles=language or SUBTITLES_OFF,
                subtitles_code=event.language_code,
                subtitles_track=event.track_name or None,
                subtitles_kind=event.kind,
                subtitles_style=style if isinstance(style, dict) else None,
                last_subtitles_code=event.language_code
                or self._state.last_subtitles_code,
                last_subtitles_name=language or self._state.last_subtitles_name,
            )
        )

    @callback
    def note_subtitles(self, code: str | None, name: str | None) -> None:
        """Record subtitles set from Home Assistant, before the TV reports it."""
        self._update_setting(
            replace(
                self._state,
                subtitles=name or SUBTITLES_OFF,
                subtitles_code=code,
                last_subtitles_code=code or self._state.last_subtitles_code,
                last_subtitles_name=name or self._state.last_subtitles_name,
            )
        )

    @callback
    def handle_video_quality(self, data: dict[str, Any]) -> None:
        """Handle the TV reporting the resolution it is playing."""
        video_id = data.get("videoId")
        if video_id and self._state.video_id and video_id != self._state.video_id:
            return
        levels = None
        raw_levels = data.get("availableQualityLevels")
        if raw_levels:
            with contextlib.suppress(ValueError, TypeError):
                parsed = json.loads(raw_levels)
                if isinstance(parsed, list):
                    levels = tuple(int(level) for level in parsed)
        self._update_setting(
            replace(
                self._state,
                video_quality=data.get("qualityLevel") or None,
                video_quality_levels=levels,
            )
        )

    @callback
    def handle_playback_speed(self, speed: float) -> None:
        """Handle a playback speed change."""
        self._update_setting(replace(self._state, playback_speed=speed))

    @callback
    def _update_setting(self, new: TvState) -> None:
        """Store a setting change without disturbing a settling state.

        Setting events arrive in bursts around seeks and ads; publishing them
        at once would also publish a half-finished playback transition.
        """
        if self._unsub_settle is not None:
            self._state = new
        else:
            self._update(new, immediate=True)

    @callback
    def handle_disconnected(self) -> None:
        """Handle the TV ending the session (e.g. YouTube sent to background)."""
        LOGGER.debug("%s ended the lounge session", self.config_entry.title)
        self._update(self._cleared(PlayerStatus.OFF), immediate=True)

    async def _async_check_stale(self, _now: datetime | None = None) -> None:
        """Detect a player that stopped without sending an event.

        When YouTube drops to its profile picker or home screen mid-video, the
        TV goes silent, and the last "playing" state would otherwise stay
        forever. A TV with an active player answers "what's playing" at once.
        """
        state = self.data
        if not self.session_enabled:
            return
        if state.status not in (PlayerStatus.PLAYING, PlayerStatus.BUFFERING):
            return
        if _position_overrun(state):
            LOGGER.debug("%s played past its end; assuming stopped", state.video_id)
            self._update(self._idle_state(), immediate=True)
            return
        if self._unsub_stale_reply is not None or not self.api.connected():
            return
        self._stale_asked_at = dt_util.utcnow()
        try:
            await self.api.get_now_playing()
        except (*_CONNECTION_ERRORS, NotConnectedException) as err:
            LOGGER.debug("Error requesting now playing: %s", err)
            return
        self._unsub_stale_reply = async_call_later(
            self.hass, STALE_REPLY_TIMEOUT, self._stale_reply_timeout
        )

    @callback
    def _stale_reply_timeout(self, _now: datetime) -> None:
        """Go idle if the TV didn't answer the staleness check."""
        self._unsub_stale_reply = None
        answered = (
            self._last_event_at is not None
            and self._stale_asked_at is not None
            and self._last_event_at >= self._stale_asked_at
        )
        if answered or self._state.status not in (
            PlayerStatus.PLAYING,
            PlayerStatus.BUFFERING,
        ):
            return
        LOGGER.debug(
            "%s didn't answer; assuming the player stopped", self.config_entry.title
        )
        self._update(self._idle_state(), immediate=True)

    def _idle_state(self) -> TvState:
        if self._app_running is False:
            return self._cleared(PlayerStatus.OFF)
        return self._cleared(PlayerStatus.IDLE)

    def _cleared(self, status: PlayerStatus) -> TvState:
        """Return a state without a video, keeping the app's settings."""
        return TvState(
            status=status,
            autoplay=self._state.autoplay,
            autoplay_supported=self._state.autoplay_supported,
            playback_speed=self._state.playback_speed,
            subtitles=self._state.subtitles,
            subtitles_code=self._state.subtitles_code,
            subtitles_track=self._state.subtitles_track,
            subtitles_kind=self._state.subtitles_kind,
            subtitles_style=self._state.subtitles_style,
            last_subtitles_code=self._state.last_subtitles_code,
            last_subtitles_name=self._state.last_subtitles_name,
            queue=self._state.queue if status is PlayerStatus.IDLE else (),
        )

    @callback
    def _update(self, new: TvState, *, immediate: bool) -> None:
        """Store a new working state and publish it now or once it settles."""
        self._state = new
        self._cancel_settle()
        if immediate:
            self._publish()
        else:
            self._unsub_settle = async_call_later(
                self.hass, SETTLE_DELAY, self._publish_settled
            )

    @callback
    def _publish_settled(self, _now: datetime) -> None:
        self._unsub_settle = None
        self._publish()

    @callback
    def _publish(self) -> None:
        if _equivalent(self.data, self._state):
            # Keep the published position so the frontend doesn't jump.
            self._state = replace(
                self._state,
                position=self.data.position,
                position_updated_at=self.data.position_updated_at,
            )
            return
        if self.data.ad_playing != self._state.ad_playing:
            LOGGER.debug(
                "ad playing: %s -> %s (player %s)",
                self.data.ad_playing,
                self._state.ad_playing,
                self._state.status,
            )
        self.async_set_updated_data(self._state)

    @callback
    def _cancel_settle(self) -> None:
        if self._unsub_settle is not None:
            self._unsub_settle()
            self._unsub_settle = None

    @callback
    def _async_request_title(self, video_id: str) -> None:
        if video_id in self._titles_pending:
            return
        self._titles_pending.add(video_id)
        self.config_entry.async_create_background_task(
            self.hass,
            self._async_fetch_title(video_id),
            f"{DOMAIN} title {video_id}",
        )

    async def _async_fetch_title(self, video_id: str) -> None:
        """Look up the video title and channel with YouTube oEmbed."""
        try:
            async with self._title_slots:
                found = await self._async_request_oembed(video_id)
        finally:
            self._titles_pending.discard(video_id)
        if found is None:
            return
        title, channel = found
        if len(self._titles) >= MAX_TITLE_CACHE:
            self._titles.pop(next(iter(self._titles)))
        self._titles[video_id] = (title, channel)
        new = self._state
        if new.video_id == video_id:
            new = replace(new, title=title, channel=channel)
        if new.up_next_video_id == video_id:
            new = replace(new, up_next_title=title)
        if new == self._state:
            if video_id in self._state.queue:
                # Only the queue list shows this title.
                self.async_update_listeners()
            return
        self._state = new
        if self._unsub_settle is None:
            self._publish()

    async def _async_request_oembed(
        self, video_id: str
    ) -> tuple[str | None, str | None] | None:
        """Return a video's title and channel, or None to try again later.

        A video oEmbed doesn't know (private, removed) is remembered without
        a title; a rate limit, server error or network error is not, so the
        next report asks again.
        """
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                OEMBED_URL,
                params={
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "format": "json",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    data = await response.json(content_type=None)
                    return data.get("title"), data.get("author_name")
                LOGGER.debug(
                    "oEmbed returned HTTP %s for %s", response.status, video_id
                )
                if response.status == 429 or response.status >= 500:
                    return None
                return None, None
        except (*_CONNECTION_ERRORS, ValueError) as err:
            LOGGER.debug("Error fetching title of %s: %s", video_id, err)
            return None


def _queue_index(queue: tuple[str, ...], state: TvState) -> int | None:
    """Return where the playing video sits in the queue.

    A video can be queued twice; the occurrence nearest the last known index
    is taken, since the TV doesn't report the index itself.
    """
    if state.video_id is None or state.video_id not in queue:
        return None
    matches = [i for i, video_id in enumerate(queue) if video_id == state.video_id]
    if state.queue_index is None:
        return matches[0]
    return min(matches, key=lambda i: abs(i - state.queue_index))


def current_position(state: TvState) -> float:
    """Return the playback position now, extrapolated while playing."""
    if state.position is None:
        return 0.0
    if state.status is not PlayerStatus.PLAYING or state.position_updated_at is None:
        return state.position
    elapsed = (dt_util.utcnow() - state.position_updated_at).total_seconds()
    return state.position + elapsed


def _position_overrun(state: TvState) -> bool:
    """Return True if a playing video's position is well past its end."""
    if (
        state.status is not PlayerStatus.PLAYING
        or state.position is None
        or state.duration is None
        or state.position_updated_at is None
    ):
        return False
    elapsed = (dt_util.utcnow() - state.position_updated_at).total_seconds()
    return state.position + elapsed > state.duration + POSITION_OVERRUN


def _equivalent(old: TvState, new: TvState) -> bool:
    """Return True if new only differs from old by the expected playback progress."""
    if replace(old, position=None, position_updated_at=None) != replace(
        new, position=None, position_updated_at=None
    ):
        return False
    if old.position is None or new.position is None:
        return old.position == new.position
    if old.position_updated_at is None or new.position_updated_at is None:
        return False
    expected = old.position
    if old.status is PlayerStatus.PLAYING:
        expected += (new.position_updated_at - old.position_updated_at).total_seconds()
    return abs(expected - new.position) < POSITION_TOLERANCE
