"""One portrait image entity per character slot for CODE27.

Resolution order (custom thumbnail -> stock portrait -> unavailable) lives
in api.py's `get_character_portrait()` - this entity just wires that up to
HA's image platform, re-fetching whenever a given slot's selected cloth
changes (not on every coordinator poll - the underlying image itself only
ever changes for that one reason). Each entity exposes its own
`slot_number` as an attribute so consumers (the dashboard card) can map a
slot to its portrait without depending on entity_id/translation naming.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from ...const import (DOMAIN)
from .api import Code27Error
from .coordinator import Code27Coordinator
from .entity import Code27Entity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Code27Coordinator = hass.data[DOMAIN][entry.entry_id]

    # Same dynamic add/remove pattern as switch.py's per-alarm switches -
    # character slots essentially never change on this hardware, but this
    # keeps the entity set honest if one ever does (renamed/removed) rather
    # than assuming the slot list at startup is permanent.
    known_slots: dict[int, Code27PortraitImage] = {}

    @callback
    def _sync_slots() -> None:
        current = {
            character["slot_number"]
            for character in coordinator.data.character_slots
            if character.get("slot_number") is not None
        }

        new_slots = current - known_slots.keys()
        if new_slots:
            new_entities = {
                slot: Code27PortraitImage(hass, coordinator, slot) for slot in new_slots
            }
            known_slots.update(new_entities)
            async_add_entities(new_entities.values())

        vanished = known_slots.keys() - current
        for slot in vanished:
            entity = known_slots.pop(slot)
            hass.async_create_task(
                entity.async_remove(force_remove=True),
                name=f"code27_remove_portrait_{slot}",
            )

    _sync_slots()
    entry.async_on_unload(coordinator.async_add_listener(_sync_slots))


class Code27PortraitImage(Code27Entity, ImageEntity):
    """One character slot's portrait, custom-model thumbnail preferred."""

    _attr_translation_key = "character_portrait"

    def __init__(
        self, hass: HomeAssistant, coordinator: Code27Coordinator, slot_number: int
    ) -> None:
        Code27Entity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._slot_number = slot_number
        self._attr_translation_placeholders = {"slot": str(slot_number)}
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_character_{slot_number}_portrait"
        self._cloth_id = self._current_cloth_id()
        self._attr_image_last_updated = dt_util.utcnow()

    def _current_cloth_id(self) -> int | None:
        for character in self.coordinator.data.character_slots:
            if character.get("slot_number") == self._slot_number:
                return character.get("selected_cloth")
        return None

    @property
    def available(self) -> bool:
        return super().available and self._current_cloth_id() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"slot_number": self._slot_number}

    @callback
    def _handle_coordinator_update(self) -> None:
        cloth_id = self._current_cloth_id()
        if cloth_id != self._cloth_id:
            self._cloth_id = cloth_id
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        if self._cloth_id is None:
            return None
        try:
            result = await self.coordinator.client.get_character_portrait(
                self._slot_number, self._cloth_id
            )
        except Code27Error as err:
            _LOGGER.debug(
                "Could not fetch portrait for slot %s: %s", self._slot_number, err
            )
            return None
        if result is None:
            return None
        content_type, data = result
        self._attr_content_type = content_type
        return data
