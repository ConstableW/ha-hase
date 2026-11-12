"""P6 MatchExplain contract.

Explain observes the ONE production matcher (_find_command_impl with
collect_trace=True) and must never influence it.  These tests prove decision
equivalence with the runtime, trace-independence of scores/tiering, the
stable reason vocabulary, and the Matcher/Engine layer separation.
"""
from __future__ import annotations

import functools
import inspect
import re
from pathlib import Path

import pytest
import yaml

from custom_components.hase.core import match_explain as match_explain_module
from custom_components.hase.core.collision_analyzer import analyze_collisions
from custom_components.hase.core.match_explain import (
    CANDIDATE_REASONS,
    DECISION_REASONS,
    DECISIONS,
    DISPOSITIONS,
    ENGINE_REASONS,
    ENGINE_STAGES,
    OPPOSITE_BASES,
    explain_match,
)
from custom_components.hase.core.spellbook_engine import (
    CommandAmbiguity,
    CommandMatch,
    _find_command_impl,
    find_command,
    load_spellbook_from_dict,
    load_spellbook_from_path,
    load_spellbook_from_text,
)

SPELLBOOKS = Path(__file__).resolve().parents[1] / "custom_components" / "hase" / "spellbooks"
CANONICAL = sorted(SPELLBOOKS.glob("*.yaml"))


def _book(*commands, default_language="de"):
    book = load_spellbook_from_dict("explain_p6", {
        "id": "explain_p6", "name": {"de": "Explain"}, "default_language": default_language,
        "commands": list(commands),
    })
    assert book is not None
    return book


def _action(spell_id, trigger, operation, target, arguments=None, **extra):
    raw = {"id": spell_id, "trigger": {"de": trigger}, "operation": operation, "target": target, **extra}
    if arguments is not None:
        raw["arguments"] = arguments
    return raw


AREA = {"kind": "area_name", "capture": "area"}


def _phrases(book, lang):
    """Concrete utterances derived from every trigger/alias, plus noise."""
    fills = ["Wohnzimmer"]
    phrases = set()
    for command in book.commands:
        triggers = []
        for value in (command.trigger, command.aliases):
            if isinstance(value, dict):
                for v in value.values():
                    triggers.extend(v if isinstance(v, list) else [v])
            elif isinstance(value, (list, tuple)):
                triggers.extend(value)
            elif isinstance(value, str):
                triggers.append(value)
        for trigger in triggers:
            for fill in fills:
                phrase = re.sub(r"\{\w+\}", fill, trigger)
                phrases.update({phrase, phrase + " bitte", "bitte " + phrase, phrase.rsplit(" ", 1)[0]})
    phrases.update({"", "xyz", "xyz abc küche"})
    return sorted(phrases)


@functools.cache
def _corpus():
    return tuple(_iter_corpus())


def _iter_corpus():
    for path in CANONICAL:
        # The bundled demo ships with disabled: true; the corpus needs its commands active.
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw.pop("disabled", None)
        book = load_spellbook_from_dict(path.stem, raw)
        if book is None:
            continue
        for lang in ("de", "en"):
            for phrase in _phrases(book, lang):
                yield path.stem, book, lang, phrase


def _same_result(a, b):
    if a is None or b is None:
        return a is b
    if type(a) is not type(b):
        return False
    return a == b


# 1-5: Explain == Runtime, and trace collection never changes the decision


def test_explain_decision_equals_runtime_on_canonical_corpus():
    checked = {"MATCH": 0, "AMBIGUOUS": 0, "NO_MATCH": 0}
    for _name, book, lang, phrase in _corpus():
        runtime = find_command(book, phrase, lang)
        explanation, explained = explain_match(book, phrase, lang)
        assert _same_result(runtime, explanation.result), (phrase, lang)
        checked[explained.decision] += 1
        if isinstance(runtime, CommandMatch):
            assert explained.decision == "MATCH"
            assert explained.selected.spell_id == runtime.command.spell_id
            assert explained.selected.score == runtime.score
            assert explained.selected.is_exact == runtime.is_exact
        elif isinstance(runtime, CommandAmbiguity):
            assert explained.decision == "AMBIGUOUS"
            assert explained.ambiguity["spell_ids"] == [
                runtime.option_a.command.spell_id, runtime.option_b.command.spell_id]
        else:
            assert explained.decision == "NO_MATCH"
    assert checked["MATCH"] > 20 and checked["NO_MATCH"] > 20


@pytest.mark.parametrize("typed_only", [False, True])
def test_collect_trace_does_not_change_result_scores_or_tiering(typed_only):
    for _name, book, lang, phrase in _corpus():
        plain, no_trace = _find_command_impl(book, phrase, lang, typed_only=typed_only)
        traced, trace = _find_command_impl(book, phrase, lang, typed_only=typed_only, collect_trace=True)
        assert no_trace is None and trace is not None
        assert _same_result(plain, traced)
        if isinstance(plain, CommandMatch):
            assert (plain.score, plain.is_exact, plain.captures) == (traced.score, traced.is_exact, traced.captures)
            chosen = [c for c in trace.candidates if c.disposition == "selected"]
            assert len(chosen) == 1 and chosen[0].score == plain.score
            assert chosen[0].specificity == plain.command.sort_key


def test_explain_ambiguous_equals_runtime_ambiguity():
    book = _book(
        _action("beacons_on", "entzünde die leuchtfeuer von {area}", "light.set_power", AREA, {"on": True}),
        _action("beacons_off", "entzünde die leuchtfeuer von {area}", "light.turn_off", AREA),
    )
    phrase = "entzünde die leuchtfeuer von gondor"
    assert isinstance(find_command(book, phrase, "de"), CommandAmbiguity)
    _, e = explain_match(book, phrase, "de")
    assert (e.decision, e.reason) == ("AMBIGUOUS", "safety_ambiguity")
    assert e.selected is None
    assert e.ambiguity["basis"] == "action_outcomes"
    assert {v["outcome"] for v in e.ambiguity["variants"]} == {"on", "off"}
    ambiguous = [c for c in e.candidates if c.disposition == "ambiguous"]
    assert [c.spell_id for c in ambiguous] == ["beacons_on", "beacons_off"]
    assert all(c.reason == "safety_ambiguity" and c.captures == {"area": "gondor"} for c in ambiguous)
    assert {c.actions[0]["outcome"] for c in ambiguous} == {"on", "off"}


def test_explain_no_match_equals_runtime_no_match_with_rejection_reason():
    book = _book({"id": "lumos", "trigger": {"de": "lumos maxima"}, "output": {"de": "ok"}})
    assert find_command(book, "lumos maxima küche", "de") is None
    _, e = explain_match(book, "lumos maxima küche", "de")
    assert (e.decision, e.reason) == ("NO_MATCH", "no_full_match")
    [candidate] = e.candidates
    assert (candidate.disposition, candidate.reason) == ("rejected", "input_not_fully_consumed")
    assert candidate.engine is None


# 6-7: Safety explanation; order_dependent is not a safety ambiguity


def test_safety_ambiguity_explains_user_fallback_variant_pair():
    book = _book(
        _action("a_on", "teste leuchtfeuer {area}", "light.set_power", AREA, {"on": True},
                expert={"operation": "light.turn_on", "target": {"kind": "entity_ids", "value": ["light.gondor"]}}),
        _action("b_off", "teste leuchtfeuer {area}", "light.turn_off", AREA),
    )
    _, e = explain_match(book, "teste leuchtfeuer Gondor", "de")
    assert e.decision == "AMBIGUOUS" and e.ambiguity["basis"] == "action_outcomes"
    roles = [(v["role"], v["operation"], v["outcome"]) for v in e.ambiguity["variants"]]
    assert roles == [("user", "light.set_power", "on"), ("user", "light.turn_off", "off")]
    a = next(c for c in e.candidates if c.spell_id == "a_on")
    assert [x["role"] for x in a.actions] == ["expert", "user"]


def test_order_dependent_is_explained_as_stable_order_not_safety():
    book = _book(
        _action("first", "teste leuchtfeuer {area}", "light.turn_on", AREA),
        _action("second", "teste leuchtfeuer {area}", "light.set_power", AREA, {"on": True}),
    )
    assert [f.kind for f in analyze_collisions(book, "de")] == ["order_dependent"]
    _, e = explain_match(book, "teste leuchtfeuer Gondor", "de")
    assert (e.decision, e.reason) == ("MATCH", "stable_order")
    assert e.ambiguity is None and e.selected.spell_id == "first"
    second = next(c for c in e.candidates if c.spell_id == "second")
    assert (second.disposition, second.reason) == ("outranked", "stable_order")


# 8: rejected-candidate reasons only where the matcher layer knows them


def test_matcher_rejection_reasons():
    book = _book(
        {"id": "specific", "trigger": {"de": "licht an in {area}"}, "output": {"de": "a"}},
        {"id": "generic", "trigger": {"de": "licht {area}"}, "output": {"de": "b"}},
        {"id": "needs_area", "trigger": {"de": "lampe {area}"}, "output": {"de": "c"}},
        {"id": "off", "trigger": {"de": "aus"}, "output": {"de": "d"}, "enabled": False},
    )
    _, e = explain_match(book, "licht an in Küche", "de")
    by_id = {c.spell_id: c for c in e.candidates}
    assert (e.decision, e.reason) == ("MATCH", "single_candidate")
    assert (by_id["generic"].disposition, by_id["generic"].reason) == ("not_evaluated", "lower_specificity_tier")
    assert (by_id["off"].disposition, by_id["off"].reason) == ("excluded", "spell_disabled")
    _, e = explain_match(book, "lampe", "de")
    assert {c.spell_id: c.reason for c in e.candidates}["needs_area"] == "required_capture_missing"
    _, e = explain_match(book, "bitte licht Küche", "de")
    assert {c.spell_id: c.reason for c in e.candidates}["generic"] == "input_not_fully_consumed"


def test_third_candidate_within_epsilon_is_not_in_top_two():
    book = _book(
        _action("on", "teste leuchtfeuer {area}", "light.turn_on", AREA),
        _action("off", "teste leuchtfeuer {area}", "light.turn_off", AREA),
        _action("on2", "teste leuchtfeuer {area}", "light.set_power", AREA, {"on": True}),
    )
    _, e = explain_match(book, "teste leuchtfeuer Gondor", "de")
    assert e.decision == "AMBIGUOUS"
    third = next(c for c in e.candidates if c.spell_id == "on2")
    assert (third.disposition, third.reason) == ("outranked", "not_in_top_two")


FALLBACK_YAML = """
name: FallbackSourceAreaCapture
commands:
  - id: beacons
    trigger: "entzündet die leuchtfeuer von {area}"
    triggers:
      - "entzündet die leuchtfeuer {area}"
    operation: light.set_power
    target:
      kind: area_name
      capture: area
      fallback_source_area: true
    arguments:
      "on": true
"""


def test_fallback_source_area_is_explained_and_real_capture_wins():
    book = load_spellbook_from_text("fallback", FALLBACK_YAML)
    _, e = explain_match(book, "entzündet die leuchtfeuer von", "de")
    assert e.decision == "MATCH" and not e.via_source_area_fallback
    assert e.selected.captures == {"area": "von"}
    superseded = [c for c in e.candidates if c.reason == "superseded_by_real_capture"]
    assert len(superseded) == 1 and superseded[0].captures == {}
    _, e = explain_match(book, "entzündet die leuchtfeuer", "de")
    assert e.decision == "MATCH" and e.via_source_area_fallback
    assert e.selected.actions[0]["target"]["kind"] == "source_area"


# 9: Engine reasons never appear as matcher reasons


def test_engine_reasons_are_reported_separately_from_matcher_reasons():
    book = _book(_action(
        "power", "schalte {area} auf {state}", "light.set_power", AREA,
        {"on": {"capture": "state", "true_values": ["an"], "false_values": ["aus"]}},
    ))
    assert find_command(book, "schalte Küche auf vielleicht", "de") is None
    _, e = explain_match(book, "schalte Küche auf vielleicht", "de")
    assert (e.decision, e.reason) == ("NO_MATCH", "no_resolvable_candidate")
    [candidate] = e.candidates
    assert (candidate.disposition, candidate.reason) == ("rejected", "candidate_unresolvable")
    assert candidate.engine["layer"] == "engine"
    assert (candidate.engine["stage"], candidate.engine["reason"]) == ("action_draft", "argument_invalid")


def test_reason_vocabulary_is_closed_and_layers_do_not_mix():
    assert not (CANDIDATE_REASONS & ENGINE_REASONS)
    assert not (DECISION_REASONS & ENGINE_REASONS)
    for _name, book, lang, phrase in _corpus():
        _, e = explain_match(book, phrase, lang)
        assert e.decision in DECISIONS and e.reason in DECISION_REASONS
        if e.ambiguity:
            assert e.ambiguity["basis"] in OPPOSITE_BASES
        for c in e.candidates:
            assert c.disposition in DISPOSITIONS, c
            assert c.reason in CANDIDATE_REASONS, c
            if c.engine is not None:
                assert c.reason == "candidate_unresolvable"
                assert c.engine["stage"] in ENGINE_STAGES and c.engine["reason"] in ENGINE_REASONS


def test_explain_module_contains_no_matching_rules():
    source = inspect.getsource(match_explain_module)
    for forbidden in (r"\bmatch_segments\b", r"\b_opposite(_proof)?\(", r"\boutputs_are_opposite\b",
                      r"\bsafety_opposite_variants\b", r"\b_AMBIGUITY_SCORE_EPSILON\b",
                      r"\bliteral_word_count\b", r"\bfuzzy_threshold\b", r"\.sort\(", r"\bsorted\("):
        assert not re.search(forbidden, source), forbidden


# Combined Spells (P7): explained by the same matcher; step problems stay Engine diagnostics


def _combined(spell_id, operation, **extra):
    return {"id": spell_id, "trigger": {"de": "abend {area} auf {pct}"},
            "steps": [{"alias": "a", "operation": operation, "target": AREA,
                       "arguments": {"brightness_pct": {"capture": "pct", "type": "int", "min": 0, "max": 100}}}],
            "output": {"de": "{a.success_count}"}, **extra}


def test_combined_spell_match_and_engine_step_diagnostics():
    book = _book(_combined("evening", "light.turn_on"))
    runtime = find_command(book, "abend Küche auf 40", "de")
    _, e = explain_match(book, "abend Küche auf 40", "de")
    assert isinstance(runtime, CommandMatch) and runtime.step_plan is not None
    assert (e.decision, e.selected.spell_id, e.selected.actions) == ("MATCH", "evening", [])
    assert find_command(book, "abend Küche auf 150", "de") is None
    _, e = explain_match(book, "abend Küche auf 150", "de")
    assert (e.decision, e.reason) == ("NO_MATCH", "no_resolvable_candidate")
    [candidate] = e.candidates
    assert (candidate.disposition, candidate.reason) == ("rejected", "candidate_unresolvable")
    assert (candidate.engine["stage"], candidate.engine["reason"]) == ("step_plan", "argument_invalid")
    assert candidate.engine["steps"] == [{"step": 0, "alias": "a", "kind": "action", "reason": "argument_invalid"}]


def test_combined_spells_never_form_a_safety_ambiguity():
    # Semantics v1: queries/Combined Spells do not take part in the
    # opposite-outcome rule, even with opposite actions inside their steps.
    book = _book(_combined("on", "light.turn_on"), _combined("off", "light.turn_off"))
    assert [f.kind for f in analyze_collisions(book, "de")] == ["order_dependent"]
    assert find_command(book, "abend Küche auf 40", "de").command.spell_id == "on"
    _, e = explain_match(book, "abend Küche auf 40", "de")
    assert (e.decision, e.reason, e.selected.spell_id) == ("MATCH", "stable_order", "on")
