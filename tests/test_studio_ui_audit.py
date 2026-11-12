"""UI audit: production builders/classifiers, without HA or device calls."""
from copy import deepcopy

import pytest
import yaml
from test_studio import adapter, studio_core


def builder(data, old=None):
    return object.__new__(adapter.SpellbookStudio).wizard_spell(
        {"id": "audit", "triggers": ["audit {area}"], "trigger_language": "de", **data}, old
    )


def action(**changes):
    return {"kind": "action", "action": "light.turn_on:0", "target_kind": "all_allowed", **changes}


@pytest.mark.parametrize("count", [0, 1, 2, 12])
def test_current_ui_save_reload_remains_wizard(count):
    saved = builder({"steps": [action() for _ in range(count)], "output": "fertig"})
    reloaded = studio_core.parse_document(yaml.safe_dump(
        {"id": "audit", "name": "Audit", "commands": [saved]}))["commands"][0]
    assert studio_core.wizard_incompatibilities(reloaded) == []
    assert len(studio_core.as_logical_steps(reloaded)) == count
    assert ("steps" in reloaded) == (count > 1)


@pytest.mark.parametrize("arguments", [{"rgb_color": [17, 123, 245]}, {"color_name": "chartreuse"}])
@pytest.mark.parametrize("count", [1, 2, 12])
def test_guided_color_roundtrip(arguments, count):
    saved = builder({"steps": [action(action="light.set_color:0", color_arguments=arguments)]
                    + [action() for _ in range(count - 1)]})
    assert studio_core.as_logical_steps(saved)[0]["arguments"] == arguments
    assert studio_core.wizard_incompatibilities(saved) == []
    again = builder({"steps": [{"kind": "preserve", "index": i} for i in range(count)]}, saved)
    assert studio_core.as_logical_steps(again) == studio_core.as_logical_steps(saved)


@pytest.mark.parametrize("bad", [{"rgb_color": [256, 0, 0]}, {"color_name": ""}, {"extra": 1}])
def test_guided_color_rejects_unrepresentable_arguments(bad):
    with pytest.raises(studio_core.StudioError):
        builder({"steps": [action(action="light.set_color:0", color_arguments=bad)]})


@pytest.mark.parametrize("expert_indices", [{1}, {1, 4, 10}, set(range(12))])
def test_structured_blockers_in_twelve_steps_and_revalidation(expert_indices):
    steps = [{"operation": "light.turn_on", "target": {"kind": "all_allowed"}, "arguments": {}}
             for _ in range(12)]
    for i in expert_indices:
        steps[i]["target"] = {"kind": "template", "value": "{{ ['light.ceiling'] }}"}
    spell = {"steps": steps}
    details = studio_core.wizard_blocker_details(spell)
    assert [b["step_index"] for b in details] == sorted(expert_indices)
    assert all(b["code"] == "template_target" and b["path"] == "target.value" for b in details)
    assert studio_core.wizard_incompatibilities(spell) == [b["message"] for b in details]
    assert studio_core.wizard_step_flags(spell) == [i in expert_indices for i in range(12)]
    fixed = min(expert_indices)
    steps[fixed]["target"] = {"kind": "all_allowed"}
    assert [b["step_index"] for b in studio_core.wizard_blocker_details(spell)] == sorted(expert_indices - {fixed})


def test_multiple_fields_same_step_and_spell_level_blocker():
    raw = {"expert": {}, "steps": [{"operation": "light.set_power", "arguments": {"on": False},
                                   "target": {"kind": "entity_name", "capture": "entity"}}]}
    assert [(b["step_index"], b["path"]) for b in studio_core.wizard_blocker_details(raw)] == [
        (None, "expert"), (0, "target.kind"), (0, "arguments")]


def test_legacy_action_is_still_expert():
    assert studio_core.wizard_incompatibilities({"operation": "light.set_power", "arguments": {"on": False},
                                                "target": {"kind": "all_allowed"}})


def test_output_only_checks_current_response_not_previous_translation():
    saved = builder({"steps": [], "output": "Guten Morgen"})
    assert saved["output"] == {"de": "Guten Morgen"}
    with pytest.raises(studio_core.StudioError):
        builder({"steps": [], "output": "", "catchphrase": ""}, saved)


def test_expert_variant_edit_keep_and_explicit_remove_preserves_fallback():
    variant = {"operation": "light.turn_on", "target": {"kind": "template", "value": "{{ ['light.a'] }}"},
               "arguments": {}, "catchphrase": {"de": "An", "en": "On"}}
    old = builder({"steps": [action()], "expert_variant": variant})
    old["metadata"] = {"untouched": {"nested": [1, 2]}}
    original = deepcopy(old)
    assert builder({"steps": [action()]}, old)["expert"] == variant
    edited = builder({"steps": [action()], "expert_variant": {**variant, "arguments": {"brightness_pct": 42}}}, old)
    assert edited["expert"]["arguments"] == {"brightness_pct": 42}
    assert edited["operation"] == old["operation"]
    removed = builder({"steps": [action()], "expert_variant": None}, edited)
    assert "expert" not in removed
    assert removed["metadata"] == old["metadata"]
    assert studio_core.wizard_blocker_details(removed) == []
    assert old == original


def test_query_capture_roundtrip_remains_guided_without_changing_classifier():
    source = {"kind": "count", "expert": True, "target_kind": "area_name", "capture": "area", "variable": "n"}
    saved = builder({"steps": [{"kind": "query", "sources": [source]}]})
    assert saved["sources"][0]["target"] == {"kind": "area_name", "capture": "area"}
    assert studio_core.wizard_step_flags(saved) == [False]
    studio_core.check_document({"id": "audit", "name": "Audit", "commands": [saved]})


def test_extra_target_and_source_options_roundtrip():
    saved = builder({"steps": [action(target_kind="area_name", target_capture="room",
                                     target_extras={"fallback_source_area": True, "extension": {"nested": 1}})],
                     "triggers": ["audit {room}"]})
    assert saved["target"] == {"kind": "area_name", "capture": "room", "fallback_source_area": True,
                               "extension": {"nested": 1}}
    source = {"kind": "count", "expert": True, "target_kind": "area_name", "capture": "area", "variable": "n",
              "target_extras": {"fallback_source_area": True}, "source_extras": {"extension": [1, 2]}}
    saved = builder({"steps": [{"kind": "query", "sources": [source]}]})
    assert saved["sources"][0]["extension"] == [1, 2]
    assert saved["sources"][0]["target"]["fallback_source_area"] is True


def test_expert_only_variant_with_no_user_action_remains_editable():
    old = {"id": "audit", "trigger": "audit", "expert": {
        "operation": "light.turn_on", "target": {"kind": "template", "value": "{{ ['light.x'] }}"}, "arguments": {}}}
    saved = builder({"steps": [], "expert_variant": old["expert"]}, old)
    assert saved["expert"] == old["expert"]
    assert "operation" not in saved
    studio_core.check_document({"id": "audit", "name": "Audit", "commands": [saved]})


def test_unknown_operation_blocker_targets_the_operation_control():
    blockers = studio_core.wizard_blocker_details({"operation": "custom.unknown", "target": {"kind": "all_allowed"}})
    assert blockers[0]["path"] == "operation"


@pytest.mark.parametrize("kind", ["area_name", "entity_name"])
def test_expert_fixed_name_target_is_not_rewritten_as_capture(kind):
    saved = builder({"steps": [{"kind": "action", "expert": True, "operation": "light.turn_on",
                               "arguments": {}, "target": {"kind": kind, "value": "Wohnzimmer"}}]})
    assert saved["target"] == {"kind": kind, "value": "Wohnzimmer"}


def test_query_fixed_area_name_is_preserved():
    source = {"kind": "count", "expert": True, "target_kind": "area_name", "target_value": "Wohnzimmer", "variable": "n"}
    saved = builder({"steps": [{"kind": "query", "sources": [source]}]})
    assert saved["sources"][0]["target"] == {"kind": "area_name", "value": "Wohnzimmer"}
