"""Regression coverage for C8 (AI), C9 (exposure), C10 (request origin)."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import test_bridge_delivery as support

N = support.N
bridge = support.bridge
spellbook = support.spellbook


class SafetyTests(unittest.IsolatedAsyncioTestCase):
    setUp = support.DeliveryTests.setUp

    def enable_ai(self, response=None):
        self.entry.options.update({
            "bridge_ai_cleanup_enabled": True,
            "bridge_ai_task_entity_id": "ai_task.local",
        })
        self.hass.services = N(async_call=AsyncMock(return_value=response))

    async def test_malformed_or_empty_ai_output_falls_back(self):
        original = "Licht Küche ausschalten"
        for response in [None, [], {}, {"data": None}, {"data": ""},
                         {"data": {"text": 7}}, {"data": {"result": ["bad"]}},
                         {"data": {"text": True}}]:
            with self.subTest(response=response):
                self.enable_ai(response)
                self.assertEqual(await bridge._ai_cleanup(self.hass, self.entry, original, None), original)

    async def test_ai_provider_failure_falls_back(self):
        self.enable_ai()
        self.hass.services.async_call.side_effect = RuntimeError("provider unavailable")
        with patch.object(bridge, "_log_activity"):
            self.assertEqual(await bridge._ai_cleanup(self.hass, self.entry, "Licht aus", None), "Licht aus")

    async def test_ai_timeout_still_reaches_assist_with_original(self):
        self.enable_ai()
        cancelled = asyncio.Event()

        async def provider(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.hass.services.async_call.side_effect = provider
        with patch.object(bridge, "_AI_CLEANUP_TIMEOUT", 0.01), \
             patch.object(bridge, "_ask_assist", AsyncMock(return_value=(None, None, False))) as assist, \
             patch.object(bridge, "_log_activity"):
            await asyncio.wait_for(bridge._forward_to_assist(self.hass, self.entry, self.c, "Licht aus", None), 1)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(assist.call_args.args[2], "Licht aus")

    async def test_cancelled_ai_does_not_fall_through_to_assist(self):
        self.enable_ai()
        entered = asyncio.Event()

        async def provider(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        self.hass.services.async_call.side_effect = provider
        with patch.object(bridge, "_ask_assist", AsyncMock()) as assist:
            task = asyncio.create_task(bridge._forward_to_assist(self.hass, self.entry, self.c, "Licht aus", None))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            assist.assert_not_awaited()

    async def test_valid_unicode_ai_output_and_entity_contract(self):
        self.enable_ai({"data": "Licht Küche aus"})
        result = await bridge._ai_cleanup(self.hass, self.entry, "Bitte Licht Küche aus", None)
        self.assertEqual(result, "Licht Küche aus")
        call = self.hass.services.async_call.call_args
        self.assertEqual(call.args[2]["entity_id"], "ai_task.local")
        self.assertTrue(call.kwargs["return_response"])

    async def test_existing_ai_guards_remain_active(self):
        for output in ["Licht Küche", "Licht Küche aus ☀", "Licht Küche auf 70 Prozent"]:
            self.enable_ai({"data": output})
            with patch.object(bridge, "_log_activity"):
                self.assertEqual(await bridge._ai_cleanup(self.hass, self.entry, "Licht Küche aus", None), "Licht Küche aus")

    async def test_origin_survives_queue_ai_and_character_changes(self):
        self.hass.config.language = "en"
        self.c.data.system = {"ai_language": "de"}
        self.entry.options["bridge_relay_response"] = True
        self.enable_ai({"data": "Licht Küche aus"})
        request = bridge._capture_request(self.hass, self.entry, self.c, {"slot": 1})
        entered, release = asyncio.Event(), asyncio.Event()

        async def provider(*args, **kwargs):
            entered.set()
            await release.wait()
            return {"data": "Licht Küche aus"}

        self.hass.services.async_call.side_effect = provider
        converse = AsyncMock(return_value=N(response=N(
            speech={"plain": {"speech": "Erledigt"}}, response_type="done",
        )))
        await self.c.bridge_lock.acquire()
        with patch.object(bridge, "conversation", N(async_converse=converse), create=True), \
             patch.object(bridge, "Context", N, create=True):
            task = asyncio.create_task(bridge._process_bridge_message(
                self.hass, self.entry, self.c, "Computer Licht Küche aus", "sensor.test", request,
            ))
            await asyncio.sleep(0)
            self.c.data.current_slot["slot_number"] = 2
            self.c.data.system["ai_language"] = "en"
            self.c.bridge_lock.release()
            await entered.wait()
            self.c.data.current_slot["slot_number"] = 3
            release.set()
            await task
        self.assertEqual(converse.call_args.kwargs["language"], "de")
        self.client.send_chat_text.assert_awaited_once_with("Erledigt", 1, "de")
        self.assertIsNone(bridge._REQUEST.get())

    async def test_event_slot_and_language_override_current_snapshot(self):
        request = bridge._capture_request(self.hass, self.entry, self.c, {"slot": 4, "language": "fr"})
        self.assertEqual((request.slot, request.language), (4, "fr"))

    async def test_correction_cannot_be_confirmed_from_other_slot(self):
        self.c.pending_correction = support.coordinator_module.PendingCorrection(
            "Licht Küche aus", "Küche", slot=2, language="de", last_suggested="Büro",
        )
        with patch.object(bridge, "_retry_with_candidate", AsyncMock()) as retry:
            consumed = await bridge._handle_correction_reply(self.hass, self.entry, self.c, "ja", None)
        self.assertFalse(consumed)
        retry.assert_not_awaited()
        self.assertIsNone(self.c.pending_correction)

    async def test_correction_keeps_original_language_and_resets_context(self):
        self.c.data.system["ai_language"] = "en"
        self.c.pending_correction = support.coordinator_module.PendingCorrection(
            "Licht Küche aus", "Küche", slot=1, language="de", last_suggested="Büro",
        )
        requests = []

        async def retry(*args):
            requests.append(bridge._current_request(self.hass, self.entry, self.c))

        with patch.object(bridge, "_retry_with_candidate", retry):
            await bridge._handle_correction_reply(self.hass, self.entry, self.c, "ja", None)
        self.assertEqual(requests[0].language, "de")
        self.assertIsNone(bridge._REQUEST.get())

    async def test_tts_skipped_after_switch_chat_still_uses_origin(self):
        self.entry.options.update({"bridge_tts_enabled": True, "bridge_relay_response": True})
        self.client.get_current_slot = AsyncMock(return_value={"slot_number": 2})
        self.client.tts_audition = AsyncMock()
        await bridge._output_speech(
            self.hass, self.entry, self.c, "Erledigt", "sensor.test",
            request=bridge.BridgeRequest(self.entry.entry_id, 1, "de"),
        )
        self.client.tts_audition.assert_not_awaited()
        self.client.send_chat_text.assert_awaited_once_with("Erledigt", 1, "de")

    async def test_tts_uses_expected_slot_voice_when_still_active(self):
        self.client.get_current_slot = AsyncMock(return_value={"slot_number": 1})
        self.client.tts_audition = AsyncMock()
        self.client.get_tts_audition_state = AsyncMock(return_value={"state": "ENDED"})
        self.c.data.character_slots = [{"slot_number": 1, "voice_id": "voice-one", "group_key": "group"}]
        with patch.object(bridge, "_TTS_POLL_INTERVAL", 0):
            await bridge.async_speak_text(self.hass, self.entry, self.c, "Hallo", "sensor.test", expected_slot=1)
        self.client.tts_audition.assert_awaited_once_with("voice-one", "Hallo", "group")

    async def test_revoked_pending_suggestion_is_not_retried(self):
        pending = support.coordinator_module.PendingCorrection("Licht Büro aus", "Büro")
        self.c.pending_correction = pending
        with patch.object(spellbook, "verify_named_candidate", return_value=None), \
             patch.object(bridge, "_forward_to_assist", AsyncMock()) as forward:
            await bridge._retry_with_candidate(self.hass, self.entry, self.c, pending, "Büro", None)
        forward.assert_not_awaited()
        self.assertIsNone(self.c.pending_correction)


class ExposureTests(unittest.TestCase):
    def test_only_exposed_present_enabled_entities_and_their_areas(self):
        def entity(id, name, area=None, device=None, disabled=None):
            return N(entity_id=id, name=name, original_name=None, aliases={name + " Alias"},
                     area_id=area, device_id=device, disabled_by=disabled, has_entity_name=True)

        entries = {
            "light.ok": entity("light.ok", "Lampe", device="dev"),
            "light.override": entity("light.override", "Override", area="override", device="dev"),
            "switch.private": entity("switch.private", "Heizungspumpe", area="private"),
            "light.disabled": entity("light.disabled", "Deaktiviert", area="disabled", disabled="user"),
            "light.dead": entity("light.dead", "Altgerät", area="dead"),
        }
        exposed = set(entries) - {"switch.private"}
        hass = N(states=N(get=lambda id: None if id == "light.dead" else N()))
        devices = {"dev": N(name="Wohnzimmer", name_by_user=None, area_id="room")}
        areas = {id: N(id=id, name=id, aliases={id + " alias"}) for id in ["room", "override", "private", "disabled", "dead", "empty"]}
        with patch.object(spellbook, "er", N(async_get=lambda h: N(entities=entries)), create=True), \
             patch.object(spellbook, "dr", N(async_get=lambda h: N(devices=devices)), create=True), \
             patch.object(spellbook, "ar", N(async_get=lambda h: N(areas=areas)), create=True), \
             patch.object(spellbook, "async_should_expose", lambda h, domain, id: domain == "conversation" and id in exposed, create=True):
            names = spellbook._registry_names(hass, True)
            for name in ["Lampe", "Lampe Alias", "Wohnzimmer Lampe", "room", "room alias", "override"]:
                self.assertIn(name, names)
            for name in ["Heizungspumpe", "Deaktiviert", "Altgerät", "private", "disabled", "dead", "empty"]:
                self.assertNotIn(name, names)
            exposed.clear()
            self.assertEqual(spellbook._registry_names(hass, True), [])


class AssistTimeoutTests(unittest.IsolatedAsyncioTestCase):
    """_ask_assist must return (None, None, False) on timeout instead of
    hanging indefinitely inside bridge_lock (B3)."""
    setUp = support.DeliveryTests.setUp

    async def test_assist_timeout_returns_none_triple(self):
        """asyncio.timeout fires → _ask_assist returns (None, None, False)."""
        async def _hanging(*args, **kwargs):
            await asyncio.sleep(9999)

        self.entry.options["bridge_enabled"] = True

        with patch.object(bridge, "Context", lambda: None, create=True), \
             patch.object(bridge, "conversation", N(async_converse=_hanging), create=True), \
             patch.object(bridge, "_ASSIST_TIMEOUT_SEC", 0.01):
            speech, resp, cont = await bridge._ask_assist(
                self.hass, self.entry, "Licht an", None
            )
        self.assertIsNone(speech)
        self.assertIsNone(resp)
        self.assertFalse(cont)


if __name__ == "__main__":
    unittest.main()
