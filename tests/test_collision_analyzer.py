from custom_components.hase.core.collision_analyzer import analyze_collisions
from custom_components.hase.core.spellbook_engine import load_spellbook_from_dict


def _book(power_a: bool, power_b: bool, capture_b: str = "room"):
    raw = {
        "id": "collision_p41",
        "name": {"de": "Collision P4.1"},
        "default_language": "de",
        "commands": [
            {
                "id": "beacons_on",
                "trigger": {"de": "teste leuchtfeuer {area}"},
                "operation": "light.set_power",
                "target": {"kind": "area_name", "capture": "area"},
                "arguments": {"on": power_a},
            },
            {
                "id": "beacons_off",
                "trigger": {"de": f"teste leuchtfeuer {{{capture_b}}}"},
                "operation": "light.set_power",
                "target": {"kind": "area_name", "capture": capture_b},
                "arguments": {"on": power_b},
            },
        ],
    }
    return load_spellbook_from_dict("collision_p41", raw)


def test_different_capture_names_are_not_static_safety_proof():
    findings = analyze_collisions(_book(True, False), "de")
    assert len(findings) == 1
    assert findings[0].kind == "order_dependent"
    assert findings[0].spell_ids == ("beacons_on", "beacons_off")


def test_same_capture_proves_static_safety_ambiguity():
    findings = analyze_collisions(_book(True, False, capture_b="area"), "de")
    assert len(findings) == 1
    assert findings[0].kind == "safety_ambiguity"
    assert findings[0].spell_ids == ("beacons_on", "beacons_off")


def test_same_effect_remains_order_dependent():
    findings = analyze_collisions(_book(True, True), "de")
    assert len(findings) == 1
    assert findings[0].kind == "order_dependent"


def _validated_book(color_vocabulary: bool):
    color = {"capture": "color", "value_map": {"rot": "red"}} if color_vocabulary else {"capture": "color"}
    raw = {
        "id": "validated_overlap", "name": {"de": "V"}, "default_language": "de",
        "commands": [
            {"id": "general", "trigger": {"de": "leuchte {area}"}, "operation": "light.turn_on",
             "target": {"kind": "area_name", "capture": "area"}, "arguments": {}},
            {"id": "specific", "trigger": {"de": "leuchte {area} {color}"}, "operation": "light.set_color",
             "target": {"kind": "area_name", "capture": "area"}, "arguments": {"color_name": color}},
        ],
    }
    return load_spellbook_from_dict("validated_overlap", raw)


def test_validated_extension_is_an_informational_resolved_overlap():
    findings = analyze_collisions(_validated_book(True), "de")
    assert [(f.kind, f.severity, f.reason) for f in findings] == [
        ("resolved_overlap", "info", "validated_placeholders_select_specific_trigger"),
    ]
    assert set(findings[0].spell_ids) == {"general", "specific"}


def test_unvalidated_extra_placeholder_is_not_reported_as_resolved():
    assert [f.reason for f in analyze_collisions(_validated_book(False), "de")] == []
