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


def book(book_id="potter", commands=None):
    return {"id": book_id, "name": book_id.title(), "commands": commands if commands is not None else [spell()]}


class DataTests(unittest.TestCase):
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

    def test_steps_spell_passes_check_document_and_is_user_capability(self):
        raw = book(commands=[self._steps_spell()])
        studio_core.check_document(raw)  # must not raise
        self.assertEqual(studio_core.spell_capability(raw["commands"][0]), "USER")
        self.assertEqual(studio_core.book_capability(raw), "USER")

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
        self.assertEqual(copied["id"], "potter")
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
        loaded = engine.load_spellbook_from_dict("potter", raw)
        self.assertEqual(engine.match(loaded, "Licht bitte").spell_id, "lumos")
        raw["commands"][0]["enabled"] = False
        self.assertIsInstance(engine.match(engine.load_spellbook_from_dict("potter", raw), "Lumos"), models.EngineNoMatch)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.resolver = N(resolve=AsyncMock(return_value=executor.ResolvedTargets((executor.ResolvedEntity("light.a", "living"),))),
                          resolve_area=AsyncMock(return_value="living"))
        self.caller = N(set_light_power=AsyncMock(return_value=True), get_entity_state=AsyncMock(), clean_vacuum_area=AsyncMock())
        area = models.AreaPolicy(frozenset({"living"}))
        self.policy = models.ScopedAreaPolicy(area, area)
        self.permissions = {"potter": False, "cc": True}

    def draft(self):
        loaded = engine.load_spellbook_from_dict("potter", book(commands=[spell(expert={"target":{"kind":"template","value":"{{ ['light.a'] }}"}})]))
        return engine.match(loaded, "Lumos").action_draft

    async def test_book_permissions_and_user_fallback(self):
        for book_id in ("potter", "cc"):
            runner = executor.ActionExecutor(self.resolver, self.caller, lambda book_id=book_id: self.permissions[book_id])
            result = await runner.execute(self.draft(), self.policy, dry_run=True)
            self.assertEqual(result.status, models.ExecutionStatus.SUCCESS)
            self.assertEqual(self.resolver.resolve.call_args.args[0].kind, "template" if book_id=="cc" else "area_id")
        self.caller.set_light_power.assert_not_awaited()

    async def test_expert_only_denied_without_resolution(self):
        raw = book(commands=[{"id":"x", "trigger":"x", "expert":{"operation":"light.set_power", "arguments":{"on":True}, "target":{"kind":"template","value":"{{ [] }}"}}}])
        draft = engine.match(engine.load_spellbook_from_dict("potter",raw),"x").action_draft
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
        loaded = engine.load_spellbook_from_dict("potter",book(commands=[spell(expert={"target":{"kind":"bogus"}})]))
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
        self.hass = N(data={"hase_studio_permissions":{"potter":True,"hdr":False}},
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
        raw,revision=await self.studio.read("potter")
        raw["name"]="Changed"
        await self.studio.write(raw,revision)
        with self.assertRaises(studio_core.StudioError):
            await self.studio.write(raw,revision)
        self.assertEqual((await self.studio.read("hdr"))[0]["name"],"Hdr")
        self.assertEqual(len(list((Path(self.directory.name)/"hase_spellbooks").glob("*.yaml"))),2)

    async def test_edit_bundled_preserves_original(self):
        original=yaml.safe_dump(book())
        (self.source/"potter.yaml").write_text(original,encoding="utf-8")
        raw,revision=await self.studio.read("potter")
        raw["name"]="Edited"
        await self.studio.write(raw,revision)
        self.assertEqual((self.source/"potter.yaml").read_text(encoding="utf-8"),original)
        self.assertEqual((await self.studio.read("potter"))[0]["name"],"Edited")

    async def test_cover_copy_override_and_reset_keep_original_and_neighbor(self):
        original = {**book(), "default_language": "de", "languages": ["de", "en"],
                    "description": {"de": "Original", "en": "Original"}}
        original_text = yaml.safe_dump(original)
        source = self.source / "different_basename.yml"
        source.write_text(original_text, encoding="utf-8")
        png = b"\x89PNG\r\n\x1a\nfixture"
        source.with_suffix(".png").write_bytes(png)
        info = await self.studio.file_info("potter", cover=True)
        self.assertEqual(info["origin"], "builtin")
        self.assertTrue(info["cover"].startswith("data:image/png;base64,"))
        raw, revision = await self.studio.read("potter")
        raw["description"]["fr"] = "Copie"
        revision = await self.studio.write(raw, revision)
        custom = Path(self.directory.name) / "hase_spellbooks" / source.name
        neighbor = custom.with_name("neighbor.png")
        neighbor.write_bytes(b"keep")
        self.assertEqual(custom.with_suffix(".png").read_bytes(), png)
        self.assertEqual((await self.studio.file_info("potter"))["origin"], "override")
        self.assertEqual((await self.studio.read("potter"))[0]["description"]["fr"], "Copie")
        result = await self.studio.dispatch("reset_book", {"book_id":"potter", "revision":revision, "confirmed":True}, None)
        self.assertTrue(result["restored"])
        self.assertFalse(custom.exists())
        self.assertFalse(custom.with_suffix(".png").exists())
        self.assertEqual(neighbor.read_bytes(), b"keep")
        self.assertEqual(source.read_text(encoding="utf-8"), original_text)
        self.assertEqual(source.with_suffix(".png").read_bytes(), png)
        self.assertEqual((await self.studio.read("potter"))[0], original)
        self.assertTrue(self.studio.expert_allowed("potter"))

    async def test_custom_delete_requires_confirmation_and_current_revision(self):
        revision = await self.studio.write(book(), None)
        path = Path(self.directory.name) / "hase_spellbooks/potter.yaml"
        path.with_suffix(".png").write_bytes(b"cover")
        for data in ({"revision":revision}, {"revision":"stale", "confirmed":True}):
            with self.assertRaises(studio_core.StudioError):
                await self.studio.dispatch("delete_book", {"book_id":"potter", **data}, None)
            self.assertTrue(path.exists())
            self.assertTrue(path.with_suffix(".png").exists())
        result = await self.studio.dispatch("delete_book", {"book_id":"potter", "revision":revision, "confirmed":True}, None)
        self.assertFalse(result["restored"])
        self.assertFalse(path.exists())
        self.assertFalse(path.with_suffix(".png").exists())
        self.assertNotIn("potter", self.hass.data["hase_studio_permissions"])
        self.assertIn("hdr", self.hass.data["hase_studio_permissions"])

    async def test_failed_first_save_rolls_back_custom_cover(self):
        source = self.source / "potter.yaml"
        source.write_text(yaml.safe_dump(book()), encoding="utf-8")
        source.with_suffix(".png").write_bytes(b"cover")
        raw, revision = await self.studio.read("potter")
        with patch.object(adapter.os, "replace", side_effect=OSError("write failed")), self.assertRaises(OSError):
            await self.studio.write(raw, revision)
        self.assertEqual(list((Path(self.directory.name) / "hase_spellbooks").iterdir()), [])
        self.assertTrue(source.exists())

    async def test_language_management_makes_full_custom_copy_and_roundtrips(self):
        raw = {**book(), "default_language":"de"}
        source = self.source / "potter.yaml"
        source.write_text(yaml.safe_dump(raw), encoding="utf-8")
        _, revision = await self.studio.read("potter")
        await self.studio.dispatch("manage_language", {"book_id":"potter", "revision":revision, "action":"add", "code":"fr", "source":"de"}, None)
        changed, _ = await self.studio.read("potter")
        self.assertEqual(changed["commands"][0]["trigger"], {"de":"Lumos", "fr":"Lumos"})
        self.assertEqual(changed["commands"][0]["target"], raw["commands"][0]["target"])
        self.assertEqual(studio_core.parse_document(yaml.safe_dump(studio_core.export_book(changed))), changed)
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
        await self.studio.dispatch("expert_permission",{"book_id":"potter","allowed":False},None)
        self.assertFalse(self.studio.expert_allowed("potter"))
        self.assertFalse(self.studio.expert_allowed("hdr"))
        self.studio.permissions.async_save.assert_awaited_once()

    async def test_sidebar_defaults_off_and_restores_independently(self):
        self.studio.permissions = N(async_load=AsyncMock(return_value={"potter": True}))
        for saved, expected in ((None, False), ({"show_in_sidebar": True}, True),
                                ({"show_in_sidebar": "true"}, False)):
            self.studio.ui_settings = N(async_load=AsyncMock(return_value=saved))
            await self.studio.async_load()
            self.assertEqual(self.hass.data["hase_studio_sidebar"], expected)
            self.assertEqual(self.hass.data["hase_studio_permissions"], {"potter": True})

    async def test_sidebar_save_preserves_book_permissions(self):
        self.studio.ui_settings = N(async_save=AsyncMock())
        calls = []
        api = N(async_update_panel=lambda hass: calls.append(hass))
        with patch.dict(sys.modules, {"hase_studio_test.studio_api": api}):
            result = await self.studio.dispatch("ui_settings", {"show_in_sidebar": True}, None)
        self.assertEqual(result, {"show_in_sidebar": True})
        self.studio.ui_settings.async_save.assert_awaited_once_with({"show_in_sidebar": True})
        self.assertEqual(self.hass.data["hase_studio_permissions"], {"potter": True, "hdr": False})
        self.assertEqual(calls, [self.hass])

    async def test_user_edit_preserves_other_trigger_languages(self):
        original = spell(trigger={"de": "Licht an", "en": "Lights on"})
        edited = self.studio.user_spell({"id": "lumos", "action": "light.turn_on:0",
            "target_kind": "source_area", "triggers": ["Illuminate"], "trigger_language": "en"}, original)
        self.assertEqual(edited["trigger"], {"de": "Licht an", "en": "Illuminate"})
        self.assertEqual(original["trigger"], {"de": "Licht an", "en": "Lights on"})

    async def test_export_never_contains_local_permission(self):
        await self.studio.write(book(),None)
        result=await self.studio.dispatch("export",{"book_id":"potter"},None)
        self.assertEqual(yaml.safe_load(result["yaml"]),book())
        self.assertNotIn("expert_allowed",result["yaml"])

    async def test_execution_requires_confirmation_and_current_revision(self):
        revision=await self.studio.write(book(),None)
        for payload in ({"revision":revision},{"confirmed":True,"revision":"old"}):
            with self.assertRaises(studio_core.StudioError):
                await self.studio.dispatch("execute",{"book_id":"potter",**payload},None)

    async def test_placeholder_test_phrases_stop_before_matching_and_fallback(self):
        raw = book(commands=[spell(trigger="lumos {area}", target={"kind":"area_name", "capture":"area"},
            expert={"operation":"light.toggle", "arguments":{}, "target":{"kind":"area_name", "capture":"area"}})])
        with patch.object(adapter, "find_command") as matcher, patch.object(adapter, "ActionExecutor") as runner:
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
        payload = {"book_id":"potter", "revision":revision, "mode":"expert", "spell":draft,
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
        self.assertNotIn("expert", (await self.studio.read("potter"))[0]["commands"][0])

    async def test_frontend_payload_cannot_enable_expert_execution(self):
        self.hass.data["hase_studio_permissions"]["potter"] = False
        raw=book(commands=[{"id":"x","trigger":"x","expert":{"operation":"light.set_power","arguments":{"on":True},"target":{"kind":"template","value":"{{ [] }}"}}}])
        revision=await self.studio.write(raw,None)
        with patch.object(adapter,"HaTargetResolver") as resolver, patch.object(adapter,"HaServiceCaller") as caller:
            result=await self.studio.dispatch("execute",{"book_id":"potter","revision":revision,
                "confirmed":True,"phrase":"x","variant":"expert","expert_allowed":True},None)
        self.assertEqual(result["reason_code"],"expert_disabled")
        resolver.return_value.resolve.assert_not_called()
        caller.return_value.set_light_power.assert_not_called()

    async def test_import_preserves_permissions_and_book_identity(self):
        revision=await self.studio.write(book(),None)
        self.studio.report=AsyncMock(return_value={})
        await self.studio.dispatch("import_apply",{"book_id":"potter","revision":revision,
            "text":yaml.safe_dump(book("cc",[spell(id="new",tags=["Collection"])]))},None)
        result=(await self.studio.read("potter"))[0]
        self.assertEqual(result["id"],"potter")
        self.assertEqual(len(result["commands"]),2)
        self.assertEqual(self.hass.data["hase_studio_permissions"],{"potter":True,"hdr":False})


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
        self.assertEqual(changed["name"], {"de":"Potter"})
        self.assertEqual(changed["commands"][0]["trigger"], {"de":"Lumos"})

    def test_runtime_primary_alias_and_response_use_same_fallback(self):
        raw = {**book(commands=[spell(trigger={"de":"Licht an", "en":"Lights on"},
            triggers={"de":["Erhelle"], "en":["Illuminate"]}, catchphrase={"de":"Erledigt", "en":"Done"})]),
            "default_language":"de", "name":{"de":"Buch", "en":"Book"}}
        loaded = engine.load_spellbook_from_dict("potter", raw)
        self.assertEqual(engine.find_command(loaded, "Lights on", "fr").catchphrase, "Done")
        self.assertEqual(engine.find_command(loaded, "Illuminate", "fr").command.spell_id, "lumos")
        self.assertIsNone(engine.find_command(loaded, "Licht an", "fr"))
        self.assertIsNone(engine.find_command(loaded, "Erhelle", "fr"))
        self.assertIsNotNone(engine.find_command(loaded, "Licht an", "de"))

    def test_runtime_default_only_and_technical_fallback(self):
        raw = {**book(commands=[spell(trigger={"de":"Licht an", "fr":"Allume"})]), "default_language":"fr"}
        loaded = engine.load_spellbook_from_dict("potter", raw)
        self.assertIsNotNone(engine.find_command(loaded, "Allume", "it"))
        raw["default_language"] = "en"
        loaded = engine.load_spellbook_from_dict("potter", raw)
        self.assertIsNone(engine.find_command(loaded, "Allume", "it"))
        self.assertIsNone(engine.find_command(loaded, "Licht an", "it"))
        raw["commands"][0]["trigger"] = {"de":"Licht an"}
        loaded = engine.load_spellbook_from_dict("potter", raw)
        self.assertIsNotNone(engine.find_command(loaded, "Licht an", "it"))
