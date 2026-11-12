"""HASE-owned binding to a standalone CODE27 entry, without device ownership."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import CONF_CODE27_EXTERNAL_ENTRY_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)
EXPECTED_CONTRACT_VERSION = 1
_REQUIRED = (
    "snapshot", "subscribe_updates", "subscribe_chat", "send_chat_reply",
    "cleanup_assist_exchange", "speak_text", "trigger_notification",
    "acknowledge_notification",
)
_CALLABLE = tuple(member for member in _REQUIRED if member != "snapshot")
_SNAPSHOT_FIELDS = (
    "available", "current_slot", "system", "character_slots",
    "notification_active",
)
EVENT_USER_MESSAGE = "hase_user_message"
EVENT_ASSISTANT_MESSAGE = "hase_assistant_message"


class Code27ExternalBinding:
    """HASE bridge adapter; all owner mutations remain in CODE27."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.target_entry_id = entry.data[CONF_CODE27_EXTERNAL_ENTRY_ID]
        self.hub: Any | None = None
        self.observed_contract_version: Any | None = None
        self.reason = "not_loaded"
        self.pending_correction = None
        self.bridge_lock = asyncio.Lock()
        self._rebind_lock = asyncio.Lock()
        self._subscriptions: list[Any] = []
        self._state_unsubscribe = None
        self._tasks: set[asyncio.Task] = set()
        self._stopping = False

    @property
    def stopping(self) -> bool:
        return self._stopping or self.hub is None or not self.hub.snapshot.available

    @property
    def data(self):
        snapshot = self.hub.snapshot if self.hub is not None else None
        return SimpleNamespace(
            current_slot=snapshot.current_slot if snapshot else {},
            system=snapshot.system if snapshot else {},
            character_slots=snapshot.character_slots if snapshot else (),
        )

    @property
    def notification_active(self) -> bool:
        return bool(self.hub and self.hub.snapshot.notification_active)

    def async_create_owned_task(self, coroutine, name: str) -> None:
        if self._stopping:
            coroutine.close()
            return
        task = self.hass.async_create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _issue_id(self) -> str:
        return f"code27_external_contract_{self.entry.entry_id}"

    def _set_issue(self, reason: str | None) -> None:
        if reason is None:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id())
            return
        ir.async_create_issue(
            self.hass, DOMAIN, self._issue_id(), is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=(
                "code27_external_missing"
                if reason == "missing_code27_entry"
                else "code27_external_contract"
            ),
            translation_placeholders=(
                {} if reason == "missing_code27_entry" else {"reason": reason}
            ),
        )

    def _unbind(self) -> None:
        for unsubscribe in self._subscriptions:
            unsubscribe()
        self._subscriptions.clear()
        self.hub = None
        self.pending_correction = None

    async def async_rebind(self) -> None:
        async with self._rebind_lock:
            if self._stopping:
                return
            self._unbind()
            target = self.hass.config_entries.async_get_entry(self.target_entry_id)
            if target is None or target.domain != "code27":
                self.reason = "missing_code27_entry"
                self._set_issue(self.reason)
                return
            state = getattr(target.state, "value", target.state)
            if state != "loaded":
                self.reason = "code27_not_loaded"
                self._set_issue(None)
                return
            hub = getattr(target, "runtime_data", None)
            version = getattr(hub, "contract_version", None)
            self.observed_contract_version = version
            if version != EXPECTED_CONTRACT_VERSION:
                self.reason = f"contract_{version!r}_expected_{EXPECTED_CONTRACT_VERSION}"
                _LOGGER.error("External CODE27 hub %s incompatible: %s", self.target_entry_id, self.reason)
                self._set_issue(self.reason)
                return
            snapshot = getattr(hub, "snapshot", None)
            if (
                getattr(hub, "entry_id", None) != target.entry_id
                or any(not callable(getattr(hub, member, None)) for member in _CALLABLE)
                or any(not hasattr(snapshot, field) for field in _SNAPSHOT_FIELDS)
            ):
                self.reason = "contract_shape_incomplete"
                _LOGGER.error("External CODE27 hub %s has an incomplete contract", self.target_entry_id)
                self._set_issue(self.reason)
                return

            def on_chat(kind: str, payload: dict[str, Any]) -> None:
                event_type = EVENT_USER_MESSAGE if kind == "user" else EVENT_ASSISTANT_MESSAGE
                # The standalone event subscriber may run in HA's executor.
                # EventBus.fire marshals the event onto HA's loop from either thread.
                self.hass.bus.fire(event_type, {**payload, "config_entry_id": self.entry.entry_id})

            self.hub = hub
            self._subscriptions.append(hub.subscribe_chat(on_chat))
            self._subscriptions.append(hub.subscribe_updates(lambda: None))
            self.reason = "bound"
            self._set_issue(None)

    async def async_start(self) -> None:
        target = self.hass.config_entries.async_get_entry(self.target_entry_id)
        if target is not None and target.domain == "code27":
            def on_state_change(*_args) -> None:
                self.async_create_owned_task(self.async_rebind(), f"code27_external_rebind_{self.entry.entry_id}")
            self._state_unsubscribe = target.async_on_state_change(on_state_change)
        await self.async_rebind()

    async def async_stop(self) -> None:
        self._stopping = True
        self._unbind()
        if self._state_unsubscribe is not None:
            self._state_unsubscribe()
            self._state_unsubscribe = None
        tasks = set(self._tasks)
        tasks.discard(asyncio.current_task())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._set_issue(None)

    async def async_send_chat_reply(self, text: str, slot: int, language: str | None = None):
        if self.hub is None:
            raise RuntimeError(self.reason)
        return await self.hub.send_chat_reply(text, slot, language)

    async def async_cleanup_assist_exchange(self, slot: int, chat_ids: list[str]):
        if self.hub is None:
            raise RuntimeError(self.reason)
        return await self.hub.cleanup_assist_exchange(slot, chat_ids)

    async def async_speak_text(self, text: str, *, expected_slot: int | None = None) -> None:
        if self.hub is None:
            raise RuntimeError(self.reason)
        await self.hub.speak_text(text, expected_slot=expected_slot)

    async def async_trigger_notification(self, message: str, color: tuple[int, int, int]):
        if self.hub is None:
            raise RuntimeError(self.reason)
        return await self.hub.trigger_notification(message, color)

    async def async_acknowledge_notification(self) -> None:
        if self.hub is None:
            raise RuntimeError(self.reason)
        await self.hub.acknowledge_notification()
