"""Buttons for CODE27."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ...const import (DOMAIN)
from .coordinator import Code27Coordinator
from .entity import Code27Entity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Code27Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            Code27PlaySoundButton(coordinator),
            Code27StopSoundButton(coordinator),
            Code27AcknowledgeNotificationButton(coordinator),
        ]
    )


class Code27PlaySoundButton(Code27Entity, ButtonEntity):
    """Play the device's built-in test sound."""

    _attr_translation_key = "play_sound"

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_play_sound"

    async def async_press(self) -> None:
        await self.coordinator.client.play_sound()


class Code27StopSoundButton(Code27Entity, ButtonEntity):
    """Stop any sound currently playing."""

    _attr_translation_key = "stop_sound"

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_stop_sound"

    async def async_press(self) -> None:
        await self.coordinator.client.stop_sound()


class Code27AcknowledgeNotificationButton(Code27Entity, ButtonEntity):
    """Dismiss the currently blinking "Hinweis" notification, if any."""

    _attr_translation_key = "acknowledge_notification"

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_acknowledge_notification"

    async def async_press(self) -> None:
        await self.coordinator.async_acknowledge_notification()
