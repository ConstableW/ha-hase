"""MCP Server provider lifecycle for HASE - HASE MCP architecture, Phase 3
(configuration/credentials) and Phase 4 (Streamable HTTP/Uvicorn
transport).
"""
from __future__ import annotations

import asyncio
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from ...const import DOMAIN, ENTRY_KIND_MCP_SERVER, SUBENTRY_TYPE_MCP_BRIDGE
from ..provider import ProviderConfigFlow, ProviderDescriptor
from .const import (
    CONF_MCP_BRIDGE_CREDENTIAL_VERIFIER,
    CONF_MCP_SERVER_PORT,
    DEFAULT_MCP_SERVER_PORT,
)
from .credentials import BridgeIdentity, CredentialIndex
from .runtime import BridgeConfig, BridgeRuntimeSupervisor, UpdateReason
from .secret_store import McpSecretStore

_LOGGER = logging.getLogger(__name__)

DATA_MCP_SECRET_STORE = "mcp_secret_store"
DATA_MCP_CREDENTIAL_INDEX = "mcp_credential_index"
DATA_MCP_SERVER_ENTRY_ID = "mcp_server_entry_id"
DATA_MCP_UVICORN_SERVER = "mcp_uvicorn_server"
DATA_MCP_UVICORN_TASK = "mcp_uvicorn_task"
DATA_MCP_LISTENER_PORT = "mcp_listener_port"
DATA_MCP_LISTENER_RESTART_LOCK = "mcp_listener_restart_lock"
DATA_MCP_RUNTIME_SUPERVISOR = "mcp_runtime_supervisor"

_TRANSPORT_STOP_TIMEOUT_SECONDS = 10
_TRANSPORT_START_TIMEOUT_SECONDS = 15


def _hass_mcp_bucket(hass: HomeAssistant) -> dict:
    return hass.data.setdefault(DOMAIN, {}).setdefault("mcp", {})


async def async_refresh_bridge_runtime(
    hass: HomeAssistant,
    identity: BridgeIdentity,
) -> None:
    """Reconcile one published Bridge with its current persisted config.

    The update listener is the normal writer. This request-time check also
    closes the small interval before that listener runs and keeps direct
    test/config-entry mutations truthful without reverting to a global
    listener restart or credential-index rebuild.
    """
    bucket = _hass_mcp_bucket(hass)
    supervisor = bucket.get(DATA_MCP_RUNTIME_SUPERVISOR)
    if supervisor is None:
        return
    entry = hass.config_entries.async_get_entry(identity.entry_id)
    subentry = entry.subentries.get(identity.subentry_id) if entry is not None else None
    if subentry is None or subentry.subentry_type != SUBENTRY_TYPE_MCP_BRIDGE:
        await supervisor.remove_bridge(identity)
        index = bucket.get(DATA_MCP_CREDENTIAL_INDEX)
        if index is not None:
            index.remove_identity(identity)
        return
    config = BridgeConfig.from_values(subentry.title, subentry.data)
    verifier = subentry.data.get(CONF_MCP_BRIDGE_CREDENTIAL_VERIFIER, "")
    snapshot = supervisor.get_snapshot(identity)
    if snapshot is None:
        supervisor.register_bridge(identity, config, None, verifier)
        index = bucket.get(DATA_MCP_CREDENTIAL_INDEX)
        if index is not None and verifier:
            index.put(verifier, identity)
        return
    if snapshot.config != config or snapshot.credential_verifier != verifier:
        reason = (
            UpdateReason.CREDENTIALS_CHANGED
            if snapshot.credential_verifier != verifier
            else UpdateReason.CONFIG_CHANGED
        )
        await supervisor.update_bridge(identity, config, None, verifier, reason)
        index = bucket.get(DATA_MCP_CREDENTIAL_INDEX)
        if index is not None:
            index.remove_identity(identity)
            if verifier:
                index.put(verifier, identity)


async def async_rebuild_credential_index(hass: HomeAssistant, entry: ConfigEntry) -> CredentialIndex:
    """Synchronize Bridge runtimes and keep the legacy index view current.

    The supervisor's immutable publication is authoritative.  The
    CredentialIndex remains in hass.data for compatibility with existing
    diagnostics/tests; authentication no longer reads it.
    """
    bucket = _hass_mcp_bucket(hass)
    bucket.setdefault(DATA_MCP_SECRET_STORE, McpSecretStore(hass))
    supervisor = bucket.setdefault(DATA_MCP_RUNTIME_SUPERVISOR, BridgeRuntimeSupervisor())
    index = bucket.setdefault(DATA_MCP_CREDENTIAL_INDEX, CredentialIndex())
    current: set[BridgeIdentity] = set()
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_MCP_BRIDGE:
            continue
        identity = BridgeIdentity(entry_id=entry.entry_id, subentry_id=subentry.subentry_id)
        current.add(identity)
        config = BridgeConfig.from_values(subentry.title, subentry.data)
        verifier = subentry.data.get(CONF_MCP_BRIDGE_CREDENTIAL_VERIFIER, "")
        previous = supervisor.get_snapshot(identity)
        if previous is None:
            supervisor.register_bridge(identity, config, None, verifier)
            _LOGGER.info(
                "MCP Bridge runtime registered bridge=%s generation=1 reason=%s",
                identity.subentry_id,
                UpdateReason.REGISTERED.value,
            )
        elif previous.config != config or previous.credential_verifier != verifier:
            reason = (
                UpdateReason.CREDENTIALS_CHANGED
                if previous.credential_verifier != verifier
                else UpdateReason.CONFIG_CHANGED
            )
            await supervisor.update_bridge(identity, config, None, verifier, reason)
        index.remove_identity(identity)
        if verifier:
            index.put(verifier, identity)

    for identity in supervisor.bridge_ids():
        if identity.entry_id == entry.entry_id and identity not in current:
            await supervisor.remove_bridge(identity)
            index.remove_identity(identity)

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
    (and what real-instance testing caught twice: first the
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
    bucket = _hass_mcp_bucket(hass)
    previous = bucket.get(DATA_MCP_UVICORN_TASK)
    if previous is not None and not previous.done():
        raise ConfigEntryError("The previous MCP listener has not stopped")
    port = entry.options.get(CONF_MCP_SERVER_PORT, DEFAULT_MCP_SERVER_PORT)
    uv_server = await hass.async_add_executor_job(_build_uvicorn_server, hass, port)
    task = asyncio.create_task(_serve_uvicorn(uv_server))
    try:
        async with asyncio.timeout(_TRANSPORT_START_TIMEOUT_SECONDS):
            while not task.done() and not (
                uv_server.started and any(
                    server.sockets for server in getattr(uv_server, "servers", ())
                )
            ):
                await asyncio.sleep(0.01)
        if task.done():
            await task
        if not uv_server.started or not any(
            server.sockets for server in getattr(uv_server, "servers", ())
        ):
            raise ConfigEntryError("MCP server exited before binding a listener")
    except BaseException as exc:
        stopped = await _stop_uvicorn(uv_server, task)
        if not stopped:
            # Retain ownership for a later unload/retry; the port is never
            # published as healthy when startup or cleanup failed.
            bucket[DATA_MCP_UVICORN_SERVER] = uv_server
            bucket[DATA_MCP_UVICORN_TASK] = task
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise ConfigEntryError(f"HASE MCP listener failed to start on port {port}") from exc
    bucket[DATA_MCP_UVICORN_SERVER] = uv_server
    bucket[DATA_MCP_UVICORN_TASK] = task
    bucket[DATA_MCP_LISTENER_PORT] = port
    task.add_done_callback(lambda done: _listener_done(hass, done))
    _LOGGER.info("HASE MCP Server listening on port %s (%s)", port, "/mcp")


def _listener_done(hass: HomeAssistant, task: asyncio.Task) -> None:
    """Never leave a stopped listener advertised as active."""
    bucket = _hass_mcp_bucket(hass)
    if bucket.get(DATA_MCP_UVICORN_TASK) is task:
        bucket.pop(DATA_MCP_LISTENER_PORT, None)
        if not bucket[DATA_MCP_UVICORN_SERVER].should_exit:
            _LOGGER.error("HASE MCP listener stopped unexpectedly")


async def _serve_uvicorn(uv_server) -> None:
    """Convert Uvicorn's bind-failure sys.exit into a normal task failure."""
    try:
        await uv_server.serve()
    except SystemExit as exc:
        raise RuntimeError("Uvicorn could not start the MCP listener") from exc


async def _stop_uvicorn(uv_server, task: asyncio.Task | None) -> bool:
    """Request termination without allowing a cancellation-resistant task to hang HA."""
    if uv_server is not None:
        uv_server.should_exit = True
        for server in tuple(getattr(uv_server, "servers", ()) or ()):
            server.close()
    if task is None:
        return True
    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=_TRANSPORT_STOP_TIMEOUT_SECONDS)
    if not done:
        _LOGGER.warning("HASE MCP Server transport did not stop within %ss", _TRANSPORT_STOP_TIMEOUT_SECONDS)
        return False
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        _LOGGER.exception("HASE MCP listener task raised during shutdown")
    return True


async def async_stop_transport(hass: HomeAssistant) -> bool:
    """Cleanly stops the listener and releases the port - called on both
    unload and (via async_unload_entry, ahead of a fresh
    async_setup_entry) reload, so a reload never leaves an orphaned
    listener bound to the old port behind."""
    bucket = _hass_mcp_bucket(hass)
    uv_server = bucket.get(DATA_MCP_UVICORN_SERVER)
    task = bucket.get(DATA_MCP_UVICORN_TASK)
    stopped = await _stop_uvicorn(uv_server, task)
    bucket.pop(DATA_MCP_LISTENER_PORT, None)
    if stopped:
        bucket.pop(DATA_MCP_UVICORN_SERVER, None)
        bucket.pop(DATA_MCP_UVICORN_TASK, None)
    return stopped


async def async_restart_transport(hass: HomeAssistant, entry: ConfigEntry, *, reason: str) -> None:
    """Restart only the listener, awaiting complete teardown before bind."""
    bucket = _hass_mcp_bucket(hass)
    lock = bucket.setdefault(DATA_MCP_LISTENER_RESTART_LOCK, asyncio.Lock())
    async with lock:
        started = time.monotonic()
        stopped = await async_stop_transport(hass)
        if not stopped:
            raise ConfigEntryError("The previous MCP listener did not stop")
        await async_start_transport(hass, entry)
        _LOGGER.info(
            "HASE MCP listener restarted reason=%s stopped=%s duration_ms=%d",
            reason,
            stopped,
            round((time.monotonic() - started) * 1000),
        )


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
    try:
        await async_rebuild_credential_index(hass, entry)
        await async_check_bridge_health(hass, entry)
        await async_start_transport(hass, entry)
    except BaseException:
        await async_unload_mcp_server_entry(hass, entry)
        raise
    entry.async_on_unload(entry.add_update_listener(_async_mcp_server_update_listener))
    return True


async def async_unload_mcp_server_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await async_stop_transport(hass):
        return False
    bucket = _hass_mcp_bucket(hass)
    bucket.pop(DATA_MCP_CREDENTIAL_INDEX, None)
    bucket.pop(DATA_MCP_SERVER_ENTRY_ID, None)
    bucket.pop(DATA_MCP_LISTENER_RESTART_LOCK, None)
    bucket.pop(DATA_MCP_RUNTIME_SUPERVISOR, None)
    return True


async def _async_mcp_server_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    bucket = _hass_mcp_bucket(hass)
    previous_port = bucket.get(DATA_MCP_LISTENER_PORT)
    current_port = entry.options.get(CONF_MCP_SERVER_PORT, DEFAULT_MCP_SERVER_PORT)
    await async_rebuild_credential_index(hass, entry)
    await async_check_bridge_health(hass, entry)
    if previous_port != current_port:
        await async_restart_transport(hass, entry, reason="listener_port_changed")


def _config_flow() -> ProviderConfigFlow:
    from .config_flow import (
        McpBridgeSubentryFlow,
        McpFlowMixin,
        McpServerOptionsFlow,
        async_mcp_server_exists,
    )

    return ProviderConfigFlow(
        step_id=ENTRY_KIND_MCP_SERVER,
        flow_mixin=McpFlowMixin,
        is_available=lambda hass: not async_mcp_server_exists(hass),
        options_flow_factory=McpServerOptionsFlow,
        subentry_flows=((SUBENTRY_TYPE_MCP_BRIDGE, McpBridgeSubentryFlow),),
    )


PROVIDER = ProviderDescriptor(
    id="mcp",
    name="Model Context Protocol Server",
    version="1.0.0",
    entry_kinds=frozenset({ENTRY_KIND_MCP_SERVER}),
    minimum_hase_version=None,
    async_setup_entry=async_setup_mcp_server_entry,
    async_unload_entry=async_unload_mcp_server_entry,
    config_flow=_config_flow(),
)
