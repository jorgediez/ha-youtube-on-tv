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

from homeassistant.const import CONF_HOST
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    APP_STATE_INTERVAL,
    CONF_APP_URL,
    CONF_SCREEN_ID,
    DOMAIN,
    LOGGER,
    LOUNGE_DEVICE_NAME,
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
MAX_TITLE_CACHE = 50
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
    """Adds the video quality event, which pyytlounge doesn't dispatch.

    The TV reports the resolution through "onVideoQualityChanged"; the
    library ignores unknown events, so it is picked up here.
    """

    def __init__(self, coordinator: YouTubeOnTvCoordinator, *args: Any) -> None:
        super().__init__(*args)
        self._coordinator = coordinator

    async def _process_event(self, event_type: str, args: list[Any]) -> None:
        if event_type == "onVideoQualityChanged" and args:
            self._coordinator.handle_video_quality(args[0])
        await super()._process_event(event_type, args)


class _Listener(EventListener):
    """Forwards pyytlounge events to the coordinator."""

    def __init__(self, coordinator: YouTubeOnTvCoordinator) -> None:
        super().__init__()
        self._coordinator = coordinator

    async def now_playing_changed(self, event: NowPlayingEvent) -> None:
        self._coordinator.handle_now_playing(event)

    async def playback_state_changed(self, event: PlaybackStateEvent) -> None:
        self._coordinator.handle_playback_state(
            event.state, event.current_time, event.duration
        )

    async def ad_state_changed(self, event: AdStateEvent) -> None:
        self._coordinator.handle_ad_state(event.ad_state, event.is_skip_enabled)

    async def ad_playing_changed(self, event: AdPlayingEvent) -> None:
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
        # Last YouTube app state reported by DIAL; None if the TV didn't answer.
        self.app_state: str | None = None
        self._unsub_settle: CALLBACK_TYPE | None = None
        self._task: asyncio.Task[None] | None = None
        self._titles: dict[str, tuple[str | None, str | None]] = {}
        # When the TV last sent a playback event, and when it was last asked
        # what's playing by the staleness check.
        self._last_event_at: datetime | None = None
        self._stale_asked_at: datetime | None = None
        self._unsub_stale_reply: CALLBACK_TYPE | None = None

    @property
    def app_running(self) -> bool | None:
        """Return whether YouTube runs on the TV, None if not known."""
        return self._app_running

    @property
    def has_app_state(self) -> bool:
        """Return True if the TV's DIAL endpoint is known and polled."""
        return self._app_url is not None

    @property
    def working_state(self) -> TvState:
        """Return the latest state, including changes not yet published."""
        return self._state

    @property
    def host(self) -> str | None:
        """Return the TV's host, if known."""
        return self.config_entry.data.get(CONF_HOST)

    async def async_start(self) -> None:
        """Link to the screen and start listening in the background.

        The session is closed by async_shutdown, which Home Assistant runs when
        the entry unloads or its setup fails.
        """
        await self.api.__aenter__()
        try:
            await self._async_link()
        except _CONNECTION_ERRORS as err:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

        entry = self.config_entry
        self._task = entry.async_create_background_task(
            self.hass, self._async_run(), f"{DOMAIN} lounge {entry.entry_id}"
        )
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

    async def async_shutdown(self) -> None:
        """Disconnect from the screen."""
        await super().async_shutdown()
        self._cancel_settle()
        if self._unsub_stale_reply is not None:
            self._unsub_stale_reply()
            self._unsub_stale_reply = None
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
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY)
            else:
                delay = RECONNECT_MIN_DELAY
                auth_failures = 0

    async def _async_connect_and_subscribe(self) -> None:
        if not self.api.linked():
            await self._async_link()
        if not self.api.connected():
            if not await self.api.connect():
                if self.api.linked():
                    raise NotConnectedException("Lounge refused the connection")
                return  # token expired; relink on next iteration
            LOGGER.debug("Connected to %s", self.config_entry.title)
            self._set_available(True)
            await self.api.get_now_playing()

        started = time.monotonic()
        await self.api.subscribe()
        if time.monotonic() - started < MIN_SUBSCRIBE_SECONDS:
            # Guard against a tight loop if the server ends requests at once.
            await asyncio.sleep(MIN_SUBSCRIBE_SECONDS)

    @callback
    def _set_available(self, available: bool) -> None:
        if self.last_update_success == available:
            return
        self.last_update_success = available
        self.async_update_listeners()

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
            # Position and duration refer to the ad, not the video.
            self._update(
                replace(
                    current,
                    status=PlayerStatus.PLAYING,
                    ad_playing=True,
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
        if ad_state is State.Playing:
            new = replace(self._state, ad_playing=True, ad_skippable=skip_enabled)
        else:
            new = replace(self._state, ad_skippable=False)
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
        self.async_set_updated_data(self._state)

    @callback
    def _cancel_settle(self) -> None:
        if self._unsub_settle is not None:
            self._unsub_settle()
            self._unsub_settle = None

    @callback
    def _async_request_title(self, video_id: str) -> None:
        self.config_entry.async_create_background_task(
            self.hass,
            self._async_fetch_title(video_id),
            f"{DOMAIN} title {video_id}",
        )

    async def _async_fetch_title(self, video_id: str) -> None:
        """Look up the video title and channel with YouTube oEmbed."""
        session = async_get_clientsession(self.hass)
        title = channel = None
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
                    title = data.get("title")
                    channel = data.get("author_name")
                else:
                    LOGGER.debug(
                        "oEmbed returned HTTP %s for %s", response.status, video_id
                    )
        except (*_CONNECTION_ERRORS, ValueError) as err:
            LOGGER.debug("Error fetching title of %s: %s", video_id, err)
            return

        if len(self._titles) >= MAX_TITLE_CACHE:
            self._titles.pop(next(iter(self._titles)))
        self._titles[video_id] = (title, channel)
        new = self._state
        if new.video_id == video_id:
            new = replace(new, title=title, channel=channel)
        if new.up_next_video_id == video_id:
            new = replace(new, up_next_title=title)
        if new == self._state:
            return
        self._state = new
        if self._unsub_settle is None:
            self._publish()


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
