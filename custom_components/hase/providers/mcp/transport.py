"""Streamable HTTP MCP transport - HASE MCP architecture, Phase 4.

Production wiring: the official low-level `mcp.server.lowlevel.Server`
over Streamable HTTP, served by uvicorn on its own dedicated internal
port - the architecture proven in Phase 0. Never the Phase 0B ASGI/
aiohttp shim (see that phase's own report for why it was rejected for
production).

Every `tools/list` and `tools/call` request re-authenticates its bearer
token against the CURRENT immutable runtime publication, then leases one
Bridge generation. Configuration decisions for that request use only the
leased snapshot. Live Home Assistant registries are still evaluated from
that snapshot, so deleted areas become a reported misconfiguration on the
next request without mixing configuration generations.

Tool selection is never natural-language matching: `tools/call` names an
exact spell_id, resolved via core.spellbook_engine.find_command_by_id and
executed via core.mcp_execution.execute_mcp_tool_call - the existing HASE
plan/executor machinery, unchanged. core.router.SpellbookRouter is never
imported here.

`tools/list_changed` push notifications are deliberately NOT implemented.
The SDK (see mcp.server.lowlevel.server.Server.get_capabilities) only
advertises `tools.listChanged=True` for protocol 2026-07-28+ when a
`subscriptions/listen` request handler is registered - that handler holds
a long-lived subscription stream per client and pushes future changes
into it, which requires exactly the per-connection session state that
`stateless_http=True` (this module's deliberate Phase 0 transport choice
- see the module docstring above) exists to avoid. Registering it would
mean re-introducing session affinity for a feature most real MCP clients
don't honour anyway (see Phase 0's own client-compatibility research).
Since no `subscriptions/listen` handler is ever registered here, the SDK
correctly and automatically advertises `tools.listChanged=False` - no
client is ever told to expect a push it won't get. Clients see changes on
their next request through the current Bridge generation; an individual
request continues against its captured immutable snapshot and may publish
only if that generation is still current.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ...const import CONF_SPELLBOOKER_BOOK, DOMAIN
from ...core import home_overview
from ...core.executor import ActionExecutor
from ...core.mcp_bridge_context import BridgeConfigurationError
from ...core.mcp_execution import execute_mcp_tool_call
from ...core.mcp_projection import McpToolProjection, project_spellbook
from ...core.models import ExecutionStatus
from ...core.spellbook_engine import find_command_by_id
from ...execution_trace import record_execution
from ...spellbook_loader import async_get_spellbook
from ...spellbooker_ha import (
    HaAssistForwarder,
    HaServiceCaller,
    HaTargetResolver,
    collect_home_overview,
)
from . import (
    DATA_MCP_RUNTIME_SUPERVISOR,
    DATA_MCP_SECRET_STORE,
    async_refresh_bridge_runtime,
)
from .bridge_context import build_bridge_context_for_subentry
from .const import STREAMABLE_HTTP_PATH
from .credentials import BridgeIdentity, compute_verifier
from .runtime import (
    BridgeRuntimeSupervisor,
    BridgeRuntimeUnavailable,
    BridgeSnapshot,
    RequestLease,
)

_LOGGER = logging.getLogger(__name__)


def _identity_from_claims(
    claims: dict[str, Any] | None,
) -> tuple[BridgeIdentity, int] | None:
    if not claims:
        return None
    entry_id = claims.get("entry_id")
    subentry_id = claims.get("subentry_id")
    generation = claims.get("bridge_generation")
    if not entry_id or not subentry_id or not isinstance(generation, int):
        return None
    return BridgeIdentity(entry_id=entry_id, subentry_id=subentry_id), generation


def _bridge_context_from_snapshot(hass, snapshot: BridgeSnapshot):
    """Resolve live HA registry facts from one immutable Bridge config."""
    return build_bridge_context_for_subentry(
        hass,
        snapshot.config.data,
        default_language=hass.config.language or "en",
        entry_id=snapshot.bridge_id.entry_id,
        subentry_id=snapshot.bridge_id.subentry_id,
        bridge_name=snapshot.config.title,
    )


async def _load_assigned_book(hass, snapshot: BridgeSnapshot):
    book_id = snapshot.config.data.get(CONF_SPELLBOOKER_BOOK)
    if not book_id:
        return None
    return await async_get_spellbook(hass, book_id)


def _build_executor(hass, book_id: str, lease: RequestLease) -> ActionExecutor:
    return ActionExecutor(
        HaTargetResolver(hass),
        HaServiceCaller(hass, before_side_effect=lease.request.commit_side_effect),
        expert_allowed=lambda: hass.data.get("hase_studio_permissions", {}).get(book_id, False) is True,
    )


def _tool_from_projection(projection: McpToolProjection):
    from mcp import types

    return types.Tool(
        name=projection.name,
        description=projection.description,
        input_schema=projection.input_schema,
        # read_only_hint is a hint only - core.executor's own action/query
        # AreaPolicy split is what actually enforces this, unchanged by
        # what gets advertised here (see McpToolProjection.read_only's own
        # docstring).
        annotations=types.ToolAnnotations(read_only_hint=projection.read_only),
    )


def _overview_tool(language: str):
    """The built-in read-only home overview (core.home_overview). Offered
    next to the Spells unless a Spell already uses its name - a Spell id
    always wins, so no existing book changes meaning."""
    from mcp import types

    return types.Tool(
        name=home_overview.TOOL_NAME,
        description=home_overview.tool_description(language),
        input_schema=home_overview.input_schema(language),
        annotations=types.ToolAnnotations(read_only_hint=True),
    )


def _overview_result(hass, context, arguments: dict):
    from mcp import types

    area = arguments.get("area")
    domain = arguments.get("domain")
    areas, entities = collect_home_overview(hass)
    text = home_overview.render_overview(
        areas,
        entities,
        context.policy,
        language=context.language,
        area=area if isinstance(area, str) and area.strip() else None,
        domain=domain if isinstance(domain, str) else None,
    )
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)])


def _call_result(result):
    """Maps core.mcp_execution.McpExecutionResult onto MCP's own
    CallToolResult/TextContent - HASE's own rendered response text (a
    success) or its own status/reason_code (anything else), never a
    second, MCP-specific wording system. `status is None` covers the two
    outcomes that never reached real execution at all (see
    McpExecutionResult's own docstring): unknown tool, or arguments that
    couldn't build a valid plan."""
    from mcp import types

    from ...core.executor import response_text_for_failure

    if result.status is None:
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"error: {result.detail}")], is_error=True)
    if result.status in (ExecutionStatus.SUCCESS, ExecutionStatus.QUERY_ANSWER):
        return types.CallToolResult(content=[types.TextContent(type="text", text=result.response_text or "")])
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=(
            result.response_text
            or (
                response_text_for_failure(result.execution_result)
                if result.execution_result is not None else "Die Aktion konnte nicht abgeschlossen werden."
            )
        ))],
        is_error=True,
    )


def build_mcp_server(hass):
    """Builds the low-level Server with hass captured by closure - never
    a module-level/global hass reference, so nothing here assumes there
    is only ever one MCP Server in the process (there's a UI-level
    singleton, not a code-level one)."""
    from mcp import types
    from mcp.server.auth.middleware.auth_context import get_access_token
    from mcp.server.lowlevel.server import Server

    def runtime_supervisor() -> BridgeRuntimeSupervisor | None:
        return hass.data.get(DOMAIN, {}).get("mcp", {}).get(DATA_MCP_RUNTIME_SUPERVISOR)

    async def begin_request(ctx) -> tuple[BridgeRuntimeSupervisor, RequestLease] | None:
        access_token = get_access_token()
        authenticated = _identity_from_claims(access_token.claims if access_token else None)
        supervisor = runtime_supervisor()
        if authenticated is None or supervisor is None:
            return None
        identity, generation = authenticated
        try:
            lease = await supervisor.resolve_for_request(
                identity,
                generation,
                request_id=getattr(ctx, "request_id", None),
            )
        except BridgeRuntimeUnavailable:
            return None
        return supervisor, lease

    async def on_list_tools(ctx, params):
        active = await begin_request(ctx)
        if active is None:
            _LOGGER.warning("MCP tools/list: no authenticated Bridge identity for this request")
            return types.ListToolsResult(tools=[])
        supervisor, lease = active
        try:
            context = _bridge_context_from_snapshot(hass, lease.snapshot)
            if isinstance(context, BridgeConfigurationError):
                _LOGGER.warning(
                    "MCP tools/list: Bridge %s is misconfigured (%s) - exposing no tools until repaired",
                    lease.snapshot.bridge_id.subentry_id,
                    context.reason,
                )
                return types.ListToolsResult(tools=[])
            book = await _load_assigned_book(hass, lease.snapshot)
            if book is None:
                return types.ListToolsResult(tools=[])
            projections, skips = project_spellbook(book, context)
            for skip in skips:
                _LOGGER.debug("MCP tools/list: spell %s not projectable (%s)", skip.spell_id, skip.reason)
            if not await supervisor.may_publish(lease):
                return types.ListToolsResult(tools=[])
            tools = [_tool_from_projection(p) for p in projections]
            if find_command_by_id(book, home_overview.TOOL_NAME) is None:
                tools.append(_overview_tool(context.language))
            return types.ListToolsResult(tools=tools)
        except asyncio.CancelledError:
            return types.ListToolsResult(tools=[])
        finally:
            await supervisor.unregister_in_flight(lease)

    async def on_call_tool(ctx, params):
        active = await begin_request(ctx)
        if active is None:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="error: unauthenticated")], is_error=True,
            )
        supervisor, lease = active
        try:
            context = _bridge_context_from_snapshot(hass, lease.snapshot)
            if isinstance(context, BridgeConfigurationError):
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=f"error: bridge_misconfigured:{context.reason}")],
                    is_error=True,
                )
            book = await _load_assigned_book(hass, lease.snapshot)
            if book is None:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text="error: no_spellbook_assigned")], is_error=True,
                )
            if (
                params.name == home_overview.TOOL_NAME
                and find_command_by_id(book, home_overview.TOOL_NAME) is None
            ):
                overview = _overview_result(hass, context, params.arguments or {})
                if not await supervisor.may_publish(lease):
                    return types.CallToolResult(
                        content=[types.TextContent(type="text", text="error: bridge_generation_changed")],
                        is_error=True,
                    )
                return overview
            from homeassistant.core import Context

            book_id = lease.snapshot.config.data.get(CONF_SPELLBOOKER_BOOK)
            executor = _build_executor(hass, book_id, lease)
            started_at = time.monotonic()
            result = await execute_mcp_tool_call(
                book,
                params.name,
                params.arguments or {},
                context,
                executor,
                context=Context(),
                assist_forwarder=HaAssistForwarder(
                    hass,
                    before_side_effect=lease.request.commit_side_effect,
                ),
            )
            if result.execution_result is not None:
                record_execution(
                    hass, channel="mcp", book_id=book_id, spell_id=params.name,
                    result=result.execution_result, started_at=started_at,
                    correlation_id=str(getattr(ctx, "request_id", "")) or None,
                )
            if not await supervisor.may_publish(lease):
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text="error: bridge_generation_changed")],
                    is_error=True,
                )
            return _call_result(result)
        except asyncio.CancelledError:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="error: bridge_generation_changed")],
                is_error=True,
            )
        finally:
            await supervisor.unregister_in_flight(lease)

    return Server(
        "hase-mcp",
        version="1.0.0",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def build_streamable_http_app(hass, *, streamable_http_path: str = STREAMABLE_HTTP_PATH):
    """Returns (Server, Starlette app) - the Starlette app is what
    uvicorn actually serves. Bearer auth via the SDK's own TokenVerifier
    hook, resolved against the runtime supervisor publication - see
    HaTokenVerifier below. `AuthSettings.issuer_url` is a required field
    of the SDK's model even though HASE runs no OAuth authorization
    server at all (see the Phase 0 report); the value is never
    dereferenced by anything, it exists only to satisfy the model."""
    from mcp.server.auth.settings import AuthSettings

    server = build_mcp_server(hass)
    auth_settings = AuthSettings(
        issuer_url="https://hase.invalid/no-oauth-server-here",
        resource_server_url=None,
        validate_token_resource=False,
    )
    app = server.streamable_http_app(
        auth=auth_settings,
        token_verifier=HaTokenVerifier(hass),
        stateless_http=True,
        host="0.0.0.0",
        streamable_http_path=streamable_http_path,
    )
    return server, app


class HaTokenVerifier:
    """mcp.server.auth.provider.TokenVerifier backed by the runtime
    supervisor's immutable credential/generation publication. The client
    never supplies a bridge_id: identity and generation come entirely
    from the matching current verifier."""

    def __init__(self, hass) -> None:
        self._hass = hass

    async def verify_token(self, token: str):
        from mcp.server.auth.provider import AccessToken

        bucket = self._hass.data.get(DOMAIN, {}).get("mcp", {})
        supervisor = bucket.get(DATA_MCP_RUNTIME_SUPERVISOR)
        secret_store = bucket.get(DATA_MCP_SECRET_STORE)
        if supervisor is None or secret_store is None:
            return None
        secret = await secret_store.async_get_secret()
        resolved = supervisor.resolve_credential(compute_verifier(secret, token))
        if resolved is None:
            return None
        binding, _snapshot = resolved
        await async_refresh_bridge_runtime(self._hass, binding.bridge_id)
        resolved = supervisor.resolve_credential(compute_verifier(secret, token))
        if resolved is None:
            return None
        binding, _snapshot = resolved
        identity = binding.bridge_id
        return AccessToken(
            token=token,
            client_id=f"{identity.entry_id}:{identity.subentry_id}",
            scopes=[],
            subject=identity.subentry_id,
            claims={
                "entry_id": identity.entry_id,
                "subentry_id": identity.subentry_id,
                "bridge_generation": binding.generation,
            },
        )
