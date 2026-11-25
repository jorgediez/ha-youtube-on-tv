"""Tests for setting up and unloading YouTube on TV."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from freezegun.api import FrozenDateTimeFactory
import pytest
from pyytlounge import EventListener, NowPlayingEvent, State
from pyytlounge.exceptions import NotSupportedException

from custom_components.youtube_on_tv.const import (
    CONF_MODEL,
    CONF_SCREEN_ID,
    DOMAIN,
)
from custom_components.youtube_on_tv.coordinator import (
    LOUNGE_SILENCE_SECONDS,
    _LoungeApi,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import (
    ENTRY_DATA,
    HOST,
    MOCK_SCREEN,
    NEW_SCREEN_ID,
    SCREEN_ID,
    FakeLounge,
    wait_for,
)
from .harness import (
    ClientSessionGenerator,
    MockConfigEntry,
    async_fire_time_changed,
    get_diagnostics_for_config_entry,
)

VIDEO_ID = "Yeke1krzPFM"
NEXT_ID = "aqz-KE-bpKQ"

MANIFEST = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "youtube_on_tv"
        / "manifest.json"
    ).read_text(encoding="utf-8")
)


async def test_setup_and_unload(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Unloading stops listening, leaving the TV playing.

    No disconnect is sent: that tells the TV the user stopped casting, which
    stops playback and drops the queue (seen on a Samsung Tizen TV).
    """
    assert mock_config_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    init_integration.disconnect.assert_not_awaited()
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
    """A screen id YouTube no longer accepts starts a reauth flow.

    Not on the first refusal at startup: like a running session, it asks to
    re-pair only after several in a row.
    """
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

    assert mock_config_entry.state is ConfigEntryState.LOADED
    await wait_for(
        lambda: len(hass.config_entries.flow.async_progress_by_handler(DOMAIN)) == 1
    )
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flows[0]["context"]["source"] == SOURCE_REAUTH
    assert mock_lounge[0].pair_with_screen_id.await_count == 4
    assert _player_unavailable(hass)


async def test_setup_bad_reply_is_retried(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_lounge: list[FakeLounge],
    mock_app_state: AsyncMock,
) -> None:
    """One bad reply from YouTube at startup doesn't ask to re-pair."""
    mock_config_entry.add_to_hass(hass)
    original = FakeLounge.__init__

    def flaky_init(self, *args, **kwargs) -> None:
        original(self, *args, **kwargs)
        link = self._link
        replies = iter([ValueError("bad json")])

        async def flaky_link(*link_args) -> bool:
            if (reply := next(replies, None)) is not None:
                raise reply
            return await link(*link_args)

        self.pair_with_screen_id.side_effect = flaky_link

    FakeLounge.__init__ = flaky_init
    try:
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    finally:
        FakeLounge.__init__ = original

    lounge = mock_lounge[0]
    await wait_for(lounge.subscribed.is_set)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert lounge.pair_with_screen_id.await_count == 2
    assert not _player_unavailable(hass)


def _refuse_token(lounge: FakeLounge):
    """Make connect act like pyytlounge on a 401: drop the token, fail.

    It yields like a real request, so a session looping on refusals spins
    without blocking the test, which then fails on its timeout.
    """

    async def refuse() -> bool:
        await asyncio.sleep(0)
        lounge._linked = False
        return False

    return refuse


async def test_refused_token_is_relinked_at_once(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A stale token YouTube refuses is replaced without waiting."""
    lounge = init_integration
    refuse = _refuse_token(lounge)
    calls = 0

    async def refuse_once() -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            return await refuse()
        return await lounge._connect()

    lounge.connect.side_effect = refuse_once
    lounge.subscribed.clear()
    coordinator = mock_config_entry.runtime_data
    with patch.object(
        coordinator, "_async_reconnect_wait", AsyncMock()
    ) as reconnect_wait:
        lounge.drop_connection()
        await wait_for(lounge.subscribed.is_set)

    reconnect_wait.assert_not_awaited()
    assert lounge.pair_with_screen_id.await_count == 2
    assert lounge.connect.await_count == 3


async def test_refused_after_relink_backs_off(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A connection refused even with a fresh token waits before retrying.

    Without the wait, the session would relink and reconnect as fast as
    YouTube answers, for as long as it keeps refusing.
    """
    caplog.set_level(logging.DEBUG, logger="custom_components.youtube_on_tv")
    lounge = init_integration
    lounge.connect.side_effect = _refuse_token(lounge)
    coordinator = mock_config_entry.runtime_data
    waiting = asyncio.Event()

    async def hold(delay: float) -> None:
        waiting.set()
        await asyncio.Event().wait()  # stay in the wait until the test ends

    with patch.object(coordinator, "_async_reconnect_wait", side_effect=hold):
        lounge.drop_connection()
        async with asyncio.timeout(5):
            await waiting.wait()

    # The stale token, then the fresh one: two tries, not a loop.
    assert lounge.connect.await_count == 3
    assert lounge.pair_with_screen_id.await_count == 2
    assert "refused the connection right after linking" in caplog.text
    assert _player_unavailable(hass)


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


async def test_reauth_reloads_the_entry(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_lounge: list[FakeLounge],
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Reauth of a running entry reloads it with the new screen id.

    A loaded entry has an update listener, and async_update_reload_and_abort
    is deprecated for such an entry; it becomes an error in Home Assistant
    2026.12.
    """
    # Core reports the deprecated call through its frame helper's logger.
    caplog.set_level(logging.WARNING, logger="homeassistant.helpers.frame")
    result = await mock_config_entry.start_reauth_flow(hass)
    with patch(
        "custom_components.youtube_on_tv.config_flow.async_get_screen_from_host",
        return_value=replace(MOCK_SCREEN, screen_id=NEW_SCREEN_ID),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()

    assert mock_config_entry.data[CONF_SCREEN_ID] == NEW_SCREEN_ID
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert len(mock_lounge) == 2, "the entry was not reloaded"
    assert "update listener" not in caplog.text


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Diagnostics don't leak the screen id or the TV's address."""
    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )
    assert diagnostics["entry"]["data"]["screen_id"] == "**REDACTED**"
    assert diagnostics["entry"]["unique_id"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["host"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["app_url"] == "**REDACTED**"
    assert diagnostics["lounge"] == {
        "linked": True,
        "connected": True,
        "screen_name": "YouTube on TV",
        "screen_device_name": "Samsung TQ75QN900FTXXC",
    }
    assert diagnostics["dial"] == {"polled": True, "app_state": "running"}
    assert diagnostics["versions"]["integration"] == MANIFEST["version"]
    assert diagnostics["state"]["status"] == "off"
    assert diagnostics["pending_state"]["status"] == "off"
    # Nothing plays, so there is nothing to redact: None shows it is unset.
    assert diagnostics["state"]["video_id"] is None
    assert diagnostics["state"]["queue_length"] == 0
    assert SCREEN_ID not in str(diagnostics)
    assert HOST not in str(diagnostics)


async def test_diagnostics_leave_out_what_is_watched(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A report says something is playing and the queue's size, not what."""
    coordinator = mock_config_entry.runtime_data
    coordinator.handle_now_playing(
        NowPlayingEvent(
            {
                "state": str(State.Playing.value),
                "videoId": VIDEO_ID,
                "currentTime": "86",
                "duration": "237",
            }
        )
    )
    coordinator.handle_queue(
        {"mdxExpandedReceiverVideoIdList": f"{VIDEO_ID},{NEXT_ID}"}
    )
    await wait_for(lambda: coordinator.data.title is not None)

    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )
    state = diagnostics["state"]
    assert state["status"] == "playing"
    for key in ("video_id", "title", "channel"):
        assert state[key] == "**REDACTED**", key
    assert state["queue_length"] == 2
    assert "queue" not in state
    for private in (VIDEO_ID, NEXT_ID, "Dolor y Gloria", "VivaSueciaVEVO"):
        assert private not in str(diagnostics), private


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
                "binary_sensor.youtube_on_samsung_neo_qled_youtube_session",
                "button.youtube_on_samsung_neo_qled_skip_ad",
                "sensor.youtube_on_samsung_neo_qled_app_state",
                "sensor.youtube_on_samsung_neo_qled_up_next",
                "sensor.youtube_on_samsung_neo_qled_subtitles",
                "sensor.youtube_on_samsung_neo_qled_video_quality",
                "switch.youtube_on_samsung_neo_qled_autoplay",
                "switch.youtube_on_samsung_neo_qled_subtitles",
                "switch.youtube_on_samsung_neo_qled_remote_session",
                "select.youtube_on_samsung_neo_qled_playback_speed",
                "todo.youtube_on_samsung_neo_qled_queue",
            },
        ),
        bedroom: (
            "YouTube on Bedroom TV",
            "UE43",
            {
                "media_player.youtube_on_bedroom_tv",
                "binary_sensor.youtube_on_bedroom_tv_ad_playing",
                "binary_sensor.youtube_on_bedroom_tv_youtube_session",
                "button.youtube_on_bedroom_tv_skip_ad",
                "sensor.youtube_on_bedroom_tv_app_state",
                "sensor.youtube_on_bedroom_tv_up_next",
                "sensor.youtube_on_bedroom_tv_subtitles",
                "sensor.youtube_on_bedroom_tv_video_quality",
                "switch.youtube_on_bedroom_tv_autoplay",
                "switch.youtube_on_bedroom_tv_subtitles",
                "switch.youtube_on_bedroom_tv_remote_session",
                "select.youtube_on_bedroom_tv_playback_speed",
                "todo.youtube_on_bedroom_tv_queue",
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


async def test_long_outage_is_reported(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A session that stays down is worth one warning, and so is its return."""
    caplog.set_level(logging.WARNING, logger="custom_components.youtube_on_tv")
    init_integration.connect.side_effect = TimeoutError("offline")
    init_integration.drop_connection()
    await wait_for(lambda: _player_unavailable(hass))

    # A brief drop says nothing: the threshold hasn't passed.
    assert "still retrying" not in caplog.text

    with patch(
        "custom_components.youtube_on_tv.coordinator.OUTAGE_WARNING_DELAY",
        timedelta(0),
    ):
        await wait_for(lambda: "still retrying" in caplog.text)
        assert caplog.text.count("still retrying") == 1

        init_integration.connect.side_effect = init_integration._connect
        await wait_for(lambda: "Reconnected to" in caplog.text)
    assert "Reconnected to Samsung Neo QLED" in caplog.text


async def test_unsupported_client_retries(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A screen pyytlounge refuses is retried, not given up on.

    It raises NotSupportedException for a blacklisted client, which today
    means a kids profile on the TV. Unhandled, that ended the background
    task for good: the TV was left uncontrolled with the entities still
    looking fine, and only reloading the entry brought it back.
    """
    caplog.set_level(logging.WARNING, logger="custom_components.youtube_on_tv")
    failures = 0

    async def kids_profile_once() -> None:
        nonlocal failures
        failures += 1
        if failures == 1:
            raise NotSupportedException("Unsupported client")
        await init_integration._subscribe()

    init_integration.subscribe.side_effect = kids_profile_once
    init_integration.subscribed.clear()
    init_integration.drop_connection()

    # The session comes back by itself, with one warning saying why.
    async with asyncio.timeout(5):
        await init_integration.subscribed.wait()
    await wait_for(lambda: not _player_unavailable(hass))
    assert caplog.text.count("YouTube client that can't be controlled") == 1


async def test_unexpected_error_retries(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An error the session doesn't know is logged and retried."""
    caplog.set_level(logging.ERROR, logger="custom_components.youtube_on_tv")
    failures = 0

    async def surprise_once() -> None:
        nonlocal failures
        failures += 1
        if failures == 1:
            raise RuntimeError("something new from the library")
        await init_integration._subscribe()

    init_integration.subscribe.side_effect = surprise_once
    init_integration.subscribed.clear()
    init_integration.drop_connection()

    async with asyncio.timeout(5):
        await init_integration.subscribed.wait()
    await wait_for(lambda: not _player_unavailable(hass))
    assert "something new from the library" in caplog.text


async def _reopen_stream(api: FakeLounge) -> None:
    """End the event stream cleanly; the session reopens it on its own."""
    calls = api.subscribe.await_count
    api._stop.set()
    await wait_for(lambda: api.subscribe.await_count > calls)


@pytest.mark.parametrize("lounge_silence", [LOUNGE_SILENCE_SECONDS])
async def test_silent_stream_is_reopened(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An event stream that goes silent is reopened, on the same session.

    YouTube sends something at least every 30 seconds. A stream that stays
    silent died without closing, and pyytlounge waits on it with no timeout:
    the player stayed on the video it last showed, hours after the TV had
    moved on, with nothing marking it stale.
    """
    api = init_integration
    api.get_now_playing.reset_mock()

    freezer.tick(timedelta(seconds=LOUNGE_SILENCE_SECONDS + 1))
    async_fire_time_changed(hass)
    # The frozen clock stops timers, wait_for's sleeps included: yield instead.
    for _ in range(100):
        if api.subscribe.await_count == 2:
            break
        await asyncio.sleep(0)
    assert api.subscribe.await_count == 2

    # Reopened without a new connection, which the TV would announce on
    # screen, and asked what's playing to catch up on what was missed.
    api.connect.assert_awaited_once()
    api.get_now_playing.assert_awaited_once()
    assert not _player_unavailable(hass)


@pytest.mark.parametrize("lounge_silence", [0.3])
async def test_events_keep_stream_open(
    hass: HomeAssistant, init_integration: FakeLounge
) -> None:
    """Each event pushes the silence bound back, so a live stream stays open."""
    api = init_integration
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    await _reopen_stream(api)
    calls = api.subscribe.await_count
    # Events every 0.1 seconds for a second: three times the bound.
    for _ in range(10):
        coordinator.note_lounge_activity()
        await asyncio.sleep(0.1)
    assert api.subscribe.await_count == calls

    # Silence: reopened once the bound passes.
    await wait_for(lambda: api.subscribe.await_count > calls)
    api.connect.assert_awaited_once()


async def test_stream_timeout_from_library_is_a_connection_error(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A TimeoutError the silence bound didn't cause is handled as before."""
    caplog.set_level(logging.DEBUG, logger="custom_components.youtube_on_tv")
    api = init_integration
    failures = 0

    async def timeout_once() -> None:
        nonlocal failures
        failures += 1
        if failures == 1:
            raise TimeoutError("read timed out")
        await api._subscribe()

    api.subscribe.side_effect = timeout_once
    await _reopen_stream(api)
    await wait_for(lambda: failures == 2)
    await wait_for(lambda: not _player_unavailable(hass))
    assert "Lounge connection error: read timed out" in caplog.text
    assert "reopening the event stream" not in caplog.text


async def test_any_lounge_event_counts_as_activity() -> None:
    """Every event the stream carries, keep-alives included, counts."""
    coordinator = MagicMock()
    api = _LoungeApi(
        coordinator, "Home Assistant", EventListener(), logging.getLogger(__name__)
    )
    await api._process_event("noop", [])
    coordinator.note_lounge_activity.assert_called_once()


def _bind_reply(*events: list[Any]) -> str:
    """Build a /bc/bind reply in the lounge's length-prefixed chunk format."""
    chunk = json.dumps([[event_id, event] for event_id, event in enumerate(events)])
    return f"{len(chunk) + 1}\n{chunk}\n"


def _lounge_status(client_name: str) -> list[Any]:
    """Return a loungeStatus event whose screen runs the given YouTube client."""
    device = {
        "type": "LOUNGE_SCREEN",
        "name": "TV",
        "deviceInfo": json.dumps({"clientName": client_name}),
    }
    return ["loungeStatus", {"devices": json.dumps([device])}]


async def test_failed_connect_drops_half_set_session() -> None:
    """A connect reply that raises midway leaves no half-connected session.

    Runs the real pyytlounge parsing: on a YouTube Kids profile the session
    ids arrive before the status that raises, and the event id is only set
    after the whole reply. Without the reset, connected() stayed True with
    no event id, and every later request failed before it was sent.
    """
    api = _LoungeApi(
        MagicMock(), "Home Assistant", EventListener(), logging.getLogger(__name__)
    )
    api.auth.screen_id = SCREEN_ID
    api.auth.lounge_id_token = "token"  # noqa: S105 (a fake token)
    reply = MagicMock(status=200)
    reply.text = AsyncMock(
        return_value=_bind_reply(
            ["c", "SID", "", 8], ["S", "GSESSION"], _lounge_status("TVHTML5_FOR_KIDS")
        )
    )
    api.session = MagicMock()
    api.session.post.return_value.__aenter__.return_value = reply

    with pytest.raises(NotSupportedException):
        await api.connect()
    assert not api.connected()

    # The next retry connects from scratch and gets a usable event id.
    reply.text.return_value = _bind_reply(
        ["c", "SID", "", 8], ["S", "GSESSION"], _lounge_status("TVHTML5")
    )
    assert await api.connect()
    assert api._last_event_id == 2


def _player_unavailable(hass: HomeAssistant) -> bool:
    """Return True once the media player has gone unavailable."""
    state = hass.states.get("media_player.youtube_on_samsung_neo_qled")
    return state is not None and state.state == "unavailable"
