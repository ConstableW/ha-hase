"""Studio uses real matching/execution and keeps books and permissions separate."""
import asyncio
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import test_core_engine as core
import yaml

studio_core = importlib.import_module("hase_spellbook_core.studio")
studio_validation = importlib.import_module("hase_spellbook_core.studio_validation")
engine = core.spellbook_engine
models = core._models_mod
executor = core._executor_mod
ROOT = Path(__file__).resolve().parents[1] / "custom_components/hase"

# Load real integration adapters without importing HA's platform entrypoint.
package = types.ModuleType("hase_studio_test")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
sys.modules[package.__name__ + ".core"] = sys.modules["hase_spellbook_core"]
for name in ("studio", "models", "executor", "spellbook_engine", "matching"):
    sys.modules[f"{package.__name__}.core.{name}"] = importlib.import_module(f"hase_spellbook_core.{name}")
adapter = importlib.import_module("hase_studio_test.studio")
N = types.SimpleNamespace


def spell(**changes):
    return {"id": "lumos", "trigger": "Lumos", "operation": "light.set_power",
            "target": {"kind": "area_id", "value": "living"}, "arguments": {"on": True}, **changes}


def book(book_id="testbook", commands=None):
    return {"id": book_id, "name": book_id.title(), "commands": commands if commands is not None else [spell()]}


class DataTests(unittest.TestCase):
    def test_target_traversal_includes_expert_steps_and_query_sources(self):
        raw_spell = {
            "id": "nested", "trigger": "nested", "target": {"kind": "entity_id", "value": "light.root"},
            "expert": {"target": {"kind": "entity_ids", "value": ["light.one", "light.two"]}},
            "sources": [{"kind": "state", "target": {"kind": "entity_id", "value": "sensor.top"}}],
            "steps": [
                {"target": {"kind": "entity_id", "value": "light.step"}},
                {"sources": [{"target": {"kind": "entity_id", "value": "sensor.nested"}}]},
            ],
        }
        paths = [path for _variant, path, _definition, _target in adapter._spell_targets(raw_spell)]
        self.assertEqual(paths, ["spell.target", "spell.sources[0].target", "spell.expert.target",
                                 "spell.steps[0].target", "spell.steps[1].sources[0].target"])
        self.assertEqual(adapter._entity_id_targets(raw_spell),
                         ["light.root", "sensor.top", "light.one", "light.two", "light.step", "sensor.nested"])

    def test_book_disabled_requires_boolean_and_is_distinct_from_spell_enabled(self):
        raw = {**book(), "disabled": True}
        studio_core.check_document(raw)
        loaded = engine.load_spellbook_from_dict("testbook", raw)
        self.assertTrue(loaded.disabled)
        self.assertTrue(loaded.commands[0].enabled)
        self.assertIsNone(engine.find_command(loaded, "Lumos"))
        raw["disabled"] = "false"
        with self.assertRaises(studio_core.StudioError):
            studio_core.check_document(raw)
        self.assertIsNone(engine.load_spellbook_from_dict("testbook", raw))

    def test_user_action_metadata_exposes_source_area_for_vacuum(self):
        vacuum = next(row for row in studio_core.user_actions() if row["operation"] == "vacuum.clean_area")
        self.assertIn("source_area", vacuum["targets"])

    def test_state_query_metadata_exposes_shared_and_domain_response_fields(self):
        query = next(row for row in studio_core.user_actions() if row["operation"] == "entity.get_state")
        self.assertEqual(query["response_fields"]["*"], ["state", "entity_name", "entity_id", "area"])
        self.assertEqual(query["response_fields"]["sensor"], ["unit"])
        self.assertIn("brightness", query["response_fields"]["light"])

    def test_duplicate_yaml_fields_rejected(self):
        with self.assertRaises(studio_core.StudioError):
            studio_core.parse_document("id: first\nid: second\nname: Test\ncommands: []")

    def test_multi_source_query_spell_passes_check_document(self):
        raw = book(commands=[{
            "id": "lights_on", "trigger": "wie viele lichter sind an",
            "sources": [{"kind": "count", "target": {"kind": "all_allowed"}, "domain_filter": "light",
                         "state_filter": "on", "variable": "lights_on"}],
            "catchphrase": "{lights_on} Lichter sind an.",
        }])
        studio_core.check_document(raw)  # must not raise
        self.assertEqual(studio_core.spell_capability(raw["commands"][0]), "USER")
        self.assertEqual(studio_core.book_capability(raw), "USER")

    def test_static_report_validates_multi_source_query_without_false_text_only_warning(self):
        raw = book(commands=[{
            "id": "lights_on", "trigger": "wie viele lichter sind an",
            "sources": [{"kind": "count", "target": {"kind": "all_allowed"}, "domain_filter": "light",
                         "state_filter": "on", "variable": "lights_on"}],
            "catchphrase": "{lights_on} Lichter sind an.",
        }])
        report = studio_core.static_report(raw)
        self.assertEqual(report["errors"], 0)
        self.assertEqual(report["spells"][0]["warnings"], [])
        self.assertTrue(report["spells"][0]["valid"])

    def test_linter_classifies_intentional_text_and_similar_trigger_as_info(self):
        raw = book(commands=[
            {"id": "greet", "trigger": "hello there", "output": "Hi"},
            {"id": "greet_again", "trigger": "hello there!", "output": "Hello"},
        ])
        report = studio_core.static_report(raw)
        self.assertEqual(report["warnings"], 0)
        self.assertGreaterEqual(report["infos"], 2)
        self.assertTrue(all(row["valid"] for row in report["spells"]))

    def test_static_report_flags_invalid_source_target(self):
        raw = book(commands=[{
            "id": "bad", "trigger": "x",
            "sources": [{"kind": "count", "target": {"kind": "all_allowed"}, "variable": "n"}],
            "catchphrase": "{n}",
        }])
        raw["commands"][0]["sources"][0]["target"] = {"kind": "area_name", "capture": "missing"}
        # A dangling capture reference is caught at load time already
        # (check_document/_load_command reject it outright) - this
        # confirms static_report never reaches the query-plan branch for
        # a spell that shouldn't have loaded at all.
        with self.assertRaises(studio_core.StudioError):
            studio_core.check_document(raw)

    def test_spell_variables_reports_captures_query_fields_and_sources(self):
        capture_spell = spell()  # operation=light.set_power, target captures nothing named
        capture_rows = studio_core.spell_variables(capture_spell)
        self.assertEqual(capture_rows, [])  # "Lumos" has no {placeholder}

        query_spell = {"id": "q", "trigger": "wie ist der zustand von {entity}",
                        "operation": "entity.get_state", "target": {"kind": "entity_name", "capture": "entity"}}
        query_rows = {row["name"]: row for row in studio_core.spell_variables(query_spell)}
        self.assertEqual(query_rows["entity"]["source"], "capture")
        self.assertEqual(query_rows["state"]["source"], "query_field")
        self.assertIn("brightness", {r["name"] for r in studio_core.spell_variables(query_spell) if r.get("domain") == "light"})

        source_spell = {
            "id": "s", "trigger": "wie viele lichter sind an",
            "sources": [{"kind": "count", "target": {"kind": "all_allowed"}, "domain_filter": "light",
                         "state_filter": "on", "variable": "lights_on"}],
            "catchphrase": "{lights_on} Lichter sind an.",
        }
        source_rows = {row["name"]: row for row in studio_core.spell_variables(source_spell)}
        self.assertEqual(source_rows["lights_on"]["source"], "source_variable")
        self.assertEqual(source_rows["lights_on"]["kind"], "count")

    def test_spell_variables_returns_empty_for_unparseable_spell(self):
        self.assertEqual(studio_core.spell_variables({"id": "x"}), [])

    def _steps_spell(self):
        return {
            "id": "night_mode", "trigger": "gute nacht",
            "steps": [
                {"alias": "shutters", "operation": "cover.close_cover", "target": {"kind": "all_allowed"}},
                {"alias": "front_door", "operation": "lock.lock", "target": {"kind": "entity_id", "value": "lock.front_door"}},
                {"alias": "open_contacts", "sources": [
                    {"kind": "count", "target": {"kind": "all_allowed"}, "domain_filter": "binary_sensor",
                     "state_filter": "on", "variable": "count"},
                    {"kind": "names", "target": {"kind": "all_allowed"}, "domain_filter": "binary_sensor",
                     "state_filter": "on", "variable": "names"},
                ]},
            ],
            "catchphrase": "{shutters.target_count} Rollos, Tür {front_door.state}, {open_contacts.count} offen: {open_contacts.names}.",
        }

    def test_steps_spell_passes_check_document_and_reports_expert_step_capability(self):
        raw = book(commands=[self._steps_spell()])
        studio_core.check_document(raw)  # must not raise
        # front_door's lock.lock step is deliberately Expert-gated (see
        # operations.py) - spell_capability() must look inside `steps` for
        # this instead of assuming every steps spell is USER-tier.
        self.assertEqual(studio_core.spell_capability(raw["commands"][0]), "USER + EXPERT")
        self.assertEqual(studio_core.book_capability(raw), "USER + EXPERT")

    def test_spell_variables_reports_namespaced_step_fields(self):
        rows = {row["name"]: row for row in studio_core.spell_variables(self._steps_spell())}
        self.assertEqual(rows["shutters.target_count"]["source"], "step_field")
        self.assertEqual(rows["shutters.target_count"]["kind"], "action")
        self.assertEqual(rows["shutters.target_count"]["alias"], "shutters")
        self.assertEqual(rows["shutters.target_count"]["operation"], "cover.close_cover")
        # front_door targets a single entity_id -> single-entity vocabulary.
        self.assertIn("front_door.state", rows)
        self.assertIn("front_door.success", rows)
        self.assertNotIn("front_door.target_count", rows)
        self.assertEqual(rows["open_contacts.count"]["kind"], "query")
        self.assertIn("open_contacts.names", rows)

    def test_static_report_validates_steps_spell(self):
        report = studio_core.static_report(book(commands=[self._steps_spell()]))
        self.assertEqual(report["errors"], 0)
        self.assertTrue(report["spells"][0]["valid"])

    def test_static_report_uses_valid_samples_for_typed_step_arguments(self):
        # Regression (found on a live instance): a capture used as an int step
        # argument was sampled as "test" and reported as a false error.
        spell = {"id": "evening", "trigger": "abend {area} auf {pct}", "steps": [
            {"alias": "dim", "operation": "light.turn_on", "target": {"kind": "area_name", "capture": "area"},
             "arguments": {"brightness_pct": {"capture": "pct", "type": "int", "min": 1, "max": 100}}}],
            "output": "{dim.success_count}"}
        row = studio_core.static_report(book(commands=[spell]))["spells"][0]
        self.assertEqual(row["errors"], [])
        self.assertTrue(row["valid"])

    def test_static_report_names_the_invalid_step(self):
        broken = self._steps_spell()
        broken["steps"][1]["operation"] = "no.such_operation"
        row = studio_core.static_report(book(commands=[broken]))["spells"][0]
        self.assertFalse(row["valid"])
        self.assertEqual(row["errors"], ["user: Schritt 2 (no.such_operation): Aktion, Ziel oder Parameter ungültig."])

    def test_static_report_flags_invalid_step_action(self):
        broken = self._steps_spell()
        broken["steps"][0]["target"] = {"kind": "nonsense"}
        with self.assertRaises(studio_core.StudioError):
            studio_core.check_document(book(commands=[broken]))

    def test_steps_and_operation_together_rejected_by_check_document(self):
        broken = self._steps_spell()
        broken["operation"] = "light.turn_on"
        broken["target"] = {"kind": "all_allowed"}
        with self.assertRaises(studio_core.StudioError):
            studio_core.check_document(book(commands=[broken]))

    def test_query_source_capabilities_exposes_backend_derived_vocabulary(self):
        caps = studio_core.query_source_capabilities()
        self.assertIn("names", caps["kinds"])
        self.assertIn("states", caps["kinds"])
        self.assertIn("count", caps["aggregate_kinds"])
        self.assertNotIn("state", caps["aggregate_kinds"])
        self.assertIn("all_allowed", caps["aggregate_target_kinds"])
        self.assertIn("entity_id", caps["single_target_kinds"])
        self.assertIsInstance(caps["max_steps"], int)

    def test_capability_and_independent_books(self):
        user = book()
        mixed = book("hdr", [spell(expert={"target": {"kind": "template", "value": "{{ [] }}"}})])
        expert = book("cc", [{"id": "x", "trigger": "x", "expert": {
            "operation": "light.set_power", "arguments": {"on": True}, "target": {"kind": "entity_id", "value": "light.x"}}}])
        for raw, capability in [(user, "USER"), (mixed, "USER + EXPERT"), (expert, "EXPERT REQUIRED")]:
            studio_core.check_document(raw)
            self.assertEqual(studio_core.book_capability(raw), capability)
        self.assertEqual(user, book())

    def test_import_does_not_set_permissions(self):
        for key in ("expert_allowed", "expert_runtime", "policy", "services"):
            with self.subTest(key=key), self.assertRaises(studio_core.StudioError):
                studio_core.parse_document(yaml.safe_dump({**book(), key: True}))

    def test_reject_aliases_paths_and_invalid_command(self):
        for text in ("id: a\nname: a\ncommands: &a [*a]", yaml.safe_dump(book("../outside")),
                     yaml.safe_dump(book(commands=[{"id":"x", "trigger":"x", "service":"light.turn_on"}]))):
            with self.assertRaises(studio_core.StudioError):
                studio_core.parse_document(text)

    def test_conflicts_default_keep_and_copy(self):
        current = book()
        incoming = book("other", [spell(arguments={"on": False})])
        self.assertEqual(studio_core.merge_book(current, incoming, {}), current)
        copied = studio_core.merge_book(current, incoming, {"lumos":"copy"})
        self.assertEqual(copied["id"], "testbook")
        self.assertEqual(len(copied["commands"]), 2)
        self.assertNotEqual(copied["commands"][0]["id"], copied["commands"][1]["id"])

    def test_expert_edit_preserves_user(self):
        old = spell()
        new = spell(target={"kind":"template", "value":"{{ [] }}"})
        kept = studio_core.preserve_user(old, new)
        self.assertEqual(kept["target"], old["target"])
        self.assertEqual(kept["expert"]["target"], new["target"])
        self.assertEqual(studio_core.spell_capability(kept), "USER + EXPERT")
        self.assertEqual(studio_core.preserve_user(old, new, remove_user=True), new)

    def test_roundtrip_preserves_metadata_variants_and_collections(self):
        raw = book(commands=[spell(tags=["Magic"], expert={"target":{"kind":"template","value":"{{ ['light.a'] }}"}})])
        raw.update(author="Someone", languages=["de","en"], metadata={"license":"CC0"})
        exported = studio_core.export_book(raw, ["lumos"], "Magic")
        self.assertEqual(studio_core.parse_document(yaml.safe_dump(exported)), raw)
        self.assertEqual(studio_core.export_book(raw, collection="Missing")["commands"], [])

    def test_alias_and_disabled_matching(self):
        raw = book(commands=[spell(triggers=["Licht bitte"] )])
        loaded = engine.load_spellbook_from_dict("testbook", raw)
        self.assertEqual(engine.match(loaded, "Licht bitte").spell_id, "lumos")
        raw["commands"][0]["enabled"] = False
        self.assertIsInstance(engine.match(engine.load_spellbook_from_dict("testbook", raw), "Lumos"), models.EngineNoMatch)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.resolver = N(resolve=AsyncMock(return_value=executor.ResolvedTargets((executor.ResolvedEntity("light.a", "living"),))),
                          resolve_area=AsyncMock(return_value="living"))
        self.caller = N(set_light_power=AsyncMock(return_value=True), get_entity_state=AsyncMock(), clean_vacuum_area=AsyncMock())
        area = models.AreaPolicy(frozenset({"living"}))
        self.policy = models.ScopedAreaPolicy(area, area)
        self.permissions = {"testbook": False, "cc": True}

    def draft(self):
        loaded = engine.load_spellbook_from_dict("testbook", book(commands=[spell(expert={"target":{"kind":"template","value":"{{ ['light.a'] }}"}})]))
        return engine.match(loaded, "Lumos").action_draft

    async def test_book_permissions_and_user_fallback(self):
        for book_id in ("testbook", "cc"):
            runner = executor.ActionExecutor(self.resolver, self.caller, lambda book_id=book_id: self.permissions[book_id])
            result = await runner.execute(self.draft(), self.policy, dry_run=True)
            self.assertEqual(result.status, models.ExecutionStatus.SUCCESS)
            self.assertEqual(self.resolver.resolve.call_args.args[0].kind, "template" if book_id=="cc" else "area_id")
        self.caller.set_light_power.assert_not_awaited()

    async def test_expert_only_denied_without_resolution(self):
        raw = book(commands=[{"id":"x", "trigger":"x", "expert":{"operation":"light.set_power", "arguments":{"on":True}, "target":{"kind":"template","value":"{{ [] }}"}}}])
        draft = engine.match(engine.load_spellbook_from_dict("testbook",raw),"x").action_draft
        result = await executor.ActionExecutor(self.resolver,self.caller).execute(draft,self.policy)
        self.assertEqual(result.reason_code,"expert_disabled")
        self.resolver.resolve.assert_not_awaited()
        self.caller.set_light_power.assert_not_awaited()

    async def test_broken_expert_falls_back_before_effect(self):
        self.resolver.resolve.side_effect = [executor.ResolvedTargets((), ({"reason":"template_error"},)), executor.ResolvedTargets((executor.ResolvedEntity("light.a","living"),))]
        result = await executor.ActionExecutor(self.resolver,self.caller,lambda:True).execute(self.draft(),self.policy)
        self.assertTrue(result.data["user_fallback"])
        self.caller.set_light_power.assert_awaited_once()

    async def test_template_raw_and_effective_policy(self):
        self.resolver.resolve.return_value = executor.ResolvedTargets((executor.ResolvedEntity("light.a","living"),executor.ResolvedEntity("light.b","bedroom")),(),["light.a","light.b"])
        result = await executor.ActionExecutor(self.resolver,self.caller,lambda:True).execute(self.draft(),self.policy,dry_run=True)
        self.assertEqual(result.data["raw"],["light.a","light.b"])
        self.assertEqual(result.data["entity_ids"],("light.a",))
        self.assertEqual(result.data["targets"][1]["reason"],"area_not_permitted")
        self.caller.set_light_power.assert_not_awaited()

    async def test_dry_run_and_execution_share_resolution(self):
        runner = executor.ActionExecutor(self.resolver,self.caller)
        draft = models.ActionDraft("light.set_power", models.ActionTarget("area_id","living"),{"on":True})
        preview = await runner.execute(draft,self.policy,dry_run=True)
        self.caller.set_light_power.assert_not_awaited()
        result = await runner.execute(draft,self.policy)
        self.assertEqual(preview.data["entity_ids"],result.data["entity_ids"])
        self.assertEqual(self.resolver.resolve.await_count,2)

    async def test_vacuum_dry_run_never_discovers_or_calls_service(self):
        draft = models.ActionDraft("vacuum.clean_area",models.ActionTarget("area_id","living"))
        result = await executor.ActionExecutor(self.resolver,self.caller).execute(draft,self.policy,dry_run=True)
        self.assertIn("warnings",result.data)
        self.caller.clean_vacuum_area.assert_not_awaited()

    async def test_malformed_expert_retains_matching_user(self):
        loaded = engine.load_spellbook_from_dict("testbook",book(commands=[spell(expert={"target":{"kind":"bogus"}})]))
        draft = engine.match(loaded,"Lumos").action_draft
        self.assertFalse(draft.expert)
        self.assertEqual(draft.target.kind,"area_id")

    async def test_user_fallback_cannot_bypass_client_operation_gate(self):
        fallback=models.ActionDraft("vacuum.clean_area",models.ActionTarget("area_id","living"))
        draft=models.ActionDraft("light.set_power",models.ActionTarget("template","{{ [] }}"),{"on":True},expert=True,user_fallback=fallback)
        runner=executor.ActionExecutor(self.resolver,self.caller,lambda:False,
                                       operation_allowed=lambda operation:operation!="vacuum.clean_area")
        result=await runner.execute(draft,self.policy)
        self.assertEqual(result.reason_code,"operation_disabled")
        self.resolver.resolve_area.assert_not_awaited()
        self.caller.clean_vacuum_area.assert_not_awaited()


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.hass = N(data={"hase_studio_permissions":{"testbook":True,"hdr":False}},
                      config=N(path=lambda part:str(Path(self.directory.name)/part)))
        async def job(fn,*args):
            return fn(*args)
        self.hass.async_add_executor_job = job
        self.studio = adapter.SpellbookStudio.__new__(adapter.SpellbookStudio)
        self.studio.hass=self.hass
        self.studio.lock=asyncio.Lock()
        self.studio.last_reports={}
        self.studio.permissions=N(async_save=AsyncMock())
        self.studio.registry=lambda:{"areas":[],"entities":[],"devices":[]}
        self.studio.profiles=lambda book_id:[]
        self.studio.studio_profiles=list
        self.source=Path(self.directory.name)/"bundled"
        self.source.mkdir()
        self.patch=patch("hase_studio_test.spellbook_loader.BUILTIN_BOOK_DIR",self.source)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def test_separate_files_and_optimistic_concurrency(self):
        await self.studio.write(book(),None)
        await self.studio.write(book("hdr"),None)
        raw,revision=await self.studio.read("testbook")
        raw["name"]="Changed"
        await self.studio.write(raw,revision)
        with self.assertRaises(studio_core.StudioError):
            await self.studio.write(raw,revision)
        self.assertEqual((await self.studio.read("hdr"))[0]["name"],"Hdr")
        self.assertEqual(len(list((Path(self.directory.name)/"hase_spellbooks").glob("*.yaml"))),2)

    async def test_disabled_book_remains_administrable_and_hidden_from_runtime(self):
        raw = {**book(), "disabled": True}
        revision = await self.studio.write(raw, None)
        self.assertTrue((await self.studio.read("testbook"))[0]["disabled"])
        self.assertIsNone(await adapter.spellbook_loader.async_get_spellbook(self.hass, "testbook"))
        self.assertNotIn("testbook", dict(await adapter.spellbook_loader.async_discover_spellbooks(self.hass)))
        raw["disabled"] = False
        await self.studio.write(raw, revision)
        self.assertFalse((await self.studio.read("testbook"))[0]["disabled"])
        self.assertIsNotNone(await adapter.spellbook_loader.async_get_spellbook(self.hass, "testbook"))
        self.assertIn("testbook", dict(await adapter.spellbook_loader.async_discover_spellbooks(self.hass)))

    async def test_edit_bundled_preserves_original(self):
        original=yaml.safe_dump(book())
        (self.source/"testbook.yaml").write_text(original,encoding="utf-8")
        raw,revision=await self.studio.read("testbook")
        raw["name"]="Edited"
        await self.studio.write(raw,revision)
        self.assertEqual((self.source/"testbook.yaml").read_text(encoding="utf-8"),original)
        self.assertEqual((await self.studio.read("testbook"))[0]["name"],"Edited")

    async def test_cover_copy_override_and_reset_keep_original_and_neighbor(self):
        original = {**book(), "default_language": "de", "languages": ["de", "en"],
                    "description": {"de": "Original", "en": "Original"}}
        original_text = yaml.safe_dump(original)
        source = self.source / "different_basename.yml"
        source.write_text(original_text, encoding="utf-8")
        png = b"\x89PNG\r\n\x1a\nfixture"
        source.with_suffix(".png").write_bytes(png)
        info = await self.studio.file_info("testbook", cover=True)
        self.assertEqual(info["origin"], "builtin")
        self.assertTrue(info["cover"].startswith("data:image/png;base64,"))
        raw, revision = await self.studio.read("testbook")
        raw["description"]["fr"] = "Copie"
        revision = await self.studio.write(raw, revision)
        custom = Path(self.directory.name) / "hase_spellbooks" / source.name
        neighbor = custom.with_name("neighbor.png")
        neighbor.write_bytes(b"keep")
        self.assertEqual(custom.with_suffix(".png").read_bytes(), png)
        self.assertEqual((await self.studio.file_info("testbook"))["origin"], "override")
        self.assertEqual((await self.studio.read("testbook"))[0]["description"]["fr"], "Copie")
        result = await self.studio.dispatch("reset_book", {"book_id":"testbook", "revision":revision, "confirmed":True}, None)
        self.assertTrue(result["restored"])
        self.assertFalse(custom.exists())
        self.assertFalse(custom.with_suffix(".png").exists())
        self.assertEqual(neighbor.read_bytes(), b"keep")
        self.assertEqual(source.read_text(encoding="utf-8"), original_text)
        self.assertEqual(source.with_suffix(".png").read_bytes(), png)
        self.assertEqual((await self.studio.read("testbook"))[0], original)
        self.assertTrue(self.studio.expert_allowed("testbook"))

    async def test_custom_delete_requires_confirmation_and_current_revision(self):
        revision = await self.studio.write(book(), None)
        path = Path(self.directory.name) / "hase_spellbooks/testbook.yaml"
        path.with_suffix(".png").write_bytes(b"cover")
        for data in ({"revision":revision}, {"revision":"stale", "confirmed":True}):
            with self.assertRaises(studio_core.StudioError):
                await self.studio.dispatch("delete_book", {"book_id":"testbook", **data}, None)
            self.assertTrue(path.exists())
            self.assertTrue(path.with_suffix(".png").exists())
        result = await self.studio.dispatch("delete_book", {"book_id":"testbook", "revision":revision, "confirmed":True}, None)
        self.assertFalse(result["restored"])
        self.assertFalse(path.exists())
        self.assertFalse(path.with_suffix(".png").exists())
        self.assertNotIn("testbook", self.hass.data["hase_studio_permissions"])
        self.assertIn("hdr", self.hass.data["hase_studio_permissions"])

    async def test_failed_first_save_rolls_back_custom_cover(self):
        source = self.source / "testbook.yaml"
        source.write_text(yaml.safe_dump(book()), encoding="utf-8")
        source.with_suffix(".png").write_bytes(b"cover")
        raw, revision = await self.studio.read("testbook")
        with patch.object(adapter.os, "replace", side_effect=OSError("write failed")), self.assertRaises(OSError):
            await self.studio.write(raw, revision)
        self.assertEqual(list((Path(self.directory.name) / "hase_spellbooks").iterdir()), [])
        self.assertTrue(source.exists())

    async def test_language_management_makes_full_custom_copy_and_roundtrips(self):
        raw = {**book(), "default_language":"de"}
        source = self.source / "testbook.yaml"
        source.write_text(yaml.safe_dump(raw), encoding="utf-8")
        _, revision = await self.studio.read("testbook")
        await self.studio.dispatch("manage_language", {"book_id":"testbook", "revision":revision, "action":"add", "code":"fr", "source":"de"}, None)
        changed, _ = await self.studio.read("testbook")
        self.assertEqual(changed["commands"][0]["trigger"], {"de":"Lumos", "fr":"Lumos"})
        self.assertEqual(changed["commands"][0]["target"], raw["commands"][0]["target"])
        # export_book strips Studio's own derived metadata.mcp cache (see
        # test_export_never_contains_local_permission) - compare against
        # the export itself, not the pre-export saved book, so this stays
        # a pure "export round-trips losslessly through YAML" check.
        exported = studio_core.export_book(changed)
        self.assertEqual(studio_core.parse_document(yaml.safe_dump(exported)), exported)
        self.assertEqual(yaml.safe_load(source.read_text(encoding="utf-8")), raw)

    async def test_language_drafts_preserve_all_languages_without_duplicating_action(self):
        old = spell(trigger="Licht an")
        edited = self.studio.user_spell({"action":"light.turn_on:0", "target_kind":"source_area",
            "trigger_language":"fr", "default_language":"de", "triggers":["Éclaire"],
            "translations":{"trigger":{"de":"Neuer deutscher Text", "en":"New English text"}}}, old)
        self.assertEqual(edited["trigger"], {"de":"Neuer deutscher Text", "en":"New English text", "fr":"Éclaire"})
        self.assertEqual(edited["operation"], "light.turn_on")
        plain = self.studio.user_spell({"action":"light.turn_on:0", "target_kind":"source_area",
            "trigger_language":"en", "default_language":"de", "triggers":["Lights on"]}, old)
        self.assertEqual(plain["trigger"], {"de":"Licht an", "en":"Lights on"})

    async def test_user_spell_defaults_and_validates_output_mode(self):
        old = spell(trigger="Licht an")
        default = self.studio.user_spell({"action":"light.turn_on:0", "target_kind":"source_area",
            "trigger_language":"de", "triggers":["Licht an"]}, old)
        self.assertEqual(default["output_mode"], "AUTO")
        explicit = self.studio.user_spell({"action":"light.turn_on:0", "target_kind":"source_area",
            "trigger_language":"de", "triggers":["Licht an"], "output_mode":"TEXT"}, old)
        self.assertEqual(explicit["output_mode"], "TEXT")
        with self.assertRaises(studio_core.StudioError):
            self.studio.user_spell({"action":"light.turn_on:0", "target_kind":"source_area",
                "trigger_language":"de", "triggers":["Licht an"], "output_mode":"LOUD"}, old)

    async def test_steps_spell_builds_multi_step_command(self):
        edited = self.studio.steps_spell({
            "triggers": ["gute nacht"], "trigger_language": "de", "default_language": "de",
            "steps": [
                {"kind": "action", "alias": "shutters", "action": "cover.close_cover:0",
                 "target_kind": "all_allowed"},
                {"kind": "query", "alias": "open_contacts", "sources": [
                    {"kind": "count", "target_kind": "all_allowed", "domain_filter": "binary_sensor",
                     "state_filter": "on", "variable": "count"},
                ]},
            ],
        }, None)
        self.assertEqual(len(edited["steps"]), 2)
        self.assertEqual(edited["steps"][0]["operation"], "cover.close_cover")
        self.assertEqual(edited["steps"][0]["alias"], "shutters")
        self.assertEqual(edited["steps"][1]["sources"][0]["variable"], "count")
        self.assertNotIn("operation", edited)
        studio_core.check_document(book(commands=[edited]))  # must round-trip validly

    async def test_steps_spell_preserves_hand_written_steps_verbatim(self):
        """Studio roundtrip: steps the guided builder cannot represent (here a
        trigger-capture target and a capture argument) are carried over
        unchanged, can be reordered with guided steps, and never rebuilt."""
        old = {"id": "evening", "trigger": {"de": "abend {area} auf {pct}"},
               "steps": [
                   {"alias": "dim", "operation": "light.turn_on", "target": {"kind": "area_name", "capture": "area"},
                    "arguments": {"brightness_pct": {"capture": "pct", "type": "int", "min": 0, "max": 100}}},
                   {"alias": "count", "sources": [{"kind": "count", "target": {"kind": "area_name", "capture": "area"},
                                                  "domain_filter": "light", "variable": "n"}]},
               ],
               "output": {"de": "{dim.success_count} {count.n}"}, "enabled": False}
        edited = self.studio.steps_spell({
            "id": "evening", "triggers": ["abend {area} auf {pct}"], "trigger_language": "de",
            "steps": [
                {"kind": "preserve", "index": 1},
                {"kind": "action", "alias": "shutters", "action": "cover.close_cover:0", "target_kind": "all_allowed"},
                {"kind": "preserve", "index": 0},
            ],
        }, old)
        self.assertEqual(edited["steps"][0], old["steps"][1])
        self.assertEqual(edited["steps"][2], old["steps"][0])
        self.assertEqual(edited["steps"][1]["operation"], "cover.close_cover")
        self.assertIs(edited["enabled"], False)
        studio_core.check_document(book(commands=[edited]))
        for bad in ({"kind": "preserve", "index": 5}, {"kind": "preserve", "index": "0"}):
            with self.assertRaises(studio_core.StudioError):
                self.studio.steps_spell({"triggers": ["abend {area} auf {pct}"], "trigger_language": "de",
                                         "steps": [bad]}, old)
        with self.assertRaises(studio_core.StudioError):  # duplicate alias across preserved/guided
            self.studio.steps_spell({"triggers": ["abend {area} auf {pct}"], "trigger_language": "de", "steps": [
                {"kind": "preserve", "index": 0},
                {"kind": "action", "alias": "dim", "action": "light.turn_on:0", "target_kind": "all_allowed"}]}, old)

    async def test_steps_spell_rejects_alias_that_shadows_a_trigger_capture(self):
        with self.assertRaisesRegex(studio_core.StudioError, "Platzhalter"):
            self.studio.steps_spell({"triggers": ["licht {area}"], "trigger_language": "de", "steps": [
                {"kind": "action", "alias": "area", "action": "light.turn_on:0", "target_kind": "all_allowed"}]}, None)

    async def test_steps_spell_rejects_duplicate_alias(self):
        with self.assertRaises(studio_core.StudioError):
            self.studio.steps_spell({
                "triggers": ["do it"], "trigger_language": "de",
                "steps": [
                    {"kind": "action", "alias": "a", "action": "light.turn_on:0", "target_kind": "all_allowed"},
                    {"kind": "action", "alias": "a", "action": "light.turn_off:0", "target_kind": "all_allowed"},
                ],
            }, None)

    async def test_steps_spell_rejects_unknown_action(self):
        with self.assertRaises(studio_core.StudioError):
            self.studio.steps_spell({
                "triggers": ["do it"], "trigger_language": "de",
                "steps": [{"kind": "action", "action": "not.a.real.action", "target_kind": "all_allowed"}],
            }, None)

    async def test_steps_spell_requires_at_least_one_step(self):
        with self.assertRaises(studio_core.StudioError):
            self.studio.steps_spell({"triggers": ["do it"], "trigger_language": "de", "steps": []}, None)

    async def test_steps_spell_rejects_area_name_target(self):
        # Not offered by this first guided builder pass - see
        # SpellbookStudio._resolve_step_target's docstring.
        with self.assertRaises(studio_core.StudioError):
            self.studio.steps_spell({
                "triggers": ["do it {area}"], "trigger_language": "de",
                "steps": [{"kind": "action", "action": "light.turn_on:0", "target_kind": "area_name"}],
            }, None)

    # -- wizard_spell(): the unified Wizard/Expert step-list builder --------

    async def test_wizard_spell_zero_steps_builds_text_only_spell(self):
        edited = self.studio.wizard_spell({
            "triggers": ["guten morgen"], "trigger_language": "de",
            "translations": {"output": "Guten Morgen!"}, "steps": [], "assist_redirect": False,
        }, None)
        for field in ("operation", "target", "arguments", "sources", "steps"):
            self.assertNotIn(field, edited)
        self.assertEqual(edited["output"], "Guten Morgen!")
        self.assertIs(edited["assist_redirect"], False)
        studio_core.check_document(book(commands=[edited]))  # must round-trip validly

    async def test_wizard_spell_zero_steps_requires_a_response(self):
        with self.assertRaises(studio_core.StudioError):
            self.studio.wizard_spell({
                "triggers": ["guten morgen"], "trigger_language": "de",
                "translations": {}, "steps": [],
            }, None)

    async def test_wizard_spell_one_action_step_matches_user_spell_shape(self):
        edited = self.studio.wizard_spell({
            "triggers": ["licht an"], "trigger_language": "de",
            "steps": [{"kind": "action", "action": "light.turn_on:0", "target_kind": "source_area"}],
        }, None)
        self.assertEqual(edited["operation"], "light.turn_on")
        self.assertEqual(edited["target"], {"kind": "source_area"})
        for field in ("sources", "steps"):
            self.assertNotIn(field, edited)
        studio_core.check_document(book(commands=[edited]))

    async def test_wizard_spell_single_step_alias_survives_flat_save_reload(self):
        edited = self.studio.wizard_spell({
            "triggers": ["close shutters"], "trigger_language": "de",
            "steps": [{"kind": "action", "alias": "shutters",
                       "action": "cover.close_cover:0", "target_kind": "all_allowed"}],
        }, None)
        self.assertNotIn("steps", edited)
        self.assertEqual(edited["alias"], "shutters")
        self.assertEqual(studio_core.as_logical_steps(edited)[0]["alias"], "shutters")
        studio_core.check_document(book(commands=[edited]))

    async def test_wizard_spell_multi_step_aliases_survive_reordering_verbatim(self):
        original = self.studio.wizard_spell({
            "triggers": ["night"], "trigger_language": "de",
            "steps": [
                {"kind": "action", "alias": "shutters", "action": "cover.close_cover:0", "target_kind": "all_allowed"},
                {"kind": "action", "alias": "lights", "action": "light.turn_off:0", "target_kind": "all_allowed"},
                {"kind": "action", "alias": "music", "action": "media_player.pause:0", "target_kind": "all_allowed"},
            ],
        }, None)
        reordered = self.studio.wizard_spell({
            "id": original["id"], "triggers": ["night"], "trigger_language": "de",
            "steps": [
                {"kind": "action", "alias": "music", "action": "media_player.pause:0", "target_kind": "all_allowed"},
                {"kind": "action", "alias": "shutters", "action": "cover.close_cover:0", "target_kind": "all_allowed"},
                {"kind": "action", "alias": "lights", "action": "light.turn_off:0", "target_kind": "all_allowed"},
            ],
        }, original)
        self.assertEqual([step["alias"] for step in reordered["steps"]], ["music", "shutters", "lights"])

    async def test_non_string_step_alias_is_rejected_without_coercion(self):
        raw = spell(operation="cover.close_cover", target={"kind": "all_allowed"}, arguments={})
        raw["alias"] = 42
        [blocker] = [row for row in studio_core.wizard_blocker_details(raw) if row["code"] == "invalid_step_alias"]
        self.assertEqual(blocker["step_index"], 0)
        self.assertEqual(raw["alias"], 42)
        with self.assertRaisesRegex(studio_core.StudioError, "Schrittname muss ein gültiger String"):
            studio_core.check_document(book(commands=[raw]))

    async def test_wizard_spell_one_expert_action_step_allows_template_target(self):
        edited = self.studio.wizard_spell({
            "triggers": ["mach das licht"], "trigger_language": "de",
            "steps": [{"kind": "action", "expert": True, "operation": "light.turn_on",
                       "target": {"kind": "template", "value": "{{ ['light.ceiling'] }}"}, "arguments": {}}],
        }, None)
        self.assertEqual(edited["operation"], "light.turn_on")
        self.assertEqual(edited["target"], {"kind": "template", "value": "{{ ['light.ceiling'] }}"})
        studio_core.check_document(book(commands=[edited]))
        self.assertEqual(studio_core.spell_capability(edited), "EXPERT ONLY")
        self.assertEqual(studio_core.wizard_incompatibilities(edited), [
            "Schritt 1: Ziel-Art „template“ (Jinja) wird vom Wizard nicht unterstützt.",
        ])

    async def test_wizard_spell_one_query_step_builds_flat_sources(self):
        edited = self.studio.wizard_spell({
            "triggers": ["wie viele lichter sind an"], "trigger_language": "de",
            "translations": {"catchphrase": "{n} Lichter sind an."},
            "steps": [{"kind": "query", "sources": [
                {"kind": "count", "target_kind": "all_allowed", "domain_filter": "light",
                 "state_filter": "on", "variable": "n"},
            ]}],
        }, None)
        self.assertEqual(edited["sources"][0]["variable"], "n")
        for field in ("operation", "target", "arguments", "steps"):
            self.assertNotIn(field, edited)
        studio_core.check_document(book(commands=[edited]))
        self.assertEqual(studio_core.wizard_incompatibilities(edited), [])

    async def test_wizard_spell_two_steps_builds_steps_shape(self):
        edited = self.studio.wizard_spell({
            "triggers": ["gute nacht"], "trigger_language": "de",
            "steps": [
                {"kind": "action", "alias": "shutters", "action": "cover.close_cover:0", "target_kind": "all_allowed"},
                {"kind": "action", "alias": "lights", "action": "light.turn_off:0", "target_kind": "all_allowed"},
            ],
        }, None)
        self.assertEqual(len(edited["steps"]), 2)
        for field in ("operation", "target", "arguments", "sources"):
            self.assertNotIn(field, edited)
        studio_core.check_document(book(commands=[edited]))

    async def test_wizard_spell_roundtrips_step_count_both_directions(self):
        one = self.studio.wizard_spell({
            "id": "s", "triggers": ["do it"], "trigger_language": "de",
            "steps": [{"kind": "action", "action": "light.turn_on:0", "target_kind": "all_allowed"}],
        }, None)
        self.assertIn("operation", one)
        two = self.studio.wizard_spell({
            "id": "s", "triggers": ["do it"], "trigger_language": "de",
            "steps": [
                {"kind": "action", "alias": "a", "action": "light.turn_on:0", "target_kind": "all_allowed"},
                {"kind": "action", "alias": "b", "action": "light.turn_off:0", "target_kind": "all_allowed"},
            ],
        }, one)
        self.assertIn("steps", two)
        self.assertNotIn("operation", two)
        back = self.studio.wizard_spell({
            "id": "s", "triggers": ["do it"], "trigger_language": "de",
            "steps": [{"kind": "action", "action": "light.turn_on:0", "target_kind": "all_allowed"}],
        }, two)
        self.assertIn("operation", back)
        self.assertNotIn("steps", back)
        studio_core.check_document(book(commands=[back]))

    async def test_wizard_spell_preserve_step_works_from_a_flat_old_spell(self):
        # as_logical_steps() normalizes a flat old spell into a one-item
        # list, so `{"kind": "preserve", "index": 0}` also works when
        # growing a legacy flat spell into a multi-step one.
        old = self.studio.wizard_spell({
            "triggers": ["licht an"], "trigger_language": "de",
            "steps": [{"kind": "action", "action": "light.turn_on:0", "target_kind": "all_allowed"}],
        }, None)
        grown = self.studio.wizard_spell({
            "id": old["id"], "triggers": ["licht an"], "trigger_language": "de",
            "steps": [
                {"kind": "preserve", "index": 0},
                {"kind": "action", "alias": "b", "action": "light.turn_off:0", "target_kind": "all_allowed"},
            ],
        }, old)
        self.assertEqual(grown["steps"][0]["operation"], "light.turn_on")
        studio_core.check_document(book(commands=[grown]))

    async def test_wizard_spell_max_twelve_steps(self):
        with self.assertRaises(studio_core.StudioError):
            self.studio.wizard_spell({
                "triggers": ["do it"], "trigger_language": "de",
                "steps": [{"kind": "action", "action": "light.turn_on:0", "target_kind": "all_allowed"}] * 13,
            }, None)

    async def test_wizard_incompatibilities_flags_expert_block_and_non_catalog_operation(self):
        with_expert_block = spell(expert={"operation": "light.turn_off", "target": {"kind": "all_allowed"}, "arguments": {}})
        self.assertIn("Enthält eine separate Expert-Variante (`expert:`-Block).",
                      studio_core.wizard_incompatibilities(with_expert_block))
        # arguments={"on": True} is a fixture convenience, not a real
        # light.set_power preset (that operation's only user_choices entry
        # is a captured `percentage` - see operations.py) - a genuinely
        # non-catalogued combination, exactly what this should flag.
        custom_arguments = spell(arguments={"on": True, "brightness": 7})
        self.assertTrue(studio_core.wizard_incompatibilities(custom_arguments))
        catalog_match = spell(operation="cover.close_cover", target={"kind": "all_allowed"}, arguments={})
        self.assertEqual(studio_core.wizard_incompatibilities(catalog_match), [])

    async def test_wizard_step_flags_marks_only_the_expert_only_step(self):
        raw = {"id": "night_mode", "trigger": "gute nacht", "steps": [
            {"alias": "shutters", "operation": "cover.close_cover", "target": {"kind": "template", "value": "{{ [] }}"}, "arguments": {}},
            {"alias": "lights", "operation": "light.turn_off", "target": {"kind": "all_allowed"}, "arguments": {}},
        ]}
        self.assertEqual(studio_core.wizard_step_flags(raw), [True, False])
        catalog_match = spell(operation="cover.close_cover", target={"kind": "all_allowed"}, arguments={})
        self.assertEqual(studio_core.wizard_step_flags(catalog_match), [False])

    async def test_translate_text_only_spell_without_turning_it_into_a_device_action(self):
        original = {"id":"greeting", "trigger":{"de":"Hallo"}, "output":{"de":"Guten Tag"}, "enabled":False}
        edited = self.studio.user_spell({"action":"__preserve__", "trigger_language":"en", "default_language":"de",
            "triggers":["Hello"], "output":"Good day"}, original)
        self.assertEqual(edited["output"], {"de":"Guten Tag", "en":"Good day"})
        self.assertNotIn("operation", edited)
        self.assertNotIn("target", edited)
        self.assertFalse(edited["enabled"])
        studio_core.check_document(book(commands=[edited]))

    async def test_permission_updates_only_selected_book(self):
        await self.studio.write(book(),None)
        await self.studio.dispatch("expert_permission",{"book_id":"testbook","allowed":False},None)
        self.assertFalse(self.studio.expert_allowed("testbook"))
        self.assertFalse(self.studio.expert_allowed("hdr"))
        self.studio.permissions.async_save.assert_awaited_once()

    async def test_sidebar_defaults_off_and_restores_independently(self):
        self.studio.permissions = N(async_load=AsyncMock(return_value={"testbook": True}))
        for saved, expected in ((None, False), ({"show_in_sidebar": True}, True),
                                ({"show_in_sidebar": "true"}, False)):
            self.studio.ui_settings = N(async_load=AsyncMock(return_value=saved))
            await self.studio.async_load()
            self.assertEqual(self.hass.data["hase_studio_sidebar"], expected)
            self.assertEqual(self.hass.data["hase_studio_permissions"], {"testbook": True})

    async def test_sidebar_save_preserves_book_permissions(self):
        self.studio.ui_settings = N(async_save=AsyncMock())
        calls = []
        api = N(async_update_panel=lambda hass: calls.append(hass))
        with patch.dict(sys.modules, {"hase_studio_test.studio_api": api}):
            result = await self.studio.dispatch("ui_settings", {"show_in_sidebar": True}, None)
        self.assertEqual(result, {"show_in_sidebar": True})
        self.studio.ui_settings.async_save.assert_awaited_once_with({"show_in_sidebar": True})
        self.assertEqual(self.hass.data["hase_studio_permissions"], {"testbook": True, "hdr": False})
        self.assertEqual(calls, [self.hass])

    async def test_user_edit_preserves_other_trigger_languages(self):
        original = spell(trigger={"de": "Licht an", "en": "Lights on"})
        edited = self.studio.user_spell({"id": "lumos", "action": "light.turn_on:0",
            "target_kind": "source_area", "triggers": ["Illuminate"], "trigger_language": "en"}, original)
        self.assertEqual(edited["trigger"], {"de": "Licht an", "en": "Illuminate"})
        self.assertEqual(original["trigger"], {"de": "Licht an", "en": "Lights on"})

    async def test_export_never_contains_local_permission(self):
        await self.studio.write(book(),None)
        result=await self.studio.dispatch("export",{"book_id":"testbook"},None)
        self.assertEqual(yaml.safe_load(result["yaml"]),book())
        self.assertNotIn("expert_allowed",result["yaml"])

    async def test_execution_requires_confirmation_and_current_revision(self):
        revision=await self.studio.write(book(),None)
        for payload in ({"revision":revision},{"confirmed":True,"revision":"old"}):
            with self.assertRaises(studio_core.StudioError):
                await self.studio.dispatch("execute",{"book_id":"testbook",**payload},None)

    async def test_placeholder_test_phrases_stop_before_matching_and_fallback(self):
        raw = book(commands=[spell(trigger="lumos {area}", target={"kind":"area_name", "capture":"area"},
            expert={"operation":"light.toggle", "arguments":{}, "target":{"kind":"area_name", "capture":"area"}})])
        with patch.object(adapter, "explain") as matcher, patch.object(adapter, "ActionExecutor") as runner:
            for execute in (False, True):
                for phrase in ("lumos {area}", "lumos {area]", "lumos area}", "lumos [area]", " "):
                    with self.subTest(execute=execute, phrase=phrase), self.assertRaisesRegex(studio_core.StudioError, "konkrete Testphrase"):
                        await self.studio.test(raw, {"phrase":phrase}, execute=execute)
            matcher.assert_not_called()
            runner.assert_not_called()

    async def test_enabled_expert_editor_executes_unsaved_toggle_not_stored_set_power(self):
        stored = spell(trigger="lumos {area}", target={"kind":"area_name", "capture":"area"})
        revision = await self.studio.write(book(commands=[stored]), None)
        draft = {**stored, "expert":{"operation":"light.toggle", "arguments":{},
                                     "target":{"kind":"area_name", "capture":"area"}}}
        area = models.AreaPolicy(frozenset({"gondor"}))
        self.studio.studio_profiles = lambda: [("client", "Client", models.ScopedAreaPolicy(area, area))]
        self.studio.operation_allowed = lambda *_: True
        resolver = N(resolve=AsyncMock(return_value=executor.ResolvedTargets((executor.ResolvedEntity("light.gondor", "gondor"),))))
        caller = N(call_service=AsyncMock(return_value=True), set_light_power=AsyncMock(return_value=True))
        payload = {"book_id":"testbook", "revision":revision, "mode":"expert", "spell":draft,
                   "persist_expert":False, "fallback_mode":"keep", "phrase":"lumos Gondor",
                   "profile_id":"client", "variant":"runtime", "confirmed":True}
        with patch.object(adapter, "HaTargetResolver", return_value=resolver), patch.object(adapter, "HaServiceCaller", return_value=caller):
            preview = await self.studio.dispatch("dry_run", payload, None)
            self.assertEqual(preview["operation"], "light.toggle")
            caller.call_service.assert_not_awaited()
            result = await self.studio.dispatch("execute", payload, None)
            self.assertEqual(result["operation"], "light.toggle")
            self.assertNotIn("user_fallback", result["data"])
            caller.call_service.assert_awaited_once_with("light", "toggle", ("light.gondor",), {}, context=None)
            caller.set_light_power.assert_not_awaited()
            resolver.resolve.return_value = executor.ResolvedTargets(())
            failed = await self.studio.dispatch("execute", payload, None)
            self.assertEqual(failed["operation"], "light.toggle")
            self.assertNotIn("user_fallback", failed["data"])
            caller.set_light_power.assert_not_awaited()
        self.assertNotIn("expert", (await self.studio.read("testbook"))[0]["commands"][0])

    async def test_frontend_payload_cannot_enable_expert_execution(self):
        self.hass.data["hase_studio_permissions"]["testbook"] = False
        raw=book(commands=[{"id":"x","trigger":"x","expert":{"operation":"light.set_power","arguments":{"on":True},"target":{"kind":"template","value":"{{ [] }}"}}}])
        revision=await self.studio.write(raw,None)
        with patch.object(adapter,"HaTargetResolver") as resolver, patch.object(adapter,"HaServiceCaller") as caller:
            result=await self.studio.dispatch("execute",{"book_id":"testbook","revision":revision,
                "confirmed":True,"phrase":"x","variant":"expert","expert_allowed":True},None)
        self.assertEqual(result["reason_code"],"expert_disabled")
        resolver.return_value.resolve.assert_not_called()
        caller.return_value.set_light_power.assert_not_called()

    async def test_import_preserves_permissions_and_book_identity(self):
        revision=await self.studio.write(book(),None)
        self.studio.report=AsyncMock(return_value={})
        await self.studio.dispatch("import_apply",{"book_id":"testbook","revision":revision,
            "text":yaml.safe_dump(book("cc",[spell(id="new",tags=["Collection"])]))},None)
        result=(await self.studio.read("testbook"))[0]
        self.assertEqual(result["id"],"testbook")
        self.assertEqual(len(result["commands"]),2)
        self.assertEqual(self.hass.data["hase_studio_permissions"],{"testbook":True,"hdr":False})

    async def test_check_mcp_reports_structural_capability_and_actual_grant_separately(self):
        # The user's own explicit requirement: Studio's "MCP prüfen" must
        # show structural capability (projectable) and actual grant
        # (requires_unrestricted_bridge) as two SEPARATE facts, never one
        # combined verdict.
        assist_redirect_spell = {
            "id": "sunrise", "trigger": "sunrise over {area}",
            "output": "Set light {area} to 100%.",
        }
        await self.studio.write(book(commands=[spell(), assist_redirect_spell]), None)
        result = await self.studio.dispatch("check_mcp", {"book_id": "testbook"}, None)
        self.assertEqual(result["projectable"], 2)
        self.assertEqual(result["not_projectable"], 0)
        self.assertEqual(result["requires_unrestricted"], 1)
        by_id = {s["id"]: s for s in result["spells"]}
        self.assertEqual(by_id["lumos"]["execution_kind"], "action")
        self.assertTrue(by_id["lumos"]["projectable"])
        self.assertFalse(by_id["lumos"]["requires_unrestricted_bridge"])
        self.assertEqual(by_id["sunrise"]["execution_kind"], "assist_redirect")
        self.assertTrue(by_id["sunrise"]["projectable"])
        self.assertTrue(by_id["sunrise"]["requires_unrestricted_bridge"])
        self.assertEqual(by_id["sunrise"]["grant_reason"], "assist_redirect_requires_unrestricted_policy")

    async def test_check_mcp_persists_status_onto_each_spell_via_metadata(self):
        # The "automatically saved status" the docs promise: "MCP prüfen"
        # must not just return a report, it must write it back onto the
        # book on disk, in the Spellbook itself (never a separate store),
        # under the already-generic, already-engine-ignored `metadata`
        # field every spell already permits.
        await self.studio.write(book(commands=[spell()]), None)
        result = await self.studio.dispatch("check_mcp", {"book_id": "testbook"}, None)
        self.assertIn("revision", result)
        raw_after, revision_after = await self.studio.read("testbook")
        self.assertEqual(revision_after, result["revision"])
        [raw_spell] = raw_after["commands"]
        cache = raw_spell["metadata"]["mcp"]
        self.assertEqual(cache["execution_kind"], "action")
        self.assertTrue(cache["projectable"])
        self.assertIn("spell_hash", cache)
        self.assertIn("schema_version", cache)

    async def test_saving_any_spell_edit_refreshes_the_persisted_mcp_status(self):
        # "Status automatisch beim Buch-Speichern pro Spell neu berechnen" -
        # not just on an explicit "MCP prüfen" click, but on every ordinary
        # save too (write() is the single hook, see attach_mcp_capability_
        # cache). A newly added text-only Spell gets a cache entry the very
        # first time the book is saved, with no "MCP prüfen" click at all.
        revision = await self.studio.write(book(commands=[spell()]), None)
        raw, revision = await self.studio.read("testbook")
        raw["commands"].append({"id": "new_text", "trigger": "tell me something", "output": "Hi."})
        await self.studio.write(raw, revision)
        raw_after, _ = await self.studio.read("testbook")
        by_id = {c["id"]: c for c in raw_after["commands"]}
        self.assertEqual(by_id["new_text"]["metadata"]["mcp"]["execution_kind"], "assist_redirect")
        self.assertEqual(by_id["lumos"]["metadata"]["mcp"]["execution_kind"], "action")

    async def test_read_reports_mcp_status_without_needing_a_check_mcp_click(self):
        # "Badges zeigen auch ohne Klick den aktuellen Stand" - the plain
        # "read" operation (opening a book in the editor) must already
        # carry a usable mcp_report, sourced from the persisted cache.
        await self.studio.write(book(commands=[spell()]), None)
        result = await self.studio.dispatch("read", {"book_id": "testbook"}, None)
        self.assertIn("mcp_report", result)
        [entry] = result["mcp_report"]["spells"]
        self.assertEqual(entry["id"], "lumos")
        self.assertTrue(entry["projectable"])
        self.assertFalse(entry["stale"])
        self.assertEqual(result["mcp_report"]["stale_count"], 0)

    async def test_read_flags_a_never_checked_spell_as_stale(self):
        # A book placed on disk by something other than Studio's own
        # write() (here: standing in for a bundled/legacy book, or a
        # directly-edited YAML file) has no metadata.mcp at all. It must
        # still show its real, freshly-computed numbers - just flagged
        # `stale`, never silently presented as an already-saved status.
        import os

        directory = Path(self.hass.config.path(adapter.USER_BOOK_SUBDIR))
        directory.mkdir(parents=True, exist_ok=True)
        raw = book(commands=[spell()])
        (directory / "testbook.yaml").write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        result = await self.studio.dispatch("read", {"book_id": "testbook"}, None)
        [entry] = result["mcp_report"]["spells"]
        self.assertTrue(entry["stale"])
        self.assertTrue(entry["projectable"])  # numbers are still real/fresh, just not yet saved
        self.assertEqual(result["mcp_report"]["stale_count"], 1)

    async def test_check_mcp_reconciles_a_stale_cache_back_to_current(self):
        await self.studio.write(book(commands=[spell()]), None)
        raw, revision = await self.studio.read("testbook")
        raw["commands"][0]["metadata"] = {"mcp": {"execution_kind": "query", "schema_version": 999}}
        await self.studio.write(raw, revision)
        # write() itself refreshes the cache - it should already be
        # current again after this save, without a separate "MCP prüfen".
        read_result = await self.studio.dispatch("read", {"book_id": "testbook"}, None)
        [entry] = read_result["mcp_report"]["spells"]
        self.assertFalse(entry["stale"])
        self.assertEqual(entry["execution_kind"], "action")

    async def test_check_mcp_reports_unknown_operation_as_not_projectable(self):
        broken_spell = {
            "id": "broken", "trigger": "do a thing in {area}", "operation": "not.a.real.operation",
            "target": {"kind": "area_id", "capture": "area"}, "arguments": {},
        }
        await self.studio.write(book(commands=[broken_spell]), None)
        result = await self.studio.dispatch("check_mcp", {"book_id": "testbook"}, None)
        self.assertEqual(result["projectable"], 0)
        self.assertEqual(result["not_projectable"], 1)
        [row] = result["spells"]
        self.assertFalse(row["projectable"])
        self.assertEqual(row["skip_reason"], "unknown_operation:not.a.real.operation")


class PerWizardStepValidationTests(unittest.IsolatedAsyncioTestCase):
    """Per-wizard-step validation architecture: one shared functional base
    (describe_spell_basics, ActionExecutor.describe_shape, _load_command's
    structured diagnostics, static_report), composed additively per step by
    core.studio_validation.validate_wizard_step. See the approved plan
    "Per-Wizard-Step Validation for Spellbook Studio"."""

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.hass = N(data={"hase_studio_permissions": {"testbook": True}},
                      config=N(path=lambda part: str(Path(self.directory.name) / part)))
        async def job(fn, *args):
            return fn(*args)
        self.hass.async_add_executor_job = job
        self.studio = adapter.SpellbookStudio.__new__(adapter.SpellbookStudio)
        self.studio.hass = self.hass
        self.studio.lock = asyncio.Lock()
        self.studio.last_reports = {}
        self.studio.permissions = N(async_save=AsyncMock())
        self.studio.registry = lambda: {"areas": [], "entities": [], "devices": []}
        self.studio.profiles = lambda book_id: []
        self.studio.studio_profiles = list
        source = Path(self.directory.name) / "bundled"
        source.mkdir()
        self.patch = patch("hase_studio_test.spellbook_loader.BUILTIN_BOOK_DIR", source)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    # -- describe_spell_basics (Step 0: Basic info) --------------------

    def test_describe_spell_basics_has_no_issues_for_a_valid_spell(self):
        # owner_spell_id="lumos": editing the very spell already saved
        # under that id in the book - must not flag itself as a duplicate.
        self.assertEqual(studio_core.describe_spell_basics(spell(), book=book(), owner_spell_id="lumos"), [])

    def test_describe_spell_basics_flags_missing_id_with_its_own_path(self):
        broken = spell()
        del broken["id"]
        issues = studio_core.describe_spell_basics(broken, book=book(commands=[broken]))
        self.assertTrue(any(i["code"] == "missing_id" and i["path"] == "id" for i in issues))

    def test_describe_spell_basics_flags_a_genuine_duplicate_id_but_not_self(self):
        original = spell()
        duplicate = spell()
        raw_book = book(commands=[original, duplicate])
        # Editing `original` in place (owner_spell_id=its own id) must not
        # flag it against itself, even though a second, genuinely
        # duplicate entry also shares that id.
        issues_as_owner = studio_core.describe_spell_basics(original, book=raw_book, owner_spell_id="lumos")
        self.assertTrue(any(i["code"] == "duplicate_id" for i in issues_as_owner))
        fixed_book = book(commands=[original])
        self.assertEqual(
            [i for i in studio_core.describe_spell_basics(original, book=fixed_book, owner_spell_id="lumos")
             if i["code"] == "duplicate_id"],
            [],
        )

    # -- ActionExecutor.describe_shape (Step 1: Actions & Queries) -----

    def test_describe_shape_reports_one_issue_per_violation(self):
        checker = executor.ActionExecutor(None, None)
        unknown_op = checker.describe_shape(models.ActionDraft(
            operation="not.a.real.operation", target=models.ActionTarget(kind="all_allowed", value=None), arguments={},
        ))
        self.assertEqual([i["code"] for i in unknown_op], ["unknown_operation"])

        bad_target_kind = checker.describe_shape(models.ActionDraft(
            operation="entity.get_state", target=models.ActionTarget(kind="all_allowed", value=None), arguments={},
        ))
        self.assertEqual([i["code"] for i in bad_target_kind], ["target_kind_not_allowed"])

        missing_and_unknown_argument = checker.describe_shape(models.ActionDraft(
            operation="light.set_power", target=models.ActionTarget(kind="source_area", value=None), arguments={"bogus": True},
        ))
        codes = {i["code"] for i in missing_and_unknown_argument}
        self.assertIn("missing_required_argument", codes)
        self.assertIn("unknown_argument", codes)
        self.assertTrue(all(i["path"].startswith("arguments.") for i in missing_and_unknown_argument))

        self.assertEqual(checker.describe_shape(models.ActionDraft(
            operation="light.set_power", target=models.ActionTarget(kind="source_area", value=None), arguments={"on": True},
        )), [])

    # -- _load_command structured diagnostics (Step 2/3: Trigger/Response) --

    def test_load_command_diagnostics_report_a_dangling_capture_only_when_asked(self):
        broken = {
            "id": "night", "trigger": "gute nacht",  # no {area} placeholder
            "operation": "light.set_power", "target": {"kind": "area_id", "capture": "area"},
            "arguments": {"on": True},
        }
        self.assertIsNone(engine._load_command("night", broken))  # diagnostics=None: unchanged default behavior
        diagnostics = []
        self.assertIsNone(engine._load_command("night", broken, diagnostics=diagnostics))
        self.assertTrue(any(d["code"] == "unknown_capture" and d["path"] == "target.capture" for d in diagnostics))

    def test_load_command_diagnostics_report_an_unknown_response_placeholder(self):
        broken = {"id": "greet", "trigger": "hallo", "output": "Hallo {nonsense}"}
        diagnostics = []
        self.assertIsNone(engine._load_command("greet", broken, diagnostics=diagnostics))
        self.assertTrue(any(d["code"] == "unknown_response_placeholder" and d["path"] == "output" for d in diagnostics))

    # -- classify_spell: Wizard-compatible / Expert-only / Invalid -----

    def test_classify_spell_three_states(self):
        # spell()'s literal {"on": True} is a structurally valid
        # light.set_power call, but not one of its own user_choices
        # presets (only "percentage" is) - genuinely expert_only, not a
        # good "wizard" example. Use an exact preset match instead.
        wizard_ready = {
            "id": "dim", "trigger": "dim {value}", "operation": "light.set_power",
            "target": {"kind": "area_id", "value": "living"},
            "arguments": {"percentage": {"capture": "value", "type": "int", "min": 0, "max": 100}},
        }
        self.assertEqual(studio_core.classify_spell(wizard_ready), "wizard")
        self.assertEqual(studio_core.classify_spell(spell()), "expert_only")
        self.assertEqual(studio_core.classify_spell({**spell(), "target": {"kind": "template", "value": "{{ [] }}"}}), "expert_only")
        self.assertEqual(studio_core.classify_spell({**spell(), "operation": "not.a.real.operation"}), "invalid")

    # -- validate_wizard_step: additive, no first-discovery-at-review ---

    def test_step_1_argument_error_is_not_first_discovered_at_step_4(self):
        raw_book = book(commands=[])
        candidate = {
            "id": "lumos", "trigger": "Lumos", "operation": "light.set_power",
            "target": {"kind": "source_area"}, "arguments": {"bogus": True},
        }
        step0 = studio_validation.validate_wizard_step(0, candidate, book=raw_book)
        self.assertEqual([i for i in step0 if i["code"] in ("missing_required_argument", "unknown_argument")], [])
        step1 = studio_validation.validate_wizard_step(1, candidate, book=raw_book)
        codes = {i["code"] for i in step1}
        self.assertIn("missing_required_argument", codes)
        self.assertIn("unknown_argument", codes)
        # Additive by construction: every later step still reports it.
        for step in (2, 3, 4):
            later_codes = {i["code"] for i in studio_validation.validate_wizard_step(step, candidate, book=raw_book)}
            self.assertTrue({"missing_required_argument", "unknown_argument"} <= later_codes)

    def test_trigger_capture_error_is_caught_at_step_2_not_before(self):
        raw_book = book()
        candidate = {
            "id": "night", "trigger": "gute nacht",  # dangling {area} capture
            "operation": "light.set_power", "target": {"kind": "area_id", "capture": "area"},
            "arguments": {"on": True},
        }
        step1 = studio_validation.validate_wizard_step(1, candidate, book=raw_book)
        self.assertEqual([i for i in step1 if i["code"] == "unknown_capture"], [],
                          "Step 1 (Actions & Queries) does not yet know about triggers")
        step2 = studio_validation.validate_wizard_step(2, candidate, book=raw_book)
        self.assertTrue(any(i["code"] == "unknown_capture" for i in step2))

    def test_response_placeholder_error_is_caught_at_step_3_not_before(self):
        raw_book = book()
        candidate = {
            "id": "greet", "trigger": "hallo", "output": "Hallo {nonsense}",
        }
        step2 = studio_validation.validate_wizard_step(2, candidate, book=raw_book)
        self.assertEqual([i for i in step2 if i["code"] == "unknown_response_placeholder"], [])
        step3 = studio_validation.validate_wizard_step(3, candidate, book=raw_book)
        self.assertTrue(any(i["code"] == "unknown_response_placeholder" for i in step3))

    def test_step_4_still_runs_as_a_full_document_safety_net(self):
        raw_book = book()
        # A dangling area-name trigger reference that never yielded a
        # valid EngineCommand at all - static_report's own compile step
        # (via _load_command) is the only thing that can still catch this
        # kind of cross-cutting failure, exactly the "safety net" role.
        candidate = {"id": "broken", "trigger": "x", "operation": "light.set_power",
                     "target": {"kind": "area_id", "capture": "missing"}, "arguments": {"on": True}}
        issues = studio_validation.validate_wizard_step(4, candidate, book=raw_book)
        self.assertTrue(issues)

    # -- wizard_spell(stage=...): tolerant assembly below the Trigger step --

    async def test_wizard_spell_stage_below_two_tolerates_a_missing_trigger(self):
        # Real save always defaults to stage=4 - unchanged, still requires
        # a trigger (test_wizard_spell_zero_steps_requires_a_response and
        # friends already cover that). Only an explicit stage < 2 (used by
        # validate_wizard_step for Basic info / Actions & Queries) may
        # tolerate an empty/missing triggers list.
        candidate = self.studio.wizard_spell(
            {"id": "draft", "name": "Draft", "translations": {}, "steps": []}, None, stage=0,
        )
        self.assertEqual(candidate["triggers"], [])
        self.assertEqual(candidate["trigger"], "")
        self.assertEqual(
            studio_core.describe_spell_basics(candidate, book=book(commands=[candidate]), owner_spell_id=candidate["id"]),
            [],
        )
        with self.assertRaises(studio_core.StudioError):
            self.studio.wizard_spell({"id": "draft", "translations": {}, "steps": []}, None, stage=2)

    # -- dispatch("validate_step"): the wizard's Next-button gate -------

    async def test_dispatch_validate_step_blocks_next_with_a_structured_argument_error(self):
        await self.studio.write(book(commands=[spell()]), None)
        result = await self.studio.dispatch("validate_step", {
            "book_id": "testbook", "step": 1, "mode": "wizard",
            "spell": {
                "id": "lumos", "translations": {}, "triggers": ["Lumos"], "trigger_language": "de",
                "steps": [{"kind": "action", "expert": True, "operation": "light.set_power",
                           "target": {"kind": "source_area"}, "arguments": {"bogus": True}}],
            },
        }, None)
        self.assertFalse(result["ok"])
        codes = {issue["code"] for issue in result["issues"]}
        self.assertTrue({"missing_required_argument", "unknown_argument"} & codes)

    async def test_dispatch_validate_step_allows_next_for_a_valid_step(self):
        await self.studio.write(book(commands=[spell()]), None)
        result = await self.studio.dispatch("validate_step", {
            "book_id": "testbook", "step": 1, "mode": "wizard",
            "spell": {
                "id": "lumos", "translations": {}, "triggers": ["Lumos"], "trigger_language": "de",
                "steps": [{"kind": "action", "expert": True, "operation": "light.set_power",
                           "target": {"kind": "source_area"}, "arguments": {"on": True}}],
            },
        }, None)
        self.assertTrue(result["ok"], result["issues"])

    async def test_dispatch_validate_step_action_draft_scope_checks_a_single_draft(self):
        await self.studio.write(book(commands=[spell()]), None)
        invalid = await self.studio.dispatch("validate_step", {
            "book_id": "testbook", "step": 1, "scope": "action_draft",
            "draft": {"operation": "light.set_power", "target": {"kind": "source_area"}, "arguments": {"bogus": True}},
        }, None)
        self.assertFalse(invalid["ok"])
        valid = await self.studio.dispatch("validate_step", {
            "book_id": "testbook", "step": 1, "scope": "action_draft",
            "draft": {"operation": "light.set_power", "target": {"kind": "source_area"}, "arguments": {"on": True}},
        }, None)
        self.assertTrue(valid["ok"])


class TemplateResolverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from test_unified_spellbooks import RealTargetExecutionTests
        RealTargetExecutionTests.setUp(self)
        self.ha.ActionTarget = models.ActionTarget

    async def test_raw_effective_and_exposure_are_distinct(self):
        class Template:
            def __init__(self,*args):
                pass
            def ensure_valid(self):
                pass
            def async_render(self,**kwargs):
                return ["light.a","light.private","light.group","light.missing","light.c"]
        with patch.dict(sys.modules,{"homeassistant.helpers.template":N(Template=Template)}):
            resolver=self.ha.HaTargetResolver(self.hass)
            result=await resolver.resolve(models.ActionTarget("template","{{ entities }}"),domain_filter="light")
        self.assertEqual([e.entity_id for e in result.entities],["light.a","light.c"])
        self.assertEqual(len(result.raw),5)
        self.assertEqual(len(result.diagnostics),3)

    async def test_invalid_list_does_not_partially_execute(self):
        resolver=self.ha.HaTargetResolver(self.hass)
        result=await resolver.resolve(models.ActionTarget("entity_ids",("light.a","light.private")),domain_filter="light")
        self.assertEqual(len(result.diagnostics),1)
        self.hass.services.async_call.assert_not_awaited()


class BookLanguageTests(unittest.TestCase):
    def test_fallback_chain_and_regional_language(self):
        resolve = adapter.language_text
        self.assertEqual(resolve({"de":"DE", "en":"EN"}, "de-CH", "fr"), "DE")
        self.assertEqual(resolve({"de":"DE", "en":"EN"}, "fr", "de"), "EN")
        self.assertEqual(resolve({"de":"DE", "fr":"FR"}, "it", "fr"), "FR")
        self.assertEqual(resolve({"de":"DE"}, "it", "en"), "DE")
        self.assertEqual(resolve({"de":"DE", "fr":"FR"}, "it", "en", "technical"), "technical")

    def test_languages_only_change_linguistic_fields(self):
        raw = {**book(), "default_language":"de"}
        extended = adapter.manage_language(raw, "add", "en", "de")
        self.assertEqual(extended["commands"][0]["trigger"], {"de":"Lumos", "en":"Lumos"})
        for field in ("operation", "target", "arguments"):
            self.assertEqual(extended["commands"][0][field], raw["commands"][0][field])
        changed = adapter.manage_language(extended, "default", "en")
        removed = adapter.manage_language(changed, "remove", "de")
        self.assertEqual(removed["commands"][0]["trigger"], {"en":"Lumos"})
        self.assertEqual(raw["commands"][0]["trigger"], "Lumos")
        with self.assertRaises(ValueError):
            adapter.manage_language(removed, "remove", "en")

    def test_changing_default_never_relabels_plain_content(self):
        raw = {**book(), "default_language":"de", "languages":["de", "en"]}
        changed = adapter.manage_language(raw, "default", "en")
        self.assertEqual(changed["name"], {"de":"Testbook"})
        self.assertEqual(changed["commands"][0]["trigger"], {"de":"Lumos"})

    def test_runtime_primary_alias_and_response_use_same_fallback(self):
        raw = {**book(commands=[spell(trigger={"de":"Licht an", "en":"Lights on"},
            triggers={"de":["Erhelle"], "en":["Illuminate"]}, catchphrase={"de":"Erledigt", "en":"Done"})]),
            "default_language":"de", "name":{"de":"Buch", "en":"Book"}}
        loaded = engine.load_spellbook_from_dict("testbook", raw)
        self.assertEqual(engine.find_command(loaded, "Lights on", "fr").catchphrase, "Done")
        self.assertEqual(engine.find_command(loaded, "Illuminate", "fr").command.spell_id, "lumos")
        self.assertIsNone(engine.find_command(loaded, "Licht an", "fr"))
        self.assertIsNone(engine.find_command(loaded, "Erhelle", "fr"))
        self.assertIsNotNone(engine.find_command(loaded, "Licht an", "de"))

    def test_runtime_default_only_and_technical_fallback(self):
        raw = {**book(commands=[spell(trigger={"de":"Licht an", "fr":"Allume"})]), "default_language":"fr"}
        loaded = engine.load_spellbook_from_dict("testbook", raw)
        self.assertIsNotNone(engine.find_command(loaded, "Allume", "it"))
        raw["default_language"] = "en"
        loaded = engine.load_spellbook_from_dict("testbook", raw)
        self.assertIsNone(engine.find_command(loaded, "Allume", "it"))
        self.assertIsNone(engine.find_command(loaded, "Licht an", "it"))
        raw["commands"][0]["trigger"] = {"de":"Licht an"}
        loaded = engine.load_spellbook_from_dict("testbook", raw)
        self.assertIsNotNone(engine.find_command(loaded, "Licht an", "it"))
