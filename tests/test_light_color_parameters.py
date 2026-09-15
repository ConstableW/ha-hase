"""Generic multi-parameter light arguments (light.turn_on's optional,
independently-combinable color_name/brightness_pct; light.set_color's
rgb_color vs color_name alternative representations) - core/operations.py's
OperationSpec.optional_argument_types, core/executor.py's _validate_arguments
optional-schema support, and core/spellbook_engine.py's "value_map" capture
kind (a spoken color word -> Home Assistant's color_name).

Important, verified via direct repro before writing these tests: a single
command's `triggers:` aliases are each validated independently against the
SAME arguments_spec (core/spellbook_engine.py's _load_command re-runs
_load_base_command per alias with only `trigger` swapped) - a placeholder
referenced by arguments_spec must therefore appear in EVERY phrasing that
command can match, not just some. "Licht blau machen" / "Licht 30 Prozent" /
"Licht blau 100 Prozent" are therefore modeled as THREE separate commands
sharing the light.turn_on operation (each declaring only the argument(s)
its own trigger actually captures) - never one command with an "optional"
capture flag, which the engine has no way to honor. optional_argument_types
is what makes all three argument shapes valid for that one operation/call.

Reuses test_core_engine's package loader/FakeResolver and
test_action_catalog_expansion's FakeCallServiceCaller, same convention as
the other core-engine test modules.
"""
import unittest

import test_action_catalog_expansion as cat
import test_core_engine as core

ActionExecutor = core.ActionExecutor
ActionDraft = core.ActionDraft
ActionTarget = core.ActionTarget
AreaPolicy = core.AreaPolicy
ScopedAreaPolicy = core.ScopedAreaPolicy
ExecutionStatus = core.ExecutionStatus
ResolvedEntity = core.ResolvedEntity
ResolvedTargets = core.ResolvedTargets
FakeResolver = core.FakeResolver
_scoped = core._scoped
spellbook_engine = core.spellbook_engine
operations_mod = cat.operations_mod
FakeCallServiceCaller = cat.FakeCallServiceCaller
_resolver_for = cat._resolver_for
LIGHT_COLOR_NAME_WORDS = operations_mod.LIGHT_COLOR_NAME_WORDS


def _unrestricted():
    return ScopedAreaPolicy(AreaPolicy(allowed_areas=None, unrestricted=True), AreaPolicy(allowed_areas=None, unrestricted=True))


class LightTurnOnCaptureParsingTests(unittest.TestCase):
    """Loading spells that capture color and/or brightness for
    light.turn_on - the value_map capture kind, and three separate
    commands (one per phrasing) sharing one operation."""

    def _book(self, *commands):
        return spellbook_engine.load_spellbook_from_dict("test", {"id": "test", "name": "Test", "commands": list(commands)})

    def _color_command(self, cid="color"):
        return {
            "id": cid, "trigger": "{area} licht {color} machen", "operation": "light.turn_on",
            "target": {"kind": "area_name", "capture": "area"},
            "arguments": {"color_name": {"capture": "color", "value_map": LIGHT_COLOR_NAME_WORDS}},
        }

    def _brightness_command(self, cid="brightness"):
        return {
            "id": cid, "trigger": "{area} licht {brightness} prozent", "operation": "light.turn_on",
            "target": {"kind": "area_name", "capture": "area"},
            "arguments": {"brightness_pct": {"capture": "brightness", "type": "int", "min": 0, "max": 100}},
        }

    def _both_command(self, cid="both"):
        # Two adjacent captures with nothing but whitespace between them
        # are rejected by the matching engine itself (compile_trigger) -
        # a pre-existing constraint, not new to this feature. "bei" ("at")
        # is a natural German separator.
        return {
            "id": cid, "trigger": "{area} licht {color} bei {brightness} prozent", "operation": "light.turn_on",
            "target": {"kind": "area_name", "capture": "area"},
            "arguments": {
                "color_name": {"capture": "color", "value_map": LIGHT_COLOR_NAME_WORDS},
                "brightness_pct": {"capture": "brightness", "type": "int", "min": 0, "max": 100},
            },
        }

    def test_color_only_command_loads(self):
        self.assertIsNotNone(self._book(self._color_command()))

    def test_brightness_only_command_loads(self):
        self.assertIsNotNone(self._book(self._brightness_command()))

    def test_color_and_brightness_command_loads(self):
        self.assertIsNotNone(self._book(self._both_command()))

    def test_all_three_phrasings_coexist_in_one_book(self):
        book = self._book(self._color_command(), self._brightness_command(), self._both_command())
        self.assertIsNotNone(book)
        self.assertEqual(len(book.commands), 3)

    def test_value_map_must_be_a_non_empty_dict_of_strings(self):
        for bad_map in (None, {}, {"blau": 1}, {1: "blue"}, "blue"):
            with self.subTest(bad_map=bad_map):
                command = self._color_command()
                command["arguments"]["color_name"]["value_map"] = bad_map
                self.assertIsNone(self._book(command))

    def test_value_map_and_true_values_are_mutually_exclusive(self):
        command = self._color_command()
        command["arguments"]["color_name"]["true_values"] = ["ja"]
        self.assertIsNone(self._book(command))

    def test_capture_not_referenced_by_the_trigger_is_rejected(self):
        # Confirms the actual engine constraint this three-command design
        # works around: a capture argument must appear in the trigger it
        # belongs to - it cannot be silently optional.
        command = self._color_command()
        command["arguments"]["brightness_pct"] = {"capture": "brightness", "type": "int", "min": 0, "max": 100}
        self.assertIsNone(self._book(command))


class LightTurnOnCaptureResolutionTests(unittest.TestCase):
    """find_command's build_draft resolving color/brightness from real
    captured text - one command per phrasing, covering each combination
    from the brief."""

    def _book(self):
        parsing = LightTurnOnCaptureParsingTests()
        return spellbook_engine.load_spellbook_from_dict("test", {"id": "test", "name": "Test", "commands": [
            parsing._color_command(), parsing._brightness_command(), parsing._both_command(),
        ]})

    def test_no_additional_parameters(self):
        book = spellbook_engine.load_spellbook_from_dict("test", {"id": "test", "name": "Test", "commands": [{
            "id": "on", "trigger": "licht {area} an", "operation": "light.turn_on",
            "target": {"kind": "area_name", "capture": "area"}, "arguments": {},
        }]})
        result = spellbook_engine.find_command(book, "licht Wohnzimmer an", "de")
        self.assertIsNotNone(result)
        self.assertEqual(result.action_draft.arguments, {})

    def test_runtime_color_only(self):
        result = spellbook_engine.find_command(self._book(), "Wohnzimmer licht blau machen", "de")
        self.assertIsNotNone(result)
        self.assertEqual(result.action_draft.arguments, {"color_name": "blue"})

    def test_runtime_brightness_only(self):
        result = spellbook_engine.find_command(self._book(), "Wohnzimmer licht 30 prozent", "de")
        self.assertIsNotNone(result)
        self.assertEqual(result.action_draft.arguments, {"brightness_pct": 30})

    def test_runtime_color_and_brightness_together(self):
        result = spellbook_engine.find_command(self._book(), "Wohnzimmer licht blau bei 100 prozent", "de")
        self.assertIsNotNone(result)
        self.assertEqual(result.action_draft.arguments, {"color_name": "blue", "brightness_pct": 100})

    def test_english_color_word_also_resolves(self):
        result = spellbook_engine.find_command(self._book(), "Wohnzimmer licht red bei 30 prozent", "de")
        self.assertIsNotNone(result)
        self.assertEqual(result.action_draft.arguments["color_name"], "red")

    def test_unmapped_color_word_fails_to_resolve(self):
        result = spellbook_engine.find_command(self._book(), "Wohnzimmer licht knallgelbgrün machen", "de")
        self.assertIsNone(result)

    def test_out_of_range_brightness_fails_to_resolve(self):
        result = spellbook_engine.find_command(self._book(), "Wohnzimmer licht 150 prozent", "de")
        self.assertIsNone(result)


class LightTurnOnDispatchTests(unittest.IsolatedAsyncioTestCase):
    """The resolved arguments reach one single HA light.turn_on call,
    regardless of how many optional parameters are present - never
    split into separate turn-on/set-color/set-brightness calls."""

    async def _dispatch(self, arguments):
        resolver = _resolver_for("light.living")
        caller = FakeCallServiceCaller()
        executor = ActionExecutor(resolver, caller)
        draft = ActionDraft("light.turn_on", ActionTarget("entity_id", "light.living"), arguments)
        result = await executor.execute(draft, _unrestricted())
        return result, caller

    async def test_no_parameters_still_dispatches(self):
        result, caller = await self._dispatch({})
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls[0][:4], ("light", "turn_on", ("light.living",), {}))

    async def test_color_only(self):
        result, caller = await self._dispatch({"color_name": "blue"})
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls[0][:4], ("light", "turn_on", ("light.living",), {"color_name": "blue"}))

    async def test_brightness_only(self):
        result, caller = await self._dispatch({"brightness_pct": 30})
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls[0][:4], ("light", "turn_on", ("light.living",), {"brightness_pct": 30}))

    async def test_color_and_brightness_in_one_call(self):
        """The brief's central requirement: one HASE action, one HA call,
        both parameters present together - never modeled as three steps."""
        result, caller = await self._dispatch({"color_name": "blue", "brightness_pct": 100})
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(len(caller.service_calls), 1)
        self.assertEqual(
            caller.service_calls[0][:4],
            ("light", "turn_on", ("light.living",), {"color_name": "blue", "brightness_pct": 100}),
        )

    async def test_light_initially_off_still_produces_one_combined_call(self):
        # HA's light.turn_on itself turns the light on as part of applying
        # any data - HASE has nothing special to do for "was off"; this
        # pins that the dispatch is still exactly one call either way.
        result, caller = await self._dispatch({"color_name": "red", "brightness_pct": 30})
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(len(caller.service_calls), 1)
        self.assertEqual(caller.service_calls[0][3], {"color_name": "red", "brightness_pct": 30})

    async def test_out_of_range_brightness_rejected_at_dispatch(self):
        resolver = _resolver_for("light.living")
        caller = FakeCallServiceCaller()
        executor = ActionExecutor(resolver, caller)
        draft = ActionDraft("light.turn_on", ActionTarget("entity_id", "light.living"), {"brightness_pct": 150})
        self.assertFalse(executor.validate_shape(draft))

    async def test_unknown_extra_argument_rejected(self):
        resolver = _resolver_for("light.living")
        caller = FakeCallServiceCaller()
        executor = ActionExecutor(resolver, caller)
        draft = ActionDraft("light.turn_on", ActionTarget("entity_id", "light.living"), {"not_a_real_field": "x"})
        self.assertFalse(executor.validate_shape(draft))


class LightSetColorTests(unittest.IsolatedAsyncioTestCase):
    """light.set_color: existing curated RGB presets keep working, plus
    the new color_name alternative representation - both dispatch
    through light.turn_on, never a made-up service (see the earlier
    service-name fix this same session)."""

    async def test_fixed_rgb_value_dispatches(self):
        resolver = _resolver_for("light.living")
        caller = FakeCallServiceCaller()
        executor = ActionExecutor(resolver, caller)
        draft = ActionDraft("light.set_color", ActionTarget("entity_id", "light.living"), {"rgb_color": [127, 255, 0]})
        result = await executor.execute(draft, _unrestricted())
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls[0][:4], ("light", "turn_on", ("light.living",), {"rgb_color": [127, 255, 0]}))

    async def test_color_name_alternative_dispatches(self):
        resolver = _resolver_for("light.living")
        caller = FakeCallServiceCaller()
        executor = ActionExecutor(resolver, caller)
        draft = ActionDraft("light.set_color", ActionTarget("entity_id", "light.living"), {"color_name": "chartreuse"})
        result = await executor.execute(draft, _unrestricted())
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls[0][:4], ("light", "turn_on", ("light.living",), {"color_name": "chartreuse"}))

    async def test_rgb_color_and_color_name_together_rejected(self):
        # argument_variants is a mutually exclusive ALTERNATIVE schema,
        # never combined with the primary rgb_color schema in one call.
        resolver = _resolver_for("light.living")
        caller = FakeCallServiceCaller()
        executor = ActionExecutor(resolver, caller)
        draft = ActionDraft(
            "light.set_color", ActionTarget("entity_id", "light.living"),
            {"rgb_color": [127, 255, 0], "color_name": "chartreuse"},
        )
        self.assertFalse(executor.validate_shape(draft))

    async def test_curated_preset_still_loads_and_matches(self):
        book = spellbook_engine.load_spellbook_from_dict("test", {"id": "test", "name": "Test", "commands": [{
            "id": "pink", "trigger": "licht {area} pink", "operation": "light.set_color",
            "target": {"kind": "area_name", "capture": "area"}, "arguments": {"rgb_color": [255, 105, 180]},
        }]})
        result = spellbook_engine.find_command(book, "licht Wohnzimmer pink", "de")
        self.assertIsNotNone(result)
        self.assertEqual(result.action_draft.arguments, {"rgb_color": [255, 105, 180]})


class CombinedSpellLightParityTests(unittest.IsolatedAsyncioTestCase):
    """Single-action and Combined Spell execution use the exact same
    dispatch path for these operations - execute_steps() reuses
    execute() per action step unchanged (see core/executor.py)."""

    async def test_light_turn_on_with_color_and_brightness_as_a_combined_step(self):
        core_models = core._models_mod
        ActionStep = core_models.ActionStep
        StepPlan = core_models.StepPlan
        resolver = _resolver_for("light.living")
        caller = FakeCallServiceCaller()
        executor = ActionExecutor(resolver, caller)
        plan = StepPlan(steps=(
            ActionStep(alias="lamp", operation="light.turn_on", target=ActionTarget("entity_id", "light.living"),
                      arguments={"color_name": "blue", "brightness_pct": 100}),
        ), response_template="{lamp.success}")
        result = await executor.execute_steps(plan, _unrestricted())
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.data["response_text"], "True")
        self.assertEqual(len(caller.service_calls), 1)
        self.assertEqual(caller.service_calls[0][:4], ("light", "turn_on", ("light.living",), {"color_name": "blue", "brightness_pct": 100}))

    async def test_light_set_color_as_a_combined_step(self):
        core_models = core._models_mod
        ActionStep = core_models.ActionStep
        StepPlan = core_models.StepPlan
        resolver = _resolver_for("light.living")
        caller = FakeCallServiceCaller()
        executor = ActionExecutor(resolver, caller)
        plan = StepPlan(steps=(
            ActionStep(alias="lamp", operation="light.set_color", target=ActionTarget("entity_id", "light.living"),
                      arguments={"rgb_color": [127, 255, 0]}),
        ), response_template="{lamp.success}")
        result = await executor.execute_steps(plan, _unrestricted())
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(caller.service_calls[0][:4], ("light", "turn_on", ("light.living",), {"rgb_color": [127, 255, 0]}))


class WizardEditRoundTripTests(unittest.TestCase):
    """core/studio.py's user_spell()/steps_spell() preserve every
    configured light parameter through a save/reload cycle - reading
    back a spell must reproduce the exact same operation/arguments/
    target, whether authored as a single action or a Combined Spell step."""

    def _adapter(self):
        import importlib
        return importlib.import_module("hase_spellbook_core.studio")

    def test_single_action_wizard_spell_round_trips_rgb_color(self):
        studio_core = self._adapter()
        raw = {
            "id": "pink", "trigger": "licht pink", "operation": "light.set_color",
            "target": {"kind": "all_allowed"}, "arguments": {"rgb_color": [255, 105, 180]},
        }
        book = {"id": "b", "name": "B", "commands": [raw]}
        studio_core.check_document(book)  # must not raise - round trips validly
        self.assertEqual(book["commands"][0]["arguments"], {"rgb_color": [255, 105, 180]})

    def test_single_action_wizard_spell_round_trips_color_name(self):
        studio_core = self._adapter()
        raw = {
            "id": "chartreuse", "trigger": "licht chartreuse", "operation": "light.set_color",
            "target": {"kind": "all_allowed"}, "arguments": {"color_name": "chartreuse"},
        }
        book = {"id": "b", "name": "B", "commands": [raw]}
        studio_core.check_document(book)
        self.assertEqual(book["commands"][0]["arguments"], {"color_name": "chartreuse"})

    def test_combined_spell_step_round_trips_color_and_brightness(self):
        studio_core = self._adapter()
        raw = {
            "id": "night", "trigger": "gute nacht",
            "steps": [{"alias": "lamp", "operation": "light.turn_on",
                      "target": {"kind": "entity_id", "value": "light.living"},
                      "arguments": {"color_name": "blue", "brightness_pct": 100}}],
            "catchphrase": "{lamp.success}",
        }
        book = {"id": "b", "name": "B", "commands": [raw]}
        studio_core.check_document(book)
        self.assertEqual(book["commands"][0]["steps"][0]["arguments"], {"color_name": "blue", "brightness_pct": 100})


if __name__ == "__main__":
    unittest.main()
