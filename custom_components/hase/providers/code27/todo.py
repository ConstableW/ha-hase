"""To-do list for CODE27's memos (Lab -> Notes & Reminders)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ...const import (DOMAIN)
from .coordinator import Code27Coordinator
from .entity import Code27Entity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Code27Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([Code27MemoTodoList(coordinator)])


def _memo_to_item(memo: dict, no_title: str) -> TodoItem:
    due = None
    next_fire_ms = memo.get("next_fire_at_ms") or 0
    if next_fire_ms:
        due = datetime.fromtimestamp(next_fire_ms / 1000, tz=timezone.utc)

    return TodoItem(
        uid=memo["id"],
        summary=memo.get("title") or no_title,
        description=memo.get("body"),
        status=(
            TodoItemStatus.COMPLETED
            if memo.get("is_expired")
            else TodoItemStatus.NEEDS_ACTION
        ),
        due=due,
    )


class Code27MemoTodoList(Code27Entity, TodoListEntity):
    """Notes & reminders stored on the CODE27 device."""

    _attr_translation_key = "memos"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
    )

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_memos"

    @property
    def todo_items(self) -> list[TodoItem]:
        lang = (self.hass.config.language or "en").lower()
        no_title = "(kein Titel)" if lang.startswith("de") else "(no title)"
        return [
            _memo_to_item(memo, no_title) for memo in self.coordinator.data.memos
        ]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        await self.coordinator.client.create_memo(
            {
                "title": item.summary,
                "body": item.description or "",
                "memo_type": "static",
            }
        )
        await self.coordinator.async_request_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        # is_expired looked like a "done" flag but turned out to be a
        # time-computed field on the device - writing it back had no visible
        # effect. CODE27's own memo editor has no "complete" action either,
        # only edit-in-place and delete, so this only ever touches title/body.
        #
        # A pure status change (the frontend's own "mark complete"
        # checkbox, which calls this same method) used to fall through
        # silently here - payload stayed empty, the method returned as if
        # it had succeeded, and the checkbox only reverted on the next
        # refresh with no explanation of why. Reject it explicitly
        # instead, since there genuinely is nothing on the device this
        # can write to.
        current = next(
            (m for m in self.coordinator.data.memos if m.get("id") == item.uid), None
        )
        if current is not None and item.status is not None:
            current_status = (
                TodoItemStatus.COMPLETED
                if current.get("is_expired")
                else TodoItemStatus.NEEDS_ACTION
            )
            if item.status != current_status:
                raise HomeAssistantError(
                    "CODE27 notes have no 'completed' state on the device - "
                    "only the title and description can be edited here."
                )

        payload: dict = {}
        if item.summary is not None:
            payload["title"] = item.summary
        if item.description is not None:
            payload["body"] = item.description
        if not payload:
            return
        await self.coordinator.client.update_memo(item.uid, payload)
        await self.coordinator.async_request_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        for uid in uids:
            await self.coordinator.client.delete_memo(uid)
        await self.coordinator.async_request_refresh()
