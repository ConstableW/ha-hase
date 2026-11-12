"""Minimal reference frontend adapter - see docs/HASE-NEW-BRIDGES.md.

A hypothetical "chat bot" Bridge: one incoming text message in, one text
reply out. Deliberately small and self-contained so it can be read start
to finish, but every call it makes is a REAL core/ API, not pseudocode -
`tests/test_reference_bridge_adapter.py` runs it through the shared
contract tests in `tests/_bridge_contract.py` (with mocked Home Assistant
calls - this module never performs a real device action).

What this adapter OWNS (transport-specific, its own business):
- how a message arrives (`handle_message`'s own `text` parameter - a
  plain string here; another adapter's transport might hand it JSON,
  a protobuf, or an already-parsed intent instead)
- which matching strategy it uses (`find_command`, natural-language
  fuzzy matching - an adapter that receives an already-disambiguated
  call, like MCP's `tools/call`, would use `find_command_by_id` instead,
  see core/mcp_execution.py)
- its own `ScopedAreaPolicy` (`_policy` below - CODE27 uses an
  intentionally unrestricted policy for its own owner-only frontend,
  see providers/code27/frontend.py's `_UNRESTRICTED_POLICY`; a real
  chat-bot Bridge would very likely scope this to the areas it is
  actually allowed to touch)
- its own reply format (`ChatReply` - a plain string here; CODE27 speaks
  it via TTS, MCP wraps it in a `TextContent`)

What this adapter NEVER owns (the Spell's own configured meaning) - it
never re-derives or reinterprets any of these itself:
- what a Spell's `operation`/`sources`/`steps`/`output`/`assist_redirect`
  actually mean or do - `build_draft`/`build_query_plan`/`build_step_plan`/
  `build_text_response` (core/spellbook_engine.py) are the only things
  that ever turn a matched command into something executable. This
  adapter matches via `find_command` (fuzzy natural-language matching),
  which already calls all three builders internally and returns a ready-
  to-execute `CommandMatch` (`.action_draft`/`.query_plan`/`.step_plan`) -
  an adapter using EXACT lookup instead (`find_command_by_id`, e.g.
  core/mcp_execution.py for an already-disambiguated `tools/call`) gets
  back only the raw `EngineCommand` and must call the three builders
  itself with its own captures. Same builders either way - never a
  second implementation of what a Spell's fields mean.
- how an action/query/Combined Spell is actually executed against Home
  Assistant - `core.executor.ActionExecutor` is the only thing that ever
  does that, via the injected TargetResolver/ServiceCaller below
- how a successful response is worded - `response_text_for_success`
  (core/executor.py) is the one place that renders a success reply,
  reused verbatim, never a second wording rule
"""
from __future__ import annotations

from dataclasses import dataclass

from custom_components.hase.core.executor import (
    ActionExecutor,
    response_text_for_success,
)
from custom_components.hase.core.models import ExecutionStatus, ScopedAreaPolicy
from custom_components.hase.core.spellbook_engine import (
    CommandAmbiguity,
    EngineSpellbook,
    find_command,
)


@dataclass(frozen=True)
class ChatReply:
    """This adapter's own output shape - never HASE's shared response
    model. `handled=False` means nothing in the book matched at all (a
    real chat bot would likely stay silent or fall back to small talk,
    not HASE's concern)."""

    handled: bool
    text: str | None
    status: str | None = None  # ExecutionStatus.value, or a local reason - never invented per call site


class ReferenceChatAdapter:
    """One Bridge instance per chat channel/room. `policy` is this
    Bridge's OWN configured permission scope (see this module's own
    docstring) - constructed by whoever sets this Bridge up, never
    computed here."""

    def __init__(self, book: EngineSpellbook, executor: ActionExecutor, policy: ScopedAreaPolicy, *, language: str = "en") -> None:
        self._book = book
        self._executor = executor
        self._policy = policy
        self._language = language

    async def handle_message(self, text: str, *, context: object | None = None) -> ChatReply:
        """The whole adapter, start to finish: match -> build -> execute
        -> respond. Every execution kind funnels through the SAME four
        lines of dispatch below - adding a Spell FORM never means adding
        a new code path elsewhere in this class, and every branch here
        must be covered by tests/_bridge_contract.py's own fixtures (see
        tests/test_reference_bridge_adapter.py) so a missing branch is a
        test failure, not a silent gap - exactly the class of bug
        providers/code27/bridge.py's own query_plan dispatch once had."""
        result = find_command(self._book, text, self._language)
        if result is None:
            return ChatReply(handled=False, text=None)
        if isinstance(result, CommandAmbiguity):
            # A real adapter would ask a follow-up question here (see
            # core.router.SpellbookRouter's own clarification handling for
            # the shared pattern) - this minimal reference simply refuses,
            # since it has no multi-turn session state of its own.
            return ChatReply(handled=True, text=None, status="ambiguous")

        command = result.command

        if result.query_plan is not None:
            execution = await self._executor.execute_query_plan(result.query_plan, self._policy, context=context)
            return ChatReply(handled=True, text=response_text_for_success(execution), status=execution.status.value)

        if result.step_plan is not None:
            execution = await self._executor.execute_steps(result.step_plan, self._policy, context=context)
            return ChatReply(handled=True, text=response_text_for_success(execution), status=execution.status.value)

        if result.action_draft is not None:
            execution = await self._executor.execute(result.action_draft, self._policy, context=context)
            text_out = result.catchphrase if execution.status is ExecutionStatus.SUCCESS and result.catchphrase else response_text_for_success(execution)
            return ChatReply(handled=True, text=text_out, status=execution.status.value)

        # Text-only: operation/sources/steps are all absent. WHAT this
        # does depends on command.assist_redirect (models.
        # DEFAULT_ASSIST_REDIRECT) - never on output's own wording or on
        # {placeholder} names (see models.py's own docstring on exactly
        # this point).
        if command.assist_redirect:
            # This minimal reference adapter has no live conversation
            # agent to forward to (unlike providers/code27/bridge.py's own
            # _forward_to_assist, or providers/mcp's HaAssistForwarder) -
            # an explicit, reported "not supported" is the only honest
            # outcome. It must NEVER return `result.output` here instead:
            # that text is a command phrase meant for a real agent to
            # interpret, and returning it as if it were an answer would
            # misrepresent an unperformed command as completed - the exact
            # failure mode core/mcp_projection.py's own _project_text_only
            # docstring warns about.
            return ChatReply(handled=True, text=None, status="assist_redirect_unsupported")

        # find_command already rendered `output` with this match's own
        # captures substituted (the same inline text-only branch core.
        # spellbook_engine.build_text_response mirrors for exact-lookup
        # callers like MCP) - never re-rendered here.
        return ChatReply(handled=True, text=result.output, status=ExecutionStatus.QUERY_ANSWER.value)
