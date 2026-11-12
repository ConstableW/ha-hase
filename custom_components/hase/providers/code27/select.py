"""Select entities for CODE27: light scene, active character and its outfit."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
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
            Code27CharacterOutfitSelect(coordinator),
            Code27AiLanguageSelect(coordinator),
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
        # An ambient colour written from here is shown on top of the applied
        # scene, so no scene is "the" current one - the picker stays empty
        # until a scene is chosen again.
        if getattr(self.coordinator, "ambient_override", False):
            return None
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
        self.coordinator.ambient_override = False
        self.async_write_ha_state()
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


def outfit_choices(character: dict) -> dict[str, int]:
    """Label -> cloth id for a character's selectable outfits.

    ``available_cloths`` is a comma list of cloth ids; a custom model's
    filename (one line per cloth id in ``custom_cloths``) names its outfit,
    stock outfits are just "Outfit N"."""
    available = [
        int(part) for part in str(character.get("available_cloths") or "").split(",")
        if part.strip().isdigit()
    ]
    custom = str(character.get("custom_cloths") or "").split("\n")
    pairs: list[tuple[str, object]] = []
    for cloth in available:
        filename = custom[cloth - 1].strip() if 0 < cloth <= len(custom) else ""
        pairs.append((filename.rsplit(".", 1)[0] if filename else f"Outfit {cloth}", cloth))
    return _dedupe_names(pairs)  # type: ignore[return-value]


class Code27CharacterOutfitSelect(Code27Entity, SelectEntity):
    """Switch the outfit of the currently active character.

    Needs firmware 1.5.3 (`POST /character/outfit/apply`); the device
    reloads the character model for about two seconds afterwards."""

    _attr_translation_key = "character_outfit"

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_character_outfit"

    def _character(self) -> dict:
        slot = self.coordinator.data.current_slot.get("slot_number")
        return next(
            (c for c in self.coordinator.data.character_slots if c.get("slot_number") == slot),
            {},
        )

    @property
    def available(self) -> bool:
        capabilities = self.coordinator.data.system.get("capabilities") or {}
        return (
            super().available
            and capabilities.get("character.model_controls", True) is not False
            and bool(outfit_choices(self._character()))
        )

    @property
    def options(self) -> list[str]:
        return list(outfit_choices(self._character()).keys())

    @property
    def current_option(self) -> str | None:
        selected = self.coordinator.data.current_slot.get("selected_cloth")
        for label, cloth in outfit_choices(self._character()).items():
            if cloth == selected:
                return label
        return None

    async def async_select_option(self, option: str) -> None:
        cloth = outfit_choices(self._character()).get(option)
        slot = self.coordinator.data.current_slot.get("slot_number")
        if cloth is None or slot is None:
            return
        await self.coordinator.async_set_outfit(slot, cloth)


# The codes `PUT /ai-language` accepts in firmware 1.5.3 (its OpenAPI enum).
AI_LANGUAGES: dict[str, str] = {
    "de": "Deutsch", "en": "English", "fr": "Français", "es": "Español", "ja": "日本語",
}


class Code27AiLanguageSelect(Code27Entity, SelectEntity):
    """The language the characters answer in (``ai_language``).

    Options are shown by name; the device takes and reports language codes.
    A code the device reports that is not in the list above is still shown
    as the current option (by its code)."""

    _attr_translation_key = "ai_language_select"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_ai_language_select"

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.data.system.get("ai_language"))

    @property
    def options(self) -> list[str]:
        options = list(AI_LANGUAGES.values())
        current = self.current_option
        if current and current not in options:
            options.append(current)
        return options

    @property
    def current_option(self) -> str | None:
        code = self.coordinator.data.system.get("ai_language")
        return AI_LANGUAGES.get(code, code) if code else None

    async def async_select_option(self, option: str) -> None:
        code = next((c for c, name in AI_LANGUAGES.items() if name == option), None)
        if code is None:
            return
        await self.coordinator.client.set_ai_language(code)
        await self.coordinator.async_request_refresh()
