"""Tests for the play queue: the to-do list and play_media enqueue."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pyytlounge import EventListener, NowPlayingEvent, State

from custom_components.youtube_on_tv.coordinator import (
    QUEUE_SETTLE_SECONDS,
    YouTubeOnTvCoordinator,
    _LoungeApi,
)
from custom_components.youtube_on_tv.todo import queue_uids
from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_ENQUEUE,
    DOMAIN as MP_DOMAIN,
    SERVICE_PLAY_MEDIA,
)
from homeassistant.components.todo import DOMAIN as TODO_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component

from .conftest import FakeLounge

PREFIX = "youtube_on_samsung_neo_qled"
QUEUE_ID = f"todo.{PREFIX}_queue"
PLAYER_ID = f"media_player.{PREFIX}"
A, B, C, D = "aqz-KE-bpKQ", "eRsGyueVLvQ", "R6MlUcmOul8", "jNQXAC9IVRw"
LIST_ID = "RQKYQmR-w0UahoeD2K7QcARqr3DiA"


def _coordinator(entry: MockConfigEntry) -> YouTubeOnTvCoordinator:
    return entry.runtime_data


async def _playing(
    hass: HomeAssistant,
    api: FakeLounge,
    entry: MockConfigEntry,
    video_id: str,
    queue: list[str],
) -> None:
    """Report video_id playing at 100 s with the given queue."""
    await api.listener.now_playing_changed(
        NowPlayingEvent(
            {
                "videoId": video_id,
                "currentTime": "100",
                "duration": "600",
                "state": str(State.Playing.value),
            }
        )
    )
    _coordinator(entry).handle_queue(
        {"listId": LIST_ID, "mdxExpandedReceiverVideoIdList": ",".join(queue)}
    )
    await hass.async_block_till_done()


async def _items(hass: HomeAssistant) -> list[dict]:
    response = await hass.services.async_call(
        TODO_DOMAIN,
        "get_items",
        {ATTR_ENTITY_ID: QUEUE_ID},
        blocking=True,
        return_response=True,
    )
    return response[QUEUE_ID]["items"]


async def _move(hass_ws_client, uid: str, previous_uid: str | None) -> dict:
    client = await hass_ws_client()
    data = {"type": "todo/item/move", "entity_id": QUEUE_ID, "uid": uid}
    if previous_uid is not None:
        data["previous_uid"] = previous_uid
    await client.send_json_auto_id(data)
    return await client.receive_json()


async def test_queue_is_listed(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Played videos are completed, the playing one is marked, titles are looked up."""
    await _playing(hass, init_integration, mock_config_entry, B, [A, B, C])

    assert hass.states.get(QUEUE_ID).state == "2"
    items = await _items(hass)
    assert [(item["uid"], item["status"]) for item in items] == [
        (A, "completed"),
        (B, "needs_action"),
        (C, "needs_action"),
    ]
    assert items[1]["summary"] == "Dolor y Gloria"
    assert items[1]["description"] == "Now playing - VivaSueciaVEVO"
    assert items[2]["description"] == "VivaSueciaVEVO"


async def test_queue_follows_the_playing_video(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """When the next video starts, the previous one counts as played."""
    await _playing(hass, init_integration, mock_config_entry, A, [A, B, C])
    await init_integration.listener.now_playing_changed(
        NowPlayingEvent({"videoId": B, "state": str(State.Playing.value)})
    )
    await hass.async_block_till_done()
    assert _coordinator(mock_config_entry).data.queue_index == 1


async def test_add_item_appends(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A pasted link is appended and shown before the TV confirms it."""
    await _playing(hass, init_integration, mock_config_entry, A, [A, B])
    await hass.services.async_call(
        TODO_DOMAIN,
        "add_item",
        {ATTR_ENTITY_ID: QUEUE_ID, "item": f"https://youtu.be/{C}"},
        blocking=True,
    )
    init_integration.add_video.assert_awaited_once_with(C)
    assert _coordinator(mock_config_entry).data.queue == (A, B, C)


async def test_add_item_plays_when_nothing_queued(
    hass: HomeAssistant, init_integration: FakeLounge
) -> None:
    """With no queue on the TV, the added video is played."""
    await hass.services.async_call(
        TODO_DOMAIN, "add_item", {ATTR_ENTITY_ID: QUEUE_ID, "item": C}, blocking=True
    )
    init_integration.play_video.assert_awaited_once_with(C)
    init_integration.add_video.assert_not_awaited()


async def test_add_item_rejects_non_videos(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Text that isn't a video link or id is rejected."""
    await _playing(hass, init_integration, mock_config_entry, A, [A])
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            TODO_DOMAIN,
            "add_item",
            {ATTR_ENTITY_ID: QUEUE_ID, "item": "cat videos"},
            blocking=True,
        )
    assert err.value.translation_key == "invalid_video"


async def test_remove_item(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Removing a video resends the queue, keeping the position of the playing one."""
    await _playing(hass, init_integration, mock_config_entry, B, [A, B, C, D])
    freezer.tick(10)
    await hass.services.async_call(
        TODO_DOMAIN,
        "remove_item",
        {ATTR_ENTITY_ID: QUEUE_ID, "item": [C]},
        blocking=True,
    )
    init_integration.set_playlist.assert_awaited_once_with([A, B, D], 1, 110.0, LIST_ID)
    assert _coordinator(mock_config_entry).data.queue == (A, B, D)


async def test_remove_playing_item_is_refused(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The playing video can't be removed from the list."""
    await _playing(hass, init_integration, mock_config_entry, B, [A, B, C])
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            TODO_DOMAIN,
            "remove_item",
            {ATTR_ENTITY_ID: QUEUE_ID, "item": [B]},
            blocking=True,
        )
    assert err.value.translation_key == "queue_playing_video"
    init_integration.set_playlist.assert_not_awaited()


@pytest.mark.parametrize(
    ("uid", "previous_uid", "expected"),
    [
        (D, B, [A, B, D, C]),  # after the playing video
        (C, D, [A, B, D, C]),  # to the end
        (D, None, [A, B, D, C]),  # to the top: played next instead
        (D, A, [A, B, D, C]),  # among played videos: played next instead
    ],
)
async def test_move_item(
    hass: HomeAssistant,
    hass_ws_client,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    uid: str,
    previous_uid: str | None,
    expected: list[str],
) -> None:
    """Dragging a video reorders the queue; nothing lands above the playing one."""
    await _playing(hass, init_integration, mock_config_entry, B, [A, B, C, D])
    response = await _move(hass_ws_client, uid, previous_uid)
    assert response["success"], response
    init_integration.set_playlist.assert_awaited_once()
    assert init_integration.set_playlist.await_args.args[:2] == (expected, 1)


async def test_move_playing_item_is_refused(
    hass: HomeAssistant,
    hass_ws_client,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The playing video stays where it is."""
    await _playing(hass, init_integration, mock_config_entry, B, [A, B, C])
    response = await _move(hass_ws_client, B, C)
    assert not response["success"]
    init_integration.set_playlist.assert_not_awaited()


async def test_edits_need_a_playing_video(
    hass: HomeAssistant,
    hass_ws_client,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Without a playing video there is no position to reorder around."""
    _coordinator(mock_config_entry).handle_queue(
        {"mdxExpandedReceiverVideoIdList": f"{A},{B}"}
    )
    await hass.async_block_till_done()
    response = await _move(hass_ws_client, B, None)
    assert not response["success"]
    init_integration.set_playlist.assert_not_awaited()


async def test_stale_reports_are_ignored_after_a_change(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The old queue the TV reports right after a change doesn't undo it."""
    await _playing(hass, init_integration, mock_config_entry, A, [A, B])
    coordinator = _coordinator(mock_config_entry)
    await coordinator.async_queue_add(C)
    old = {"mdxExpandedReceiverVideoIdList": f"{A},{B}"}

    coordinator.handle_queue(old)
    assert coordinator.data.queue == (A, B, C)

    with patch(
        "custom_components.youtube_on_tv.coordinator.time.monotonic",
        return_value=1e12 + QUEUE_SETTLE_SECONDS,
    ):
        coordinator.handle_queue(old)
    await hass.async_block_till_done()
    assert coordinator.data.queue == (A, B)


async def test_queue_cleared_when_app_closes(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The queue is kept while idle and dropped when the session ends."""
    await _playing(hass, init_integration, mock_config_entry, A, [A, B])
    await init_integration.listener.now_playing_changed(NowPlayingEvent({}))
    await hass.async_block_till_done()
    coordinator = _coordinator(mock_config_entry)
    assert coordinator.data.queue == (A, B)
    assert coordinator.data.queue_index is None

    await init_integration.listener.disconnected(MagicMock())
    await hass.async_block_till_done()
    assert coordinator.data.queue == ()


def test_repeated_videos_get_distinct_uids() -> None:
    """A video queued twice gets two list entries."""
    assert queue_uids((A, B, A, A)) == [A, B, f"{A}~2", f"{A}~3"]


async def test_playing_index_with_repeats(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """With a repeated video, the occurrence nearest the last index is taken."""
    await _playing(hass, init_integration, mock_config_entry, B, [A, B, C, A])
    coordinator = _coordinator(mock_config_entry)
    await init_integration.listener.now_playing_changed(
        NowPlayingEvent({"videoId": C, "state": str(State.Playing.value)})
    )
    await init_integration.listener.now_playing_changed(
        NowPlayingEvent({"videoId": A, "state": str(State.Playing.value)})
    )
    await hass.async_block_till_done()
    assert coordinator.data.queue_index == 3


@pytest.mark.parametrize(
    ("enqueue", "method", "args"),
    [
        ("add", "add_video", (C,)),
        ("next", "insert_video", (C,)),
        ("replace", "set_playlist", ([C], 0, 0, "")),
        ("play", "play_video", (C,)),
    ],
)
async def test_play_media_enqueue(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    enqueue: str,
    method: str,
    args: tuple,
) -> None:
    """play_media queues videos as asked."""
    await _playing(hass, init_integration, mock_config_entry, A, [A, B])
    await hass.services.async_call(
        MP_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: PLAYER_ID,
            ATTR_MEDIA_CONTENT_ID: C,
            ATTR_MEDIA_CONTENT_TYPE: "video",
            ATTR_MEDIA_ENQUEUE: enqueue,
        },
        blocking=True,
    )
    getattr(init_integration, method).assert_awaited_once_with(*args)


async def test_play_next_is_shown_after_the_playing_video(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A video queued next appears right after the playing one."""
    await _playing(hass, init_integration, mock_config_entry, A, [A, B])
    await _coordinator(mock_config_entry).async_queue_next(C)
    assert _coordinator(mock_config_entry).data.queue == (A, C, B)


async def test_lounge_api_reports_queue_and_sends_commands() -> None:
    """The client forwards the queue from nowPlaying and builds queue commands."""
    coordinator = MagicMock()
    api = _LoungeApi(coordinator, "Home Assistant", EventListener())
    data = {"videoId": A, "mdxExpandedReceiverVideoIdList": f"{A},{B}"}
    await api._process_event("nowPlaying", [data])
    coordinator.handle_queue.assert_called_once_with(data)

    api._command = AsyncMock(return_value=True)
    assert await api.add_video(C)
    api._command.assert_awaited_with("addVideo", {"videoId": C})
    assert await api.insert_video(C)
    api._command.assert_awaited_with("insertVideo", {"videoId": C})
    assert await api.set_playlist([A, B], 1, 12.5, LIST_ID)
    api._command.assert_awaited_with(
        "setPlaylist",
        {
            "videoIds": f"{A},{B}",
            "videoId": B,
            "currentIndex": 1,
            "currentTime": 12.5,
            "listId": LIST_ID,
        },
    )


# Queueing with a video playing but no queue known


async def _playing_without_queue(
    hass: HomeAssistant, api: FakeLounge, video_id: str, *, wait: bool = True
) -> None:
    await api.listener.now_playing_changed(
        NowPlayingEvent(
            {
                "videoId": video_id,
                "currentTime": "100",
                "duration": "600",
                "state": str(State.Playing.value),
            }
        )
    )
    if wait:
        await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("enqueue", "method"), [("next", "insert_video"), ("add", "add_video")]
)
async def test_queue_on_a_video_without_known_queue(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    enqueue: str,
    method: str,
) -> None:
    """A video playing with no queue reported is queued after, not replaced.

    Seen on an Apple TV: the session dropped and came back while a video
    played, and the TV reported no queue; play next then played the new
    video at once.
    """
    await _playing_without_queue(hass, init_integration, A)
    assert _coordinator(mock_config_entry).data.queue == ()
    init_integration.get_now_playing.reset_mock()

    await hass.services.async_call(
        MP_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: PLAYER_ID,
            ATTR_MEDIA_CONTENT_ID: C,
            ATTR_MEDIA_CONTENT_TYPE: "video",
            ATTR_MEDIA_ENQUEUE: enqueue,
        },
        blocking=True,
    )

    getattr(init_integration, method).assert_awaited_once_with(C)
    init_integration.play_video.assert_not_awaited()
    # The TV is asked for its queue, so the list shows it.
    init_integration.get_now_playing.assert_awaited()


async def test_next_plays_when_nothing_plays(
    hass: HomeAssistant, init_integration: FakeLounge
) -> None:
    """With no video at all, play next just plays it."""
    await hass.services.async_call(
        MP_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: PLAYER_ID,
            ATTR_MEDIA_CONTENT_ID: C,
            ATTR_MEDIA_CONTENT_TYPE: "video",
            ATTR_MEDIA_ENQUEUE: "next",
        },
        blocking=True,
    )
    init_integration.play_video.assert_awaited_once_with(C)
    init_integration.insert_video.assert_not_awaited()


async def test_bare_video_id_through_the_rest_api(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    hass_client,
) -> None:
    """A bare 11-character id is a video, as a link is (REST answers 200)."""
    await _playing(hass, init_integration, mock_config_entry, A, [A, B])
    assert await async_setup_component(hass, "api", {})
    client = await hass_client()
    response = await client.post(
        "/api/services/media_player/play_media",
        json={
            ATTR_ENTITY_ID: PLAYER_ID,
            ATTR_MEDIA_CONTENT_TYPE: "video",
            ATTR_MEDIA_CONTENT_ID: C,
            ATTR_MEDIA_ENQUEUE: "next",
        },
    )
    assert response.status == 200
    init_integration.insert_video.assert_awaited_once_with(C)


# Resending the queue


@pytest.mark.parametrize(
    ("queue", "index"),
    [([A, B], 1), ([A, B], 2), ([A, B], -1), ([], 0)],
    ids=["other video", "past the end", "negative", "empty"],
)
async def test_queue_set_refuses_a_queue_without_the_playing_video(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    queue: list[str],
    index: int,
) -> None:
    """The index must point at the playing video, or nothing is sent."""
    await _playing(hass, init_integration, mock_config_entry, A, [A, B])
    with pytest.raises(ServiceValidationError) as err:
        await _coordinator(mock_config_entry).async_queue_set(queue, index)
    assert err.value.translation_key == "queue_changed"
    init_integration.set_playlist.assert_not_awaited()


# Title lookups


async def _lookups_done(coordinator: YouTubeOnTvCoordinator) -> None:
    """Wait until no title lookup is in flight."""
    async with asyncio.timeout(5):
        while coordinator._titles_pending:
            await asyncio.sleep(0.01)


async def test_titles_fetched_a_few_at_a_time_nearest_first(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A 50-video queue sends at most 4 lookups at once, nearest first."""
    coordinator = _coordinator(mock_config_entry)
    queue = [f"video{i:06d}" for i in range(50)]
    gate = asyncio.Event()
    running = 0
    peak = 0
    order: list[str] = []

    async def lookup(video_id: str) -> tuple[str, str]:
        nonlocal running, peak
        order.append(video_id)
        running += 1
        peak = max(peak, running)
        await gate.wait()
        running -= 1
        return f"Title {video_id}", "Channel"

    with patch.object(coordinator, "_async_request_oembed", side_effect=lookup):
        try:
            await _playing_without_queue(hass, init_integration, queue[20], wait=False)
            coordinator.handle_queue(
                {"mdxExpandedReceiverVideoIdList": ",".join(queue)}
            )
            for _ in range(5):
                await asyncio.sleep(0)
            first = list(order)
        finally:
            gate.set()
        await _lookups_done(coordinator)

    assert len(first) == 4
    assert set(first) == {queue[20], queue[19], queue[21], queue[18]}

    assert peak == 4
    assert sorted(order) == sorted(queue)
    assert coordinator.title(queue[0]) == ("Title video000000", "Channel")


async def test_repeated_queue_report_does_not_fetch_again(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A lookup in flight is not started again; a failed one is retried."""
    coordinator = _coordinator(mock_config_entry)
    gate = asyncio.Event()
    calls: list[str] = []

    async def lookup(video_id: str) -> tuple[str, str] | None:
        calls.append(video_id)
        await gate.wait()
        return None  # rate limited: not cached

    with patch.object(coordinator, "_async_request_oembed", side_effect=lookup):
        try:
            await _playing_without_queue(hass, init_integration, A, wait=False)
            coordinator.handle_queue({"mdxExpandedReceiverVideoIdList": f"{A},{B}"})
            coordinator.handle_queue({"mdxExpandedReceiverVideoIdList": f"{A},{B},{C}"})
            for _ in range(5):
                await asyncio.sleep(0)
            first = list(calls)
        finally:
            gate.set()
        await _lookups_done(coordinator)
        assert sorted(first) == sorted([A, B, C])
        assert coordinator.title(B) == (None, None)

        calls.clear()
        coordinator.handle_queue({"mdxExpandedReceiverVideoIdList": f"{A},{C}"})
        await _lookups_done(coordinator)
        assert sorted(calls) == sorted([A, C])


@pytest.mark.parametrize(
    ("status", "expected"),
    [(404, (None, None)), (401, (None, None)), (429, None), (503, None)],
)
async def test_oembed_errors_cached_only_when_lasting(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    expected: tuple | None,
) -> None:
    """An unknown video is remembered; a rate limit or outage is asked again."""
    aioclient_mock.clear_requests()
    aioclient_mock.get("https://www.youtube.com/oembed", status=status)
    result = await _coordinator(mock_config_entry)._async_request_oembed(A)
    assert result == expected


async def test_play_list_opens_a_closed_app_first(
    hass: HomeAssistant,
    init_integration: FakeLounge,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A list sent while YouTube is closed opens it with the first video."""
    coordinator = _coordinator(mock_config_entry)
    coordinator._app_running = False

    async def launch(video_id: str | None = None) -> None:
        coordinator._app_running = True
        await _playing_without_queue(hass, init_integration, video_id)

    with patch.object(coordinator, "async_launch", side_effect=launch) as mock:
        await coordinator.async_play_list([B, C], 1, 30)

    mock.assert_awaited_once_with(C)
    init_integration.set_playlist.assert_awaited_once_with([B, C], 1, 30, "")
