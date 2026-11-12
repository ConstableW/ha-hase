"""Targeted tests for P8 – Generic Home-Assistant Todo domain.

Covers all four acceptance cases declared in the P8 spec:

    READ       – "Was steht auf der Einkaufsliste?"
    FILTERED   – "Was stehen heute noch für Aufgaben an?"
    CREATE     – "Füge {item} auf die Einkaufsliste hinzu."
    UPDATE     – "Markiere {item} als erledigt."

Deliberately uses a fake ServiceCaller (no HA runtime) so the tests
are fast, deterministic, and run in the cloud CI container without
any Home Assistant install.

Import pattern mirrors test_action_catalog_expansion.py: import
test_core_engine as core to get the shared package loader, FakeResolver,
FakeCaller and all shared types.

Design rules / P8 hard constraints verified here:
  - No special todo logic in Router/Matcher/Conversation/Combined-Spells
  - No own todo data store in HASE
  - No Todoist/Bring! special paths  (same operations work against all three)
  - `{item}` is a trigger capture resolved by spellbook_engine, NOT a new
    global placeholder
  - Combined-Spells semantics NOT extended
"""
from __future__ import annotations

import importlib
import unittest

import test_core_engine as core   # shared package loader + FakeResolver/FakeCaller

# Aliases matching the naming convention used elsewhere in the test suite
spellbook_engine = core.spellbook_engine
ActionExecutor   = core.ActionExecutor
ActionDraft      = core.ActionDraft
ActionTarget     = core.ActionTarget
AreaPolicy       = core.AreaPolicy
ScopedAreaPolicy = core.ScopedAreaPolicy
ExecutionStatus  = core.ExecutionStatus
ResolvedEntity   = core.ResolvedEntity
ResolvedTargets  = core.ResolvedTargets
TargetNotFound   = core.TargetNotFound
FakeResolver     = core.FakeResolver
FakeCaller       = core.FakeCaller
_scoped          = core._scoped

operations_mod = importlib.import_module("hase_spellbook_core.operations")
OPERATION_CATALOG = operations_mod.OPERATION_CATALOG


# ── Helpers ────────────────────────────────────────────────────────────────

def _resolver_for(entity_id: str, area_id: str = "home") -> FakeResolver:
    return FakeResolver({entity_id: ResolvedTargets((ResolvedEntity(entity_id, area_id),))})


def _policy() -> ScopedAreaPolicy:
    p = AreaPolicy(allowed_areas=None, unrestricted=True)
    return _scoped(p)


# ── FakeTodoCaller ─────────────────────────────────────────────────────────

class FakeTodoCaller(FakeCaller):
    """Extends core.FakeCaller with call_service_returning (generic response-
    service path) and a call_service recording, so both query and action
    operations can be exercised without a real HA runtime."""

    def __init__(self, items: list[dict] | None = None, *, accept: bool = True):
        super().__init__(accept=accept)
        self._todo_items = items   # None → simulate fetch error
        self.service_calls: list[tuple] = []

    async def call_service(self, domain, service, entity_ids, data, *, area_id=None, context=None):
        self.service_calls.append((domain, service, entity_ids, dict(data)))
        self.contexts.append(context)
        return self._accept

    async def call_service_returning(self, domain, service, entity_id, data, *, context=None):
        if self._todo_items is None:
            return None   # simulate hard fetch error
        items = list(self._todo_items)
        if data.get("status"):
            items = [i for i in items if i.get("status") == data["status"]]
        return {entity_id: {"items": items}}


# ── Shared fixture ─────────────────────────────────────────────────────────

_ITEMS_FIXTURE: list[dict] = [
    {"uid": "1", "summary": "Milch",   "status": "needs_action"},
    {"uid": "2", "summary": "Eier",    "status": "needs_action"},
    {"uid": "3", "summary": "Butter",  "status": "completed"},
    {"uid": "4", "summary": "Käse",    "status": "needs_action"},
]


# ══════════════════════════════════════════════════════════════════════════════
# 1. OPERATION CATALOG CONTRACT
# ══════════════════════════════════════════════════════════════════════════════

class TodoCatalogContractTests(unittest.TestCase):

    def test_all_four_todo_operations_present(self):
        for op in ("todo.list_items", "todo.add_item", "todo.update_item", "todo.remove_item"):
            self.assertIn(op, OPERATION_CATALOG, f"{op} missing from OPERATION_CATALOG")

    def test_list_items_is_query_with_todo_domain(self):
        spec = OPERATION_CATALOG["todo.list_items"]
        self.assertEqual(spec.kind, "query")
        self.assertEqual(spec.domain_filter, "todo")

    def test_action_operations_have_correct_service_tuples(self):
        expected = {
            "todo.add_item":    ("todo", "add_item"),
            "todo.update_item": ("todo", "update_item"),
            "todo.remove_item": ("todo", "remove_item"),
        }
        for op, svc in expected.items():
            self.assertEqual(OPERATION_CATALOG[op].service, svc, op)

    def test_all_todo_ops_use_entity_only_targets(self):
        for op in ("todo.list_items", "todo.add_item", "todo.update_item", "todo.remove_item"):
            spec = OPERATION_CATALOG[op]
            self.assertIn("entity_id",   spec.allowed_target_kinds, op)
            self.assertIn("entity_name", spec.allowed_target_kinds, op)
            self.assertNotIn("area_name", spec.allowed_target_kinds,
                             f"{op} should not allow area_name")

    def test_add_item_requires_item_argument(self):
        spec = OPERATION_CATALOG["todo.add_item"]
        self.assertIn("item", spec.argument_types)
        self.assertIs(spec.argument_types["item"], str)

    def test_update_item_has_optional_status_and_rename(self):
        spec = OPERATION_CATALOG["todo.update_item"]
        self.assertIn("status", spec.optional_argument_types)
        self.assertIn("rename", spec.optional_argument_types)

    def test_list_items_has_optional_status_filter(self):
        spec = OPERATION_CATALOG["todo.list_items"]
        self.assertIn("status", spec.optional_argument_types)
        self.assertNotIn("due_filter", spec.optional_argument_types,
                         "due_filter requires bespoke client-side logic (dropped in P8 generality repair)")

    def test_list_items_uses_generic_response_service_path(self):
        spec = OPERATION_CATALOG["todo.list_items"]
        self.assertTrue(spec.return_response, "todo.list_items must use generic return_response path")
        self.assertEqual(spec.service, ("todo", "get_items"))
        self.assertEqual(spec.response_list_path, ("items",))
        self.assertEqual(spec.response_list_text_field, "summary")

    def test_list_items_response_fields_declared_on_operation(self):
        spec = OPERATION_CATALOG["todo.list_items"]
        all_fields = {f for fields in spec.response_fields.values() for f in fields}
        for f in ("items", "count"):
            self.assertIn(f, all_fields, f"Missing {f!r} in todo.list_items response_fields")


# ══════════════════════════════════════════════════════════════════════════════
# 2. ACCEPTANCE CASE A – READ  ("Was steht auf der Einkaufsliste?")
# ══════════════════════════════════════════════════════════════════════════════

class ReadTodoListTests(unittest.IsolatedAsyncioTestCase):

    async def _run(self, items, arguments=None, response_template=None):
        entity_id = "todo.einkaufsliste"
        caller    = FakeTodoCaller(items=items)
        executor  = ActionExecutor(_resolver_for(entity_id), caller, expert_allowed=lambda: True)
        draft     = ActionDraft(
            "todo.list_items",
            ActionTarget("entity_id", entity_id),
            arguments or {},
            response_template=response_template,
        )
        return await executor.execute(draft, _policy())

    async def test_read_returns_query_answer(self):
        result = await self._run(_ITEMS_FIXTURE)
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.reason_code, "ok")

    async def test_items_field_contains_all_summaries(self):
        result = await self._run(_ITEMS_FIXTURE)
        for item in _ITEMS_FIXTURE:
            self.assertIn(item["summary"], result.data["items"])

    async def test_count_matches_total_item_count(self):
        result = await self._run(_ITEMS_FIXTURE)
        self.assertEqual(result.data["count"], len(_ITEMS_FIXTURE))

    async def test_empty_list_returns_zeroes_and_empty_string(self):
        result = await self._run([])
        self.assertEqual(result.data["items"], "")
        self.assertEqual(result.data["count"], 0)

    async def test_fetch_error_returns_failed(self):
        result = await self._run(None)   # None → caller simulates error
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(result.reason_code, "response_service_failed")

    async def test_response_template_is_rendered_into_response_text(self):
        result = await self._run(
            [{"uid": "1", "summary": "Milch", "status": "needs_action"}],
            response_template="Auf der Liste: {items} ({count} Eintrag)",
        )
        self.assertIn("response_text", result.data)
        self.assertIn("Milch",    result.data["response_text"])
        self.assertIn("1 Eintrag", result.data["response_text"])

    async def test_state_field_is_the_readable_list_not_the_count(self):
        # Regression ("40"): `state` used to be str(count), which is what the
        # spoken answer fell back to. The list is the answer; count is separate.
        result = await self._run(_ITEMS_FIXTURE)
        self.assertEqual(result.data["state"], result.data["items"])
        self.assertNotEqual(result.data["state"], str(len(_ITEMS_FIXTURE)))

    async def test_names_count_and_entries_are_structured(self):
        result = await self._run(_ITEMS_FIXTURE)
        self.assertEqual(result.data["names"], ["Milch", "Eier", "Butter", "Käse"])
        self.assertEqual(result.data["count"], len(result.data["names"]))
        self.assertEqual(len(result.data["entries"]), 4)

    async def test_default_answer_lists_todo_names_not_the_count(self):
        # No response_template (Guided default): the rendered answer is the tasks.
        response_text_for_success = importlib.import_module("hase_spellbook_core.executor").response_text_for_success
        many = [{"uid": str(i), "summary": f"Aufgabe {i}", "status": "needs_action"} for i in range(40)]
        result = await self._run(many)
        text = response_text_for_success(result)
        self.assertNotEqual(text, "40")
        self.assertIn("Aufgabe 0", text)
        self.assertIn("Aufgabe 39", text)

    async def test_default_answer_for_empty_list(self):
        response_text_for_success = importlib.import_module("hase_spellbook_core.executor").response_text_for_success
        text = response_text_for_success(await self._run([]))
        self.assertEqual(text, "Keine Einträge.")

    async def test_entries_keep_description_due_uid_status_when_supplied(self):
        full = {"uid": "u1", "summary": "Steuer", "status": "needs_action", "due": "2026-10-01", "description": "Belege"}
        result = await self._run([full, {"uid": "u2", "summary": "Milch", "status": "completed"}])
        self.assertEqual(result.data["entries"][0], full)
        self.assertEqual(result.data["entries"][1], {"uid": "u2", "summary": "Milch", "status": "completed"})

    async def test_todoist_and_bring_lists_behave_identically(self):
        # todo.inbox (Todoist) and todo.einkaufen (Bring) go through the same path.
        for entity_id in ("todo.inbox", "todo.einkaufen"):
            caller = FakeTodoCaller(items=_ITEMS_FIXTURE)
            executor = ActionExecutor(_resolver_for(entity_id), caller, expert_allowed=lambda: True)
            draft = ActionDraft("todo.list_items", ActionTarget("entity_id", entity_id), {})
            result = await executor.execute(draft, _policy())
            self.assertEqual(result.data["entity_id"], entity_id)
            self.assertEqual(result.data["names"], ["Milch", "Eier", "Butter", "Käse"])


# ══════════════════════════════════════════════════════════════════════════════
# 3. ACCEPTANCE CASE B – FILTERED READ  ("Was stehen heute noch für Aufgaben an?")
# ══════════════════════════════════════════════════════════════════════════════

class FilteredReadTodoListTests(unittest.IsolatedAsyncioTestCase):

    async def _run(self, items, arguments):
        entity_id = "todo.einkaufsliste"
        caller    = FakeTodoCaller(items=items)
        executor  = ActionExecutor(_resolver_for(entity_id), caller, expert_allowed=lambda: True)
        draft     = ActionDraft(
            "todo.list_items",
            ActionTarget("entity_id", entity_id),
            arguments,
        )
        return await executor.execute(draft, _policy())

    async def test_status_filter_forwarded_to_caller_and_applied(self):
        result = await self._run(_ITEMS_FIXTURE, {"status": "needs_action"})
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        items_str = result.data["items"]
        self.assertIn("Milch",   items_str)
        self.assertNotIn("Butter", items_str)   # completed → pre-filtered by caller

    async def test_status_filter_updates_count(self):
        result = await self._run(_ITEMS_FIXTURE, {"status": "needs_action"})
        incomplete_items = [i for i in _ITEMS_FIXTURE if i.get("status") == "needs_action"]
        self.assertEqual(result.data["count"], len(incomplete_items))


# ══════════════════════════════════════════════════════════════════════════════
# 4. ACCEPTANCE CASE C – CREATE  ("Füge {item} auf die Einkaufsliste hinzu.")
# ══════════════════════════════════════════════════════════════════════════════

class CreateTodoItemTests(unittest.IsolatedAsyncioTestCase):

    async def _run(self, item: str, entity_id: str = "todo.einkaufsliste"):
        caller   = FakeTodoCaller()
        executor = ActionExecutor(_resolver_for(entity_id), caller, expert_allowed=lambda: True)
        draft    = ActionDraft(
            "todo.add_item",
            ActionTarget("entity_id", entity_id),
            {"item": item},
        )
        result = await executor.execute(draft, _policy())
        return result, caller

    async def test_add_item_returns_success(self):
        result, _ = await self._run("Joghurt")
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)

    async def test_add_item_calls_todo_add_item_service(self):
        _, caller = await self._run("Joghurt")
        self.assertEqual(len(caller.service_calls), 1)
        domain, service, _, _ = caller.service_calls[0]
        self.assertEqual((domain, service), ("todo", "add_item"))

    async def test_add_item_passes_item_name_in_service_data(self):
        _, caller = await self._run("Joghurt")
        _, _, _, data = caller.service_calls[0]
        self.assertEqual(data["item"], "Joghurt")

    async def test_add_item_targets_the_correct_entity(self):
        _, caller = await self._run("Joghurt", "todo.inbox")
        _, _, entity_ids, _ = caller.service_calls[0]
        self.assertIn("todo.inbox", entity_ids)

    async def test_add_item_missing_item_arg_rejected_before_service_call(self):
        entity_id = "todo.einkaufsliste"
        caller    = FakeTodoCaller()
        executor  = ActionExecutor(_resolver_for(entity_id), caller, expert_allowed=lambda: True)
        draft     = ActionDraft(
            "todo.add_item",
            ActionTarget("entity_id", entity_id),
            {},   # missing required "item"
        )
        result = await executor.execute(draft, _policy())
        self.assertEqual(result.status, ExecutionStatus.INVALID)
        self.assertEqual(len(caller.service_calls), 0)

    async def test_add_item_service_rejection_returns_failed(self):
        entity_id = "todo.einkaufsliste"
        caller    = FakeTodoCaller(accept=False)
        executor  = ActionExecutor(_resolver_for(entity_id), caller, expert_allowed=lambda: True)
        draft     = ActionDraft(
            "todo.add_item",
            ActionTarget("entity_id", entity_id),
            {"item": "Joghurt"},
        )
        result = await executor.execute(draft, _policy())
        self.assertEqual(result.status, ExecutionStatus.FAILED)


# ══════════════════════════════════════════════════════════════════════════════
# 5. ACCEPTANCE CASE D – UPDATE/COMPLETE  ("Markiere {item} als erledigt.")
# ══════════════════════════════════════════════════════════════════════════════

class UpdateTodoItemTests(unittest.IsolatedAsyncioTestCase):
    """todo.update_item routes through generic call_service.

    HA resolves `item` by uid OR summary string server-side — HASE passes
    the captured item name directly; no uid lookup needed (P8 key audit finding).
    """

    async def _run(self, arguments: dict, entity_id: str = "todo.einkaufsliste"):
        caller   = FakeTodoCaller()
        executor = ActionExecutor(_resolver_for(entity_id), caller, expert_allowed=lambda: True)
        draft    = ActionDraft(
            "todo.update_item",
            ActionTarget("entity_id", entity_id),
            arguments,
        )
        result = await executor.execute(draft, _policy())
        return result, caller

    async def test_update_item_complete_returns_success(self):
        result, _ = await self._run({"item": "Milch", "status": "completed"})
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)

    async def test_update_item_passes_item_name_and_status_to_service(self):
        _, caller = await self._run({"item": "Milch", "status": "completed"})
        _, _, _, data = caller.service_calls[0]
        self.assertEqual(data["item"],   "Milch")
        self.assertEqual(data["status"], "completed")

    async def test_update_item_calls_todo_update_item_service(self):
        _, caller = await self._run({"item": "Milch", "status": "completed"})
        domain, service, _, _ = caller.service_calls[0]
        self.assertEqual((domain, service), ("todo", "update_item"))

    async def test_update_item_rename_optional_argument_forwarded(self):
        _, caller = await self._run({"item": "Milch", "rename": "Bio-Milch"})
        _, _, _, data = caller.service_calls[0]
        self.assertEqual(data.get("rename"), "Bio-Milch")

    async def test_update_item_without_item_arg_rejected(self):
        result, caller = await self._run({})
        self.assertEqual(result.status, ExecutionStatus.INVALID)
        self.assertEqual(len(caller.service_calls), 0)

    async def test_update_item_works_against_todoist_entity(self):
        result, caller = await self._run(
            {"item": "Buy milk", "status": "completed"}, "todo.inbox"
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        _, _, entity_ids, _ = caller.service_calls[0]
        self.assertIn("todo.inbox", entity_ids)

    async def test_update_item_works_against_bring_entity(self):
        result, caller = await self._run(
            {"item": "Milch", "status": "completed"}, "todo.einkaufen"
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        _, _, entity_ids, _ = caller.service_calls[0]
        self.assertIn("todo.einkaufen", entity_ids)


# ══════════════════════════════════════════════════════════════════════════════
# 6. REMOVE  (completeness)
# ══════════════════════════════════════════════════════════════════════════════

class RemoveTodoItemTests(unittest.IsolatedAsyncioTestCase):

    async def _run(self, item: str):
        entity_id = "todo.einkaufsliste"
        caller    = FakeTodoCaller()
        executor  = ActionExecutor(_resolver_for(entity_id), caller, expert_allowed=lambda: True)
        draft     = ActionDraft(
            "todo.remove_item",
            ActionTarget("entity_id", entity_id),
            {"item": item},
        )
        result = await executor.execute(draft, _policy())
        return result, caller

    async def test_remove_item_returns_success(self):
        result, _ = await self._run("Milch")
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)

    async def test_remove_item_calls_correct_service_with_item_data(self):
        _, caller = await self._run("Milch")
        domain, service, _, data = caller.service_calls[0]
        self.assertEqual((domain, service), ("todo", "remove_item"))
        self.assertEqual(data["item"], "Milch")


# ══════════════════════════════════════════════════════════════════════════════
# 7. SPELLBOOK-LEVEL INTEGRATION (trigger → capture → draft → execute)
# ══════════════════════════════════════════════════════════════════════════════

class SpellbookIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Walk a minimal spellbook through the real spellbook_engine, proving
    that the four acceptance-case trigger patterns → captures → correct
    executor calls — no mocking of the engine."""

    def _load(self, commands: list[dict]):
        book_dict = {"id": "todo_test", "name": "Todo Test", "commands": commands}
        return spellbook_engine.load_spellbook_from_dict("todo_test", book_dict)

    # ── load tests ─────────────────────────────────────────────────────────

    def test_read_spell_loads(self):
        book = self._load([{
            "id": "read_list", "trigger": "Was steht auf der Einkaufsliste",
            "operation": "todo.list_items",
            "target": {"kind": "entity_id", "value": "todo.einkaufsliste"},
            "arguments": {},
            "output": "Auf der Liste: {items}",
        }])
        self.assertIsNotNone(book, "read_list spell failed to load")

    def test_create_spell_loads_with_item_capture(self):
        book = self._load([{
            "id": "add_item",
            "trigger": "Füge {item} auf die Einkaufsliste hinzu",
            "operation": "todo.add_item",
            "target": {"kind": "entity_id", "value": "todo.einkaufsliste"},
            "arguments": {"item": {"capture": "item", "type": "str"}},
        }])
        self.assertIsNotNone(book, "add_item spell failed to load")

    def test_update_spell_loads_with_item_capture_and_static_status(self):
        book = self._load([{
            "id": "mark_done",
            "trigger": "Markiere {item} als erledigt",
            "operation": "todo.update_item",
            "target": {"kind": "entity_id", "value": "todo.einkaufsliste"},
            "arguments": {"item": {"capture": "item", "type": "str"}, "status": "completed"},
        }])
        self.assertIsNotNone(book, "mark_done spell failed to load")

    # ── match + capture tests ───────────────────────────────────────────────

    def test_read_spell_matches_trigger_phrase(self):
        book = self._load([{
            "id": "read_list", "trigger": "Was steht auf der Einkaufsliste",
            "operation": "todo.list_items",
            "target": {"kind": "entity_id", "value": "todo.einkaufsliste"},
            "arguments": {},
        }])
        found = spellbook_engine.find_command(book, "Was steht auf der Einkaufsliste", "de")
        self.assertIsNotNone(found)
        self.assertEqual(found.action_draft.operation, "todo.list_items")

    def test_create_spell_captures_item_from_phrase(self):
        book = self._load([{
            "id": "add_item",
            "trigger": "Füge {item} auf die Einkaufsliste hinzu",
            "operation": "todo.add_item",
            "target": {"kind": "entity_id", "value": "todo.einkaufsliste"},
            "arguments": {"item": {"capture": "item", "type": "str"}},
        }])
        found = spellbook_engine.find_command(book, "Füge Milch auf die Einkaufsliste hinzu", "de")
        self.assertIsNotNone(found)
        self.assertEqual(found.action_draft.arguments.get("item"), "Milch")

    def test_update_spell_captures_item_and_includes_static_status(self):
        book = self._load([{
            "id": "mark_done",
            "trigger": "Markiere {item} als erledigt",
            "operation": "todo.update_item",
            "target": {"kind": "entity_id", "value": "todo.einkaufsliste"},
            "arguments": {"item": {"capture": "item", "type": "str"}, "status": "completed"},
        }])
        found = spellbook_engine.find_command(book, "Markiere Milch als erledigt", "de")
        self.assertIsNotNone(found)
        self.assertEqual(found.action_draft.arguments.get("item"),   "Milch")
        self.assertEqual(found.action_draft.arguments.get("status"), "completed")

    # ── end-to-end (spell → executor → fake caller) ─────────────────────────

    async def test_create_spell_end_to_end(self):
        book = self._load([{
            "id": "add_item",
            "trigger": "Füge {item} auf die Einkaufsliste hinzu",
            "operation": "todo.add_item",
            "target": {"kind": "entity_id", "value": "todo.einkaufsliste"},
            "arguments": {"item": {"capture": "item", "type": "str"}},
        }])
        found = spellbook_engine.find_command(book, "Füge Joghurt auf die Einkaufsliste hinzu", "de")
        self.assertIsNotNone(found)

        entity_id = "todo.einkaufsliste"
        caller    = FakeTodoCaller()
        executor  = ActionExecutor(_resolver_for(entity_id), caller, expert_allowed=lambda: True)
        result    = await executor.execute(found.action_draft, _policy())

        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        domain, service, _, data = caller.service_calls[0]
        self.assertEqual((domain, service), ("todo", "add_item"))
        self.assertEqual(data["item"], "Joghurt")

    async def test_update_spell_end_to_end(self):
        book = self._load([{
            "id": "mark_done",
            "trigger": "Markiere {item} als erledigt",
            "operation": "todo.update_item",
            "target": {"kind": "entity_id", "value": "todo.einkaufsliste"},
            "arguments": {"item": {"capture": "item", "type": "str"}, "status": "completed"},
        }])
        found = spellbook_engine.find_command(book, "Markiere Milch als erledigt", "de")
        self.assertIsNotNone(found)

        entity_id = "todo.einkaufsliste"
        caller    = FakeTodoCaller()
        executor  = ActionExecutor(_resolver_for(entity_id), caller, expert_allowed=lambda: True)
        result    = await executor.execute(found.action_draft, _policy())

        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        domain, service, _, data = caller.service_calls[0]
        self.assertEqual((domain, service), ("todo", "update_item"))
        self.assertEqual(data["item"],   "Milch")
        self.assertEqual(data["status"], "completed")


# ══════════════════════════════════════════════════════════════════════════════
# 8. MULTI-BACKEND GENERALITY (native HA / Todoist / Bring!)
# ══════════════════════════════════════════════════════════════════════════════

class MultiBackendGeneralityTests(unittest.IsolatedAsyncioTestCase):
    """Demonstrates P8's key invariant: the same HASE todo operations work
    identically against all three backends.  No backend-specific code paths
    exist in HASE — only the entity_id in the spell target differs."""

    _BACKENDS = [
        "todo.einkaufsliste",   # native HA Todo
        "todo.inbox",           # Todoist
        "todo.einkaufen",       # Bring!
    ]

    async def _run_list(self, entity_id: str) -> "ExecutionResult":
        caller   = FakeTodoCaller(items=[{"uid": "1", "summary": "Item A", "status": "needs_action"}])
        executor = ActionExecutor(_resolver_for(entity_id), caller, expert_allowed=lambda: True)
        draft    = ActionDraft("todo.list_items", ActionTarget("entity_id", entity_id), {})
        return await executor.execute(draft, _policy())

    async def _run_add(self, entity_id: str):
        caller   = FakeTodoCaller()
        executor = ActionExecutor(_resolver_for(entity_id), caller, expert_allowed=lambda: True)
        draft    = ActionDraft("todo.add_item", ActionTarget("entity_id", entity_id), {"item": "Testitem"})
        return await executor.execute(draft, _policy()), caller

    async def _run_complete(self, entity_id: str):
        caller   = FakeTodoCaller()
        executor = ActionExecutor(_resolver_for(entity_id), caller, expert_allowed=lambda: True)
        draft    = ActionDraft(
            "todo.update_item",
            ActionTarget("entity_id", entity_id),
            {"item": "Testitem", "status": "completed"},
        )
        return await executor.execute(draft, _policy()), caller

    async def test_list_items_returns_query_answer_on_all_backends(self):
        for entity_id in self._BACKENDS:
            with self.subTest(entity_id=entity_id):
                result = await self._run_list(entity_id)
                self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER, entity_id)
                self.assertIn("Item A", result.data["items"])

    async def test_add_item_succeeds_on_all_backends(self):
        for entity_id in self._BACKENDS:
            with self.subTest(entity_id=entity_id):
                result, caller = await self._run_add(entity_id)
                self.assertEqual(result.status, ExecutionStatus.SUCCESS, entity_id)
                domain, service, eids, data = caller.service_calls[0]
                self.assertEqual((domain, service), ("todo", "add_item"))
                self.assertEqual(data["item"], "Testitem")

    async def test_complete_item_succeeds_on_all_backends(self):
        for entity_id in self._BACKENDS:
            with self.subTest(entity_id=entity_id):
                result, caller = await self._run_complete(entity_id)
                self.assertEqual(result.status, ExecutionStatus.SUCCESS, entity_id)
                domain, service, eids, data = caller.service_calls[0]
                self.assertEqual((domain, service), ("todo", "update_item"))
                self.assertEqual(data["status"], "completed")


# ══════════════════════════════════════════════════════════════════════════════
# 9. STR-CAPTURE CONTRACT (generic; no todo-specific behavior)
# ══════════════════════════════════════════════════════════════════════════════

class StrCaptureContractTests(unittest.TestCase):
    """Regression suite for the 'type: str' capture extension in
    spellbook_engine._valid_arguments_spec / _resolve_arguments.

    Key invariants:
      (a) The extension is purely opt-in: an UNTYPED bare {"capture": "x"}
          that has neither true_values/false_values nor type/value_map is
          STILL rejected by the loader — semantics unchanged.
      (b) An EXPLICITLY typed {"capture": "x", "type": "str"} spell loads
          and resolves the captured text verbatim (no case folding, no trim
          beyond strip(), no numeric conversion).
      (c) The new type is GENERIC: it works for ANY operation/argument key,
          not only todo operations.
      (d) Whitespace-only capture resolves to None (unchanged behavior:
          captured_raw.strip() == "" → None).
    """

    def _load_one(self, command: dict):
        book_dict = {"id": "contract_test", "name": "Contract Test", "commands": [command]}
        return spellbook_engine.load_spellbook_from_dict("contract_test", book_dict)

    # (a) Existing rejection semantics are unchanged ──────────────────────────

    def test_bare_capture_without_type_is_still_rejected(self):
        """An untyped argument capture that lacks true_values/false_values
        must still be rejected — this verifies we did NOT loosen that gate."""
        book = self._load_one({
            "id": "bad_spell",
            "trigger": "do {thing}",
            "operation": "light.turn_on",
            "target": {"kind": "entity_id", "value": "light.test"},
            "arguments": {"some_arg": {"capture": "thing"}},  # bare — no type
        })
        self.assertIsNone(book, "Bare untyped capture must still be rejected at load time")

    def test_bare_capture_with_false_values_still_loads(self):
        """A capture with false_values (boolean mode) still works unchanged."""
        book = self._load_one({
            "id": "bool_spell",
            "trigger": "{val} ein",
            "operation": "switch.turn_on",
            "target": {"kind": "entity_id", "value": "switch.test"},
            "arguments": {"dummy": {
                "capture": "val",
                "true_values": ["an", "ein"],
                "false_values": ["aus"],
            }},
        })
        self.assertIsNotNone(book, "Boolean capture with true/false_values must still load")

    # (b) Opt-in str type loads and resolves verbatim ─────────────────────────

    def test_str_capture_loads(self):
        """A capture with type='str' must load successfully."""
        book = self._load_one({
            "id": "str_spell",
            "trigger": "set label {label}",
            "operation": "input_text.set_value",
            "target": {"kind": "entity_id", "value": "input_text.test"},
            "arguments": {"value": {"capture": "label", "type": "str"}},
        })
        self.assertIsNotNone(book, "str-typed capture must load")

    def test_str_capture_resolves_verbatim(self):
        """Captured text is passed through unchanged (case, punctuation, etc.)."""
        book = self._load_one({
            "id": "str_spell",
            "trigger": "set label {label}",
            "operation": "input_text.set_value",
            "target": {"kind": "entity_id", "value": "input_text.test"},
            "arguments": {"value": {"capture": "label", "type": "str"}},
        })
        self.assertIsNotNone(book)
        found = spellbook_engine.find_command(book, "set label Hallo Welt", "en")
        self.assertIsNotNone(found)
        self.assertEqual(found.action_draft.arguments["value"], "Hallo Welt")

    def test_str_capture_preserves_mixed_case(self):
        """str capture must NOT lower-case or normalise the captured text."""
        book = self._load_one({
            "id": "case_spell",
            "trigger": "add {item}",
            "operation": "todo.add_item",
            "target": {"kind": "entity_id", "value": "todo.test"},
            "arguments": {"item": {"capture": "item", "type": "str"}},
        })
        self.assertIsNotNone(book)
        found = spellbook_engine.find_command(book, "add MixedCase Item", "en")
        self.assertIsNotNone(found)
        self.assertEqual(found.action_draft.arguments["item"], "MixedCase Item")

    # (c) Generic — works with non-todo operations ────────────────────────────

    def test_str_capture_works_on_non_todo_operation(self):
        """Confirm the str type is not todo-specific: works with any operation."""
        book = self._load_one({
            "id": "generic_spell",
            "trigger": "set option {opt}",
            "operation": "select.select_option",
            "target": {"kind": "entity_id", "value": "select.test"},
            "arguments": {"option": {"capture": "opt", "type": "str"}},
        })
        self.assertIsNotNone(book, "str capture must work for select.select_option too")
        found = spellbook_engine.find_command(book, "set option auto", "en")
        self.assertIsNotNone(found)
        self.assertEqual(found.action_draft.arguments["option"], "auto")

    # (d) Whitespace-only capture → None (no change to existing behavior) ─────

    def test_str_capture_empty_after_strip_yields_none(self):
        """A whitespace-only capture was captured_raw.strip() == '' before
        and must still resolve to None (caller gets None → FAILED)."""
        book = self._load_one({
            "id": "ws_spell",
            "trigger": "mark {item} done",
            "operation": "todo.update_item",
            "target": {"kind": "entity_id", "value": "todo.test"},
            "arguments": {"item": {"capture": "item", "type": "str"}, "status": "completed"},
        })
        # We can't inject whitespace via find_command (the engine strips
        # the input), so test _resolve_arguments directly via private module
        # attribute (same pattern used in test_core_engine.py for internals).
        resolve = spellbook_engine._resolve_arguments
        result = resolve({"item": {"capture": "item", "type": "str"}}, {"item": "  "})
        self.assertIsNone(result, "Whitespace-only capture must resolve to None")


# ══════════════════════════════════════════════════════════════════════════════
# 10. ITEM-RESOLUTION CONTRACT (name → HA UID → update/complete)
# ══════════════════════════════════════════════════════════════════════════════

class ItemResolutionContractTests(unittest.IsolatedAsyncioTestCase):
    """Verifies HASE's delegation contract for item resolution.

    Contract summary
    ────────────────
    HASE passes the captured item name verbatim to HA's `todo.update_item`
    (and `todo.remove_item`) via the `item` field.  HA performs the
    name-to-UID lookup internally and is responsible for:
      • raising ServiceValidationError if the name matches 0 items  (not found)
      • raising ServiceValidationError if the name matches 2+ items (ambiguous)

    HASE's role: propagate HA's rejection as ExecutionStatus.FAILED.
    HASE must NOT silently pick the first match — it never sees the UID.

    This means the "name → UID" contract lives entirely in HA.  HASE is
    correct as long as it passes the exact spoken name to HA and surfaces
    HA failures as FAILED — which these tests verify.
    """

    async def test_update_passes_exact_captured_name_to_ha(self):
        """The item field sent to HA must equal the captured string exactly."""
        caller   = FakeTodoCaller()
        executor = ActionExecutor(_resolver_for("todo.einkaufsliste"), caller, expert_allowed=lambda: True)
        draft    = ActionDraft(
            "todo.update_item",
            ActionTarget("entity_id", "todo.einkaufsliste"),
            {"item": "Milch (Bio)", "status": "completed"},
        )
        result = await executor.execute(draft, _policy())
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        _, _, _, data = caller.service_calls[0]
        self.assertEqual(data["item"], "Milch (Bio)",
            "HASE must pass the exact captured name to HA, not modify or look up by UID")

    async def test_ha_rejection_surfaces_as_failed(self):
        """When HA's service call returns False (rejected, e.g. ambiguous name
        or not-found), HASE must return FAILED — never SUCCESS."""
        caller   = FakeTodoCaller(accept=False)   # simulate HA rejection
        executor = ActionExecutor(_resolver_for("todo.einkaufsliste"), caller, expert_allowed=lambda: True)
        draft    = ActionDraft(
            "todo.update_item",
            ActionTarget("entity_id", "todo.einkaufsliste"),
            {"item": "Duplicated Item", "status": "completed"},
        )
        result = await executor.execute(draft, _policy())
        self.assertEqual(result.status, ExecutionStatus.FAILED,
            "HA rejection must propagate as FAILED, not silently succeed")

    async def test_remove_passes_exact_captured_name_to_ha(self):
        """Same contract for todo.remove_item — no UID lookup in HASE."""
        caller   = FakeTodoCaller()
        executor = ActionExecutor(_resolver_for("todo.einkaufsliste"), caller, expert_allowed=lambda: True)
        draft    = ActionDraft(
            "todo.remove_item",
            ActionTarget("entity_id", "todo.einkaufsliste"),
            {"item": "Käse"},
        )
        result = await executor.execute(draft, _policy())
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        _, _, _, data = caller.service_calls[0]
        self.assertEqual(data["item"], "Käse")

    async def test_add_does_not_require_name_lookup(self):
        """todo.add_item never needs a pre-existing item — name goes straight
        to HA as the new item summary.  No UID involved at all."""
        caller   = FakeTodoCaller()
        executor = ActionExecutor(_resolver_for("todo.einkaufsliste"), caller, expert_allowed=lambda: True)
        draft    = ActionDraft(
            "todo.add_item",
            ActionTarget("entity_id", "todo.einkaufsliste"),
            {"item": "Neue Sache"},
        )
        result = await executor.execute(draft, _policy())
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        _, _, _, data = caller.service_calls[0]
        self.assertEqual(data["item"], "Neue Sache")


# ══════════════════════════════════════════════════════════════════════════════
# 11. COMBINED SPELL STEP – {alias.items} / {alias.count} RESOLVER
# ══════════════════════════════════════════════════════════════════════════════

step_variables_mod = importlib.import_module("hase_spellbook_core.step_variables")
action_step_fields = step_variables_mod.action_step_fields
models_mod = importlib.import_module("hase_spellbook_core.models")
ActionStep = models_mod.ActionStep
StepPlan   = models_mod.StepPlan


class TodoListItemsStepFieldsTests(unittest.TestCase):
    """action_step_fields() must expose items/count/entity_id/success for
    todo.list_items regardless of which target kind the step declares."""

    def test_items_and_count_in_fields_for_entity_id_target(self):
        fields = action_step_fields("todo.list_items", "entity_id")
        self.assertIn("items",     fields)
        self.assertIn("count",     fields)
        self.assertIn("entity_id", fields)
        self.assertIn("success",   fields)

    def test_items_and_count_in_fields_for_entity_ids_target(self):
        fields = action_step_fields("todo.list_items", "entity_ids")
        self.assertIn("items", fields)
        self.assertIn("count", fields)

    def test_no_aggregate_batch_fields_for_list_items(self):
        """target_count/success_count make no sense for a single-entity query."""
        fields = action_step_fields("todo.list_items", "entity_ids")
        self.assertNotIn("target_count",   fields)
        self.assertNotIn("success_count",  fields)
        self.assertNotIn("failed_count",   fields)
        self.assertNotIn("failed_names",   fields)

    def test_action_ops_still_get_aggregate_fields_for_entity_ids_target(self):
        """Regression: action ops unaffected by the return_response branch."""
        fields = action_step_fields("todo.add_item", "entity_ids")
        self.assertIn("target_count",  fields)
        self.assertIn("success_count", fields)
        self.assertNotIn("items", fields)


class _EntityIdsResolver:
    """FakeResolver extended to handle entity_ids target kind (tuple of ids)."""
    def __init__(self, table):
        self._table = table
    async def resolve(self, target, *, domain_filter=None):
        if target.kind == "entity_ids":
            entities = []
            for eid in (target.value or ()):
                r = self._table.get(eid)
                if r is not None:
                    entities.extend(r.entities)
            return ResolvedTargets(tuple(entities), (), None) if entities else TargetNotFound()
        return self._table.get(target.value, TargetNotFound())
    async def resolve_area(self, target):
        return TargetNotFound()


_ENTITY_TABLE = {"todo.einkaufen": ResolvedTargets((ResolvedEntity("todo.einkaufen", "home"),))}


class TodoListItemsStepExecutionTests(unittest.IsolatedAsyncioTestCase):
    """execute_steps() must report status=ok and populate {alias.items} /
    {alias.count} when a Combined Spell step uses todo.list_items."""

    def _plan(self, response_template: str, target: "ActionTarget | None" = None) -> "StepPlan":
        return StepPlan(
            steps=(ActionStep(
                alias="inhalt",
                operation="todo.list_items",
                target=target or ActionTarget("entity_id", "todo.einkaufen"),
            ),),
            response_template=response_template,
        )

    async def _run(self, plan: "StepPlan", items: list[dict]) -> "ExecutionResult":
        caller   = FakeTodoCaller(items=items)
        executor = ActionExecutor(_EntityIdsResolver(_ENTITY_TABLE), caller, expert_allowed=lambda: True)
        return await executor.execute_steps(plan, _policy())

    async def test_step_status_is_ok(self):
        result = await self._run(self._plan("{inhalt.count}"), _ITEMS_FIXTURE)
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        step = result.data["steps"][0]
        self.assertEqual(step["status"], "ok")

    async def test_step_items_field_contains_summaries(self):
        result = await self._run(self._plan("{inhalt.items}"), _ITEMS_FIXTURE)
        step = result.data["steps"][0]
        items_val = step["values"].get("items", "")
        for item in _ITEMS_FIXTURE:
            self.assertIn(item["summary"], items_val)

    async def test_step_count_matches_fixture_length(self):
        result = await self._run(self._plan("{inhalt.count}"), _ITEMS_FIXTURE)
        step = result.data["steps"][0]
        self.assertEqual(step["values"].get("count"), len(_ITEMS_FIXTURE))

    async def test_response_template_alias_items_rendered(self):
        result = await self._run(
            self._plan("Auf der Liste: {inhalt.items}"),
            [{"uid": "1", "summary": "Milch", "status": "needs_action"}],
        )
        self.assertIn("Milch", result.data.get("response_text", ""))

    async def test_response_template_alias_count_rendered(self):
        result = await self._run(
            self._plan("Es stehen {inhalt.count} Einträge auf der Liste"),
            _ITEMS_FIXTURE,
        )
        self.assertIn(str(len(_ITEMS_FIXTURE)), result.data.get("response_text", ""))

    async def test_entity_ids_target_kind_also_works(self):
        """Regression: entity_ids target kind (tuple value) used to give target_count=0."""
        plan = self._plan("{inhalt.count}", ActionTarget("entity_ids", ("todo.einkaufen",)))
        result = await self._run(plan, _ITEMS_FIXTURE)
        step = result.data["steps"][0]
        self.assertEqual(step["status"], "ok")
        self.assertEqual(step["values"].get("count"), len(_ITEMS_FIXTURE))

    async def test_empty_list_step_still_ok(self):
        result = await self._run(self._plan("{inhalt.count}"), [])
        step = result.data["steps"][0]
        self.assertEqual(step["status"], "ok")
        self.assertEqual(step["values"].get("count"), 0)
        self.assertEqual(step["values"].get("items"), "")


if __name__ == "__main__":
    unittest.main()


# ══════════════════════════════════════════════════════════════════════════════
# GUIDED DEFAULT – "Todo-Liste lesen" asks only for open items
# ══════════════════════════════════════════════════════════════════════════════

class GuidedTodoOpenOnlyTests(unittest.IsolatedAsyncioTestCase):
    studio_core = importlib.import_module("hase_spellbook_core.studio")

    def _list_actions(self):
        return [a for a in self.studio_core.user_actions() if a["operation"] == "todo.list_items"]

    def test_guided_todo_list_sets_needs_action(self):
        first = self._list_actions()[0]
        self.assertEqual(first["label"], "Todo-Liste lesen")
        self.assertEqual(first["arguments"], {"status": "needs_action"})

    def test_unfiltered_preset_still_guided_so_existing_spells_stay_guided(self):
        self.assertIn({}, [a["arguments"] for a in self._list_actions()])
        self.assertIsNone(self.studio_core._catalog_match("todo.list_items", {}))
        self.assertIsNone(self.studio_core._catalog_match("todo.list_items", {"status": "needs_action"}))

    async def _run(self, arguments):
        caller = FakeTodoCaller(items=_ITEMS_FIXTURE)
        executor = ActionExecutor(_resolver_for("todo.inbox"), caller, expert_allowed=lambda: True)
        draft = ActionDraft("todo.list_items", ActionTarget("entity_id", "todo.inbox"), arguments)
        return await executor.execute(draft, _policy())

    async def test_guided_arguments_return_only_open_items(self):
        result = await self._run(self._list_actions()[0]["arguments"])
        self.assertEqual(result.data["names"], ["Milch", "Eier", "Käse"])

    async def test_raw_expert_without_status_stays_unfiltered(self):
        result = await self._run({})
        self.assertEqual(result.data["names"], ["Milch", "Eier", "Butter", "Käse"])

    async def test_explicit_status_is_not_overridden(self):
        result = await self._run({"status": "completed"})
        self.assertEqual(result.data["names"], ["Butter"])
