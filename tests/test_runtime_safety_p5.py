"""P5 runtime safety contract - restored regression tests.

Runtime and the static Collision Analyzer must use the same shared, HA-free
typed safety proof (core.action_semantics.same_target_opposite_outcomes) for
every statically provable case.  A real safety ambiguity must never execute
any action.  The Semantics-v1 boolean-flip fallback stays unchanged.
"""
from __future__ import annotations

import asyncio
import itertools

import pytest

from custom_components.hase.core.action_semantics import (
    ActionOutcome,
    StaticActionTarget,
    TargetRelation,
    canonical_action_outcome,
    same_target_opposite_outcomes,
    target_relation,
)
from custom_components.hase.core.collision_analyzer import analyze_collisions
from custom_components.hase.core.executor import (
    ActionExecutor,
    ResolvedEntity,
    ResolvedTargets,
    TargetNotFound,
)
from custom_components.hase.core.models import (
    ActionDraft,
    ActionTarget,
    AreaPolicy,
    ConversationRequest,
    ConversationStatus,
    EngineAmbiguous,
    EngineMatch,
    FrontendMode,
    ScopedAreaPolicy,
)
from custom_components.hase.core.router import AgentConfig, SpellbookRouter
from custom_components.hase.core.spellbook_engine import (
    CommandAmbiguity,
    CommandMatch,
    _drafts_are_opposite,
    find_command,
    load_spellbook_from_dict,
    match,
)

PHRASE = "teste leuchtfeuer Gondor"

ON_SET_POWER = ("light.set_power", {"on": True})
OFF_SET_POWER = ("light.set_power", {"on": False})
ON_TURN_ON = ("light.turn_on", None)
OFF_TURN_OFF = ("light.turn_off", None)


def _command(spell_id, action, *, capture="area", trigger=None):
    operation, arguments = action
    raw = {
        "id": spell_id,
        "trigger": {"de": trigger or f"teste leuchtfeuer {{{capture}}}"},
        "operation": operation,
        "target": {"kind": "area_name", "capture": capture},
    }
    if arguments is not None:
        raw["arguments"] = arguments
    return raw


def _book(*commands):
    book = load_spellbook_from_dict("p5_runtime_safety", {
        "id": "p5_runtime_safety",
        "name": {"de": "P5 Runtime Safety"},
        "default_language": "de",
        "commands": list(commands),
    })
    assert book is not None
    return book


def _pair(action_a, action_b, *, capture_b="area"):
    return _book(
        _command("beacons_a", action_a),
        _command("beacons_b", action_b, capture=capture_b),
    )


class _RecordingCaller:
    """Records every service-caller call; any call at all is an executed action."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        async def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return True
        return _record


class _Resolver:
    async def resolve(self, target, *, domain_filter):
        if target.value == "Gondor":
            return ResolvedTargets((ResolvedEntity("light.gondor", "gondor"),))
        return TargetNotFound()

    async def resolve_area(self, target):
        return TargetNotFound()


_request_ids = itertools.count()


def _route(book, text=PHRASE):
    caller = _RecordingCaller()
    router = SpellbookRouter(ActionExecutor(_Resolver(), caller))
    policy = AreaPolicy(allowed_areas=frozenset({"gondor"}))
    config = AgentConfig(
        mode=FrontendMode.SPELLBOOK_ONLY,
        spellbook=book,
        policy=ScopedAreaPolicy(actions=policy, queries=policy),
    )
    request = ConversationRequest(
        request_id=f"p5-{next(_request_ids)}", agent_entry_id="e1", text=text,
        language="de", source_namespace="test",
    )
    result = asyncio.run(router.handle(request, config))
    return result, caller


OPPOSITE_PAIRS = [
    pytest.param(ON_SET_POWER, OFF_SET_POWER, id="a_set_power_true_vs_false"),
    pytest.param(ON_TURN_ON, OFF_TURN_OFF, id="b_turn_on_vs_turn_off"),
    pytest.param(ON_SET_POWER, OFF_TURN_OFF, id="c_set_power_true_vs_turn_off"),
    pytest.param(OFF_TURN_OFF, ON_SET_POWER, id="c_reversed_order"),
    pytest.param(ON_TURN_ON, OFF_SET_POWER, id="turn_on_vs_set_power_false"),
]


@pytest.mark.parametrize(("action_a", "action_b"), OPPOSITE_PAIRS)
def test_opposite_outcomes_same_target_are_runtime_ambiguous(action_a, action_b):
    book = _pair(action_a, action_b)
    assert isinstance(find_command(book, PHRASE, "de"), CommandAmbiguity)
    assert isinstance(match(book, PHRASE, "de"), EngineAmbiguous)


@pytest.mark.parametrize(("action_a", "action_b"), OPPOSITE_PAIRS)
def test_opposite_outcomes_same_target_execute_no_action(action_a, action_b):
    result, caller = _route(_pair(action_a, action_b))
    assert result.status is ConversationStatus.CONFIRMATION_REQUIRED
    assert result.execution_results == ()
    assert caller.calls == []


@pytest.mark.parametrize(("action_a", "action_b"), OPPOSITE_PAIRS)
def test_runtime_and_analyzer_agree_on_provable_safety_case(action_a, action_b):
    book = _pair(action_a, action_b)
    kinds = [finding.kind for finding in analyze_collisions(book, "de")]
    assert kinds == ["safety_ambiguity"]
    assert isinstance(find_command(book, PHRASE, "de"), CommandAmbiguity)


@pytest.mark.parametrize(("action_a", "action_b"), [
    pytest.param(ON_SET_POWER, ON_TURN_ON, id="d_same_outcome_on"),
    pytest.param(OFF_SET_POWER, OFF_TURN_OFF, id="d_same_outcome_off"),
    pytest.param(ON_TURN_ON, ON_TURN_ON, id="d_identical_action"),
])
def test_same_outcome_is_not_a_safety_ambiguity(action_a, action_b):
    book = _pair(action_a, action_b)
    found = find_command(book, PHRASE, "de")
    assert isinstance(found, CommandMatch)
    assert isinstance(match(book, PHRASE, "de"), EngineMatch)
    assert all(f.kind != "safety_ambiguity" for f in analyze_collisions(book, "de"))


def test_same_outcome_positive_control_executes_exactly_one_action():
    result, caller = _route(_pair(ON_SET_POWER, ON_TURN_ON))
    assert result.status is ConversationStatus.COMPLETED
    assert len(caller.calls) == 1


def test_different_targets_are_not_a_safety_ambiguity():
    book = _book(
        {"id": "gondor_on", "trigger": {"de": "leuchtfeuer gondor"}, "operation": "light.turn_on",
         "target": {"kind": "area_name", "value": "Gondor"}},
        {"id": "rohan_off", "trigger": {"de": "leuchtfeuer gondor"}, "operation": "light.turn_off",
         "target": {"kind": "area_name", "value": "Rohan"}},
    )
    assert all(f.kind != "safety_ambiguity" for f in analyze_collisions(book, "de"))
    assert isinstance(find_command(book, "leuchtfeuer gondor", "de"), CommandMatch)
    assert not _drafts_are_opposite(
        ActionDraft("light.turn_off", ActionTarget("area_name", "Gondor"), {}),
        ActionDraft("light.turn_on", ActionTarget("area_name", "Rohan"), {}),
    )


def test_unknown_target_relation_is_never_treated_as_same():
    area = StaticActionTarget(kind="area_name", capture="area")
    room = StaticActionTarget(kind="area_name", capture="room")
    literal = StaticActionTarget(kind="area_name", value="Gondor")
    assert target_relation(area, room) is TargetRelation.UNKNOWN
    assert target_relation(area, literal) is TargetRelation.UNKNOWN
    assert target_relation(area, ActionTarget("area_name", "Gondor")) is TargetRelation.UNKNOWN
    assert target_relation(
        ActionTarget("area_name", None), ActionTarget("area_name", "Gondor"),
    ) is TargetRelation.UNKNOWN
    for a, b in [(area, room), (area, literal)]:
        assert not same_target_opposite_outcomes(a, "light.turn_on", {}, b, "light.turn_off", {})
    # The static analyzer must not raise a safety error for {area} vs {room}.
    book = _pair(ON_TURN_ON, OFF_TURN_OFF, capture_b="room")
    assert [f.kind for f in analyze_collisions(book, "de")] == ["order_dependent"]


def test_shared_proof_requires_known_canonical_outcomes():
    target = ActionTarget("area_name", "Gondor")
    assert canonical_action_outcome("light.toggle", {}) is None
    assert canonical_action_outcome("light.turn_on", {}) is ActionOutcome.ON
    assert canonical_action_outcome("light.turn_off", {}) is ActionOutcome.OFF
    assert not same_target_opposite_outcomes(target, "light.toggle", {}, target, "light.turn_off", {})


def test_semantics_v1_boolean_fallback_is_preserved():
    target = ActionTarget("area_name", "Gondor")
    assert _drafts_are_opposite(
        ActionDraft("custom.operation", target, {"enabled": True}),
        ActionDraft("custom.operation", target, {"enabled": False}),
    )
    assert not _drafts_are_opposite(
        ActionDraft("custom.operation", target, {"enabled": True}),
        ActionDraft("custom.operation", ActionTarget("area_name", "Rohan"), {"enabled": False}),
    )
