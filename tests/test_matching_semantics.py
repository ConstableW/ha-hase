"""HA-free contract tests for Matching Semantics v1.

These tests intentionally freeze the observable matcher behavior documented
above find_command(). They are contracts, not proposals for a new ranking model.
"""

from custom_components.hase.core.spellbook_engine import (
    CommandAmbiguity,
    CommandMatch,
    find_command,
    load_spellbook_from_dict,
)


def _book(*commands):
    book = load_spellbook_from_dict(
        "semantics_v1",
        {"id": "semantics_v1", "name": "Semantics v1", "commands": list(commands)},
    )
    assert book is not None
    return book


def _text(spell_id, trigger, output):
    return {"id": spell_id, "trigger": trigger, "output": output}


def _power(spell_id, trigger, on):
    return {
        "id": spell_id,
        "trigger": trigger,
        "operation": "light.set_power",
        "target": {"kind": "area_name", "value": "Test Area"},
        "arguments": {"on": on},
    }


def test_lower_tier_not_evaluated_if_higher_matches():
    book = _book(
        _text("higher", "turn light {area}", "higher"),
        _text("lower", "turn {area}", "lower"),
    )

    result = find_command(book, "turn light kitchen")

    assert isinstance(result, CommandMatch)
    assert result.command.spell_id == "higher"


def test_unresolvable_draft_discards_candidate():
    book = _book(
        {
            "id": "higher_unresolvable",
            "trigger": "set temp {value}",
            "operation": "climate.set_temperature",
            "target": {"kind": "area_name", "value": "Test Area"},
            "arguments": {
                "temperature": {
                    "capture": "value",
                    "type": "float",
                    "min": 5,
                    "max": 35,
                }
            },
        },
        _text("lower_resolvable", "set {value}", "lower"),
    )

    result = find_command(book, "set temp bananas")

    assert isinstance(result, CommandMatch)
    assert result.command.spell_id == "lower_resolvable"
    assert result.captures["value"] == "temp bananas"


def test_third_candidate_within_epsilon_ignored():
    book = _book(
        _text("first", "alpha", "Licht an"),
        _text("second", "alpha", "Status unverändert"),
        _text("third", "alpha", "Licht aus"),
    )

    result = find_command(book, "alpha")

    assert isinstance(result, CommandMatch)
    assert result.command.spell_id == "first"


def test_equal_score_non_opposite_resolves_by_order():
    book = _book(
        _text("first", "status {area}", "Erste Antwort"),
        _text("second", "status {room}", "Zweite Antwort"),
    )

    result = find_command(book, "status kitchen")

    assert isinstance(result, CommandMatch)
    assert result.command.spell_id == "first"


def test_trailing_tokens_reject_match():
    book = _book(_text("lumos_maxima", "lumos maxima", "ok"))

    result = find_command(book, "lumos maxima kitchen")

    assert result is None


def test_higher_specificity_beats_higher_fuzzy_score():
    book = _book(
        _text("higher_specificity", "turn light {area}", "higher"),
        _text("lower_exact", "turn {area}", "lower"),
    )

    result = find_command(book, "turn ligh kitchen")

    assert isinstance(result, CommandMatch)
    assert result.command.spell_id == "higher_specificity"
    assert result.score < 1.0


def test_opposite_top_two_within_epsilon_is_ambiguous():
    book = _book(
        _power("on", "switch light", True),
        _power("off", "switch light", False),
    )

    result = find_command(book, "switch light")

    assert isinstance(result, CommandAmbiguity)
    assert result.option_a.command.spell_id == "on"
    assert result.option_b.command.spell_id == "off"
