"""CODE27 provider lifecycle for HASE."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import Code27Client
from .bridge import async_setup_bridge
from ...const import DOMAIN, SUBENTRY_TYPE_SATELLITE
from .const import (
    CONF_LOCAL_SECRET,
    CONF_MACHINE_ID,
    CONF_SCAN_INTERVAL,
    CONF_USER_ID,
    DEFAULT_SCAN_INTERVAL,
)
from .coordinator import Code27Coordinator
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    # A CODE27 device entry may own SATELLITE subentries
    # (SUBENTRY_TYPE_SATELLITE) - conversation.py's async_setup_entry
    # creates one ConversationEntity per such subentry, alongside this
    # entry's own normal device platforms. A device entry with no
    # satellite subentries just adds zero conversation entities here,
    # never an error.
    Platform.CONVERSATION,
    Platform.IMAGE,
    Platform.LIGHT,
    Platform.MEDIA_PLAYER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TODO,
]


async def async_setup_code27_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start a CODE27 device, its bridge and satellite platforms."""
    session = async_get_clientsession(hass)
    client = Code27Client(
        session,
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_USER_ID],
        entry.data[CONF_LOCAL_SECRET],
        entry.data.get(CONF_MACHINE_ID),
    )

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = Code27Coordinator(hass, entry, client, scan_interval)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    # Snapshot of this entry's SATELLITE subentries at setup time - see
    # _async_update_listener's own note on why this is what actually
    # notices a satellite being added/edited/removed and reloads for
    # it, since conversation.py's async_setup_entry only ever reads
    # subentries once, at setup.
    coordinator.known_satellite_subentries = _satellite_subentries_snapshot(entry)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    coordinator.async_start_realtime()
    entry.async_on_unload(coordinator.async_stop_realtime)

    async_setup_bridge(hass, entry, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async_register_services(hass)

    return True


def _satellite_subentries_snapshot(entry: ConfigEntry) -> dict[str, tuple[str, dict]]:
    """(title, data) per SATELLITE subentry this entry owns - used to
    notice one being added, edited or removed, none of which
    conversation.py's async_setup_entry (which only ever reads
    subentries once, at setup) would otherwise pick up without a
    reload."""
    return {
        subentry_id: (subentry.title, dict(subentry.data))
        for subentry_id, subentry in entry.subentries.items()
        if subentry.subentry_type == SUBENTRY_TYPE_SATELLITE
    }


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # Only the polling interval, and a SATELLITE subentry actually
    # changing, need the entry reloaded. Everything else the options
    # flow can change (bridge settings, spellbook selection, ...) is
    # already read fresh from entry.options on every chat message, so
    # reloading for those would just cause an unnecessary interruption
    # - and defeat "switch spellbook, no reload".
    #
    # A subentry's own flow (SatelliteSubentryFlow in config_flow.py)
    # deliberately never reloads directly - async_update_reload_and_abort
    # would raise, since this entry always has an update_listener (this
    # one) - so detecting the change here, generically, via this
    # listener is the only place it can happen for both adding a new
    # satellite and reconfiguring an existing one.
    coordinator: Code27Coordinator = hass.data[DOMAIN][entry.entry_id]
    new_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    new_satellites = _satellite_subentries_snapshot(entry)
    if (
        new_interval != coordinator.update_interval.total_seconds()
        or new_satellites != coordinator.known_satellite_subentries
    ):
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_code27_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await hass.data[DOMAIN][entry.entry_id].async_stop_realtime()
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            async_unregister_services(hass)
    return unload_ok
