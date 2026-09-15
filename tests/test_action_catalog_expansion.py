"""Phase 1 of the Spellbook Studio capability expansion: new HA domains/
actions in core/operations.py's OPERATION_CATALOG, promoted USER-tier
operations that used to be Expert-gated, and the capability-driven metadata
(core/studio.py's entity_capability_flags/user_actions) the Wizard reads to
decide what to offer for a specific entity. See docs/SPELLBOOK-STUDIO.md.

Reuses test_core_engine's package loader and FakeResolver rather than
building a second one - only FakeCaller needed an addition (call_service),
since the shared one predates any operation that reaches the generic
direct-service dispatch path.
"""
import importlib
import unittest

import test_core_engine as core

operations_mod = importlib.import_module("hase_spellbook_core.operations")
studio_core = importlib.import_module("hase_spellbook_core.studio")

ActionExecutor = core.ActionExecutor
ActionDraft = core.ActionDraft
ActionTarget = core.ActionTarget
AreaPolicy = core.AreaPolicy
ExecutionStatus = core.ExecutionStatus
ResolvedEntity = core.ResolvedEntity
ResolvedTargets = core.ResolvedTargets
FakeResolver = core.FakeResolver
_scoped = core._scoped
OPERATION_CATALOG = operations_mod.OPERATION_CATALOG


class FakeCallServiceCaller(core.FakeCaller):
    """core.FakeCaller predates any operation reaching the generic
    call_service dispatch path (every pre-existing test either used
    light.set_power/media_player.skip_track/entity.get_state/
    vacuum.clean_area, or exercised call_service through a different fake
    elsewhere) - this adds it without touching the shared class."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.service_calls: list[tuple] = []

    async def call_service(self, domain, service, entity_ids, data, *, area_id=None, context=None):
        self.service_calls.append((domain, service, entity_ids, dict(data), area_id))
        self.contexts.append(context)
        return self._accept


def _resolver_for(entity_id: str, area_id: str = "living") -> FakeResolver:
    return FakeResolver({entity_id: ResolvedTargets((ResolvedEntity(entity_id, area_id),))})


class NewOperationsExecuteTests(unittest.IsolatedAsyncioTestCase):
    """One dispatch-shape test per newly-added domain, spot-checking the
    generic service-call path actually reaches the right (domain, service,
    data) - not exhaustive over every one of the ~50 catalog entries (they
    all funnel through the same generic dispatch code), but at least one
    per domain family plus every operation with non-trivial argument
    handling (float ranges, fixed-preset choices)."""

    async def _run(self, operation, arguments, entity_id="switch.lamp"):
        caller = FakeCallServiceCaller()
        executor = ActionExecutor(_resolver_for(entity_id), caller)
        draft = ActionDraft(operation, ActionTarget("entity_id", entity_id), arguments)
        result = await executor.execute(draft, _scoped(AreaPolicy(allowed_areas=None, unrestricted=True)))
        return result, caller

    async def test_switch_toggle_dispatches_generic_service(self):
        result, caller = await self._run("switch.toggle", {}, "switch.lamp")
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls, [("switch", "toggle", ("switch.lamp",), {}, None)])

    async def test_input_boolean_turn_on(self):
        result, caller = await self._run("input_boolean.turn_on", {}, "input_boolean.guest_mode")
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls[0][:2], ("input_boolean", "turn_on"))

    async def test_cover_set_position_dispatches_position_argument(self):
        result, caller = await self._run("cover.set_cover_position", {"position": 40}, "cover.blinds")
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls[0], ("cover", "set_cover_position", ("cover.blinds",), {"position": 40}, None))

    async def test_cover_set_position_out_of_range_rejected(self):
        result, _ = await self._run("cover.set_cover_position", {"position": 101}, "cover.blinds")
        self.assertEqual(result.status, ExecutionStatus.INVALID)
        self.assertEqual(result.reason_code, "bad_arguments")

    async def test_cover_tilt_operations_dispatch(self):
        result, caller = await self._run("cover.open_cover_tilt", {}, "cover.blinds")
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls[0][:2], ("cover", "open_cover_tilt"))

    async def test_climate_set_temperature_accepts_float(self):
        result, caller = await self._run("climate.set_temperature", {"temperature": 21.5}, "climate.living")
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls[0], ("climate", "set_temperature", ("climate.living",), {"temperature": 21.5}, None))

    async def test_climate_set_temperature_rejects_out_of_range(self):
        result, _ = await self._run("climate.set_temperature", {"temperature": 60.0}, "climate.living")
        self.assertEqual(result.status, ExecutionStatus.INVALID)
        self.assertEqual(result.reason_code, "bad_arguments")

    async def test_climate_set_temperature_rejects_non_numeric(self):
        result, _ = await self._run("climate.set_temperature", {"temperature": "warm"}, "climate.living")
        self.assertEqual(result.status, ExecutionStatus.INVALID)
        self.assertEqual(result.reason_code, "bad_arguments")

    async def test_climate_set_hvac_mode_dispatches_fixed_preset_value(self):
        result, caller = await self._run("climate.set_hvac_mode", {"hvac_mode": "heat"}, "climate.living")
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls[0], ("climate", "set_hvac_mode", ("climate.living",), {"hvac_mode": "heat"}, None))

    async def test_fan_oscillate_bool_argument(self):
        result, caller = await self._run("fan.oscillate", {"oscillating": True}, "fan.bedroom")
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls[0], ("fan", "oscillate", ("fan.bedroom",), {"oscillating": True}, None))

    async def test_media_player_select_source(self):
        result, caller = await self._run("media_player.select_source", {"source": "Spotify"}, "media_player.tv")
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls[0], ("media_player", "select_source", ("media_player.tv",), {"source": "Spotify"}, None))

    async def test_vacuum_start_stop_return_to_base(self):
        for op, service in (("vacuum.start", "start"), ("vacuum.stop", "stop"), ("vacuum.return_to_base", "return_to_base")):
            with self.subTest(op=op):
                result, caller = await self._run(op, {}, "vacuum.roomba")
                self.assertEqual(result.status, ExecutionStatus.SUCCESS)
                self.assertEqual(caller.service_calls[0][:2], ("vacuum", service))

    async def test_scene_script_automation_button_entity_only(self):
        for op, entity in (
            ("scene.turn_on", "scene.movie_night"),
            ("script.turn_on", "script.good_morning"),
            ("automation.trigger", "automation.lights_off"),
            ("button.press", "button.doorbell"),
        ):
            with self.subTest(op=op):
                result, caller = await self._run(op, {}, entity)
                self.assertEqual(result.status, ExecutionStatus.SUCCESS)
                self.assertEqual(caller.service_calls[0][2], (entity,))

    async def test_number_and_input_number_set_value(self):
        for op, entity in (("number.set_value", "number.target"), ("input_number.set_value", "input_number.target")):
            with self.subTest(op=op):
                result, caller = await self._run(op, {"value": 12.5}, entity)
                self.assertEqual(result.status, ExecutionStatus.SUCCESS)
                self.assertEqual(caller.service_calls[0][3], {"value": 12.5})

    async def test_select_and_input_select_option(self):
        for op, entity in (("select.select_option", "select.mode"), ("input_select.select_option", "input_select.mode")):
            with self.subTest(op=op):
                result, caller = await self._run(op, {"option": "Eco"}, entity)
                self.assertEqual(result.status, ExecutionStatus.SUCCESS)
                self.assertEqual(caller.service_calls[0][3], {"option": "Eco"})

    async def test_timer_lifecycle_operations(self):
        for op, service in (
            ("timer.start", "start"), ("timer.pause", "pause"),
            ("timer.cancel", "cancel"), ("timer.finish", "finish"),
        ):
            with self.subTest(op=op):
                result, caller = await self._run(op, {}, "timer.egg")
                self.assertEqual(result.status, ExecutionStatus.SUCCESS)
                self.assertEqual(caller.service_calls[0][:2], ("timer", service))

    async def test_light_set_color_dispatches_preset_rgb(self):
        result, caller = await self._run("light.set_color", {"rgb_color": [255, 0, 0]}, "light.lamp")
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls[0], ("light", "set_color", ("light.lamp",), {"rgb_color": [255, 0, 0]}, None))

    async def test_light_set_effect_uses_turn_on_service(self):
        result, caller = await self._run("light.set_effect", {"effect": "colorloop"}, "light.lamp")
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls[0], ("light", "turn_on", ("light.lamp",), {"effect": "colorloop"}, None))

    async def test_string_argument_over_length_bound_rejected(self):
        result, _ = await self._run("light.set_effect", {"effect": "x" * 200}, "light.lamp")
        self.assertEqual(result.status, ExecutionStatus.INVALID)
        self.assertEqual(result.reason_code, "bad_arguments")

    async def test_string_argument_empty_rejected(self):
        result, _ = await self._run("light.set_effect", {"effect": ""}, "light.lamp")
        self.assertEqual(result.status, ExecutionStatus.INVALID)
        self.assertEqual(result.reason_code, "bad_arguments")


class ExpertTierPromotionTests(unittest.IsolatedAsyncioTestCase):
    """Confirms the deliberate USER/EXPERT split: light color/kelvin and
    the cover/fan/media_player generic-service ops are promoted to
    USER-tier (executable/testable without the local Expert toggle,
    per the brief's item 13); lock.lock/unlock stay Expert-gated
    (physical security, not one of the promoted capabilities)."""

    def test_promoted_operations_are_not_expert_only(self):
        promoted = [
            "light.set_color", "light.set_color_temperature", "light.set_effect",
            "cover.open_cover", "cover.close_cover", "cover.set_cover_position",
            "climate.set_temperature", "climate.set_hvac_mode",
            "fan.turn_on", "fan.set_percentage", "fan.oscillate",
            "media_player.volume_up", "media_player.volume_set", "media_player.select_source",
            "vacuum.start", "scene.turn_on", "script.turn_on", "button.press",
            "switch.turn_on", "input_boolean.turn_on",
        ]
        for operation in promoted:
            with self.subTest(operation=operation):
                self.assertFalse(OPERATION_CATALOG[operation].expert_only)

    def test_lock_operations_remain_expert_gated(self):
        self.assertTrue(OPERATION_CATALOG["lock.lock"].expert_only)
        self.assertTrue(OPERATION_CATALOG["lock.unlock"].expert_only)

    async def test_promoted_operation_executes_without_expert_allowed(self):
        caller = FakeCallServiceCaller()
        executor = ActionExecutor(_resolver_for("light.lamp"), caller, expert_allowed=lambda: False)
        draft = ActionDraft("light.set_color", ActionTarget("entity_id", "light.lamp"), {"rgb_color": [255, 0, 0]})
        result = await executor.execute(draft, _scoped(AreaPolicy(allowed_areas=None, unrestricted=True)))
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)

    async def test_lock_denied_without_expert_allowed(self):
        caller = FakeCallServiceCaller()
        executor = ActionExecutor(_resolver_for("lock.front_door"), caller, expert_allowed=lambda: False)
        draft = ActionDraft("lock.lock", ActionTarget("entity_id", "lock.front_door"), {})
        result = await executor.execute(draft, _scoped(AreaPolicy(allowed_areas=None, unrestricted=True)))
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertEqual(result.reason_code, "expert_disabled")


class UserActionsMetadataTests(unittest.TestCase):
    def test_new_operations_appear_with_capability_hints(self):
        rows = {row["operation"]: row for row in studio_core.user_actions()}
        self.assertIn("cover.set_cover_tilt_position", rows)
        self.assertEqual(rows["cover.set_cover_tilt_position"]["capability_hint"], "tilt")
        self.assertEqual(rows["light.set_color"]["capability_hint"], "rgb")
        self.assertEqual(rows["climate.set_hvac_mode"]["capability_hint"], "hvac_modes")
        self.assertIsNone(rows["switch.turn_on"]["capability_hint"])

    def test_entity_only_operations_never_offer_area_targets(self):
        rows = {row["operation"]: row for row in studio_core.user_actions()}
        for operation in ("scene.turn_on", "script.turn_on", "automation.trigger", "button.press", "timer.start"):
            with self.subTest(operation=operation):
                self.assertNotIn("area_name", rows[operation]["targets"])
                self.assertNotIn("area_id", rows[operation]["targets"])

    def test_light_set_effect_and_select_options_reachable_but_not_in_simple_picker(self):
        # Documented follow-up: entity-specific enum values (effect/source/
        # option/fan preset) aren't offered as user_choices yet (no live
        # per-entity picker built this pass) - but the catalog entry exists
        # so Manual/Expert editing already works end-to-end.
        rows = {row["operation"] for row in studio_core.user_actions()}
        self.assertNotIn("light.set_effect", rows)
        self.assertNotIn("select.select_option", rows)
        self.assertIn("light.set_effect", OPERATION_CATALOG)
        self.assertIn("select.select_option", OPERATION_CATALOG)


class EntityCapabilityFlagsTests(unittest.TestCase):
    def test_light_reports_rgb_color_temp_and_effects(self):
        flags = studio_core.entity_capability_flags("light", {
            "supported_color_modes": ["color_temp", "hs"],
            "effect_list": ["colorloop", "fireplace"],
        })
        self.assertTrue(flags["rgb"])
        self.assertTrue(flags["color_temp"])
        self.assertEqual(flags["effect"], ["colorloop", "fireplace"])

    def test_light_without_rgb_support_reports_false(self):
        flags = studio_core.entity_capability_flags("light", {"supported_color_modes": ["onoff"]})
        self.assertFalse(flags["rgb"])
        self.assertFalse(flags["color_temp"])
        self.assertEqual(flags["effect"], [])

    def test_cover_tilt_bit_flags(self):
        # SET_TILT_POSITION=128 only
        flags = studio_core.entity_capability_flags("cover", {"supported_features": 128})
        self.assertTrue(flags["tilt"])
        self.assertFalse(flags["position"])
        # SET_POSITION=4 only
        flags = studio_core.entity_capability_flags("cover", {"supported_features": 4})
        self.assertTrue(flags["position"])
        self.assertFalse(flags["tilt"])

    def test_climate_reports_declared_mode_lists(self):
        flags = studio_core.entity_capability_flags("climate", {
            "hvac_modes": ["heat", "off"], "preset_modes": ["eco"], "fan_modes": [],
        })
        self.assertEqual(flags["hvac_modes"], ["heat", "off"])
        self.assertEqual(flags["preset_modes"], ["eco"])
        self.assertEqual(flags["fan_modes"], [])

    def test_unknown_domain_returns_empty_flags(self):
        self.assertEqual(studio_core.entity_capability_flags("sensor", {"unit_of_measurement": "°C"}), {})

    def test_missing_or_malformed_attributes_do_not_crash(self):
        self.assertEqual(studio_core.entity_capability_flags("light", {}), {"rgb": False, "color_temp": False, "effect": []})
        self.assertEqual(studio_core.entity_capability_flags("cover", None), {"position": False, "tilt": False})


if __name__ == "__main__":
    unittest.main()
