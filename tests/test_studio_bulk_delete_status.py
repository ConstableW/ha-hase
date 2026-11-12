"""P6.5 backend: all-or-nothing bulk delete and tab status summaries."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from custom_components.hase import studio
from custom_components.hase.core.studio import mcp_capability_report

AREA = {"kind": "area_name", "capture": "area"}


def _book():
    trigger = {"de": "leuchtfeuer {area}"}
    return {
        "id": "b", "name": "B", "default_language": "de", "languages": ["de"],
        "commands": [
            {"id": "on", "trigger": trigger, "operation": "light.turn_on", "target": AREA, "enabled": True},
            {"id": "off", "trigger": trigger, "operation": "light.turn_off", "target": AREA, "enabled": True},
            {"id": "text", "trigger": {"de": "hallo"}, "output": {"de": "hi"}, "enabled": False},
            {"id": "keep", "trigger": {"de": "lumos"}, "operation": "light.turn_on",
             "target": {"kind": "area_name", "value": "Gondor"}, "enabled": True},
        ],
    }


def _instance(raw):
    instance = studio.SpellbookStudio.__new__(studio.SpellbookStudio)
    instance.read = AsyncMock(return_value=(deepcopy(raw), "rev-1"))
    instance.report = AsyncMock(side_effect=lambda raw, data: {"status": studio._collision_report(raw, "de")["status"]})
    instance.write = AsyncMock(return_value="rev-2")
    return instance


def _run(instance, operation, data):
    return asyncio.run(instance.dispatch(operation, {"book_id": "b", "revision": "rev-1", **data}, None))


def test_delete_spells_is_registered_in_websocket_schema():
    source = (Path(studio.__file__).parent / "studio_api.py").read_text(encoding="utf-8")
    schema = source[source.index('vol.Required("operation")'):source.index('vol.Optional("data"')]
    assert '"delete_spells"' in schema


def test_bulk_delete_removes_exactly_the_selection_in_one_write():
    raw = _book()
    instance = _instance(raw)
    result = _run(instance, "delete_spells", {"spell_ids": ["off", "text"], "confirmed": True})
    assert result["revision"] == "rev-2" and result["deleted"] == ["off", "text"]
    instance.write.assert_awaited_once()
    written, revision = instance.write.await_args.args
    assert revision == "rev-1"
    assert [c["id"] for c in written["commands"]] == ["on", "keep"]
    # enabled of the remaining spells is untouched
    assert {c["id"]: c["enabled"] for c in written["commands"]} == {"on": True, "keep": True}
    # diagnosis recomputed on the new book: the on/off safety conflict is gone
    assert result["report"]["status"]["state"] == "ok"


@pytest.mark.parametrize("ids", [["off", "ghost"], [], ["off", "off"], "off", [3]])
def test_bulk_delete_rejects_invalid_or_stale_selection_without_writing(ids):
    instance = _instance(_book())
    with pytest.raises(studio.StudioError):
        _run(instance, "delete_spells", {"spell_ids": ids, "confirmed": True})
    instance.write.assert_not_awaited()
    instance.report.assert_not_awaited()


def test_bulk_delete_requires_explicit_confirmation():
    instance = _instance(_book())
    with pytest.raises(studio.StudioError):
        _run(instance, "delete_spells", {"spell_ids": ["off"]})
    instance.write.assert_not_awaited()


def test_collision_report_status_reflects_analyzer_severities():
    raw = _book()
    status = studio._collision_report(raw, "de")["status"]
    assert status == {"state": "error", "errors": 1, "warnings": 0, "infos": 0}
    raw["commands"][1]["operation"] = "light.turn_on"  # same outcome -> order_dependent
    assert studio._collision_report(raw, "de")["status"]["state"] == "warning"
    raw["commands"] = raw["commands"][2:]
    assert studio._collision_report(raw, "de")["status"] == {"state": "ok", "errors": 0, "warnings": 0, "infos": 0}


def test_report_keeps_schema_and_safety_errors_apart_and_sums_them():
    # errors used to be overwritten by the safety count alone.
    raw = _book()
    instance = studio.SpellbookStudio.__new__(studio.SpellbookStudio)

    class _Hass:
        async def async_add_executor_job(self, func, *args):
            return func(*args)

    instance.hass = _Hass()
    instance.areas = lambda: []
    instance.test = AsyncMock(return_value={"status": "SUCCESS"})
    report = asyncio.run(instance.report(raw, {"language": "de", "book_language": "de"}))
    assert report["safety_errors"] == 2  # both sides of the on/off pair
    assert report["errors"] == report["schema_errors"] + report["safety_errors"]
    assert report["status"]["state"] == "error"
    assert report["status"]["errors"] == report["errors"]


def test_mcp_report_status_counts_spells_needing_attention_once():
    raw = _book()
    report = mcp_capability_report(raw, mark_stale=True)
    attention = {e["id"] for e in report["spells"]
                 if not e["projectable"] or e["requires_unrestricted_bridge"] or e.get("stale")}
    assert report["status"]["warnings"] == len(attention)
    assert report["status"]["state"] == ("warning" if attention else "ok")
    assert report["status"]["errors"] == 0


def test_report_lists_which_spells_the_executable_tiles_count():
    raw = _book()
    instance = studio.SpellbookStudio.__new__(studio.SpellbookStudio)

    class _Hass:
        async def async_add_executor_job(self, func, *args):
            return func(*args)

    instance.hass = _Hass()
    instance.areas = lambda: []

    async def fake_test(raw, payload):
        return {"status": "SUCCESS" if payload["phrase"] == "lumos" else "NO_MATCH"}

    instance.test = fake_test
    report = asyncio.run(instance.report(raw, {"language": "de", "book_language": "de"}))
    assert report["executable_ids"]["user"] == ["keep"]
    assert report["user_executable"] == len(report["executable_ids"]["user"])
    assert report["expert_executable"] == len(report["executable_ids"]["expert"])
    assert report["warnings"] == sum(len(row["warnings"]) for row in report["spells"])
