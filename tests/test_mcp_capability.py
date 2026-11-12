"""core.mcp_capability table-driven tests - Spellbook Studio's own "MCP
prüfen" support. core/mcp_capability.py has zero Home Assistant imports
(same boundary rule as the rest of core/), loaded exactly like
core.spellbook_engine/core.mcp_projection already are elsewhere in this
suite - see test_mcp_projection.py's own loading pattern, reproduced here
standalone so this file has no dependency on test collection order.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "hase"

_core_spec = importlib.util.spec_from_file_location(
    "hase_spellbook_core", ROOT / "core/__init__.py",
    submodule_search_locations=[str(ROOT / "core")],
)
if _core_spec.name not in sys.modules:
    _core_module = importlib.util.module_from_spec(_core_spec)
    sys.modules[_core_spec.name] = _core_module
    _core_spec.loader.exec_module(_core_module)

engine = importlib.import_module("hase_spellbook_core.spellbook_engine")
capability = importlib.import_module("hase_spellbook_core.mcp_capability")


def book(*commands, book_id: str = "test") -> engine.EngineSpellbook:
    raw = {"id": book_id, "name": "Test", "commands": list(commands)}
    loaded = engine.load_spellbook_from_dict(book_id, raw)
    assert loaded is not None, "test fixture spellbook failed to load"
    return loaded


def one(book_obj: engine.EngineSpellbook):
    assert len(book_obj.commands) == 1, [c.spell_id for c in book_obj.commands]
    return book_obj.commands[0]


LUMOS = {
    "id": "lumos", "trigger": "lumos {area}",
    "operation": "light.set_power",
    "target": {"kind": "area_name", "capture": "area"},
    "arguments": {"on": True},
}
AVG_TEMP = {
    "id": "avg_temp", "trigger": "average temperature in {area}",
    "sources": [
        {"kind": "avg", "target": {"kind": "area_name", "capture": "area"},
         "domain_filter": "sensor", "device_class": "temperature", "variable": "t"},
    ],
    "output": "{t} degrees",
}
COMBINED = {
    "id": "evening_routine", "trigger": "evening routine {area}",
    "steps": [
        {"alias": "light", "operation": "light.turn_on", "target": {"kind": "area_name", "capture": "area"}},
        {"alias": "temp", "sources": [
            {"kind": "avg", "target": {"kind": "area_name", "capture": "area"},
             "domain_filter": "sensor", "device_class": "temperature", "variable": "t"},
        ]},
    ],
    "output": "{light.target_count} lights, {temp.t} degrees",
}
TEXT_ANSWER = {
    "id": "free_text", "trigger": "tell me a joke", "output": "Why...", "assist_redirect": False,
}
ASSIST_REDIRECT = {
    "id": "sunrise", "trigger": "sunrise over {area}", "output": "Set light {area} to 100%.",
}


class ExecutionKindTests(unittest.TestCase):
    def test_action_command(self):
        self.assertEqual(capability.execution_kind(one(book(LUMOS))), "action")

    def test_query_command(self):
        self.assertEqual(capability.execution_kind(one(book(AVG_TEMP))), "query")

    def test_combined_command(self):
        self.assertEqual(capability.execution_kind(one(book(COMBINED))), "combined")

    def test_text_answer_command(self):
        self.assertEqual(capability.execution_kind(one(book(TEXT_ANSWER))), "text_answer")

    def test_assist_redirect_command(self):
        self.assertEqual(capability.execution_kind(one(book(ASSIST_REDIRECT))), "assist_redirect")

    def test_never_inferred_from_placeholder_names_or_wording(self):
        # The user's own explicit, repeated requirement: {area}/{target}
        # placeholders and command-sounding wording must never influence
        # classification - only operation/sources/steps/assist_redirect.
        looks_like_a_command = {
            "id": "looks_like_a_command", "trigger": "turn off the light in {area}",
            "output": "Licht {area} ausschalten.",
        }
        # assist_redirect defaults True (models.DEFAULT_ASSIST_REDIRECT) -
        # classified as assist_redirect purely from that field, regardless
        # of how command-like the wording reads.
        self.assertEqual(capability.execution_kind(one(book(looks_like_a_command))), "assist_redirect")
        looks_like_an_answer = {**looks_like_a_command, "id": "x", "assist_redirect": False}
        self.assertEqual(capability.execution_kind(one(book(looks_like_an_answer))), "text_answer")


class CommandCapabilityTests(unittest.TestCase):
    def test_action_is_projectable_not_read_only_never_requires_unrestricted(self):
        result = capability.check_command_mcp_capability(one(book(LUMOS)))
        self.assertTrue(result.projectable)
        self.assertIsNone(result.skip_reason)
        self.assertFalse(result.read_only)
        self.assertFalse(result.requires_unrestricted_bridge)
        self.assertIsNone(result.grant_reason)

    def test_query_is_projectable_and_read_only(self):
        result = capability.check_command_mcp_capability(one(book(AVG_TEMP)))
        self.assertTrue(result.projectable)
        self.assertTrue(result.read_only)
        self.assertFalse(result.requires_unrestricted_bridge)

    def test_text_answer_is_projectable_read_only_never_requires_unrestricted(self):
        result = capability.check_command_mcp_capability(one(book(TEXT_ANSWER)))
        self.assertTrue(result.projectable)
        self.assertTrue(result.read_only)
        self.assertFalse(result.requires_unrestricted_bridge)
        self.assertIsNone(result.grant_reason)

    def test_assist_redirect_is_projectable_never_read_only_and_requires_unrestricted(self):
        # The user's own explicit requirement: structural capability
        # (projectable) and actual grant (requires_unrestricted_bridge)
        # are reported SEPARATELY, never conflated - an assist_redirect
        # Spell is a real, callable tool (projectable=True) that ALSO
        # carries a real precondition (requires_unrestricted_bridge=True),
        # with the reason surfaced using the exact same reason_code the
        # runtime DENIED path reports.
        result = capability.check_command_mcp_capability(one(book(ASSIST_REDIRECT)))
        self.assertTrue(result.projectable)
        self.assertFalse(result.read_only)
        self.assertTrue(result.requires_unrestricted_bridge)
        self.assertEqual(result.grant_reason, "assist_redirect_requires_unrestricted_policy")
        self.assertEqual(result.grant_reason, capability.ASSIST_REDIRECT_GRANT_REASON)

    def test_unknown_operation_is_not_projectable_with_a_concrete_reason(self):
        b = book({"id": "bad", "trigger": "do a thing in {area}", "operation": "not.a.real.operation",
                  "target": {"kind": "area_name", "capture": "area"}, "arguments": {}})
        result = capability.check_command_mcp_capability(one(b))
        self.assertFalse(result.projectable)
        self.assertEqual(result.skip_reason, "unknown_operation:not.a.real.operation")
        self.assertIsNone(result.read_only)
        self.assertFalse(result.requires_unrestricted_bridge)


class BookCapabilityTests(unittest.TestCase):
    def test_every_enabled_command_is_checked(self):
        b = book(LUMOS, AVG_TEMP, TEXT_ANSWER, ASSIST_REDIRECT)
        results = capability.check_book_mcp_capability(b)
        self.assertEqual({r.spell_id for r in results}, {"lumos", "avg_temp", "free_text", "sunrise"})

    def test_disabled_command_is_silently_absent(self):
        b = book({**LUMOS, "enabled": False}, AVG_TEMP)
        results = capability.check_book_mcp_capability(b)
        self.assertEqual({r.spell_id for r in results}, {"avg_temp"})

    def test_duplicate_spell_id_is_reported_never_silently_resolved(self):
        first = {"id": "dup", "trigger": "say hi", "output": "Hi!", "assist_redirect": False}
        second = {"id": "dup", "trigger": "say bye", "output": "Bye!", "assist_redirect": False}
        b = book(first, second)
        results = capability.check_book_mcp_capability(b)
        self.assertEqual(len(results), 2)
        for result in results:
            self.assertFalse(result.projectable)
            self.assertEqual(result.skip_reason, "duplicate_spell_id")

    def test_aggregate_counts_match_demo_spellbook(self):
        # demo.yaml is the generic test contract: every command is projectable
        # (never skipped), and exactly its text-only assist_redirect Spell
        # ("sunrise") requires an unrestricted Bridge. Expected values are
        # derived from the raw YAML so they follow the demo book.
        path = ROOT / "spellbooks" / "demo.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["disabled"] = False  # Exercise commands in the shipped-disabled Demo.
        demo_book = engine.load_spellbook_from_dict("demo", raw)
        self.assertIsNotNone(demo_book)
        results = capability.check_book_mcp_capability(demo_book)
        self.assertEqual(len(results), len(raw["commands"]))
        self.assertTrue(all(r.projectable for r in results))
        text_only = {
            c["id"] for c in raw["commands"]
            if not any(k in c for k in ("operation", "sources", "steps")) and c.get("assist_redirect", True)
        }
        self.assertEqual({r.spell_id for r in results if r.requires_unrestricted_bridge}, text_only)
        self.assertIn("sunrise", text_only)
        self.assertTrue(all(r.execution_kind == "assist_redirect" for r in results if r.requires_unrestricted_bridge))
        self.assertTrue({"action", "query", "combined", "assist_redirect"} <= {r.execution_kind for r in results})


if __name__ == "__main__":
    unittest.main()
