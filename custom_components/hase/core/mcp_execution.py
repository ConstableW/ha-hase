"""MCP execution adapter - HASE MCP architecture, Phase 2.

Executes an ALREADY-SELECTED Spell - identified by spell_id, exactly as an
authenticated MCP Bridge's tools/call names it - through the existing HASE
plan/execution machinery: core.spellbook_engine's build_draft/
build_query_plan/build_step_plan, then core.executor.ActionExecutor - or,
for a text-only Spell (no operation/sources/steps), core.
text_only_execution.execute_text_only_command - the SAME shared function
core.router.SpellbookRouter also calls directly for a spoken/typed
HASE-Agent turn, never a second implementation. A tools/call for a
text-only Spell always performs the Spell's configured function one way or
the other; it is never satisfied by merely returning the command/question
text back to the caller.

Deliberately never runs core.spellbook_engine.find_command()/match() (text/
fuzzy matching) and never touches core.router.SpellbookRouter or its
InteractionStateStore/clarification machinery. An MCP tool call has already
named the exact Spell - there is nothing left to disambiguate, and the
session/clarification machinery that exists purely to resolve AMBIGUOUS
NATURAL LANGUAGE does not apply to a call that was never natural language
matching in the first place. Every genuine security/authorization boundary
downstream of that (Assist exposure, action/query AreaPolicy, expert-tier
gating, target resolution, executor argument/shape validation) is entirely
unaffected: this module reaches it through the exact same ActionExecutor
any other frontend uses, never a parallel path.

No Home Assistant imports - same boundary as the rest of core/. The real
ActionExecutor (wired to HaTargetResolver/HaServiceCaller by whichever
provider module wires up the MCP transport in a later phase) is passed in
by the caller, exactly like core.router.SpellbookRouter already takes an
injected ActionExecutor rather than constructing one itself.
"""
from __future__ import annotations

from typing import Any

from .executor import ActionExecutor, response_text_for_success
from .mcp_projection import (
    BridgeContext,
    ProjectionSkip,
    build_captures,
    project_command,
)
from .models import ExecutionResult, ExecutionStatus
from .spellbook_engine import (
    EngineCommand,
    EngineSpellbook,
    build_draft,
    build_query_plan,
    build_step_plan,
    find_command_by_id,
)
from .text_only_execution import (
    AssistForwarder,
    AssistForwardOutcome,
    CommandExecutionResult,
)
from .text_only_execution import execute_text_only_command as _execute_text_only_command

# Re-exported under this module's own historical name - MCP's own test
# suite and any other MCP-specific caller keep working unchanged; the
# real dataclass now lives in core.text_only_execution (a neutral module
# both this adapter and core.router.SpellbookRouter depend on, never one
# depending on the other's own adapter-specific module - see that
# module's own docstring for why this split exists).
McpExecutionResult = CommandExecutionResult
# Re-exported for the same reason (existing imports of AssistForwarder/
# AssistForwardOutcome FROM this module keep working).
__all__ = [
    "AssistForwarder", "AssistForwardOutcome", "McpExecutionResult",
    "execute_mcp_tool_call",
]


def _unresolved(detail: str) -> McpExecutionResult:
    return McpExecutionResult(status=None, response_text=None, execution_result=None, detail=detail)


def _finish(execution: ExecutionResult, *, catchphrase: str | None = None) -> McpExecutionResult:
    if execution.status is ExecutionStatus.SUCCESS:
        text = catchphrase or response_text_for_success(execution)
        return McpExecutionResult(status=execution.status, response_text=text, execution_result=execution)
    if execution.status is ExecutionStatus.QUERY_ANSWER:
        return McpExecutionResult(
            status=execution.status, response_text=response_text_for_success(execution), execution_result=execution
        )
    return McpExecutionResult(status=execution.status, response_text=None, execution_result=execution)


async def execute_mcp_tool_call(
    book: EngineSpellbook,
    spell_id: str,
    mcp_arguments: dict[str, Any] | None,
    bridge: BridgeContext,
    executor: ActionExecutor,
    *,
    context: object | None = None,
    assist_forwarder: AssistForwarder | None = None,
) -> McpExecutionResult:
    """The whole Phase 2 execution path, in one call:

        spell_id -> find_command_by_id (never find_command/match)
                 -> project_command (Phase 1, re-derives the SAME
                    Bridge-context projection tools/list already
                    published, so a stale/forged argument shape is
                    rejected the same way regardless of what the caller
                    actually sent)
                 -> build_captures (Phase 1 - the ONLY MCP-arguments ->
                    captures conversion; never reimplemented here)
                 -> build_draft / build_query_plan / build_step_plan
                    (core.spellbook_engine, unchanged)
                 -> ActionExecutor.execute / execute_query_plan /
                    execute_steps (core.executor, unchanged - Assist
                    exposure, AreaPolicy, expert-tier gating and argument/
                    shape validation all happen here, exactly as for any
                    other frontend)

    `context` is opaque (this module has zero homeassistant imports) and
    is forwarded verbatim to the executor, exactly like
    ConversationRequest.context already is for a spoken/text turn - so an
    MCP-triggered service call is attributed to whoever/whatever the
    caller says triggered it, not to nothing.
    """
    command = find_command_by_id(book, spell_id)
    if command is None or not command.enabled:
        # A disabled command is invisible to project_spellbook() too (see
        # its own docstring) - tools/list would never have advertised it,
        # but a caller could still name it directly, so this is checked
        # again here rather than trusted from the tool name alone.
        return _unresolved(f"unknown_tool:{spell_id}")

    projection = project_command(command, bridge)
    if isinstance(projection, ProjectionSkip):
        return _unresolved(f"unprojectable_tool:{projection.reason}")

    captures = build_captures(projection, mcp_arguments)
    if captures is None:
        return _unresolved("missing_required_argument")

    return await _execute_projected(command, captures, bridge, executor, context=context, assist_forwarder=assist_forwarder)


async def _execute_projected(
    command: EngineCommand,
    captures: dict[str, str],
    bridge: BridgeContext,
    executor: ActionExecutor,
    *,
    context: object | None,
    assist_forwarder: AssistForwarder | None = None,
) -> McpExecutionResult:
    # A text-only Spell (no operation/sources/steps - see
    # core.mcp_projection._project_text_only) never reaches
    # core.executor.ActionExecutor at all: there is no target, no area for
    # it to check a policy against or dispatch a service against. Dispatched
    # via core.text_only_execution.execute_text_only_command - the SAME
    # shared function core.router.SpellbookRouter calls directly for a
    # spoken/typed HASE-Agent turn, never a second implementation of what
    # command.assist_redirect (models.DEFAULT_ASSIST_REDIRECT) means.
    if command.operation is None and not command.sources and not command.steps:
        return await _execute_text_only_command(
            command, captures, language=bridge.language, policy=bridge.policy,
            assist_forwarder=assist_forwarder, context=context,
        )

    # source_area targets (core.executor's `source_area` kind) resolve by
    # DIRECT area-id registry lookup, never by name - always area_id, never
    # area_name (see core.mcp_projection.BridgeArea's own docstring on why
    # the two are never interchangeable).
    source_area_id = bridge.area.area_id if bridge.area is not None else None

    if command.sources:
        plan = build_query_plan(command, captures, bridge.language)
        if plan is None:
            return _unresolved("unresolvable_query_plan")
        execution = await executor.execute_query_plan(
            plan, bridge.policy, context=context, source_area_id=source_area_id
        )
        return _finish(execution)

    if command.steps:
        plan = build_step_plan(command, captures, bridge.language)
        if plan is None:
            return _unresolved("unresolvable_step_plan")
        execution = await executor.execute_steps(
            plan, bridge.policy, context=context, source_area_id=source_area_id
        )
        return _finish(execution)

    draft = build_draft(command, captures, bridge.language)
    if draft is None:
        return _unresolved("unresolvable_action_draft")
    execution = await executor.execute(draft, bridge.policy, context=context, source_area_id=source_area_id)
    return _finish(execution, catchphrase=draft.catchphrase)

