"""In-process access to one MCP Bridge's Spell catalog and execution.

Same projection / permission / execution path as the Streamable-HTTP
transport (providers/mcp/transport.py), but without a network hop or token:
used by the CODE27 tool-integration executor (providers/code27/tool_executor.py)
so Codie's model can cast the Spells of one Bridge. A Bridge's area scope,
Assist exposure, expert gating and generation leases all apply unchanged.
"""
from __future__ import annotations

import time
from typing import Any

from ...const import CONF_SPELLBOOKER_BOOK, DOMAIN
from ...core.mcp_bridge_context import BridgeConfigurationError
from ...core.mcp_execution import execute_mcp_tool_call
from ...core.mcp_projection import project_spellbook
from ...core.models import ExecutionStatus
from ...execution_trace import record_execution
from ...spellbooker_ha import HaAssistForwarder
from . import DATA_MCP_RUNTIME_SUPERVISOR
from .credentials import BridgeIdentity
from .runtime import BridgeRuntimeUnavailable
from .transport import (
    _bridge_context_from_snapshot,
    _build_executor,
    _load_assigned_book,
)


def _supervisor(hass):
    return hass.data.get(DOMAIN, {}).get("mcp", {}).get(DATA_MCP_RUNTIME_SUPERVISOR)


async def _lease(hass, identity: BridgeIdentity):
    supervisor = _supervisor(hass)
    if supervisor is None:
        raise BridgeRuntimeUnavailable("supervisor_missing")
    generation = supervisor.get_generation(identity)
    if generation is None:
        raise BridgeRuntimeUnavailable("bridge_unregistered")
    return supervisor, await supervisor.resolve_for_request(
        identity, generation, request_id="codie-tool"
    )


async def async_list_spells(hass, identity: BridgeIdentity) -> list[dict[str, Any]]:
    """[{spell_id, description, arguments:{name:{type,required,enum?}}, read_only}]"""
    supervisor, lease = await _lease(hass, identity)
    try:
        context = _bridge_context_from_snapshot(hass, lease.snapshot)
        if isinstance(context, BridgeConfigurationError):
            return []
        book = await _load_assigned_book(hass, lease.snapshot)
        if book is None:
            return []
        projections, _skips = project_spellbook(book, context)
        out = []
        for p in projections:
            props = p.input_schema.get("properties", {})
            required = set(p.input_schema.get("required", []))
            args = {}
            for name, spec in props.items():
                entry: dict[str, Any] = {"type": spec.get("type", "string"), "required": name in required}
                if "enum" in spec:
                    entry["enum"] = spec["enum"]
                args[name] = entry
            out.append({"spell_id": p.spell_id, "description": p.description,
                        "arguments": args, "read_only": p.read_only})
        return out
    finally:
        await supervisor.unregister_in_flight(lease)


async def async_cast_spell(
    hass, identity: BridgeIdentity, spell_id: str, arguments: dict[str, Any] | None
) -> tuple[bool, str]:
    """(ok, text). Never raises for an unusable Bridge - returns (False, reason)."""
    try:
        supervisor, lease = await _lease(hass, identity)
    except BridgeRuntimeUnavailable as err:
        return False, f"bridge_unavailable:{err}"
    try:
        context = _bridge_context_from_snapshot(hass, lease.snapshot)
        if isinstance(context, BridgeConfigurationError):
            return False, f"bridge_misconfigured:{context.reason}"
        book = await _load_assigned_book(hass, lease.snapshot)
        if book is None:
            return False, "no_spellbook_assigned"
        from homeassistant.core import Context

        book_id = lease.snapshot.config.data.get(CONF_SPELLBOOKER_BOOK)
        executor = _build_executor(hass, book_id, lease)
        started_at = time.monotonic()
        result = await execute_mcp_tool_call(
            book, spell_id, arguments or {}, context, executor,
            context=Context(),
            assist_forwarder=HaAssistForwarder(
                hass, before_side_effect=lease.request.commit_side_effect
            ),
        )
        if result.execution_result is not None:
            record_execution(
                hass, channel="codie_tool", book_id=book_id, spell_id=spell_id,
                result=result.execution_result, started_at=started_at, correlation_id=None,
            )
        if not await supervisor.may_publish(lease):
            return False, "bridge_generation_changed"
        if result.status is None:
            return False, f"error:{result.detail}"
        if result.status in (ExecutionStatus.SUCCESS, ExecutionStatus.QUERY_ANSWER):
            return True, result.response_text or "ok"
        from ...core.executor import response_text_for_failure

        text = result.response_text or (
            response_text_for_failure(result.execution_result)
            if result.execution_result is not None else "failed"
        )
        return False, text
    finally:
        await supervisor.unregister_in_flight(lease)
