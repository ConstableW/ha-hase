"""Runs docs/examples/reference_bridge_adapter.py through the shared
contract fixtures (tests/_bridge_contract.py) - proves the documented
minimal adapter pattern actually works against real core/ APIs, and that
the contract helper genuinely catches a missing-dispatch-branch bug (see
_bridge_contract.py's own module docstring for the real CODE27 query_plan
history this guards against).

The reference adapter's own imports are written the way a REAL provider
module would write them (`from custom_components.hase.core... import ...`)
since that is what belongs in the docs - this test file is the one place
that bridges that real import path onto the same HA-free `core/` modules
every other test in this suite already loads under the `hase_spellbook_
core` alias (see test_studio.py for the same technique applied to
`hase_studio_test`), so importing the reference adapter here never
requires a real `homeassistant` install.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "hase"

_core_spec = importlib.util.spec_from_file_location(
    "hase_spellbook_core", COMPONENT / "core/__init__.py",
    submodule_search_locations=[str(COMPONENT / "core")],
)
if _core_spec.name not in sys.modules:
    _core_module = importlib.util.module_from_spec(_core_spec)
    sys.modules[_core_spec.name] = _core_module
    _core_spec.loader.exec_module(_core_module)

# Make `from custom_components.hase.core.<name> import ...` (the reference
# adapter's own, real-provider-shaped imports) resolve to the SAME already-
# loaded, HA-free modules - never a second copy.
_cc_pkg = types.ModuleType("custom_components")
_cc_pkg.__path__ = [str(ROOT / "custom_components")]
_hase_pkg = types.ModuleType("custom_components.hase")
_hase_pkg.__path__ = [str(COMPONENT)]
sys.modules.setdefault("custom_components", _cc_pkg)
sys.modules.setdefault("custom_components.hase", _hase_pkg)
sys.modules["custom_components.hase.core"] = sys.modules["hase_spellbook_core"]
for _name in ("executor", "models", "spellbook_engine"):
    sys.modules[f"custom_components.hase.core.{_name}"] = importlib.import_module(f"hase_spellbook_core.{_name}")

_adapter_spec = importlib.util.spec_from_file_location(
    "reference_bridge_adapter", ROOT / "docs/examples/reference_bridge_adapter.py",
)
_adapter_module = importlib.util.module_from_spec(_adapter_spec)
sys.modules[_adapter_spec.name] = _adapter_module
_adapter_spec.loader.exec_module(_adapter_module)
ReferenceChatAdapter = _adapter_module.ReferenceChatAdapter

import _bridge_contract as contract

executor_mod = importlib.import_module("hase_spellbook_core.executor")
models = importlib.import_module("hase_spellbook_core.models")

ActionExecutor = executor_mod.ActionExecutor
ResolvedEntity = executor_mod.ResolvedEntity
ResolvedTargets = executor_mod.ResolvedTargets
TargetNotFound = executor_mod.TargetNotFound


class FakeResolver:
    """A single fixture area ("Bruchtal") with one light entity - just
    enough for every CONTRACT_SPELLS kind to resolve for real, never a
    real Home Assistant registry."""

    async def resolve(self, target, *, domain_filter):
        if target.value != "Bruchtal":
            return TargetNotFound()
        return ResolvedTargets((ResolvedEntity("light.bruchtal_lamp", "bruchtal"),))

    async def resolve_area(self, target):
        return "bruchtal" if target.value == "Bruchtal" else TargetNotFound()


class FakeCaller:
    """Mocked Home Assistant calls only - this test never performs a real
    device action, per this repo's own binding rule."""

    def __init__(self):
        self.light_calls = []

    async def set_light_power(self, entity_ids, on, *, area_id=None, context=None):
        self.light_calls.append((entity_ids, on))
        return True

    async def call_service(self, domain, service, entity_ids, data, *, area_id=None, context=None):
        self.light_calls.append((domain, service, entity_ids))
        return True

    async def get_entity_state(self, entity_id):
        return {"state": "on", "unit": None}

    async def skip_track(self, entity_ids, forward, *, area_id=None, context=None):
        return True

    async def clean_vacuum_area(self, area_id, *, context=None):
        raise AssertionError("contract fixtures never target a vacuum")


class ReferenceAdapterContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        book = contract.build_contract_book()
        executor = ActionExecutor(FakeResolver(), FakeCaller())
        policy = models.ScopedAreaPolicy(
            actions=models.AreaPolicy(allowed_areas=None, unrestricted=True),
            queries=models.AreaPolicy(allowed_areas=None, unrestricted=True),
        )
        self.adapter = ReferenceChatAdapter(book, executor, policy, language="en")

    async def test_every_contract_execution_kind_reaches_a_real_dispatch_outcome(self):
        async def dispatch(spell_id, captures):
            spell = next(s for s in contract.CONTRACT_SPELLS if s["id"] == spell_id)
            text = spell["trigger"].format(**captures) if captures else spell["trigger"]
            return await self.adapter.handle_message(text)

        def extract_status(reply):
            return reply.status or ("handled" if reply.handled else "")

        results = await contract.assert_every_execution_kind_is_dispatched(dispatch, extract_status=extract_status)
        # assist_redirect is a real, reported outcome here (this minimal
        # adapter deliberately doesn't support it - see the adapter's own
        # docstring) - never a silent gap and never the raw command text.
        self.assertEqual(results["contract_assist_redirect"], "assist_redirect_unsupported")
        self.assertEqual(results["contract_action"], "SUCCESS")
        self.assertEqual(results["contract_query"], "QUERY_ANSWER")
        self.assertEqual(results["contract_combined"], "QUERY_ANSWER")
        self.assertEqual(results["contract_text_answer"], "QUERY_ANSWER")

    async def test_action_reaches_the_real_mocked_service_call_never_a_real_device(self):
        reply = await self.adapter.handle_message("contract turn on the light in Bruchtal")
        self.assertTrue(reply.handled)
        self.assertEqual(reply.text, "Licht an.")

    async def test_assist_redirect_never_returns_the_raw_command_text(self):
        reply = await self.adapter.handle_message("contract sunrise over Bruchtal")
        self.assertIsNone(reply.text)
        self.assertEqual(reply.status, "assist_redirect_unsupported")

    async def test_unmatched_text_is_not_handled(self):
        reply = await self.adapter.handle_message("this matches nothing at all")
        self.assertFalse(reply.handled)


class ContractHelperCatchesAMissingDispatchBranchTests(unittest.IsolatedAsyncioTestCase):
    """Proves the property this whole contract exists for: a dispatch
    function with one execution kind silently missing (mirroring
    providers/code27/bridge.py's own real, historical query_plan gap -
    see _bridge_contract.py's module docstring) fails
    assert_every_execution_kind_is_dispatched, rather than passing
    quietly. This is the check that would have caught that bug on day
    one, not after a live user report."""

    async def test_a_dispatch_missing_the_query_plan_branch_fails_the_contract(self):
        async def broken_dispatch(spell_id, captures):
            # Deliberately mirrors the real historical bug: every kind
            # EXCEPT query_plan is handled; a flat multi-source query
            # falls through unrecognized.
            if spell_id == "contract_query":
                return None  # the missing branch - nothing ever handled this
            return "handled"

        with self.assertRaises(AssertionError) as caught:
            await contract.assert_every_execution_kind_is_dispatched(broken_dispatch, extract_status=lambda o: o or "")
        self.assertIn("contract_query", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
