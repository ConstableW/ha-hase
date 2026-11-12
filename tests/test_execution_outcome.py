"""Evidence boundaries for batched HA actions and bounded diagnostics."""
import unittest

import test_core_engine  # noqa: F401 - initializes the pure-core package alias
from hase_spellbook_core.executor import (
    ActionExecutor,
    ResolvedEntity,
    ResolvedTargets,
    ServiceCallOutcome,
    response_text_for_failure,
)
from hase_spellbook_core.models import (
    ActionDraft,
    ActionStep,
    ActionTarget,
    AreaPolicy,
    ExecutionStatus,
    ScopedAreaPolicy,
    StepPlan,
)


def policy():
    unrestricted = AreaPolicy(allowed_areas=None, unrestricted=True)
    return ScopedAreaPolicy(actions=unrestricted, queries=unrestricted)


class Resolver:
    def __init__(self, ids):
        self.ids = ids

    async def resolve(self, target, *, domain_filter):
        return ResolvedTargets(tuple(ResolvedEntity(entity_id, "room") for entity_id in self.ids))


class Caller:
    def __init__(self, outcome, states=None):
        self.outcome = outcome
        self.states = states or {}
        self.calls = []

    async def set_light_power(self, entity_ids, on, *, context=None):
        self.calls.append(entity_ids)
        return self.outcome

    async def call_service(self, domain, service, entity_ids, data, *, area_id=None, context=None):
        self.calls.append(entity_ids)
        return self.outcome

    async def get_entity_state(self, entity_id):
        return self.states.get(entity_id)


class BatchOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_batch_acceptance_for_all_executable_targets(self):
        caller = Caller(ServiceCallOutcome(True))
        result = await ActionExecutor(Resolver(("light.a", "light.b")), caller).execute(
            ActionDraft("light.set_power", ActionTarget("all_allowed"), {"on": True}), policy()
        )
        self.assertEqual(caller.calls, [("light.a", "light.b")])
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.data["outcome_certainty"], "service_accepted")

    async def test_known_unavailable_is_excluded_before_single_call(self):
        # HA adapter resolution has its own unavailable-state tests. Its
        # output here contains only executable targets; no extra calls.
        caller = Caller(ServiceCallOutcome(True))
        result = await ActionExecutor(Resolver(("light.reachable",)), caller).execute(
            ActionDraft("light.set_power", ActionTarget("all_allowed"), {"on": True}), policy()
        )
        self.assertEqual(caller.calls, [("light.reachable",)])
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)

    async def test_error_for_attempted_target_remains_uncertain(self):
        caller = Caller(ServiceCallOutcome(False, "TimeoutError", "sensitive low-level details"))
        result = await ActionExecutor(Resolver(("light.a", "light.b")), caller).execute(
            ActionDraft("light.set_power", ActionTarget("all_allowed"), {"on": True}), policy()
        )
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(result.data["attempted_entity_ids"], ("light.a", "light.b"))
        self.assertEqual(result.data["outcome_certainty"], "unknown")
        self.assertNotIn("sensitive", response_text_for_failure(result))
        self.assertNotIn("2", response_text_for_failure(result))

    async def test_legacy_boolean_rejection_does_not_invent_cause(self):
        caller = Caller(False)
        result = await ActionExecutor(Resolver(("light.a",)), caller).execute(
            ActionDraft("light.set_power", ActionTarget("all_allowed"), {"on": True}), policy()
        )
        self.assertEqual(result.data["outcome_certainty"], "unknown")
        self.assertNotIn("error_type", result.data)

    async def test_immediate_state_lag_is_observation_not_failure_count(self):
        caller = Caller(ServiceCallOutcome(True), {"switch.a": {"state": "off", "entity_name": "A"}})
        result = await ActionExecutor(Resolver(("switch.a",)), caller).execute_steps(
            StepPlan(steps=(ActionStep("x", "switch.turn_on", ActionTarget("all_allowed")),), response_template=None), policy()
        )
        self.assertEqual(result.reason_code, "ok")
        self.assertIsNone(result.data["variables"]["x"]["success_count"])
        self.assertIsNone(result.data["variables"]["x"]["failed_count"])
        self.assertEqual(result.data["steps"][0]["observation"]["other_count"], 1)


if __name__ == "__main__":
    unittest.main()
