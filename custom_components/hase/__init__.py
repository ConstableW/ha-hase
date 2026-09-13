"""HASE — Home Assistant Spellbook Engine."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .const import (
    CONF_ENTRY_KIND,
    ENTRY_KIND_CODE27_DEVICE,
    ENTRY_KIND_SPELLBOOKER,
    ENTRY_KIND_SPELLBOOKER_SATELLITE,
)
from .providers.code27 import async_setup_code27_entry, async_unload_code27_entry

SPELLBOOKER_PLATFORMS = [Platform.CONVERSATION]


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
        return True
    return await async_setup_code27_entry(hass, entry)


async def _async_spellbooker_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.data.get(CONF_ENTRY_KIND, ENTRY_KIND_CODE27_DEVICE) in (
        ENTRY_KIND_SPELLBOOKER,
        ENTRY_KIND_SPELLBOOKER_SATELLITE,
    ):
        return await hass.config_entries.async_unload_platforms(
            entry, SPELLBOOKER_PLATFORMS
        )
    return await async_unload_code27_entry(hass, entry)
