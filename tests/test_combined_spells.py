"""Combined Spells: one spell composed of multiple actions and queries,
aliased and merged into one response (core/models.py's ActionStep/
QueryStep/StepPlan, core/spellbook_engine.py's `steps` loading/parsing,
core/executor.py's ActionExecutor.execute_steps). Also covers the new
"names"/"states" QuerySourceSpec aggregate kinds and the _KeepMissingFields
dotted-placeholder fix, both of which are usable by the pre-existing flat
`sources:` field too, not just `steps`.

Reuses test_core_engine's package loader/FakeResolver, same convention as
test_query_plan.py.
"""
import unittest

import test_core_engine as core

ActionExecutor = core.ActionExecutor
ActionTarget = core.ActionTarget
AreaPolicy = core.AreaPolicy
ScopedAreaPolicy = core.ScopedAreaPolicy
ExecutionStatus = core.ExecutionStatus
ResolvedEntity = core.ResolvedEntity
ResolvedTargets = core.ResolvedTargets
TargetNotFound = core.TargetNotFound
FakeResolver = core.FakeResolver
spellbook_engine = core.spellbook_engine
ActionStep = core._models_mod.ActionStep
QueryStep = core._models_mod.QueryStep
StepPlan = core._models_mod.StepPlan
QuerySourceSpec = core._models_mod.QuerySourceSpec
ConversationRequest = core.ConversationRequest
ConversationStatus = core.ConversationStatus
FrontendMode = core.FrontendMode
AgentConfig = core.AgentConfig
SpellbookRouter = core.SpellbookRouter


class StepCaller:
    """get_entity_state keyed per entity_id (like test_query_plan.py's
    MultiStateCaller) plus a generic call_service dispatch (like
    test_action_catalog_expansion.py's FakeCallServiceCaller) - Combined
    Spells' action steps need both."""

    def __init__(self, states: dict[str, dict] | None = None, *, accept: bool = True):
        self._states = states or {}
        self._accept = accept
        self.service_calls: list[tuple] = []
        self.contexts: list = []

    async def get_entity_state(self, entity_id):
        return self._states.get(entity_id)

    async def call_service(self, domain, service, entity_ids, data, *, area_id=None, context=None):
        self.service_calls.append((domain, service, entity_ids, dict(data), area_id))
        self.contexts.append(context)
        return self._accept

    async def set_light_power(self, *a, **k):
        raise AssertionError("not used by combined-spell tests")

    async def skip_track(self, *a, **k):
        raise AssertionError("not used by combined-spell tests")

    async def clean_vacuum_area(self, *a, **k):
        raise AssertionError("not used by combined-spell tests")


def _unrestricted():
    policy = AreaPolicy(allowed_areas=None, unrestricted=True)
    return ScopedAreaPolicy(actions=policy, queries=policy)


def _restricted(*areas):
    policy = AreaPolicy(allowed_areas=frozenset(areas))
    return ScopedAreaPolicy(actions=policy, queries=policy)


def _state(value, **attrs):
    return {"state": value, "unit": attrs.pop("unit", None), "device_class": attrs.pop("device_class", None),
            "entity_name": attrs.pop("entity_name", None), "attributes": attrs.pop("attributes", {}), **attrs}


class ParsingTests(unittest.TestCase):
    def _book(self, command):
        return spellbook_engine.load_spellbook_from_dict("test", {"id": "test", "name": "Test", "commands": [command]})

    def _combined_spell(self, **overrides):
        command = {
            "id": "combined", "trigger": "gute nacht",
            "steps": [
                {"alias": "shutters", "operation": "cover.close_cover", "target": {"kind": "all_allowed"}},
                {"alias": "front_door", "operation": "lock.lock", "target": {"kind": "entity_id", "value": "lock.front_door"}},
                {"alias": "open_contacts", "sources": [
                    {"kind": "count", "target": {"kind": "all_allowed"}, "domain_filter": "binary_sensor",
                     "state_filter": "on", "variable": "count"},
                ]},
            ],
            "catchphrase": "{shutters.target_count} Rollos, Tür {front_door.success}, {open_contacts.count} offen.",
        }
        command.update(overrides)
        return command

    def test_valid_steps_command_loads(self):
        book = self._book(self._combined_spell())
        self.assertIsNotNone(book)
        self.assertEqual(len(book.commands), 1)
        self.assertEqual(len(book.commands[0].steps), 3)

    def test_operation_and_steps_together_rejected(self):
        command = self._combined_spell(operation="entity.get_state", target={"kind": "entity_name", "capture": "e"})
        command["trigger"] = "wie ist {e}"
        self.assertIsNone(self._book(command))

    def test_sources_and_steps_together_rejected(self):
        command = self._combined_spell(sources=[{"kind": "count", "target": {"kind": "all_allowed"}, "variable": "n"}])
        self.assertIsNone(self._book(command))

    def test_steps_and_expert_together_rejected(self):
        command = self._combined_spell(expert={"operation": "light.turn_on", "target": {"kind": "all_allowed"}})
        self.assertIsNone(self._book(command))

    def test_empty_steps_list_rejected(self):
        book = self._book({"id": "bad", "trigger": "test", "steps": []})
        self.assertIsNone(book)

    def test_too_many_steps_rejected(self):
        steps = [{"alias": f"s{i}", "operation": "light.turn_on", "target": {"kind": "all_allowed"}} for i in range(13)]
        book = self._book({"id": "bad", "trigger": "test", "steps": steps})
        self.assertIsNone(book)

    def test_duplicate_alias_rejected(self):
        steps = [
            {"alias": "a", "operation": "light.turn_on", "target": {"kind": "all_allowed"}},
            {"alias": "a", "operation": "light.turn_off", "target": {"kind": "all_allowed"}},
        ]
        book = self._book({"id": "bad", "trigger": "test", "steps": steps})
        self.assertIsNone(book)

    def test_alias_shadowing_trigger_capture_rejected(self):
        steps = [{"alias": "area", "operation": "light.turn_on", "target": {"kind": "all_allowed"}}]
        book = self._book({"id": "bad", "trigger": "mach licht in {area}", "steps": steps})
        self.assertIsNone(book)

    def test_invalid_alias_identifier_rejected(self):
        steps = [{"alias": "not valid!", "operation": "light.turn_on", "target": {"kind": "all_allowed"}}]
        book = self._book({"id": "bad", "trigger": "test", "steps": steps})
        self.assertIsNone(book)

    def test_step_without_alias_is_allowed(self):
        steps = [{"operation": "light.turn_on", "target": {"kind": "all_allowed"}}]
        book = self._book({"id": "ok", "trigger": "test", "steps": steps})
        self.assertIsNotNone(book)

    def test_step_missing_operation_and_sources_rejected(self):
        steps = [{"alias": "a", "target": {"kind": "all_allowed"}}]
        book = self._book({"id": "bad", "trigger": "test", "steps": steps})
        self.assertIsNone(book)

    def test_step_with_both_operation_and_sources_rejected(self):
        steps = [{
            "alias": "a", "operation": "light.turn_on", "target": {"kind": "all_allowed"},
            "sources": [{"kind": "count", "target": {"kind": "all_allowed"}, "variable": "n"}],
        }]
        book = self._book({"id": "bad", "trigger": "test", "steps": steps})
        self.assertIsNone(book)

    def test_action_step_invalid_target_rejected(self):
        steps = [{"alias": "a", "operation": "light.turn_on", "target": {"kind": "nonsense"}}]
        book = self._book({"id": "bad", "trigger": "test", "steps": steps})
        self.assertIsNone(book)

    def test_action_step_dangling_argument_capture_rejected(self):
        steps = [{
            "alias": "a", "operation": "light.set_power", "target": {"kind": "all_allowed"},
            "arguments": {"percentage": {"capture": "nope", "type": "int", "min": 0, "max": 100}},
        }]
        book = self._book({"id": "bad", "trigger": "test", "steps": steps})
        self.assertIsNone(book)

    def test_query_step_reuses_source_validation(self):
        steps = [{"alias": "a", "sources": [{"kind": "nonsense", "target": {"kind": "all_allowed"}, "variable": "n"}]}]
        book = self._book({"id": "bad", "trigger": "test", "steps": steps})
        self.assertIsNone(book)

    def test_dotted_response_placeholder_for_declared_alias_field_allowed(self):
        book = self._book(self._combined_spell())
        self.assertIsNotNone(book)

    def test_dotted_response_placeholder_for_unknown_field_rejected(self):
        command = self._combined_spell(catchphrase="{shutters.not_a_real_field}")
        self.assertIsNone(self._book(command))

    def test_dotted_response_placeholder_for_unknown_alias_rejected(self):
        command = self._combined_spell(catchphrase="{not_an_alias.target_count}")
        self.assertIsNone(self._book(command))

    def test_bare_alias_placeholder_rejected(self):
        command = self._combined_spell(catchphrase="{shutters}")
        self.assertIsNone(self._book(command))

    def test_chained_dot_placeholder_rejected(self):
        command = self._combined_spell(catchphrase="{shutters.target_count.extra}")
        self.assertIsNone(self._book(command))

    def test_alias_is_not_usable_in_a_later_step_target_or_arguments(self):
        """Contract boundary: `{alias.field}` exists only in the response
        (output/catchphrase). A step's target/arguments may reference trigger
        captures only - an alias there is an unknown capture and rejected."""
        area = {"kind": "area_name", "capture": "area"}
        first = {"alias": "lights", "operation": "light.turn_on", "target": area}
        self.assertIsNone(self._book(self._combined_spell(trigger="mach {area}", catchphrase=None, steps=[
            first, {"operation": "light.turn_on", "target": {"kind": "area_name", "capture": "lights"}}])))
        self.assertIsNone(self._book(self._combined_spell(trigger="mach {area}", catchphrase=None, steps=[
            first, {"operation": "light.turn_on", "target": area,
                    "arguments": {"brightness_pct": {"capture": "lights", "type": "int"}}}])))

    def test_step_plan_diagnostics_name_the_failing_step_without_changing_the_result(self):
        command = self._book(self._combined_spell(
            trigger="licht {area} auf {pct}", catchphrase=None,
            steps=[
                {"alias": "a", "operation": "light.turn_on", "target": {"kind": "area_name", "capture": "area"}},
                {"alias": "b", "operation": "light.turn_on", "target": {"kind": "area_name", "capture": "area"},
                 "arguments": {"brightness_pct": {"capture": "pct", "type": "int", "min": 0, "max": 100}}},
            ],
        )).commands[0]
        diagnostics = []
        captures = {"area": "Küche", "pct": "150"}
        self.assertIsNone(spellbook_engine.build_step_plan(command, captures, "de", diagnostics=diagnostics))
        self.assertIsNone(spellbook_engine.build_step_plan(command, captures, "de"))
        self.assertEqual(diagnostics, [{"step": 1, "alias": "b", "kind": "action", "reason": "argument_invalid"}])

    def test_new_aggregate_kinds_names_states_load_in_flat_sources(self):
        # "names"/"states" are plain new QuerySourceSpec kinds - usable in
        # the pre-existing flat `sources:` field too, not just steps.
        book = self._book({
            "id": "ok", "trigger": "welche fenster sind offen",
            "sources": [
                {"kind": "names", "target": {"kind": "all_allowed"}, "domain_filter": "binary_sensor",
                 "state_filter": "on", "variable": "open_names"},
                {"kind": "states", "target": {"kind": "all_allowed"}, "domain_filter": "binary_sensor",
                 "variable": "all_states"},
            ],
            "catchphrase": "Offen: {open_names}. Zustände: {all_states}.",
        })
        self.assertIsNotNone(book)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, plan, policies=None, resolver=None, caller=None, source_area_id=None, expert_allowed=None):
        executor = ActionExecutor(resolver, caller, expert_allowed=expert_allowed)
        return await executor.execute_steps(plan, policies or _unrestricted(), source_area_id=source_area_id)

    async def test_two_action_steps_and_a_query_step_combined_response(self):
        """The brief's own worked example, at the unit level: close all
        shutters (aggregate outcome), lock the front door (single-entity
        outcome), and count open contacts (query step) - one response."""
        resolver = FakeResolver({
            None: ResolvedTargets((ResolvedEntity("cover.a", "living"), ResolvedEntity("cover.b", "living"))),
            "lock.front_door": ResolvedTargets((ResolvedEntity("lock.front_door", "entry"),)),
        })
        caller = StepCaller({
            "cover.a": _state("closed"), "cover.b": _state("closed"),
            "lock.front_door": _state("locked", entity_name="Haustür"),
        })
        plan = StepPlan(
            steps=(
                ActionStep(alias="shutters", operation="cover.close_cover",
                          target=ActionTarget("all_allowed", None)),
                ActionStep(alias="front_door", operation="lock.lock",
                          target=ActionTarget("entity_id", "lock.front_door")),
                QueryStep(alias="open_contacts", sources=(
                    QuerySourceSpec("count", ActionTarget("all_allowed", None), domain_filter="binary_sensor",
                                     state_filter="on", variable="count"),
                )),
            ),
            response_template=(
                "{shutters.target_count} Rollos, Erfolgreich: {shutters.success_count}. "
                "Tür: {front_door.state} ({front_door.entity_name}). "
                "Offene Kontakte: {open_contacts.count}."
            ),
        )
        # open_contacts resolves against the same all_allowed=None key as
        # shutters in FakeResolver's table - swap resolvers per call via a
        # tiny wrapper so each step gets its own intended targets.
        class PerCallResolver:
            def __init__(self):
                self.calls = 0

            async def resolve(self, target, *, domain_filter):
                self.calls += 1
                if domain_filter == "cover":
                    return ResolvedTargets((ResolvedEntity("cover.a", "living"), ResolvedEntity("cover.b", "living")))
                if domain_filter == "binary_sensor":
                    return ResolvedTargets(())
                return resolver._table.get(target.value, TargetNotFound())

            async def resolve_area(self, target):
                return TargetNotFound()

        # lock.lock is deliberately expert-gated in the operation catalog
        # (physical security) - unrelated to Combined Spells itself, so
        # enable it here rather than substituting a different operation.
        result = await self._run(plan, resolver=PerCallResolver(), caller=caller, expert_allowed=lambda: True)
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.data["variables"]["shutters"]["target_count"], 2)
        self.assertIsNone(result.data["variables"]["shutters"]["success_count"])
        self.assertEqual(result.data["steps"][0]["observation"]["matching_count"], 2)
        self.assertEqual(result.data["variables"]["front_door"]["state"], "locked")
        self.assertEqual(result.data["variables"]["front_door"]["entity_name"], "Haustür")
        self.assertEqual(result.data["variables"]["open_contacts"]["count"], 0)
        self.assertIn("2 Rollos", result.data["response_text"])
        self.assertIn("Tür: locked (Haustür)", result.data["response_text"])
        self.assertIn("Offene Kontakte: 0", result.data["response_text"])

    async def test_single_entity_action_step_exposes_success_and_state(self):
        resolver = FakeResolver({"light.x": ResolvedTargets((ResolvedEntity("light.x", "living"),))})
        caller = StepCaller({"light.x": _state("on", entity_name="Lampe")})
        plan = StepPlan(steps=(
            ActionStep(alias="lamp", operation="light.turn_on", target=ActionTarget("entity_id", "light.x")),
        ), response_template="{lamp.success} {lamp.state} {lamp.entity_name}")
        result = await self._run(plan, resolver=resolver, caller=caller)
        variables = result.data["variables"]["lamp"]
        self.assertEqual(variables["success"], True)
        self.assertEqual(variables["state"], "on")
        self.assertEqual(variables["entity_name"], "Lampe")
        self.assertEqual(result.data["response_text"], "True on Lampe")

    async def test_denied_step_aborts_remaining_steps(self):
        resolver = FakeResolver({"office": ResolvedTargets((ResolvedEntity("light.office", "office"),))})
        caller = StepCaller({})
        plan = StepPlan(steps=(
            ActionStep(alias="office", operation="light.turn_on", target=ActionTarget("area_id", "office")),
            ActionStep(alias="never_runs", operation="light.turn_off", target=ActionTarget("all_allowed", None)),
        ), response_template="{office.success}")
        result = await self._run(plan, policies=_restricted("living"), resolver=resolver, caller=caller)
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertEqual(len(result.data["steps"]), 1)
        self.assertEqual(caller.service_calls, [])

    async def test_failed_step_does_not_abort_later_steps(self):
        """A rejected service call for one step must not erase a later
        step's results - the brief's "8 von 9 Rollos... Noch offen" style
        partial response depends on this."""
        class PerDomainResolver:
            async def resolve(self, target, *, domain_filter):
                if domain_filter == "switch":
                    return ResolvedTargets(())
                return ResolvedTargets((ResolvedEntity("light.b", "living"),))

            async def resolve_area(self, target):
                return TargetNotFound()

        caller = StepCaller({"light.b": _state("on")})
        plan = StepPlan(steps=(
            ActionStep(alias="first", operation="switch.turn_on", target=ActionTarget("all_allowed", None)),
            ActionStep(alias="second", operation="light.turn_on", target=ActionTarget("all_allowed", None)),
        ), response_template="{first.target_count}/{second.target_count}")
        result = await self._run(plan, resolver=PerDomainResolver(), caller=caller)
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.reason_code, "partial")
        self.assertEqual(result.data["variables"]["first"]["target_count"], 0)
        self.assertEqual(result.data["variables"]["second"]["target_count"], 1)
        self.assertEqual(result.data["response_text"], "0/1")

    async def test_confirmable_operation_reports_snapshot_without_claiming_terminal_success(self):
        resolver = FakeResolver({None: ResolvedTargets((
            ResolvedEntity("switch.a", "living"), ResolvedEntity("switch.b", "living"), ResolvedEntity("switch.c", "living"),
        ))})
        # Service call is globally accepted, but only two of three entities
        # actually report the confirmable end-state afterwards.
        caller = StepCaller({
            "switch.a": _state("on"), "switch.b": _state("on"), "switch.c": _state("off", entity_name="Steckdose C"),
        })
        plan = StepPlan(steps=(
            ActionStep(alias="switches", operation="switch.turn_on", target=ActionTarget("all_allowed", None)),
        ), response_template="{switches.success_count}/{switches.target_count} ({switches.failed_names})")
        result = await self._run(plan, resolver=resolver, caller=caller)
        variables = result.data["variables"]["switches"]
        self.assertEqual(variables["target_count"], 3)
        self.assertIsNone(variables["success_count"])
        self.assertIsNone(variables["failed_count"])
        self.assertEqual(variables["failed_names"], [])
        self.assertEqual(result.data["steps"][0]["observation"], {
            "status": "immediate_state_snapshot", "matching_count": 2,
            "other_count": 1, "other_names": ["Steckdose C"],
        })
        self.assertEqual(result.data["response_text"], "?/3 (?)")

    async def test_non_confirmable_operation_falls_back_to_batch_outcome(self):
        """script.turn_on has no unconditional end-state - honest batch
        accept/reject only, no invented per-entity precision."""
        resolver = FakeResolver({None: ResolvedTargets((
            ResolvedEntity("script.a", None), ResolvedEntity("script.b", None),
        ))})
        caller = StepCaller({})
        plan = StepPlan(steps=(
            ActionStep(alias="scripts", operation="script.turn_on", target=ActionTarget("all_allowed", None)),
        ), response_template="{scripts.success_count}/{scripts.target_count}")
        result = await self._run(plan, resolver=resolver, caller=caller)
        variables = result.data["variables"]["scripts"]
        self.assertEqual(variables["target_count"], 2)
        self.assertIsNone(variables["success_count"])
        self.assertIsNone(variables["failed_count"])
        self.assertEqual(caller.service_calls[0][:2], ("script", "turn_on"))

    async def test_rejected_service_call_does_not_invent_per_entity_failure(self):
        resolver = FakeResolver({None: ResolvedTargets((ResolvedEntity("switch.a", "living"),))})
        caller = StepCaller({}, accept=False)
        plan = StepPlan(steps=(
            ActionStep(alias="switches", operation="switch.turn_on", target=ActionTarget("all_allowed", None)),
        ), response_template="{switches.success_count}/{switches.target_count}")
        result = await self._run(plan, resolver=resolver, caller=caller)
        self.assertEqual(result.reason_code, "partial")
        variables = result.data["variables"]["switches"]
        self.assertEqual(variables["target_count"], 1)
        self.assertIsNone(variables["success_count"])
        self.assertIsNone(variables["failed_count"])

    async def test_step_without_alias_still_executes_but_contributes_no_variables(self):
        resolver = FakeResolver({None: ResolvedTargets((ResolvedEntity("light.a", "living"),))})
        caller = StepCaller({"light.a": _state("on")})
        plan = StepPlan(steps=(
            ActionStep(alias=None, operation="light.turn_on", target=ActionTarget("all_allowed", None)),
        ), response_template="Erledigt.")
        result = await self._run(plan, resolver=resolver, caller=caller)
        self.assertEqual(result.data["variables"], {})
        self.assertEqual(len(caller.service_calls), 1)
        self.assertEqual(result.data["response_text"], "Erledigt.")

    async def test_query_step_names_and_states_kinds(self):
        resolver = FakeResolver({None: ResolvedTargets((
            ResolvedEntity("binary_sensor.window", "living"), ResolvedEntity("binary_sensor.door", "entry"),
        ))})
        caller = StepCaller({
            "binary_sensor.window": _state("on", entity_name="Küchenfenster"),
            "binary_sensor.door": _state("on", entity_name="Terrassentür"),
        })
        plan = StepPlan(steps=(
            QueryStep(alias="open_contacts", sources=(
                QuerySourceSpec("count", ActionTarget("all_allowed", None), variable="count"),
                QuerySourceSpec("names", ActionTarget("all_allowed", None), variable="names"),
                QuerySourceSpec("states", ActionTarget("all_allowed", None), variable="states"),
            )),
        ), response_template="{open_contacts.count}: {open_contacts.names}")
        result = await self._run(plan, resolver=resolver, caller=caller)
        variables = result.data["variables"]["open_contacts"]
        self.assertEqual(variables["count"], 2)
        # variables (data["variables"]) keeps raw values, same convention
        # execute_query_plan already uses - sanitizing/joining happens
        # only for the rendered response_text below.
        self.assertEqual(variables["names"], ["Küchenfenster", "Terrassentür"])
        self.assertEqual(result.data["response_text"], "2: Küchenfenster, Terrassentür")

    async def test_dry_run_does_not_call_services_and_uses_batch_semantics(self):
        """The Studio's "Testen - ohne Geräteaufruf" preview (execute()'s
        existing dry_run flag) must work for Combined Spells too - no
        service call, and no invented per-entity confirmation for what
        would otherwise be a confirmable operation."""
        resolver = FakeResolver({None: ResolvedTargets((
            ResolvedEntity("switch.a", "living"), ResolvedEntity("switch.b", "living"),
        ))})
        caller = StepCaller({})
        plan = StepPlan(steps=(
            ActionStep(alias="switches", operation="switch.turn_on", target=ActionTarget("all_allowed", None)),
        ), response_template="{switches.target_count}/{switches.success_count}")
        executor = ActionExecutor(resolver, caller)
        result = await executor.execute_steps(plan, _unrestricted(), dry_run=True)
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(caller.service_calls, [])
        self.assertEqual(result.data["variables"]["switches"], {
            "target_count": 2, "success_count": None, "failed_count": None, "failed_names": [],
        })
        self.assertEqual(result.data["response_text"], "2/?")

    async def test_query_then_action_with_capture_alias_output_and_step_reports(self):
        """Representative mixed flow built by the engine from YAML: a query
        step then action steps, a trigger capture used in a later step's
        target and argument, a failing middle step (unknown operation) that
        does not abort the rest, alias fields in the output, and the
        descriptive per-step report the Studio dry-run shows."""
        book = spellbook_engine.load_spellbook_from_dict("t", {"id": "t", "name": "T", "commands": [{
            "id": "evening", "trigger": "abend {area} auf {pct}",
            "steps": [
                {"alias": "before", "sources": [
                    {"kind": "count", "target": {"kind": "area_name", "capture": "area"}, "domain_filter": "light",
                     "state_filter": "on", "variable": "on"}]},
                {"alias": "broken", "operation": "no.such_operation", "target": {"kind": "area_name", "capture": "area"}},
                {"alias": "dim", "operation": "light.turn_on", "target": {"kind": "area_name", "capture": "area"},
                 "arguments": {"brightness_pct": {"capture": "pct", "type": "int", "min": 0, "max": 100}}},
            ],
            "output": "{area}: vorher {before.on} an, {dim.success_count}/{dim.target_count} gedimmt, Fehler {broken.target_count}",
        }]})
        command = book.commands[0]
        plan = spellbook_engine.build_step_plan(command, {"area": "Küche", "pct": "40"}, "de")

        class AreaResolver:
            async def resolve(self, target, *, domain_filter):
                assert target.value == "Küche"
                return ResolvedTargets((ResolvedEntity("light.k", "kitchen"),))

            async def resolve_area(self, target):
                return TargetNotFound()

        caller = StepCaller({"light.k": _state("on")})
        result = await self._run(plan, resolver=AreaResolver(), caller=caller)
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.reason_code, "partial")
        self.assertEqual(result.data["response_text"], "Küche: vorher 1 an, ?/1 gedimmt, Fehler 0")
        self.assertEqual(caller.service_calls, [("light", "turn_on", ("light.k",), {"brightness_pct": 40}, None)])
        reports = result.data["steps"]
        self.assertEqual([(r["index"], r["alias"], r["kind"], r["status"]) for r in reports],
                         [(0, "before", "query", "ok"), (1, "broken", "action", "failed"), (2, "dim", "action", "ok")])
        self.assertEqual(reports[1]["reason_code"], "unknown_operation")
        self.assertEqual(reports[2]["operation"], "light.turn_on")
        self.assertEqual(reports[2]["target"], {"kind": "area_name", "value": "Küche"})
        self.assertEqual(reports[2]["arguments"], {"brightness_pct": 40})
        self.assertEqual(reports[2]["entity_ids"], ["light.k"])
        self.assertIsNone(reports[2]["values"]["success_count"])
        self.assertEqual(reports[2]["observation"]["matching_count"], 1)

        caller = StepCaller({"light.k": _state("on")})
        dry = await ActionExecutor(AreaResolver(), caller).execute_steps(plan, _unrestricted(), dry_run=True)
        self.assertEqual(caller.service_calls, [])
        self.assertEqual([r.get("reason_code") for r in dry.data["steps"]], [None, "unknown_operation", "dry_run"])

    async def test_no_response_template_returns_no_response_text(self):
        resolver = FakeResolver({None: ResolvedTargets(())})
        plan = StepPlan(steps=(
            ActionStep(alias="a", operation="light.turn_on", target=ActionTarget("all_allowed", None)),
        ), response_template=None)
        result = await self._run(plan, resolver=resolver, caller=StepCaller({}))
        self.assertIsNone(result.data["response_text"])

    async def test_step_dispatches_through_the_same_call_path_as_a_single_action_spell(self):
        """Combined Spells reuse execute() unchanged per action step (see
        execute_steps' docstring) - this pins that a step using one of
        the two operations fixed in the HA-service-call audit
        (light.set_color/light.set_color_temperature used to call
        nonexistent HA services) dispatches identically whether it's a
        lone spell or one step among several, single-entity here since
        that's what exercises core/step_variables.py's action_step_fields
        single-entity branch (state/entity_name/success)."""
        resolver = FakeResolver({"light.x": ResolvedTargets((ResolvedEntity("light.x", "living"),))})
        caller = StepCaller({"light.x": _state("on", entity_name="Lampe")})
        plan = StepPlan(steps=(
            ActionStep(alias="color", operation="light.set_color", target=ActionTarget("entity_id", "light.x"),
                      arguments={"rgb_color": [255, 105, 180]}),
            ActionStep(alias="temp", operation="light.set_color_temperature", target=ActionTarget("entity_id", "light.x"),
                      arguments={"kelvin": 3000}),
        ), response_template="{color.success} {temp.success}")
        result = await self._run(plan, resolver=resolver, caller=caller)
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.data["response_text"], "True True")
        self.assertEqual(caller.service_calls[0][:4], ("light", "turn_on", ("light.x",), {"rgb_color": [255, 105, 180]}))
        self.assertEqual(caller.service_calls[1][:4], ("light", "turn_on", ("light.x",), {"color_temp_kelvin": 3000}))


class RouterIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end through SpellbookRouter.handle() - mirrors
    test_query_plan.py's own RouterIntegrationTests for `sources`."""

    def _book(self, command):
        return spellbook_engine.load_spellbook_from_dict("test", {"id": "test", "name": "Test", "commands": [command]})

    def _request(self, text):
        return ConversationRequest(
            request_id="test-req", agent_entry_id="e1", text=text, language="de", source_namespace="test",
        )

    async def test_combined_spell_end_to_end(self):
        book = self._book({
            "id": "night_mode", "trigger": "gute nacht",
            "steps": [
                {"alias": "lights", "operation": "light.turn_off", "target": {"kind": "all_allowed"}},
                {"alias": "contacts", "sources": [
                    {"kind": "count", "target": {"kind": "all_allowed"}, "domain_filter": "binary_sensor",
                     "state_filter": "on", "variable": "n"},
                ]},
            ],
            "catchphrase": "{lights.success_count} von {lights.target_count} Lichtern aus, {contacts.n} noch offen.",
            "output_mode": "TEXT",
        })

        class TwoDomainResolver:
            async def resolve(self, target, *, domain_filter):
                if domain_filter == "light":
                    return ResolvedTargets((ResolvedEntity("light.a", "living"), ResolvedEntity("light.b", "living")))
                return ResolvedTargets((ResolvedEntity("binary_sensor.door", "entry"),))

            async def resolve_area(self, target):
                return TargetNotFound()

        caller = StepCaller({
            "light.a": _state("off"), "light.b": _state("off"), "binary_sensor.door": _state("on"),
        })
        router = SpellbookRouter(ActionExecutor(TwoDomainResolver(), caller))
        config = AgentConfig(mode=FrontendMode.SPELLBOOK_ONLY, spellbook=book, policy=_unrestricted())
        result = await router.handle(self._request("gute nacht"), config)
        self.assertEqual(result.status, ConversationStatus.COMPLETED)
        self.assertEqual(result.response_text, "? von 2 Lichtern aus, 1 noch offen.")
        self.assertEqual(result.output_mode, "TEXT")

    async def test_combined_spell_denied_by_area_policy(self):
        book = self._book({
            "id": "office_lights", "trigger": "buero licht aus",
            "steps": [{"alias": "a", "operation": "light.turn_off", "target": {"kind": "area_id", "value": "office"}}],
            "catchphrase": "{a.success_count}",
        })
        resolver = FakeResolver({"office": ResolvedTargets((ResolvedEntity("light.office", "office"),))})
        router = SpellbookRouter(ActionExecutor(resolver, StepCaller({})))
        config = AgentConfig(mode=FrontendMode.SPELLBOOK_ONLY, spellbook=book, policy=_restricted("living"))
        result = await router.handle(self._request("buero licht aus"), config)
        self.assertEqual(result.status, ConversationStatus.REJECTED)


if __name__ == "__main__":
    unittest.main()
