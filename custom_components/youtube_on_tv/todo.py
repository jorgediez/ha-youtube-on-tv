"""To-do list mirroring the TV's YouTube play queue."""

from __future__ import annotations

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import YouTubeOnTvConfigEntry
from .const import DOMAIN
from .coordinator import YouTubeOnTvCoordinator
from .entity import YouTubeOnTvEntity
from .media_player import parse_video_id

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YouTubeOnTvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the queue list."""
    async_add_entities([YouTubeOnTvQueue(entry.runtime_data)])


def queue_uids(queue: tuple[str, ...]) -> list[str]:
    """Return a stable id per queue entry; a repeated video gets a suffix."""
    seen: dict[str, int] = {}
    uids = []
    for video_id in queue:
        seen[video_id] = seen.get(video_id, 0) + 1
        count = seen[video_id]
        uids.append(video_id if count == 1 else f"{video_id}~{count}")
    return uids


class YouTubeOnTvQueue(YouTubeOnTvEntity, TodoListEntity):
    """The play queue: add a link to queue it, drag to reorder, delete to drop.

    Videos already played show as completed. The playing video stays in
    place: it can't be moved or deleted, and nothing can move above it.
    """

    _attr_translation_key = "queue"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.MOVE_TODO_ITEM
    )

    def __init__(self, coordinator: YouTubeOnTvCoordinator) -> None:
        """Initialize the queue list."""
        super().__init__(coordinator, "queue")

    @property
    def todo_items(self) -> list[TodoItem]:
        """Return the queue as to-do items."""
        state = self.coordinator.data
        items = []
        for i, (uid, video_id) in enumerate(
            zip(queue_uids(state.queue), state.queue, strict=True)
        ):
            title, channel = self.coordinator.title(video_id)
            played = state.queue_index is not None and i < state.queue_index
            description = channel
            if i == state.queue_index:
                description = f"Now playing - {channel}" if channel else "Now playing"
            items.append(
                TodoItem(
                    uid=uid,
                    summary=title or video_id,
                    description=description,
                    status=(
                        TodoItemStatus.COMPLETED
                        if played
                        else TodoItemStatus.NEEDS_ACTION
                    ),
                )
            )
        return items

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Queue the video whose link or id is the item's text."""
        video_id = parse_video_id(item.summary or "")
        if video_id is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_video",
                translation_placeholders={"media_id": item.summary or ""},
            )
        await self.coordinator.async_queue_add(video_id)

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Remove videos from the queue."""
        queue, index = self._editable()
        current = queue[index][0]
        if current in uids:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="queue_playing_video"
            )
        remaining = [entry for entry in queue if entry[0] not in uids]
        await self._async_apply(remaining, current)

    async def async_move_todo_item(
        self, uid: str, previous_uid: str | None = None
    ) -> None:
        """Move a video after previous_uid, or to the top when it's None."""
        queue, index = self._editable()
        current = queue[index][0]
        if uid == current:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="queue_playing_video"
            )
        uids = [entry[0] for entry in queue]
        if uid not in uids or (previous_uid is not None and previous_uid not in uids):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="queue_changed"
            )
        entry = queue.pop(uids.index(uid))
        uids.remove(uid)
        position = 0 if previous_uid is None else uids.index(previous_uid) + 1
        # Above the playing video it would count as played; play it next.
        position = max(position, uids.index(current) + 1)
        queue.insert(position, entry)
        await self._async_apply(queue, current)

    def _editable(self) -> tuple[list[tuple[str, str]], int]:
        """Return the queue as (uid, video id) pairs and the playing index."""
        state = self.coordinator.data
        if state.queue_index is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_video"
            )
        return list(zip(queue_uids(state.queue), state.queue, strict=True)), (
            state.queue_index
        )

    async def _async_apply(self, queue: list[tuple[str, str]], current: str) -> None:
        """Send the new queue order, keeping the playing video playing."""
        index = [entry[0] for entry in queue].index(current)
        await self.coordinator.async_queue_set(
            [video_id for _, video_id in queue], index
        )
