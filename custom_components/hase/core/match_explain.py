"""Stable MatchExplain contract (P6) - HA-free, observation only.

``explain_match()`` runs the ONE production matcher
(spellbook_engine._find_command_impl via ``explain()``) with trace collection
and projects that trace into a stable, JSON-friendly contract.  This module
contains no matching rules: it never ranks, scores, tiers, compares outcomes
or decides ambiguity.  "Explain may observe matching, but must never
influence matching."

API vs. debug
-------------
Stable API: ``decision``, ``reason``, candidate ``disposition``/``reason``,
the reason-code sets below, ``engine.stage``/``engine.reason`` and the action
summaries (role, operation, outcome, target).
NOT API: everything under ``debug`` (raw trace codes, token/segment indices,
stable-order index) and numeric score values (shown for diagnosis, but their
exact values are an implementation detail of the fuzzy matcher).

Layers
------
Matcher reasons describe why the matcher chose/rejected a candidate.  A
candidate discarded because its action/query/step/response could not be built
is a matcher decision (Semantics v1 rule 4) with the matcher reason
``candidate_unresolvable``; the *cause* is Engine-layer knowledge and is
reported only in the separate ``engine`` block, never as a matcher reason.
Home Assistant target resolution, policy and execution happen after matching
and are not part of MatchExplain.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# The projection relies only on the trace of the same run (never on result
# class identity), so it stays correct even if a caller holds objects from a
# re-imported engine module.
from .spellbook_engine import (
    CommandExplanation,
    EngineSpellbook,
    MatchTraceCandidate,
    explain,
)

CONTRACT = "hase.match_explain/1"

DECISIONS = frozenset({"MATCH", "AMBIGUOUS", "NO_MATCH"})

DECISION_REASONS = frozenset({
    # MATCH
    "single_candidate", "clear_winner", "within_epsilon_not_opposite", "stable_order",
    # AMBIGUOUS
    "safety_ambiguity",
    # NO_MATCH
    "no_candidate", "no_full_match", "no_resolvable_candidate",
})

DISPOSITIONS = frozenset({
    "selected", "ambiguous", "outranked", "rejected", "not_evaluated", "excluded",
})

CANDIDATE_REASONS = frozenset({
    # selected (same codes as the MATCH decision reason)
    "single_candidate", "clear_winner", "within_epsilon_not_opposite", "stable_order",
    # ambiguous
    "safety_ambiguity",
    # outranked
    "lower_score", "not_in_top_two", "superseded_by_real_capture", "fallback_deferred",
    # rejected (matcher layer)
    "no_trigger_match", "input_not_fully_consumed", "required_capture_missing",
    "trigger_unavailable", "candidate_unresolvable",
    # not_evaluated
    "lower_specificity_tier",
    # excluded
    "spell_disabled", "typed_only_excluded",
})

ENGINE_STAGES = frozenset({"action_draft", "query_plan", "step_plan", "response"})
ENGINE_REASONS = frozenset({
    "target_unresolved", "argument_invalid", "no_operation", "draft_build_failed",
    "query_plan_build_failed", "step_plan_build_failed", "response_format_error",
})

OPPOSITE_BASES = frozenset({"action_outcomes", "v1_boolean_flip", "output_text"})


@dataclass(frozen=True)
class ExplainCandidate:
    spell_id: str
    trigger: str
    is_alias: bool
    specificity: int
    score: float | None
    is_exact: bool | None
    captures: dict[str, str]
    disposition: str
    reason: str
    actions: list[dict[str, Any]] = field(default_factory=list)
    engine: dict[str, Any] | None = None
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MatchExplain:
    contract: str
    semantics: int
    text: str
    language: str
    typed_only: bool
    decision: str
    reason: str
    selected: ExplainCandidate | None
    ambiguity: dict[str, Any] | None
    via_source_area_fallback: bool
    candidates: list[ExplainCandidate]
    debug: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _candidate(entry: MatchTraceCandidate) -> ExplainCandidate:
    # Every trace entry carries a disposition/outcome_reason when collected by
    # explain(); a missing one would be an instrumentation bug, surfaced as-is.
    return ExplainCandidate(
        spell_id=entry.spell_id,
        trigger=entry.trigger_text,
        is_alias=entry.is_alias,
        specificity=entry.specificity,
        score=entry.score,
        is_exact=entry.is_exact,
        captures=dict(entry.captures or {}),
        disposition=entry.disposition or "unknown",
        reason=entry.outcome_reason or "unknown",
        actions=[dict(a) for a in entry.actions],
        engine=(
            {"layer": "engine", "stage": entry.engine["stage"], "reason": entry.engine["reason"],
             **({"variants": entry.engine["variants"]} if entry.engine.get("variants") else {}),
             **({"steps": entry.engine["steps"]} if entry.engine.get("steps") else {})}
            if entry.engine else None
        ),
        debug={
            "trace_reason": entry.reason,
            "order": entry.order,
            "evaluated": entry.evaluated,
            **({k: v for k, v in (entry.debug or {}).items() if k != "reason"}),
        },
    )


def _no_match_reason(candidates: list[ExplainCandidate]) -> str:
    considered = [c for c in candidates if c.disposition not in {"excluded"}]
    if not considered:
        return "no_candidate"
    if any(c.reason == "candidate_unresolvable" for c in considered):
        return "no_resolvable_candidate"
    return "no_full_match"


def build_match_explain(explanation: CommandExplanation) -> MatchExplain:
    """Pure projection of one real matcher run (CommandExplanation)."""
    trace = explanation.trace
    candidates = [_candidate(entry) for entry in trace.candidates]
    selected = None
    ambiguity = None
    if trace.decision == "ambiguous":
        assert explanation.result is not None
        decision, reason = "AMBIGUOUS", "safety_ambiguity"
        ambiguity = {
            "reason": "safety_ambiguity",
            "basis": trace.opposite_basis,
            "spell_ids": list(trace.ambiguity_spell_ids or ()),
            "variants": [dict(v) for v in trace.opposite_variants or ()],
            "epsilon": trace.epsilon,
        }
    elif trace.decision == "match":
        assert explanation.result is not None
        decision, reason = "MATCH", trace.selection_reason or "unknown"
        selected = next((c for c in candidates if c.disposition == "selected"), None)
    else:
        assert explanation.result is None
        decision, reason = "NO_MATCH", _no_match_reason(candidates)
    return MatchExplain(
        contract=CONTRACT,
        semantics=trace.semantics,
        text=trace.text,
        language=trace.language,
        typed_only=trace.typed_only,
        decision=decision,
        reason=reason,
        selected=selected,
        ambiguity=ambiguity,
        via_source_area_fallback=trace.via_source_area_fallback,
        candidates=candidates,
        debug={"trace_decision": trace.decision, "trace_reason": trace.reason, "epsilon": trace.epsilon},
    )


def explain_match(
    book: EngineSpellbook, text: str, lang: str = "en", *, typed_only: bool = False,
) -> tuple[CommandExplanation, MatchExplain]:
    """Run the production matcher once (with trace) and return both the raw
    explanation (result + trace) and the stable MatchExplain projection."""
    explanation = explain(book, text, lang, typed_only=typed_only)
    return explanation, build_match_explain(explanation)
