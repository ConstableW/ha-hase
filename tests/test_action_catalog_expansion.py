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
        # HA has no standalone "light.set_color" service - color is a
        # light.turn_on data field, same as effect (see the next test).
        result, caller = await self._run("light.set_color", {"rgb_color": [255, 0, 0]}, "light.lamp")
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls[0], ("light", "turn_on", ("light.lamp",), {"rgb_color": [255, 0, 0]}, None))

    async def test_light_set_color_temperature_uses_turn_on_service(self):
        # Same bug class as set_color: HA has no standalone
        # "light.set_color_temperature" service, and the real turn_on
        # field is "color_temp_kelvin", not the catalog's user-facing
        # "kelvin" argument key.
        result, caller = await self._run("light.set_color_temperature", {"kelvin": 3000}, "light.lamp")
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(
            caller.service_calls[0], ("light", "turn_on", ("light.lamp",), {"color_temp_kelvin": 3000}, None),
        )

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


# --- Generic HA service-call forwarding audit ---------------------------
#
# Regressions found and fixed alongside these tests (2026-09-15):
#   - light.set_color / light.set_color_temperature declared services
#     ("light.set_color" / "light.set_color_temperature") that do not
#     exist in Home Assistant - color and color temperature are both
#     light.turn_on data fields. Every call unconditionally failed with
#     service_call_rejected regardless of the chosen color/kelvin value.
#   - core/spellbook_engine.py's captured-argument resolver
#     (_resolve_arguments/_valid_arguments_spec) never had a "float"
#     branch, only "int" - climate.set_temperature, number.set_value and
#     input_number.set_value all declare a "float" capture in their own
#     user_choices, so any spell built from those Wizard presets failed
#     to even LOAD, let alone match or execute.
#   - providers/code27/bridge.py's all-lights bridge shortcut read
#     draft.arguments.get("on"), which is always None for a
#     percentage-based light.set_power draft - the real HA call was
#     correct, but the spoken confirmation always said "off" regardless
#     of the actual percentage (see test_unified_spellbooks.py).
#
# The two classes below are a generic, parameterized regression guard
# against the same bug CLASS recurring for a future operation: a wrong/
# invented HA service name, a silently dropped or renamed argument, or a
# capture type nothing actually resolves.


def _sample_arguments_for_spec(spec) -> dict:
    """One valid, in-range argument dict built directly from an
    OperationSpec's own declared types/ranges - independent of whether
    the operation has any user_choices, so this also covers Manual/
    Expert-only operations (lock.lock, light.set_effect, ...) that the
    simple Wizard picker doesn't offer a preset for."""
    sample: dict = {}
    for name, expected_type in spec.argument_types.items():
        if expected_type is bool:
            sample[name] = True
        elif expected_type is int:
            minimum, maximum = spec.int_ranges.get(name, (0, 100))
            sample[name] = (minimum + maximum) // 2
        elif expected_type is float:
            minimum, maximum = spec.float_ranges.get(name, (0.0, 100.0))
            sample[name] = (minimum + maximum) / 2
        elif expected_type is str:
            sample[name] = "sample"
        elif expected_type is list:
            sample[name] = [10, 20, 30]
    return sample


def _sample_capture_text(value_spec: dict) -> str:
    """The spoken/typed text a trigger capture would need to produce
    `value_spec`'s declared sample - mirrors core/studio.py's
    static_report() int/float midpoint sampling and studio.js's
    expertArgumentDefaults(), so a gap in any of the three (like the
    missing "float" branch found above) shows up here too."""
    if value_spec.get("type") in ("int", "float"):
        minimum, maximum = value_spec.get("min", 0), value_spec.get("max", 100)
        midpoint = (minimum + maximum) / 2
        return str(int(midpoint)) if midpoint == int(midpoint) else str(midpoint)
    for key in ("true_values", "false_values"):
        values = value_spec.get(key) or []
        if values:
            return values[0]
    return "sample"


# core/executor.py's _dispatch_action applies a small, explicit set of
# translations between the catalog's user-facing argument key/value and
# Home Assistant's real service data (see that module for the canonical
# list). Kept as an INDEPENDENT table here, not shared code, so a change
# to one without the other is caught by this test rather than silently
# staying in sync.
_EXPECTED_SERVICE_DATA_TRANSLATIONS = {
    "media_player.volume_set": lambda args: {"volume_level": args["volume_level"] / 10.0},
    "media_player.volume_mute": lambda args: {"is_volume_muted": True},
    "light.set_color_temperature": lambda args: {"color_temp_kelvin": args["kelvin"]},
}


def _expected_service_data(operation: str, resolved_arguments: dict) -> dict:
    translate = _EXPECTED_SERVICE_DATA_TRANSLATIONS.get(operation)
    return translate(resolved_arguments) if translate else dict(resolved_arguments)


# Dispatched through their own ServiceCaller method (skip_track) or a
# different resolution path entirely (a query, or area-based/provider-
# routed) - not the generic call_service forwarding these tests audit.
# Each already has its own dedicated coverage elsewhere.
_NOT_CALL_SERVICE_DISPATCHED = frozenset({"entity.get_state", "vacuum.clean_area"})


class FullCatalogDispatchContractTests(unittest.IsolatedAsyncioTestCase):
    """Every OPERATION_CATALOG entry, dispatched directly via a synthetic
    ActionDraft built from the operation's own declared argument types/
    ranges - covers Manual/Expert-only operations with no user_choices
    too (lock.lock, light.set_effect, climate.set_fan_mode, ...), so a
    future catalog entry with a wrong service mapping is caught even
    before anyone builds a Wizard preset for it."""

    async def test_every_catalog_entry_dispatches_correctly(self):
        for operation, spec in OPERATION_CATALOG.items():
            if operation in _NOT_CALL_SERVICE_DISPATCHED:
                continue
            domain = spec.domain_filter or "light"
            entity_id = f"{domain}.contract_target"
            arguments = _sample_arguments_for_spec(spec)
            with self.subTest(operation=operation):
                resolver = _resolver_for(entity_id)
                caller = FakeCallServiceCaller()
                executor = ActionExecutor(resolver, caller, expert_allowed=lambda: True)
                draft = ActionDraft(operation, ActionTarget("entity_id", entity_id), arguments)
                result = await executor.execute(draft, _scoped(AreaPolicy(allowed_areas=None, unrestricted=True)))
                self.assertEqual(result.status, ExecutionStatus.SUCCESS, f"{operation}: {result.reason_code}")

                if operation == "light.set_power":
                    domain_called, service_called, entity_ids_called, data_called, _ = caller.service_calls[-1]
                    self.assertEqual((domain_called, service_called), ("light", "turn_on"))
                    self.assertEqual(data_called, {"brightness_pct": arguments["percentage"]})
                    continue
                if operation == "media_player.skip_track":
                    self.assertEqual(caller.calls[-1], ((entity_id,), arguments["forward"]))
                    continue

                domain_called, service_called, entity_ids_called, data_called, _ = caller.service_calls[-1]
                self.assertEqual((domain_called, service_called), spec.service, operation)
                self.assertEqual(entity_ids_called, (entity_id,))
                self.assertEqual(data_called, _expected_service_data(operation, arguments), operation)


class WizardChoiceForwardingContractTests(unittest.IsolatedAsyncioTestCase):
    """Complements FullCatalogDispatchContractTests by walking every row
    of user_actions() (the Wizard's real action-picker data) through the
    REAL trigger -> capture -> build_draft -> execute chain, not a
    directly-constructed ActionDraft - this is the layer that had the
    "float" capture-type gap (climate.set_temperature, number.set_value,
    input_number.set_value all failed to even load a spell built from
    their own user_choices preset)."""

    async def test_every_wizard_user_choice_forwards_its_resolved_value(self):
        for row in studio_core.user_actions():
            operation = row["operation"]
            if operation in _NOT_CALL_SERVICE_DISPATCHED:
                continue
            spec = OPERATION_CATALOG[operation]
            domain = spec.domain_filter or "light"
            entity_id = f"{domain}.contract_target"

            captures: dict[str, str] = {}
            for value in row["arguments"].values():
                if isinstance(value, dict) and isinstance(value.get("capture"), str):
                    captures[value["capture"]] = _sample_capture_text(value)
            trigger = "do it" + "".join(f" {{{name}}}" for name in captures)

            command = {
                "id": "t", "trigger": trigger, "operation": operation,
                "target": {"kind": "entity_id", "value": entity_id},
                "arguments": row["arguments"],
            }
            with self.subTest(operation=operation, choice=row["label"]):
                book = core.spellbook_engine.load_spellbook_from_dict(
                    "t", {"id": "t", "name": "T", "commands": [command]},
                )
                self.assertIsNotNone(book, f"{operation}: spell failed to load")
                phrase = trigger.format(**captures)
                found = core.spellbook_engine.find_command(book, phrase, "de")
                self.assertIsNotNone(found, f"{operation}: no match for {phrase!r}")
                draft = found.action_draft
                self.assertIsNotNone(draft, f"{operation}: draft failed to resolve")

                resolver = _resolver_for(entity_id)
                caller = FakeCallServiceCaller()
                executor = ActionExecutor(resolver, caller, expert_allowed=lambda: True)
                result = await executor.execute(draft, _scoped(AreaPolicy(allowed_areas=None, unrestricted=True)))
                self.assertEqual(result.status, ExecutionStatus.SUCCESS, f"{operation}: {result.reason_code}")

                if operation == "media_player.skip_track":
                    self.assertEqual(caller.calls[-1], ((entity_id,), draft.arguments["forward"]))
                    continue
                if operation == "light.set_power":
                    # No explicit `service` (dispatched via its own
                    # early-return branch in _dispatch_action) - see
                    # FullCatalogDispatchContractTests above for the
                    # same check.
                    domain_called, service_called, entity_ids_called, data_called, _ = caller.service_calls[-1]
                    self.assertEqual((domain_called, service_called), ("light", "turn_on"))
                    self.assertEqual(data_called, {"brightness_pct": draft.arguments["percentage"]})
                    continue

                domain_called, service_called, entity_ids_called, data_called, _ = caller.service_calls[-1]
                self.assertEqual((domain_called, service_called), spec.service, operation)
                self.assertEqual(entity_ids_called, (entity_id,))
                self.assertEqual(data_called, _expected_service_data(operation, draft.arguments), operation)


class FloatCaptureRegressionTests(unittest.IsolatedAsyncioTestCase):
    """Dedicated regression coverage for the "float" capture-type gap
    (see the module comment above) - one explicit test per affected
    operation, in addition to the generic parameterized coverage above."""

    async def _dim_via_capture(self, operation, target_kind, target_value, arg_key, spec_extra, captured_text):
        command = {
            "id": "t", "trigger": "set {value}", "operation": operation,
            "target": {"kind": target_kind, "value": target_value},
            "arguments": {arg_key: {"capture": "value", "type": "float", **spec_extra}},
        }
        book = core.spellbook_engine.load_spellbook_from_dict("t", {"id": "t", "name": "T", "commands": [command]})
        self.assertIsNotNone(book, f"{operation}: spell with a float capture failed to load")
        found = core.spellbook_engine.find_command(book, f"set {captured_text}", "de")
        return found.action_draft if found is not None else None

    async def test_climate_set_temperature_float_capture_resolves_and_dispatches(self):
        draft = await self._dim_via_capture(
            "climate.set_temperature", "entity_id", "climate.living", "temperature",
            {"min": 5, "max": 35}, "21",
        )
        self.assertEqual(draft.arguments, {"temperature": 21.0})
        resolver = _resolver_for("climate.living")
        caller = FakeCallServiceCaller()
        result = await ActionExecutor(resolver, caller).execute(
            draft, _scoped(AreaPolicy(allowed_areas=None, unrestricted=True)),
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls[0][:4], ("climate", "set_temperature", ("climate.living",), {"temperature": 21.0}))

    async def test_number_set_value_float_capture_resolves_and_dispatches(self):
        draft = await self._dim_via_capture(
            "number.set_value", "entity_id", "number.target", "value", {"min": -1000, "max": 1000}, "42",
        )
        self.assertEqual(draft.arguments, {"value": 42.0})
        resolver = _resolver_for("number.target")
        caller = FakeCallServiceCaller()
        result = await ActionExecutor(resolver, caller).execute(
            draft, _scoped(AreaPolicy(allowed_areas=None, unrestricted=True)),
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.service_calls[0][:4], ("number", "set_value", ("number.target",), {"value": 42.0}))

    async def test_input_number_set_value_float_capture_resolves_and_dispatches(self):
        draft = await self._dim_via_capture(
            "input_number.set_value", "entity_id", "input_number.target", "value",
            {"min": -1000, "max": 1000}, "42",
        )
        self.assertEqual(draft.arguments, {"value": 42.0})

    async def test_out_of_range_float_capture_fails_to_resolve(self):
        draft = await self._dim_via_capture(
            "climate.set_temperature", "entity_id", "climate.living", "temperature",
            {"min": 5, "max": 35}, "50",
        )
        self.assertIsNone(draft)


if __name__ == "__main__":
    unittest.main()
