"""ActionExecutor - the only thing in this feature allowed to turn an
ActionDraft into a real effect. Validates the operation against a
small, closed catalog, resolves the target and checks it against an
AreaPolicy, then - and only then - asks an injected ServiceCaller to
actually do it.

Never talks to Home Assistant itself: TargetResolver and ServiceCaller
are Protocols. The real HA implementations live in
custom_components/hase/spellbooker_ha.py; tests inject fakes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from .models import ActionDraft, ActionTarget, AreaPolicy, ExecutionResult, ExecutionStatus, ScopedAreaPolicy


@dataclass(frozen=True)
class OperationSpec:
    kind: str  # "action" | "query"
    allowed_target_kinds: frozenset[str]
    argument_types: dict[str, type]  # name -> required exact type


OPERATION_CATALOG: dict[str, OperationSpec] = {
    "light.set_power": OperationSpec(
        kind="action",
        allowed_target_kinds=frozenset(
            {"area_name", "area_id", "entity_name", "entity_id", "all_allowed", "source_area"}
        ),
        argument_types={"on": bool},
    ),
    "entity.get_state": OperationSpec(
        kind="query",
        allowed_target_kinds=frozenset({"entity_name", "entity_id"}),
        argument_types={},
    ),
    "vacuum.clean_area": OperationSpec(
        kind="action",
        # Area-only, deliberately - "which vacuum, which room" is a
        # discovery problem (native HA mapping, or a per-brand
        # provider), not something a spoken entity_name could name
        # directly. See _execute_vacuum_clean_area below.
        allowed_target_kinds=frozenset({"area_name", "area_id"}),
        argument_types={},
    ),
    "media_player.skip_track": OperationSpec(
        kind="action",
        allowed_target_kinds=frozenset(
            {"area_name", "area_id", "entity_name", "entity_id", "all_allowed", "source_area"}
        ),
        argument_types={"forward": bool},
    ),
}


@dataclass(frozen=True)
class ResolvedEntity:
    entity_id: str
    area_id: str | None


@dataclass(frozen=True)
class ResolvedTargets:
    entities: tuple[ResolvedEntity, ...]


class TargetNotFound:
    """The named area/entity doesn't exist (or isn't exposed) - distinct
    from an empty ResolvedTargets, which means it exists but matched
    nothing (e.g. an area with no lights)."""


@dataclass(frozen=True)
class VacuumCleanOutcome:
    """What came of trying to clean a resolved area. "started" is a
    service-call acceptance, same non-guarantee as everywhere else in
    this file - not proof the vacuum physically began. "ambiguous"
    means more than one room across the house's vacuums matched this
    area's name - mirrors bridge.py's own rule: a wrong room is a real
    physical mistake, never guessed at."""

    status: Literal["started", "no_match", "ambiguous", "error"]
    vacuum_entity_id: str | None = None
    room_name: str | None = None
    # Human-readable "vacuum/room, vacuum/room" description, populated
    # only for status="ambiguous" - diagnostic detail for whichever
    # frontend's own activity log wants to show what collided (see
    # bridge.py's vacuum_ambiguous log message, which pre-dates the
    # executor and expects this).
    candidates_description: str | None = None


class TargetResolver(Protocol):
    async def resolve(
        self, target: ActionTarget, *, domain_filter: str | None
    ) -> ResolvedTargets | TargetNotFound: ...

    async def resolve_area(self, target: ActionTarget) -> str | TargetNotFound:
        """Resolve an area_name/area_id target to a concrete area_id
        only - no entity involved. Used by area-only operations like
        vacuum.clean_area, where policy is checked against the area
        itself before any vacuum-specific discovery even starts."""
        ...


class ServiceCaller(Protocol):
    async def set_light_power(
        self, entity_ids: tuple[str, ...], on: bool, *, context: object | None = None
    ) -> bool:
        """Returns whether the service call was accepted. Acceptance is
        not physical confirmation - SUCCESS here means "HA took the
        call", same caveat as the rest of Home Assistant's service
        layer. `context` is opaque here (this package has zero
        homeassistant imports) - the real implementation forwards it
        to hass.services.async_call so the call is attributed to
        whoever/whatever triggered the conversation turn, same as any
        other HA-initiated service call, instead of showing up in the
        Logbook/History as uninitiated."""
        ...

    async def get_entity_state(self, entity_id: str) -> dict | None:
        """Returns {"state": ..., "unit": ... | None} or None if the
        entity has no current state. Read-only - no context to
        attribute."""
        ...

    async def skip_track(
        self, entity_ids: tuple[str, ...], forward: bool, *, context: object | None = None
    ) -> bool:
        """Next track if forward else previous. Same acceptance-only
        caveat as set_light_power."""
        ...

    async def clean_vacuum_area(
        self, area_id: str, *, context: object | None = None
    ) -> VacuumCleanOutcome:
        """Tries HA's own native vacuum.clean_area feature first, then
        falls back to the per-brand provider YAMLs - see
        spellbooker_ha.py's HaVacuumCaller for the two-tier logic
        ported from bridge.py's _handle_vacuum_clean_action."""
        ...


def _entity_permitted(entity: ResolvedEntity, policy: AreaPolicy) -> bool:
    """An area-less entity (no HA area assigned) has no area for
    `permits_area` to check - under a RESTRICTED policy that rightly
    means it can never be in the allowed set. Under `unrestricted`,
    though, there is nothing to restrict at all, so treating it as
    denied purely because it happens to have no area contradicts
    unrestricted's own meaning. Found via a second-round audit."""
    if entity.area_id is None:
        return policy.unrestricted
    return policy.permits_area(entity.area_id)


_TARGET_KINDS_REQUIRING_A_VALUE = frozenset({"area_name", "area_id", "entity_name", "entity_id"})


def _valid_target_shape(target: ActionTarget) -> bool:
    """`validate_shape` checked `target.kind` was one this operation
    allows, but never checked `target.value` itself - an empty string
    fails safely later (TargetNotFound), but a non-string (a backend
    proposal could set `value` to anything; ActionTarget doesn't
    enforce it) crashes downstream resolution with an AttributeError
    the first time something calls .strip() on it. Found via a
    second-round audit. Catching it here, before any proposal in a
    batch runs, is the whole point of validate_shape existing."""
    if target.kind in ("all_allowed", "none", "source_area"):
        return target.value is None
    if target.kind in _TARGET_KINDS_REQUIRING_A_VALUE:
        return isinstance(target.value, str) and bool(target.value.strip())
    return True


def _validate_arguments(spec: OperationSpec, arguments) -> bool:
    # arguments comes straight off an ActionDraft, which a backend
    # proposal builds with no validation of its own (dataclasses don't
    # enforce field types) - a non-dict (None, a list, ...) must fail
    # closed here, before `set(arguments)` (raises TypeError on None)
    # or `arguments[name]` (raises TypeError on a list, since `name`
    # is a string key, not an index) ever run. Found live (external
    # audit): arguments=None and arguments=["on"] both crashed with an
    # uncaught TypeError instead of the controlled rejection
    # validate_shape exists to guarantee for a whole proposal batch.
    if not isinstance(arguments, dict):
        return False
    if set(arguments) != set(spec.argument_types):
        return False
    for name, expected_type in spec.argument_types.items():
        value = arguments[name]
        # bool is an int subclass in Python - an int-typed argument must
        # never silently accept True/False, and a bool-typed argument
        # must never accept a plain 0/1. isinstance alone conflates
        # both directions, so both are checked explicitly.
        if expected_type is bool and not isinstance(value, bool):
            return False
        if expected_type is int and (isinstance(value, bool) or not isinstance(value, int)):
            return False
        if expected_type not in (bool, int) and not isinstance(value, expected_type):
            return False
    return True


class ActionExecutor:
    def __init__(self, resolver: TargetResolver, caller: ServiceCaller) -> None:
        self._resolver = resolver
        self._caller = caller

    def validate_shape(self, draft: ActionDraft) -> bool:
        """Static, side-effect-free checks only - operation known,
        target kind allowed for it, arguments well-typed. Does NOT
        resolve a target or check policy (that needs a live registry
        lookup, and for an action - not a query - IS a side effect
        risk in itself if done per-proposal ahead of time). Lets a
        caller with a whole batch of proposals (a SATELLITE backend's
        multi-action reply) reject an internally-malformed batch
        before running any of it, rather than discovering proposal #3
        is garbage after #1 and #2 already had real effects."""
        # The proposal object itself is untrusted, same as any of its
        # fields - a backend reply's "proposals" list is built with no
        # validation of its own, so a malformed entry could be None or
        # any other non-ActionDraft object, not just an ActionDraft
        # with a bad field. draft.operation below would otherwise
        # raise AttributeError straight out of the batch check in
        # router.py's _consult_backend (`all(validate_shape(d) for d
        # in proposals)`), uncaught, all the way to the router's own
        # caller - never a controlled rejection. Found live (external
        # audit): proposal=None reproduced exactly that crash.
        if not isinstance(draft, ActionDraft):
            return False
        spec = OPERATION_CATALOG.get(draft.operation)
        if spec is None:
            return False
        if draft.target.kind not in spec.allowed_target_kinds:
            return False
        if not _valid_target_shape(draft.target):
            return False
        return _validate_arguments(spec, draft.arguments)

    async def execute(
        self,
        draft: ActionDraft,
        policies: ScopedAreaPolicy,
        *,
        context: object | None = None,
        source_area_id: str | None = None,
    ) -> ExecutionResult:
        spec = OPERATION_CATALOG.get(draft.operation)
        if spec is None:
            return ExecutionResult(
                status=ExecutionStatus.UNSUPPORTED,
                reason_code="unknown_operation",
                data={"operation": draft.operation},
            )

        if draft.target.kind not in spec.allowed_target_kinds:
            return ExecutionResult(
                status=ExecutionStatus.INVALID,
                reason_code="target_kind_not_allowed_for_operation",
            )

        if not _valid_target_shape(draft.target):
            return ExecutionResult(status=ExecutionStatus.INVALID, reason_code="malformed_target")

        if not _validate_arguments(spec, draft.arguments):
            return ExecutionResult(status=ExecutionStatus.INVALID, reason_code="bad_arguments")

        # Action/query classification comes from the operation catalog
        # itself, never the caller - see ScopedAreaPolicy's own
        # docstring on why the two are never a single shared policy.
        policy = policies.actions if spec.kind == "action" else policies.queries

        target = draft.target
        if target.kind == "source_area":
            # Opt-in per command, never a silent fallback for
            # area_name/entity_name (those still mean exactly what the
            # user said, or nothing). No source area for this request
            # (e.g. a text-only frontend with no device behind it) is
            # a controlled rejection, same as any other unresolved
            # target - it must NEVER fall back to all_allowed, that
            # would turn a one-room command into a whole-house one.
            if not source_area_id:
                return ExecutionResult(status=ExecutionStatus.INVALID, reason_code="no_source_area")
            target = ActionTarget(kind="area_id", value=source_area_id)

        if draft.operation == "vacuum.clean_area":
            return await self._execute_vacuum_clean_area(target, policy, context=context)

        domain_filter = {
            "light.set_power": "light",
            "media_player.skip_track": "media_player",
        }.get(draft.operation)
        resolved = await self._resolver.resolve(target, domain_filter=domain_filter)
        if isinstance(resolved, TargetNotFound):
            return ExecutionResult(status=ExecutionStatus.INVALID, reason_code="target_not_found")

        entities = resolved.entities
        if draft.target.kind == "all_allowed":
            # "all_allowed" means everything Operation AND Policy
            # permit (stage-1/README.md) - forbidden members are
            # quietly excluded, not cause for denying the batch. A
            # named target (area_name, entity_name, ...) is the
            # opposite: the user asked for something specific, so any
            # forbidden member denies the whole request instead of
            # silently narrowing it - see the `denied` check below.
            entities = tuple(e for e in entities if _entity_permitted(e, policy))
        if not entities:
            return ExecutionResult(status=ExecutionStatus.INVALID, reason_code="no_matching_targets")

        if draft.target.kind != "all_allowed":
            denied = [e for e in entities if not _entity_permitted(e, policy)]
            if denied:
                # Fail the whole set, never a silent partial execution.
                return ExecutionResult(
                    status=ExecutionStatus.DENIED,
                    reason_code="area_not_permitted",
                    data={"denied_entity_count": len(denied)},
                )

        entity_ids = tuple(e.entity_id for e in entities)

        if spec.kind == "query":
            state = await self._caller.get_entity_state(entity_ids[0])
            if state is None:
                return ExecutionResult(status=ExecutionStatus.FAILED, reason_code="no_state")
            return ExecutionResult(status=ExecutionStatus.QUERY_ANSWER, reason_code="ok", data=state)

        if draft.operation == "media_player.skip_track":
            accepted = await self._caller.skip_track(entity_ids, draft.arguments["forward"], context=context)
        else:
            accepted = await self._caller.set_light_power(entity_ids, draft.arguments["on"], context=context)
        if not accepted:
            return ExecutionResult(status=ExecutionStatus.FAILED, reason_code="service_call_rejected")
        return ExecutionResult(
            status=ExecutionStatus.SUCCESS, reason_code="ok", data={"entity_ids": entity_ids}
        )

    async def _execute_vacuum_clean_area(
        self, target: ActionTarget, policy: AreaPolicy, *, context: object | None = None
    ) -> ExecutionResult:
        area_id = await self._resolver.resolve_area(target)
        if isinstance(area_id, TargetNotFound):
            return ExecutionResult(status=ExecutionStatus.INVALID, reason_code="target_not_found")
        if not policy.permits_area(area_id):
            return ExecutionResult(status=ExecutionStatus.DENIED, reason_code="area_not_permitted")

        outcome = await self._caller.clean_vacuum_area(area_id, context=context)
        if outcome.status == "started":
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                reason_code="ok",
                data={"vacuum_entity_id": outcome.vacuum_entity_id, "room_name": outcome.room_name},
            )
        if outcome.status == "no_match":
            return ExecutionResult(status=ExecutionStatus.INVALID, reason_code="no_matching_targets")
        if outcome.status == "ambiguous":
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                reason_code="ambiguous_vacuum_match",
                data={"candidates_description": outcome.candidates_description},
            )
        return ExecutionResult(status=ExecutionStatus.FAILED, reason_code="vacuum_error")
