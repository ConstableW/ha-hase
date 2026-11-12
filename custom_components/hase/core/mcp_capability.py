"""MCP capability check - Spellbook Studio's own "MCP prüfen" support.

Answers, for every Spell in a Spellbook, two SEPARATE questions - the user's
own explicit requirement, never conflated into one verdict:

- STRUCTURAL capability: can this Spell be turned into a coherent MCP tool
  AT ALL? Re-derives the exact same core.mcp_projection.project_command
  every real tools/list call already uses, against one reference
  BridgeContext (area-less, fully unrestricted). Every real project_command
  skip reason is Bridge-independent - only argument SHAPE (required/
  optional/omitted, see project_command's own CASE A/B/C handling) varies
  per Bridge, never whether the Spell projects at all - so one reference
  context answers this correctly for every Bridge that could ever be
  assigned this Spellbook. Never a second projection rule: a Spell this
  module reports as projectable is, by construction, exactly what a real
  Bridge's tools/list would also project (mechanically re-checked, not
  merely asserted).
- ACTUAL grant: does calling it, once assigned to a real Bridge, need that
  Bridge's OWN policy to satisfy something beyond mere projectability?
  Today this is a real yes/no only for a Spell whose execution kind is
  "assist_redirect" (core.text_only_execution._execute_assist_redirect requires
  the calling Bridge's ScopedAreaPolicy to be FULLY unrestricted -
  conversation.async_converse has no per-call area/entity scoping of its
  own to hand a narrower policy to). Every other execution kind's per-call
  target/area outcome depends on the CONCRETE arguments a caller supplies,
  which this check has none of - correctly left unresolved rather than
  guessing an unbased yes/no.

No Home Assistant import - same boundary as the rest of core/.
"""
from __future__ import annotations

from dataclasses import dataclass

from .mcp_projection import BridgeContext, ProjectionSkip, project_command
from .models import AreaPolicy, ScopedAreaPolicy
from .spellbook_engine import EngineCommand, EngineSpellbook, duplicate_spell_ids

# The one reason ACTUAL grant is ever a decidable yes/no without a concrete
# call - the exact SAME reason_code core.mcp_execution._execute_assist_
# redirect reports for a real, live DENIED tools/call, so Studio's
# up-front warning and the runtime refusal are never worded differently
# for the same underlying fact.
ASSIST_REDIRECT_GRANT_REASON = "assist_redirect_requires_unrestricted_policy"

# A neutral, Bridge-independent reference context: no speaking-device area,
# fully unrestricted actions/queries. See this module's own docstring for
# why one reference context is enough to answer STRUCTURAL projectability
# for every real Bridge - it never decides ACTUAL grant, which is computed
# separately and explicitly below.
_REFERENCE_BRIDGE = BridgeContext(
    area=None,
    policy=ScopedAreaPolicy(
        actions=AreaPolicy(allowed_areas=None, unrestricted=True),
        queries=AreaPolicy(allowed_areas=None, unrestricted=True),
    ),
)


def execution_kind(command: EngineCommand) -> str:
    """What calling this Spell's MCP tool actually DOES - read directly off
    the exact same fields project_command/core.mcp_execution already branch
    on (operation/sources/steps/assist_redirect), never inferred from
    `output`'s wording, a placeholder name, or a catchphrase (see
    models.DEFAULT_ASSIST_REDIRECT's own docstring on why that would be
    unreliable - the user's own explicit, twice-repeated requirement)."""
    if command.steps:
        return "combined"
    if command.sources:
        return "query"
    if command.operation is not None:
        return "action"
    if command.assist_redirect:
        return "assist_redirect"
    return "text_answer"


@dataclass(frozen=True)
class McpCapabilityResult:
    spell_id: str
    execution_kind: str
    projectable: bool
    skip_reason: str | None
    read_only: bool | None
    requires_unrestricted_bridge: bool
    grant_reason: str | None


def check_command_mcp_capability(command: EngineCommand) -> McpCapabilityResult:
    kind = execution_kind(command)
    result = project_command(command, _REFERENCE_BRIDGE)
    if isinstance(result, ProjectionSkip):
        return McpCapabilityResult(
            spell_id=command.spell_id, execution_kind=kind, projectable=False, skip_reason=result.reason,
            read_only=None, requires_unrestricted_bridge=False, grant_reason=None,
        )
    requires_unrestricted = kind == "assist_redirect"
    return McpCapabilityResult(
        spell_id=command.spell_id, execution_kind=kind, projectable=True, skip_reason=None,
        read_only=result.read_only, requires_unrestricted_bridge=requires_unrestricted,
        grant_reason=ASSIST_REDIRECT_GRANT_REASON if requires_unrestricted else None,
    )


def check_book_mcp_capability(book: EngineSpellbook) -> tuple[McpCapabilityResult, ...]:
    """Every ENABLED command's own check. A disabled command mirrors
    core.mcp_projection.project_spellbook's own invisibility rule (silently
    absent, never reported - the same state it already has everywhere
    else). A spell_id shared by more than one command becomes its own
    "duplicate_spell_id" result, exactly like project_spellbook - two
    same-named tools would be ambiguous to any MCP client."""
    duplicates = duplicate_spell_ids(book)
    results: list[McpCapabilityResult] = []
    for command in book.commands:
        if not command.enabled:
            continue
        if command.spell_id in duplicates:
            results.append(McpCapabilityResult(
                spell_id=command.spell_id, execution_kind=execution_kind(command), projectable=False,
                skip_reason="duplicate_spell_id", read_only=None, requires_unrestricted_bridge=False,
                grant_reason=None,
            ))
            continue
        results.append(check_command_mcp_capability(command))
    return tuple(results)
