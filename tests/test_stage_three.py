"""Failure-path regression tests for audit stage three, with external adapters."""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_harness_support as support

N = support.N
api = support.api
coord = support.coordinator_module
book = support.engine
loader = support.loader


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    setUp = support.DeliveryTests.setUp

    def full_client(self):
        for method in ["get_system", "get_music_page", "get_music_source", "get_music_playback",
                       "get_music_audio_route", "get_bluetooth_state", "get_light_master_switch",
                       "get_applied_light_project", "get_current_slot", "get_music_storage"]:
            setattr(self.client, method, AsyncMock(return_value={}))
        for method in ["list_light_projects", "list_alarms", "list_memos",
                       "list_character_settings", "get_chat_history_count"]:
            setattr(self.client, method, AsyncMock(return_value=[]))

    def music_inventory(self, version=5, name="song.mp3"):
        """The live TF-card payload shape: absolute paths plus root_path."""
        return {
            "is_loaded": True,
            "root_path": "/storage/142E2DF92E2DD4900",
            "capacity_total_bytes": 127848673280,
            "version": version,
            "music_files": [
                {
                    "name": name,
                    "path": f"/storage/142E2DF92E2DD4900/code27/music/{name}",
                    "duration_ms": 1000,
                }
            ],
        }

    # -- TF-card inventory lifecycle (real-device regression) ----------------

    async def test_rest_storage_populates_the_inventory_before_any_push(self):
        """After a restart the inventory must not depend on a push arriving."""
        self.full_client()
        self.client.get_music_storage.return_value = self.music_inventory(version=3)
        snapshot = await self.c._async_update_data()
        self.assertEqual(snapshot.music_storage["version"], 3)
        self.assertEqual(len(snapshot.music_storage["music_files"]), 1)

    async def test_poll_without_a_storage_answer_keeps_the_last_inventory(self):
        self.full_client()
        self.c._handle_ws_music_storage(self.music_inventory(version=5))
        self.client.get_music_storage.return_value = {}
        snapshot = await self.c._async_update_data()
        self.assertEqual(snapshot.music_storage["version"], 5)
        self.assertEqual(len(snapshot.music_storage["music_files"]), 1)

    async def test_poll_with_an_unusable_storage_answer_keeps_the_inventory(self):
        self.full_client()
        self.c._handle_ws_music_storage(self.music_inventory(version=5))
        self.client.get_music_storage.return_value = {"is_loaded": True}
        snapshot = await self.c._async_update_data()
        self.assertEqual(snapshot.music_storage["version"], 5)

    async def test_storage_push_during_full_poll_survives_the_late_http_read(self):
        self.full_client()
        entered, release = asyncio.Event(), asyncio.Event()

        async def storage():
            entered.set()
            await release.wait()
            return {}  # a poll answer without an inventory, racing the push

        self.client.get_music_storage.side_effect = storage
        task = asyncio.create_task(self.c._async_update_data())
        await entered.wait()
        self.c._handle_ws_music_storage(self.music_inventory(version=9))
        release.set()
        snapshot = await task
        self.assertEqual(snapshot.music_storage["version"], 9)

    async def test_older_rest_inventory_does_not_replace_a_newer_push(self):
        self.full_client()
        self.c._handle_ws_music_storage(self.music_inventory(version=10))
        self.client.get_music_storage.return_value = self.music_inventory(
            version=4, name="older.mp3"
        )
        snapshot = await self.c._async_update_data()
        self.assertEqual(snapshot.music_storage["version"], 10)
        self.assertEqual(
            snapshot.music_storage["music_files"][0]["name"], "song.mp3"
        )

    async def test_newer_rest_inventory_replaces_the_previous_one(self):
        self.full_client()
        self.c._handle_ws_music_storage(self.music_inventory(version=4))
        self.client.get_music_storage.return_value = self.music_inventory(
            version=11, name="new.mp3"
        )
        snapshot = await self.c._async_update_data()
        self.assertEqual(snapshot.music_storage["version"], 11)
        self.assertEqual(
            snapshot.music_storage["music_files"][0]["name"], "new.mp3"
        )

    async def test_storage_read_failure_does_not_fail_the_poll(self):
        self.full_client()
        self.client.get_music_storage.side_effect = api.Code27DeviceError(
            "UNSUPPORTED", "no storage endpoint"
        )
        snapshot = await self.c._async_update_data()
        self.assertEqual(snapshot.music_storage, {})
        self.assertEqual(snapshot.system, {})

    async def test_a_push_without_an_inventory_never_empties_the_inventory(self):
        self.full_client()
        self.c._handle_ws_music_storage(self.music_inventory(version=5))
        self.c._handle_ws_music_storage({})
        self.c._handle_ws_music_storage({"is_loaded": True})
        self.c._handle_ws_music_storage(None)
        self.assertEqual(len(self.c.data.music_storage["music_files"]), 1)

    async def test_an_explicitly_empty_loaded_card_is_still_accepted(self):
        self.full_client()
        self.c._handle_ws_music_storage(self.music_inventory(version=5))
        self.c._handle_ws_music_storage(
            {"is_loaded": True, "music_files": [], "version": 6}
        )
        self.assertEqual(self.c.data.music_storage["music_files"], [])
        self.assertEqual(self.c.data.music_storage["version"], 6)

    async def test_playback_push_never_touches_the_storage_inventory(self):
        self.full_client()
        self.c._handle_ws_music_storage(self.music_inventory(version=5))
        for i in range(10):
            self.c._handle_ws_music_playback({"version": i, "status": "PLAYING"})
        self.assertEqual(len(self.c.data.music_storage["music_files"]), 1)
        self.assertEqual(self.c.data.music_playback["version"], 9)

    async def test_push_does_not_reset_poll_or_mark_other_resources_healthy(self):
        self.c.last_update_success = False
        self.c.async_set_updated_data = Mock(side_effect=AssertionError("reschedules full poll"))
        for i in range(20):
            self.c._handle_ws_music_playback({"version": i, "status": "PLAYING"})
        self.assertFalse(self.c.last_update_success)
        self.c.async_set_updated_data.assert_not_called()
        self.assertEqual(self.c.data.music_playback["version"], 19)

    async def test_stale_and_duplicate_push_rejected(self):
        self.c.async_update_listeners = Mock()
        self.c._handle_ws_music_page({"version": 9})
        self.c._handle_ws_music_page({"version": 8})
        self.c._handle_ws_music_page({"version": 9})
        self.c._handle_ws_music_page(None)
        self.c.async_update_listeners.assert_called_once()

    async def test_push_during_full_poll_survives_late_http(self):
        self.full_client()
        entered, release = asyncio.Event(), asyncio.Event()

        async def playback():
            entered.set()
            await release.wait()
            return {"status": "OLD"}  # no versions: use in-flight generation

        self.client.get_music_playback.side_effect = playback
        task = asyncio.create_task(self.c._async_update_data())
        await entered.wait()
        self.c._handle_ws_music_playback({"status": "NEW"})
        release.set()
        snapshot = await task
        self.assertEqual(snapshot.music_playback["status"], "NEW")

    def test_build_character_overview_merges_slots_with_last_message(self):
        character_slots = [
            {"slot_number": 1, "name": "Lilya"},
            {"slot_number": 2, "name": "Mara"},
        ]
        history_counts = [
            {"slot": 1, "messages": [{"content": "old"}, {"content": "hi", "role": "assistant", "created_at": "t2"}]},
        ]
        current_slot = {"slot_number": 2}
        overview = coord.Code27Coordinator._build_character_overview(
            character_slots, history_counts, current_slot
        )
        self.assertEqual(overview[0], {
            "slot_number": 1, "name": "Lilya", "active": False,
            "last_message": "hi", "last_role": "assistant", "last_created_at": "t2",
        })
        self.assertEqual(overview[1], {
            "slot_number": 2, "name": "Mara", "active": True,
            "last_message": None, "last_role": None, "last_created_at": None,
        })

    async def test_older_http_does_not_replace_preexisting_push(self):
        self.full_client()
        self.c._handle_ws_music_source({"version": 10})
        self.client.get_music_source.return_value = {"version": 9}
        snapshot = await self.c._async_update_data()
        self.assertEqual(snapshot.music_source["version"], 10)

    async def test_notification_restore_failure_is_retryable(self):
        self.c.notification_active = True
        self.c.notification_message = "remember"
        self.c._pre_notification_light_enabled = True
        self.client.preview_light_color = AsyncMock(side_effect=[api.Code27ConnectionError("offline"), None])
        with self.assertRaises(api.Code27ConnectionError):
            await self.c.async_acknowledge_notification()
        self.assertTrue(self.c.notification_active)
        self.assertEqual(self.c.notification_message, "remember")
        await self.c.async_acknowledge_notification()
        self.assertFalse(self.c.notification_active)
        self.assertEqual(self.client.preview_light_color.await_count, 2)

    async def test_failure_after_preview_stop_retries_master_switch(self):
        self.c.notification_active = True
        self.c._pre_notification_light_enabled = False
        self.client.stop_light_preview = AsyncMock()
        self.client.set_light_master_switch = AsyncMock(side_effect=[api.Code27ConnectionError("offline"), None])
        with self.assertRaises(api.Code27ConnectionError):
            await self.c.async_acknowledge_notification()
        await self.c.async_acknowledge_notification()
        self.assertEqual(self.client.set_light_master_switch.await_count, 2)
        self.assertFalse(self.c.notification_active)

    async def test_failed_notify_can_be_cleaned_up(self):
        self.client.set_light_master_switch = AsyncMock()
        self.client.preview_light_color = AsyncMock(side_effect=api.Code27ConnectionError("timeout"))
        self.client.stop_light_preview = AsyncMock()
        with self.assertRaises(api.Code27ConnectionError):
            await self.c.async_trigger_notification("message", (1, 2, 3))
        self.assertFalse(self.c.notification_active)
        await self.c.async_acknowledge_notification()
        self.client.stop_light_preview.assert_awaited_once()
        self.assertEqual(self.client.set_light_master_switch.call_args.args, (False,))

    async def test_parallel_acknowledgements_only_restore_once(self):
        self.c.notification_active = True
        self.c._pre_notification_light_enabled = False
        self.client.stop_light_preview = AsyncMock()
        self.client.set_light_master_switch = AsyncMock()
        await asyncio.gather(self.c.async_acknowledge_notification(), self.c.async_acknowledge_notification())
        self.client.stop_light_preview.assert_awaited_once()

    async def test_remote_recovery_disabled_by_default_does_not_call_client(self):
        self.full_client()
        self.client.get_system.side_effect = api.Code27ConnectionError("offline")
        self.client.attempt_remote_recovery = AsyncMock()
        self.entry.data = {"host": "old-host", "port": 8081}

        class UpdateFailed(Exception):
            pass

        with patch.object(coord, "UpdateFailed", UpdateFailed, create=True), \
             self.assertRaises(UpdateFailed):
            await self.c._async_update_data()
        self.client.attempt_remote_recovery.assert_not_awaited()

    async def test_remote_recovery_enabled_and_succeeds_retries_poll_and_persists_host(self):
        self.full_client()
        self.client.get_system.side_effect = [
            api.Code27ConnectionError("offline"), {"machine_id": "m1"},
        ]
        self.client.attempt_remote_recovery = AsyncMock(return_value="10.0.0.9")
        self.entry.data = {"host": "old-host", "port": 8081}
        self.entry.options = {**self.entry.options, "remote_recovery_enabled": True}
        self.hass.config_entries = N(async_update_entry=Mock())

        snapshot = await self.c._async_update_data()

        self.client.attempt_remote_recovery.assert_awaited_once()
        self.hass.config_entries.async_update_entry.assert_called_once()
        call = self.hass.config_entries.async_update_entry.call_args
        self.assertEqual(call.args[0], self.entry)
        self.assertEqual(call.kwargs["data"]["host"], "10.0.0.9")
        self.assertEqual(snapshot.system, {"machine_id": "m1"})
        self.assertEqual(self.client.get_system.await_count, 2)

    async def test_remote_recovery_enabled_but_fails_still_raises_update_failed(self):
        self.full_client()
        self.client.get_system.side_effect = api.Code27ConnectionError("offline")
        self.client.attempt_remote_recovery = AsyncMock(return_value=None)
        self.entry.data = {"host": "old-host", "port": 8081}
        self.entry.options = {**self.entry.options, "remote_recovery_enabled": True}
        self.hass.config_entries = N(async_update_entry=Mock())

        class UpdateFailed(Exception):
            pass

        with patch.object(coord, "UpdateFailed", UpdateFailed, create=True), \
             self.assertRaises(UpdateFailed):
            await self.c._async_update_data()
        self.client.attempt_remote_recovery.assert_awaited_once()
        self.hass.config_entries.async_update_entry.assert_not_called()

    async def test_setup_auth_error_reaches_home_assistant(self):
        self.full_client()
        class AuthFailed(Exception):
            pass
        class UpdateFailed(Exception):
            pass
        self.client.get_system.side_effect = api.Code27AuthError("UNAUTHORIZED")
        self.c.async_config_entry_first_refresh = self.c._async_update_data
        init = support.load_source("__init__", **support.constants, vol=VOL,
            CONF_HOST="host", CONF_PORT="port", Platform=N(**{v.upper(): v for v in ["binary_sensor", "button", "image", "light", "media_player", "number", "select", "sensor", "switch", "todo", "conversation"]}),
            cv=N(ensure_list=list, config_entry_only_config_schema=lambda domain: object()), async_get_clientsession=lambda h: None,
            Code27Client=lambda *args: self.client, Code27Coordinator=lambda *args: self.c)
        self.entry.data = {"host": "test", "port": 8081, "user_id": "u", "local_secret": "s"}
        with patch.object(coord, "ConfigEntryAuthFailed", AuthFailed, create=True), \
             patch.object(coord, "UpdateFailed", UpdateFailed, create=True), \
             self.assertRaises(AuthFailed):
            await init.async_setup_entry(self.hass, self.entry)
        self.client.get_system.assert_awaited_once()


VOL = N(Schema=lambda value: value, Required=lambda key, **kw: key,
        Optional=lambda key, **kw: key, All=lambda *args: args,
        Range=lambda **kw: kw, In=lambda value: value, Length=lambda **kw: kw)


class FlowBase:
    def __init_subclass__(cls, **kwargs):
        pass
    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}
    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}
    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}
    def async_update_reload_and_abort(self, entry, *, data_updates):
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, **data_updates}
        )
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_abort(reason="reauth_successful")
    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id
    def _abort_if_unique_id_configured(self):
        pass


flow_module = support.load_source("config_flow", **support.constants,
    vol=VOL, ConfigFlow=FlowBase, OptionsFlow=FlowBase,
    # This file's own FlowTests only exercise device pairing
    # (async_step_pair/verify/reauth) - SatelliteSubentryFlow is never
    # instantiated here, so these just need to be valid base
    # classes/types for the module to exec without a NameError.
    ConfigSubentryFlow=FlowBase, ConfigSubentry=dict, SubentryFlowResult=dict,
    callback=lambda f: f,
    CONF_HOST="host", CONF_PORT="port", Code27Error=api.Code27Error,
    Code27ConnectionError=api.Code27ConnectionError)


class FlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.flow = flow_module.HaseConfigFlow()
        self.flow.hass = N(
            config_entries=N(
                async_entries=lambda domain: [],
                async_update_entry=Mock(),
                async_reload=AsyncMock(),
                async_schedule_reload=Mock(),
            )
        )
        self.flow._client = N(pair_confirm=AsyncMock(return_value=("user", "secret")),
                              get_system=AsyncMock(return_value={"machine_id": "device-a"}))

    async def test_identity_read_retry_does_not_pair_twice(self):
        self.flow._client.get_system.side_effect = [api.Code27ConnectionError("offline"), {"machine_id": "device-a"}]
        result = await self.flow.async_step_pair({"pairing_code": "123456"})
        self.assertEqual(result["step_id"], "verify")
        self.assertEqual(result["errors"]["base"], "cannot_connect")
        result = await self.flow.async_step_verify({})
        self.assertEqual(result["type"], "create_entry")
        self.flow._client.pair_confirm.assert_awaited_once()

    async def test_wrong_device_reauth_does_not_change_existing_entry(self):
        self.flow._reauth_entry = N(data={"machine_id": "other"}, unique_id="other")
        result = await self.flow.async_step_pair({"pairing_code": "123456"})
        self.assertEqual(result["reason"], "wrong_device")
        self.flow.hass.config_entries.async_update_entry.assert_not_called()
        self.flow.hass.config_entries.async_reload.assert_not_awaited()
        self.flow.hass.config_entries.async_schedule_reload.assert_not_called()

    async def test_matching_device_reauth_persists_reloads_aborts_without_creating(self):
        entry = N(
            data={
                "machine_id": "device-a",
                "user_id": "old-user",
                "local_secret": "old-secret",
            },
            unique_id="device-a",
            entry_id="entry",
        )
        self.flow._reauth_entry = entry

        def persist(updated_entry, *, data):
            updated_entry.data = data

        self.flow.hass.config_entries.async_update_entry.side_effect = persist
        update_reload_and_abort = Mock(
            wraps=self.flow.async_update_reload_and_abort
        )
        self.flow.async_update_reload_and_abort = update_reload_and_abort
        create_entry = Mock(wraps=self.flow.async_create_entry)
        self.flow.async_create_entry = create_entry

        result = await self.flow.async_step_pair({"pairing_code": "123456"})

        self.assertEqual(result["type"], "abort")
        self.assertEqual(result["reason"], "reauth_successful")
        update_reload_and_abort.assert_called_once_with(
            entry,
            data_updates={"user_id": "user", "local_secret": "secret"},
        )
        self.flow.hass.config_entries.async_update_entry.assert_called_once_with(
            entry,
            data={
                "machine_id": "device-a",
                "user_id": "user",
                "local_secret": "secret",
            },
        )
        self.assertEqual(entry.data["user_id"], "user")
        self.assertEqual(entry.data["local_secret"], "secret")
        self.flow.hass.config_entries.async_schedule_reload.assert_called_once_with(
            "entry"
        )
        self.flow.hass.config_entries.async_reload.assert_not_awaited()
        create_entry.assert_not_called()

    async def test_invalid_system_response_returns_verification_form(self):
        self.flow._client.get_system.side_effect = api.Code27Error("invalid_response")
        result = await self.flow.async_step_pair({"pairing_code": "123456"})
        self.assertEqual(result["errors"]["base"], "unknown")




class ResponseTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, payload, status=200, method="GET", path="/data/user/system"):
        response = N(status=status, text=AsyncMock(return_value=payload))
        client = api.Code27Client(N(request=AsyncMock(return_value=response)), "test", 8081)
        return await client._call(method, path, signed=False)

    async def test_malformed_envelopes_and_resource_shapes_are_domain_errors(self):
        for payload in ['[]', 'null', '7', '{bad', '{}', '{"data":null}', '{"data":[]}', '{"data":{"ai_language":[]}}']:
            with self.subTest(payload=payload), self.assertRaises(api.Code27Error):
                await self.call(payload)

    async def test_all_401_shapes_are_auth_errors(self):
        for payload in ['<html>unauthorized</html>', '[]', 'null', '', '{"code":"expired"}']:
            with self.subTest(payload=payload), self.assertRaises(api.Code27AuthError):
                await self.call(payload, status=401)

    async def test_invalid_list_members_rejected(self):
        for payload in ['{"data":{}}', '{"data":[null]}', '{"data":[{}]}']:
            with self.assertRaises(api.Code27Error):
                await self.call(payload, path="/data/user/memos")

    async def test_valid_read_and_empty_write_acknowledgement(self):
        self.assertEqual(await self.call('{"data":{}}'), {"data": {}})
        self.assertEqual(await self.call('', status=204, method="POST", path="/data/user/sound/stop"), {})

    async def test_invalid_body_not_in_exception(self):
        with self.assertRaises(api.Code27Error) as result:
            await self.call("private response contents")
        self.assertNotIn("private response", str(result.exception))


class SpellbookTests(unittest.TestCase):
    def test_invalid_field_types_and_templates_are_skipped(self):
        bad = [7, {"trigger": 7, "output": "test"},
               {"trigger": {"de": []}, "output": "test"},
               {"trigger": "hello", "output": {"de": 7}},
               {"trigger": "hello", "output": "test", "catchphrase": []},
               {"trigger": "hello {area}", "output": "{area.name}"},
               {"trigger": "hello {area}", "output": "{area:>2}"},
               {"trigger": "hello {area}", "output": "{"},
               {"trigger": {"de": "hello {area}", "en": "hello {target}"}, "output": "{area}"}]
        for raw in bad:
            with self.subTest(raw=raw):
                self.assertIsNone(book._load_command("test", raw))

    def test_invalid_metadata_cannot_break_discovery(self):
        for id, name in [([], "valid"), ({}, "valid"), ("valid", []), ("", "valid")]:
            self.assertIsNone(book.load_spellbook_from_dict("test", {"id": id, "name": name, "commands": []}))

    def test_valid_localized_template_still_matches(self):
        command = book._load_command("test", {"trigger": "lumos {area}",
                                     "output": {"de": "Licht {area} an", "en": "Light {area} on"}})
        spell = book.EngineSpellbook("test", "Test", [command])
        self.assertEqual(book.find_command(spell, "lumos Küche", "de").output, "Licht Küche an")

    def test_bad_command_does_not_hide_valid_command_in_same_file(self):
        path = N(stem="test", read_text=lambda **kw: "yaml")
        raw = {"id": "test", "name": "Test", "commands": [
            {"trigger": 7, "output": "bad"}, {"trigger": "hello", "output": "valid"}]}
        with patch.object(book, "yaml", N(safe_load=lambda text: raw), create=True):
            loaded = book.load_spellbook_from_path(path)
        self.assertEqual(len(loaded.commands), 1)


class SpellbookDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_file_does_not_break_options_discovery(self):
        class File:
            def __init__(self, name):
                self.name = name
                self.stem = name
            def read_text(self, **kwargs):
                return self.name
            def __lt__(self, other):
                return self.name < other.name
        files = [File("bad"), File("good")]
        directory = N(is_dir=lambda: True, glob=lambda pattern: files if pattern == "*.yaml" else [])
        raw = {"bad": {"id": [], "name": 7, "commands": []},
               "good": {"id": "good", "name": "Good", "commands": []}}
        async def execute(function):
            return function()
        with patch.object(loader, "_book_dirs", return_value=[directory]), \
             patch.object(book, "yaml", N(safe_load=lambda text: raw[text]), create=True):
            result = await loader.async_discover_spellbooks(N(async_add_executor_job=execute))
        self.assertEqual(result, [("good", "Good")])

    async def test_file_deleted_during_lookup_is_not_a_bridge_error(self):
        path = N(stem="gone", read_text=Mock(side_effect=FileNotFoundError()))
        async def execute(function):
            return function()
        with patch.object(loader, "_list_book_files", return_value={"gone": path}):
            result = await loader.async_get_spellbook(N(async_add_executor_job=execute), "gone")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()

