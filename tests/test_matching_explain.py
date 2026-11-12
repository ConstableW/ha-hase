
from custom_components.hase.core.spellbook_engine import (
    CommandAmbiguity, CommandMatch, explain, find_command, load_spellbook_from_dict,
)

def _book(*commands):
    book = load_spellbook_from_dict(
        "explain_v1", {"id": "explain_v1", "name": "Explain", "commands": list(commands)}
    )
    assert book is not None
    return book

def _text(spell_id, trigger, output):
    return {"id": spell_id, "trigger": trigger, "output": output}

def _power(spell_id, trigger, on):
    return {
        "id": spell_id, "trigger": trigger, "operation": "light.set_power",
        "target": {"kind": "area_name", "value": "Test Area"},
        "arguments": {"on": on},
    }

def test_explain_same_match_decision_as_find_command():
    book = _book(_text("specific", "turn light {area}", "ok"), _text("generic", "turn {area}", "ok"))
    normal = find_command(book, "turn light kitchen")
    diagnostic = explain(book, "turn light kitchen")
    assert isinstance(normal, CommandMatch)
    assert diagnostic.result == normal
    assert diagnostic.trace.decision == "match"
    assert diagnostic.trace.selected_spell_id == "specific"
    assert any(c.reason == "lower_tier_not_considered" for c in diagnostic.trace.candidates)

def test_explain_same_ambiguity_as_find_command():
    book = _book(_power("on", "switch light", True), _power("off", "switch light", False))
    normal = find_command(book, "switch light")
    diagnostic = explain(book, "switch light")
    assert isinstance(normal, CommandAmbiguity)
    assert diagnostic.result == normal
    assert diagnostic.trace.decision == "ambiguous"
    assert diagnostic.trace.ambiguity_spell_ids == ("on", "off")
    assert diagnostic.trace.reason == "top_two_opposite_within_epsilon"

def test_explain_records_no_match_rejection():
    book = _book(_text("lumos", "lumos maxima", "ok"))
    diagnostic = explain(book, "lumos maxima kitchen")
    assert diagnostic.result is None
    assert diagnostic.trace.decision == "no_match"
    assert diagnostic.trace.reason == "no_eligible_candidate"
    assert diagnostic.trace.candidates[0].reason == "trigger_no_match"
