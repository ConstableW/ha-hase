"""HASE MCP architecture, Phase 2: core.mcp_execution table-driven tests.

Reuses test_core_engine's package loader (hase_spellbook_core already
registered in sys.modules by importing it) so core.mcp_execution/
core.mcp_projection load as plain HA-free modules, same convention as
test_mcp_projection.py and test_combined_spells.py.
"""
from __future__ import annotations

import importlib
import unittest
from pathlib import Path

import test_core_engine as core

spellbook_engine = core.spellbook_engine
ActionExecutor = core.ActionExecutor
ResolvedEntity = core.ResolvedEntity
ResolvedTargets = core.ResolvedTargets
TargetNotFound = core.TargetNotFound
VacuumCleanOutcome = core.VacuumCleanOutcome
AreaPolicy = core.AreaPolicy
ScopedAreaPolicy = core.ScopedAreaPolicy
ExecutionStatus = core.ExecutionStatus

mcp_projection = importlib.import_module("hase_spellbook_core.mcp_projection")
mcp_execution = importlib.import_module("hase_spellbook_core.mcp_execution")

BridgeContext = mcp_projection.BridgeContext
McpExecutionResult = mcp_execution.McpExecutionResult
execute_mcp_tool_call = mcp_execution.execute_mcp_tool_call
find_command_by_id = spellbook_engine.find_command_by_id


def book(*commands, book_id: str = "test"):
    raw = {"id": book_id, "name": "Test", "commands": list(commands)}
    loaded = spellbook_engine.load_spellbook_from_dict(book_id, raw)
    assert loaded is not None, "test fixture spellbook failed to load"
    return loaded


def policy(allowed=(), unrestricted=False) -> AreaPolicy:
    return AreaPolicy(allowed_areas=frozenset(allowed), unrestricted=unrestricted)


def scoped(actions: AreaPolicy, queries: AreaPolicy | None = None) -> ScopedAreaPolicy:
    return ScopedAreaPolicy(actions=actions, queries=queries or actions)


class FakeResolver:
    """entities: {target_value: (entity_id, area_id)}. A value ABSENT from
    this table resolves to TargetNotFound - simulating "not exposed to
    Assist" through the exact same TargetResolver boundary the real
    HaTargetResolver applies for real (see ExposureBoundaryTests)."""

    def __init__(self, entities: dict[str, tuple[str, str | None]] | None = None, areas: dict[str, str] | None = None):
        self._entities = entities or {}
        self._areas = areas or {}

    async def resolve(self, target, *, domain_filter):
        entry = self._entities.get(target.value)
        if entry is None:
            return TargetNotFound()
        entity_id, area_id = entry
        return ResolvedTargets((ResolvedEntity(entity_id, area_id),))

    async def resolve_area(self, target):
        return self._areas.get(target.value, TargetNotFound())


class FakeCaller:
    def __init__(self, *, accept=True, states: dict[str, dict] | None = None, vacuum_outcome=None):
        self._accept = accept
        self._states = states or {}
        self._vacuum_outcome = vacuum_outcome or VacuumCleanOutcome(status="started", vacuum_entity_id="vacuum.x", room_name="x")
        self.light_calls: list[tuple] = []
        self.service_calls: list[tuple] = []
        self.vacuum_calls: list[str] = []
        self.contexts: list = []

    async def set_light_power(self, entity_ids, on, *, area_id=None, context=None):
        self.light_calls.append((entity_ids, on, area_id))
        self.contexts.append(context)
        return self._accept

    async def call_service(self, domain, service, entity_ids, data, *, area_id=None, context=None):
        self.service_calls.append((domain, service, entity_ids, dict(data), area_id))
        self.contexts.append(context)
        return self._accept

    async def get_entity_state(self, entity_id):
        return self._states.get(entity_id)

    async def skip_track(self, entity_ids, forward, *, area_id=None, context=None):
        self.service_calls.append(("media_player", "skip", entity_ids, {"forward": forward}, area_id))
        return self._accept

    async def clean_vacuum_area(self, area_id, *, context=None):
        self.vacuum_calls.append(area_id)
        self.contexts.append(context)
        return self._vacuum_outcome


LUMOS = {
    "id": "lumos",
    "trigger": "lumos {area}",
    "operation": "light.set_power",
    "target": {"kind": "area_name", "capture": "area"},
    "arguments": {"on": True},
    "catchphrase": "Lumos {area}.",
}


def make_area(name: str, area_id: str | None = None):
    """A real Home Assistant area's id and display name are usually
    DIFFERENT strings (Phase 3's binding correction); this derives a
    plausible id from the name only so fixtures stay short."""
    return mcp_projection.BridgeArea(area_id=area_id or name.lower().replace(" ", "_"), area_name=name)


def bridge_a(area="Jans Zimmer") -> BridgeContext:
    a = make_area(area) if isinstance(area, str) else area
    return BridgeContext(area=a, policy=scoped(policy(allowed=[a.area_id])), language="de")


def bridge_b() -> BridgeContext:
    return BridgeContext(area=None, policy=scoped(policy(unrestricted=True)), language="de")


def bridge_c(area="Wohnzimmer") -> BridgeContext:
    a = make_area(area) if isinstance(area, str) else area
    return BridgeContext(area=a, policy=scoped(policy(unrestricted=True)), language="de")


class FindCommandByIdTests(unittest.TestCase):
    def test_exact_lookup_finds_the_command(self):
        b = book(LUMOS, {**LUMOS, "id": "nox", "trigger": "nox {area}", "arguments": {"on": False}})
        found = find_command_by_id(b, "nox")
        self.assertIsNotNone(found)
        self.assertEqual(found.spell_id, "nox")
        self.assertFalse(found.target_spec == {})  # sanity: a real command, not a stub

    def test_unknown_id_returns_none(self):
        b = book(LUMOS)
        self.assertIsNone(find_command_by_id(b, "does_not_exist"))

    def test_never_expands_aliases_into_separate_ids(self):
        with_alias = {**LUMOS, "triggers": ["licht an {area}"]}
        b = book(with_alias)
        self.assertIsNone(find_command_by_id(b, "licht an {area}"))
        self.assertIsNotNone(find_command_by_id(b, "lumos"))

    def test_duplicate_spell_id_never_arbitrarily_resolves_one(self):
        first = {**LUMOS, "id": "dup"}
        second = {**LUMOS, "id": "dup", "trigger": "nox {area}", "arguments": {"on": False}}
        b = book(first, second)
        self.assertIsNone(find_command_by_id(b, "dup"))


class UnknownToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_spell_id(self):
        b = book(LUMOS)
        executor = ActionExecutor(FakeResolver(), FakeCaller())
        result = await execute_mcp_tool_call(b, "does_not_exist", {}, bridge_a(), executor)
        self.assertIsNone(result.status)
        self.assertEqual(result.detail, "unknown_tool:does_not_exist")

    async def test_duplicate_spell_id_refuses_execution_rather_than_guessing(self):
        first = {**LUMOS, "id": "dup"}
        second = {**LUMOS, "id": "dup", "trigger": "nox {area}", "arguments": {"on": False}}
        b = book(first, second)
        executor = ActionExecutor(FakeResolver(), FakeCaller())
        result = await execute_mcp_tool_call(b, "dup", {}, bridge_a(), executor)
        self.assertIsNone(result.status)
        self.assertEqual(result.detail, "unknown_tool:dup")
        self.assertIsNone(result.response_text)

    async def test_disabled_spell_is_unknown_even_though_find_command_by_id_would_find_it(self):
        b = book({**LUMOS, "enabled": False})
        self.assertIsNotNone(find_command_by_id(b, "lumos"))  # the low-level lookup DOES find it
        executor = ActionExecutor(FakeResolver(), FakeCaller())
        result = await execute_mcp_tool_call(b, "lumos", {}, bridge_a(), executor)
        self.assertIsNone(result.status)
        self.assertEqual(result.detail, "unknown_tool:lumos")

    async def test_unprojectable_tool_reason_is_propagated(self):
        b = book({
            "id": "typo_op", "trigger": "does something",
            "operation": "light.set_powerr", "target": {"kind": "all_allowed"}, "arguments": {},
        })
        executor = ActionExecutor(FakeResolver(), FakeCaller())
        result = await execute_mcp_tool_call(b, "typo_op", {}, bridge_a(), executor)
        self.assertIsNone(result.status)
        self.assertTrue(result.detail.startswith("unprojectable_tool:unknown_operation:"))


class BadArgumentsTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_required_argument_propagated_cleanly(self):
        b = book(LUMOS)
        executor = ActionExecutor(FakeResolver(), FakeCaller())
        result = await execute_mcp_tool_call(b, "lumos", {}, bridge_b(), executor)  # CASE B: area required, not given
        self.assertIsNone(result.status)
        self.assertEqual(result.detail, "missing_required_argument")
        self.assertIsNone(result.response_text)
        self.assertIsNone(result.execution_result)

    async def test_out_of_range_argument_is_rejected_by_the_real_engine_not_silently_accepted(self):
        command = {
            "id": "raise_blind", "trigger": "blind {area} to {value} percent",
            "operation": "cover.set_cover_position",
            "target": {"kind": "area_name", "capture": "area"},
            "arguments": {"position": {"capture": "value", "type": "int", "min": 0, "max": 100}},
        }
        b = book(command)
        resolver = FakeResolver({"Buero": ("cover.buero", "buero")})
        executor = ActionExecutor(resolver, FakeCaller())
        # 999 clears the MCP schema's own advisory `maximum: 100`, so this
        # proves the REAL engine-side range check (not just the JSON
        # Schema hint) is what actually gates it.
        result = await execute_mcp_tool_call(
            b, "raise_blind", {"area": "Buero", "value": 999}, bridge_a(area="Buero"), executor,
        )
        self.assertIsNone(result.status)
        self.assertEqual(result.detail, "unresolvable_action_draft")


class ActionSpellTests(unittest.IsolatedAsyncioTestCase):
    async def test_case_a_implicit_bridge_area_executes_for_real(self):
        b = book(LUMOS)
        # The spoken/injected value is the area's NAME ("Jans Zimmer" -
        # CASE A injects bridge.area.area_name into this area_name-kind
        # capture), but AreaPolicy checks the RESOLVED entity's area_id
        # ("jans_zimmer", matching bridge_a()'s own policy.allowed_areas) -
        # the two are never the same field, by design (Phase 3).
        resolver = FakeResolver({"Jans Zimmer": ("light.jan", "jans_zimmer")})
        caller = FakeCaller()
        executor = ActionExecutor(resolver, caller)
        result = await execute_mcp_tool_call(b, "lumos", {}, bridge_a(area="Jans Zimmer"), executor)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.response_text, "Lumos Jans Zimmer.")  # rendered action response (catchphrase)
        self.assertEqual(caller.light_calls, [(("light.jan",), True, None)])

    async def test_case_b_explicit_target_executes_for_real(self):
        b = book(LUMOS)
        resolver = FakeResolver({"Kinderzimmer Jan": ("light.kinder", "kinderzimmer")})
        caller = FakeCaller()
        executor = ActionExecutor(resolver, caller)
        result = await execute_mcp_tool_call(
            b, "lumos", {"area": "Kinderzimmer Jan"}, bridge_b(), executor,
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.response_text, "Lumos Kinderzimmer Jan.")
        self.assertEqual(caller.light_calls, [(("light.kinder",), True, None)])

    async def test_case_c_implicit_uses_bridge_area(self):
        b = book(LUMOS)
        resolver = FakeResolver({"Wohnzimmer": ("light.wz", "wohnzimmer")})
        caller = FakeCaller()
        executor = ActionExecutor(resolver, caller)
        result = await execute_mcp_tool_call(b, "lumos", {}, bridge_c(area="Wohnzimmer"), executor)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.light_calls, [(("light.wz",), True, None)])

    async def test_case_c_explicit_overrides_bridge_area(self):
        b = book(LUMOS)
        resolver = FakeResolver({"Wohnzimmer": ("light.wz", "wohnzimmer"), "Kinderzimmer Jan": ("light.kinder", "kinderzimmer")})
        caller = FakeCaller()
        executor = ActionExecutor(resolver, caller)
        result = await execute_mcp_tool_call(
            b, "lumos", {"area": "Kinderzimmer Jan"}, bridge_c(area="Wohnzimmer"), executor,
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.light_calls, [(("light.kinder",), True, None)])


class QuerySpellTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_spell_renders_response_with_variables(self):
        command = {
            "id": "avg_temp", "trigger": "average temperature in {area}",
            "sources": [
                {"kind": "avg", "target": {"kind": "area_name", "capture": "area"},
                 "domain_filter": "sensor", "device_class": "temperature", "variable": "t"},
            ],
            "output": "{t} in {area}",
        }
        b = book(command)
        resolver = FakeResolver({"Wohnzimmer": ("sensor.temp", "wohnzimmer")})
        caller = FakeCaller(states={"sensor.temp": {"state": "21.5", "unit": "°C", "device_class": "temperature"}})
        executor = ActionExecutor(resolver, caller)
        result = await execute_mcp_tool_call(b, "avg_temp", {}, bridge_c(area="Wohnzimmer"), executor)
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.response_text, "21.5 in Wohnzimmer")


class CombinedSpellTests(unittest.IsolatedAsyncioTestCase):
    async def test_combined_spell_executes_through_execute_steps_and_renders_variables(self):
        command = {
            "id": "clean_and_report", "trigger": "clean {area} and report",
            "steps": [
                # A post-action state re-read is an immediate observation,
                # never terminal proof of per-device success.
                {"alias": "cleaning", "operation": "light.turn_on", "target": {"kind": "area_name", "capture": "area"}},
                {"alias": "lights", "sources": [
                    {"kind": "count", "target": {"kind": "area_name", "capture": "area"},
                     "domain_filter": "light", "state_filter": "on", "variable": "n"},
                ]},
            ],
            "output": "{cleaning.success_count} erledigt, {lights.n} Lichter an.",
        }
        b = book(command)
        resolver = FakeResolver({"Wohnzimmer": ("light.wz", "wohnzimmer")}, areas={"Wohnzimmer": "wohnzimmer"})
        caller = FakeCaller(states={"light.wz": {"state": "on"}})
        executor = ActionExecutor(resolver, caller)
        result = await execute_mcp_tool_call(b, "clean_and_report", {}, bridge_c(area="Wohnzimmer"), executor)
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.response_text, "? erledigt, 1 Lichter an.")
        self.assertEqual(caller.service_calls, [("light", "turn_on", ("light.wz",), {}, None)])


class SourceAreaPropagationTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_area_target_uses_bridge_area_for_every_bridge_with_an_area(self):
        command = {
            "id": "clean_here", "trigger": "clean here",
            "operation": "vacuum.clean_area", "target": {"kind": "source_area"},
        }
        b = book(command)
        # Phase 3: source_area_id is now ALWAYS bridge.area.area_id, never
        # the display name - resolve_area receives the real area_id
        # directly, and policy.allowed_areas is keyed on that same id.
        buero = make_area("Buero", area_id="buero")
        resolver = FakeResolver(areas={"buero": "buero"})
        caller = FakeCaller(vacuum_outcome=VacuumCleanOutcome(status="started", vacuum_entity_id="vacuum.b", room_name="Buero"))
        executor = ActionExecutor(resolver, caller)
        bridge = BridgeContext(area=buero, policy=scoped(policy(allowed=["buero"])), language="de")
        result = await execute_mcp_tool_call(b, "clean_here", {}, bridge, executor)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.vacuum_calls, ["buero"])

    async def test_source_area_target_fails_closed_for_a_bridge_with_no_area(self):
        command = {
            "id": "clean_here", "trigger": "clean here",
            "operation": "vacuum.clean_area", "target": {"kind": "source_area"},
        }
        b = book(command)
        executor = ActionExecutor(FakeResolver(), FakeCaller())
        result = await execute_mcp_tool_call(b, "clean_here", {}, bridge_b(), executor)
        self.assertEqual(result.status, ExecutionStatus.INVALID)
        self.assertEqual(result.execution_result.reason_code, "no_source_area")


class PolicyDenialTests(unittest.IsolatedAsyncioTestCase):
    async def test_action_policy_denies_an_explicit_out_of_scope_target(self):
        b = book(LUMOS)
        resolver = FakeResolver({"Buero": ("light.buero", "buero")})
        executor = ActionExecutor(resolver, FakeCaller())
        # Own area Wohnzimmer, but action scope is Wohnzimmer+Kinderzimmer
        # only - Buero is explicitly out of scope, proving the REAL
        # AreaPolicy still gates an explicit CASE-C value at execution
        # time, not just at projection time.
        wz = make_area("Wohnzimmer")
        kz = make_area("Kinderzimmer")
        bridge = BridgeContext(
            area=wz, policy=scoped(policy(allowed=[wz.area_id, kz.area_id])), language="de",
        )
        result = await execute_mcp_tool_call(b, "lumos", {"area": "Buero"}, bridge, executor)
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertEqual(result.execution_result.reason_code, "area_not_permitted")
        self.assertIsNone(result.response_text)

    async def test_query_policy_denies_independently_of_action_policy(self):
        command = {
            "id": "avg_temp", "trigger": "average temperature in {area}",
            "sources": [
                {"kind": "avg", "target": {"kind": "area_name", "capture": "area"},
                 "domain_filter": "sensor", "device_class": "temperature", "variable": "t"},
            ],
            "output": "{t}",
        }
        b = book(command)
        resolver = FakeResolver({"Buero": ("sensor.buero", "buero")})
        executor = ActionExecutor(resolver, FakeCaller())
        # actions unrestricted, queries CASE C (own area + one other, so
        # the argument stays optional/visible rather than CASE A/omitted)
        # locked to Wohnzimmer+Kinderzimmer only - proves the query view,
        # not the action view, gates a query-classified target, matching
        # core.executor's own action/query policy split.
        wz = make_area("Wohnzimmer")
        kz = make_area("Kinderzimmer")
        bridge = BridgeContext(
            area=wz,
            policy=scoped(actions=policy(unrestricted=True), queries=policy(allowed=[wz.area_id, kz.area_id])),
            language="de",
        )
        result = await execute_mcp_tool_call(b, "avg_temp", {"area": "Buero"}, bridge, executor)
        self.assertEqual(result.status, ExecutionStatus.DENIED)


class ExposureBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_target_absent_from_resolver_table_fails_exactly_like_any_other_frontend(self):
        # FakeResolver's table stands in for HaTargetResolver's own
        # async_should_expose(...) filtering: an entity simply never
        # appearing in what the resolver returns IS how "not exposed to
        # Assist" already looks to core.executor, for every frontend
        # alike - MCP goes through the identical boundary, not a bypass.
        b = book(LUMOS)
        resolver = FakeResolver({})  # "Jans Zimmer" deliberately absent - not exposed
        executor = ActionExecutor(resolver, FakeCaller())
        result = await execute_mcp_tool_call(b, "lumos", {}, bridge_a(area="Jans Zimmer"), executor)
        self.assertEqual(result.status, ExecutionStatus.INVALID)
        self.assertIsNone(result.response_text)


class ExpertGatingTests(unittest.IsolatedAsyncioTestCase):
    def _book(self):
        return book({
            "id": "lock_door", "trigger": "lock {area}",
            "operation": "lock.lock",  # expert_only in the operation catalog
            "target": {"kind": "area_name", "capture": "area"},
        })

    async def test_expert_disabled_denies(self):
        b = self._book()
        resolver = FakeResolver({"Buero": ("lock.buero", "buero")})
        executor = ActionExecutor(resolver, FakeCaller(), expert_allowed=lambda: False)
        result = await execute_mcp_tool_call(b, "lock_door", {"area": "Buero"}, bridge_b(), executor)
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertEqual(result.execution_result.reason_code, "expert_disabled")

    async def test_expert_enabled_executes(self):
        b = self._book()
        resolver = FakeResolver({"Buero": ("lock.buero", "buero")})
        caller = FakeCaller()
        executor = ActionExecutor(resolver, caller, expert_allowed=lambda: True)
        result = await execute_mcp_tool_call(b, "lock_door", {"area": "Buero"}, bridge_b(), executor)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls, [("lock", "lock", ("lock.buero",), {}, None)])


class PortedDualTierCommandTests(unittest.IsolatedAsyncioTestCase):
    """A "ported" Spell - one authored with BOTH a plain user-tier
    definition and a separate `expert:` variant (core.spellbook_engine.
    build_draft's own dual-tier handling) - is a DIFFERENT mechanism from
    a single operation hard-gated via OPERATION_CATALOG's own
    expert_only=True (see ExpertGatingTests above): calling it through MCP
    with Expert disabled must transparently execute the safe user-tier
    fallback rather than being denied outright, and with Expert enabled
    must execute the expert-tier variant instead - core.mcp_execution
    reaches this through the exact same build_draft/ActionExecutor path
    every other frontend uses, never a bypass."""

    def _book(self):
        return book({
            "id": "ported_light", "trigger": "ported light",
            "operation": "light.set_power",
            "target": {"kind": "entity_id", "value": "light.x"},
            "arguments": {"on": True},
            "expert": {
                "operation": "light.set_power",
                "target": {"kind": "entity_id", "value": "light.x"},
                "arguments": {"on": False},
            },
        })

    async def test_projection_reflects_only_the_user_tier_shape(self):
        # The expert variant targets the SAME fixed entity here, so this
        # mainly proves project_command never even looks at
        # command.expert_command - both projections would differ if it did.
        result = mcp_projection.project_command(self._book().commands[0], bridge_a())
        self.assertIsInstance(result, mcp_projection.McpToolProjection)
        self.assertEqual(result.input_schema, {"type": "object", "properties": {}})

    async def test_expert_disabled_transparently_falls_back_to_the_user_tier(self):
        b = self._book()
        resolver = FakeResolver({"light.x": ("light.x", None)})
        caller = FakeCaller()
        executor = ActionExecutor(resolver, caller, expert_allowed=lambda: False)
        result = await execute_mcp_tool_call(b, "ported_light", {}, bridge_b(), executor)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.light_calls, [(("light.x",), True, None)])
        self.assertTrue(result.execution_result.data.get("user_fallback"))

    async def test_expert_enabled_executes_the_expert_tier(self):
        b = self._book()
        resolver = FakeResolver({"light.x": ("light.x", None)})
        caller = FakeCaller()
        executor = ActionExecutor(resolver, caller, expert_allowed=lambda: True)
        result = await execute_mcp_tool_call(b, "ported_light", {}, bridge_b(), executor)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.light_calls, [(("light.x",), False, None)])
        self.assertNotIn("user_fallback", result.execution_result.data)


class PoisonedResolver:
    """Raises on any use - proves a text-only Spell's execution never
    touches target resolution at all (see TextOnlySpellExecutionTests'
    own "no HA interaction" checks below)."""

    async def resolve(self, target, *, domain_filter):
        raise AssertionError("text-only Spell execution must never resolve a target")

    async def resolve_area(self, target):
        raise AssertionError("text-only Spell execution must never resolve an area")


class PoisonedCaller:
    """Raises on any use - the HA-service-call counterpart to
    PoisonedResolver."""

    async def set_light_power(self, *a, **k):
        raise AssertionError("text-only Spell execution must never call a HA service")

    async def call_service(self, *a, **k):
        raise AssertionError("text-only Spell execution must never call a HA service")

    async def get_entity_state(self, *a, **k):
        raise AssertionError("text-only Spell execution must never read HA state")

    async def skip_track(self, *a, **k):
        raise AssertionError("text-only Spell execution must never call a HA service")

    async def clean_vacuum_area(self, *a, **k):
        raise AssertionError("text-only Spell execution must never call a HA service")


class FakeAssistForwarder:
    """Records every command_text/language it was asked to forward -
    proves core.mcp_execution's assist_redirect branch actually calls it
    (or correctly never does), never a second, parallel execution path."""

    def __init__(
        self, speech: str | None = "Ich habe das erledigt.", is_error: bool = False,
        is_query_answer: bool = False, continue_conversation: bool = False,
    ):
        self.calls: list[tuple[str, str]] = []
        self._speech = speech
        self._is_error = is_error
        self._is_query_answer = is_query_answer
        self._continue_conversation = continue_conversation

    async def forward(self, command_text, *, language, context):
        self.calls.append((command_text, language))
        return mcp_execution.AssistForwardOutcome(
            speech=self._speech, is_error=self._is_error, is_query_answer=self._is_query_answer,
            continue_conversation=self._continue_conversation,
        )


class PoisonedAssistForwarder:
    """Raises on any use - proves a DENIED assist_redirect call never
    even reaches Home Assistant's conversation agent, the AssistForwarder
    counterpart to PoisonedResolver/PoisonedCaller above."""

    async def forward(self, *a, **k):
        raise AssertionError("a restricted Bridge's assist_redirect call must never reach the forwarder")


class TextOnlySpellExecutionTests(unittest.IsolatedAsyncioTestCase):
    """Pure free-text Spells with assist_redirect=False execute via
    core.spellbook_engine.build_text_response directly - never through
    core.executor.ActionExecutor at all, since there is no target/area to
    resolve or HA service to call. Reuses the exact projection every
    tools/list call already published (find_command_by_id ->
    project_command -> build_captures, unchanged) - the same
    "re-derive, never trust the caller's shape" pattern every other MCP
    tool call already goes through. assist_redirect=True Spells are a
    DIFFERENT execution kind (Home Assistant's own configured conversation
    agent actually performs the command) - see AssistRedirectExecutionTests
    below."""

    def _executor(self):
        return ActionExecutor(PoisonedResolver(), PoisonedCaller())

    async def test_plain_text_response_with_no_placeholders(self):
        b = book({
            "id": "joke", "trigger": "tell me a joke", "output": "Why did the chicken cross the road?",
            "assist_redirect": False,
        })
        result = await execute_mcp_tool_call(b, "joke", {}, bridge_a(), self._executor())
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.response_text, "Why did the chicken cross the road?")
        self.assertEqual(result.execution_result.reason_code, "text_response")

    async def test_placeholder_supplied_via_mcp_arguments_is_substituted(self):
        b = book({"id": "greet", "trigger": "hail {area}", "output": "Hail, {area}!", "assist_redirect": False})
        result = await execute_mcp_tool_call(b, "greet", {"area": "Bruchtal"}, bridge_a(), self._executor())
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.response_text, "Hail, Bruchtal!")

    async def test_missing_required_placeholder_is_rejected_before_any_rendering(self):
        b = book({"id": "greet", "trigger": "hail {area}", "output": "Hail, {area}!", "assist_redirect": False})
        result = await execute_mcp_tool_call(b, "greet", {}, bridge_a(), self._executor())
        self.assertIsNone(result.status)
        self.assertEqual(result.detail, "missing_required_argument")

    async def test_output_referencing_an_undeclared_placeholder_fails_safe_at_the_render_level(self):
        # core.spellbook_engine's own loader already rejects a Spell whose
        # `output` references a placeholder absent from its trigger (the
        # same validation match()'s own text-only rendering already
        # relies on) - so this specific failure can never actually reach
        # execute_mcp_tool_call through a validly-loaded book. Tested
        # directly against build_text_response instead, proving the
        # defensive `.format` failure path itself fails safe (returns
        # None, never raises) rather than asserting an unreachable
        # end-to-end case.
        b = book({"id": "greet", "trigger": "hail {area}", "output": "Hail, {area}!"})
        command = b.commands[0]
        self.assertIsNone(spellbook_engine.build_text_response(command, {}, "de"))

    async def test_language_fallback_still_executes(self):
        b = book({
            "id": "en_only", "trigger": "greet only", "output": {"en": "Hello there."},
            "assist_redirect": False,
        })
        de_master = BridgeContext(area=None, policy=scoped(policy(unrestricted=True)), language="de")
        result = await execute_mcp_tool_call(b, "en_only", {}, de_master, self._executor())
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.response_text, "Hello there.")

    async def test_disabled_text_only_command_is_unknown(self):
        b = book({"id": "joke", "trigger": "tell me a joke", "output": "Why...", "enabled": False})
        result = await execute_mcp_tool_call(b, "joke", {}, bridge_a(), self._executor())
        self.assertIsNone(result.status)
        self.assertEqual(result.detail, "unknown_tool:joke")

    async def test_never_touches_the_executor_at_all(self):
        # PoisonedResolver/PoisonedCaller raise on ANY method call - a
        # successful result here IS the proof that ActionExecutor was
        # never invoked for a text-only Spell.
        b = book({
            "id": "joke", "trigger": "tell me a joke", "output": "Why...", "assist_redirect": False,
        })
        result = await execute_mcp_tool_call(b, "joke", {}, bridge_a(), self._executor())
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)


class AssistRedirectExecutionTests(unittest.IsolatedAsyncioTestCase):
    """command.assist_redirect=True (models.DEFAULT_ASSIST_REDIRECT) hands
    `output` to Home Assistant's own configured conversation agent for
    REAL interpretation/execution - core.mcp_execution.
    _execute_assist_redirect, never a fabricated success and never the
    raw command text returned as if reading it were the same as
    performing it. Never touches core.executor.ActionExecutor
    (PoisonedResolver/PoisonedCaller prove that), goes through the
    injected AssistForwarder instead."""

    def _executor(self):
        return ActionExecutor(PoisonedResolver(), PoisonedCaller())

    async def test_unrestricted_bridge_actually_executes_via_the_forwarder(self):
        # The user's own explicit requirement: MCP must actually PERFORM
        # the configured function. bridge_b is unrestricted (mirrors
        # providers.code27.frontend._UNRESTRICTED_POLICY, the only other
        # caller of this mechanism today), so the forward genuinely
        # happens and its real reply becomes the tool's response text.
        b = book({"id": "sunrise", "trigger": "sunrise over {area}", "output": "Set light {area} to 100%."})
        self.assertTrue(b.commands[0].assist_redirect)
        forwarder = FakeAssistForwarder(speech="Licht Gondor ist jetzt hell.")
        result = await execute_mcp_tool_call(
            b, "sunrise", {"area": "Gondor"}, bridge_b(), self._executor(), assist_forwarder=forwarder,
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.response_text, "Licht Gondor ist jetzt hell.")
        self.assertEqual(forwarder.calls, [("Set light Gondor to 100%.", "de")])

    async def test_restricted_bridge_is_denied_before_ever_reaching_assist(self):
        # bridge_a is scoped to one area, not unrestricted (see
        # AreaPolicy.unrestricted's own docstring).
        # conversation.async_converse has no per-call area/entity scoping
        # of its own, so a restricted Bridge's own permissions must never
        # be bypassed by routing through Assist instead of a typed
        # operation - denied outright, and PoisonedAssistForwarder proves
        # this is checked BEFORE the forwarder is ever reached.
        b = book({"id": "sunrise", "trigger": "sunrise over {area}", "output": "Set light {area} to 100%."})
        result = await execute_mcp_tool_call(
            b, "sunrise", {"area": "Jans Zimmer"}, bridge_a(), self._executor(),
            assist_forwarder=PoisonedAssistForwarder(),
        )
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertEqual(result.execution_result.reason_code, "assist_redirect_requires_unrestricted_policy")

    async def test_a_transport_without_a_wired_forwarder_reports_it_not_silently(self):
        b = book({"id": "sunrise", "trigger": "sunrise over {area}", "output": "Set light {area} to 100%."})
        result = await execute_mcp_tool_call(b, "sunrise", {"area": "Gondor"}, bridge_b(), self._executor())
        self.assertIsNone(result.status)
        self.assertEqual(result.detail, "assist_redirect_unavailable")

    async def test_an_error_reply_from_assist_is_reported_as_failed_never_success(self):
        b = book({"id": "sunrise", "trigger": "sunrise over {area}", "output": "Set light {area} to 100%."})
        forwarder = FakeAssistForwarder(speech="Das habe ich nicht verstanden.", is_error=True)
        result = await execute_mcp_tool_call(
            b, "sunrise", {"area": "Gondor"}, bridge_b(), self._executor(), assist_forwarder=forwarder,
        )
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(result.execution_result.reason_code, "assist_error")

    async def test_a_confirmed_action_with_no_assist_speech_still_reports_success(self):
        # The user's own explicit requirement: a successfully executed
        # Spell without a configured/returned reply must stay SUCCESS,
        # never be downgraded to FAILED merely for having nothing to say
        # - a real action can complete silently, exactly like a typed
        # ActionDraft with no catchphrase already falls back to a generic
        # confirmation (_finish's own response_text_for_success).
        b = book({"id": "sunrise", "trigger": "sunrise over {area}", "output": "Set light {area} to 100%."})
        forwarder = FakeAssistForwarder(speech=None, is_error=False)
        result = await execute_mcp_tool_call(
            b, "sunrise", {"area": "Gondor"}, bridge_b(), self._executor(), assist_forwarder=forwarder,
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.response_text, "Erledigt.")

    async def test_confirmed_action_done_uses_the_spells_own_catchphrase_not_assists_raw_reply(self):
        # Mirrors providers.code27.bridge._forward_to_assist's own
        # substitution rule exactly: Assist's raw reply must never
        # unconditionally replace the Spell's own configured response -
        # for a genuinely confirmed (non-follow-up) completion, the
        # Spell's own catchphrase is what gets returned.
        b = book({
            "id": "sunrise", "trigger": "sunrise over {area}", "output": "Set light {area} to 100%.",
            "catchphrase": "Der neue Tag beginnt.",
        })
        forwarder = FakeAssistForwarder(speech="Home Assistant's own generic reply.")
        result = await execute_mcp_tool_call(
            b, "sunrise", {"area": "Gondor"}, bridge_b(), self._executor(), assist_forwarder=forwarder,
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.response_text, "Der neue Tag beginnt.")

    async def test_query_answer_never_substitutes_the_catchphrase_and_reports_query_answer(self):
        # Assist merely ANSWERING a question is a different outcome from
        # performing the Spell's action - the catchphrase (which implies
        # the action happened) must never stand in for it, and the
        # reported status must honestly reflect "answered", not "done".
        b = book({
            "id": "sunrise", "trigger": "sunrise over {area}", "output": "Set light {area} to 100%.",
            "catchphrase": "Der neue Tag beginnt.",
        })
        forwarder = FakeAssistForwarder(speech="It is currently daytime.", is_query_answer=True)
        result = await execute_mcp_tool_call(
            b, "sunrise", {"area": "Gondor"}, bridge_b(), self._executor(), assist_forwarder=forwarder,
        )
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.response_text, "It is currently daytime.")

    async def test_a_follow_up_question_never_gets_papered_over_by_the_catchphrase(self):
        # continue_conversation=True: the configured agent is asking a
        # follow-up, reported as ACTION_DONE too (HA has no distinct
        # response_type for this) - the catchphrase must not hide the
        # real question the caller needs to see, exactly like
        # _forward_to_assist's own exclusion.
        b = book({
            "id": "sunrise", "trigger": "sunrise over {area}", "output": "Set light {area} to 100%.",
            "catchphrase": "Der neue Tag beginnt.",
        })
        forwarder = FakeAssistForwarder(speech="Which light did you mean?", continue_conversation=True)
        result = await execute_mcp_tool_call(
            b, "sunrise", {"area": "Gondor"}, bridge_b(), self._executor(), assist_forwarder=forwarder,
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.response_text, "Which light did you mean?")


class NoSecondMatchingPassTests(unittest.IsolatedAsyncioTestCase):
    async def test_identical_trigger_text_never_confuses_which_spell_runs(self):
        # Two commands sharing the EXACT SAME trigger text but different
        # ids/operations - textually ambiguous for find_command()/match(),
        # utterly unambiguous by spell_id. Proves MCP execution resolves
        # by identity, never by re-matching text.
        twin_a = {"id": "twin_on", "trigger": "toggle {area}", "operation": "light.set_power",
                  "target": {"kind": "area_name", "capture": "area"}, "arguments": {"on": True}}
        twin_b = {"id": "twin_off", "trigger": "toggle {area}", "operation": "light.set_power",
                  "target": {"kind": "area_name", "capture": "area"}, "arguments": {"on": False}}
        b = book(twin_a, twin_b)
        resolver = FakeResolver({"Buero": ("light.buero", "buero")})
        caller = FakeCaller()
        executor = ActionExecutor(resolver, caller)
        result = await execute_mcp_tool_call(b, "twin_off", {"area": "Buero"}, bridge_b(), executor)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.light_calls, [(("light.buero",), False, None)])


class NoRouterImportTests(unittest.TestCase):
    def test_mcp_execution_module_never_imports_router_or_uses_text_matching(self):
        self.assertFalse(hasattr(mcp_execution, "SpellbookRouter"))
        self.assertFalse(hasattr(mcp_execution, "find_command"))
        self.assertFalse(hasattr(mcp_execution, "match"))
        source = importlib.import_module("hase_spellbook_core.mcp_execution").__file__
        text = Path(source).read_text(encoding="utf-8")
        self.assertNotIn("from .router", text)
        self.assertNotIn("import router", text)


if __name__ == "__main__":
    unittest.main()
