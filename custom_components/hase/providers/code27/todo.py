"""To-do list for CODE27's memos (Lab -> Notes & Reminders)."""
from __future__ import annotations

import logging
import re
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
from .const import ALARM_DAY_BITS, ALARM_WEEK_ORDER, alarm_days
from .coordinator import Code27Coordinator
from .entity import Code27Entity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Code27Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([Code27MemoTodoList(coordinator), Code27AlarmTodoList(coordinator)])


def _memo_to_item(memo: dict, no_title: str) -> TodoItem:
    due = None
    next_fire_ms = memo.get("next_fire_at_ms") or 0
    if next_fire_ms:
        try:
            due = datetime.fromtimestamp(next_fire_ms / 1000, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            due = None

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


# -- alarms as a to-do list ----------------------------------------------------
# One entity for all alarms instead of one entity per alarm: nothing to clean
# up when an alarm is deleted. An item's title is "HH:MM label", its
# description the repeat days ("täglich", "Mo Di Mi", "Mo-Fr", "einmalig"),
# and "needs action" means the alarm is switched on (completed = off).

_DAY_ALIASES: dict[str, str] = {}
for _key, _names in {
    "mon": ("mo", "mon", "montag", "monday"),
    "tue": ("di", "tue", "dienstag", "tuesday"),
    "wed": ("mi", "wed", "mittwoch", "wednesday"),
    "thu": ("do", "thu", "donnerstag", "thursday"),
    "fri": ("fr", "fri", "freitag", "friday"),
    "sat": ("sa", "sat", "samstag", "saturday"),
    "sun": ("so", "sun", "sonntag", "sunday"),
}.items():
    for _name in _names:
        _DAY_ALIASES[_name] = _key
_DAILY_WORDS = {"täglich", "taeglich", "tgl", "daily", "everyday", "jedentag"}
_ONCE_WORDS = {"einmalig", "einmal", "once", "nie", "never"}
_DAY_LABELS = {
    "de": {"mon": "Mo", "tue": "Di", "wed": "Mi", "thu": "Do", "fri": "Fr", "sat": "Sa", "sun": "So"},
    "en": {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri", "sat": "Sat", "sun": "Sun"},
}
_TIME_PREFIX = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*(.*)$")


def parse_alarm_title(summary: str) -> tuple[int, int, str]:
    """``"07:30 Aufstehen"`` -> (7, 30, "Aufstehen"). Raises ValueError."""
    match = _TIME_PREFIX.match(summary or "")
    if not match:
        raise ValueError("title must start with the time, e.g. 07:30 Get up")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ValueError(f"{hour:02d}:{minute:02d} is not a valid time")
    return hour, minute, match.group(3).strip()


def parse_alarm_days(text: str | None) -> int:
    """Repeat description -> ``repeat_mask``. Empty means a one-off alarm.
    Accepts "täglich"/"daily", "einmalig"/"once", weekday names or
    abbreviations (German or English) and ranges such as "Mo-Fr"."""
    mask = 0
    for token in re.split(r"[\s,;/]+", (text or "").lower()):
        token = token.strip(".")
        if not token:
            continue
        if token.replace(" ", "") in _DAILY_WORDS:
            return 127
        if token in _ONCE_WORDS:
            continue
        if "-" in token:
            first, _, last = token.partition("-")
            a, b = _DAY_ALIASES.get(first), _DAY_ALIASES.get(last)
            if not a or not b:
                raise ValueError(f"unknown weekday range: {token}")
            i, j = ALARM_WEEK_ORDER.index(a), ALARM_WEEK_ORDER.index(b)
            span = ALARM_WEEK_ORDER[i:j + 1] if i <= j else ALARM_WEEK_ORDER[i:] + ALARM_WEEK_ORDER[:j + 1]
            for day in span:
                mask |= ALARM_DAY_BITS[day]
            continue
        day = _DAY_ALIASES.get(token)
        if not day:
            raise ValueError(f"unknown weekday: {token}")
        mask |= ALARM_DAY_BITS[day]
    return mask


def format_alarm_days(mask: int, lang: str) -> str:
    de = lang.lower().startswith("de")
    days = alarm_days(mask)
    if not days:
        return "einmalig" if de else "once"
    if len(days) == 7:
        return "täglich" if de else "daily"
    labels = _DAY_LABELS["de" if de else "en"]
    return " ".join(labels[d] for d in days)


class Code27AlarmTodoList(Code27Entity, TodoListEntity):
    """All alarms of the device as one to-do list (see the block comment above)."""

    _attr_translation_key = "alarms_list"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_alarms_todo"

    @property
    def todo_items(self) -> list[TodoItem]:
        lang = self.hass.config.language or "en"
        alarms = sorted(
            (a for a in self.coordinator.data.alarms if a.get("id")),
            key=lambda a: (a.get("hour", 0), a.get("minute", 0), str(a["id"])),
        )
        return [
            TodoItem(
                uid=a["id"],
                summary=(
                    f"{int(a.get('hour', 0)):02d}:{int(a.get('minute', 0)):02d} "
                    f"{a.get('label') or a.get('tag') or ''}"
                ).strip(),
                description=format_alarm_days(a.get("repeat_mask", 0), lang),
                status=(
                    TodoItemStatus.NEEDS_ACTION if a.get("enabled") else TodoItemStatus.COMPLETED
                ),
            )
            for a in alarms
        ]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        try:
            hour, minute, label = parse_alarm_title(item.summary or "")
            mask = parse_alarm_days(item.description)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.client.create_alarm({
            "hour": hour, "minute": minute, "label": label,
            "repeat_mask": mask, "enabled": item.status != TodoItemStatus.COMPLETED,
        })
        await self.coordinator.async_request_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        current = next((a for a in self.coordinator.data.alarms if a.get("id") == item.uid), None)
        if current is None:
            raise HomeAssistantError("This alarm no longer exists on the device")
        update: dict = {}
        try:
            if item.summary is not None:
                update["hour"], update["minute"], update["label"] = parse_alarm_title(item.summary)
            if item.description is not None:
                lang = self.hass.config.language or "en"
                # Home Assistant hands the unchanged text back on a pure
                # status toggle - keep the current mask in that case.
                if item.description != format_alarm_days(current.get("repeat_mask", 0), lang):
                    update["repeat_mask"] = parse_alarm_days(item.description)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        if item.status is not None:
            update["enabled"] = item.status == TodoItemStatus.NEEDS_ACTION
        if not update:
            return
        await self.coordinator.client.update_alarm(item.uid, update)
        await self.coordinator.async_request_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        for uid in uids:
            await self.coordinator.client.delete_alarm(uid)
        await self.coordinator.async_request_refresh()
