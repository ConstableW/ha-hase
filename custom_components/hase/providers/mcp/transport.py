"""Streamable HTTP MCP transport - HASE MCP architecture, Phase 4.

Production wiring: the official low-level `mcp.server.lowlevel.Server`
over Streamable HTTP, served by uvicorn on its own dedicated internal
port - the architecture proven in Phase 0. Never the Phase 0B ASGI/
aiohttp shim (see that phase's own report for why it was rejected for
production).

Every `tools/list` and `tools/call` request re-authenticates its bearer
token against the CURRENT providers.mcp.credentials.CredentialIndex and
re-resolves its Bridge's configuration - including its area, against the
CURRENT Home Assistant area registry - fresh, every single time (see
`_resolve_current_bridge` below). Nothing about a Bridge's identity,
Spellbook, area or scope is ever cached across requests: a credential
regenerated or a Bridge deleted stops resolving on the very next request,
and a Bridge's configured area being removed from the registry turns it
into a reported misconfiguration on the very next request - no reload of
the MCP Server or its Bridges needed for any of this to take effect.

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
client is ever told to expect a push it won't get. Clients still see any
change (Spellbook edit, credential rotation, Bridge add/edit/delete, area
now-invalid) on their very next `tools/list` call regardless, because
nothing about a Bridge is ever cached across requests (see above) - the
one-request-later staleness window this leaves is accepted as the cost of
the simpler, session-free transport.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ...const import CONF_SPELLBOOKER_BOOK, DOMAIN, SUBENTRY_TYPE_MCP_BRIDGE
from ...core.executor import ActionExecutor
from ...core.mcp_bridge_context import BridgeConfigurationError
from ...core.mcp_execution import execute_mcp_tool_call
from ...core.mcp_projection import BridgeContext, McpToolProjection, project_spellbook
from ...core.models import ExecutionStatus
from ...spellbook_loader import async_get_spellbook
from ...spellbooker_ha import HaAssistForwarder, HaServiceCaller, HaTargetResolver
from . import DATA_MCP_CREDENTIAL_INDEX, DATA_MCP_SECRET_STORE
from .bridge_context import build_bridge_context_for_subentry
from .const import STREAMABLE_HTTP_PATH
from .credentials import BridgeIdentity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ResolvedBridge:
    identity: BridgeIdentity
    subentry: Any  # homeassistant.config_entries.ConfigSubentry
    context: BridgeContext | BridgeConfigurationError


def _identity_from_claims(claims: dict[str, Any] | None) -> BridgeIdentity | None:
    if not claims:
        return None
    entry_id = claims.get("entry_id")
    subentry_id = claims.get("subentry_id")
    if not entry_id or not subentry_id:
        return None
    return BridgeIdentity(entry_id=entry_id, subentry_id=subentry_id)


async def _resolve_current_bridge(hass, identity: BridgeIdentity) -> _ResolvedBridge | None:
    """Re-resolves EVERYTHING about a Bridge from its current, live
    configuration - called fresh on every single request (see this
    module's own docstring). Returns None only when the Bridge subentry
    itself is gone (deleted, or the whole MCP Server entry is gone) - a
    live configuration problem with its AREA is a distinct
    BridgeConfigurationError, never conflated with "Bridge doesn't exist"."""
    entry = hass.config_entries.async_get_entry(identity.entry_id)
    if entry is None:
        return None
    subentry = entry.subentries.get(identity.subentry_id)
    if subentry is None or subentry.subentry_type != SUBENTRY_TYPE_MCP_BRIDGE:
        return None
    context = build_bridge_context_for_subentry(
        hass, subentry.data, default_language=hass.config.language or "en",
        entry_id=identity.entry_id, subentry_id=identity.subentry_id, bridge_name=subentry.title,
    )
    return _ResolvedBridge(identity=identity, subentry=subentry, context=context)


async def _load_assigned_book(hass, subentry):
    book_id = subentry.data.get(CONF_SPELLBOOKER_BOOK)
    if not book_id:
        return None
    return await async_get_spellbook(hass, book_id)


def _build_executor(hass, book_id: str) -> ActionExecutor:
    return ActionExecutor(
        HaTargetResolver(hass),
        HaServiceCaller(hass),
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


def _call_result(result):
    """Maps core.mcp_execution.McpExecutionResult onto MCP's own
    CallToolResult/TextContent - HASE's own rendered response text (a
    success) or its own status/reason_code (anything else), never a
    second, MCP-specific wording system. `status is None` covers the two
    outcomes that never reached real execution at all (see
    McpExecutionResult's own docstring): unknown tool, or arguments that
    couldn't build a valid plan."""
    from mcp import types

    if result.status is None:
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"error: {result.detail}")], is_error=True)
    if result.status in (ExecutionStatus.SUCCESS, ExecutionStatus.QUERY_ANSWER):
        return types.CallToolResult(content=[types.TextContent(type="text", text=result.response_text or "")])
    reason = result.execution_result.reason_code if result.execution_result is not None else "unknown"
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=f"{result.status.value}: {reason}")], is_error=True,
    )


def build_mcp_server(hass):
    """Builds the low-level Server with hass captured by closure - never
    a module-level/global hass reference, so nothing here assumes there
    is only ever one MCP Server in the process (there's a UI-level
    singleton, not a code-level one)."""
    from mcp import types
    from mcp.server.auth.middleware.auth_context import get_access_token
    from mcp.server.lowlevel.server import Server

    async def on_list_tools(ctx, params):
        access_token = get_access_token()
        identity = _identity_from_claims(access_token.claims if access_token else None)
        if identity is None:
            _LOGGER.warning("MCP tools/list: no authenticated Bridge identity for this request")
            return types.ListToolsResult(tools=[])

        resolved = await _resolve_current_bridge(hass, identity)
        if resolved is None:
            _LOGGER.warning("MCP tools/list: Bridge %s no longer exists", identity.subentry_id)
            return types.ListToolsResult(tools=[])
        if isinstance(resolved.context, BridgeConfigurationError):
            _LOGGER.warning(
                "MCP tools/list: Bridge %s is misconfigured (%s) - exposing no tools until repaired",
                identity.subentry_id, resolved.context.reason,
            )
            return types.ListToolsResult(tools=[])

        book = await _load_assigned_book(hass, resolved.subentry)
        if book is None:
            return types.ListToolsResult(tools=[])

        projections, skips = project_spellbook(book, resolved.context)
        for skip in skips:
            _LOGGER.debug("MCP tools/list: spell %s not projectable (%s)", skip.spell_id, skip.reason)
        return types.ListToolsResult(tools=[_tool_from_projection(p) for p in projections])

    async def on_call_tool(ctx, params):
        access_token = get_access_token()
        identity = _identity_from_claims(access_token.claims if access_token else None)
        if identity is None:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="error: unauthenticated")], is_error=True,
            )

        resolved = await _resolve_current_bridge(hass, identity)
        if resolved is None:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="error: bridge_not_found")], is_error=True,
            )
        if isinstance(resolved.context, BridgeConfigurationError):
            # Never silently treated as an area-less MASTER Bridge - a
            # configured-but-invalid area blocks execution outright.
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"error: bridge_misconfigured:{resolved.context.reason}")],
                is_error=True,
            )

        book = await _load_assigned_book(hass, resolved.subentry)
        if book is None:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="error: no_spellbook_assigned")], is_error=True,
            )

        from homeassistant.core import Context

        book_id = resolved.subentry.data.get(CONF_SPELLBOOKER_BOOK)
        executor = _build_executor(hass, book_id)
        result = await execute_mcp_tool_call(
            book, params.name, params.arguments or {}, resolved.context, executor, context=Context(),
            assist_forwarder=HaAssistForwarder(hass),
        )
        return _call_result(result)

    return Server(
        "hase-mcp",
        version="1.0.0",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def build_streamable_http_app(hass, *, streamable_http_path: str = STREAMABLE_HTTP_PATH):
    """Returns (Server, Starlette app) - the Starlette app is what
    uvicorn actually serves. Bearer auth via the SDK's own TokenVerifier
    hook, resolved against the runtime CredentialIndex - see
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
    providers.mcp.credentials.CredentialIndex - the exact seam Phase 3
    was built for. The client never supplies a bridge_id anywhere in this
    path: identity comes entirely from which verifier the token's HMAC
    matches, resolved fresh against whatever the index currently holds -
    a credential regenerated or a Bridge deleted since the index was last
    rebuilt (on MCP Server setup/reload) stops resolving immediately,
    because the index itself was rebuilt at that point, not because this
    verifier does anything clever per call."""

    def __init__(self, hass) -> None:
        self._hass = hass

    async def verify_token(self, token: str):
        from mcp.server.auth.provider import AccessToken

        bucket = self._hass.data.get(DOMAIN, {}).get("mcp", {})
        index = bucket.get(DATA_MCP_CREDENTIAL_INDEX)
        secret_store = bucket.get(DATA_MCP_SECRET_STORE)
        if index is None or secret_store is None:
            return None
        secret = await secret_store.async_get_secret()
        identity = index.resolve(secret, token)
        if identity is None:
            return None
        return AccessToken(
            token=token,
            client_id=f"{identity.entry_id}:{identity.subentry_id}",
            scopes=[],
            subject=identity.subentry_id,
            claims={"entry_id": identity.entry_id, "subentry_id": identity.subentry_id},
        )
