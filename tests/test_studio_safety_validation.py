from custom_components.hase import studio


def finding(a="a", b="b", kind="safety_ambiguity"):
    return {
        "kind": kind,
        "spell_ids": (a, b),
        "triggers": ("x {area}", "x {area}"),
        "specificity": (1, 1),
        "reason": "test",
    }


def test_interactive_owner_only_gets_error(monkeypatch):
    monkeypatch.setattr(
        studio, "_collision_report",
        lambda raw, language="en": {
            "semantics": 1, "scope": "test",
            "findings": [finding()], "count": 1,
        },
    )
    issues = studio._validation_issues(
        {"commands": []}, owner_spell_id="b"
    )
    assert [(i["spell_id"], i["related_spell_ids"]) for i in issues] == [
        ("b", ["a"])
    ]


def test_existing_peer_is_not_owner(monkeypatch):
    monkeypatch.setattr(
        studio, "_collision_report",
        lambda raw, language="en": {
            "semantics": 1, "scope": "test",
            "findings": [finding()], "count": 1,
        },
    )
    assert studio._validation_issues(
        {"commands": []}, owner_spell_id="other"
    ) == []


def test_full_book_marks_both_sides(monkeypatch):
    monkeypatch.setattr(
        studio, "_collision_report",
        lambda raw, language="en": {
            "semantics": 1, "scope": "test",
            "findings": [finding()], "count": 1,
        },
    )
    issues = studio._validation_issues({"commands": []})
    assert {(i["spell_id"], tuple(i["related_spell_ids"])) for i in issues} == {
        ("a", ("b",)),
        ("b", ("a",)),
    }


def test_order_dependent_is_not_validation_error(monkeypatch):
    monkeypatch.setattr(
        studio, "_collision_report",
        lambda raw, language="en": {
            "semantics": 1, "scope": "test",
            "findings": [finding(kind="order_dependent")], "count": 1,
        },
    )
    assert studio._validation_issues({"commands": []}) == []


def test_validation_does_not_mutate_enabled(monkeypatch):
    monkeypatch.setattr(
        studio, "_collision_report",
        lambda raw, language="en": {
            "semantics": 1, "scope": "test",
            "findings": [finding()], "count": 1,
        },
    )
    raw = {
        "commands": [
            {"id": "a", "enabled": True},
            {"id": "b", "enabled": False},
        ]
    }
    before = [dict(x) for x in raw["commands"]]
    studio._validation_issues(raw)
    assert raw["commands"] == before

def _real_safety_book():
    return {
        "id": "demo",
        "name": "Demo",
        "default_language": "de",
        "languages": ["de", "en"],
        "commands": [
            {
                "id": "existing_off",
                "name": {"de": "Leuchtfeuer aus"},
                "trigger": {
                    "de": "entzündet die leuchtfeuer von {area}",
                    "en": "light the beacons of {area}",
                },
                "operation": "light.turn_off",
                "target": {"kind": "area_name", "capture": "area"},
                "arguments": {},
                "enabled": True,
            },
            {
                "id": "edited_on",
                "trigger": {
                    "de": "entzündet die leuchtfeuer von {area}",
                    "en": "light the beacons of {area}",
                },
                "operation": "light.set_power",
                "target": {"kind": "area_name", "capture": "area"},
                "arguments": {"on": True},
                "enabled": True,
            },
        ],
    }


def test_spell_display_name_uses_localized_name_then_trigger_then_id():
    raw = _real_safety_book()

    assert studio._spell_display_name(raw, "existing_off", "de") == "Leuchtfeuer aus"
    assert (
        studio._spell_display_name(raw, "edited_on", "de")
        == "entzündet die leuchtfeuer von {area}"
    )
    assert studio._spell_display_name(raw, "missing", "de") == "missing"


def test_real_safety_confirmation_contains_author_facing_peer_name():
    raw = _real_safety_book()

    result = studio._owned_safety_confirmation(raw, "edited_on", "de")

    assert result is not None
    assert result["requires_confirmation"] is True
    assert result["confirmation_code"] == "safety_ambiguity"
    assert result["related_spell_ids"] == ["existing_off"]
    assert result["related_spell_names"] == ["Leuchtfeuer aus"]
    assert result["validation_issues"][0]["spell_id"] == "edited_on"
    assert result["validation_issues"][0]["related_spell_ids"] == ["existing_off"]


def test_save_spell_safety_confirmation_does_not_write(monkeypatch):
    import asyncio
    from copy import deepcopy
    from unittest.mock import AsyncMock

    raw = _real_safety_book()
    existing = raw["commands"][0]
    candidate = raw["commands"][1]
    raw["commands"] = [existing]

    instance = studio.SpellbookStudio.__new__(studio.SpellbookStudio)
    instance.read = AsyncMock(return_value=(deepcopy(raw), "rev-1"))
    instance.report = AsyncMock(return_value={"errors": 0})
    instance.write = AsyncMock(return_value="rev-2")

    monkeypatch.setattr(
        studio,
        "preserve_user",
        lambda old, new, remove_user=False: deepcopy(new),
    )

    async def run():
        return await instance.dispatch(
            "save_spell",
            {
                "book_id": "demo",
                "revision": "rev-1",
                "spell_id": None,
                "mode": "expert",
                "spell": deepcopy(candidate),
                "language": "de",
                "book_language": "de",
            },
            None,
        )

    result = asyncio.run(run())

    assert result["requires_confirmation"] is True
    assert result["confirmation_code"] == "safety_ambiguity"
    assert result["related_spell_names"] == ["Leuchtfeuer aus"]
    instance.report.assert_not_awaited()
    instance.write.assert_not_awaited()


def test_save_spell_safety_override_reaches_write(monkeypatch):
    import asyncio
    from copy import deepcopy
    from unittest.mock import AsyncMock

    raw = _real_safety_book()
    existing = raw["commands"][0]
    candidate = raw["commands"][1]
    raw["commands"] = [existing]

    instance = studio.SpellbookStudio.__new__(studio.SpellbookStudio)
    instance.read = AsyncMock(return_value=(deepcopy(raw), "rev-1"))
    instance.report = AsyncMock(return_value={"errors": 1})
    instance.write = AsyncMock(return_value="rev-2")

    monkeypatch.setattr(
        studio,
        "preserve_user",
        lambda old, new, remove_user=False: deepcopy(new),
    )

    async def run():
        return await instance.dispatch(
            "save_spell",
            {
                "book_id": "demo",
                "revision": "rev-1",
                "spell_id": None,
                "mode": "expert",
                "spell": deepcopy(candidate),
                "language": "de",
                "book_language": "de",
                "allow_safety_ambiguity": True,
            },
            None,
        )

    result = asyncio.run(run())

    assert result["revision"] == "rev-2"
    instance.report.assert_awaited_once()
    instance.write.assert_awaited_once()

    written_raw, written_revision = instance.write.await_args.args
    assert written_revision == "rev-1"
    assert [row["id"] for row in written_raw["commands"]] == [
        "existing_off",
        "edited_on",
    ]
    assert written_raw["commands"][0]["enabled"] is True
    assert written_raw["commands"][1]["enabled"] is True

