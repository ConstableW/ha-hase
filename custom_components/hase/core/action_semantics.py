"""HA-free semantic contracts for statically provable action safety.

This module deliberately knows nothing about Home Assistant state, entity
resolution, spell ranking, fuzzy matching, or execution.  It only answers
questions that can be proven from the action declaration itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Literal, Mapping

from .models import ActionTarget


class ActionOutcome(str, Enum):
    ON = "on"
    OFF = "off"


class TargetRelation(str, Enum):
    SAME = "same"
    DIFFERENT = "different"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class StaticActionTarget:
    """Target identity before trigger captures have been resolved.

    Exactly one of ``capture`` and ``value`` is normally present for named
    targets.  Keeping the capture name is essential: {area} and {room} are
    UNKNOWN, not SAME, even if both occupy the same trigger position.
    """

    kind: str
    value: str | tuple[str, ...] | None = None
    capture: str | None = None


def canonical_action_outcome(
    operation: str,
    arguments: Mapping[str, object],
) -> ActionOutcome | None:
    """Return an outcome only when it is statically provable."""
    if operation == "light.turn_on":
        return ActionOutcome.ON
    if operation == "light.turn_off":
        return ActionOutcome.OFF
    if operation == "light.set_power":
        value = arguments.get("on")
        if value is True:
            return ActionOutcome.ON
        if value is False:
            return ActionOutcome.OFF
    return None


def action_outcomes_are_opposite(
    operation_a: str,
    arguments_a: Mapping[str, object],
    operation_b: str,
    arguments_b: Mapping[str, object],
) -> bool:
    """True only for two known, canonical, opposite outcomes."""
    a = canonical_action_outcome(operation_a, arguments_a)
    b = canonical_action_outcome(operation_b, arguments_b)
    return (
        a is not None
        and b is not None
        and {a, b} == {ActionOutcome.ON, ActionOutcome.OFF}
    )


def same_target_opposite_outcomes(
    target_a: ActionTarget | StaticActionTarget,
    operation_a: str,
    arguments_a: Mapping[str, object],
    target_b: ActionTarget | StaticActionTarget,
    operation_b: str,
    arguments_b: Mapping[str, object],
) -> bool:
    """The single shared typed safety proof.

    True only when the target relation is provably SAME *and* both actions
    have known, canonical, opposite outcomes.  Used unchanged by the static
    Collision Analyzer (symbolic StaticActionTargets) and by the runtime
    ambiguity decision (resolved ActionTargets), so both layers agree on
    every case this contract can prove.  UNKNOWN is never treated as SAME.
    """
    return (
        target_relation(target_a, target_b) is TargetRelation.SAME
        and action_outcomes_are_opposite(operation_a, arguments_a, operation_b, arguments_b)
    )


VariantRole = Literal["user", "expert"]


@dataclass(frozen=True)
class ActionVariant:
    """One action a candidate can actually execute, reduced to what the
    safety proof needs.  ``target`` is a StaticActionTarget for the static
    Collision Analyzer and a resolved ActionTarget at runtime."""

    role: VariantRole
    operation: str
    target: ActionTarget | StaticActionTarget
    arguments: Mapping[str, object]


def draft_action_variants(draft: Any) -> tuple[ActionVariant, ...]:
    """Runtime projection of a built ActionDraft: the draft itself plus every
    ``user_fallback`` it carries.  Deliberately no permission simulation -
    every variant the candidate may execute takes part in the safety proof.
    Duck-typed on ActionDraft; never builds a draft."""
    variants: list[ActionVariant] = []
    seen: set[int] = set()
    current = draft
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        variants.append(ActionVariant(
            role="expert" if getattr(current, "expert", False) else "user",
            operation=current.operation,
            target=current.target,
            arguments=current.arguments,
        ))
        current = getattr(current, "user_fallback", None)
    return tuple(variants)


def command_action_variants(command: Any) -> tuple[ActionVariant, ...]:
    """Static projection of a spellbook command: its own typed action (the
    user variant, or the sole variant of an expert-only command) and its
    expert variant, if declared.  Queries, Combined Spells and text-only
    commands have no action variants.  Duck-typed on EngineCommand."""
    if getattr(command, "sources", ()) or getattr(command, "steps", ()):
        return ()
    variants: list[ActionVariant] = []
    expert = getattr(command, "expert_command", None)
    own_role: VariantRole = "user" if expert is not None or not getattr(command, "expert_only", False) else "expert"
    for role, variant in ((own_role, command), ("expert", expert)):
        if variant is None or not isinstance(getattr(variant, "operation", None), str):
            continue
        variants.append(ActionVariant(
            role=role,
            operation=variant.operation,
            target=static_target(variant.target_spec if isinstance(variant.target_spec, Mapping) else {}),
            arguments=variant.arguments_spec if isinstance(variant.arguments_spec, Mapping) else {},
        ))
    return tuple(variants)


def safety_opposite_variants(
    variants_a: Iterable[ActionVariant],
    variants_b: Iterable[ActionVariant],
) -> tuple[ActionVariant, ActionVariant] | None:
    """First variant pair (in declaration order) that proves SAME effective
    target and opposite canonical outcomes, else None.  Shared by runtime
    and Collision Analyzer; UNKNOWN is never promoted to SAME."""
    variants_b = tuple(variants_b)
    for a in variants_a:
        for b in variants_b:
            if same_target_opposite_outcomes(
                a.target, a.operation, a.arguments, b.target, b.operation, b.arguments,
            ):
                return a, b
    return None


def describe_variant(variant: ActionVariant) -> dict[str, Any]:
    """JSON-friendly description of one action variant for diagnostics
    (Explain, Collision details).  Pure projection; no decision logic."""
    outcome = canonical_action_outcome(variant.operation, variant.arguments)
    target = variant.target
    value = getattr(target, "value", None)
    described: dict[str, Any] = {
        "kind": target.kind,
        "value": list(value) if isinstance(value, tuple) else value,
    }
    capture = getattr(target, "capture", None)
    if capture is not None:
        described["capture"] = capture
    return {
        "role": variant.role,
        "operation": variant.operation,
        "outcome": outcome.value if outcome is not None else None,
        "target": described,
    }


def static_target(target_spec: Mapping[str, object]) -> StaticActionTarget:
    """Preserve the symbolic identity of a spellbook target declaration."""
    value = target_spec.get("value")
    if (
        isinstance(value, list)
        and value
        and all(isinstance(v, str) and v.strip() for v in value)
    ):
        # Same canonical form the runtime builds for entity_ids
        # (spellbook_engine._resolve_target: tuple(value)); YAML yields lists.
        value = tuple(value)
    if not isinstance(value, (str, tuple)):
        value = None
    capture = target_spec.get("capture")
    if not isinstance(capture, str):
        capture = None
    return StaticActionTarget(
        kind=str(target_spec.get("kind", "")),
        value=value,
        capture=capture,
    )


def target_relation(
    target_a: ActionTarget | StaticActionTarget,
    target_b: ActionTarget | StaticActionTarget,
) -> TargetRelation:
    """Conservatively compare two target identities.

    UNKNOWN is intentional.  The contract must never turn a merely possible
    run-time equality into a static safety error.
    """
    if target_a.kind != target_b.kind:
        # all_allowed/none/source_area are semantically distinct from named
        # targets and from each other.
        return TargetRelation.DIFFERENT

    # Static symbolic targets: preserve capture identity.
    if isinstance(target_a, StaticActionTarget) or isinstance(target_b, StaticActionTarget):
        if not isinstance(target_a, StaticActionTarget) or not isinstance(target_b, StaticActionTarget):
            return TargetRelation.UNKNOWN

        if target_a.kind in {"all_allowed", "none", "source_area"}:
            return TargetRelation.SAME

        if target_a.capture is not None or target_b.capture is not None:
            if target_a.capture is not None and target_b.capture is not None:
                return (
                    TargetRelation.SAME
                    if target_a.capture == target_b.capture
                    else TargetRelation.UNKNOWN
                )
            return TargetRelation.UNKNOWN

        if target_a.value is not None and target_b.value is not None:
            return (
                TargetRelation.SAME
                if target_a.value == target_b.value
                else TargetRelation.DIFFERENT
            )
        return TargetRelation.UNKNOWN

    # Runtime ActionTargets already contain resolved capture values.
    if target_a.kind in {"all_allowed", "none", "source_area"}:
        return TargetRelation.SAME
    if target_a.value is None or target_b.value is None:
        return TargetRelation.UNKNOWN
    return (
        TargetRelation.SAME
        if target_a.value == target_b.value
        else TargetRelation.DIFFERENT
    )
