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

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from .models import (
    QUERY_RESPONSE_FIELDS_BY_DOMAIN,
    ActionDraft,
    ActionStep,
    ActionTarget,
    AreaPolicy,
    ExecutionResult,
    ExecutionStatus,
    QueryPlan,
    QuerySourceSpec,
    QueryStep,
    ScopedAreaPolicy,
    StepPlan,
)

# The declarative operation/capability catalog lives in core/operations.py -
# this module is execution/validation only. Re-exported here (not just used
# internally) so existing importers (core/studio.py, studio.py, tests) keep
# working unchanged: `from .core.executor import OPERATION_CATALOG` remains
# valid, it just re-reads the one shared dict instead of owning a copy.
from .operations import OPERATION_CATALOG, OperationSpec
from .step_variables import (
    CONFIRMABLE_ACTION_END_STATES,
    MAX_CONFIRMATION_ENTITIES,
    SINGLE_ENTITY_TARGET_KINDS,
    action_step_fields,
)


@dataclass(frozen=True)
class ResolvedEntity:
    entity_id: str
    area_id: str | None


@dataclass(frozen=True)
class ResolvedTargets:
    entities: tuple[ResolvedEntity, ...]
    diagnostics: tuple[dict, ...] = ()
    raw: object = None


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
        self, entity_ids: tuple[str, ...], on: bool, *, area_id: str | None = None,
        context: object | None = None
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

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_ids: tuple[str, ...],
        data: dict,
        *,
        area_id: str | None = None,
        context: object | None = None,
    ) -> bool:
        """Call one of the executor's closed, pre-mapped HA services.

        `domain`/`service` are selected by ActionExecutor from its own
        whitelist, never taken directly from imported spellbook data.
        """
        ...

    async def get_entity_state(self, entity_id: str) -> dict | None:
        """Returns {"state": ..., "unit": ... | None} or None if the
        entity has no current state. Read-only - no context to
        attribute."""
        ...

    async def skip_track(
        self, entity_ids: tuple[str, ...], forward: bool, *, area_id: str | None = None,
        context: object | None = None
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


# A variable with no usable value (missing entity, unavailable state, empty
# aggregation, ...) renders as this short neutral token instead of a raw
# "None"/leftover "{var}" or a crashed turn (item 14 - Fehlerverhalten).
_MISSING_VALUE_TOKEN = "?"


def _sanitize_response_value(value: object) -> object:
    """None -> the missing-value token (unchanged existing behavior); a
    list/tuple (the "names"/"states" aggregate kinds, or a Combined Spell
    step's failed_names) -> a human-joined string, since a raw Python list
    formatted into a sentence would read as "['a', 'b']" - never a
    special case per kind, just "any list/tuple value gets joined"."""
    if value is None:
        return _MISSING_VALUE_TOKEN
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) if value else _MISSING_VALUE_TOKEN
    return value


def _render_query_response(template: str, variables: dict) -> str:
    safe_values = {name: _sanitize_response_value(value) for name, value in variables.items()}
    try:
        return template.format_map(safe_values)
    except (KeyError, IndexError, ValueError, AttributeError, TypeError):
        # A malformed template shouldn't reach here (Studio validates
        # variable names against declared sources at save time) - degrade
        # to the raw template rather than losing the whole response.
        return template


def response_text_for_success(execution: ExecutionResult) -> str:
    """The rendered text for a SUCCESS/QUERY_ANSWER ExecutionResult -
    shared by core.router (a spoken/text turn) and core.mcp_execution (an
    MCP tools/call), so neither ever invents its own success wording. A
    QUERY_ANSWER's own response_template (execute_query_plan/execute_steps)
    or entity.get_state's response_template already rendered into
    data["response_text"] when the draft/plan declared one; otherwise this
    falls back to the raw state+unit, same as always."""
    if execution.status is ExecutionStatus.QUERY_ANSWER:
        if isinstance(execution.data.get("response_text"), str):
            return execution.data["response_text"]
        state = execution.data.get("state")
        unit = execution.data.get("unit")
        if state is None:
            return "Kein Zustand verfügbar."
        return f"{state}{unit}" if unit else str(state)
    return "Erledigt."


class _AliasNamespace:
    """A per-alias response-variable namespace for a Combined Spell step
    ({alias.field} response variables) - `str.format`'s dotted-field
    syntax resolves via ordinary getattr for free once a plain identifier
    is bound to an object exposing that attribute. Built exhaustively (see
    ActionExecutor.execute_steps) so every declared field is present -
    never left absent, since a genuinely missing attribute would raise
    AttributeError, which _render_query_response's `except` would then
    (incorrectly, for this per-field case) degrade the WHOLE response text
    over, not just this one field."""

    def __init__(self, values: dict[str, object]) -> None:
        self.__dict__.update(values)


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
    if target.kind == "template":
        return isinstance(target.value, str) and 0 < len(target.value.strip()) <= 8192
    if target.kind == "entity_ids":
        return isinstance(target.value, tuple) and 0 < len(target.value) <= 256 and all(
            isinstance(v, str) and bool(v.strip()) for v in target.value)
    if target.kind in _TARGET_KINDS_REQUIRING_A_VALUE:
        return isinstance(target.value, str) and bool(target.value.strip())
    return True


_MAX_ARGUMENT_STRING_LENGTH = 100


def _argument_type_matches(value, expected_type: type) -> bool:
    if expected_type is bool:
        return isinstance(value, bool)
    if expected_type is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type is str:
        # A bound against nonsense/abusive payloads on the string-typed
        # arguments (hvac_mode, effect, source, option, ...) - HA's own
        # service call still enforces whether the value is actually valid
        # for the target entity, same non-goal as int_ranges/float_ranges.
        return isinstance(value, str) and 0 < len(value) <= _MAX_ARGUMENT_STRING_LENGTH
    return isinstance(value, expected_type)


def _validate_arguments(spec: OperationSpec, arguments) -> bool:
    if not isinstance(arguments, dict):
        return False

    # The primary schema tolerates any subset of spec.optional_argument_types
    # alongside the REQUIRED argument_types keys (light.turn_on's color_name/
    # brightness_pct: either, both, or neither may be present in one call) -
    # argument_variants stay an exact-match ALTERNATIVE schema, unaffected
    # (a mutually exclusive representation, e.g. light.set_power's {"on": bool}
    # instead of {"percentage": int}, never combined with optional additions).
    # optional_argument_types defaults to {} for every operation that doesn't
    # declare any, which collapses this back to the original "== required"
    # check - zero behavior change for the rest of the catalog.
    present = set(arguments)
    required = set(spec.argument_types)
    optional = spec.optional_argument_types
    primary_ok = required <= present <= required | set(optional) and all(
        _argument_type_matches(arguments[name], spec.argument_types.get(name, optional.get(name)))
        for name in present
    )
    variant_ok = any(
        present == set(schema)
        and all(_argument_type_matches(arguments[name], expected) for name, expected in schema.items())
        for schema in spec.argument_variants
    )
    if not (primary_ok or variant_ok):
        return False

    for name, (minimum, maximum) in spec.int_ranges.items():
        if name not in arguments:
            continue
        value = arguments[name]
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            return False

    for name, (minimum, maximum) in spec.float_ranges.items():
        if name not in arguments:
            continue
        value = arguments[name]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not minimum <= value <= maximum
        ):
            return False

    rgb_color = arguments.get("rgb_color")
    if rgb_color is not None and (
        not isinstance(rgb_color, list)
        or len(rgb_color) != 3
        or any(
            not isinstance(channel, int)
            or isinstance(channel, bool)
            or not 0 <= channel <= 255
            for channel in rgb_color
        )
    ):
        return False
    return True



class ActionExecutor:
    def __init__(self, resolver: TargetResolver, caller: ServiceCaller,
                 expert_allowed: Callable[[], bool] | None = None,
                 operation_allowed: Callable[[str], bool] | None = None) -> None:
        self._resolver = resolver
        self._caller = caller
        self._expert_allowed = expert_allowed or (lambda: False)
        self._operation_allowed = operation_allowed or (lambda operation: True)

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
        if not isinstance(draft.operation, str) or not isinstance(draft.target, ActionTarget):
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
        dry_run: bool = False,
    ) -> ExecutionResult:
        if not isinstance(draft, ActionDraft) or not isinstance(draft.target, ActionTarget) or not isinstance(draft.operation, str):
            return ExecutionResult(ExecutionStatus.INVALID, "malformed_action")
        # Permission is supplied by local configuration, never by a spell/import.
        # Operations marked expert_only are gated even if an imported spell omits
        # draft.expert=True.
        spec = OPERATION_CATALOG.get(draft.operation)
        if draft.expert or (spec is not None and spec.expert_only) or draft.target.kind == "template":
            fallback = draft.user_fallback
            if fallback is not None and (fallback.expert or fallback.target.kind == "template" or fallback.user_fallback):
                fallback = None
            if not self._expert_allowed():
                if fallback is not None:
                    result = await self.execute(fallback, policies, context=context,
                                                source_area_id=source_area_id, dry_run=dry_run)
                    return replace(result, data={**result.data, "user_fallback": True,
                                                 "effective_operation": fallback.operation,
                                                 "effective_catchphrase": fallback.catchphrase})
                return ExecutionResult(ExecutionStatus.DENIED, "expert_disabled")
            # Resolve expert once before any effect. Never fallback after a service failure.
            prepared = await self._execute(replace(draft, user_fallback=None), policies,
                                           context=context, source_area_id=source_area_id, dry_run=True)
            if prepared.status != ExecutionStatus.SUCCESS and prepared.reason_code != "vacuum_mapping_pending":
                if fallback is not None:
                    result = await self.execute(fallback, policies, context=context,
                                                source_area_id=source_area_id, dry_run=dry_run)
                    return replace(result, data={**result.data, "user_fallback": True,
                                                 "effective_operation": fallback.operation,
                                                 "effective_catchphrase": fallback.catchphrase,
                                                 "expert_error": prepared.reason_code})
                return prepared
            if dry_run:
                return prepared
            # Freeze the checked targets; execute checks exposure/policy again.
            if draft.target.kind == "template":
                draft = replace(draft, target=ActionTarget("entity_ids", tuple(prepared.data["entity_ids"])))
        return await self._execute(draft, policies, context=context,
                                   source_area_id=source_area_id, dry_run=dry_run)

    async def _execute(self, draft, policies, *, context=None, source_area_id=None, dry_run=False):
        if not self._operation_allowed(draft.operation):
            return ExecutionResult(ExecutionStatus.DENIED, "operation_disabled")
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
            return await self._execute_vacuum_clean_area(target, policy, context=context, dry_run=dry_run)

        resolved = await self._resolver.resolve(target, domain_filter=spec.domain_filter)
        if isinstance(resolved, TargetNotFound):
            return ExecutionResult(status=ExecutionStatus.INVALID, reason_code="target_not_found")

        entities = resolved.entities
        diagnostics = list(resolved.diagnostics)
        diagnostics.extend({"entity_id": e.entity_id, "area_id": e.area_id,
                            "reason": "allowed" if _entity_permitted(e, policy) else "area_not_permitted"}
                           for e in entities)
        details = {"raw": resolved.raw, "targets": diagnostics}
        if draft.target.kind == "entity_ids" and resolved.diagnostics:
            return ExecutionResult(ExecutionStatus.INVALID, "target_not_found", details)
        if draft.target.kind in ("all_allowed", "template"):
            # "all_allowed" means everything Operation AND Policy
            # permit (stage-1/README.md) - forbidden members are
            # quietly excluded, not cause for denying the batch. A
            # named target (area_name, entity_name, ...) is the
            # opposite: the user asked for something specific, so any
            # forbidden member denies the whole request instead of
            # silently narrowing it - see the `denied` check below.
            entities = tuple(e for e in entities if _entity_permitted(e, policy))
        if not entities:
            return ExecutionResult(status=ExecutionStatus.INVALID, reason_code="no_matching_targets", data=details)

        if draft.target.kind not in ("all_allowed", "template"):
            denied = [e for e in entities if not _entity_permitted(e, policy)]
            if denied:
                # Fail the whole set, never a silent partial execution.
                return ExecutionResult(
                    status=ExecutionStatus.DENIED,
                    reason_code="area_not_permitted",
                    data={**details, "denied_entity_count": len(denied)},
                )

        entity_ids = tuple(e.entity_id for e in entities)
        if spec.kind == "query":
            state = await self._caller.get_entity_state(entity_ids[0])
            if state is None:
                return ExecutionResult(status=ExecutionStatus.FAILED, reason_code="no_state")
            if draft.response_template and any(
                f"{{{name}}}" in draft.response_template
                for fields in QUERY_RESPONSE_FIELDS_BY_DOMAIN.values()
                for name in fields
            ):
                values = {
                    name: ""
                    for fields in QUERY_RESPONSE_FIELDS_BY_DOMAIN.values()
                    for name in fields
                }
                values.update({key: "" if value is None else value for key, value in state.items()})
                try:
                    state = {**state, "response_text": draft.response_template.format_map(values)}
                except (KeyError, ValueError, AttributeError, TypeError):
                    pass
            return ExecutionResult(status=ExecutionStatus.QUERY_ANSWER, reason_code="ok", data=state)

        if dry_run:
            return ExecutionResult(ExecutionStatus.SUCCESS, "dry_run",
                                   {**details, "entity_ids": entity_ids})

        accepted = await self._dispatch_action(
            draft,
            entity_ids,
            context=context,
        )
        if accepted is None:
            # Should be unreachable while catalog/dispatch stay in sync; fail closed.
            return ExecutionResult(
                status=ExecutionStatus.UNSUPPORTED,
                reason_code="unknown_operation",
                data={"operation": draft.operation},
            )
        if not accepted:
            # entity_ids is included even on rejection (not just SUCCESS,
            # below) so a Combined Spell step (core.executor.ActionExecutor.
            # execute_steps) can still report a real target_count/
            # failed_names for a batch the service call rejected outright -
            # no existing caller reads a "data" key on this result today.
            return ExecutionResult(status=ExecutionStatus.FAILED, reason_code="service_call_rejected",
                                   data={"entity_ids": entity_ids})
        return ExecutionResult(
            status=ExecutionStatus.SUCCESS, reason_code="ok", data={"entity_ids": entity_ids}
        )

    async def _dispatch_action(
        self,
        draft: ActionDraft,
        entity_ids: tuple[str, ...],
        *,
        area_id: str | None = None,
        context: object | None = None,
    ) -> bool | None:
        """Dispatch one validated action to either entity_ids or a native HA area_id."""
        target_kwargs = {"context": context}
        if area_id is not None:
            target_kwargs["area_id"] = area_id

        if draft.operation == "media_player.skip_track":
            return await self._caller.skip_track(
                entity_ids,
                draft.arguments["forward"],
                **target_kwargs,
            )

        if draft.operation == "light.set_power":
            if "percentage" in draft.arguments:
                percentage = draft.arguments["percentage"]
                if percentage == 0:
                    return await self._caller.call_service(
                        "light",
                        "turn_off",
                        entity_ids,
                        {},
                        **target_kwargs,
                    )
                return await self._caller.call_service(
                    "light",
                    "turn_on",
                    entity_ids,
                    {"brightness_pct": percentage},
                    **target_kwargs,
                )
            return await self._caller.set_light_power(
                entity_ids,
                draft.arguments["on"],
                **target_kwargs,
            )

        spec = OPERATION_CATALOG.get(draft.operation)
        if spec is not None and spec.service is not None:
            domain, service = spec.service
            service_data = dict(draft.arguments)

            if draft.operation == "media_player.volume_set":
                # Expert input is intentionally a simple integer scale 0..10.
                # Home Assistant expects volume_level as a float 0.0..1.0.
                service_data = {
                    "volume_level": draft.arguments["volume_level"] / 10.0
                }
            elif draft.operation == "media_player.volume_mute":
                # This expert operation means mute-on, not toggle.
                service_data = {"is_volume_muted": True}
            elif draft.operation == "light.set_color_temperature":
                # Home Assistant's light.turn_on expects "color_temp_kelvin",
                # not the catalog's user-facing "kelvin" argument key (kept
                # as-is so a spell authored against this operation needs no
                # migration) - same translate-only-at-the-final-call pattern
                # as volume_set/volume_mute above.
                service_data = {"color_temp_kelvin": draft.arguments["kelvin"]}

            return await self._caller.call_service(
                domain,
                service,
                entity_ids,
                service_data,
                **target_kwargs,
            )

        return None

    async def _execute_vacuum_clean_area(
        self, target: ActionTarget, policy: AreaPolicy, *, context: object | None = None, dry_run: bool = False
    ) -> ExecutionResult:
        area_id = await self._resolver.resolve_area(target)
        if isinstance(area_id, TargetNotFound):
            return ExecutionResult(status=ExecutionStatus.INVALID, reason_code="target_not_found")
        if not policy.permits_area(area_id):
            return ExecutionResult(status=ExecutionStatus.DENIED, reason_code="area_not_permitted")

        if dry_run:
            return ExecutionResult(ExecutionStatus.NOT_EXECUTED, "vacuum_mapping_pending", {
                "area_id": area_id, "entity_ids": [],
                "warnings": ["Sauger-/Raum-Zuordnung wird erst bei Ausführung vom vorhandenen Provider ermittelt."],
            })

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

    async def execute_query_plan(
        self,
        plan: QueryPlan,
        policies: ScopedAreaPolicy,
        *,
        context: object | None = None,
        source_area_id: str | None = None,
    ) -> ExecutionResult:
        """The multi-source-query sibling of execute() (item 4/5) - resolves
        every source through the exact same TargetResolver/AreaPolicy an
        action would use, merges their variables into one dict, and renders
        the response template. A per-source data problem (missing entity,
        unavailable state, empty aggregation, ...) degrades ONLY that
        variable to a safe placeholder and is reported in data["sources"]
        for the Wizard's Preview (item 9/14) - it never aborts the whole
        query. The one thing that DOES abort the whole query is a policy
        DENIAL on a named (non-all_allowed) source target, mirroring
        execute()'s own "a named target with a forbidden member denies the
        whole request" rule - a security boundary, not a data-availability
        one, so it must never silently degrade into a "?" that reads like
        "tried but nothing there"."""
        variables: dict[str, object] = {}
        source_reports: list[dict] = []
        for source in plan.sources:
            value, unit_value, report = await self._resolve_query_source(
                source, policies.queries, source_area_id=source_area_id,
            )
            if report["status"] == "denied":
                return ExecutionResult(
                    status=ExecutionStatus.DENIED,
                    reason_code="area_not_permitted",
                    data={"sources": [*source_reports, report]},
                )
            variables[source.variable] = value
            if source.unit_variable:
                variables[source.unit_variable] = unit_value
            source_reports.append(report)

        response_text = (
            _render_query_response(plan.response_template, variables)
            if plan.response_template else None
        )
        any_soft_failure = any(report["status"] != "ok" for report in source_reports)
        return ExecutionResult(
            status=ExecutionStatus.QUERY_ANSWER,
            reason_code="ok" if not any_soft_failure else "partial",
            data={"response_text": response_text, "variables": variables, "sources": source_reports},
        )

    async def execute_steps(
        self,
        plan: StepPlan,
        policies: ScopedAreaPolicy,
        *,
        context: object | None = None,
        source_area_id: str | None = None,
        dry_run: bool = False,
    ) -> ExecutionResult:
        """Combined Spells' sibling of execute()/execute_query_plan() -
        runs every step SEQUENTIALLY (no real parallelism this pass - the
        brief permits it "only where simple/bounded/safe", punting on it
        entirely is the safer choice for a first implementation), reusing
        execute() for an ActionStep and _resolve_query_source for a
        QueryStep's own sources - the exact same TargetResolver/AreaPolicy/
        ServiceCaller machinery a single action or a flat multi-source
        query already uses, never a parallel execution path.

        Every step's alias contributes its own namespace of response
        variables regardless of THAT step's own outcome - a later step's
        results are never blanked out by an earlier step's failure, so a
        response like "8 von 9 Rollos wurden geschlossen... Noch offen:
        Küchenfenster" (some steps ok, one failed) stays possible. Only a
        DENIED step (a policy/security boundary, same as
        execute_query_plan's "a named target with a forbidden member
        denies the whole request" rule) aborts every remaining step -
        anything else (INVALID/FAILED/UNSUPPORTED) degrades only that
        step's own alias and continues.

        A single HA service call is a batch accept/reject, not per-entity
        confirmation - see step_variables.CONFIRMABLE_ACTION_END_STATES'
        docstring for exactly which operations get a real per-entity
        success/failure count (via a bounded post-action state re-read)
        versus the honest batch-level fallback every other operation gets.

        `dry_run=True` (the Studio's "Testen - ohne Geräteaufruf" preview,
        same flag execute() already has) threads straight through to each
        action step's own execute() call - no service is actually called,
        and the per-entity confirmation re-read is skipped too (there is
        nothing new to confirm when nothing happened - see
        _execute_action_step). Query steps are read-only already, so
        nothing changes for them."""
        variables: dict[str, dict[str, object]] = {}
        step_reports: list[dict] = []
        for step in plan.steps:
            if isinstance(step, ActionStep):
                fields, report = await self._execute_action_step(
                    step, policies, context=context, source_area_id=source_area_id, dry_run=dry_run,
                )
            else:
                fields, report = await self._execute_query_step(
                    step, policies.queries, source_area_id=source_area_id,
                )
            step_reports.append(report)
            if report["status"] == "denied":
                return ExecutionResult(
                    status=ExecutionStatus.DENIED,
                    reason_code=report.get("reason_code") or "area_not_permitted",
                    data={"steps": step_reports},
                )
            if step.alias:
                variables[step.alias] = fields

        # `variables` (returned in data["variables"], same convention as
        # execute_query_plan's own raw dict above) keeps raw values -
        # sanitizing (None -> "?", list -> joined string) happens only for
        # the ephemeral namespace objects used to render response_text,
        # never mutating what's reported back to a caller/the Studio.
        render_namespace = {
            alias: _AliasNamespace({name: _sanitize_response_value(value) for name, value in fields.items()})
            for alias, fields in variables.items()
        }
        response_text = (
            _render_query_response(plan.response_template, render_namespace)
            if plan.response_template else None
        )
        any_soft_failure = any(report["status"] != "ok" for report in step_reports)
        return ExecutionResult(
            status=ExecutionStatus.QUERY_ANSWER,
            reason_code="ok" if not any_soft_failure else "partial",
            data={"response_text": response_text, "variables": variables, "steps": step_reports},
        )

    async def _execute_action_step(
        self, step: ActionStep, policies: ScopedAreaPolicy, *, context, source_area_id, dry_run: bool = False,
    ) -> tuple[dict[str, object], dict]:
        """Runs one action step through the ordinary execute() path
        unchanged, then derives its declared response fields (see
        step_variables.action_step_fields) from the outcome. USER-tier
        only (see ActionStep's docstring) - an expert_only operation
        used as a step is denied by execute() itself exactly like any
        other expert-gated action with no fallback.

        `dry_run=True` threads straight into execute() (no service is
        actually called) and skips the per-entity confirmation re-read
        below - there is nothing new to confirm when nothing happened,
        so a dry-run's aggregate outcome always uses the honest batch
        semantics (target_count/success_count from the resolved targets
        and the dry-run's own accept/reject, same as a real but
        non-confirmable operation would report)."""
        draft = ActionDraft(operation=step.operation, target=step.target, arguments=step.arguments)
        execution = await self.execute(draft, policies, context=context, source_area_id=source_area_id, dry_run=dry_run)
        if execution.status is ExecutionStatus.DENIED:
            return {}, {"alias": step.alias, "status": "denied", "reason_code": execution.reason_code}

        success = execution.status is ExecutionStatus.SUCCESS
        entity_ids = tuple(execution.data.get("entity_ids") or ())
        fields = action_step_fields(step.operation, step.target.kind)
        values: dict[str, object] = {}

        if step.target.kind in SINGLE_ENTITY_TARGET_KINDS:
            entity_id = entity_ids[0] if entity_ids else None
            state = await self._caller.get_entity_state(entity_id) if entity_id else None
            for field in fields:
                values[field] = success if field == "success" else (state.get(field) if state is not None else None)
        else:
            target_count = len(entity_ids)
            confirmable = step.operation in CONFIRMABLE_ACTION_END_STATES
            if not dry_run and success and confirmable and 0 < target_count <= MAX_CONFIRMATION_ENTITIES:
                expected = CONFIRMABLE_ACTION_END_STATES[step.operation]
                success_count = 0
                failed_names: list[str] = []
                for entity_id in entity_ids:
                    state = await self._caller.get_entity_state(entity_id)
                    if state is not None and state.get("state") == expected:
                        success_count += 1
                    else:
                        failed_names.append(state.get("entity_name") if state is not None else entity_id)
                values = {
                    "target_count": target_count, "success_count": success_count,
                    "failed_count": target_count - success_count, "failed_names": failed_names,
                }
            else:
                # Honest batch semantics: a single HA service call is
                # accept/reject for the whole set, not per-entity - see
                # execute_steps' own docstring.
                values = {
                    "target_count": target_count,
                    "success_count": target_count if success else 0,
                    "failed_count": 0 if success else target_count,
                    "failed_names": [],
                }

        return values, {
            "alias": step.alias, "status": "ok" if success else "failed", "reason_code": execution.reason_code,
        }

    async def _execute_query_step(
        self, step: QueryStep, policy: AreaPolicy, *, source_area_id,
    ) -> tuple[dict[str, object], dict]:
        """Runs one query step's sources through _resolve_query_source
        unchanged - the exact same per-source resolution a flat
        `sources:` query already uses, just merged into this step's own
        alias namespace instead of one flat variables dict."""
        values: dict[str, object] = {}
        source_reports: list[dict] = []
        for source in step.sources:
            value, unit_value, report = await self._resolve_query_source(
                source, policy, source_area_id=source_area_id,
            )
            if report["status"] == "denied":
                return {}, {"alias": step.alias, "status": "denied", "reason_code": "area_not_permitted"}
            values[source.variable] = value
            if source.unit_variable:
                values[source.unit_variable] = unit_value
            source_reports.append(report)
        status = "ok" if all(r["status"] == "ok" for r in source_reports) else "partial"
        return values, {"alias": step.alias, "status": status, "sources": source_reports}

    async def _resolve_query_source(
        self, source: QuerySourceSpec, policy: AreaPolicy, *, source_area_id: str | None,
    ) -> tuple[object, object, dict]:
        """Returns (value, unit_value, report). report["status"] is "ok",
        "denied" (the whole plan must be denied - see execute_query_plan),
        or one of item 14's soft per-source failure codes:
        no_source_area, target_not_found, ambiguous_source, unavailable,
        missing_attribute, empty_aggregation."""
        target = source.target
        if target.kind == "source_area":
            if not source_area_id:
                return None, None, {"variable": source.variable, "status": "no_source_area"}
            target = ActionTarget(kind="area_id", value=source_area_id)

        resolved = await self._resolver.resolve(target, domain_filter=source.domain_filter)
        if isinstance(resolved, TargetNotFound):
            return None, None, {"variable": source.variable, "status": "target_not_found"}

        entities = resolved.entities
        if target.kind == "all_allowed":
            entities = tuple(e for e in entities if _entity_permitted(e, policy))
        elif any(not _entity_permitted(e, policy) for e in entities):
            return None, None, {"variable": source.variable, "status": "denied"}

        if source.device_class is not None:
            filtered = []
            for entity in entities:
                state = await self._caller.get_entity_state(entity.entity_id)
                if state is not None and state.get("device_class") == source.device_class:
                    filtered.append(entity)
            entities = tuple(filtered)

        is_aggregate = source.kind in ("count", "min", "max", "avg", "sum", "names", "states")
        if not is_aggregate:
            if not entities:
                return None, None, {"variable": source.variable, "status": "target_not_found"}
            if len(entities) > 1:
                return None, None, {"variable": source.variable, "status": "ambiguous_source"}
            state = await self._caller.get_entity_state(entities[0].entity_id)
            if state is None:
                return None, None, {"variable": source.variable, "status": "target_not_found"}
            if source.kind == "attribute":
                attributes = state.get("attributes")
                if not isinstance(attributes, dict) or source.attribute not in attributes:
                    return None, None, {"variable": source.variable, "status": "missing_attribute"}
                return attributes[source.attribute], None, {"variable": source.variable, "status": "ok"}
            raw_state = state.get("state")
            unavailable = raw_state is None or raw_state in ("unknown", "unavailable")
            unit_value = state.get("unit") if source.unit_variable else None
            return (
                None if unavailable else raw_state,
                unit_value,
                {"variable": source.variable, "status": "unavailable" if unavailable else "ok"},
            )

        if source.state_filter is not None:
            matching = []
            for entity in entities:
                state = await self._caller.get_entity_state(entity.entity_id)
                if state is not None and state.get("state") == source.state_filter:
                    matching.append(entity)
            entities = tuple(matching)

        if source.kind == "count":
            return len(entities), None, {"variable": source.variable, "status": "ok", "matched": len(entities)}

        if source.kind in ("names", "states"):
            # A zero-match set is a valid, reported answer here too - same
            # "count of zero is not an error" convention as above, degraded
            # to the missing-value token only at render time (an empty
            # list has nothing useful to join into a sentence).
            values: list[object] = []
            for entity in entities:
                state = await self._caller.get_entity_state(entity.entity_id)
                if source.kind == "names":
                    values.append(state.get("entity_name") if state is not None else entity.entity_id)
                else:
                    values.append(state.get("state") if state is not None else None)
            return values, None, {"variable": source.variable, "status": "ok", "matched": len(values)}

        numeric_values = []
        for entity in entities:
            state = await self._caller.get_entity_state(entity.entity_id)
            if state is None:
                continue
            try:
                numeric_values.append(float(state.get("state")))
            except (TypeError, ValueError):
                continue
        if not numeric_values:
            return None, None, {"variable": source.variable, "status": "empty_aggregation"}
        aggregate = {
            "min": min, "max": max,
            "avg": lambda values: round(sum(values) / len(values), 1),
            "sum": lambda values: round(sum(values), 1),
        }[source.kind](numeric_values)
        return aggregate, None, {"variable": source.variable, "status": "ok", "matched": len(numeric_values)}
