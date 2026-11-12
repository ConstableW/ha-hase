"""Text-only Spell execution - the shared, neutral core for the FIFTH
execution kind every real frontend that can reach a text-only command
(no operation/sources/steps - see core.models.DEFAULT_ASSIST_REDIRECT's
own docstring) dispatches through: core.router.SpellbookRouter (native
HA-Voice/HASE-Agent, both the standalone SPELLBOOK_ONLY agent and a
SATELLITE subentry) and core.mcp_execution (MCP tools/call). Neither of
those adapters owns this logic - both depend on it, from here, never one
depending on the other's own adapter-specific module.

This module exists specifically to fix a wrong dependency direction: an
earlier version of this feature had core.router import this exact
function FROM core.mcp_execution. That was backwards and misleading - a
HASE-Agent/native-voice turn never touches MCP, the network, or any
MCP-specific concept at all; it was only ever reusing a plain Python
function, and that function happened to live in the wrong (MCP-specific)
module. Both core.router and core.mcp_execution now depend on this
neutral module instead, symmetrically - see docs/HASE-MCP.md's own "Text-
only Spells and `assist_redirect`" section and docs/HASE-ARCHITECTURE.md.

No Home Assistant import - same boundary as the rest of core/.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .book_languages import text as resolve_localized
from .executor import response_text_for_success
from .models import ExecutionResult, ExecutionStatus, ScopedAreaPolicy
from .spellbook_engine import EngineCommand, build_text_response


@dataclass(frozen=True)
class AssistForwardOutcome:
    """One conversation.async_converse round-trip's outcome, transport-
    neutral (no Home Assistant import here - see AssistForwarder below).
    Mirrors the exact three-way distinction providers.code27.bridge.
    _forward_to_assist already relies on (HA's IntentResponseType has
    exactly three values: ERROR, QUERY_ANSWER, and everything else is
    ACTION_DONE - never a fourth), so _execute_assist_redirect below can
    apply the SAME catchphrase-substitution rule that module already
    uses, rather than inventing a second one:

    - `speech`: Assist's own reply text. None only when Assist raised, or
      genuinely returned no speech at all - a real, reportable outcome in
      its own right (a successful action can complete silently), never
      treated as equivalent to `is_error`.
    - `is_error`: response_type == IntentResponseType.ERROR.
    - `is_query_answer`: response_type == IntentResponseType.QUERY_ANSWER
      - Assist merely ANSWERED a question, it did not perform an action;
      a Spell's own configured catchphrase must never stand in for this
      (see _forward_to_assist's own docstring on exactly this point).
    - `continue_conversation`: the agent is asking a follow-up question
      (HA has no distinct response_type for this - it reports
      ACTION_DONE too). A one-shot caller with no conversation_id
      continuity of its own still must never let a catchphrase paper
      over the real follow-up question the caller needs to see - same
      exclusion _forward_to_assist applies."""

    speech: str | None
    is_error: bool
    is_query_answer: bool
    continue_conversation: bool


class AssistForwarder(Protocol):
    """Home Assistant's own configured conversation agent, reached the
    exact same way providers.code27.bridge._ask_assist already does
    (conversation.async_converse) - injected here so core/ stays free of
    Home Assistant imports, exactly like core.executor's own
    TargetResolver/ServiceCaller Protocols. Used ONLY for a text-only
    command with assist_redirect=True (see models.DEFAULT_ASSIST_REDIRECT)
    - a typed operation/sources/steps command always executes through
    ActionExecutor instead, never through this."""

    async def forward(
        self, command_text: str, *, language: str, context: object | None
    ) -> AssistForwardOutcome: ...


@dataclass(frozen=True)
class CommandExecutionResult:
    """Transport-neutral outcome of executing one already-matched
    text-only command. `status`/`execution_result` are HASE's OWN
    ExecutionStatus/ExecutionResult - deliberately not re-encoded into a
    second, adapter-specific status vocabulary, so every caller (MCP's
    own tools/call, core.router's spoken/typed turn) can distinguish
    success from DENIED/FAILED using exactly the same status/reason_code
    information every other execution kind already relies on.

    `status` is None only when the command's own `output` failed to
    resolve to real text at all (a missing/invalid placeholder) -
    `detail` carries a plain-text reason for that one case, the same
    "reason_code as a short descriptive string" convention
    ExecutionResult.reason_code already uses everywhere else.

    `response_text` is populated ONLY for a genuine success (status is
    SUCCESS or QUERY_ANSWER) and is always HASE's OWN rendered response,
    via the EXACT SAME response_text_for_success() every other execution
    kind uses. Never a second, adapter-specific wording system."""

    status: ExecutionStatus | None
    response_text: str | None
    execution_result: ExecutionResult | None = None
    detail: str | None = None


def _unresolved(detail: str) -> CommandExecutionResult:
    return CommandExecutionResult(status=None, response_text=None, execution_result=None, detail=detail)


def _finish(execution: ExecutionResult, *, catchphrase: str | None = None) -> CommandExecutionResult:
    if execution.status is ExecutionStatus.SUCCESS:
        text = catchphrase or response_text_for_success(execution)
        return CommandExecutionResult(status=execution.status, response_text=text, execution_result=execution)
    if execution.status is ExecutionStatus.QUERY_ANSWER:
        # A catchphrase is what to say after DOING something - a query has
        # nothing to "do", so applying it here would silently swallow the
        # actual answer, exactly the reasoning core.router's own
        # _execute_and_respond already documents for the identical case.
        return CommandExecutionResult(
            status=execution.status, response_text=response_text_for_success(execution), execution_result=execution
        )
    return CommandExecutionResult(status=execution.status, response_text=None, execution_result=execution)


async def execute_text_only_command(
    command: EngineCommand,
    captures: dict[str, str],
    *,
    language: str,
    policy: ScopedAreaPolicy,
    assist_forwarder: AssistForwarder | None,
    context: object | None = None,
) -> CommandExecutionResult:
    """The whole text-only execution kind (no operation/sources/steps),
    in ONE shared place - called identically by core.mcp_execution (from
    an MCP BridgeContext's own language/policy) and core.router.
    SpellbookRouter (from a spoken/typed turn's own ConversationRequest.
    language + AgentConfig.policy). What "execution" means depends on
    command.assist_redirect (see models.DEFAULT_ASSIST_REDIRECT's own
    docstring) - a fifth execution kind, entirely separate from
    operation/sources/steps:
      - False: rendering `output` from the already-built captures IS the
        entire execution (a genuine fixed/templated answer).
      - True: `output` is a command phrase for Home Assistant's own
        configured conversation agent to interpret and execute for real
        - see _execute_assist_redirect."""
    if command.assist_redirect:
        return await _execute_assist_redirect(command, captures, language, policy, assist_forwarder, context=context)
    text = build_text_response(command, captures, language)
    if text is None:
        return _unresolved("unresolvable_text_response")
    execution = ExecutionResult(
        status=ExecutionStatus.QUERY_ANSWER, reason_code="text_response", data={"response_text": text}
    )
    return _finish(execution)


async def _execute_assist_redirect(
    command: EngineCommand,
    captures: dict[str, str],
    language: str,
    policy: ScopedAreaPolicy,
    assist_forwarder: AssistForwarder | None,
    *,
    context: object | None,
) -> CommandExecutionResult:
    """assist_redirect=True hands the rendered `output` text to Home
    Assistant's own configured conversation agent for real interpretation/
    execution - the same capability providers.code27.bridge.
    _forward_to_assist already has, reachable through both MCP and native
    HASE-Agent/voice via this shared function (see AssistForwarder above).
    Never returns the command text itself as if that were a completed
    answer - a missing forwarder or a denied policy are reported as
    concrete, evidenced outcomes, not silently downgraded into "here is
    the text".

    Gated on `policy` being FULLY unrestricted (actions AND queries -
    mirrors providers.code27.frontend._UNRESTRICTED_POLICY, the only
    other caller of this mechanism today): conversation.async_converse
    has no per-call area/entity scoping parameter of its own to hand a
    ScopedAreaPolicy to, so a genuinely restricted caller must never
    reach it at all - denied outright (ExecutionStatus.DENIED, a real,
    reported outcome, not a silent skip), never downgraded to "whatever
    Home Assistant's own Assist-exposure list happens to expose" (that is
    a different, coarser boundary than this caller's own configured
    AreaPolicy, and must never substitute for it). This is what keeps a
    scoped caller's own permissions from being bypassed by routing
    through Assist instead of a typed operation - checked BEFORE the
    forwarder is ever called, so a restricted caller's command text never
    even reaches Home Assistant's conversation agent.

    Once forwarded, the outcome is mapped using the EXACT SAME rule
    providers.code27.bridge._forward_to_assist already applies (see
    AssistForwardOutcome's own docstring for the three-way split this
    mirrors):
    - is_error: ExecutionStatus.FAILED - never a fabricated success.
    - is_query_answer: ExecutionStatus.QUERY_ANSWER, Assist's own reply -
      the Spell's own configured `catchphrase` is never substituted here,
      since nothing was DONE, only answered.
    - otherwise (a confirmed action, or a follow-up question flagged via
      continue_conversation): ExecutionStatus.SUCCESS. The Spell's own
      `catchphrase` stands in for Assist's reply ONLY for a genuinely
      confirmed, non-follow-up completion - exactly mirroring
      _forward_to_assist's own substitution condition - so Assist's raw
      reply never unconditionally overrides the Spell author's own
      configured response. When neither a catchphrase applies nor Assist
      said anything at all (a successful action can complete silently),
      _finish's own SUCCESS fallback (response_text_for_success) supplies
      the same generic confirmation any other silent, catchphrase-less
      action already gets - a successfully executed Spell with no
      configured reply stays a reported SUCCESS, never downgraded to
      FAILED merely for having nothing to say."""
    text = build_text_response(command, captures, language)
    if text is None:
        return _unresolved("unresolvable_text_response")
    if not (policy.actions.unrestricted and policy.queries.unrestricted):
        execution = ExecutionResult(
            status=ExecutionStatus.DENIED, reason_code="assist_redirect_requires_unrestricted_policy",
        )
        return _finish(execution)
    if assist_forwarder is None:
        # This caller was never wired up with a real AssistForwarder - a
        # deployment/wiring gap, not a security decision. Distinct
        # reason_code from the DENIED case above so the two are never
        # confused when diagnosing a refusal.
        return _unresolved("assist_redirect_unavailable")
    outcome = await assist_forwarder.forward(text, language=language, context=context)

    if outcome.is_error:
        execution = ExecutionResult(
            status=ExecutionStatus.FAILED, reason_code="assist_error",
            data={"response_text": outcome.speech} if outcome.speech else {},
        )
        return _finish(execution)

    if outcome.is_query_answer:
        execution = ExecutionResult(
            status=ExecutionStatus.QUERY_ANSWER, reason_code="assist_redirect",
            data={"response_text": outcome.speech},
        )
        return _finish(execution)

    spell_catchphrase = (
        resolve_localized(command.catchphrase, language, command.default_language)
        if command.catchphrase else None
    )
    confirmed = spell_catchphrase and not outcome.continue_conversation
    execution = ExecutionResult(status=ExecutionStatus.SUCCESS, reason_code="assist_redirect")
    return _finish(execution, catchphrase=spell_catchphrase if confirmed else outcome.speech)
