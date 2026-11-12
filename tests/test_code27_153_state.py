"""Firmware 1.5.3 additions: utterance speech, device/conversation state,
AI language."""
from __future__ import annotations

import ast
import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import test_bridge_delivery as support

api = support.api
bridge = support.bridge
PROVIDER = Path(__file__).resolve().parents[1] / "custom_components" / "hase" / "providers" / "code27"


class UtteranceSpeechTests(unittest.IsolatedAsyncioTestCase):
    setUp = support.DeliveryTests.setUp

    def _speak(self, **kwargs):
        self.activity = []
        patcher = patch.object(
            bridge, "_log_activity", lambda hass, entry, message, entity_id=None: self.activity.append(message)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return bridge.async_speak_text(self.hass, self.entry, self.c, "Hallo", "sensor.test", **kwargs)

    async def test_utterance_is_followed_to_completion(self):
        self.client.speak_text = AsyncMock(return_value={"utterance_id": "u1", "state": "queued"})
        self.client.get_utterance = AsyncMock(side_effect=[{"state": "playing"}, {"state": "completed"}])
        self.client.tts_audition = AsyncMock()
        with patch.object(bridge, "_TTS_POLL_INTERVAL", 0):
            await self._speak()
        self.client.speak_text.assert_awaited_once_with("Hallo", 1)
        self.client.tts_audition.assert_not_awaited()
        self.assertIn(bridge._msg(self.hass, "tts_ok"), self.activity)
        self.assertEqual(self.c.data.last_tts_message["content"], "Hallo")

    async def test_failed_utterance_is_reported(self):
        self.client.speak_text = AsyncMock(return_value={"utterance_id": "u1", "state": "queued"})
        self.client.get_utterance = AsyncMock(return_value={"state": "failed"})
        with patch.object(bridge, "_TTS_POLL_INTERVAL", 0):
            await self._speak()
        self.assertIn(bridge._msg(self.hass, "tts_failed"), self.activity)

    async def test_start_error_is_reported_without_fallback(self):
        self.client.speak_text = AsyncMock(side_effect=api.Code27Error("blocked"))
        self.client.tts_audition = AsyncMock()
        await self._speak()
        self.client.tts_audition.assert_not_awaited()
        self.assertTrue(any("blocked" in m for m in self.activity))

    async def test_old_firmware_falls_back_to_preview_endpoint(self):
        self.client.speak_text = AsyncMock(return_value=None)
        self.client.tts_audition = AsyncMock()
        self.client.get_tts_audition_state = AsyncMock(return_value={"state": "ENDED"})
        self.c.data.character_slots = [{"slot_number": 1, "voice_id": "v", "group_key": "g"}]
        with patch.object(bridge, "_TTS_POLL_INTERVAL", 0):
            await self._speak()
        self.client.tts_audition.assert_awaited_once_with("v", "Hallo", "g")


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_speak_text_posts_utterance(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={"data": {"utterance_id": "u"}})
        self.assertEqual(await client.speak_text("hi", 2), {"utterance_id": "u"})
        method, path = client._call.mock_calls[0].args
        body = client._call.mock_calls[0].kwargs["body"]
        self.assertEqual((method, path), ("POST", "/data/user/tts/utterances"))
        self.assertEqual((body["slot"], body["text"]), (2, "hi"))
        self.assertEqual(len(body["request_id"]), 36)

    async def test_speak_text_is_none_and_remembered_before_1_5_3(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(side_effect=api.Code27NotFoundError("404"))
        self.assertIsNone(await client.speak_text("hi"))
        self.assertIsNone(await client.speak_text("again"))
        self.assertEqual(client._call.await_count, 1)

    async def test_state_reads_are_empty_before_1_5_3(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(side_effect=api.Code27NotFoundError("404"))
        self.assertEqual(await client.get_device_state(), {})
        self.assertEqual(await client.get_conversation_state(), {})

    async def test_ai_language_round_trip(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={"data": {"ai_language": "de"}})
        self.assertEqual(await client.get_ai_language(), "de")
        await client.set_ai_language("fr")
        client._call.assert_awaited_with(
            "PUT", "/data/user/ai-language", body={"ai_language": "fr"}
        )


class AlarmBodyTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_fills_the_fields_1_5_3_requires(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={"data": {"id": "a"}})
        await client.create_alarm({"hour": 7, "minute": 5, "label": "x", "repeat_mask": 0, "enabled": True})
        body = client._call.mock_calls[0].kwargs["body"]["alarm"]
        for key in ("tag", "ringtone", "snooze_enabled", "snooze_minutes", "snooze_max_count"):
            self.assertIn(key, body)
        self.assertEqual((body["hour"], body["minute"]), (7, 5))

    async def test_update_keeps_ringtone_and_snooze_of_the_existing_alarm(self):
        client = api.Code27Client(None, "device", 8081)
        client.list_alarms = AsyncMock(return_value=[{
            "id": "a", "ringtone": "ringtone_custom", "snooze_minutes": 4,
            "snooze_enabled": False, "snooze_max_count": 1, "tag": "t",
        }])
        client._call = AsyncMock(return_value={"data": {}})
        await client.update_alarm("a", {"hour": 8, "minute": 0, "label": "y", "repeat_mask": 127, "enabled": True})
        body = client._call.mock_calls[0].kwargs["body"]["alarm"]
        self.assertEqual(client._call.mock_calls[0].args, ("PUT", "/data/user/alarm/a"))
        self.assertEqual(
            (body["ringtone"], body["snooze_minutes"], body["snooze_enabled"], body["tag"]),
            ("ringtone_custom", 4, False, "t"),
        )
        self.assertEqual(body["hour"], 8)


class MemoBodyTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_note_fills_the_required_fields(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={"data": {"id": "m"}})
        await client.create_memo({"title": "t", "body": "b", "memo_type": "static"})
        body = client._call.mock_calls[0].kwargs["body"]["memo"]
        for key in ("schedule_type", "interval_days", "trigger_year", "trigger_month",
                    "trigger_day", "hour", "minute", "timezone", "ringtone"):
            self.assertIn(key, body)
        self.assertEqual((body["title"], body["schedule_type"]), ("t", "none"))

    async def test_update_keeps_the_schedule_of_a_reminder(self):
        client = api.Code27Client(None, "device", 8081)
        client.list_memos = AsyncMock(return_value=[{
            "id": "m", "title": "old", "body": "b", "memo_type": "reminder",
            "schedule_type": "once_at", "trigger_year": 2026, "trigger_month": 12,
            "trigger_day": 24, "hour": 9, "minute": 30, "timezone": "Europe/Berlin",
            "interval_days": 1, "ringtone": "ringtone_default", "is_expired": False,
        }])
        client._call = AsyncMock(return_value={"data": {}})
        await client.update_memo("m", {"title": "new"})
        body = client._call.mock_calls[0].kwargs["body"]["memo"]
        self.assertEqual(client._call.mock_calls[0].args, ("PUT", "/data/user/memos/m"))
        self.assertEqual(
            (body["title"], body["memo_type"], body["schedule_type"], body["trigger_day"],
             body["hour"], body["timezone"], body["body"]),
            ("new", "reminder", "once_at", 24, 9, "Europe/Berlin", "b"),
        )
        self.assertNotIn("is_expired", body)


class OutfitLinkTests(unittest.IsolatedAsyncioTestCase):
    CHARS = [
        {"slot_number": 1, "link_other_original_cloth": ",,", "custom_cloths": "\nLylia.vrm\nLylia_v2.vrm"},
        {"slot_number": 4, "link_other_original_cloth": "1:2,1:2,1:1", "custom_cloths": "\n\n"},
        {"slot_number": 5, "link_other_original_cloth": "4:3,,1:3", "custom_cloths": ""},
        {"slot_number": 6, "link_other_original_cloth": "6:1", "custom_cloths": ""},
        {"slot_number": 7, "link_other_original_cloth": "x:y,7:", "custom_cloths": ""},
    ]
    resolve = staticmethod(api.Code27Client._resolve_outfit_link)

    def test_plain_outfits_resolve_to_themselves(self):
        self.assertEqual(self.resolve(self.CHARS, 1, 2), (1, 2))
        self.assertEqual(self.resolve(self.CHARS, 9, 1), (9, 1), "unknown slot")

    def test_a_link_points_to_the_other_characters_outfit(self):
        self.assertEqual(self.resolve(self.CHARS, 4, 1), (1, 2))
        self.assertEqual(self.resolve(self.CHARS, 4, 3), (1, 1))

    def test_links_chain_and_stop_on_loops_or_garbage(self):
        self.assertEqual(self.resolve(self.CHARS, 5, 1), (1, 1), "5:1 -> 4:3 -> 1:1")
        self.assertEqual(self.resolve(self.CHARS, 6, 1), (6, 1), "self link")
        self.assertEqual(self.resolve(self.CHARS, 7, 1), (7, 1), "malformed")
        self.assertEqual(self.resolve(self.CHARS, 7, 2), (7, 2), "empty target")
        self.assertEqual(self.resolve(self.CHARS, 4, 99), (4, 99), "outfit beyond the list")

    async def test_a_linked_outfit_shows_the_original_portrait_not_a_custom_model(self):
        # Slot 1's outfit 2 is the custom model "Lylia" for Lilya herself, but
        # Ella's link "1:2" means the factory outfit 2 of slot 1.
        client = api.Code27Client(None, "device", 8081)
        client.list_character_settings = AsyncMock(return_value=self.CHARS)
        client.get_cloth_uploaded_models = AsyncMock(return_value=[])
        client.get_original_portraits_manifest = AsyncMock(
            return_value=[{"slot_number": 1, "cloth_id": 2, "filename": "1_2.png"}]
        )
        client._call_binary = AsyncMock(return_value=("image/png", b"stock"))
        result = await client.get_character_portrait(4, 1)
        self.assertEqual(result, ("image/png", b"stock"))
        client._call_binary.assert_awaited_once_with(
            "/resources/system/assets/original-portraits/1_2.png"
        )

    async def test_an_unlinked_custom_outfit_still_uses_its_own_thumbnail(self):
        client = api.Code27Client(None, "device", 8081)
        client.list_character_settings = AsyncMock(return_value=self.CHARS)
        client.get_cloth_uploaded_models = AsyncMock(return_value=[])
        client._call_binary = AsyncMock(return_value=("image/png", b"custom"))
        self.assertEqual(await client.get_character_portrait(1, 2), ("image/png", b"custom"))
        client._call_binary.assert_awaited_once_with(
            "/resources/user/assets/thumbnails/1_2_Lylia.png"
        )

    async def test_portrait_still_works_when_the_character_list_cannot_be_read(self):
        client = api.Code27Client(None, "device", 8081)
        client.list_character_settings = AsyncMock(side_effect=api.Code27Error("down"))
        client.get_cloth_uploaded_models = AsyncMock(return_value=[])
        client.get_original_portraits_manifest = AsyncMock(return_value=[])
        client._call_binary = AsyncMock(return_value=("image/png", b"png"))
        self.assertEqual(await client.get_character_portrait(2, 2), ("image/png", b"png"))
        client._call_binary.assert_awaited_once_with(
            "/resources/system/assets/original-portraits/2_2.png"
        )


class OptionalStatePollTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        hass = support.N(
            config=support.N(language="de"),
            bus=support.N(async_fire=lambda kind, data: None),
            async_create_task=lambda coro, **kwargs: asyncio.create_task(coro),
        )
        self.client = support.N()
        self.c = support.coordinator_module.Code27Coordinator(
            hass, support.N(entry_id="e", options={}, data={}), self.client, 15
        )

    async def test_returns_the_state(self):
        self.client.get_device_state = AsyncMock(return_value={"screen_on": True})
        self.assertEqual(
            await self.c._poll_optional_state("get_device_state"), {"screen_on": True}
        )

    async def test_failure_is_non_fatal(self):
        self.client.get_conversation_state = AsyncMock(side_effect=RuntimeError("boom"))
        result = await self.c._poll_optional_state("get_conversation_state")
        self.assertIsInstance(result, api.Code27Error)

    async def test_missing_client_method_is_non_fatal(self):
        self.assertIsInstance(
            await self.c._poll_optional_state("get_device_state"), api.Code27Error
        )

    async def test_auth_failure_still_propagates(self):
        self.client.get_device_state = AsyncMock(side_effect=api.Code27AuthError("x"))
        with self.assertRaises(api.Code27AuthError):
            await self.c._poll_optional_state("get_device_state")


class AiLanguageOptionsTests(unittest.TestCase):
    def test_offered_languages_match_the_device_enum(self):
        tree = ast.parse((PROVIDER / "select.py").read_text(encoding="utf-8"))
        node = next(
            n for n in tree.body
            if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "AI_LANGUAGES"
        )
        # 1.5.3 OpenAPI: PUT /ai-language accepts exactly these codes.
        self.assertEqual(set(ast.literal_eval(node.value)), {"en", "ja", "fr", "es", "de"})


if __name__ == "__main__":
    unittest.main()
