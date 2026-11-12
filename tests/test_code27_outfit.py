"""Outfit switching (firmware 1.5.3): select labels, coordinator flow."""
from __future__ import annotations

import ast
import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import test_bridge_delivery as harness

PROVIDER = Path(__file__).resolve().parents[1] / "custom_components" / "hase" / "providers" / "code27"


def _load_select_helpers():
    """`outfit_choices` and `_dedupe_names` are pure; the entity classes need
    Home Assistant, so only those two functions are executed here."""
    tree = ast.parse((PROVIDER / "select.py").read_text(encoding="utf-8"))
    wanted = [
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name in {"_dedupe_names", "outfit_choices"}
    ]
    namespace: dict = {}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), "select.py", "exec"), namespace)
    return namespace["outfit_choices"]


outfit_choices = _load_select_helpers()


class OutfitChoicesTests(unittest.TestCase):
    def test_stock_and_custom_outfits_are_labelled(self):
        character = {
            "available_cloths": "1,2,3",
            "custom_cloths": "\nLylia.vrm\nLylia_v2.vrm",
        }
        self.assertEqual(
            outfit_choices(character), {"Outfit 1": 1, "Lylia": 2, "Lylia_v2": 3}
        )

    def test_no_custom_cloths_means_plain_numbers(self):
        self.assertEqual(
            outfit_choices({"available_cloths": "1,3", "custom_cloths": "\n\n"}),
            {"Outfit 1": 1, "Outfit 3": 3},
        )

    def test_duplicate_names_are_disambiguated(self):
        labels = outfit_choices(
            {"available_cloths": "1,2", "custom_cloths": "A.vrm\nA.vrm"}
        )
        self.assertEqual(sorted(labels.values()), [1, 2])
        self.assertEqual(len(labels), 2)

    def test_missing_or_garbage_available_cloths_yield_nothing(self):
        self.assertEqual(outfit_choices({}), {})
        self.assertEqual(outfit_choices({"available_cloths": ",x,"}), {})


class SetOutfitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        coordinator_module = harness.coordinator_module
        hass = harness.N(
            config=harness.N(language="de"),
            bus=harness.N(async_fire=lambda kind, data: None),
            async_create_task=lambda coro, **kwargs: asyncio.create_task(coro),
        )
        entry = harness.N(entry_id="device-a", options={}, data={})
        self.client = harness.N(apply_outfit=AsyncMock(return_value={"selected_cloth": 3}))
        self.c = coordinator_module.Code27Coordinator(hass, entry, self.client, 15)
        self.c.data = coordinator_module.Code27Data(current_slot={"slot_number": 1})
        self.c.async_request_refresh = AsyncMock()
        self.states = []
        self.c.async_update_listeners = lambda: self.states.append(self.c.model_loading)

    async def test_waits_for_model_load_and_flags_loading(self):
        self.client.get_character_loading_state = AsyncMock(side_effect=[
            {"is_loading": True}, {"is_loading": False},
        ])
        with patch.object(asyncio, "sleep", AsyncMock()):
            await self.c.async_set_outfit(1, 3)
        self.client.apply_outfit.assert_awaited_once_with(1, selected_cloth=3)
        self.assertEqual(self.states, [True, False])
        self.assertFalse(self.c.model_loading)
        self.assertEqual(self.client.get_character_loading_state.await_count, 2)
        self.c.async_request_refresh.assert_awaited_once()

    async def test_state_read_errors_are_retried_until_deadline(self):
        self.client.get_character_loading_state = AsyncMock(side_effect=[
            harness.api.Code27Error("busy"), {"is_loading": False},
        ])
        with patch.object(asyncio, "sleep", AsyncMock()):
            await self.c.async_set_outfit(1, 2)
        self.assertEqual(self.client.get_character_loading_state.await_count, 2)
        self.assertFalse(self.c.model_loading)

    async def test_rejected_apply_never_sets_loading(self):
        self.client.apply_outfit = AsyncMock(side_effect=harness.api.Code27Error("409"))
        with self.assertRaises(harness.api.Code27Error):
            await self.c.async_set_outfit(1, 3)
        self.assertEqual(self.states, [])
        self.assertFalse(self.c.model_loading)


if __name__ == "__main__":
    unittest.main()
