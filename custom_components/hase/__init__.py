"""HASE — Home Assistant Spellbook Engine."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.exceptions import ConfigEntryError

from .const import (
    CONF_ENTRY_KIND,
    DOMAIN,
    ENTRY_KIND_CODE27_EXTERNAL_HUB,
    ENTRY_KIND_SPELLBOOKER,
)
from .providers.registry import get_provider_registry

SPELLBOOKER_PLATFORMS = [Platform.CONVERSATION]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the shared Studio once; books and client selections stay separate."""
    get_provider_registry(hass)
    from .studio_api import async_setup_studio
    await async_setup_studio(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a standalone agent or delegate a companion to its provider."""
    kind = entry.data.get(CONF_ENTRY_KIND)
    if kind == ENTRY_KIND_CODE27_EXTERNAL_HUB:
        from .code27_external import Code27ExternalBinding
        from .providers.code27.bridge import async_setup_bridge
        from .providers.code27.services import async_register_services

        binding = Code27ExternalBinding(hass, entry)
        hass.data.setdefault("hase", {})[entry.entry_id] = binding
        await binding.async_start()
        async_setup_bridge(hass, entry, binding)
        async_register_services(hass)
        entry.async_on_unload(entry.add_update_listener(_async_external_update_listener))
        await hass.config_entries.async_forward_entry_setups(entry, SPELLBOOKER_PLATFORMS)
        await _async_studio_entry_loaded(hass, entry)
        return True
    if kind == ENTRY_KIND_SPELLBOOKER:
        entry.async_on_unload(
            entry.add_update_listener(_async_spellbooker_update_listener)
        )
        await hass.config_entries.async_forward_entry_setups(
            entry, SPELLBOOKER_PLATFORMS
        )
        await _async_studio_entry_loaded(hass, entry)
        return True
    provider = get_provider_registry(hass).resolve_entry(entry)
    if provider is None:
        detail = f"entry kind {kind!r}" if kind is not None else "a missing entry kind"
        raise ConfigEntryError(
            f"No installed HASE provider can handle {detail}. "
            "Install the required provider or remove this config entry."
        )
    loaded = await provider.async_setup_entry(hass, entry)
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


async def _async_external_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload satellites after a HASE-owned binding entry changes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    kind = entry.data.get(CONF_ENTRY_KIND)
    if kind == ENTRY_KIND_CODE27_EXTERNAL_HUB:
        unloaded = await hass.config_entries.async_unload_platforms(
            entry, SPELLBOOKER_PLATFORMS
        )
        if unloaded:
            binding = hass.data.get("hase", {}).pop(entry.entry_id, None)
            if binding is not None:
                await binding.async_stop()
            from .providers.code27.services import async_unregister_services

            async_unregister_services(hass)
    elif kind == ENTRY_KIND_SPELLBOOKER:
        unloaded = await hass.config_entries.async_unload_platforms(
            entry, SPELLBOOKER_PLATFORMS
        )
    else:
        provider = get_provider_registry(hass).resolve_entry(entry)
        if provider is None:
            detail = f"entry kind {kind!r}" if kind is not None else "a missing entry kind"
            raise ConfigEntryError(
                f"No installed HASE provider can unload {detail}."
            )
        unloaded = await provider.async_unload_entry(hass, entry)
    if unloaded and getattr(hass, "data", {}).get("hase_studio") is not None:
        from .studio_api import async_entry_unloaded
        async_entry_unloaded(hass, entry)
    return unloaded
