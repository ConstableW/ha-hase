"""Sensors for CODE27."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ...const import DOMAIN
from .coordinator import Code27Coordinator
from .entity import Code27Entity

DIAGNOSTIC_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="ai_language",
        translation_key="ai_language",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="timezone",
        translation_key="timezone",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="build_type",
        translation_key="build_type",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Code27Coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[Code27Entity] = [
        Code27SystemSensor(coordinator, description)
        for description in DIAGNOSTIC_DESCRIPTIONS
    ]
    entities.append(Code27ActiveCharacterSensor(coordinator))
    entities.append(Code27AlarmCountSensor(coordinator))
    entities.append(Code27MemoCountSensor(coordinator))
    entities.append(Code27LastUserMessageSensor(coordinator))
    entities.append(Code27LastAssistantMessageSensor(coordinator))
    entities.append(Code27CharacterOverviewSensor(coordinator))
    entities.append(Code27MusicStorageSensor(coordinator))
    entities.append(Code27CapabilitiesSensor(coordinator))
    async_add_entities(entities)


class Code27SystemSensor(Code27Entity, SensorEntity):
    """Exposes a single field from /data/user/system."""

    def __init__(
        self, coordinator: Code27Coordinator, description: SensorEntityDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_{description.key}"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.system.get(self.entity_description.key)


class Code27ActiveCharacterSensor(Code27Entity, SensorEntity):
    """Name of the currently selected character slot.

    Only the character's display name is exposed here, never its
    personality prompt or other private configuration.
    """

    _attr_translation_key = "active_character"

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_active_character"

    @property
    def native_value(self) -> str | None:
        slot_number = self.coordinator.data.current_slot.get("slot_number")
        for slot in self.coordinator.data.character_slots:
            if slot.get("slot_number") == slot_number:
                return slot.get("name")
        return None


class Code27AlarmCountSensor(Code27Entity, SensorEntity):
    """Number of enabled alarms."""

    _attr_translation_key = "active_alarms"
    _attr_native_unit_of_measurement = "alarms"

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_active_alarms"

    @property
    def native_value(self) -> int:
        return sum(1 for a in self.coordinator.data.alarms if a.get("enabled"))


class Code27MemoCountSensor(Code27Entity, SensorEntity):
    """Number of stored memos / reminders."""

    _attr_translation_key = "memo_count"
    _attr_native_unit_of_measurement = "memos"

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_memo_count"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.memos)


class _Code27LastMessageSensor(Code27Entity, SensorEntity):
    """Base for the last-user/assistant-message sensors.

    HA sensor states are capped at 255 characters, so the state is truncated
    and the untruncated text is also available as an attribute.
    """

    _message_key: str

    @property
    def native_value(self) -> str | None:
        message = getattr(self.coordinator.data, self._message_key)
        content = message.get("content")
        if not content:
            return None
        return content if len(content) <= 255 else content[:252] + "..."

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        message = getattr(self.coordinator.data, self._message_key)
        return {
            "full_text": message.get("content"),
            "chat_id": message.get("chat_id"),
            "created_at": message.get("created_at"),
        }


class Code27LastUserMessageSensor(_Code27LastMessageSensor):
    """The last thing you said to your CODE27 character."""

    _attr_translation_key = "last_user_message"
    _message_key = "last_user_message"

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_last_user_message"


class Code27LastAssistantMessageSensor(_Code27LastMessageSensor):
    """The last thing your CODE27 character said back.

    Also carries the recent conversation (up to ~30 messages, whatever the
    coordinator's chat poller last fetched) as a "history" attribute, for
    dashboards that want to show more than just the latest exchange.
    """

    _attr_translation_key = "last_assistant_message"
    _message_key = "last_assistant_message"

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_last_assistant_message"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = dict(super().extra_state_attributes)
        attrs["history"] = [
            {
                "role": m.get("role"),
                "content": m.get("content"),
                "created_at": m.get("created_at"),
                "chat_id": m.get("chat_id"),
            }
            for m in self.coordinator.data.chat_messages
        ]
        return attrs


class Code27CharacterOverviewSensor(Code27Entity, SensorEntity):
    """Every character slot at a glance - name, whether it's the currently
    active one, and its last message - built from a single history/count
    call, entirely separate from the per-active-slot event pipeline (see
    `Code27Data.character_overview`). Intended for a dashboard card that
    shows all characters, not just the active one.
    """

    _attr_translation_key = "character_overview"
    _attr_native_unit_of_measurement = "characters"

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_character_overview"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.character_overview)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"characters": self.coordinator.data.character_overview}


class Code27MusicStorageSensor(Code27Entity, SensorEntity):
    """TF-card usage - percent used, computed from the WS-pushed
    `music_storage` snapshot (see `Code27Data.music_storage`; no REST
    fallback exists for this yet, so this reads `unknown` until the first
    push after connect, per the audit's own live-confirmed behavior)."""

    _attr_translation_key = "music_storage"
    _attr_native_unit_of_measurement = "%"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_music_storage"

    @property
    def native_value(self) -> int | None:
        storage = self.coordinator.data.music_storage
        total = storage.get("capacity_total_bytes")
        used = storage.get("capacity_used_bytes")
        if not isinstance(total, (int, float)) or not total:
            return None
        if not isinstance(used, (int, float)):
            return None
        return round(used / total * 100)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        storage = self.coordinator.data.music_storage
        return {
            "is_loaded": storage.get("is_loaded"),
            "root_path": storage.get("root_path"),
            "capacity_total_bytes": storage.get("capacity_total_bytes"),
            "capacity_used_bytes": storage.get("capacity_used_bytes"),
            "file_count": len(storage.get("music_files") or []),
        }


class Code27CapabilitiesSensor(Code27Entity, SensorEntity):
    """The device's `capabilities` feature-flag object from `/data/user/system`,
    made visible instead of being silently consumed - Phase 9's "restricted
    capabilities" concern. No feature in this integration is currently
    gated on these flags (every confirmed-live flag on the one tested
    device was enabled) - surfaced as diagnostic data so a device with
    fewer capabilities is at least observable, rather than building
    gating logic that can't be verified without such a device. See
    docs/CAPABILITIES.md for the known flag-to-feature mapping.
    """

    _attr_translation_key = "capabilities"
    _attr_native_unit_of_measurement = "capabilities"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_capabilities"

    @property
    def native_value(self) -> int:
        capabilities = self.coordinator.data.system.get("capabilities") or {}
        return sum(1 for enabled in capabilities.values() if enabled)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"capabilities": self.coordinator.data.system.get("capabilities") or {}}
