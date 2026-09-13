"""Ad-hoc color/effect light for CODE27's LED ring.

This uses the same non-persisted "preview" channel the device's own Light
Editor uses while designing a project (Lab -> Light Editor -> Preview), so
turning this light on/off or changing its color never touches any saved
light project. For picking one of your saved animations, use the "Light
scene" select entity instead.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ...const import (DOMAIN)
from .const import (LIGHT_EFFECT_TO_TYPE)
from .coordinator import Code27Coordinator
from .entity import Code27Entity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Code27Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([Code27PreviewLight(coordinator)])


class Code27PreviewLight(Code27Entity, LightEntity):
    """A plain color light backed by CODE27's ephemeral light-preview channel."""

    _attr_translation_key = "preview_light"
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = list(LIGHT_EFFECT_TO_TYPE)

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_preview_light"

    @property
    def is_on(self) -> bool:
        # Mirrors the master switch, same physical LEDs as the "LED light"
        # switch entity. Color/effect below only describe what HA itself
        # last asked for, since CODE27 has no live preview-state readback.
        return bool(self.coordinator.data.light_master_switch.get("enabled"))

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        # Lives on the coordinator (not here) so a notification's blink can
        # snapshot and restore it - see coordinator.async_acknowledge_notification.
        return self.coordinator.ambient_rgb_color

    @property
    def brightness(self) -> int:
        return self.coordinator.ambient_brightness

    @property
    def effect(self) -> str:
        return self.coordinator.ambient_effect

    def _led_count(self) -> int:
        for project in self.coordinator.data.light_projects:
            if project.get("ledCount"):
                return int(project["ledCount"])
        return 17

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_RGB_COLOR in kwargs:
            self.coordinator.ambient_rgb_color = kwargs[ATTR_RGB_COLOR]
        if ATTR_BRIGHTNESS in kwargs:
            self.coordinator.ambient_brightness = kwargs[ATTR_BRIGHTNESS]
        if ATTR_EFFECT in kwargs and kwargs[ATTR_EFFECT] in LIGHT_EFFECT_TO_TYPE:
            self.coordinator.ambient_effect = kwargs[ATTR_EFFECT]

        if not self.coordinator.data.light_master_switch.get("enabled"):
            await self.coordinator.client.set_light_master_switch(True)

        red, green, blue = self.coordinator.ambient_rgb_color
        await self.coordinator.client.preview_light_color(
            red,
            green,
            blue,
            brightness=self.coordinator.ambient_brightness,
            effect=LIGHT_EFFECT_TO_TYPE[self.coordinator.ambient_effect],
            led_count=self._led_count(),
        )
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.client.stop_light_preview()
        await self.coordinator.client.set_light_master_switch(False)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
