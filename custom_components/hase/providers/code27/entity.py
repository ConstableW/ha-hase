"""Base entity for CODE27."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ...const import (DOMAIN)
from .const import (CONF_MACHINE_ID, MANUFACTURER, MODEL)
from .coordinator import Code27Coordinator


class Code27Entity(CoordinatorEntity[Code27Coordinator]):
    """Common device info for all CODE27 entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.data.get(CONF_MACHINE_ID, coordinator.entry.entry_id)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, machine_id)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=coordinator.entry.title,
            configuration_url=f"http://{coordinator.client.host}:{coordinator.client.port}",
        )
