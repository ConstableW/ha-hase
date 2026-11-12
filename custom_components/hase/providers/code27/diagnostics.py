"""Diagnostics support for CODE27.

Exposes coordinator state and config data for Home Assistant's built-in
diagnostics download (Settings → Devices & services → entry → Download
Diagnostics).  Sensitive fields are redacted so the download is safe to
share in a bug report (E6).
"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .coordinator import Code27Coordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a CODE27 config entry."""
    from ...const import DOMAIN  # local import avoids circular dependency

    coordinator: Code27Coordinator | None = (
        hass.data.get(DOMAIN, {}).get(entry.entry_id)
    )

    coordinator_info: dict[str, Any] = {}
    if coordinator is not None and coordinator.data is not None:
        data = coordinator.data
        coordinator_info = {
            "last_update_success": coordinator.last_update_success,
            "stopping": coordinator.stopping,
            "notification_active": coordinator.notification_active,
            "chat_active_slot": coordinator._chat_active_slot,
            "pending_chat_replies_count": len(coordinator._pending_chat_replies),
            "own_chat_ids_count": len(coordinator._own_chat_ids),
            "clock_skew_s": coordinator.client.clock_skew_s,
            "poll_errors": data.poll_errors,
            "proactive_config_available": bool(data.proactive_config),
            # Raw vs. merged is exactly what poll_errors above already
            # distinguishes for THIS field: "music_page" present in
            # poll_errors means the current data.music_page below is the
            # merge-fallback's last-good cache, not a fresh read this
            # cycle (see Code27Coordinator._merge_poll_results). Included
            # for the musikmodus_aktiv-after-reboot investigation.
            "music_page": data.music_page,
        }

    # Deliberate allowlist: never serialize credentials, addresses, device/user
    # IDs, arbitrary options, chat text, or firmware-supplied personal fields.
    return {"coordinator": coordinator_info}


async def async_get_device_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: DeviceEntry,
) -> dict[str, Any]:
    """Return diagnostics for one device page's "Download diagnostics".

    Every device this entry owns (the CODE27 hub itself, and any HASE-Agent
    Satellite device registered under the same entry - see
    HA-CLOSING-AUDIT-2026-09-24's device-diagnostics finding) shares the
    ONE coordinator the entry-level function above already reports on;
    there is nothing meaningfully different to compute per device, so this
    augments that same payload with which device was actually asked for,
    rather than duplicating the coordinator introspection."""
    entry_diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    return {
        "device": {
            "name": device.name_by_user or device.name,
            "model": device.model,
            "manufacturer": device.manufacturer,
            "identifiers": sorted(f"{domain}:{value}" for domain, value in device.identifiers),
        },
        **entry_diagnostics,
    }
