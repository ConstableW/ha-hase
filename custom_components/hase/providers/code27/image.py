"""Portrait image entities for CODE27: one per character slot, plus one
preview per outfit of the active character.

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
from .select import outfit_choices

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

    # The device has three outfits per character (cloth ids 1-3), so these
    # three exist permanently and follow whichever character is active.
    async_add_entities(
        [Code27OutfitPreviewImage(hass, coordinator, cloth_id) for cloth_id in (1, 2, 3)]
    )


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


class Code27OutfitPreviewImage(Code27Entity, ImageEntity):
    """Portrait of outfit N of the *active* character - what the dashboard
    card shows on its outfit chips. Deliberately has no ``slot_number``
    attribute (the card's character list matches portraits by it); the
    owning slot is ``for_slot`` and the outfit's display name ``label``,
    the same text the outfit select offers."""

    _attr_translation_key = "outfit_preview"

    def __init__(
        self, hass: HomeAssistant, coordinator: Code27Coordinator, cloth_id: int
    ) -> None:
        Code27Entity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._cloth_id = cloth_id
        self._attr_translation_placeholders = {"cloth": str(cloth_id)}
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_outfit_{cloth_id}_preview"
        self._image_key = self._current_key()
        self._attr_image_last_updated = dt_util.utcnow()

    def _slot(self) -> int | None:
        return self.coordinator.data.current_slot.get("slot_number")

    def _character(self) -> dict[str, Any]:
        slot = self._slot()
        return next(
            (c for c in self.coordinator.data.character_slots if c.get("slot_number") == slot),
            {},
        )

    def _label(self) -> str | None:
        for label, cloth in outfit_choices(self._character()).items():
            if cloth == self._cloth_id:
                return label
        return None

    def _current_key(self) -> tuple[Any, ...]:
        """What the picture depends on: whose outfit, and its model file."""
        custom = str(self._character().get("custom_cloths") or "").split("\n")
        model = custom[self._cloth_id - 1].strip() if self._cloth_id <= len(custom) else ""
        return (self._slot(), model)

    @property
    def available(self) -> bool:
        return super().available and self._label() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"cloth_id": self._cloth_id, "label": self._label(), "for_slot": self._slot()}

    @callback
    def _handle_coordinator_update(self) -> None:
        key = self._current_key()
        if key != self._image_key:
            self._image_key = key
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        slot = self._slot()
        if slot is None or self._label() is None:
            return None
        try:
            result = await self.coordinator.client.get_character_portrait(slot, self._cloth_id)
        except Code27Error as err:
            _LOGGER.debug("Could not fetch outfit %s preview: %s", self._cloth_id, err)
            return None
        if result is None:
            return None
        content_type, data = result
        self._attr_content_type = content_type
        return data
