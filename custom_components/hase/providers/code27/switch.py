"""Switches for CODE27: one per alarm.

The LED master on/off used to be its own switch entity here, but that's
redundant with the "Umgebungsfarbe" light entity (light.py), which already
turns the same master switch on/off as part of its own on/off. Kept as one
control instead of two competing toggles for the same physical LEDs.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ...const import (DOMAIN)
from .const import CONF_ALARM_SWITCHES
from .coordinator import Code27Coordinator
from .entity import Code27Entity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Code27Coordinator = hass.data[DOMAIN][entry.entry_id]

    # Per-alarm switches are optional (CONF_ALARM_SWITCHES, on by default).
    # Switched off, any that exist are removed from the registry for good;
    # switched on, switches whose alarm no longer exists (deleted while Home
    # Assistant was not running) are cleaned up once at startup - but only
    # after a poll that really read the alarms, so a failed read can never
    # wipe live switches.
    registry = er.async_get(hass)
    machine_id = entry.unique_id or entry.entry_id
    prefix = f"{machine_id}_alarm_"
    existing = [
        e for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.domain == "switch" and (e.unique_id or "").startswith(prefix)
    ]
    if not entry.options.get(CONF_ALARM_SWITCHES, True):
        for registered in existing:
            registry.async_remove(registered.entity_id)
        return
    if "alarms" not in coordinator.data.poll_errors:
        live_ids = {a["id"] for a in coordinator.data.alarms if a.get("id")}
        for registered in existing:
            if registered.unique_id[len(prefix):] not in live_ids:
                registry.async_remove(registered.entity_id)

    # Keyed by alarm_id -> the live entity, not just a bare set of known
    # ids: the previous version only ever dropped a vanished id from its
    # tracking set, never actually removed the entity itself, so a
    # deleted alarm's switch stayed registered forever showing
    # "unavailable" (available already correctly reflects nonexistence,
    # but the entity itself never went away). Worse, since the id was
    # gone from the tracking set, an alarm reappearing under the same id
    # later triggered a second async_add_entities() for a unique_id that
    # (as far as the registry was concerned) already existed.
    known_alarms: dict[str, Code27AlarmSwitch] = {}

    @callback
    def _sync_alarms() -> None:
        current_ids = {a["id"] for a in coordinator.data.alarms if a.get("id")}

        new_ids = current_ids - known_alarms.keys()
        if new_ids:
            new_entities = {
                alarm_id: Code27AlarmSwitch(coordinator, alarm_id) for alarm_id in new_ids
            }
            known_alarms.update(new_entities)
            async_add_entities(new_entities.values())

        vanished_ids = known_alarms.keys() - current_ids
        for alarm_id in vanished_ids:
            entity = known_alarms.pop(alarm_id)
            # async_remove() is a coroutine; _sync_alarms runs as a plain
            # coordinator-listener callback, so schedule it rather than
            # awaiting directly. force_remove=True also clears the entity
            # registry entry, not just the live platform - otherwise the
            # same alarm_id reappearing later would still collide with a
            # lingering (if inactive) registry entry.
            hass.async_create_task(
                entity.async_remove(force_remove=True),
                name=f"code27_remove_alarm_{alarm_id}",
            )

    _sync_alarms()
    entry.async_on_unload(coordinator.async_add_listener(_sync_alarms))


class Code27AlarmSwitch(Code27Entity, SwitchEntity):
    """Enable/disable a single alarm defined on the device."""

    _attr_translation_key = "alarm"

    def __init__(self, coordinator: Code27Coordinator, alarm_id: str) -> None:
        super().__init__(coordinator)
        self._alarm_id = alarm_id
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_alarm_{alarm_id}"

    def _alarm(self) -> dict[str, Any]:
        for alarm in self.coordinator.data.alarms:
            if alarm.get("id") == self._alarm_id:
                return alarm
        return {}

    @property
    def available(self) -> bool:
        return super().available and bool(self._alarm())

    @property
    def name(self) -> str:
        alarm = self._alarm()
        label = alarm.get("label") or alarm.get("tag")
        hour = alarm.get("hour", 0)
        minute = alarm.get("minute", 0)
        time_str = f"{hour:02d}:{minute:02d}"
        return f"{label} ({time_str})" if label else f"Alarm {time_str}"

    @property
    def is_on(self) -> bool:
        return bool(self._alarm().get("enabled"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        alarm = self._alarm()
        return {
            "alarm_id": alarm.get("id"),
            "hour": alarm.get("hour"),
            "minute": alarm.get("minute"),
            "repeat_mask": alarm.get("repeat_mask"),
            "label": alarm.get("label"),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.client.enable_alarm(self._alarm_id)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.client.disable_alarm(self._alarm_id)
        await self.coordinator.async_request_refresh()
