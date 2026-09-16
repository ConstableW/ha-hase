"""HASE — Home Assistant Spellbook Engine."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .const import (
    CONF_ENTRY_KIND,
    ENTRY_KIND_CODE27_DEVICE,
    ENTRY_KIND_MCP_SERVER,
    ENTRY_KIND_SPELLBOOKER,
    ENTRY_KIND_SPELLBOOKER_SATELLITE,
)
from .providers.code27 import async_setup_code27_entry, async_unload_code27_entry
from .providers.mcp import async_setup_mcp_server_entry, async_unload_mcp_server_entry

SPELLBOOKER_PLATFORMS = [Platform.CONVERSATION]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the shared Studio once; books and client selections stay separate."""
    from .studio_api import async_setup_studio
    await async_setup_studio(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a standalone agent or delegate a companion to its provider."""
    kind = entry.data.get(CONF_ENTRY_KIND, ENTRY_KIND_CODE27_DEVICE)
    if kind == ENTRY_KIND_SPELLBOOKER_SATELLITE:
        raise ConfigEntryError("Add a satellite as a subentry of its CODE27 device.")
    if kind == ENTRY_KIND_SPELLBOOKER:
        entry.async_on_unload(
            entry.add_update_listener(_async_spellbooker_update_listener)
        )
        await hass.config_entries.async_forward_entry_setups(
            entry, SPELLBOOKER_PLATFORMS
        )
        await _async_studio_entry_loaded(hass, entry)
        return True
    if kind == ENTRY_KIND_MCP_SERVER:
        # Infrastructure only - no Spellbook/area/scope of its own, no
        # platforms to forward. Its Bridge subentries are what Studio-
        # adjacent tooling would care about, exactly like a CODE27
        # device's own Satellite subentries.
        loaded = await async_setup_mcp_server_entry(hass, entry)
        if loaded:
            await _async_studio_entry_loaded(hass, entry)
        return loaded
    loaded = await async_setup_code27_entry(hass, entry)
    if loaded:
        await _async_studio_entry_loaded(hass, entry)
    return loaded


async def _async_studio_entry_loaded(hass, entry):
    if getattr(hass, "data", {}).get("hase_studio") is not None:
        from .studio_api import async_entry_loaded
        await async_entry_loaded(hass, entry)


async def _async_spellbooker_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    kind = entry.data.get(CONF_ENTRY_KIND, ENTRY_KIND_CODE27_DEVICE)
    if kind in (ENTRY_KIND_SPELLBOOKER, ENTRY_KIND_SPELLBOOKER_SATELLITE):
        unloaded = await hass.config_entries.async_unload_platforms(
            entry, SPELLBOOKER_PLATFORMS
        )
    elif kind == ENTRY_KIND_MCP_SERVER:
        unloaded = await async_unload_mcp_server_entry(hass, entry)
    else:
        unloaded = await async_unload_code27_entry(hass, entry)
    if unloaded and getattr(hass, "data", {}).get("hase_studio") is not None:
        from .studio_api import async_entry_unloaded
        async_entry_unloaded(hass, entry)
    return unloaded
