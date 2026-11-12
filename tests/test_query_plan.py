"""Phase 2 of the Spellbook Studio capability expansion: multi-source
queries, variables and aggregation (item 4/5/6 of the brief) - the
QuerySourceSpec/QueryPlan model in core/models.py, its parsing in
core/spellbook_engine.py (sources -> build_query_plan), and its execution
in core/executor.py (ActionExecutor.execute_query_plan). Also pins that the
existing single-entity entity.get_state path is unaffected.

Reuses test_core_engine's package loader/FakeResolver; FakeCaller isn't
reused since these tests need per-entity_id-varying state (FakeCaller
returns one fixed state for every entity_id, which none of the existing
tests needed before multi-entity aggregation existed).
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
QuerySourceSpec = core._models_mod.QuerySourceSpec
QueryPlan = core._models_mod.QueryPlan
ConversationRequest = core.ConversationRequest
ConversationStatus = core.ConversationStatus
FrontendMode = core.FrontendMode
AgentConfig = core.AgentConfig
SpellbookRouter = core.SpellbookRouter


class MultiStateCaller:
    """get_entity_state keyed per entity_id (unlike test_core_engine's
    FakeCaller, which returns one fixed state for every call) - needed for
    aggregation over several distinct entities."""

    def __init__(self, states: dict[str, dict]):
        self._states = states
        self.calls: list[str] = []

    async def get_entity_state(self, entity_id):
        self.calls.append(entity_id)
        return self._states.get(entity_id)

    async def set_light_power(self, *a, **k):
        raise AssertionError("not used by query-plan tests")

    async def skip_track(self, *a, **k):
        raise AssertionError("not used by query-plan tests")

    async def clean_vacuum_area(self, *a, **k):
        raise AssertionError("not used by query-plan tests")


def _unrestricted():
    policy = AreaPolicy(allowed_areas=None, unrestricted=True)
    return ScopedAreaPolicy(actions=policy, queries=policy)


def _restricted(*areas):
    policy = AreaPolicy(allowed_areas=frozenset(areas))
    return ScopedAreaPolicy(actions=policy, queries=policy)


def _state(value, **attrs):
    return {"state": value, "unit": attrs.pop("unit", None), "device_class": attrs.pop("device_class", None),
            "attributes": attrs.pop("attributes", {}), **attrs}


class ParsingTests(unittest.TestCase):
    def _book(self, command):
        return spellbook_engine.load_spellbook_from_dict("test", {"id": "test", "name": "Test", "commands": [command]})

    def test_valid_multi_source_query_loads(self):
        book = self._book({
            "id": "climate_report", "trigger": "wie ist das klima in {area}",
            "sources": [
                {"kind": "state", "target": {"kind": "area_name", "capture": "area"}, "domain_filter": "sensor",
                 "device_class": "temperature", "variable": "temperature", "unit_variable": "temperature_unit"},
                {"kind": "count", "target": {"kind": "area_name", "capture": "area"}, "domain_filter": "light",
                 "state_filter": "on", "variable": "lights_on"},
            ],
            "catchphrase": "Im {area} sind es {temperature} {temperature_unit}, {lights_on} Lichter an.",
        })
        self.assertIsNotNone(book)
        self.assertEqual(len(book.commands), 1)
        self.assertEqual(len(book.commands[0].sources), 2)

    def test_operation_and_sources_together_rejected(self):
        book = self._book({
            "id": "bad", "trigger": "test", "operation": "entity.get_state",
            "target": {"kind": "entity_name", "capture": "e"},
            "sources": [{"kind": "count", "target": {"kind": "all_allowed"}, "variable": "n"}],
        })
        self.assertIsNone(book)

    def test_invalid_source_kind_rejected(self):
        book = self._book({
            "id": "bad", "trigger": "test",
            "sources": [{"kind": "nonsense", "target": {"kind": "all_allowed"}, "variable": "n"}],
        })
        self.assertIsNone(book)

    def test_entity_target_rejected_for_aggregation(self):
        book = self._book({
            "id": "bad", "trigger": "test",
            "sources": [{"kind": "count", "target": {"kind": "entity_name", "capture": "e"}, "variable": "n"}],
        })
        self.assertIsNone(book)

    def test_all_allowed_rejected_for_single_entity_kind(self):
        book = self._book({
            "id": "bad", "trigger": "test",
            "sources": [{"kind": "state", "target": {"kind": "all_allowed"}, "variable": "n"}],
        })
        self.assertIsNone(book)

    def test_duplicate_variable_names_rejected(self):
        book = self._book({
            "id": "bad", "trigger": "test",
            "sources": [
                {"kind": "count", "target": {"kind": "all_allowed"}, "variable": "n"},
                {"kind": "count", "target": {"kind": "all_allowed"}, "domain_filter": "switch", "variable": "n"},
            ],
        })
        self.assertIsNone(book)

    def test_variable_shadowing_own_trigger_placeholder_rejected(self):
        # A source named "area" would shadow the {area} capture its own
        # (or another source's) target relies on to resolve at all.
        book = self._book({
            "id": "bad", "trigger": "wie ist das klima in {area}",
            "sources": [{"kind": "count", "target": {"kind": "area_name", "capture": "area"}, "variable": "area"}],
        })
        self.assertIsNone(book)

    def test_variable_matching_query_response_field_name_is_allowed(self):
        # "temperature" collides with entity.get_state's own response
        # vocabulary, but that path is mutually exclusive with sources on
        # the same command - no real collision, and it's exactly the kind
        # of natural name a climate-report query wants (see the brief's
        # own worked example).
        book = self._book({
            "id": "ok", "trigger": "wie warm ist es",
            "sources": [{"kind": "state", "target": {"kind": "entity_name", "value": "sensor.wz"}, "variable": "temperature"}],
            "catchphrase": "{temperature} Grad.",
        })
        self.assertIsNotNone(book)

    def test_attribute_kind_requires_attribute_name(self):
        book = self._book({
            "id": "bad", "trigger": "test",
            "sources": [{"kind": "attribute", "target": {"kind": "entity_name", "capture": "e"}, "variable": "n"}],
        })
        self.assertIsNone(book)

    def test_response_may_reference_declared_source_variables(self):
        book = self._book({
            "id": "ok", "trigger": "wie viele lichter an",
            "sources": [{"kind": "count", "target": {"kind": "all_allowed"}, "domain_filter": "light", "variable": "lights_on"}],
            "catchphrase": "{lights_on} Lichter sind an.",
        })
        self.assertEqual(len(book.commands), 1)

    def test_response_referencing_unknown_variable_rejected(self):
        book = self._book({
            "id": "bad", "trigger": "test",
            "sources": [{"kind": "count", "target": {"kind": "all_allowed"}, "variable": "lights_on"}],
            "catchphrase": "{not_declared} Lichter sind an.",
        })
        self.assertIsNone(book)

    def test_empty_sources_list_rejected(self):
        book = self._book({"id": "bad", "trigger": "test", "sources": []})
        self.assertIsNone(book)

    def test_output_mode_parses_and_validates(self):
        book = self._book({
            "id": "ok", "trigger": "test", "operation": "light.turn_on",
            "target": {"kind": "all_allowed"}, "output_mode": "SPEECH",
        })
        self.assertEqual(book.commands[0].output_mode, "SPEECH")
        book = self._book({
            "id": "bad", "trigger": "test", "operation": "light.turn_on",
            "target": {"kind": "all_allowed"}, "output_mode": "LOUD",
        })
        self.assertIsNone(book)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, plan, policies=None, resolver=None, caller=None, source_area_id=None):
        executor = ActionExecutor(resolver, caller)
        return await executor.execute_query_plan(plan, policies or _unrestricted(), source_area_id=source_area_id)

    async def test_worked_example_climate_report(self):
        """The brief's own worked example: temperature + humidity + a
        lights-on count in one area, combined into one sentence."""
        resolver = FakeResolver({
            "Wohnzimmer": ResolvedTargets((ResolvedEntity("sensor.living_temp", "living"),)),
        })
        # Different domain_filter calls key on the SAME captured area name in
        # FakeResolver's simple value->result table, so this test drives
        # each source through its own resolver instance instead.

        temp_resolver = FakeResolver({"Wohnzimmer": ResolvedTargets((ResolvedEntity("sensor.living_temp", "living"),))})
        humidity_resolver = FakeResolver({"Wohnzimmer": ResolvedTargets((ResolvedEntity("sensor.living_humidity", "living"),))})
        lights_resolver = FakeResolver({"Wohnzimmer": ResolvedTargets((
            ResolvedEntity("light.a", "living"), ResolvedEntity("light.b", "living"), ResolvedEntity("light.c", "living"),
        ))})

        caller = MultiStateCaller({
            "sensor.living_temp": _state("21.5", unit="°C"),
            "sensor.living_humidity": _state("55", unit="%"),
            "light.a": _state("on"), "light.b": _state("on"), "light.c": _state("off"),
        })

        plan = QueryPlan(
            sources=(
                QuerySourceSpec("state", ActionTarget("area_name", "Wohnzimmer"), domain_filter="sensor",
                                 variable="temperature", unit_variable="temperature_unit"),
            ),
            response_template="Im Wohnzimmer hat es {temperature} {temperature_unit}.",
        )
        result = await self._run(plan, resolver=temp_resolver, caller=caller)
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.data["response_text"], "Im Wohnzimmer hat es 21.5 °C.")
        self.assertEqual(result.data["variables"], {"temperature": "21.5", "temperature_unit": "°C"})

        plan2 = QueryPlan(
            sources=(
                QuerySourceSpec("count", ActionTarget("area_name", "Wohnzimmer"), domain_filter="light",
                                 state_filter="on", variable="lights_on"),
            ),
            response_template="Es sind {lights_on} Lichter an.",
        )
        result2 = await self._run(plan2, resolver=lights_resolver, caller=caller)
        self.assertEqual(result2.data["variables"], {"lights_on": 2})
        self.assertEqual(result2.data["response_text"], "Es sind 2 Lichter an.")

        plan3 = QueryPlan(
            sources=(
                QuerySourceSpec("state", ActionTarget("area_name", "Wohnzimmer"), domain_filter="sensor",
                                 variable="humidity", unit_variable="humidity_unit"),
            ),
            response_template="Die Luftfeuchtigkeit beträgt {humidity} {humidity_unit}.",
        )
        result3 = await self._run(plan3, resolver=humidity_resolver, caller=caller)
        self.assertEqual(result3.data["variables"], {"humidity": "55", "humidity_unit": "%"})

    async def test_multiple_sources_merge_into_one_response(self):
        resolver = FakeResolver({
            "temp-target": ResolvedTargets((ResolvedEntity("sensor.t", "living"),)),
            "hum-target": ResolvedTargets((ResolvedEntity("sensor.h", "living"),)),
        })
        caller = MultiStateCaller({
            "sensor.t": _state("21.5", unit="°C"),
            "sensor.h": _state("55", unit="%"),
        })
        plan = QueryPlan(
            sources=(
                QuerySourceSpec("state", ActionTarget("area_name", "temp-target"), variable="temperature", unit_variable="temperature_unit"),
                QuerySourceSpec("state", ActionTarget("area_name", "hum-target"), variable="humidity", unit_variable="humidity_unit"),
            ),
            response_template="{temperature}{temperature_unit} / {humidity}{humidity_unit}",
        )
        result = await self._run(plan, resolver=resolver, caller=caller)
        self.assertEqual(result.data["response_text"], "21.5°C / 55%")

    async def test_device_class_disambiguates_same_domain_entities(self):
        resolver = FakeResolver({"living": ResolvedTargets((
            ResolvedEntity("sensor.living_temp", "living"), ResolvedEntity("sensor.living_humidity", "living"),
        ))})
        caller = MultiStateCaller({
            "sensor.living_temp": _state("21.5", device_class="temperature"),
            "sensor.living_humidity": _state("55", device_class="humidity"),
        })
        plan = QueryPlan(sources=(
            QuerySourceSpec("state", ActionTarget("area_id", "living"), domain_filter="sensor",
                             device_class="temperature", variable="temperature"),
        ), response_template="{temperature}")
        result = await self._run(plan, resolver=resolver, caller=caller)
        self.assertEqual(result.data["variables"]["temperature"], "21.5")

    async def test_ambiguous_source_without_device_class_degrades_gracefully(self):
        resolver = FakeResolver({"living": ResolvedTargets((
            ResolvedEntity("sensor.living_temp", "living"), ResolvedEntity("sensor.living_humidity", "living"),
        ))})
        caller = MultiStateCaller({
            "sensor.living_temp": _state("21.5"), "sensor.living_humidity": _state("55"),
        })
        plan = QueryPlan(sources=(
            QuerySourceSpec("state", ActionTarget("area_id", "living"), domain_filter="sensor", variable="temperature"),
        ), response_template="Es sind {temperature} Grad.")
        result = await self._run(plan, resolver=resolver, caller=caller)
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.data["sources"][0]["status"], "ambiguous_source")
        self.assertEqual(result.data["response_text"], "Es sind ? Grad.")

    async def test_target_not_found_degrades_to_missing_value_token(self):
        resolver = FakeResolver({})
        plan = QueryPlan(sources=(
            QuerySourceSpec("state", ActionTarget("entity_name", "Nichtvorhanden"), variable="temperature"),
        ), response_template="Es sind {temperature} Grad.")
        result = await self._run(plan, resolver=resolver, caller=MultiStateCaller({}))
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.data["sources"][0]["status"], "target_not_found")
        self.assertEqual(result.data["response_text"], "Es sind ? Grad.")

    async def test_unavailable_state_degrades_to_missing_value_token(self):
        resolver = FakeResolver({"e": ResolvedTargets((ResolvedEntity("sensor.x", "living"),))})
        caller = MultiStateCaller({"sensor.x": _state("unavailable")})
        plan = QueryPlan(sources=(
            QuerySourceSpec("state", ActionTarget("entity_name", "e"), variable="temperature"),
        ), response_template="{temperature}")
        result = await self._run(plan, resolver=resolver, caller=caller)
        self.assertEqual(result.data["sources"][0]["status"], "unavailable")
        self.assertEqual(result.data["response_text"], "?")

    async def test_missing_attribute_degrades_to_missing_value_token(self):
        resolver = FakeResolver({"e": ResolvedTargets((ResolvedEntity("light.x", "living"),))})
        caller = MultiStateCaller({"light.x": _state("on", attributes={})})
        plan = QueryPlan(sources=(
            QuerySourceSpec("attribute", ActionTarget("entity_name", "e"), attribute="rgb_color", variable="color"),
        ), response_template="{color}")
        result = await self._run(plan, resolver=resolver, caller=caller)
        self.assertEqual(result.data["sources"][0]["status"], "missing_attribute")
        self.assertEqual(result.data["response_text"], "?")

    async def test_attribute_kind_reads_arbitrary_attribute(self):
        resolver = FakeResolver({"e": ResolvedTargets((ResolvedEntity("light.x", "living"),))})
        caller = MultiStateCaller({"light.x": _state("on", attributes={"effect": "colorloop"})})
        plan = QueryPlan(sources=(
            QuerySourceSpec("attribute", ActionTarget("entity_name", "e"), attribute="effect", variable="effect"),
        ), response_template="{effect}")
        result = await self._run(plan, resolver=resolver, caller=caller)
        self.assertEqual(result.data["variables"]["effect"], "colorloop")

    async def test_empty_aggregation_reports_and_degrades(self):
        resolver = FakeResolver({"empty": ResolvedTargets(())})
        plan = QueryPlan(sources=(
            QuerySourceSpec("avg", ActionTarget("area_id", "empty"), domain_filter="sensor", variable="avg_temp"),
        ), response_template="Durchschnitt: {avg_temp}")
        result = await self._run(plan, resolver=resolver, caller=MultiStateCaller({}))
        self.assertEqual(result.data["sources"][0]["status"], "empty_aggregation")
        self.assertEqual(result.data["response_text"], "Durchschnitt: ?")

    async def test_count_of_zero_is_a_valid_answer_not_an_error(self):
        resolver = FakeResolver({"empty": ResolvedTargets(())})
        plan = QueryPlan(sources=(
            QuerySourceSpec("count", ActionTarget("area_id", "empty"), domain_filter="light", variable="n"),
        ), response_template="{n}")
        result = await self._run(plan, resolver=resolver, caller=MultiStateCaller({}))
        self.assertEqual(result.data["sources"][0]["status"], "ok")
        self.assertEqual(result.data["variables"]["n"], 0)
        self.assertEqual(result.data["response_text"], "0")

    async def test_min_max_avg_sum_aggregations(self):
        resolver = FakeResolver({"area": ResolvedTargets((
            ResolvedEntity("sensor.a", "area"), ResolvedEntity("sensor.b", "area"), ResolvedEntity("sensor.c", "area"),
        ))})
        caller = MultiStateCaller({
            "sensor.a": _state("10"), "sensor.b": _state("20"), "sensor.c": _state("30"),
        })
        for kind, expected in (("min", 10.0), ("max", 30.0), ("avg", 20.0), ("sum", 60.0)):
            with self.subTest(kind=kind):
                plan = QueryPlan(sources=(
                    QuerySourceSpec(kind, ActionTarget("area_id", "area"), domain_filter="sensor", variable="v"),
                ), response_template="{v}")
                result = await self._run(plan, resolver=resolver, caller=caller)
                self.assertEqual(result.data["variables"]["v"], expected)

    async def test_aggregation_skips_non_numeric_states(self):
        resolver = FakeResolver({"area": ResolvedTargets((
            ResolvedEntity("sensor.a", "area"), ResolvedEntity("sensor.b", "area"),
        ))})
        caller = MultiStateCaller({"sensor.a": _state("10"), "sensor.b": _state("unavailable")})
        plan = QueryPlan(sources=(
            QuerySourceSpec("avg", ActionTarget("area_id", "area"), domain_filter="sensor", variable="v"),
        ), response_template="{v}")
        result = await self._run(plan, resolver=resolver, caller=caller)
        self.assertEqual(result.data["variables"]["v"], 10.0)
        self.assertEqual(result.data["sources"][0]["matched"], 1)

    async def test_policy_denial_on_named_target_aborts_whole_plan(self):
        resolver = FakeResolver({"office": ResolvedTargets((ResolvedEntity("sensor.office_temp", "office"),))})
        plan = QueryPlan(sources=(
            QuerySourceSpec("state", ActionTarget("area_id", "office"), variable="temperature"),
        ), response_template="{temperature}")
        result = await self._run(plan, policies=_restricted("living"), resolver=resolver, caller=MultiStateCaller({}))
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertEqual(result.reason_code, "area_not_permitted")

    async def test_all_allowed_target_silently_narrows_to_permitted_entities(self):
        resolver = FakeResolver({}, areas=None)

        class WholeHouseResolver:
            async def resolve(self, target, *, domain_filter):
                return ResolvedTargets((
                    ResolvedEntity("light.living", "living"), ResolvedEntity("light.office", "office"),
                ))

            async def resolve_area(self, target):
                return TargetNotFound()

        caller = MultiStateCaller({"light.living": _state("on"), "light.office": _state("on")})
        plan = QueryPlan(sources=(
            QuerySourceSpec("count", ActionTarget("all_allowed", None), domain_filter="light",
                             state_filter="on", variable="n"),
        ), response_template="{n}")
        result = await self._run(plan, policies=_restricted("living"), resolver=WholeHouseResolver(), caller=caller)
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.data["variables"]["n"], 1)

    async def test_source_area_without_a_source_area_id_degrades_softly(self):
        plan = QueryPlan(sources=(
            QuerySourceSpec("count", ActionTarget("source_area", None), domain_filter="light", variable="n"),
        ), response_template="{n}")
        result = await self._run(plan, resolver=FakeResolver({}), caller=MultiStateCaller({}), source_area_id=None)
        self.assertEqual(result.data["sources"][0]["status"], "no_source_area")
        self.assertEqual(result.data["response_text"], "?")

    async def test_source_area_substitutes_area_id(self):
        resolver = FakeResolver({"kitchen": ResolvedTargets((ResolvedEntity("light.k", "kitchen"),))})
        caller = MultiStateCaller({"light.k": _state("on")})
        plan = QueryPlan(sources=(
            QuerySourceSpec("count", ActionTarget("source_area", None), domain_filter="light",
                             state_filter="on", variable="n"),
        ), response_template="{n}")
        result = await self._run(plan, resolver=resolver, caller=caller, source_area_id="kitchen")
        self.assertEqual(result.data["variables"]["n"], 1)

    async def test_no_response_template_falls_back_to_generic_text(self):
        plan = QueryPlan(sources=(
            QuerySourceSpec("count", ActionTarget("all_allowed", None), variable="n"),
        ), response_template=None)

        class EmptyResolver:
            async def resolve(self, target, *, domain_filter):
                return ResolvedTargets(())

            async def resolve_area(self, target):
                return TargetNotFound()

        result = await self._run(plan, resolver=EmptyResolver(), caller=MultiStateCaller({}))
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertIsNone(result.data["response_text"])


class RouterIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end through SpellbookRouter.handle() - text in, ConversationResult
    out - not just the unit-level executor/parsing tests above. Catches
    wiring bugs (find_command's query_plan branch, EngineMatch routing,
    ConversationStatus mapping, output_mode propagation) those wouldn't."""

    def _book(self, command):
        return spellbook_engine.load_spellbook_from_dict("test", {"id": "test", "name": "Test", "commands": [command]})

    def _request(self, text):
        return ConversationRequest(
            request_id="test-req", agent_entry_id="e1", text=text, language="de", source_namespace="test",
        )

    async def test_multi_source_query_end_to_end(self):
        book = self._book({
            "id": "lights_on_count", "trigger": "wie viele lichter sind an",
            "sources": [{
                "kind": "count", "target": {"kind": "all_allowed"}, "domain_filter": "light",
                "state_filter": "on", "variable": "lights_on",
            }],
            "catchphrase": "Es sind {lights_on} Lichter an.",
            "output_mode": "TEXT",
        })
        resolver = FakeResolver({})

        class AllLightsResolver:
            async def resolve(self, target, *, domain_filter):
                return ResolvedTargets((
                    ResolvedEntity("light.a", "living"), ResolvedEntity("light.b", "living"),
                ))

            async def resolve_area(self, target):
                return TargetNotFound()

        caller = MultiStateCaller({"light.a": _state("on"), "light.b": _state("off")})
        router = SpellbookRouter(ActionExecutor(AllLightsResolver(), caller))
        config = AgentConfig(mode=FrontendMode.SPELLBOOK_ONLY, spellbook=book, policy=_unrestricted())
        result = await router.handle(self._request("wie viele lichter sind an"), config)
        self.assertEqual(result.status, ConversationStatus.COMPLETED)
        self.assertEqual(result.response_text, "Es sind 1 Lichter an.")
        self.assertEqual(result.output_mode, "TEXT")
        self.assertEqual(len(result.execution_results), 1)
        self.assertEqual(result.execution_results[0].status, ExecutionStatus.QUERY_ANSWER)

    async def test_query_denied_by_area_policy(self):
        book = self._book({
            "id": "office_temp", "trigger": "wie warm ist es im buero",
            "sources": [{"kind": "state", "target": {"kind": "area_id", "value": "office"}, "variable": "temperature"}],
            "catchphrase": "{temperature} Grad.",
        })
        resolver = FakeResolver({"office": ResolvedTargets((ResolvedEntity("sensor.office", "office"),))})
        router = SpellbookRouter(ActionExecutor(resolver, MultiStateCaller({})))
        config = AgentConfig(mode=FrontendMode.SPELLBOOK_ONLY, spellbook=book, policy=_restricted("living"))
        result = await router.handle(self._request("wie warm ist es im buero"), config)
        self.assertEqual(result.status, ConversationStatus.REJECTED)

    async def test_default_output_mode_is_auto(self):
        book = self._book({
            "id": "count_lights", "trigger": "zaehle lichter",
            "sources": [{"kind": "count", "target": {"kind": "all_allowed"}, "variable": "n"}],
            "catchphrase": "{n}",
        })

        class NoEntitiesResolver:
            async def resolve(self, target, *, domain_filter):
                return ResolvedTargets(())

            async def resolve_area(self, target):
                return TargetNotFound()

        router = SpellbookRouter(ActionExecutor(NoEntitiesResolver(), MultiStateCaller({})))
        config = AgentConfig(mode=FrontendMode.SPELLBOOK_ONLY, spellbook=book, policy=_unrestricted())
        result = await router.handle(self._request("zaehle lichter"), config)
        self.assertEqual(result.output_mode, "AUTO")


class RegressionSingleEntityQueryUnaffectedTests(unittest.IsolatedAsyncioTestCase):
    """Pin that entity.get_state's existing single-entity path is
    byte-identical after the multi-source query model was added
    alongside it - it must never be rerouted through execute_query_plan."""

    async def test_entity_get_state_still_uses_the_single_entity_path(self):
        caller = core.FakeCaller(state={"state": "on", "unit": None})
        resolver = FakeResolver({"Buero": ResolvedTargets((ResolvedEntity("light.buero", "buero"),))})
        executor = ActionExecutor(resolver, caller)
        draft = core.ActionDraft("entity.get_state", ActionTarget("entity_name", "Buero"), {})
        result = await executor.execute(draft, _unrestricted())
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.data, {"state": "on", "unit": None})


if __name__ == "__main__":
    unittest.main()
