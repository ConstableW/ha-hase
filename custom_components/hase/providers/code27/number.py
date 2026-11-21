"""Number entities for CODE27 (firmware >= 1.6.0): display brightness."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
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
    async_add_entities([Code27BrightnessNumber(coordinator)])


class Code27BrightnessNumber(Code27Entity, NumberEntity):
    """Screen brightness 0-100 (GET/PUT /data/user/device/brightness)."""

    _attr_translation_key = "display_brightness"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_display_brightness"

    @property
    def available(self) -> bool:
        data = self.coordinator.data.device_brightness
        return super().available and bool(data) and data.get("writable", True) is not False

    @property
    def native_value(self) -> float | None:
        level = self.coordinator.data.device_brightness.get("level")
        return float(level) if isinstance(level, (int, float)) else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.client.set_device_brightness(int(value))
        await self.coordinator.async_request_refresh()
