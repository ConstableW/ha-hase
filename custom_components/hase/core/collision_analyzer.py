"""Static collision diagnostics for HASE Matching Semantics v1.

This module is advisory only: it never changes matching, rejects a command, or
changes whether a Spellbook loads. It describes structurally visible trigger
overlaps so authors can inspect cases whose runtime outcome may depend on
specificity, stable order, or the conservative ambiguity rule.

No Home Assistant imports.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import combinations
from typing import Any, Literal

from .book_languages import text as resolve_localized
from .action_semantics import (
    ActionVariant,
    command_action_variants,
    describe_variant,
    safety_opposite_variants,
)
from .matching import Segment, compile_trigger, literal_word_count
from .spellbook_engine import EngineCommand, EngineSpellbook, _numeric_capture_names, _vocabulary_capture_words

CollisionKind = Literal[
    "hard_collision",
    "resolved_overlap",
    "order_dependent",
    "safety_ambiguity",
]


Severity = Literal["error", "warning", "info"]

# Authoring severity of each finding kind (single source for Studio badges,
# validation and summaries): safety_ambiguity is an authoring error with
# explicit save confirmation (and a runtime failsafe); order_dependent and
# hard_collision are warnings; resolved_overlap is informational.
SEVERITY: dict[str, Severity] = {
    "safety_ambiguity": "error",
    "order_dependent": "warning",
    "hard_collision": "warning",
    "resolved_overlap": "info",
}


@dataclass(frozen=True)
class CollisionFinding:
    kind: CollisionKind
    spell_ids: tuple[str, str]
    triggers: tuple[str, str]
    specificity: tuple[int, int]
    reason: str
    severity: Severity = "info"
    # For safety_ambiguity: the variant pair (in spell_ids order) that proves
    # SAME effective target + opposite canonical outcomes.
    evidence: tuple[dict[str, Any], dict[str, Any]] | None = None


@dataclass(frozen=True)
class _TriggerView:
    command: EngineCommand
    text: str
    segments: tuple[Segment, ...]
    specificity: int
    order: int


def _shape(segments: tuple[Segment, ...]) -> tuple[tuple[str, str], ...]:
    """Placeholder names are irrelevant to the language accepted by a trigger."""
    return tuple((kind, "*" if kind == "ph" else value.casefold()) for kind, value in segments)


def _literal_signature(segments: tuple[Segment, ...]) -> tuple[str, ...]:
    return tuple(value.casefold() for kind, value in segments if kind == "lit")


def _views(book: EngineSpellbook, lang: str) -> list[_TriggerView]:
    views: list[_TriggerView] = []
    order = 0
    for command in book.commands:
        if not command.enabled:
            continue
        disambiguable = (
            _numeric_capture_names(command.arguments_spec, command.steps)
            | _vocabulary_capture_words(command.arguments_spec, command.steps).keys()
        )
        trigger = resolve_localized(command.trigger, lang, command.default_language)
        if trigger:
            segments = command.compiled.get(trigger) or compile_trigger(trigger, disambiguable)
            if segments is not None:
                views.append(_TriggerView(
                    command, trigger, tuple(segments), literal_word_count(segments), order,
                ))
                order += 1
        aliases = resolve_localized(command.aliases, lang, command.default_language, [])
        for alias in aliases:
            segments = compile_trigger(alias, disambiguable)
            if segments is not None:
                views.append(_TriggerView(
                    command, alias, tuple(segments), literal_word_count(segments), order,
                ))
                order += 1
    return views


def _structurally_overlap(a: _TriggerView, b: _TriggerView) -> bool:
    # v1 deliberately stays conservative. Identical normalized segment shapes
    # are certain overlaps. Same literal skeleton with placeholders in the same
    # positions is the important authoring collision (e.g. status {area} vs
    # status {room}). Broader fuzzy-language intersection is intentionally not
    # guessed statically.
    return _shape(a.segments) == _shape(b.segments)


def _validated_extension(general: _TriggerView, specific: _TriggerView) -> bool:
    """`specific` is `general` plus trailing placeholders that are ALL
    semantically validated (numeric / closed vocabulary): "lumos {area}" vs
    "lumos {area} {color}". At runtime the validated decomposition wins when
    it can be satisfied (Matching Semantics rule 4, tie-break) and the
    general trigger takes the whole remainder otherwise, so the overlap is
    resolved by design rather than a collision."""
    general_segments, specific_segments = general.segments, specific.segments
    if len(specific_segments) <= len(general_segments):
        return False
    if _shape(specific_segments[: len(general_segments)]) != _shape(general_segments):
        return False
    command = specific.command
    validated = _numeric_capture_names(command.arguments_spec, command.steps) | _vocabulary_capture_words(
        command.arguments_spec, command.steps,
    ).keys()
    return all(kind == "ph" and name in validated for kind, name in specific_segments[len(general_segments):])


def _safety_proof(a: EngineCommand, b: EngineCommand) -> tuple[ActionVariant, ActionVariant] | None:
    """Static safety proof using the same contracts as the runtime.

    Every executable action variant of each command (user and expert,
    see action_semantics.command_action_variants) takes part, pairwise
    user/user, user/expert, expert/user and expert/expert - the same
    variant semantics the runtime applies to its top-2 candidates.
    Trigger overlap and target equality are separate questions.  A symbolic
    target is only SAME when that can be proven from the declaration itself;
    {area} vs {room}, or literal vs placeholder, is UNKNOWN and therefore
    cannot produce a safety error.  Queries/Combined/text-only commands have
    no action variants.  Returns the proving variant pair, else None.
    """
    return safety_opposite_variants(command_action_variants(a), command_action_variants(b))


def _structurally_opposite(a: EngineCommand, b: EngineCommand) -> bool:
    return _safety_proof(a, b) is not None


def analyze_collisions(book: EngineSpellbook, lang: str = "en") -> tuple[CollisionFinding, ...]:
    """Return advisory Semantics-v1 collision findings; never mutates *book*."""
    findings: list[CollisionFinding] = []
    for a, b in combinations(_views(book, lang), 2):
        if a.command.spell_id == b.command.spell_id:
            continue
        specificity = (a.specificity, b.specificity)
        common = dict(
            spell_ids=(a.command.spell_id, b.command.spell_id),
            triggers=(a.text, b.text),
            specificity=specificity,
        )
        if a.specificity == b.specificity and (_validated_extension(a, b) or _validated_extension(b, a)):
            findings.append(CollisionFinding(
                kind="resolved_overlap",
                reason="validated_placeholders_select_specific_trigger",
                **common,
            ))
            continue
        if not _structurally_overlap(a, b):
            continue

        if a.specificity != b.specificity:
            findings.append(CollisionFinding(
                kind="resolved_overlap",
                reason="specificity_v1_selects_higher_tier",
                **common,
            ))
            continue

        proof = _safety_proof(a.command, b.command)
        if proof is not None:
            findings.append(CollisionFinding(
                kind="safety_ambiguity",
                reason="same_tier_structurally_opposite",
                evidence=(describe_variant(proof[0]), describe_variant(proof[1])),
                **common,
            ))
            continue

        if _shape(a.segments) == _shape(b.segments):
            findings.append(CollisionFinding(
                kind="order_dependent",
                reason="same_tier_same_trigger_shape_stable_order_decides_equal_scores",
                **common,
            ))
            continue

        findings.append(CollisionFinding(
            kind="hard_collision",
            reason="same_tier_overlapping_trigger_language_without_static_resolution",
            **common,
        ))

    return tuple(replace(f, severity=SEVERITY[f.kind]) for f in findings)
