"""MCP Server provider lifecycle for HASE - HASE MCP architecture, Phase 3
(configuration/credentials) and Phase 4 (Streamable HTTP/Uvicorn
transport).
"""
from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from ...const import DOMAIN, SUBENTRY_TYPE_MCP_BRIDGE
from .const import (
    CONF_MCP_BRIDGE_CREDENTIAL_VERIFIER,
    CONF_MCP_SERVER_PORT,
    DEFAULT_MCP_SERVER_PORT,
)
from .credentials import BridgeIdentity, CredentialIndex
from .secret_store import McpSecretStore

_LOGGER = logging.getLogger(__name__)

DATA_MCP_SECRET_STORE = "mcp_secret_store"
DATA_MCP_CREDENTIAL_INDEX = "mcp_credential_index"
DATA_MCP_SERVER_ENTRY_ID = "mcp_server_entry_id"
DATA_MCP_UVICORN_SERVER = "mcp_uvicorn_server"
DATA_MCP_UVICORN_TASK = "mcp_uvicorn_task"

_TRANSPORT_STOP_TIMEOUT_SECONDS = 10


def _hass_mcp_bucket(hass: HomeAssistant) -> dict:
    return hass.data.setdefault(DOMAIN, {}).setdefault("mcp", {})


async def async_rebuild_credential_index(hass: HomeAssistant, entry: ConfigEntry) -> CredentialIndex:
    """Rebuild the runtime verifier -> BridgeIdentity index from every
    currently-configured Bridge subentry on this MCP Server entry.

    Called on setup and on every reload - any Bridge add/edit/regenerate/
    delete reloads the whole owning entry via its own update listener
    below, the same "any change reloads" convention conversation.py
    already documents for every other spellbooker entry - so the index is
    always rebuilt from the CURRENT persisted state, never incrementally
    patched. That's what makes a stale in-memory entry (surviving past a
    credential's regeneration or a Bridge's deletion) impossible by
    construction, rather than something a cache-invalidation step has to
    get right."""
    bucket = _hass_mcp_bucket(hass)
    bucket.setdefault(DATA_MCP_SECRET_STORE, McpSecretStore(hass))
    index = CredentialIndex()
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_MCP_BRIDGE:
            continue
        verifier = subentry.data.get(CONF_MCP_BRIDGE_CREDENTIAL_VERIFIER)
        if verifier:
            index.put(verifier, BridgeIdentity(entry_id=entry.entry_id, subentry_id=subentry.subentry_id))
    bucket[DATA_MCP_CREDENTIAL_INDEX] = index
    bucket[DATA_MCP_SERVER_ENTRY_ID] = entry.entry_id
    return index


def _build_uvicorn_server(hass: HomeAssistant, port: int):
    """The synchronous half of starting the transport: importing
    `uvicorn`/`mcp` for the first time (real disk I/O + module
    compilation, e.g. `mcp.server.auth.settings`'s own pydantic import
    chain, and uvicorn's own lazy `http_protocol_class` import) and
    building the Starlette app / uvicorn.Server object. Deliberately never
    called directly from async code - see async_start_transport, which
    runs this in the executor instead. A cold import running straight on
    the event loop is exactly what HA's own blocking-call detector flags
    (and what real UAT against testzwerg caught twice: first the
    mcp/pydantic import chain here, then - once that was moved off the
    loop - uvicorn.Server._serve() still calling config.load() itself,
    which does its OWN lazy import of the HTTP protocol implementation).
    Calling config.load() ourselves, right here in the executor, is what
    closes the second one: _serve() only calls it "if not config.loaded",
    so by the time .serve() actually runs on the event loop there is
    nothing left for it to import."""
    import uvicorn

    from .transport import build_streamable_http_app

    _server, app = build_streamable_http_app(hass)
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    config.load()
    return uvicorn.Server(config)


async def async_start_transport(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Starts the Streamable HTTP listener as a background asyncio task on
    HA's own event loop - the Phase 0-proven shape (uvicorn.Server.serve()
    as a plain asyncio task), never the Phase 0B aiohttp shim. `uvicorn`/
    `mcp` are imported lazily (function-local, see _build_uvicorn_server)
    so nothing outside this one code path needs them importable - every
    other module in this integration, and every existing test, stays
    completely unaffected by whether these packages are installed - and
    that lazy import is pushed onto the executor so the (real, one-time)
    disk I/O it does never blocks the event loop."""
    port = entry.options.get(CONF_MCP_SERVER_PORT, DEFAULT_MCP_SERVER_PORT)
    uv_server = await hass.async_add_executor_job(_build_uvicorn_server, hass, port)
    task = asyncio.create_task(uv_server.serve())
    bucket = _hass_mcp_bucket(hass)
    bucket[DATA_MCP_UVICORN_SERVER] = uv_server
    bucket[DATA_MCP_UVICORN_TASK] = task
    _LOGGER.info("HASE MCP Server listening on port %s (%s)", port, "/mcp")


async def async_stop_transport(hass: HomeAssistant) -> None:
    """Cleanly stops the listener and releases the port - called on both
    unload and (via async_unload_entry, ahead of a fresh
    async_setup_entry) reload, so a reload never leaves an orphaned
    listener bound to the old port behind."""
    bucket = _hass_mcp_bucket(hass)
    uv_server = bucket.pop(DATA_MCP_UVICORN_SERVER, None)
    task = bucket.pop(DATA_MCP_UVICORN_TASK, None)
    if uv_server is not None:
        uv_server.should_exit = True
    if task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_TRANSPORT_STOP_TIMEOUT_SECONDS)
        except TimeoutError:
            _LOGGER.warning("HASE MCP Server transport did not stop within %ss; cancelling", _TRANSPORT_STOP_TIMEOUT_SECONDS)
            task.cancel()
        except asyncio.CancelledError:
            pass


async def async_check_bridge_health(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Resolves every Bridge subentry's current BridgeContext once, purely
    for its repair-issue side effect (see bridge_context.
    build_bridge_context_for_subentry / .repairs) - so a Bridge whose area
    was deleted is flagged as broken even if no MCP client ever calls in,
    not only reactively on the next real request. Also clears repair
    issues for any Bridge that no longer exists."""
    from .bridge_context import build_bridge_context_for_subentry
    from .repairs import gc_stale_bridge_issues

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_MCP_BRIDGE:
            continue
        build_bridge_context_for_subentry(
            hass, subentry.data, default_language=hass.config.language or "en",
            entry_id=entry.entry_id, subentry_id=subentry.subentry_id, bridge_name=subentry.title,
        )
    gc_stale_bridge_issues(hass, entry_id=entry.entry_id, current_subentry_ids=entry.subentries)


async def async_setup_mcp_server_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry.async_on_unload(entry.add_update_listener(_async_mcp_server_update_listener))
    await async_rebuild_credential_index(hass, entry)
    await async_check_bridge_health(hass, entry)
    await async_start_transport(hass, entry)
    return True


async def async_unload_mcp_server_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await async_stop_transport(hass)
    bucket = _hass_mcp_bucket(hass)
    bucket.pop(DATA_MCP_CREDENTIAL_INDEX, None)
    bucket.pop(DATA_MCP_SERVER_ENTRY_ID, None)
    return True


async def _async_mcp_server_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
