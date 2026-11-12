"""Preserved frontend result-to-speech regressions through generic execute().

Adapter/resolver/executor integration is covered by test_unified_spellbooks.
"""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import test_bridge_delivery as support

N = support.N
constants = support.constants

HASE_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "hase"


def _load_core_package():
    if "hase_spellbook_core" in sys.modules:
        return sys.modules["hase_spellbook_core"]
    spec = importlib.util.spec_from_file_location(
        "hase_spellbook_core",
        HASE_ROOT / "core" / "__init__.py",
        submodule_search_locations=[str(HASE_ROOT / "core")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_load_core_package()
_models = importlib.import_module("hase_spellbook_core.models")
_executor = importlib.import_module("hase_spellbook_core.executor")
ExecutionResult = _models.ExecutionResult
ExecutionStatus = _models.ExecutionStatus


class FakeAdapter:
    """Stands in for adapters.code27.Code27FrontendAdapter - records
    what it was asked to do and returns a pre-set ExecutionResult."""

    _next_result: ExecutionResult | None = None
    _calls: list = []  # noqa: RUF012 - shared call recorder reset in setUp.

    def __init__(self, hass):
        pass

    async def execute(self, draft, **kwargs):
        if draft.operation == "vacuum.clean_area":
            FakeAdapter._calls.append(("vacuum", draft.target.value))
        else:
            FakeAdapter._calls.append(("lights", draft.arguments["on"]))
        return FakeAdapter._next_result


def load_bridge():
    return support.load_source(
        "bridge", **constants, spellbook=support.spellbook, callback=lambda f: f,
        PendingCorrection=support.coordinator_module.PendingCorrection,
        EVENT_USER_MESSAGE=support.coordinator_module.EVENT_USER_MESSAGE,
        intent=N(IntentResponseType=N(ERROR="error", ACTION_DONE="done")),
        Code27FrontendAdapter=FakeAdapter,
        ExecutionStatus=ExecutionStatus,
        response_text_for_failure=_executor.response_text_for_failure,
        response_text_for_success=_executor.response_text_for_success,
    )


class VacuumCutoverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bridge = load_bridge()
        FakeAdapter._calls = []
        self.hass = N(
            config=N(language="de"),
            bus=N(async_fire=lambda *a, **kw: None),
        )
        self.entry = N(entry_id="e1", options={})
        self.coordinator = N(
            entry=self.entry,
            data=N(current_slot={"slot_number": 1}, system={}),
        )

    async def test_success_speaks_catchphrase_and_logs_room(self):
        FakeAdapter._next_result = ExecutionResult(
            ExecutionStatus.SUCCESS, "ok", {"vacuum_entity_id": "vacuum.kreacher", "room_name": "Zwergstadt"}
        )
        with patch.object(self.bridge, "_output_speech", AsyncMock()) as speak, \
             patch.object(self.bridge, "_log_activity") as log:
            await self.bridge._handle_vacuum_clean_action(
                self.hass, self.entry, self.coordinator, "Zwergstadt", "Wird gereinigt.", None
            )
        self.assertEqual(FakeAdapter._calls, [("vacuum", "Zwergstadt")])
        speak.assert_awaited_once()
        self.assertEqual(speak.call_args.args[3], "Wird gereinigt.")
        log.assert_called_once()

    async def test_unresolved_area_logs_only_no_speech(self):
        FakeAdapter._next_result = ExecutionResult(ExecutionStatus.INVALID, "target_not_found", {})
        with patch.object(self.bridge, "_output_speech", AsyncMock()) as speak, \
             patch.object(self.bridge, "_log_activity") as log:
            await self.bridge._handle_vacuum_clean_action(
                self.hass, self.entry, self.coordinator, "Nirgendwo", None, None
            )
        speak.assert_not_awaited()
        self.assertIn("Nirgendwo", log.call_args.args[2])

    async def test_ambiguous_logs_only_no_speech(self):
        FakeAdapter._next_result = ExecutionResult(
            ExecutionStatus.FAILED, "ambiguous_vacuum_match",
            {"candidates_description": "vacuum.a/Room, vacuum.b/Room"},
        )
        with patch.object(self.bridge, "_output_speech", AsyncMock()) as speak, \
             patch.object(self.bridge, "_log_activity") as log:
            await self.bridge._handle_vacuum_clean_action(
                self.hass, self.entry, self.coordinator, "Room", None, None
            )
        speak.assert_not_awaited()
        log.assert_called_once()

    async def test_generic_failure_speaks_error(self):
        FakeAdapter._next_result = ExecutionResult(ExecutionStatus.FAILED, "vacuum_error", {})
        with patch.object(self.bridge, "_output_speech", AsyncMock()) as speak:
            await self.bridge._handle_vacuum_clean_action(
                self.hass, self.entry, self.coordinator, "Zwergstadt", None, None
            )
        speak.assert_awaited_once()


class LightAllCutoverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bridge = load_bridge()
        FakeAdapter._calls = []
        self.hass = N(config=N(language="de"), bus=N(async_fire=lambda *a, **kw: None))
        self.entry = N(entry_id="e1", options={})
        self.coordinator = N(entry=self.entry, data=N(current_slot={"slot_number": 1}, system={}))

    async def test_light_all_on_calls_adapter_with_true(self):
        FakeAdapter._next_result = ExecutionResult(ExecutionStatus.SUCCESS, "ok", {})
        with patch.object(self.bridge, "_output_speech", AsyncMock()), \
             patch.object(self.bridge, "_log_activity"):
            await self.bridge._handle_light_all_action(
                self.hass, self.entry, self.coordinator, True, None, None
            )
        self.assertEqual(FakeAdapter._calls, [("lights", True)])

    async def test_light_all_off_calls_adapter_with_false(self):
        FakeAdapter._next_result = ExecutionResult(ExecutionStatus.SUCCESS, "ok", {})
        with patch.object(self.bridge, "_output_speech", AsyncMock()), \
             patch.object(self.bridge, "_log_activity"):
            await self.bridge._handle_light_all_action(
                self.hass, self.entry, self.coordinator, False, None, None
            )
        self.assertEqual(FakeAdapter._calls, [("lights", False)])

    async def test_failure_speaks_error_message(self):
        FakeAdapter._next_result = ExecutionResult(ExecutionStatus.FAILED, "service_call_rejected", {})
        with patch.object(self.bridge, "_output_speech", AsyncMock()) as speak, \
             patch.object(self.bridge, "_log_activity"):
            await self.bridge._handle_light_all_action(
                self.hass, self.entry, self.coordinator, True, None, None
            )
        speak.assert_awaited_once()
        self.assertIn("nicht sicher", speak.call_args.args[3])
        self.assertNotIn("service_call_rejected", speak.call_args.args[3])


if __name__ == "__main__":
    unittest.main()
