"""Versioned, ownership-safe external hub surface for HASE consumers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .coordinator import EVENT_ASSISTANT_MESSAGE, EVENT_USER_MESSAGE
from .speech import async_speak_text

EXTERNAL_HUB_CONTRACT_VERSION = 1


@dataclass(frozen=True)
class HubSnapshot:
    available: bool
    current_slot: dict[str, Any]
    system: dict[str, Any]
    character_slots: tuple[dict[str, Any], ...]
    notification_active: bool


class Code27ExternalHub:
    """Public capabilities; no credential or ConfigEntry mutation method."""

    contract_version = EXTERNAL_HUB_CONTRACT_VERSION

    def __init__(self, hass, entry, coordinator) -> None:
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self.entry_id = entry.entry_id

    @property
    def snapshot(self) -> HubSnapshot:
        coordinator = self._coordinator
        data = coordinator.data
        return HubSnapshot(
            available=bool(coordinator.last_update_success and not coordinator.stopping),
            current_slot=dict(data.current_slot) if data else {},
            system=dict(data.system) if data else {},
            character_slots=tuple(dict(slot) for slot in data.character_slots) if data else (),
            notification_active=coordinator.notification_active,
        )

    def subscribe_updates(self, callback: Callable[[], None]) -> Callable[[], None]:
        return self._coordinator.async_add_listener(callback)

    def subscribe_chat(self, callback: Callable[[str, dict[str, Any]], None]) -> Callable[[], None]:
        def listen(kind: str):
            def handler(event) -> None:
                if event.data.get("config_entry_id") == self.entry_id:
                    callback(kind, dict(event.data))
            return handler

        remove_user = self._hass.bus.async_listen(EVENT_USER_MESSAGE, listen("user"))
        remove_assistant = self._hass.bus.async_listen(EVENT_ASSISTANT_MESSAGE, listen("assistant"))

        def unsubscribe() -> None:
            remove_user()
            remove_assistant()

        return unsubscribe

    async def set_tool_dispatcher(self, dispatcher) -> None:
        """Optional contract-v1 addition (firmware >= 1.6.0): a consumer such as
        HASE hands in the catalog/execution behind the Codie tool gateway; None
        withdraws it. Without a dispatcher this device registers no tools."""
        await self._coordinator.async_set_tool_dispatcher(dispatcher)

    async def send_chat_reply(self, text: str, slot: int, language: str | None = None) -> str | None:
        return await self._coordinator.async_send_chat_reply(text, slot, language)

    async def send_chat_image_reply(
        self, image: bytes, filename: str, content_type: str, text: str,
        slot: int, language: str | None = None,
    ):
        return await self._coordinator.async_send_chat_image_reply(
            image, filename, content_type, text, slot, language
        )

    async def speak_text(self, text: str, *, expected_slot: int | None = None) -> None:
        await async_speak_text(self._hass, self._entry, self._coordinator, text, expected_slot=expected_slot)

    async def trigger_notification(self, message: str, color: tuple[int, int, int]):
        return await self._coordinator.async_trigger_notification(message, color)

    async def acknowledge_notification(self) -> None:
        await self._coordinator.async_acknowledge_notification()
