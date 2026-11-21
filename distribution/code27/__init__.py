"""Standalone CODE27 integration, generated from HASE's CODE27 device source."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import Code27Client
from .const import (
    CONF_LOCAL_SECRET,
    CONF_MACHINE_ID,
    CONF_SCAN_INTERVAL,
    CONF_USER_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import Code27Coordinator
from .external_hub import Code27ExternalHub
from .music_api import async_setup_music_entry
from .services import async_register_services, async_unregister_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CARDS_VERSION = "0.1.0"

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.LIGHT,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TODO,
]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    await _async_register_cards(hass)
    return True


async def _async_register_cards(hass: HomeAssistant) -> None:
    """Serve the dashboard cards from the integration and load them automatically.

    HACS only installs custom_components/<domain>/, so the cards live here
    instead of in a separate www folder.
    """
    from pathlib import Path

    from homeassistant.components import frontend
    from homeassistant.components.http import StaticPathConfig

    await hass.http.async_register_static_paths([
        StaticPathConfig("/code27-cards", str(Path(__file__).parent / "cards"), False),
    ])
    for card in ("haseamp-card.js", "code27-card.js"):
        frontend.add_extra_js_url(hass, f"/code27-cards/{card}?v={CARDS_VERSION}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = Code27Client(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_USER_ID],
        entry.data[CONF_LOCAL_SECRET],
        entry.data.get(CONF_MACHINE_ID),
    )
    coordinator = Code27Coordinator(
        hass, entry, client, entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.runtime_data = Code27ExternalHub(hass, entry, coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    coordinator.async_start_realtime()
    entry.async_on_unload(coordinator.async_stop_realtime)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)
    await async_setup_music_entry(hass, entry, coordinator)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None and (
        entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        != coordinator.update_interval.total_seconds()
    ):
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if coordinator is not None:
            await coordinator.async_stop_realtime()
        if not any(isinstance(value, Code27Coordinator) for value in hass.data.get(DOMAIN, {}).values()):
            async_unregister_services(hass)
    return unloaded
