"""Select entities for CODE27: light scene and active character."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ...const import (DOMAIN)
from .coordinator import Code27Coordinator
from .entity import Code27Entity

_LOGGER = logging.getLogger(__name__)


def _dedupe_names(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build a name -> value dict from (name, value) pairs, disambiguating
    duplicate names by appending the value instead of the plain dict
    comprehension this used to be - which silently dropped every entry
    but the last one sharing a name (two light projects, or two character
    slots, saved under the same name on the device itself): the dropdown
    only ever showed one of them, and there was no way to select whichever
    one lost the collision. ids/slot numbers are themselves unique, so
    appending one always resolves the collision."""
    counts: dict[str, int] = {}
    for name, _ in pairs:
        counts[name] = counts.get(name, 0) + 1
    result: dict[str, object] = {}
    for name, value in pairs:
        key = name if counts[name] == 1 else f"{name} ({value})"
        result[key] = value
    return result


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Code27Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            Code27LightProjectSelect(coordinator),
            Code27CharacterSlotSelect(coordinator),
        ]
    )


class Code27LightProjectSelect(Code27Entity, SelectEntity):
    """Pick which saved light project is currently applied."""

    _attr_translation_key = "light_project"

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_light_project"

    def _name_to_id(self) -> dict[str, str]:
        pairs = [
            (project.get("name") or project["id"], project["id"])
            for project in self.coordinator.data.light_projects
        ]
        return _dedupe_names(pairs)

    @property
    def options(self) -> list[str]:
        return list(self._name_to_id().keys())

    @property
    def current_option(self) -> str | None:
        applied_id = self.coordinator.data.applied_light_project.get("projectId")
        if not applied_id:
            return None
        for name, project_id in self._name_to_id().items():
            if project_id == applied_id:
                return name
        return None

    async def async_select_option(self, option: str) -> None:
        project_id = self._name_to_id().get(option)
        if project_id is None:
            return
        await self.coordinator.client.apply_light_project(project_id)
        await self.coordinator.async_request_refresh()


class Code27CharacterSlotSelect(Code27Entity, SelectEntity):
    """Switch which character preset is active."""

    _attr_translation_key = "character_slot"

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_character_slot"

    def _name_to_slot(self) -> dict[str, int]:
        pairs = [
            (slot.get("name") or f"Slot {slot.get('slot_number')}", slot["slot_number"])
            for slot in self.coordinator.data.character_slots
            if slot.get("slot_number") is not None
        ]
        return _dedupe_names(pairs)

    @property
    def options(self) -> list[str]:
        return list(self._name_to_slot().keys())

    @property
    def current_option(self) -> str | None:
        current = self.coordinator.data.current_slot.get("slot_number")
        for name, slot_number in self._name_to_slot().items():
            if slot_number == current:
                return name
        return None

    async def async_select_option(self, option: str) -> None:
        slot_number = self._name_to_slot().get(option)
        if slot_number is None:
            return
        await self.coordinator.client.update_selected_slot(slot_number)
        await self.coordinator.async_request_refresh()
