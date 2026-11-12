"""Cross-component regressions from the closing audit; no live device calls."""
from __future__ import annotations

import asyncio
import importlib
import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, Mock, patch

import test_bridge_delivery as delivery
import test_code27_music as music

# Reuse the existing isolated core package. Several legacy harnesses alias it
# into custom_components later; importing a second router here splits Enum identities.
import test_core_engine as core
import test_regression_wpd_wpe as regression

matching = core.matching
ActionExecutor = core.ActionExecutor
ActionTarget = core.ActionTarget
AmbiguousOption = core.AmbiguousOption
AreaPolicy = core.AreaPolicy
ExecutionStatus = core.ExecutionStatus
ScopedAreaPolicy = core.ScopedAreaPolicy
SpellbookRouter = core.SpellbookRouter
ActionStep = core._models_mod.ActionStep
StepPlan = core._models_mod.StepPlan
action_step_fields = importlib.import_module("hase_spellbook_core.step_variables").action_step_fields


class CoordinatorAuditTests(unittest.IsolatedAsyncioTestCase):
    setUp = delivery.DeliveryTests.setUp
    _setup_full_client = regression.RequestExpiredCoordinatorTests._setup_full_client

    async def test_optional_endpoint_keeps_last_value_and_recovers(self):
        self._setup_full_client()
        self.client.list_memos.return_value = [{"id": "memo"}]
        self.c.data = await self.c._async_update_data()
        self.client.list_memos.side_effect = delivery.api.Code27Error("unsupported")
        self.client.get_system.return_value = {"fresh": True}
        self.c.data = await self.c._async_update_data()
        self.assertEqual(self.c.data.memos, [{"id": "memo"}])
        self.assertTrue(self.c.data.system["fresh"])
        self.assertIn("memos", self.c.data.poll_errors)
        self.client.list_memos.side_effect = None
        self.client.list_memos.return_value = []
        result = await self.c._async_update_data()
        self.assertEqual(result.memos, [])
        self.assertNotIn("memos", result.poll_errors)

    async def test_storage_auth_failure_is_fatal(self):
        self._setup_full_client()
        self.client.get_music_storage.side_effect = delivery.api.Code27AuthError("UNAUTHORIZED")
        with patch.object(delivery.coordinator_module, "ConfigEntryAuthFailed", RuntimeError, create=True), \
             self.assertRaisesRegex(RuntimeError, "UNAUTHORIZED"):
            await self.c._async_update_data()

    async def test_storage_clock_failure_is_retryable(self):
        self._setup_full_client()
        self.client.get_music_storage.side_effect = delivery.api.Code27RequestExpiredError()
        with patch.object(delivery.coordinator_module, "UpdateFailed", RuntimeError, create=True), \
             self.assertRaisesRegex(RuntimeError, "clock skew"):
            await self.c._async_update_data()

    async def test_startup_cleanup_retries_until_success(self):
        self._setup_full_client()
        self.client.stop_light_preview.side_effect = [delivery.api.Code27ConnectionError("offline"), None]
        await self.c._async_update_data()
        self.assertFalse(self.c._startup_cleanup_done)
        await self.c._async_update_data()
        await self.c._async_update_data()
        self.assertEqual(self.client.stop_light_preview.await_count, 2)

    async def test_auth_after_ip_recovery_still_requests_reauthentication(self):
        self._setup_full_client()
        self.entry.options["remote_recovery_enabled"] = True
        self.client.attempt_remote_recovery = AsyncMock(return_value="new-host")
        self.hass.config_entries = N(async_update_entry=Mock())
        self.client.get_system.side_effect = [
            delivery.api.Code27ConnectionError("old IP"),
            delivery.api.Code27AuthError("UNAUTHORIZED"),
        ]
        with patch.object(delivery.coordinator_module, "ConfigEntryAuthFailed", RuntimeError, create=True), \
             self.assertRaisesRegex(RuntimeError, "UNAUTHORIZED"):
            await self.c._async_update_data()

    async def test_optional_failure_keeps_newer_push_not_old_rest_value(self):
        self._setup_full_client()
        self.client.get_music_playback.return_value = {"status": "PAUSED"}
        self.c.data = await self.c._async_update_data()
        self.c._handle_music_push("music_playback", {"status": "PLAYING"})
        self.client.get_music_playback.side_effect = delivery.api.Code27Error("unavailable")
        result = await self.c._async_update_data()
        self.assertEqual(result.music_playback["status"], "PLAYING")
        self.assertIn("music_playback", result.poll_errors)

    def prime_chat(self):
        self.c._chat_active_slot = 1
        self.c._chat_dates[1] = ""
        self.c._chat_seen[1] = set()
        self.hass.bus.async_fire = Mock()

    async def test_unacknowledged_output_survives_idle_backoff(self):
        self.prime_chat()
        self.client.send_chat_text.return_value = None
        with patch.object(delivery.coordinator_module.time, "monotonic", return_value=1000):
            await self.c.async_send_chat_reply("licht an", 1)
        self.c._chat_last_event_at = 0
        self.c._chat_last_polled_at = 1000
        self.client.get_chat_history_snapshot.return_value = ("2026-09-24", [
            {"chat_id": "echo", "role": "user", "content": "licht an"},
        ])
        with patch.object(delivery.coordinator_module.time, "monotonic", return_value=1031):
            await self.c._async_poll_chat()
        self.hass.bus.async_fire.assert_not_called()
        self.assertIn((1, "echo"), self.c._own_chat_ids)

    async def test_later_real_message_is_not_consumed_by_old_write(self):
        self.prime_chat()
        self.client.send_chat_text.return_value = None
        with patch.object(delivery.coordinator_module.time, "monotonic", return_value=1000), \
             patch.object(delivery.coordinator_module.time, "time", return_value=2000):
            await self.c.async_send_chat_reply("ok", 1)
        self.client.get_chat_history_snapshot.return_value = ("2026-09-24", [
            {"chat_id": "real", "role": "user", "content": "ok", "created_at": 2040},
        ])
        with patch.object(delivery.coordinator_module.time, "monotonic", return_value=1040):
            await self.c._async_poll_chat()
        self.hass.bus.async_fire.assert_called_once()
        self.assertEqual(self.c._pending_chat_replies, [])

    async def test_missing_image_caption_id_keeps_reservation(self):
        self.client.send_chat_image = AsyncMock(return_value=(None, "image"))
        await self.c.async_send_chat_image_reply(b"x", "x.jpg", "image/jpeg", "caption", 1)
        self.assertEqual(len(self.c._pending_chat_replies), 1)
        self.assertIn((1, "image"), self.c._own_chat_ids)


class MusicAuditTests(music.BackendTestCase):
    async def test_coordinator_push_drives_loop_advance(self):
        await self.add("a", "b", play=True)
        d = delivery.DeliveryTests()
        d.setUp()
        tasks = []
        d.c.async_update_listeners = lambda: tasks.append(asyncio.create_task(
            self.backend.async_note_playback(music.ENTRY_ID, d.c.data.music_playback)
        ))
        base = {**self.playing("a"), "play_mode": "single_loop"}
        d.c._handle_music_push("music_playback", {**base, "position_ms": 59_900})
        await asyncio.gather(*tasks)
        tasks.clear()
        d.c._handle_music_push("music_playback", {**base, "position_ms": 200})
        await asyncio.gather(*tasks)
        self.assertEqual(self.backend.queue(music.ENTRY_ID).current_track.title, "b")

    async def test_track_change_and_position_reset_advance_only_once(self):
        await self.add("a", "b", "c", play=True)
        await self.backend.async_note_playback(music.ENTRY_ID, {
            **self.playing("a"), "play_mode": "single_loop", "position_ms": 59_900,
        })
        result = await self.backend.async_note_playback(music.ENTRY_ID, {
            **self.playing("b"), "play_mode": "single_loop", "position_ms": 200,
        })
        self.assertTrue(result["resynced"])
        self.assertFalse(result["advanced"])
        self.assertEqual(self.backend.queue(music.ENTRY_ID).current_track.title, "b")

    async def test_early_reset_is_not_a_track_end(self):
        await self.add("a", "b", play=True)
        for position in (10_000, 200):
            result = await self.backend.async_note_playback(music.ENTRY_ID, {
                **self.playing("a"), "play_mode": "single_loop", "position_ms": position,
            })
        self.assertFalse(result["advanced"])

    async def test_seek_from_end_to_start_does_not_advance(self):
        await self.add("a", "b", play=True)
        await self.backend.async_note_playback(music.ENTRY_ID, {
            **self.playing("a"), "play_mode": "single_loop", "position_ms": 59_900,
        })
        await self.backend.async_dispatch("transport", {"target": music.ENTRY_ID, "action": "seek", "position": 0})
        result = await self.backend.async_note_playback(music.ENTRY_ID, {
            **self.playing("a"), "play_mode": "single_loop", "position_ms": 200,
        })
        self.assertFalse(result["advanced"])

    async def test_resume_drift_activates_paused_queue(self):
        await self.add("a", "b", play=True)
        self.backend.queue(music.ENTRY_ID).set_playing(False)
        self.coordinator.data.music_playback = self.playing("outside")
        await self.backend.async_dispatch("transport", {"target": music.ENTRY_ID, "action": "play"})
        self.assertTrue(self.backend.queue(music.ENTRY_ID).playing)
        self.assertTrue(self.store.payload["targets"][music.ENTRY_ID]["queue"]["playing"])

    async def test_failed_play_cannot_be_persisted_by_later_operation(self):
        self.control.side_effect = music.MusicError("offline")
        with self.assertRaises(music.MusicError):
            await self.add("a", play=True)
        await self.backend.async_dispatch("set_shuffle", {"target": music.ENTRY_ID, "shuffle": True})
        self.assertFalse(self.backend.queue(music.ENTRY_ID).playing)
        self.assertFalse(self.store.payload["targets"][music.ENTRY_ID]["queue"]["playing"])

    async def test_native_media_seek_uses_queue_discontinuity_handling(self):
        await self.add("a", "b", play=True)
        observation = {**self.playing("a"), "play_mode": "single_loop", "position_ms": 59_900}
        self.coordinator.data.music_playback = observation
        await self.backend.async_note_playback(music.ENTRY_ID, observation)
        fixture = delivery.MediaPlayerTests()
        entity = fixture._entity(client=self.coordinator.client, music_playback=observation)
        entity.coordinator.entry = N(entry_id=music.ENTRY_ID)
        with patch.object(delivery.media_player, "async_get_music_runtime", return_value=self.backend):
            await entity.async_media_seek(0)
        result = await self.backend.async_note_playback(music.ENTRY_ID, {**observation, "position_ms": 100})
        self.assertFalse(result["advanced"])

    async def test_cancellation_leaves_no_playing_intent(self):
        entered = asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        self.control.side_effect = blocked
        task = asyncio.create_task(self.add("a", play=True))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.backend.queue(music.ENTRY_ID).playing)
        self.assertFalse(self.store.payload["targets"][music.ENTRY_ID]["queue"]["playing"])

    async def test_clear_waits_for_inflight_play(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
        self.control.side_effect = blocked
        play = asyncio.create_task(self.add("a", play=True))
        await entered.wait()
        clear = asyncio.create_task(self.backend.async_dispatch("queue_clear", {"target": music.ENTRY_ID}))
        await asyncio.sleep(0)
        self.assertFalse(clear.done())
        release.set()
        await asyncio.gather(play, clear)
        self.assertFalse(self.backend.queue(music.ENTRY_ID).playing)
        self.assertEqual(self.backend.queue(music.ENTRY_ID).items, [])


class MatchingAuditTests(unittest.TestCase):
    def test_negation_inside_free_text_is_not_command_negation(self):
        for trigger, text in (("sage {text}", "sage bitte nicht stoeren"),
                              ("spiele {title}", "spiele No Woman No Cry")):
            with self.subTest(text=text):
                self.assertIsNotNone(matching.match_segments(
                    matching.compile_trigger(trigger), matching.tokenize_with_spans(text), text,
                ))

    def test_opposite_commands_allow_polite_suffix(self):
        self.assertTrue(matching.outputs_are_opposite("schalte Licht ein bitte", "schalte Licht aus bitte"))
        self.assertFalse(matching.outputs_are_opposite("nimm ein Licht aus", "nimm ein Licht aus"))


class ResponseAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_reply_never_uses_full_success_template(self):
        executor = ActionExecutor(None, None)
        executor._execute_action_step = AsyncMock(side_effect=[
            ({"success_count": 1}, {"status": "ok"}),
            ({}, {"status": "denied", "reason_code": "area_not_permitted"}),
        ])
        plan = StepPlan(steps=(
            ActionStep(alias="a", operation="light.turn_on", target=ActionTarget("all_allowed", None)),
            ActionStep(alias="b", operation="light.turn_on", target=ActionTarget("all_allowed", None)),
        ), response_template="Alles erledigt: {a.success_count}, {b.success_count}")
        result = await executor.execute_steps(plan, ScopedAreaPolicy(AreaPolicy(frozenset()), AreaPolicy(frozenset())), context=None, source_area_id=None)
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertEqual(result.data["partial_response_text"], "Schritt 1: abgeschlossen.")

    async def test_missing_target_is_failed_without_invented_zero_counts(self):
        # E2E-02: a missing target's fields must never be simply {} -
        # _AliasNamespace requires every declared field present so a
        # genuinely missing one doesn't raise AttributeError and degrade
        # the whole Combined-Spell response, not just this step's own
        # alias (see executor.py's own comment on this early return).
        # "No invented zero counts" (this test's own name/original
        # concern) still holds: every value here is None, the sanitizer's
        # missing-value token downstream - never a misleading 0 that
        # would imply "attempted, zero succeeded".
        executor = ActionExecutor(core.FakeResolver({}), None)
        fields, report = await executor._execute_action_step(
            ActionStep(alias="a", operation="light.turn_on", target=ActionTarget("entity_id", "light.missing")),
            ScopedAreaPolicy(AreaPolicy(None, unrestricted=True), AreaPolicy(None, unrestricted=True)),
            context=None, source_area_id=None,
        )
        expected_fields = {name: None for name in action_step_fields("light.turn_on", "entity_id")}
        self.assertEqual(fields, expected_fields)
        self.assertNotIn(0, fields.values())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason_code"], "target_not_found")

    async def test_multiword_ordinal_chooses_requested_option(self):
        router = SpellbookRouter(None)
        router._store = N(discard=Mock())
        router._dispatch_match = AsyncMock(return_value="chosen")
        a = AmbiguousOption(None, "a", "licht", "Licht an")
        b = AmbiguousOption(None, "b", "licht", "Licht aus")
        config = N(spellbook=N(commands=[N(spell_id="a", enabled=True), N(spell_id="b", enabled=True)]), policy=None)
        await router._consume_reply(N(text="die zweite"), None, N(option_a=a, option_b=b), config)
        self.assertIs(router._dispatch_match.await_args.args[1], b)


class ReconfigureAuditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import test_mcp_config_flow as flows
        self.module = flows._load_flow_module()
        self.module.Code27ConnectionError = delivery.api.Code27ConnectionError
        self.module.Code27Error = delivery.api.Code27Error
        self.entry = N(entry_id="device", unique_id="machine", options={}, data={
            "entry_kind": "code27_device", "host": "old", "port": 8081,
            "user_id": "user", "local_secret": "secret", "machine_id": "machine",
        })
        self.client = N(get_system=AsyncMock(return_value={"machine_id": "machine"}))
        self.module.Code27Client = Mock(return_value=self.client)
        self.flow = self.module.HaseConfigFlow()
        self.flow.hass = N(config_entries=N(async_get_entry=lambda _: self.entry))
        self.flow.context = {"entry_id": "device"}
        self.flow.async_update_reload_and_abort = Mock(return_value={"type": "abort", "reason": "reconfigure_successful"})

    async def test_updates_address_only_after_identity_check(self):
        result = await self.flow.async_step_reconfigure({"host": "new", "port": 8082})
        self.assertEqual(result["reason"], "reconfigure_successful")
        self.flow.async_update_reload_and_abort.assert_called_once_with(
            self.entry, data_updates={"host": "new", "port": 8082},
        )
        self.assertEqual(self.entry.data["local_secret"], "secret")

    async def test_wrong_device_leaves_entry_unchanged(self):
        self.client.get_system.return_value = {"machine_id": "other"}
        result = await self.flow.async_step_reconfigure({"host": "new", "port": 8082})
        self.assertEqual(result["reason"], "wrong_device")
        self.flow.async_update_reload_and_abort.assert_not_called()

    async def test_other_providers_do_not_enter_code27_reconfigure(self):
        self.entry.data = {"entry_kind": "mcp_server"}
        result = await self.flow.async_step_reconfigure({"host": "new", "port": 8082})
        self.assertEqual(result["reason"], "not_supported")
        self.module.Code27Client.assert_not_called()

    async def test_legacy_paired_identity_is_preserved(self):
        self.entry.data.pop("entry_kind")
        self.entry.data["machine_id"] = "user"
        self.client.get_system.return_value = {}
        result = await self.flow.async_step_reconfigure({"host": "new", "port": 8082})
        self.assertEqual(result["reason"], "reconfigure_successful")

    async def test_connection_failure_is_retryable_without_update(self):
        self.client.get_system.side_effect = delivery.api.Code27ConnectionError("offline")
        result = await self.flow.async_step_reconfigure({"host": "new", "port": 8082})
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
        self.flow.async_update_reload_and_abort.assert_not_called()


class ExternalHubFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import test_mcp_config_flow as flows
        self.module = flows._load_flow_module()

    def test_embedded_options_flow_is_the_registered_provider_flow(self):
        entry = N(data={"entry_kind": "code27_device"}, options={})
        flow = self.module.HaseConfigFlow.async_get_options_flow(entry)
        self.assertIsInstance(flow, self.module.Code27OptionsFlow)
        self.assertNotIsInstance(flow, self.module.UnavailableProviderOptionsFlow)

    async def test_external_hub_can_rebind_without_changing_owner(self):
        original = N(entry_id="hase-binding", data={
            "entry_kind": "code27_external_hub", "code27_entry_id": "deleted",
        })
        target = N(entry_id="new-code27", domain="code27", title="CODE27 Papa")
        entries = N(
            async_get_entry=lambda entry_id: original if entry_id == "hase-binding" else target,
            async_entries=lambda domain: [target] if domain == "code27" else [original],
        )
        flow = self.module.HaseConfigFlow()
        flow.hass = N(config_entries=entries)
        flow.context = {"entry_id": original.entry_id}
        flow.async_update_and_abort = Mock(return_value={"type": "abort", "reason": "reconfigure_successful"})
        form = await flow.async_step_reconfigure()
        self.assertEqual(form["step_id"], "reconfigure_external_hub")
        result = await flow.async_step_reconfigure({"code27_entry_id": target.entry_id})
        self.assertEqual(result["reason"], "reconfigure_successful")
        flow.async_update_and_abort.assert_called_once_with(
            original, data_updates={"code27_entry_id": target.entry_id},
        )


class TimeoutAuditTests(unittest.IsolatedAsyncioTestCase):
    setUp = delivery.DeliveryTests.setUp

    async def test_timeout_after_side_effect_is_not_retried_or_announced_as_success(self):
        effects = []
        async def action_then_stall(*args, **kwargs):
            effects.append("already executed")
            await asyncio.Event().wait()
        self.entry.options["bridge_enabled"] = True
        with patch.object(delivery.bridge, "Context", lambda: None, create=True), \
             patch.object(delivery.bridge, "conversation", N(async_converse=action_then_stall), create=True), \
             patch.object(delivery.bridge, "_ASSIST_TIMEOUT_SEC", 0.01), \
             patch.object(delivery.bridge, "_output_speech", AsyncMock()) as output:
            result = await delivery.bridge._forward_to_assist(
                self.hass, self.entry, self.c, "licht an", None, skip_ai_cleanup=True,
            )
        self.assertEqual(effects, ["already executed"])
        self.assertEqual(result, (None, None, "licht an"))
        output.assert_not_awaited()
