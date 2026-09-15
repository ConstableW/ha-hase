"""The `hase/studio` WebSocket command is the single choke point that decides
who may use Studio at all, and who may additionally touch access-control
configuration. GateTests exercises that choke point in isolation (with a
mocked SpellbookStudio/StudioAccessControl) to prove every ordinary operation
reaches dispatch() unfiltered for an authorized non-admin - the regression
test against hidden `is_admin` assumptions the task explicitly asks for.
FullFlowTests then drives the same real, unmocked entrypoint end-to-end as an
authorized non-admin through list/create_book/read/save_spell/validate/
dry_run/execute, reusing the temp-directory hass.config fixture StorageTests
already uses in test_studio.py.
"""
import ast
import asyncio
import importlib
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

import test_core_engine as core  # loads core/ as "hase_spellbook_core" once.
import test_studio as studio_test  # noqa: F401 - loads the real studio.py adapter as "hase_studio_test.studio".

ROOT = Path(__file__).resolve().parents[1]
studio_core = importlib.import_module("hase_spellbook_core.studio")
StudioError = studio_core.StudioError
adapter = importlib.import_module("hase_studio_test.studio")
core_access = importlib.import_module("hase_spellbook_core.access_control")
models = core._models_mod
executor_mod = core._executor_mod
N = types.SimpleNamespace

ORDINARY_OPERATIONS = [
    "delete_book", "reset_book", "manage_language", "ui_settings", "list", "areas", "entities", "cover",
    "read", "create_book", "save_metadata", "expert_permission", "validate_all", "import_book_preview",
    "import_book_apply", "validate", "dry_run", "execute", "export", "import_preview", "import_apply",
    "save_spell", "delete_spell", "duplicate_spell", "toggle_spell",
]


def load_studio_command():
    """AST-extract studio_command() (and the constant it references) with
    HA's websocket_api replaced by pass-through fakes, same technique
    test_studio_panel.py already uses for panel registration - avoids
    needing the real `homeassistant` package for a plain function test."""
    source = ast.parse((ROOT / "custom_components/hase/studio_api.py").read_text(encoding="utf-8"))
    wanted_names = {"studio_command", "_ACCESS_MANAGEMENT_OPERATIONS"}
    nodes = [n for n in source.body if getattr(n, "name", None) in wanted_names
             or any(isinstance(t, ast.Name) and t.id in wanted_names for t in getattr(n, "targets", []))]
    fake_websocket_api = SimpleNamespace(
        websocket_command=lambda schema: (lambda f: f),
        async_response=lambda f: f,
    )
    import logging

    import voluptuous as vol
    scope = {"websocket_api": fake_websocket_api, "StudioError": StudioError, "vol": vol, "_LOGGER": logging.getLogger("test_studio_access_api")}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "studio_api.py", "exec"), scope)  # noqa: S102 - compile only repository-owned functions.
    return scope["studio_command"]


def make_hass(*, access, studio=None):
    return N(data={"hase_studio_access": access, **({"hase_studio": studio} if studio is not None else {})})


def make_connection(*, user_id="u1", is_admin=False, group_ids=()):
    user = N(id=user_id, is_admin=is_admin, groups=[N(id=g) for g in group_ids])
    return N(user=user, send_error=Mock(), send_result=Mock(), context=Mock(return_value="ctx"))


class GateTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.studio_command = load_studio_command()

    async def test_denied_user_never_reaches_dispatch(self):
        access = N(is_authorized=Mock(return_value=False), snapshot=AsyncMock(), async_set=AsyncMock())
        studio = N(entries={"e1"}, dispatch=AsyncMock())
        hass = make_hass(access=access, studio=studio)
        connection = make_connection(is_admin=False)
        await self.studio_command(hass, connection, {"id": 1, "operation": "list", "data": {}})
        connection.send_error.assert_called_once_with(1, "unauthorized", "Kein Zugriff auf HASE Studio.")
        studio.dispatch.assert_not_called()
        connection.send_result.assert_not_called()

    async def test_authorized_non_admin_cannot_read_access_config(self):
        access = N(is_authorized=Mock(return_value=True), snapshot=AsyncMock(), async_set=AsyncMock())
        hass = make_hass(access=access, studio=N(entries={"e1"}, dispatch=AsyncMock()))
        connection = make_connection(is_admin=False)
        await self.studio_command(hass, connection, {"id": 2, "operation": "access_get", "data": {}})
        connection.send_error.assert_called_once_with(2, "unauthorized", "Nur Administratoren dürfen die Zugriffsverwaltung ändern.")
        access.snapshot.assert_not_called()

    async def test_authorized_non_admin_cannot_write_access_config(self):
        access = N(is_authorized=Mock(return_value=True), snapshot=AsyncMock(), async_set=AsyncMock())
        hass = make_hass(access=access, studio=N(entries={"e1"}, dispatch=AsyncMock()))
        connection = make_connection(is_admin=False)
        await self.studio_command(hass, connection, {"id": 3, "operation": "access_set", "data": {"mode": "all", "allowed_user_ids": [], "allowed_group_ids": []}})
        connection.send_error.assert_called_once_with(3, "unauthorized", "Nur Administratoren dürfen die Zugriffsverwaltung ändern.")
        access.async_set.assert_not_called()

    async def test_admin_can_read_and_write_access_config(self):
        access = N(is_authorized=Mock(return_value=True), snapshot=AsyncMock(return_value={"config": {"mode": "admin"}, "groups": [], "users": []}),
                    async_set=AsyncMock(return_value={"config": {"mode": "all"}, "groups": [], "users": []}))
        hass = make_hass(access=access, studio=N(entries={"e1"}, dispatch=AsyncMock()))
        connection = make_connection(is_admin=True)

        await self.studio_command(hass, connection, {"id": 4, "operation": "access_get", "data": {}})
        connection.send_result.assert_called_once_with(4, {"config": {"mode": "admin"}, "groups": [], "users": []})

        await self.studio_command(hass, connection, {"id": 5, "operation": "access_set", "data": {"mode": "all", "allowed_user_ids": ["a"], "allowed_group_ids": ["g"]}})
        access.async_set.assert_called_once_with("all", ["a"], ["g"])
        connection.send_result.assert_called_with(5, {"config": {"mode": "all"}, "groups": [], "users": []})

    async def test_authorized_non_admin_ordinary_operations_reach_dispatch_unfiltered(self):
        """No operation in the whole catalog is silently blocked for a
        non-admin - proves there is no hidden is_admin check anywhere on
        this path, not just that one sample operation returns 200."""
        access = N(is_authorized=Mock(return_value=True))
        for i, operation in enumerate(ORDINARY_OPERATIONS):
            with self.subTest(operation=operation):
                studio = N(entries={"e1"}, dispatch=AsyncMock(return_value={"ok": operation}))
                hass = make_hass(access=access, studio=studio)
                connection = make_connection(is_admin=False)
                await self.studio_command(hass, connection, {"id": i, "operation": operation, "data": {"x": 1}})
                studio.dispatch.assert_called_once_with(operation, {"x": 1}, "ctx")
                connection.send_result.assert_called_once_with(i, {"ok": operation})
                connection.send_error.assert_not_called()

    async def test_studio_error_and_generic_error_handling_are_unaffected(self):
        access = N(is_authorized=Mock(return_value=True))
        studio = N(entries={"e1"}, dispatch=AsyncMock(side_effect=StudioError("bad input")))
        hass = make_hass(access=access, studio=studio)
        connection = make_connection(is_admin=True)
        await self.studio_command(hass, connection, {"id": 6, "operation": "list", "data": {}})
        connection.send_error.assert_called_once_with(6, "studio_validation", "bad input")

        studio.dispatch = AsyncMock(side_effect=RuntimeError("boom"))
        connection = make_connection(is_admin=True)
        await self.studio_command(hass, connection, {"id": 7, "operation": "list", "data": {}})
        connection.send_error.assert_called_once_with(7, "studio_error", "Die Anfrage konnte nicht abgeschlossen werden. Bitte Eingaben prüfen und erneut laden.")

    async def test_integration_not_loaded_is_a_studio_error_not_a_crash(self):
        access = N(is_authorized=Mock(return_value=True))
        studio = N(entries=set(), dispatch=AsyncMock())
        hass = make_hass(access=access, studio=studio)
        connection = make_connection(is_admin=True)
        await self.studio_command(hass, connection, {"id": 8, "operation": "list", "data": {}})
        connection.send_error.assert_called_once_with(8, "studio_validation", "Die HASE-Integration ist nicht geladen.")
        studio.dispatch.assert_not_called()


class FullFlowTests(IsolatedAsyncioTestCase):
    """Drives the real websocket entrypoint end-to-end as an authorized
    non-admin, proving Studio is fully functional for them - not merely
    reachable."""

    async def asyncSetUp(self):
        self.studio_command = load_studio_command()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        hass = N(data={"hase_studio_permissions": {}, "hase_studio_sidebar": False},
                  config=N(path=lambda part: str(Path(self.directory.name) / part)))

        async def job(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = job
        studio = adapter.SpellbookStudio.__new__(adapter.SpellbookStudio)
        studio.hass = hass
        studio.lock = asyncio.Lock()
        studio.last_reports = {}
        studio.permissions = N(async_save=AsyncMock())
        studio.profiles = lambda book_id: []
        studio.studio_profiles = list
        studio.areas = list  # report() needs this; no real HA area registry in this fixture.
        studio.entries = {"entry-1"}
        source = Path(self.directory.name) / "bundled"
        source.mkdir()
        self.patch = patch("hase_studio_test.spellbook_loader.BUILTIN_BOOK_DIR", source)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        hass.data["hase_studio"] = studio

        access_adapter = importlib.import_module("hase_studio_test.studio_access")
        access = access_adapter.StudioAccessControl.__new__(access_adapter.StudioAccessControl)
        access.hass = hass
        access.config = core_access.AccessConfig(
            mode=core_access.ACCESS_MODE_CUSTOM,
            allowed_user_ids=frozenset(),
            allowed_group_ids=frozenset({"g-family"}),
        )
        hass.data["hase_studio_access"] = access

        self.hass = hass
        self.studio = studio
        self.connection = make_connection(user_id="nonadmin-1", is_admin=False, group_ids=["g-family"])
        self.next_id = 0

    async def call(self, operation, data):
        self.next_id += 1
        self.connection.send_result.reset_mock()
        self.connection.send_error.reset_mock()
        await self.studio_command(self.hass, self.connection, {"id": self.next_id, "operation": operation, "data": data})
        if self.connection.send_error.called:
            raise AssertionError(f"{operation} failed: {self.connection.send_error.call_args}")
        return self.connection.send_result.call_args.args[1]

    async def test_authorized_non_admin_never_denied(self):
        await self.call("list", {})
        self.connection.send_error.assert_not_called()

    async def test_full_non_admin_studio_flow(self):
        # An authorized non-admin gets the complete Studio, not a reduced
        # subset - browse, create, edit, validate, dry-run and execute.
        listing = await self.call("list", {})
        self.assertEqual(listing["books"], [])

        created = await self.call("create_book", {"book_id": "familybook", "name": "Family Book", "language": "en"})
        revision = created["revision"]

        opened = await self.call("read", {"book_id": "familybook"})
        self.assertEqual(opened["book"]["commands"], [])

        # A trigger with an unfillable placeholder skips report()'s own
        # self-test (see studio.py's report(): "if '{' in phrase: continue"),
        # so save_spell/read exercise real persistence without needing a
        # full Home Assistant area/entity registry in this lightweight test.
        spell_payload = {
            "triggers": ["hallo {gadget}"], "action": "light-on", "target_kind": "all_allowed",
        }
        info = await self.call("list", {})
        light_action = next((a for a in info["actions"] if a["operation"] == "light.turn_on" and "all_allowed" in a["targets"]), None)
        self.assertIsNotNone(light_action, "fixture expects a light action supporting an all_allowed target")
        spell_payload["action"] = light_action["id"]

        saved = await self.call("save_spell", {"book_id": "familybook", "revision": revision, "mode": "user", "spell": spell_payload})
        revision = saved["revision"]
        opened = await self.call("read", {"book_id": "familybook"})
        self.assertEqual(len(opened["book"]["commands"]), 1)
        spell_id = opened["book"]["commands"][0]["id"]

        # validate/dry_run/execute drive real target resolution - fake only
        # the innermost device-call layer, same technique the existing
        # test_studio.py suite already uses (test_placeholder_test_phrases_
        # stop_before_matching_and_fallback), so the WebSocket/dispatch path
        # above it stays entirely real and unmocked.
        draft = models.ActionDraft("light.turn_on", models.ActionTarget("all_allowed", None), {})
        found = N(step_plan=None, action_draft=draft, command=N(spell_id=spell_id), captures={})
        dry_run_result = models.ExecutionResult(status=models.ExecutionStatus.SUCCESS, reason_code="dry_run", data={})
        execute_result = models.ExecutionResult(status=models.ExecutionStatus.SUCCESS, reason_code="ok", data={})

        with patch.object(adapter, "find_command", return_value=found), \
             patch.object(adapter, "ActionExecutor") as runner_cls:
            runner_cls.return_value.execute = AsyncMock(side_effect=[dry_run_result, execute_result])

            validated = await self.call("validate", {"book_id": "familybook", "revision": revision})
            # The dynamic {gadget} placeholder itself always earns one
            # expected "test with a concrete phrase" warning - the point
            # here is that validate/report() ran to completion at all.
            self.assertEqual(validated["warnings"], 1)

            dry_run = await self.call("dry_run", {"book_id": "familybook", "revision": revision, "phrase": "hallo widget"})
            self.assertEqual(dry_run["status"], "SUCCESS")

            executed = await self.call("execute", {"book_id": "familybook", "revision": revision, "phrase": "hallo widget", "confirmed": True})
            self.assertEqual(executed["status"], "SUCCESS")
            self.assertEqual(runner_cls.return_value.execute.await_count, 2)


if __name__ == "__main__":
    import unittest
    unittest.main()
