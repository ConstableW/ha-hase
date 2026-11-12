"""Optional per-spell failure wording (`failure_text`).

A spell may declare its own sentence for a FAILED execution, localized like
`output`. It replaces the fixed sentence of
core.executor.response_text_for_failure only for a real failure; a denial
or invalid input keeps the fixed sentence. Absent, nothing changes.
"""
from __future__ import annotations

import unittest

import test_core_engine as core
import test_mcp_execution as mcp

spellbook_engine = core.spellbook_engine
ExecutionStatus = core.ExecutionStatus
ExecutionResult = core._models_mod.ExecutionResult
ActionExecutor = core.ActionExecutor
response_text_for_failure = core._executor_mod.response_text_for_failure

LAMP = {
    "id": "lamp",
    "trigger": {"de": "lampe {area} an", "en": "lamp {area} on"},
    "operation": "light.set_power",
    "target": {"kind": "area_name", "capture": "area"},
    "arguments": {"on": True},
    "catchphrase": "Licht {area} ist an.",
    "failure_text": {"de": "Das Licht in {area} reagiert gerade nicht.", "en": "The {area} light isn't responding."},
}


def load(command):
    return spellbook_engine.load_spellbook_from_dict("t", {"id": "t", "name": "T", "commands": [command]})


class LoaderTests(unittest.TestCase):
    def test_failure_text_is_loaded(self):
        book = load(LAMP)
        self.assertIsNotNone(book)
        self.assertEqual(book.commands[0].failure_text, LAMP["failure_text"])

    def test_spell_without_failure_text_still_loads(self):
        raw = {k: v for k, v in LAMP.items() if k != "failure_text"}
        self.assertIsNone(load(raw).commands[0].failure_text)

    def test_invalid_type_is_reported(self):
        diagnostics: list = []
        cmd = spellbook_engine._load_command("lamp", {**LAMP, "failure_text": 5}, diagnostics=diagnostics)
        self.assertIsNone(cmd)
        self.assertEqual(diagnostics[0]["code"], "invalid_failure_text")

    def test_unknown_placeholder_is_reported(self):
        diagnostics: list = []
        cmd = spellbook_engine._load_command(
            "lamp", {**LAMP, "failure_text": "Fehler bei {brightness}"}, diagnostics=diagnostics,
        )
        self.assertIsNone(cmd)
        self.assertEqual(diagnostics[0]["code"], "unknown_failure_text_placeholder")


class RenderTests(unittest.TestCase):
    def test_draft_carries_rendered_failure_text_per_language(self):
        command = load(LAMP).commands[0]
        de = spellbook_engine.build_draft(command, {"area": "Küche"}, "de")
        en = spellbook_engine.build_draft(command, {"area": "Kitchen"}, "en")
        self.assertEqual(de.failure_text, "Das Licht in Küche reagiert gerade nicht.")
        self.assertEqual(en.failure_text, "The Kitchen light isn't responding.")

    def test_only_a_real_failure_uses_the_spell_text(self):
        custom = "Das Licht reagiert gerade nicht."
        failed = ExecutionResult(ExecutionStatus.FAILED, "service_call_rejected")
        unavailable = ExecutionResult(ExecutionStatus.FAILED, "target_unavailable")
        denied = ExecutionResult(ExecutionStatus.DENIED, "area_not_allowed")
        invalid = ExecutionResult(ExecutionStatus.INVALID, "invalid_arguments")
        self.assertEqual(response_text_for_failure(failed, custom), custom)
        self.assertEqual(response_text_for_failure(unavailable, custom), custom)
        self.assertEqual(response_text_for_failure(denied, custom), "Diese Aktion ist nicht erlaubt.")
        self.assertEqual(response_text_for_failure(invalid, custom), "Die Angaben für diese Aktion sind ungültig.")
        self.assertEqual(response_text_for_failure(failed), "Die Aktion konnte nicht sicher bestätigt werden.")


class McpTests(unittest.IsolatedAsyncioTestCase):
    def _executor(self, accept):
        resolver = mcp.FakeResolver({"Küche": ("light.kueche", "kueche")})
        return ActionExecutor(resolver, mcp.FakeCaller(accept=accept))

    async def test_failed_call_returns_the_spell_text(self):
        result = await mcp.execute_mcp_tool_call(
            load(LAMP), "lamp", {"area": "Küche"}, mcp.bridge_b(), self._executor(False),
        )
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(result.response_text, "Das Licht in Küche reagiert gerade nicht.")

    async def test_success_still_returns_the_catchphrase(self):
        result = await mcp.execute_mcp_tool_call(
            load(LAMP), "lamp", {"area": "Küche"}, mcp.bridge_b(), self._executor(True),
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.response_text, "Licht Küche ist an.")

    async def test_failed_call_without_spell_text_leaves_the_fixed_sentence_to_the_transport(self):
        raw = {k: v for k, v in LAMP.items() if k != "failure_text"}
        result = await mcp.execute_mcp_tool_call(
            load(raw), "lamp", {"area": "Küche"}, mcp.bridge_b(), self._executor(False),
        )
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertIsNone(result.response_text)


class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_turn_speaks_the_spell_text_on_failure(self):
        resolver = core.FakeResolver({"Küche": core.ResolvedTargets((core.ResolvedEntity("light.kueche", "kueche"),))})
        router = core.SpellbookRouter(ActionExecutor(resolver, core.FakeCaller(accept=False)))
        config = core.AgentConfig(
            mode=core.FrontendMode.SPELLBOOK_ONLY, spellbook=load(LAMP),
            policy=core._scoped(core.AreaPolicy(allowed_areas=frozenset({"kueche"}))),
        )
        request = core.ConversationRequest(
            request_id=core._next_request_id(), agent_entry_id="e1", text="lampe Küche an",
            language="de", source_namespace="test",
        )
        result = await router.handle(request, config)
        self.assertEqual(result.status, core.ConversationStatus.ERROR)
        self.assertEqual(result.response_text, "Das Licht in Küche reagiert gerade nicht.")


if __name__ == "__main__":
    unittest.main()
