"""Safety contract over every executable action variant (user + expert).

Two top-2 candidates are safety-opposite when ANY pair of their executable
action variants proves SAME effective target + opposite canonical outcomes.
Runtime (spellbook_engine._drafts_are_opposite) and the static Collision
Analyzer share one projection and one proof (core.action_semantics), so they
must not drift apart again.  No permission simulation; UNKNOWN is never SAME.
"""
from __future__ import annotations

import asyncio
import itertools

import pytest

from custom_components.hase.core.action_semantics import (
    ActionVariant,
    StaticActionTarget,
    TargetRelation,
    command_action_variants,
    draft_action_variants,
    safety_opposite_variants,
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
)

PHRASE = "teste leuchtfeuer Gondor"
TRIGGER = "teste leuchtfeuer {area}"

AREA = {"kind": "area_name", "capture": "area"}
ROOM = {"kind": "area_name", "capture": "room"}
GONDOR = {"kind": "area_name", "value": "Gondor"}
ROHAN = {"kind": "area_name", "value": "Rohan"}
ENTITY = {"kind": "entity_ids", "value": ["light.gondor"]}

TURN_ON = ("light.turn_on", None)
TURN_OFF = ("light.turn_off", None)
SET_ON = ("light.set_power", {"on": True})
SET_OFF = ("light.set_power", {"on": False})
TOGGLE = ("light.toggle", None)


def _action(action, target):
    operation, arguments = action
    raw = {"operation": operation, "target": target}
    if arguments is not None:
        raw["arguments"] = arguments
    return raw


def _spell(spell_id, user=None, expert=None, *, trigger=TRIGGER, extra=None):
    raw = {"id": spell_id, "trigger": {"de": trigger}}
    if user is not None:
        raw.update(_action(*user))
    if expert is not None:
        raw["expert"] = _action(*expert)
    if extra:
        raw.update(extra)
    return raw


def _book(*commands):
    book = load_spellbook_from_dict("variants", {
        "id": "variants", "name": {"de": "Varianten"}, "default_language": "de",
        "commands": list(commands),
    })
    assert book is not None
    return book


def _analyzer_kinds(book):
    return [finding.kind for finding in analyze_collisions(book, "de")]


def _is_safety(book):
    return "safety_ambiguity" in _analyzer_kinds(book)


def _runtime_ambiguous(book, phrase=PHRASE):
    return isinstance(find_command(book, phrase, "de"), CommandAmbiguity)


class _RecordingCaller:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        async def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return True
        return _record


class _Resolver:
    async def resolve(self, target, *, domain_filter):
        return ResolvedTargets((ResolvedEntity("light.gondor", "gondor"),))

    async def resolve_area(self, target):
        return TargetNotFound()


_ids = itertools.count()


def _route(book, *, expert_allowed):
    caller = _RecordingCaller()
    router = SpellbookRouter(ActionExecutor(
        _Resolver(), caller, expert_allowed=lambda: expert_allowed,
    ))
    policy = AreaPolicy(allowed_areas=frozenset({"gondor"}))
    config = AgentConfig(
        mode=FrontendMode.SPELLBOOK_ONLY, spellbook=book,
        policy=ScopedAreaPolicy(actions=policy, queries=policy),
    )
    request = ConversationRequest(
        request_id=f"variants-{next(_ids)}", agent_entry_id="e1", text=PHRASE,
        language="de", source_namespace="test",
    )
    return asyncio.run(router.handle(request, config)), caller


# ---------------------------------------------------------------------------
# A-F: every variant combination that proves opposite is a safety ambiguity
# ---------------------------------------------------------------------------

OPPOSITE_CASES = {
    "A_user_user": (
        _spell("a", user=(SET_ON, AREA)),
        _spell("b", user=(TURN_OFF, AREA)),
    ),
    "B_expert_expert": (
        _spell("a", user=(TOGGLE, AREA), expert=(TURN_ON, AREA)),
        _spell("b", user=(TOGGLE, AREA), expert=(TURN_OFF, AREA)),
    ),
    "C_expert_user": (
        _spell("a", user=(TOGGLE, AREA), expert=(TURN_ON, AREA)),
        _spell("b", user=(TURN_OFF, AREA)),
    ),
    "D_user_expert": (
        _spell("a", user=(SET_ON, AREA)),
        _spell("b", user=(TOGGLE, AREA), expert=(TURN_OFF, AREA)),
    ),
    # The reproduced bug: expert drafts target different things, but A may
    # fall back to its user action, which is the exact opposite of B.
    "E_user_fallback_opposite": (
        _spell("a", user=(SET_ON, AREA), expert=(TURN_ON, ENTITY)),
        _spell("b", user=(TURN_OFF, AREA)),
    ),
    "F_users_same_experts_opposite": (
        _spell("a", user=(TURN_ON, AREA), expert=(TURN_OFF, AREA)),
        _spell("b", user=(TURN_ON, AREA), expert=(TURN_ON, AREA)),
    ),
}


@pytest.mark.parametrize("case", sorted(OPPOSITE_CASES))
def test_opposite_variant_pair_is_safety_ambiguity_in_analyzer_and_runtime(case):
    book = _book(*OPPOSITE_CASES[case])
    assert _analyzer_kinds(book) == ["safety_ambiguity"]
    assert _runtime_ambiguous(book)


@pytest.mark.parametrize("case", sorted(OPPOSITE_CASES))
def test_opposite_variant_pair_is_symmetric(case):
    a, b = OPPOSITE_CASES[case]
    book = _book(b, a)
    assert _analyzer_kinds(book) == ["safety_ambiguity"]
    assert _runtime_ambiguous(book)


def test_E_expert_drafts_alone_are_not_opposite_only_the_fallback_is():
    found = find_command(_book(*OPPOSITE_CASES["E_user_fallback_opposite"]), PHRASE, "de")
    assert isinstance(found, CommandAmbiguity)
    drafts = {found.option_a.command.spell_id: found.option_a.action_draft,
              found.option_b.command.spell_id: found.option_b.action_draft}
    expert_a = drafts["a"]
    assert expert_a.expert and expert_a.user_fallback is not None
    # Without the fallback the pair would not be provable ...
    assert safety_opposite_variants(
        draft_action_variants(ActionDraft(expert_a.operation, expert_a.target, expert_a.arguments, expert=True)),
        draft_action_variants(drafts["b"]),
    ) is None
    # ... the user_fallback variant is what proves it.
    pair = safety_opposite_variants(draft_action_variants(expert_a), draft_action_variants(drafts["b"]))
    assert pair is not None
    assert pair[0].role == "user" and pair[0].operation == "light.set_power"


# ---------------------------------------------------------------------------
# L: a detected safety ambiguity never reaches a service call
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expert_allowed", [True, False])
@pytest.mark.parametrize("case", sorted(OPPOSITE_CASES))
def test_L_runtime_safety_ambiguity_performs_no_service_call(case, expert_allowed):
    result, caller = _route(_book(*OPPOSITE_CASES[case]), expert_allowed=expert_allowed)
    assert result.status is ConversationStatus.CONFIRMATION_REQUIRED
    assert result.execution_results == ()
    assert caller.calls == []


# ---------------------------------------------------------------------------
# G-K: no false safety ambiguity
# ---------------------------------------------------------------------------

def test_G_different_targets_across_all_variants_are_not_safety():
    book = _book(
        _spell("a", user=(TURN_ON, GONDOR), expert=(TURN_ON, GONDOR)),
        _spell("b", user=(TURN_OFF, ROHAN), expert=(TURN_OFF, ROHAN)),
    )
    assert not _is_safety(book)
    assert isinstance(find_command(book, PHRASE, "de"), CommandMatch)


def test_H_unknown_target_relation_is_never_promoted_to_same():
    book = _book(
        _spell("a", user=(TOGGLE, AREA), expert=(TURN_ON, AREA)),
        _spell("b", user=(TOGGLE, ROOM), expert=(TURN_OFF, ROOM), trigger="teste leuchtfeuer {room}"),
    )
    assert not _is_safety(book)
    a_variants = command_action_variants(book.commands[0])
    b_variants = command_action_variants(book.commands[1])
    for va, vb in itertools.product(a_variants, b_variants):
        assert target_relation(va.target, vb.target) is TargetRelation.UNKNOWN
    assert safety_opposite_variants(a_variants, b_variants) is None
    literal = ActionVariant("user", "light.turn_on", StaticActionTarget("area_name", value="Gondor"), {})
    capture = ActionVariant("expert", "light.turn_off", StaticActionTarget("area_name", capture="area"), {})
    assert safety_opposite_variants([literal], [capture]) is None


@pytest.mark.parametrize(("a", "b"), [
    pytest.param(_spell("a", user=(TURN_ON, AREA), expert=(SET_ON, AREA)),
                 _spell("b", user=(SET_ON, AREA), expert=(TURN_ON, AREA)), id="all_on"),
    pytest.param(_spell("a", user=(TURN_OFF, AREA), expert=(SET_OFF, AREA)),
                 _spell("b", user=(SET_OFF, AREA)), id="all_off"),
    pytest.param(_spell("a", user=(TOGGLE, AREA), expert=(TOGGLE, AREA)),
                 _spell("b", user=(TURN_OFF, AREA)), id="unknown_outcome"),
])
def test_I_same_or_unknown_outcomes_are_not_safety(a, b):
    book = _book(a, b)
    assert not _is_safety(book)
    assert isinstance(find_command(book, PHRASE, "de"), CommandMatch)


def test_J_candidate_without_expert_keeps_single_variant_and_behavior():
    command = _book(_spell("a", user=(SET_ON, AREA))).commands[0]
    variants = command_action_variants(command)
    assert [(v.role, v.operation) for v in variants] == [("user", "light.set_power")]
    draft = ActionDraft("light.turn_on", ActionTarget("area_name", "Gondor"), {})
    assert [(v.role, v.operation) for v in draft_action_variants(draft)] == [("user", "light.turn_on")]
    assert isinstance(find_command(_book(_spell("a", user=(SET_ON, AREA)),
                                         _spell("b", user=(TURN_ON, AREA))), PHRASE, "de"), CommandMatch)


QUERY = {"sources": [{"kind": "count", "target": AREA, "domain_filter": "light",
                      "state_filter": "on", "variable": "n"}], "output": "{n} Lichter."}
COMBINED = {"steps": [{"alias": "on", "operation": "light.turn_on", "target": AREA}]}


@pytest.mark.parametrize("extra", [QUERY, COMBINED], ids=["query", "combined"])
def test_K_query_and_combined_spells_have_no_action_variants(extra):
    book = _book(_spell("a", extra=extra), _spell("b", user=(TURN_OFF, AREA)))
    assert command_action_variants(book.commands[0]) == ()
    assert not _is_safety(book)
    assert isinstance(find_command(book, PHRASE, "de"), CommandMatch)


def test_expert_only_spell_projects_one_expert_variant():
    command = _book(_spell("a", expert=(TURN_ON, AREA))).commands[0]
    assert [(v.role, v.operation) for v in command_action_variants(command)] == [("expert", "light.turn_on")]


# ---------------------------------------------------------------------------
# M: the Semantics-v1 boolean fallback stays on the primary drafts
# ---------------------------------------------------------------------------

def test_M_semantics_v1_fallback_preserved():
    target = ActionTarget("area_name", "Gondor")
    assert _drafts_are_opposite(
        ActionDraft("custom.operation", target, {"enabled": True}),
        ActionDraft("custom.operation", target, {"enabled": False}),
    )
    fallback_flip = ActionDraft("light.set_power", ActionTarget("entity_ids", ("light.x",)), {"on": True},
                                expert=True,
                                user_fallback=ActionDraft("custom.operation", target, {"enabled": True}))
    # v1 fallback is not widened to variants: only the typed proof is.
    assert not _drafts_are_opposite(fallback_flip, ActionDraft("custom.operation", target, {"enabled": False}))


# ---------------------------------------------------------------------------
# Contract: analyzer safety_ambiguity <=> runtime top-2 ambiguity
# ---------------------------------------------------------------------------

_CONTRACT_ACTIONS = (TURN_ON, TURN_OFF, SET_OFF, TOGGLE)
# AREA vs ROHAN is UNKNOWN statically and DIFFERENT at runtime ("Gondor" is
# captured) - both systems agree that nothing is provable.  AREA vs a literal
# "Gondor" is deliberately excluded: statically UNKNOWN, but the runtime sees
# the resolved value; the static proof is a conservative subset there.
# ENTITY vs ENTITY is SAME in both layers since static_target() canonicalizes
# YAML lists to the runtime tuple form.
_CONTRACT_TARGETS = (AREA, ROHAN, ENTITY)
_CONTRACT_USERS = [(a, t) for a in _CONTRACT_ACTIONS for t in _CONTRACT_TARGETS]
_CONTRACT_SPELLS = [(u, None) for u in _CONTRACT_USERS] + [
    (u, e) for u in _CONTRACT_USERS[:4] for e in _CONTRACT_USERS
]


def test_contract_analyzer_and_runtime_agree_on_provable_action_safety():
    mismatches = []
    checked = 0
    for (user_a, expert_a), (user_b, expert_b) in itertools.product(_CONTRACT_SPELLS, repeat=2):
        book = _book(_spell("a", user=user_a, expert=expert_a), _spell("b", user=user_b, expert=expert_b))
        analyzer = _is_safety(book)
        runtime = _runtime_ambiguous(book)
        checked += 1
        if analyzer != runtime:
            mismatches.append((user_a, expert_a, user_b, expert_b, analyzer, runtime))
    assert checked > 1000
    assert mismatches == []


def test_contract_entity_ids_literal_targets():
    # static_target() canonicalizes YAML lists to the runtime's tuple form.
    book = _book(_spell("a", user=(TURN_ON, ENTITY)), _spell("b", user=(TURN_OFF, ENTITY)))
    assert _runtime_ambiguous(book)
    assert _is_safety(book)


def test_contract_entity_ids_different_lists_are_not_safety():
    other = {"kind": "entity_ids", "value": ["light.rohan"]}
    book = _book(_spell("a", user=(TURN_ON, ENTITY)), _spell("b", user=(TURN_OFF, other)))
    assert not _runtime_ambiguous(book)
    assert not _is_safety(book)


def test_static_target_list_canonicalization_matches_runtime():
    from custom_components.hase.core.action_semantics import static_target
    assert static_target(ENTITY).value == ("light.gondor",)
    assert static_target({"kind": "entity_ids", "value": []}).value is None
    assert static_target({"kind": "entity_ids", "value": ["light.a", 3]}).value is None
