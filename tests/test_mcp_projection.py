"""HASE MCP architecture, Phase 1: core.mcp_projection table-driven tests.

core/mcp_projection.py has zero Home Assistant imports (same boundary rule
as the rest of core/), so it is loaded exactly like core.spellbook_engine/
core.models already are elsewhere in this suite - see test_bridge_delivery.
py's own `hase_spellbook_core` package alias, reproduced here standalone so
this file has no dependency on test collection order.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "hase"

_core_spec = importlib.util.spec_from_file_location(
    "hase_spellbook_core", ROOT / "core/__init__.py",
    submodule_search_locations=[str(ROOT / "core")],
)
if _core_spec.name not in sys.modules:
    _core_module = importlib.util.module_from_spec(_core_spec)
    sys.modules[_core_spec.name] = _core_module
    _core_spec.loader.exec_module(_core_module)

engine = importlib.import_module("hase_spellbook_core.spellbook_engine")
models = importlib.import_module("hase_spellbook_core.models")
mcp = importlib.import_module("hase_spellbook_core.mcp_projection")

AreaPolicy = models.AreaPolicy
ScopedAreaPolicy = models.ScopedAreaPolicy


def book(*commands, book_id: str = "test") -> engine.EngineSpellbook:
    raw = {"id": book_id, "name": "Test", "commands": list(commands)}
    loaded = engine.load_spellbook_from_dict(book_id, raw)
    assert loaded is not None, "test fixture spellbook failed to load"
    return loaded


def one(book_obj: engine.EngineSpellbook) -> engine.EngineCommand:
    assert len(book_obj.commands) == 1, [c.spell_id for c in book_obj.commands]
    return book_obj.commands[0]


def policy(allowed=(), unrestricted=False) -> AreaPolicy:
    return AreaPolicy(allowed_areas=frozenset(allowed), unrestricted=unrestricted)


def scoped(actions: AreaPolicy, queries: AreaPolicy | None = None) -> ScopedAreaPolicy:
    return ScopedAreaPolicy(actions=actions, queries=queries or actions)


def make_area(name: str, area_id: str | None = None) -> mcp.BridgeArea:
    """A real Home Assistant area's id and display name are usually
    DIFFERENT strings (Phase 3's binding correction - see
    core.mcp_projection.BridgeArea's own docstring); this derives a
    plausible id from the name only so fixtures stay short, never because
    the two are actually the same value in general."""
    return mcp.BridgeArea(area_id=area_id or name.lower().replace(" ", "_"), area_name=name)


# Bridge context fixtures matching the architecture assessment's own
# CASE A (room-bound single area) / B (master, no area) / C (own area +
# wider/unrestricted scope) naming throughout. AreaPolicy.allowed_areas
# always holds the area's id (matching core.executor's own convention),
# never its display name.
def bridge_a(area="Jans Zimmer", language="de") -> mcp.BridgeContext:
    a = make_area(area) if isinstance(area, str) else area
    return mcp.BridgeContext(area=a, policy=scoped(policy(allowed=[a.area_id])), language=language)


def bridge_b(language="de") -> mcp.BridgeContext:
    return mcp.BridgeContext(area=None, policy=scoped(policy(unrestricted=True)), language=language)


def bridge_c(area="Wohnzimmer", language="de") -> mcp.BridgeContext:
    a = make_area(area) if isinstance(area, str) else area
    return mcp.BridgeContext(area=a, policy=scoped(policy(unrestricted=True)), language=language)


LUMOS = {
    "id": "lumos",
    "trigger": "lumos {area}",
    "operation": "light.set_power",
    "target": {"kind": "area_name", "capture": "area"},
    "arguments": {"on": True},
}


class ConcreteEffectDescriptionTests(unittest.TestCase):
    def project(self, arguments, description=None, language="de", spell_id="arbitrary"):
        raw = {**LUMOS, "id": spell_id, "arguments": arguments}
        if description is not None:
            raw["description"] = description
        return mcp.project_command(one(book(raw)), bridge_b(language))

    def test_fixed_power_is_distinct_for_arbitrary_names(self):
        for language, on_text, off_text in (
            ("de", "Schaltet Licht ein.", "Schaltet Licht aus."),
            ("en", "Turns lights on.", "Turns lights off."),
        ):
            on = self.project({"on": True}, language=language, spell_id="unrelated_a")
            off = self.project({"on": False}, language=language, spell_id="unrelated_b")
            self.assertTrue(on.description.startswith(on_text))
            self.assertTrue(off.description.startswith(off_text))
            self.assertEqual(on.input_schema, off.input_schema)
            self.assertFalse(on.read_only)
            self.assertFalse(off.read_only)

    def test_localized_author_description_and_regional_language(self):
        description = {"de": "Beleuchtet die Halle.", "en": "Illuminates the hall."}
        command = one(book({**LUMOS, "description": description}))
        self.assertEqual(command.description, description)
        self.assertIn("Beleuchtet die Halle.", self.project({"on": True}, description, "de-DE").description)
        self.assertIn("Illuminates the hall.", self.project({"on": True}, description, "en").description)

    def test_missing_translation_uses_shared_english_fallback(self):
        result = self.project({"on": False}, {"en": "Darkens the hall."})
        self.assertTrue(result.description.startswith("Schaltet Licht aus."))
        self.assertIn("Darkens the hall.", result.description)

    def test_shared_description_and_no_description_fallback(self):
        self.assertIn("My description.", self.project({"on": True}, "My description.").description)
        self.assertTrue(self.project({"on": False}).description.startswith("Schaltet Licht aus."))
        self.assertTrue(self.project({"on": True}, 123).description.startswith("Schaltet Licht ein."))

    def test_fixed_brightness_matches_executor_zero_and_nonzero(self):
        for language in ("de", "en"):
            zero = self.project({"percentage": 0}, language=language)
            positive = self.project({"percentage": 35}, language=language)
            self.assertTrue(zero.description.startswith(
                "Schaltet Licht aus." if language == "de" else "Turns lights off."))
            self.assertIn("35", positive.description)
            self.assertIn("Helligkeit" if language == "de" else "brightness", positive.description)

    def test_captured_brightness_describes_zero_behavior(self):
        raw = {**LUMOS, "trigger": "dim {area} to {value}",
               "arguments": {"percentage": {"capture": "value", "type": "int", "min": 0, "max": 100}}}
        for language in ("de", "en"):
            result = mcp.project_command(one(book(raw)), bridge_b(language))
            self.assertIn("0", result.description)
            self.assertIn("ausgeschaltet" if language == "de" else "turns lights off", result.description)

    def test_description_preserved_for_query_steps_and_text(self):
        forms = (
            {"output": "Hello", "assist_redirect": False},
            {"sources": [{"variable": "n", "kind": "count", "domain_filter": "light",
                          "target": {"kind": "all_allowed"}}], "output": "{n}"},
            {"steps": [{"alias": "a", "operation": "light.set_power",
                        "target": {"kind": "all_allowed"}, "arguments": {"on": True}}]},
        )
        for form in forms:
            command = one(book({"id": "generic", "trigger": "hello", "description": "Details.", **form}))
            self.assertEqual(command.description, "Details.")
            self.assertIn("Details.", mcp.project_command(command, bridge_b()).description)


class CaseABCTests(unittest.TestCase):
    """Room-bound / master / own-area+wider-scope projection of the SAME
    Spell, for the SAME tool name - the architecture assessment's central
    claim, and the corrected instruction that description and inputSchema
    are always projected together from the same Bridge-context decision."""

    def setUp(self):
        self.book = book(LUMOS)
        self.command = one(self.book)

    def test_case_a_room_bound_has_no_target_argument(self):
        result = mcp.project_command(self.command, bridge_a())
        self.assertIsInstance(result, mcp.McpToolProjection)
        self.assertEqual(result.name, "lumos")
        self.assertEqual(result.input_schema, {"type": "object", "properties": {}})
        self.assertEqual(result.arguments, ())
        self.assertEqual(
            result.description,
            "Schaltet Licht ein. Beispiel-Sprachbefehl: 'lumos'.",
        )

    def test_case_b_master_requires_target(self):
        result = mcp.project_command(self.command, bridge_b())
        self.assertIsInstance(result, mcp.McpToolProjection)
        self.assertEqual(result.name, "lumos")
        self.assertEqual(
            result.input_schema,
            {"type": "object", "properties": {"area": {"type": "string", "description": mcp._ARG_TEXT["de"]["area_required"]}},
             "required": ["area"]},
        )
        self.assertEqual(
            result.description,
            "Schaltet Licht ein. Beispiel-Sprachbefehl: 'lumos <area>'.",
        )
        self.assertNotIn("Ohne genannten", result.description)

    def test_case_c_own_area_wider_scope_target_optional_with_default(self):
        result = mcp.project_command(self.command, bridge_c())
        self.assertIsInstance(result, mcp.McpToolProjection)
        self.assertEqual(result.name, "lumos")
        self.assertNotIn("required", result.input_schema)
        self.assertEqual(result.input_schema["properties"]["area"]["type"], "string")
        [arg] = result.arguments
        self.assertFalse(arg.required)
        self.assertEqual(arg.implicit_default, "Wohnzimmer")
        self.assertIn("'lumos'", result.description)
        self.assertIn("'lumos <area>'", result.description)
        self.assertIn("Ohne genannten", result.description)

    def test_same_tool_name_on_every_bridge(self):
        names = {mcp.project_command(self.command, b).name for b in (bridge_a(), bridge_b(), bridge_c())}
        self.assertEqual(names, {"lumos"})

    def test_english_language_bridge(self):
        result = mcp.project_command(self.command, bridge_c(language="en"))
        self.assertIn("Turns lights on.", result.description)
        self.assertIn("Example voice command", result.description)
        self.assertIn("If no value is named", result.description)


class ArgumentTypeTests(unittest.TestCase):
    """int / float / boolean-as-enum / value_map captures, plus entity
    captures - every genuine-user-capture shape core.spellbook_engine's
    _resolve_arguments itself supports."""

    def test_int_capture(self):
        b = book({
            "id": "raise_blind", "trigger": "blind {area} to {value} percent",
            "operation": "cover.set_cover_position",
            "target": {"kind": "area_name", "capture": "area"},
            "arguments": {"position": {"capture": "value", "type": "int", "min": 0, "max": 100}},
        })
        result = mcp.project_command(one(b), bridge_a())
        prop = result.input_schema["properties"]["value"]
        self.assertEqual(prop["type"], "integer")
        self.assertEqual(prop["minimum"], 0)
        self.assertEqual(prop["maximum"], 100)
        self.assertIn("value", result.input_schema["required"])

    def test_float_capture(self):
        b = book({
            "id": "set_temp", "trigger": "temperature {area} to {value} degrees",
            "operation": "climate.set_temperature",
            "target": {"kind": "area_name", "capture": "area"},
            "arguments": {"temperature": {"capture": "value", "type": "float", "min": 5, "max": 35}},
        })
        result = mcp.project_command(one(b), bridge_a())
        prop = result.input_schema["properties"]["value"]
        self.assertEqual(prop["type"], "number")
        self.assertEqual(prop["minimum"], 5)
        self.assertEqual(prop["maximum"], 35)

    def test_boolean_capture_projects_as_word_enum_not_native_boolean(self):
        b = book({
            "id": "toggle_light", "trigger": "light {area} turn {state}",
            "operation": "light.set_power",
            "target": {"kind": "area_name", "capture": "area"},
            "arguments": {"on": {"capture": "state", "true_values": ["an", "ein"], "false_values": ["aus"]}},
        })
        result = mcp.project_command(one(b), bridge_a())
        prop = result.input_schema["properties"]["state"]
        self.assertEqual(prop["type"], "string")
        self.assertEqual(set(prop["enum"]), {"an", "ein", "aus"})
        self.assertIn("state", result.input_schema["required"])

    def test_value_map_capture(self):
        b = book({
            "id": "color_light", "trigger": "light {area} color {farbe}",
            "operation": "light.turn_on",
            "target": {"kind": "area_name", "capture": "area"},
            "arguments": {"color_name": {"capture": "farbe", "value_map": {"rot": "red", "blau": "blue"}}},
        })
        result = mcp.project_command(one(b), bridge_a())
        prop = result.input_schema["properties"]["farbe"]
        self.assertEqual(prop["type"], "string")
        self.assertEqual(set(prop["enum"]), {"rot", "blau"})

    def test_entity_capture_always_required_regardless_of_bridge(self):
        b = book({
            "id": "query_state", "trigger": "state of {entity}",
            "operation": "entity.get_state",
            "target": {"kind": "entity_name", "capture": "entity"},
        })
        c = one(b)
        for bridge in (bridge_a(), bridge_b(), bridge_c()):
            with self.subTest(bridge=bridge.area):
                result = mcp.project_command(c, bridge)
                self.assertIn("entity", result.input_schema["required"])
                self.assertEqual(result.input_schema["properties"]["entity"]["type"], "string")


class ContextualTargetTests(unittest.TestCase):
    """Fixed/contextual targets must never become MCP arguments, for any
    Bridge - the architecture assessment's "no generic target_area
    parameter" ruling, and source_area specifically (HASE's OWN existing
    speaking-device-area mechanism, entirely separate from the area_name/
    area_id capture mechanism this module projects)."""

    def _assert_no_arguments(self, raw_command):
        b = book(raw_command)
        c = one(b)
        for bridge in (bridge_a(), bridge_b(), bridge_c()):
            with self.subTest(bridge=bridge.area, command=raw_command["id"]):
                result = mcp.project_command(c, bridge)
                self.assertIsInstance(result, mcp.McpToolProjection)
                self.assertEqual(result.input_schema, {"type": "object", "properties": {}})
                self.assertEqual(result.arguments, ())

    def test_all_allowed_target(self):
        self._assert_no_arguments({
            "id": "lumos_maxima", "trigger": "lumos maxima",
            "operation": "light.set_power", "target": {"kind": "all_allowed"}, "arguments": {"on": True},
        })

    def test_source_area_target(self):
        self._assert_no_arguments({
            "id": "clean_here", "trigger": "clean here",
            "operation": "vacuum.clean_area", "target": {"kind": "source_area"},
        })

    def test_fixed_value_area_target(self):
        self._assert_no_arguments({
            "id": "lumos_wohnzimmer", "trigger": "lumos wohnzimmer",
            "operation": "light.set_power",
            "target": {"kind": "area_name", "value": "Wohnzimmer"}, "arguments": {"on": True},
        })

    def test_entity_ids_target(self):
        self._assert_no_arguments({
            "id": "fixed_lights", "trigger": "fixed lights on",
            "operation": "light.set_power",
            "target": {"kind": "entity_ids", "value": ["light.a", "light.b"]}, "arguments": {"on": True},
        })

    def test_template_target_is_projected_not_excluded(self):
        # Corrected architecture requirement: no MCP-specific category
        # exclusion for expert-only/template-targeted Spells - this must
        # still produce a tool, just with zero target arguments (a
        # template's `value` is a fixed string, never a capture).
        self._assert_no_arguments({
            "id": "expert_lumos", "trigger": "expert lumos",
            "operation": "light.set_power",
            "target": {"kind": "template", "value": "{{ states.light | map(attribute='entity_id') | list }}"},
            "arguments": {"on": True},
        })


class ExpertOnlyIsProjectedTests(unittest.TestCase):
    """A plain expert-only command (no user tier at all) is projected like
    any other typed command - core.executor's own expert-tier gating
    authorizes the call, this module invents no second permission model."""

    def test_expert_only_operation_is_projected(self):
        b = book({
            "id": "lock_door", "trigger": "lock {area}",
            "operation": "lock.lock",  # expert_only=True in OPERATION_CATALOG
            "target": {"kind": "area_name", "capture": "area"},
        })
        result = mcp.project_command(one(b), bridge_b())
        self.assertIsInstance(result, mcp.McpToolProjection)
        self.assertEqual(result.name, "lock_door")
        self.assertIn("area", result.input_schema["required"])


class QueryPolicyViewTests(unittest.TestCase):
    """A target's action-vs-query policy view is read from
    core.operations.OPERATION_CATALOG, never guessed - queries and actions
    genuinely can differ per Bridge (a Bridge might answer status queries
    anywhere while staying restricted on where it may act)."""

    def test_action_and_query_policies_diverge_for_the_same_shaped_capture(self):
        multi_source_query = {
            "id": "lights_on_count", "trigger": "how many lights on in {area}",
            "sources": [
                {"kind": "count", "target": {"kind": "area_name", "capture": "area"},
                 "domain_filter": "light", "state_filter": "on", "variable": "n"},
            ],
            "output": "{n}",
        }
        b = book(multi_source_query)
        c = one(b)
        # actions locked to Wohnzimmer only; queries unrestricted - same
        # capture name, same command, deliberately different policies.
        wz = make_area("Wohnzimmer")
        bridge = mcp.BridgeContext(
            area=wz,
            policy=scoped(actions=policy(allowed=[wz.area_id]), queries=policy(unrestricted=True)),
        )
        result = mcp.project_command(c, bridge)
        self.assertIsInstance(result, mcp.McpToolProjection)
        [arg] = result.arguments
        # queries=unrestricted -> CASE C (optional), not CASE A (omitted) -
        # proves the query view, not the action view, governed this target.
        self.assertFalse(arg.required)
        self.assertEqual(arg.implicit_default, "Wohnzimmer")


class MultiSourceAndCombinedSpellTests(unittest.TestCase):
    def test_multi_source_query_aggregation(self):
        b = book({
            "id": "avg_temp", "trigger": "average temperature in {area}",
            "sources": [
                {"kind": "avg", "target": {"kind": "area_name", "capture": "area"},
                 "domain_filter": "sensor", "device_class": "temperature", "variable": "t"},
            ],
            "output": "{t}",
        })
        result = mcp.project_command(one(b), bridge_a())
        self.assertEqual(result.name, "avg_temp")
        self.assertEqual(result.input_schema, {"type": "object", "properties": {}})

    def test_combined_spell_shared_capture_merges_across_steps(self):
        combined = {
            "id": "clean_and_report", "trigger": "clean {area} and report",
            "steps": [
                {"alias": "cleaning", "operation": "vacuum.clean_area", "target": {"kind": "area_name", "capture": "area"}},
                {"alias": "lights", "sources": [
                    {"kind": "count", "target": {"kind": "area_name", "capture": "area"},
                     "domain_filter": "light", "state_filter": "on", "variable": "n"},
                ]},
            ],
            "output": "{cleaning.success_count} {lights.n}",
        }
        b = book(combined)
        c = one(b)

        # Master (no area): every occurrence is CASE B -> required.
        result_master = mcp.project_command(c, bridge_b())
        self.assertIn("area", result_master.input_schema["required"])

        # Room-bound single area, matching BOTH the action and query
        # policy -> every occurrence agrees CASE A -> omitted entirely.
        buero = make_area("Buero")
        room_bound = mcp.BridgeContext(area=buero, policy=scoped(policy(allowed=[buero.area_id])))
        result_room = mcp.project_command(c, room_bound)
        self.assertEqual(result_room.arguments, ())

        # Own area, but ONLY the query view is wider (CASE C) while the
        # action view is locked to that one area (CASE A) - merge priority
        # REQUIRED > OPTIONAL > OMIT means the shared capture must still
        # surface as optional, never silently disappear.
        mixed = mcp.BridgeContext(
            area=buero,
            policy=scoped(actions=policy(allowed=[buero.area_id]), queries=policy(unrestricted=True)),
        )
        result_mixed = mcp.project_command(c, mixed)
        [arg] = result_mixed.arguments
        self.assertFalse(arg.required)
        self.assertEqual(arg.implicit_default, "Buero")

    def test_combined_spell_action_step_argument_capture(self):
        combined = {
            "id": "dim_and_lock", "trigger": "dim {area} to {value} percent and lock",
            "steps": [
                {"alias": "dim", "operation": "light.set_power", "target": {"kind": "area_name", "capture": "area"},
                 "arguments": {"percentage": {"capture": "value", "type": "int", "min": 0, "max": 100}}},
                {"alias": "lock", "operation": "lock.lock", "target": {"kind": "area_name", "capture": "area"}},
            ],
            "output": "{dim.success_count} {lock.success_count}",
        }
        b = book(combined)
        result = mcp.project_command(one(b), bridge_b())
        self.assertEqual(result.input_schema["properties"]["value"]["type"], "integer")
        self.assertIn("area", result.input_schema["required"])
        self.assertIn("value", result.input_schema["required"])


class ProjectionSkipTests(unittest.TestCase):
    def test_unknown_operation_is_skipped_not_silently_listed(self):
        b = book({
            "id": "typo_op", "trigger": "does something",
            "operation": "light.set_powerr",  # typo, not in OPERATION_CATALOG
            "target": {"kind": "all_allowed"}, "arguments": {},
        })
        result = mcp.project_command(one(b), bridge_a())
        self.assertIsInstance(result, mcp.ProjectionSkip)
        self.assertTrue(result.reason.startswith("unknown_operation:"))

    def test_conflicting_capture_definition_is_skipped(self):
        combined = {
            "id": "conflict", "trigger": "do {x} things to {y}",
            "steps": [
                {"alias": "a", "operation": "light.set_power", "target": {"kind": "area_name", "capture": "x"}},
                {"alias": "b", "operation": "cover.set_cover_position", "target": {"kind": "area_name", "capture": "y"},
                 "arguments": {"position": {"capture": "x", "type": "int", "min": 0, "max": 100}}},
            ],
            "output": "done",
        }
        b = book(combined)
        result = mcp.project_command(one(b), bridge_a())
        self.assertIsInstance(result, mcp.ProjectionSkip)
        self.assertTrue(result.reason.startswith("conflicting_capture_definition:"))

    def test_project_spellbook_reports_skips_separately_from_projections(self):
        good = dict(LUMOS)
        bad = {
            "id": "typo_op2", "trigger": "does something else",
            "operation": "not_a_real_operation",
            "target": {"kind": "all_allowed"}, "arguments": {},
        }
        b = book(good, bad)
        projections, skips = mcp.project_spellbook(b, bridge_a())
        self.assertEqual([p.spell_id for p in projections], ["lumos"])
        self.assertEqual([s.spell_id for s in skips], ["typo_op2"])


class DuplicateSpellIdTests(unittest.TestCase):
    """The spellbook loader never enforces spell_id uniqueness (an
    authoring mistake, not a load failure) - MCP projection must not
    silently expose two same-named tools, and must not silently pick one
    to execute either (see BuildCapturesRoundTripTests/test_mcp_execution.
    py's own FindCommandByIdTests for the execution-side half)."""

    def _duplicate_book(self):
        first = {
            "id": "dup", "trigger": "lumos {area}",
            "operation": "light.set_power",
            "target": {"kind": "area_name", "capture": "area"}, "arguments": {"on": True},
        }
        second = {
            "id": "dup", "trigger": "nox {area}",
            "operation": "light.set_power",
            "target": {"kind": "area_name", "capture": "area"}, "arguments": {"on": False},
        }
        return book(first, second)

    def test_duplicate_spell_id_detected_at_the_engine_level(self):
        b = self._duplicate_book()
        self.assertEqual(engine.duplicate_spell_ids(b), frozenset({"dup"}))

    def test_neither_conflicting_command_is_exposed_as_a_tool(self):
        b = self._duplicate_book()
        projections, skips = mcp.project_spellbook(b, bridge_a())
        self.assertEqual(projections, ())
        self.assertEqual(len(skips), 2)
        self.assertTrue(all(s.spell_id == "dup" and s.reason == "duplicate_spell_id" for s in skips))

    def test_a_book_without_conflicts_is_unaffected(self):
        b = book(LUMOS, {**LUMOS, "id": "nox", "trigger": "nox {area}", "arguments": {"on": False}})
        self.assertEqual(engine.duplicate_spell_ids(b), frozenset())
        projections, skips = mcp.project_spellbook(b, bridge_a())
        self.assertEqual({p.spell_id for p in projections}, {"lumos", "nox"})
        self.assertEqual(skips, ())


class SpellbookLevelExclusionTests(unittest.TestCase):
    """A disabled command is silently absent from project_spellbook - the
    same invisibility it already has everywhere else, not a new MCP-only
    rule. A pure free-text/output-only command is NOT excluded (unlike
    SATELLITE/voice's own typed_only filter) - it projects as a read-only
    text tool instead, see TextOnlySpellProjectionTests below."""

    def test_disabled_command_excluded(self):
        b = book({**LUMOS, "enabled": False})
        projections, skips = mcp.project_spellbook(b, bridge_a())
        self.assertEqual(projections, ())
        self.assertEqual(skips, ())

    def test_output_only_command_is_projected_as_a_read_only_text_tool(self):
        b = book({
            "id": "free_text", "trigger": "tell me a joke",
            "output": {"de": "Warum...", "en": "Why..."}, "assist_redirect": False,
        })
        projections, skips = mcp.project_spellbook(b, bridge_a())
        self.assertEqual(skips, ())
        [tool] = projections
        self.assertEqual(tool.name, "free_text")
        self.assertTrue(tool.read_only)
        self.assertEqual(tool.input_schema, {"type": "object", "properties": {}})

    def test_assist_redirect_command_is_also_projected_never_excluded(self):
        # Unlike SATELLITE/voice's own typed_only filter, an
        # assist_redirect=True command is not invisible to MCP either -
        # core.mcp_execution actually forwards it to Home Assistant's own
        # conversation agent at call time (see
        # TextOnlySpellProjectionTests.test_assist_redirect_projects_as_a_
        # real_non_read_only_tool), so it must appear in tools/list too.
        b = book({"id": "sunrise", "trigger": "sunrise over {area}", "output": "Set light {area} to 100%."})
        projections, skips = mcp.project_spellbook(b, bridge_a())
        self.assertEqual(skips, ())
        [tool] = projections
        self.assertEqual(tool.name, "sunrise")
        self.assertFalse(tool.read_only)


class BuildCapturesRoundTripTests(unittest.TestCase):
    """The reverse mapping, proven to actually feed a real ActionDraft/
    QueryPlan via build_draft/build_query_plan - not just shape assertions
    on the schema."""

    def test_case_a_synthesizes_default_and_builds_a_real_draft(self):
        b = book(LUMOS)
        c = one(b)
        bridge = bridge_a(area="Jans Zimmer")
        projection = mcp.project_command(c, bridge)
        # CASE A means the argument itself is omitted from inputSchema
        # entirely (no McpArgument at all - see test_case_a_room_bound_has_
        # no_target_argument), but its value still has to reach the engine
        # somehow: McpToolProjection.implicit_captures carries it, and
        # build_captures seeds the result from it before anything else -
        # exactly as if the user had spoken the Bridge's own area.
        self.assertEqual(projection.implicit_captures, {"area": "Jans Zimmer"})
        captures = mcp.build_captures(projection, {})
        self.assertEqual(captures, {"area": "Jans Zimmer"})
        draft = engine.build_draft(c, captures, "de")
        self.assertIsNotNone(draft)
        self.assertEqual(draft.target.value, "Jans Zimmer")

    def test_case_b_missing_required_argument_returns_none(self):
        b = book(LUMOS)
        projection = mcp.project_command(one(b), bridge_b())
        self.assertIsNone(mcp.build_captures(projection, {}))

    def test_case_b_explicit_argument_builds_a_real_draft(self):
        b = book(LUMOS)
        c = one(b)
        projection = mcp.project_command(c, bridge_b())
        captures = mcp.build_captures(projection, {"area": "Kinderzimmer Jan"})
        self.assertEqual(captures, {"area": "Kinderzimmer Jan"})
        draft = engine.build_draft(c, captures, "de")
        self.assertIsNotNone(draft)
        self.assertEqual(draft.target.value, "Kinderzimmer Jan")

    def test_case_c_omitted_argument_uses_implicit_default(self):
        b = book(LUMOS)
        c = one(b)
        bridge = bridge_c(area="Wohnzimmer")
        projection = mcp.project_command(c, bridge)
        captures = mcp.build_captures(projection, {})
        self.assertEqual(captures, {"area": "Wohnzimmer"})
        draft = engine.build_draft(c, captures, "de")
        self.assertIsNotNone(draft)
        self.assertEqual(draft.target.value, "Wohnzimmer")

    def test_case_c_explicit_argument_overrides_default(self):
        b = book(LUMOS)
        c = one(b)
        projection = mcp.project_command(c, bridge_c(area="Wohnzimmer"))
        captures = mcp.build_captures(projection, {"area": "Kinderzimmer Jan"})
        self.assertEqual(captures, {"area": "Kinderzimmer Jan"})

    def test_int_capture_round_trip_builds_a_real_query_or_draft(self):
        b = book({
            "id": "raise_blind", "trigger": "blind {area} to {value} percent",
            "operation": "cover.set_cover_position",
            "target": {"kind": "area_name", "capture": "area"},
            "arguments": {"position": {"capture": "value", "type": "int", "min": 0, "max": 100}},
        })
        c = one(b)
        projection = mcp.project_command(c, bridge_a(area="Buero"))
        captures = mcp.build_captures(projection, {"value": 42})
        self.assertEqual(captures["value"], "42")
        draft = engine.build_draft(c, {**captures, "area": "Buero"}, "de")
        self.assertIsNotNone(draft)
        self.assertEqual(draft.arguments["position"], 42)

    def test_multi_source_query_round_trip(self):
        b = book({
            "id": "avg_temp", "trigger": "average temperature in {area}",
            "sources": [
                {"kind": "avg", "target": {"kind": "area_name", "capture": "area"},
                 "domain_filter": "sensor", "device_class": "temperature", "variable": "t"},
            ],
            "output": "{t}",
        })
        c = one(b)
        bridge = bridge_c(area="Wohnzimmer")
        projection = mcp.project_command(c, bridge)
        captures = mcp.build_captures(projection, {})
        self.assertEqual(captures, {"area": "Wohnzimmer"})
        plan = engine.build_query_plan(c, captures, "de")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.sources[0].target.value, "Wohnzimmer")


class LanguageResolutionTests(unittest.TestCase):
    """Trigger/alias language resolution for tool descriptions reuses
    core.book_languages.text() unchanged (mcp_projection's own
    resolve_localized alias) - a missing translation falls back rather
    than excluding the command from tools/list, and the requested
    language never changes WHICH tool is exposed or what its inputSchema
    permits, only how the description text reads."""

    def _de_en_command(self):
        return {
            "id": "greet", "trigger": {"de": "grüß {area}", "en": "greet {area}"},
            "operation": "light.set_power",
            "target": {"kind": "area_name", "capture": "area"},
            "arguments": {"on": True},
        }

    def _en_only_command(self):
        return {
            "id": "greet_en_only", "trigger": "greet only {area}",  # bare string -> "en" per _load_command
            "operation": "light.set_power",
            "target": {"kind": "area_name", "capture": "area"},
            "arguments": {"on": True},
        }

    def test_direct_translation_is_used_when_present(self):
        b = book(self._de_en_command())
        result = mcp.project_command(one(b), bridge_a(language="de"))
        self.assertIn("'grüß'", result.description)
        self.assertNotIn("greet", result.description)

    def test_requesting_a_language_the_command_lacks_falls_back_rather_than_excluding_it(self):
        b = book(self._en_only_command())
        result = mcp.project_command(one(b), bridge_a(language="de"))
        # Still projected as a real tool - a missing translation is never
        # a reason to skip a typed, enabled command (see ProjectionSkip's
        # own docstring: only genuine structural problems skip a command).
        self.assertIsInstance(result, mcp.McpToolProjection)
        self.assertIn("'greet only'", result.description)

    def test_spellbook_level_projection_never_drops_a_typed_command_for_a_missing_translation(self):
        b = book(self._de_en_command(), self._en_only_command())
        projections, skips = mcp.project_spellbook(b, bridge_a(language="de"))
        self.assertEqual(len(projections), 2)
        self.assertEqual(skips, ())

    def test_language_changes_wording_only_never_the_input_schema_or_permitted_area(self):
        b = book(self._de_en_command())
        c = one(b)
        result_de = mcp.project_command(c, bridge_c(language="de"))
        result_en = mcp.project_command(c, bridge_c(language="en"))
        self.assertNotEqual(result_de.description, result_en.description)
        # Per-argument DESCRIPTION text legitimately varies by language
        # too (it's display text, same as the tool description) - but the
        # actual permission-relevant shape (which properties exist, which
        # are required, type/enum/min/max) and the implicit default VALUE
        # (the Bridge's own area name, itself language-independent) must
        # not.
        def shape(schema):
            return {
                name: {k: v for k, v in prop.items() if k != "description"}
                for name, prop in schema["properties"].items()
            } | {"required": schema.get("required")}

        self.assertEqual(shape(result_de.input_schema), shape(result_en.input_schema))
        self.assertEqual(
            [a.implicit_default for a in result_de.arguments],
            [a.implicit_default for a in result_en.arguments],
        )


class DescriptionWordingTests(unittest.TestCase):
    """Tool descriptions state facts (effect, then the matching voice
    phrase as a labeled example) rather than issuing an instruction to the
    calling agent - and the area-argument text explicitly forbids
    inventing a value, not just implying it."""

    def test_area_argument_text_forbids_inventing_a_value(self):
        for key in ("area_required", "area_optional"):
            with self.subTest(key=key):
                self.assertIn("never invent or guess", mcp._ARG_TEXT["en"][key])
                self.assertIn("erfinden oder raten", mcp._ARG_TEXT["de"][key])

    def test_no_description_uses_the_old_imperative_wording(self):
        for lang_texts in mcp._ROUTING_TEXT.values():
            for text in lang_texts.values():
                self.assertNotIn("MUSS", text)
                self.assertNotIn("MUST", text)

    def test_query_only_command_gets_the_generic_query_effect_sentence(self):
        b = book({
            "id": "avg_temp", "trigger": "average temperature in {area}",
            "sources": [
                {"kind": "avg", "target": {"kind": "area_name", "capture": "area"},
                 "domain_filter": "sensor", "device_class": "temperature", "variable": "t"},
            ],
            "output": "{t}",
        })
        result = mcp.project_command(one(b), bridge_a())
        self.assertTrue(result.description.startswith(mcp._QUERY_EFFECT_TEXT["de"]))
        self.assertIn("Beispiel-Sprachbefehl", result.description)

    def test_combined_spell_gets_the_generic_combined_effect_sentence(self):
        combined = {
            "id": "clean_and_report", "trigger": "clean {area} and report",
            "steps": [
                {"alias": "cleaning", "operation": "vacuum.clean_area", "target": {"kind": "area_name", "capture": "area"}},
                {"alias": "lights", "sources": [
                    {"kind": "count", "target": {"kind": "area_name", "capture": "area"},
                     "domain_filter": "light", "state_filter": "on", "variable": "n"},
                ]},
            ],
            "output": "{cleaning.success_count} {lights.n}",
        }
        result = mcp.project_command(one(book(combined)), bridge_b())
        self.assertTrue(result.description.startswith(mcp._COMBINED_EFFECT_TEXT["de"]))

    def test_single_operation_with_a_real_service_names_it_from_the_catalog(self):
        # light.turn_off has a plain (domain, service) tuple in
        # OPERATION_CATALOG (no bespoke dispatch, unlike light.set_power) -
        # its effect sentence must be generated from that real service
        # name, not hand-authored a second time here.
        b = book({
            "id": "lights_off", "trigger": "turn off {area}",
            "operation": "light.turn_off",
            "target": {"kind": "area_name", "capture": "area"},
            "arguments": {},
        })
        result_de = mcp.project_command(one(b), bridge_a(language="de"))
        self.assertIn("light.turn_off", result_de.description)
        self.assertTrue(result_de.description.startswith("Ruft den Home-Assistant-Dienst"))
        result_en = mcp.project_command(one(b), bridge_a(language="en"))
        self.assertTrue(result_en.description.startswith("Calls Home Assistant's"))
        self.assertIn("light.turn_off", result_en.description)

    def test_bespoke_dispatch_operation_uses_its_own_hand_written_sentence(self):
        b = book(LUMOS)
        result = mcp.project_command(one(b), bridge_a(language="en"))
        self.assertEqual(mcp._effect_text(one(b), "en"), "Turns lights on.")
        self.assertTrue(result.description.startswith("Turns lights on."))


class TextOnlySpellProjectionTests(unittest.TestCase):
    """Pure free-text/lore Spells (no operation/sources/steps - only
    `output`) project as read-only MCP tools, using the SAME project_
    command/project_spellbook entry points as every other Spell - never a
    second projection path. Reuses the SAME BridgeArea/policy fixtures as
    CaseABCTests, even though area semantics never actually apply to a
    text-only Spell's own placeholders (there is no target_spec at all)."""

    def test_no_placeholders_projects_a_tool_with_an_empty_schema(self):
        b = book({"id": "free_text", "trigger": "tell me a joke", "output": "Why did...", "assist_redirect": False})
        result = mcp.project_command(one(b), bridge_a())
        self.assertIsInstance(result, mcp.McpToolProjection)
        self.assertEqual(result.name, "free_text")
        self.assertTrue(result.read_only)
        self.assertEqual(result.input_schema, {"type": "object", "properties": {}})
        self.assertEqual(result.arguments, ())

    def test_trigger_placeholders_become_required_string_arguments(self):
        b = book({
            "id": "describe_area", "trigger": "tell me about {area}",
            "output": "{area} is a fine place.", "assist_redirect": False,
        })
        result = mcp.project_command(one(b), bridge_b())
        self.assertIsInstance(result, mcp.McpToolProjection)
        self.assertEqual(
            result.input_schema,
            {
                "type": "object",
                "properties": {"area": {"type": "string", "description": mcp._ARG_TEXT["de"]["text_placeholder"]}},
                "required": ["area"],
            },
        )
        [arg] = result.arguments
        self.assertTrue(arg.required)
        self.assertIsNone(arg.implicit_default)

    def test_multiple_placeholders_all_become_required_arguments_in_trigger_order(self):
        b = book({
            "id": "compare", "trigger": "compare {first} and {second}",
            "output": "{first} versus {second}.", "assist_redirect": False,
        })
        result = mcp.project_command(one(b), bridge_b())
        self.assertEqual(result.input_schema["required"], ["first", "second"])

    def test_never_area_semantics_even_with_a_placeholder_named_area(self):
        # No target_spec exists for a text-only command at all - "area" is
        # just another plain placeholder name here, never CASE A/B/C,
        # never implicitly defaulted from the Bridge's own area, unlike a
        # genuine area_name-kind capture on a typed command.
        b = book({
            "id": "greet_area", "trigger": "hail {area}", "output": "Hail, {area}!",
            "assist_redirect": False,
        })
        result = mcp.project_command(one(b), bridge_a(area="Jans Zimmer"))
        self.assertEqual(result.arguments[0].capture_name, "area")
        self.assertTrue(result.arguments[0].required)
        self.assertIsNone(result.arguments[0].implicit_default)
        self.assertEqual(result.implicit_captures, {})

    def test_language_fallback_still_projects_the_tool(self):
        b = book({
            "id": "en_only", "trigger": "greet only", "output": "Hello there.", "assist_redirect": False,
        })
        result = mcp.project_command(one(b), bridge_a(language="de"))
        self.assertIsInstance(result, mcp.McpToolProjection)

    def test_unresolvable_trigger_text_is_a_projection_skip_with_a_concrete_reason(self):
        raw = {
            "id": "polyglot", "trigger": {"fr": "bonjour", "es": "hola"},
            "output": {"fr": "bonjour !", "es": "hola !"},
            "default_language": "it", "assist_redirect": False,
        }
        b = engine.load_spellbook_from_dict("test", {"id": "test", "name": "Test", "commands": [raw]})
        result = mcp.project_command(one(b), bridge_a(language="de"))
        self.assertIsInstance(result, mcp.ProjectionSkip)
        self.assertEqual(result.reason, "unresolvable_text")

    def test_spellbook_level_reports_the_skip_rather_than_dropping_it_silently(self):
        raw = {
            "id": "polyglot", "trigger": {"fr": "bonjour", "es": "hola"},
            "output": {"fr": "bonjour !", "es": "hola !"},
            "default_language": "it", "assist_redirect": False,
        }
        b = engine.load_spellbook_from_dict("test", {"id": "test", "name": "Test", "commands": [raw]})
        projections, skips = mcp.project_spellbook(b, bridge_a(language="de"))
        self.assertEqual(projections, ())
        [skip] = skips
        self.assertEqual(skip.reason, "unresolvable_text")

    def test_duplicate_spell_id_among_text_only_commands_is_excluded(self):
        first = {"id": "dup", "trigger": "say hi", "output": "Hi!", "assist_redirect": False}
        second = {"id": "dup", "trigger": "say bye", "output": "Bye!", "assist_redirect": False}
        b = book(first, second)
        projections, skips = mcp.project_spellbook(b, bridge_a())
        self.assertEqual(projections, ())
        # Reported once per conflicting command, not merged into one.
        self.assertEqual([s.reason for s in skips], ["duplicate_spell_id", "duplicate_spell_id"])

    def test_disabled_text_only_command_is_silently_absent(self):
        b = book({
            "id": "free_text", "trigger": "tell me a joke", "output": "Why...",
            "enabled": False, "assist_redirect": False,
        })
        projections, skips = mcp.project_spellbook(b, bridge_a())
        self.assertEqual(projections, ())
        self.assertEqual(skips, ())

    def test_description_labels_it_as_text_only_and_never_imperative(self):
        b = book({
            "id": "free_text", "trigger": "tell me a joke", "output": "Why...", "assist_redirect": False,
        })
        result_de = mcp.project_command(one(b), bridge_a(language="de"))
        self.assertIn("Text", result_de.description)
        self.assertNotIn("MUSS", result_de.description)
        result_en = mcp.project_command(one(b), bridge_a(language="en"))
        self.assertIn("predefined text response", result_en.description)
        self.assertNotIn("MUST", result_en.description)

    def test_assist_redirect_projects_as_a_real_non_read_only_tool(self):
        # command.assist_redirect=True (the current default - see
        # models.DEFAULT_ASSIST_REDIRECT) means `output` is a command
        # phrase for Home Assistant's own configured conversation agent to
        # interpret and execute for real - core.mcp_execution now actually
        # performs that forward (see _execute_assist_redirect), so this
        # projects as a genuine callable tool, never a ProjectionSkip. It
        # may perform a device action, so it is never read-only.
        b = book({"id": "sunrise", "trigger": "sunrise over {area}", "output": "Set light {area} to 100%."})
        result = mcp.project_command(one(b), bridge_a())
        self.assertIsInstance(result, mcp.McpToolProjection)
        self.assertFalse(result.read_only)

    def test_default_is_assist_redirect_for_now_pending_the_bundled_content_review(self):
        # No assist_redirect field at all - today's actual default for
        # every currently-bundled text-only Spell (see
        # models.DEFAULT_ASSIST_REDIRECT's own migration note). Still a
        # real, callable tool - just not read-only.
        b = book({"id": "unmarked", "trigger": "do the thing", "output": "Doing the thing."})
        self.assertTrue(one(b).assist_redirect)
        result = mcp.project_command(one(b), bridge_a())
        self.assertIsInstance(result, mcp.McpToolProjection)
        self.assertFalse(result.read_only)

    def test_assist_redirect_description_discloses_it_may_act_never_read_only_wording(self):
        b = book({"id": "sunrise", "trigger": "sunrise over {area}", "output": "Set light {area} to 100%."})
        result_de = mcp.project_command(one(b), bridge_a(language="de"))
        self.assertIn("Geräteaktion", result_de.description)
        result_en = mcp.project_command(one(b), bridge_a(language="en"))
        self.assertIn("may perform a device action", result_en.description)


class ReadOnlyAnnotationTests(unittest.TestCase):
    """McpToolProjection.read_only tracks OPERATION_CATALOG's own
    action/query classification per target - never a second
    classification - and is what providers/mcp/transport.py surfaces as
    MCP's own ToolAnnotations.read_only_hint."""

    def test_action_command_is_not_read_only(self):
        b = book(LUMOS)
        result = mcp.project_command(one(b), bridge_a())
        self.assertFalse(result.read_only)

    def test_query_command_is_read_only(self):
        b = book({
            "id": "avg_temp", "trigger": "average temperature in {area}",
            "sources": [
                {"kind": "avg", "target": {"kind": "area_name", "capture": "area"},
                 "domain_filter": "sensor", "device_class": "temperature", "variable": "t"},
            ],
            "output": "{t}",
        })
        result = mcp.project_command(one(b), bridge_a())
        self.assertTrue(result.read_only)

    def test_combined_spell_with_only_query_steps_is_read_only(self):
        combined = {
            "id": "just_queries", "trigger": "status of {area}",
            "steps": [
                {"alias": "lights", "sources": [
                    {"kind": "count", "target": {"kind": "area_name", "capture": "area"},
                     "domain_filter": "light", "state_filter": "on", "variable": "n"},
                ]},
            ],
            "output": "{lights.n}",
        }
        result = mcp.project_command(one(book(combined)), bridge_a())
        self.assertTrue(result.read_only)

    def test_combined_spell_with_any_action_step_is_not_read_only(self):
        combined = {
            "id": "clean_and_report", "trigger": "clean {area} and report",
            "steps": [
                {"alias": "cleaning", "operation": "vacuum.clean_area", "target": {"kind": "area_name", "capture": "area"}},
                {"alias": "lights", "sources": [
                    {"kind": "count", "target": {"kind": "area_name", "capture": "area"},
                     "domain_filter": "light", "state_filter": "on", "variable": "n"},
                ]},
            ],
            "output": "{cleaning.success_count} {lights.n}",
        }
        result = mcp.project_command(one(book(combined)), bridge_a())
        self.assertFalse(result.read_only)

    def test_text_only_command_is_read_only(self):
        b = book({
            "id": "free_text", "trigger": "tell me a joke", "output": "Why...", "assist_redirect": False,
        })
        result = mcp.project_command(one(b), bridge_a())
        self.assertTrue(result.read_only)


if __name__ == "__main__":
    unittest.main()
