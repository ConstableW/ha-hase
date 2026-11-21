"""CODE27 provider lifecycle for HASE."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ...const import (
    CONF_ENTRY_KIND,
    DOMAIN,
    ENTRY_KIND_CODE27_DEVICE,
    ENTRY_KIND_SPELLBOOKER_SATELLITE,
    SUBENTRY_TYPE_SATELLITE,
)
from ..provider import ProviderConfigFlow, ProviderDescriptor
from .api import Code27Client
from .tool_bridge import async_setup_tool_bridge
from .const import (
    CONF_LOCAL_SECRET,
    CONF_MACHINE_ID,
    CONF_SCAN_INTERVAL,
    CONF_USER_ID,
    DATA_CODE27,
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
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TODO,
]


async def async_setup_code27_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start a CODE27 device, its Codie tool gateway and satellite platforms."""
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

    await async_setup_tool_bridge(hass, entry, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async_register_services(hass)

    await _async_setup_music(hass, entry, coordinator)

    return True


async def _async_setup_music(hass: HomeAssistant, entry: ConfigEntry, coordinator) -> None:
    """Wire this device into the CODE27 music runtime.

    The runtime lives in ``music_runtime.py`` / ``music_api.py``: it adds a
    HASE-side queue, persistent playlists and queue autoadvance on top of this
    provider's existing music support, and it is what the provider-neutral
    HASEAMP card talks to. It is optional for the device - if it cannot start,
    the device, its entities and the media browser must keep working, so
    failures are logged rather than propagated.
    """
    try:
        from .music_api import async_setup_music_entry
    except ImportError:  # pragma: no cover - harness without package context
        _LOGGER.debug("CODE27 music wiring skipped: provider package context unavailable")
        return
    try:
        await async_setup_music_entry(hass, entry, coordinator)
    except Exception:
        _LOGGER.exception("CODE27 music setup failed for entry %s", entry.entry_id)


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
        # Shared integration infrastructure may remain after the final CODE27
        # coordinator is gone: the provider registry, the MCP bucket and this
        # provider's own bucket (which holds the music runtime - its queue and
        # playlists are HASE-side and intentionally survive a device reload).
        if not any(
            key not in {"provider_registry", "mcp", DATA_CODE27}
            for key in hass.data[DOMAIN]
        ):
            async_unregister_services(hass)
    return unload_ok


async def _async_setup_provider_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    if entry.data.get(CONF_ENTRY_KIND) == ENTRY_KIND_SPELLBOOKER_SATELLITE:
        raise ConfigEntryError("Add a satellite as a subentry of its provider device.")
    return await async_setup_code27_entry(hass, entry)


async def _async_unload_provider_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    if entry.data.get(CONF_ENTRY_KIND) == ENTRY_KIND_SPELLBOOKER_SATELLITE:
        return await hass.config_entries.async_unload_platforms(
            entry, [Platform.CONVERSATION]
        )
    return await async_unload_code27_entry(hass, entry)


def _is_legacy_code27_entry(entry: ConfigEntry) -> bool:
    """Match entries created before CODE27 began persisting entry_kind."""
    return CONF_ENTRY_KIND not in entry.data and CONF_HOST in entry.data


def _config_flow() -> ProviderConfigFlow:
    try:
        flow_mixin = Code27FlowMixin
        options_flow = Code27OptionsFlow
        subentry_flow = SatelliteSubentryFlow
    except NameError:
        from .config_flow import (
            Code27FlowMixin as flow_mixin,
        )
        from .config_flow import (
            Code27OptionsFlow as options_flow,
        )
        from .config_flow import (
            SatelliteSubentryFlow as subentry_flow,
        )

    return ProviderConfigFlow(
        step_id=ENTRY_KIND_CODE27_DEVICE,
        flow_mixin=flow_mixin,
        is_available=lambda hass: True,
        options_flow_factory=options_flow,
        subentry_flows=((SUBENTRY_TYPE_SATELLITE, subentry_flow),),
    )


PROVIDER = ProviderDescriptor(
    id="code27",
    name="CODE27",
    version="1.0.0",
    entry_kinds=frozenset(
        {ENTRY_KIND_CODE27_DEVICE, ENTRY_KIND_SPELLBOOKER_SATELLITE}
    ),
    minimum_hase_version=None,
    async_setup_entry=_async_setup_provider_entry,
    async_unload_entry=_async_unload_provider_entry,
    config_flow=_config_flow(),
    legacy_entry_matcher=_is_legacy_code27_entry,
)
