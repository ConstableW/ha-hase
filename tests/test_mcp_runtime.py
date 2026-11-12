"""BridgeRuntimeSupervisor regression tests for the stateless MCP model."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path

MCP_DIR = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hase"
    / "providers"
    / "mcp"
)
PACKAGE = "hase_mcp_runtime_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(MCP_DIR)]
sys.modules.setdefault(PACKAGE, package)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", MCP_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


credentials = _load("credentials")
runtime = _load("runtime")
BridgeIdentity = credentials.BridgeIdentity
BridgeConfig = runtime.BridgeConfig
BridgeRuntimeState = runtime.BridgeRuntimeState
BridgeRuntimeSupervisor = runtime.BridgeRuntimeSupervisor
BridgeRuntimeUnavailable = runtime.BridgeRuntimeUnavailable
RequestPhase = runtime.RequestPhase
UpdateReason = runtime.UpdateReason


class BridgeRuntimeSupervisorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.supervisor = BridgeRuntimeSupervisor()
        self.a = BridgeIdentity("server", "a")
        self.b = BridgeIdentity("server", "b")
        self.config_a = BridgeConfig.from_values("A", {"book": "one"})
        self.config_b = BridgeConfig.from_values("B", {"book": "two"})

    def register_a(self, verifier: str = "credential-a") -> None:
        self.supervisor.register_bridge(self.a, self.config_a, "context-a", verifier)

    async def lease_worker(
        self,
        bridge_id,
        generation,
        ready: asyncio.Event,
        release: asyncio.Event,
        result: dict,
        *,
        commit: bool = False,
    ) -> None:
        lease = await self.supervisor.resolve_for_request(bridge_id, generation, "request")
        result["lease"] = lease
        if commit:
            lease.request.commit_side_effect()
        ready.set()
        try:
            await release.wait()
            result["completed"] = True
        except asyncio.CancelledError:
            result["cancelled"] = True
        finally:
            await self.supervisor.unregister_in_flight(lease)

    # 1
    async def test_register_bridge_publishes_generation_one(self):
        self.register_a()
        self.assertEqual(self.supervisor.get_generation(self.a), 1)
        self.assertEqual(self.supervisor.get_snapshot(self.a).config, self.config_a)

    # 2
    async def test_duplicate_registration_is_rejected(self):
        self.register_a()
        with self.assertRaises(ValueError):
            self.register_a()

    # 3
    async def test_config_update_bumps_generation_and_replaces_snapshot(self):
        self.register_a()
        changed = BridgeConfig.from_values("A2", {"book": "three"})
        await self.supervisor.update_bridge(self.a, changed, "context-2", "credential-a")
        self.assertEqual(self.supervisor.get_generation(self.a), 2)
        self.assertEqual(self.supervisor.get_snapshot(self.a).config, changed)

    # 4
    async def test_same_config_update_is_documented_no_op(self):
        self.register_a()
        await self.supervisor.update_bridge(
            self.a, self.config_a, "context-a", "credential-a"
        )
        self.assertEqual(self.supervisor.get_generation(self.a), 1)

    # 5
    async def test_updating_a_does_not_change_b(self):
        self.register_a()
        self.supervisor.register_bridge(self.b, self.config_b, "context-b", "credential-b")
        before = self.supervisor.get_snapshot(self.b)
        await self.supervisor.update_bridge(
            self.a, BridgeConfig.from_values("A2", {}), None, "credential-a"
        )
        self.assertIs(self.supervisor.get_snapshot(self.b), before)

    # 6
    async def test_credential_rotation_rejects_old_and_accepts_new(self):
        self.register_a("old")
        await self.supervisor.update_bridge(
            self.a,
            self.config_a,
            "context-a",
            "new",
            UpdateReason.CREDENTIALS_CHANGED,
        )
        self.assertIsNone(self.supervisor.resolve_credential("old"))
        self.assertEqual(self.supervisor.resolve_credential("new")[0].generation, 2)

    # 7
    async def test_credential_rotation_preserves_peer_credential(self):
        self.register_a("old")
        self.supervisor.register_bridge(self.b, self.config_b, None, "peer")
        await self.supervisor.update_bridge(self.a, self.config_a, "context-a", "new")
        self.assertEqual(self.supervisor.resolve_credential("peer")[0].bridge_id, self.b)

    # 8
    async def test_parallel_requests_same_bridge_are_not_serialized(self):
        self.register_a()
        first = await self.supervisor.resolve_for_request(self.a, 1, "same")
        second = await self.supervisor.resolve_for_request(self.a, 1, "same")
        self.assertNotEqual(first.request.request_id, second.request.request_id)
        await self.supervisor.unregister_in_flight(first)
        await self.supervisor.unregister_in_flight(second)

    # 9
    async def test_parallel_requests_different_bridges_are_independent(self):
        self.register_a()
        self.supervisor.register_bridge(self.b, self.config_b, None, "credential-b")
        a_lease, b_lease = await asyncio.gather(
            self.supervisor.resolve_for_request(self.a, 1),
            self.supervisor.resolve_for_request(self.b, 1),
        )
        self.assertEqual((a_lease.snapshot.bridge_id, b_lease.snapshot.bridge_id), (self.a, self.b))

    # 10
    async def test_old_generation_cannot_publish(self):
        self.register_a()
        lease = await self.supervisor.resolve_for_request(self.a, 1)
        lease.request.commit_side_effect()
        await self.supervisor.update_bridge(
            self.a, BridgeConfig.from_values("A2", {}), None, "credential-a"
        )
        self.assertFalse(await self.supervisor.may_publish(lease))

    # 11
    async def test_pre_side_effect_request_is_cancelled_on_update(self):
        self.register_a()
        ready, release, result = asyncio.Event(), asyncio.Event(), {}
        task = asyncio.create_task(self.lease_worker(self.a, 1, ready, release, result))
        await ready.wait()
        await self.supervisor.update_bridge(
            self.a, BridgeConfig.from_values("A2", {}), None, "credential-a"
        )
        await task
        self.assertTrue(result.get("cancelled"))

    # 12
    async def test_post_side_effect_request_is_not_hard_cancelled(self):
        self.register_a()
        ready, release, result = asyncio.Event(), asyncio.Event(), {}
        task = asyncio.create_task(
            self.lease_worker(self.a, 1, ready, release, result, commit=True)
        )
        await ready.wait()
        await self.supervisor.update_bridge(
            self.a, BridgeConfig.from_values("A2", {}), None, "credential-a"
        )
        release.set()
        await task
        self.assertTrue(result.get("completed"))
        self.assertFalse(await self.supervisor.may_publish(result["lease"]))

    # 13
    async def test_rapid_updates_leave_only_last_generation(self):
        self.register_a()
        for number in range(2, 7):
            await self.supervisor.update_bridge(
                self.a, BridgeConfig.from_values(f"A{number}", {"n": number}), None, "credential-a"
            )
        self.assertEqual(self.supervisor.get_generation(self.a), 6)
        self.assertEqual(self.supervisor.get_snapshot(self.a).config.title, "A6")

    # 14
    async def test_unregister_in_flight_is_idempotent(self):
        self.register_a()
        lease = await self.supervisor.resolve_for_request(self.a, 1)
        await self.supervisor.unregister_in_flight(lease)
        await self.supervisor.unregister_in_flight(lease)

    # 15
    async def test_request_cancel_is_idempotent(self):
        self.register_a()
        lease = await self.supervisor.resolve_for_request(self.a, 1)
        lease.request.commit_side_effect()
        lease.request.cancel()
        lease.request.cancel()
        self.assertTrue(lease.request.cancel_requested)

    # 16
    async def test_concurrent_generation_bumps_have_one_current_snapshot(self):
        self.register_a()
        await asyncio.gather(
            self.supervisor.update_bridge(
                self.a, BridgeConfig.from_values("A2", {"n": 2}), None, "credential-a"
            ),
            self.supervisor.update_bridge(
                self.a, BridgeConfig.from_values("A3", {"n": 3}), None, "credential-a"
            ),
        )
        snapshot = self.supervisor.get_snapshot(self.a)
        self.assertEqual(snapshot.generation, 3)
        self.assertIn(snapshot.config.title, {"A2", "A3"})

    # 17
    async def test_remove_unpublishes_bridge_and_credential(self):
        self.register_a()
        await self.supervisor.remove_bridge(self.a)
        self.assertIsNone(self.supervisor.get_snapshot(self.a))
        self.assertIsNone(self.supervisor.resolve_credential("credential-a"))

    # 18
    async def test_wrong_authenticated_generation_fails_fast(self):
        self.register_a()
        with self.assertRaises(BridgeRuntimeUnavailable):
            await self.supervisor.resolve_for_request(self.a, 99)

    # 19
    async def test_invalidated_bridge_fails_fast(self):
        self.register_a()
        self.supervisor._runtimes[self.a].state = BridgeRuntimeState.INVALIDATED
        with self.assertRaises(BridgeRuntimeUnavailable):
            await self.supervisor.resolve_for_request(self.a, 1)

    # 20
    async def test_unregistered_bridge_fails_fast(self):
        with self.assertRaises(BridgeRuntimeUnavailable):
            await self.supervisor.resolve_for_request(self.a, 1)

    # 21
    async def test_credential_binding_and_snapshot_share_generation(self):
        self.register_a()
        binding, snapshot = self.supervisor.resolve_credential("credential-a")
        self.assertEqual(binding.generation, snapshot.generation)

    # 22
    async def test_publication_and_nested_config_are_immutable(self):
        source = {"areas": ["one"]}
        config = BridgeConfig.from_values("A", source)
        self.supervisor.register_bridge(self.a, config, None, "")
        source["areas"].append("two")
        self.assertEqual(config.data["areas"], ("one",))
        with self.assertRaises(TypeError):
            self.supervisor._publication.bridges[self.b] = self.supervisor.get_snapshot(self.a)

    # 23
    async def test_duplicate_external_request_ids_get_unique_ownership(self):
        self.register_a()
        first = await self.supervisor.resolve_for_request(self.a, 1, "external")
        second = await self.supervisor.resolve_for_request(self.a, 1, "external")
        self.assertNotEqual(first.request.request_id, second.request.request_id)

    # 24
    async def test_remove_cancels_pre_side_effect_request(self):
        self.register_a()
        ready, release, result = asyncio.Event(), asyncio.Event(), {}
        task = asyncio.create_task(self.lease_worker(self.a, 1, ready, release, result))
        await ready.wait()
        await self.supervisor.remove_bridge(self.a)
        await task
        self.assertTrue(result.get("cancelled"))

    # 25
    async def test_remove_does_not_cancel_committed_side_effect(self):
        self.register_a()
        ready, release, result = asyncio.Event(), asyncio.Event(), {}
        task = asyncio.create_task(
            self.lease_worker(self.a, 1, ready, release, result, commit=True)
        )
        await ready.wait()
        await self.supervisor.remove_bridge(self.a)
        release.set()
        await task
        self.assertTrue(result.get("completed"))

    # 26
    async def test_side_effect_phase_only_moves_forward(self):
        self.register_a()
        lease = await self.supervisor.resolve_for_request(self.a, 1)
        lease.request.commit_side_effect()
        lease.request.commit_side_effect()
        self.assertIs(lease.request.phase, RequestPhase.POST_SIDE_EFFECT)

    # 27
    async def test_bridge_ids_drop_only_removed_bridge(self):
        self.register_a()
        self.supervisor.register_bridge(self.b, self.config_b, None, "credential-b")
        await self.supervisor.remove_bridge(self.a)
        self.assertEqual(self.supervisor.bridge_ids(), (self.b,))

    # 28
    async def test_current_generation_may_publish(self):
        self.register_a()
        lease = await self.supervisor.resolve_for_request(self.a, 1)
        self.assertTrue(await self.supervisor.may_publish(lease))


if __name__ == "__main__":
    unittest.main()
