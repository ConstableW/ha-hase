"""P6.2/P6.3 Studio backend: Explain operation and conflict badge data.

Explain runs the production matcher on the complete book (with an unsaved
editor draft substituted) and never writes, reports or executes.  Conflict
badges are derived from analyzer findings and analyzer severities only.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pytest
from pathlib import Path

from custom_components.hase import studio
from custom_components.hase.core.spellbook_engine import CommandAmbiguity, find_command, load_spellbook_from_dict
from custom_components.hase.core.executor import ResolvedEntity, ResolvedTargets
from custom_components.hase.core.models import AreaPolicy, ScopedAreaPolicy


def _book():
    area = {"kind": "area_name", "capture": "area"}
    trigger = {"de": "entzündet die leuchtfeuer von {area}"}
    return {
        "id": "demo", "name": "Demo", "default_language": "de", "languages": ["de"],
        "commands": [
            {"id": "beacons_on", "name": {"de": "Leuchtfeuer einschalten"}, "trigger": trigger,
             "operation": "light.set_power", "target": area, "arguments": {"on": True}, "enabled": True},
            {"id": "beacons_alt", "name": {"de": "Leuchtfeuer Alternative"}, "trigger": trigger,
             "operation": "light.turn_on", "target": area, "enabled": True},
            {"id": "beacons_off", "name": {"de": "Leuchtfeuer ausschalten"}, "trigger": trigger,
             "operation": "light.turn_off", "target": area, "enabled": True},
        ],
    }


def _instance(raw):
    instance = studio.SpellbookStudio.__new__(studio.SpellbookStudio)
    instance.read = AsyncMock(return_value=(deepcopy(raw), "rev-1"))
    instance.report = AsyncMock(return_value={"errors": 0})
    instance.write = AsyncMock(return_value="rev-2")
    return instance


def _dispatch(instance, operation, data):
    return asyncio.run(instance.dispatch(operation, {"book_id": "demo", "revision": "rev-1", **data}, None))


def test_explain_operation_is_registered_in_websocket_schema():
    source = (Path(studio.__file__).parent / "studio_api.py").read_text(encoding="utf-8")
    schema = source[source.index('vol.Required("operation")'):source.index('vol.Optional("data"')]
    assert '"explain"' in schema


def test_explain_operation_explains_complete_book_without_side_effects():
    raw = _book()
    raw["commands"] = [raw["commands"][0], raw["commands"][2]]  # on, off
    instance = _instance(raw)
    result = _dispatch(instance, "explain", {"phrase": "entzündet die leuchtfeuer von gondor", "language": "de"})
    explained = result["match_explain"]
    runtime = find_command(load_spellbook_from_dict("demo", raw), "entzündet die leuchtfeuer von gondor", "de")
    assert isinstance(runtime, CommandAmbiguity)
    assert explained["decision"] == "AMBIGUOUS" and explained["reason"] == "safety_ambiguity"
    assert explained["ambiguity"]["spell_ids"] == [runtime.option_a.command.spell_id, runtime.option_b.command.spell_id]
    instance.write.assert_not_awaited()
    instance.report.assert_not_awaited()


def test_explain_operation_uses_unsaved_draft_in_book_context():
    raw = _book()
    raw["commands"] = raw["commands"][:1]  # only beacons_on saved
    draft = deepcopy(_book()["commands"][2])  # unsaved beacons_off
    instance = _instance(raw)
    result = _dispatch(instance, "explain", {
        "phrase": "entzündet die leuchtfeuer von gondor", "language": "de", "mode": "expert", "spell": draft,
    })
    assert result["match_explain"]["decision"] == "AMBIGUOUS"
    instance.write.assert_not_awaited()


@pytest.mark.parametrize("phrase", ["", "lumos {area}", 7])
def test_explain_operation_rejects_placeholder_phrases(phrase):
    with pytest.raises(studio.StudioError):
        _dispatch(_instance(_book()), "explain", {"phrase": phrase})


def test_dry_run_response_carries_match_explain_with_same_decision():
    raw = _book()
    instance = _instance(raw)
    result = asyncio.run(instance.test(raw, {"phrase": "lumos maxima", "language": "de"}))
    assert result["status"] == "NO_MATCH"
    assert result["match_explain"]["decision"] == "NO_MATCH"
    assert result["match_explain"]["reason"] == "no_full_match"
    assert result["matching_trace"]["decision"] == "no_match"


def test_draft_dry_run_matches_against_complete_book():
    raw = _book()
    raw["commands"] = raw["commands"][:1]
    draft = deepcopy(_book()["commands"][2])
    instance = _instance(raw)
    result = _dispatch(instance, "dry_run", {
        "phrase": "entzündet die leuchtfeuer von gondor", "language": "de",
        "mode": "expert", "spell": draft,
    })
    assert result["status"] == "AMBIGUOUS"
    assert result["match_explain"]["decision"] == "AMBIGUOUS"
    instance.write.assert_not_awaited()


def test_disabled_book_rejects_studio_live_execution():
    raw = _book()
    raw["disabled"] = True
    instance = _instance(raw)
    with pytest.raises(studio.StudioError, match="deaktiviert"):
        _dispatch(instance, "execute", {"phrase": "test", "confirmed": True})
    instance.write.assert_not_awaited()


def test_flat_query_dry_run_uses_production_query_executor_without_service_call():
    raw = {"id": "demo", "name": "Demo", "commands": [{
        "id": "lights", "trigger": "wie viele lichter an",
        "sources": [{"kind": "count", "target": {"kind": "all_allowed"},
                     "domain_filter": "light", "state_filter": "on", "variable": "lights_on"}],
        "output": "{lights_on} Lichter",
    }]}
    instance = _instance(raw)
    instance.hass = object()
    area = AreaPolicy(None, unrestricted=True)
    instance.studio_policy = lambda _profile: ScopedAreaPolicy(area, area)
    resolver = SimpleNamespace(resolve=AsyncMock(return_value=ResolvedTargets(
        (ResolvedEntity("light.a", "living"), ResolvedEntity("light.b", "living")))))
    caller = SimpleNamespace(get_entity_state=AsyncMock(side_effect=[
        {"state": "on"}, {"state": "off"},
    ]), set_light_power=AsyncMock())
    with patch.object(studio, "HaTargetResolver", return_value=resolver), \
         patch.object(studio, "HaServiceCaller", return_value=caller):
        result = asyncio.run(instance.test(raw, {"phrase": "wie viele lichter an"}))
    assert result["status"] == "QUERY_ANSWER"
    assert result["data"]["response_text"] == "1 Lichter"
    assert result["data"]["sources"][0]["matched"] == 1
    assert result["query_plan"]["sources"][0]["target"]["kind"] == "all_allowed"
    assert result["preview_only"] is True
    caller.set_light_power.assert_not_awaited()


def test_conflict_badge_data_comes_from_analyzer_severity():
    raw = _book()
    raw["commands"][1]["trigger"] = {"de": "entzündet die leuchtfeuer von {area} bitte"}
    raw["commands"].append({"id": "beacons_alt2", "trigger": {"de": "entzündet die leuchtfeuer von {area} bitte"},
                            "operation": "light.set_power", "target": {"kind": "area_name", "capture": "area"},
                            "arguments": {"on": True}})
    raw["commands"].append({"id": "quiet", "trigger": {"de": "ruhe"}, "output": {"de": "ok"}})
    report = studio._collision_report(raw, "de")
    by_kind = {f["kind"]: f for f in report["findings"]}
    assert by_kind["safety_ambiguity"]["severity"] == "error"
    assert by_kind["order_dependent"]["severity"] == "warning"
    spells = report["spells"]
    assert spells["beacons_on"]["status"] == "error" and spells["beacons_off"]["status"] == "error"
    assert spells["beacons_alt"]["status"] == "warning" and spells["beacons_alt"]["warnings"] == 1
    assert spells["quiet"]["status"] == "ok"
    evidence = by_kind["safety_ambiguity"]["evidence"]
    assert [e["outcome"] for e in evidence] == ["on", "off"]
    assert evidence[0]["target"] == {"kind": "area_name", "value": None, "capture": "area"}


def test_badge_errors_and_validation_errors_are_the_same_analyzer_findings():
    raw = _book()
    report = studio._collision_report(raw, "de")
    issues = studio._validation_issues(raw, "de")
    error_spells = {sid for sid, s in report["spells"].items() if s["status"] == "error"}
    assert error_spells == {issue["spell_id"] for issue in issues}


def test_disabled_spell_badge_is_not_analyzed_and_not_touched():
    raw = _book()
    raw["commands"][2]["enabled"] = False
    before = deepcopy(raw)
    report = studio._collision_report(raw, "de")
    assert report["spells"]["beacons_off"]["status"] == "not_analyzed"
    assert raw == before


def test_confirmed_safety_save_does_not_touch_enabled_of_either_spell(monkeypatch):
    raw = _book()
    existing, candidate = deepcopy(raw["commands"][2]), deepcopy(raw["commands"][0])
    raw["commands"] = [existing]
    instance = _instance(raw)
    monkeypatch.setattr(studio, "preserve_user", lambda old, new, remove_user=False: deepcopy(new))
    data = {"spell_id": None, "mode": "expert", "spell": candidate, "language": "de", "book_language": "de"}
    first = _dispatch(instance, "save_spell", data)
    assert first["requires_confirmation"] is True and first["confirmation_code"] == "safety_ambiguity"
    instance.write.assert_not_awaited()
    second = _dispatch(instance, "save_spell", {**data, "allow_safety_ambiguity": True})
    assert second["revision"] == "rev-2"
    written, _ = instance.write.await_args.args
    assert {c["id"]: c.get("enabled") for c in written["commands"]} == {"beacons_off": True, "beacons_on": True}


def test_saving_a_disabled_opposite_spell_needs_no_confirmation_and_stays_disabled(monkeypatch):
    # Disabled spells never take part in matching, so they cannot be a
    # runtime safety ambiguity; the analyzer does not flag them either.
    raw = _book()
    existing, candidate = deepcopy(raw["commands"][2]), deepcopy(raw["commands"][0])
    candidate["enabled"] = False
    raw["commands"] = [existing]
    instance = _instance(raw)
    monkeypatch.setattr(studio, "preserve_user", lambda old, new, remove_user=False: deepcopy(new))
    result = _dispatch(instance, "save_spell", {"spell_id": None, "mode": "expert", "spell": candidate,
                                                "language": "de", "book_language": "de"})
    assert "requires_confirmation" not in result
    written, _ = instance.write.await_args.args
    assert {c["id"]: c.get("enabled") for c in written["commands"]} == {"beacons_off": True, "beacons_on": False}


def test_explain_shows_frozen_top_two_rule_for_a_third_opposite_candidate():
    """Semantics v1 (frozen): only the top-2 take part in ambiguity.  With
    on / alt(on) / off at equal score, runtime selects beacons_on; Explain
    must show that truthfully, with beacons_off as not_in_top_two, while the
    pairwise analyzer still reports on<->off as an authoring error."""
    raw = _book()
    instance = _instance(raw)
    explained = _dispatch(instance, "explain", {"phrase": "entzündet die leuchtfeuer von gondor", "language": "de"})["match_explain"]
    runtime = find_command(load_spellbook_from_dict("demo", raw), "entzündet die leuchtfeuer von gondor", "de")
    assert runtime.command.spell_id == "beacons_on"
    assert (explained["decision"], explained["selected"]["spell_id"]) == ("MATCH", "beacons_on")
    by_id = {c["spell_id"]: c for c in explained["candidates"]}
    assert (by_id["beacons_alt"]["disposition"], by_id["beacons_alt"]["reason"]) == ("outranked", "stable_order")
    assert (by_id["beacons_off"]["disposition"], by_id["beacons_off"]["reason"]) == ("outranked", "not_in_top_two")
    assert studio._collision_report(raw, "de")["spells"]["beacons_off"]["status"] == "error"
