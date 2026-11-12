"""Binary sensors for CODE27."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ...const import DOMAIN
from .coordinator import Code27Coordinator
from .entity import Code27Entity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Code27Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            Code27MusicModeBinarySensor(coordinator),
            Code27NotificationActiveBinarySensor(coordinator),
            Code27BluetoothConnectedBinarySensor(coordinator),
            Code27Use24HourTimeBinarySensor(coordinator),
            Code27ModelLoadingBinarySensor(coordinator),
            Code27DeviceStateBinarySensor(coordinator, "screen_on"),
            Code27DeviceStateBinarySensor(coordinator, "locked"),
            Code27DeviceStateBinarySensor(coordinator, "conversation_blocked"),
        ]
    )


class Code27MusicModeBinarySensor(Code27Entity, BinarySensorEntity):
    """Whether the music page is currently open on the device screen."""

    _attr_translation_key = "music_mode_active"

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_music_mode_active"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.music_page.get("is_open"))


class Code27NotificationActiveBinarySensor(Code27Entity, BinarySensorEntity):
    """Whether a "Hinweis" notification is currently blinking the LEDs.

    Triggered via the hase.notify service (e.g. from Node-RED) and cleared
    either by the matching hase.notify_clear call or the "Hinweis
    quittieren" button. CODE27 has no such concept itself - this is purely
    tracked on the Home Assistant side.
    """

    _attr_translation_key = "notification_active"

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_notification_active"

    @property
    def is_on(self) -> bool:
        return self.coordinator.notification_active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "message": self.coordinator.notification_message,
            "color": self.coordinator.notification_color,
        }


class Code27BluetoothConnectedBinarySensor(Code27Entity, BinarySensorEntity):
    """Whether a Bluetooth device is currently connected for audio
    playback - from `Code27Data.bluetooth_state` (REST-polled and
    WS-pushed, see coordinator.py)."""

    _attr_translation_key = "bluetooth_connected"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_bluetooth_connected"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.bluetooth_state.get("is_connected"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.coordinator.data.bluetooth_state
        return {
            "device_name": state.get("device_name"),
            "device_address": state.get("device_address"),
            "paired_device_count": state.get("paired_device_count"),
            "is_bluetooth_enabled": state.get("is_bluetooth_enabled"),
            "local_device_name": state.get("local_device_name"),
        }


class Code27Use24HourTimeBinarySensor(Code27Entity, BinarySensorEntity):
    """Whether the device's clock is set to 24-hour time (CODE27 -> Settings
    -> Date & Time), added to `/data/user/system` in app v1.5.1 - on
    older firmware this key is simply absent, so `is_on` reads False."""

    _attr_translation_key = "use_24_hour_time"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_use_24_hour_time"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.system.get("use_24_hour_time"))


class Code27ModelLoadingBinarySensor(Code27Entity, BinarySensorEntity):
    """On while the character model reloads after an outfit switch made
    through Home Assistant (select or `set_outfit`). A switch made on the
    device itself is not reported here."""

    _attr_translation_key = "model_loading"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_model_loading"

    @property
    def is_on(self) -> bool:
        return self.coordinator.model_loading


class Code27DeviceStateBinarySensor(Code27Entity, BinarySensorEntity):
    """One flag of `GET /device/state` (firmware 1.5.3+): ``screen_on``,
    ``locked`` or ``conversation_blocked``. While a block applies, the
    device's own ``block_reason`` / ``display_key`` are exposed as
    attributes. "On" always means the flag is true (so ``locked`` is on
    while the device is locked)."""

    def __init__(self, coordinator: Code27Coordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key = f"device_{key}"
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_device_{key}"

    @property
    def available(self) -> bool:
        return super().available and self._key in self.coordinator.data.device_state

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.device_state.get(self._key))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._key != "conversation_blocked":
            return None
        state = self.coordinator.data.device_state
        return {
            "block_reason": state.get("block_reason") or None,
            "display_key": state.get("display_key") or None,
        }
