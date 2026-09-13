"""C1-C4 regression tests, runnable without a Home Assistant installation.

Load the actual source with only external imports replaced by small adapters.
No device, HA service, network request or filesystem write is performed by tests.
"""
import ast
import asyncio
import hashlib
import hmac
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "hase"


def load_source(name, **adapters):
    provider = ROOT / "providers" / "code27"
    source_path = ROOT / f"{name}.py"
    if name in {"api", "coordinator", "bridge", "spellbook", "websocket_client", "entity", "media_player"}:
        source_path = provider / f"{name}.py"
    paths = [source_path]
    if name == "const":
        paths = [ROOT / "const.py", provider / "const.py"]
    elif name == "__init__":
        paths = [provider / "services.py", provider / "__init__.py", source_path]
    elif name == "config_flow":
        paths = [provider / "config_flow.py", source_path]
    nodes = [node for path in paths for node in ast.parse(path.read_text(encoding="utf-8")).body]
    nodes = [n for n in nodes if not (isinstance(n, ast.ImportFrom) and n.module == "__future__")]
    tree = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)] + nodes, type_ignores=[])
    ast.fix_missing_locations(tree)
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom)
        and (node.level or (node.module or "").startswith("homeassistant"))
        or isinstance(node, ast.Import)
        and any(alias.name in {"aiohttp", "yaml", "voluptuous"} for alias in node.names)
    )]
    module = types.ModuleType(f"code27_test_{name}")
    module.__file__ = str(source_path)
    module.__dict__.update(adapters)
    sys.modules[module.__name__] = module
    exec(compile(tree, module.__file__, "exec"), module.__dict__)
    return module


N = types.SimpleNamespace
const = load_source("const")
constants = {key: value for key, value in vars(const).items() if key.isupper()}


class _FakeFormData:
    """Stand-in for aiohttp.FormData - records what would have been sent
    without needing a real aiohttp install in this lightweight harness."""

    def __init__(self) -> None:
        self.fields: list[tuple] = []

    def add_field(self, name, value, filename=None, content_type=None):
        self.fields.append((name, value, filename, content_type))


api = load_source("api", aiohttp=N(ClientError=OSError, FormData=_FakeFormData))


class _FakeBrowseMedia:
    """Stand-in for homeassistant.components.media_player.BrowseMedia -
    just records whatever kwargs it was built with."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


media_player = load_source(
    "media_player", **constants,
    MediaPlayerEntity=type("MediaPlayerEntity", (), {}),
    Code27Entity=type("Code27Entity", (), {}),
    MediaPlayerEntityFeature=N(
        PLAY=1, PAUSE=2, NEXT_TRACK=4, PREVIOUS_TRACK=8, VOLUME_SET=16,
        SELECT_SOURCE=32, PLAY_MEDIA=64, BROWSE_MEDIA=128,
    ),
    MediaPlayerState=N(PLAYING="playing", PAUSED="paused", IDLE="idle"),
    MediaClass=N(DIRECTORY="directory", MUSIC="music"),
    MediaType=N(MUSIC="music"),
    BrowseMedia=_FakeBrowseMedia,
    HomeAssistantError=RuntimeError,
    Code27DeviceError=api.Code27DeviceError,
)


class CoordinatorBase:
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, *args, **kwargs):
        self.hass = hass

    def async_update_listeners(self):
        pass


coordinator_module = load_source(
    "coordinator", **constants, DataUpdateCoordinator=CoordinatorBase,
    Code27Error=api.Code27Error, Code27AuthError=api.Code27AuthError,
    Code27ConnectionError=api.Code27ConnectionError, CONF_HOST="host",
)
spellbook = load_source("spellbook")
bridge = load_source(
    "bridge", **constants, spellbook=spellbook, callback=lambda f: f,
    PendingCorrection=coordinator_module.PendingCorrection,
    EVENT_USER_MESSAGE=coordinator_module.EVENT_USER_MESSAGE,
    intent=N(IntentResponseType=N(ERROR="error", ACTION_DONE="done")),
)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        self.hass = N(
            config=N(language="de"),
            bus=N(async_fire=lambda kind, data: self.events.append((kind, data))),
            async_create_task=lambda coro, **kwargs: asyncio.create_task(coro),
        )
        self.entry = N(entry_id="device-a", options={
            "bridge_enabled": True, "bridge_codeword": "Computer",
        })
        self.client = N(
            get_chat_history_snapshot=AsyncMock(return_value=("2026-09-09", [])),
            send_chat_text=AsyncMock(return_value="own-1"),
        )
        self.c = coordinator_module.Code27Coordinator(self.hass, self.entry, self.client, 15)
        self.c.data = coordinator_module.Code27Data(current_slot={"slot_number": 1})

    def messages(self, *ids):
        return [{"chat_id": i, "role": "user", "content": f"Computer {i}"} for i in ids]

    async def poll(self, messages):
        self.client.get_chat_history_snapshot.return_value = ("2026-09-09", messages)
        await self.c._async_poll_chat()

    async def test_empty_baseline_then_first_message(self):
        await self.poll([])
        await self.poll(self.messages("first"))
        self.assertEqual([e[1]["chat_id"] for e in self.events], ["first"])

    async def test_burst_delivers_every_message_once(self):
        await self.poll(self.messages("old"))
        messages = self.messages("old", *[str(i) for i in range(50)])
        await self.poll(messages)
        await self.poll(messages)
        self.assertEqual(len(self.events), 50)
        self.assertEqual([e[1]["chat_id"] for e in self.events], [str(i) for i in range(50)])
        self.assertEqual(len(self.c.data.chat_messages), 30)

    async def test_switch_and_return_do_not_replay_inactive_history(self):
        await self.poll(self.messages("a-old"))
        self.c.data.current_slot["slot_number"] = 2
        await self.poll(self.messages("b-old"))
        self.c.data.current_slot["slot_number"] = 1
        await self.poll(self.messages("a-old", "a-inactive"))
        self.assertEqual(self.events, [])
        await self.poll(self.messages("a-old", "a-inactive", "a-new"))
        self.assertEqual(self.events[0][1]["slot"], 1)
        self.assertEqual(self.events[0][1]["chat_id"], "a-new")

    async def test_late_poll_cannot_replace_new_slot(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def snapshot(*args):
            entered.set()
            await release.wait()
            return "2026-09-09", self.messages("old-slot")

        self.client.get_chat_history_snapshot.side_effect = snapshot
        task = asyncio.create_task(self.c._async_poll_chat())
        await entered.wait()
        self.c.data.current_slot["slot_number"] = 2
        await self.c._async_poll_chat()  # overlapping tick is skipped
        release.set()
        await task
        self.assertEqual(self.c.data.chat_messages, [])
        self.assertEqual(self.client.get_chat_history_snapshot.await_count, 1)

    async def test_own_output_suppressed_but_same_text_from_user_allowed(self):
        await self.poll([])
        text = "Computer Licht aus"
        await self.c.async_send_chat_reply(text, 1)
        await self.poll([
            {"chat_id": "own-1", "role": "user", "content": text},
            {"chat_id": "human", "role": "user", "content": text},
        ])
        self.assertEqual([e[1]["chat_id"] for e in self.events], ["human"])
        self.assertEqual(len(self.c.data.chat_messages), 2)

    async def test_ambiguous_send_suppresses_one_matching_echo(self):
        await self.poll([])
        self.client.send_chat_text.side_effect = api.Code27ConnectionError("timeout")
        with self.assertRaises(api.Code27ConnectionError):
            await self.c.async_send_chat_reply("Computer Licht aus", 1)
        await self.poll([{"chat_id": "accepted", "role": "user", "content": "Computer Licht aus"}])
        self.assertEqual(self.events, [])

    async def test_poll_waits_until_send_id_is_registered(self):
        await self.poll([])
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(*args):
            entered.set()
            await release.wait()
            return "own-1"

        self.client.send_chat_text.side_effect = send
        task = asyncio.create_task(self.c.async_send_chat_reply("Computer own", 1))
        await entered.wait()
        await self.poll(self.messages("own-1"))
        self.assertEqual(self.events, [])
        release.set()
        await task
        await self.poll(self.messages("own-1"))
        self.assertEqual(self.events, [])

    async def test_new_codeword_command_replaces_pending_action(self):
        self.c.pending_correction = coordinator_module.PendingCorrection("Kuche ausschalten", "Kuche")
        forward = AsyncMock(return_value=(None, None, "Buero einschalten"))
        with patch.object(bridge, "_forward_to_assist", forward), patch.object(bridge, "_log_activity"):
            await bridge._handle_bridge_message(
                self.hass, self.entry, self.c, "Computer Buero einschalten", None
            )
        self.assertIsNone(self.c.pending_correction)
        self.assertEqual(forward.call_args.args[3], "Buero einschalten")

    async def test_new_spell_cancels_correction(self):
        self.c.pending_correction = coordinator_module.PendingCorrection("old off", "old")
        self.entry.options["bridge_spellbook"] = "test"
        book = N(name="test")
        match = ("Licht Buero einschalten", None, "lumos", "lumos", True, {}, None)
        with patch.object(spellbook, "async_get_spellbook", AsyncMock(return_value=book)), \
             patch.object(spellbook, "match_spellbook", return_value=match), \
             patch.object(bridge, "_forward_to_assist", AsyncMock(return_value=(None, None, "new"))) as forward, \
             patch.object(bridge, "_log_activity"):
            await bridge._handle_bridge_message(self.hass, self.entry, self.c, "lumos Buero", None)
        self.assertIsNone(self.c.pending_correction)
        self.assertEqual(forward.call_args.args[3], "Licht Buero einschalten")

    async def test_missing_configured_spellbook_warns_instead_of_silent_fallback(self):
        # Second-round audit: a spellbook_id that no longer resolves
        # (removed/renamed file, like the unused star_trek.yaml this
        # was found while removing) used to fail silently and fall
        # straight through to the codeword path with nothing in the
        # log explaining why every spellbook command for this entry
        # stopped working.
        self.entry.options["bridge_spellbook"] = "ghost_book"
        with patch.object(spellbook, "async_get_spellbook", AsyncMock(return_value=None)), \
             patch.object(bridge, "_LOGGER") as logger, \
             patch.object(bridge, "_log_activity"):
            await bridge._handle_bridge_message(self.hass, self.entry, self.c, "Computer test", None)
        self.assertTrue(logger.warning.called)
        self.assertTrue(
            any("ghost_book" in call.args for call in logger.warning.call_args_list),
            f"expected a warning mentioning 'ghost_book', got: {logger.warning.call_args_list}",
        )

    async def test_unrelated_sentence_is_not_a_correction_candidate(self):
        self.c.pending_correction = coordinator_module.PendingCorrection("Kuche ausschalten", "Kuche")
        with patch.object(spellbook, "_registry_names", return_value=["Buero"]), \
             patch.object(bridge, "_retry_with_candidate", AsyncMock()) as retry, \
             patch.object(bridge, "_output_speech", AsyncMock()):
            await bridge._handle_correction_reply(self.hass, self.entry, self.c, "Buero einschalten", None)
        retry.assert_not_awaited()

    async def test_name_wrapper_and_abort_still_work(self):
        self.c.pending_correction = coordinator_module.PendingCorrection("Kuche ausschalten", "Kuche")
        with patch.object(spellbook, "_registry_names", return_value=["Buero"]), \
             patch.object(bridge, "_retry_with_candidate", AsyncMock()) as retry:
            await bridge._handle_correction_reply(self.hass, self.entry, self.c, "ich meinte Buero", None)
            retry.assert_awaited_once()
        with patch.object(bridge, "_log_activity"):
            await bridge._handle_correction_reply(self.hass, self.entry, self.c, "Abbruch", None)
        self.assertIsNone(self.c.pending_correction)

    async def test_unload_cancels_ai_before_assist(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def cleanup(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch.object(bridge, "_ai_cleanup", cleanup), \
             patch.object(bridge, "_ask_assist", AsyncMock()) as assist:
            self.c.async_create_owned_task(
                bridge._forward_to_assist(self.hass, self.entry, self.c, "test", None), "test"
            )
            await entered.wait()
            await self.c.async_stop_realtime()
            self.assertTrue(cancelled.is_set())
            assist.assert_not_awaited()
            await self.c.async_stop_realtime()  # cleanup is idempotent
            self.assertFalse(self.c._owned_tasks)

    async def test_unload_cancels_inflight_poll(self):
        entered = asyncio.Event()

        async def snapshot(*args):
            entered.set()
            await asyncio.Event().wait()

        self.client.get_chat_history_snapshot.side_effect = snapshot
        task = asyncio.create_task(self.c._async_poll_chat())
        await entered.wait()
        await self.c.async_stop_realtime()
        self.assertTrue(task.cancelled())
        self.assertEqual(self.events, [])

    async def test_device_queues_are_independent(self):
        other = coordinator_module.Code27Coordinator(self.hass, N(entry_id="b"), self.client, 15)
        other.data = coordinator_module.Code27Data(current_slot={"slot_number": 1})
        entered, release = asyncio.Event(), asyncio.Event()

        async def handle(hass, entry, coordinator, *args):
            if coordinator is self.c:
                entered.set()
                await release.wait()

        with patch.object(bridge, "_handle_bridge_message", handle):
            task = asyncio.create_task(bridge._process_bridge_message(self.hass, self.entry, self.c, "a", None))
            await entered.wait()
            await asyncio.wait_for(bridge._process_bridge_message(self.hass, self.entry, other, "b", None), 1)
            release.set()
            await task

    async def test_same_device_requests_are_serialized(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def handle(hass, entry, coordinator, text, entity_id):
            calls.append(text)
            if text == "first":
                entered.set()
                await release.wait()

        with patch.object(bridge, "_handle_bridge_message", handle):
            first = asyncio.create_task(bridge._process_bridge_message(self.hass, self.entry, self.c, "first", None))
            await entered.wait()
            second = asyncio.create_task(bridge._process_bridge_message(self.hass, self.entry, self.c, "second", None))
            await asyncio.sleep(0)
            self.assertEqual(calls, ["first"])
            release.set()
            await asyncio.gather(first, second)
            self.assertEqual(calls, ["first", "second"])

    async def test_event_listener_filters_device_and_tracks_work(self):
        listeners, cleanups = {}, []
        self.entry.async_on_unload = cleanups.append
        self.hass.bus.async_listen = lambda kind, handler: listeners.setdefault(kind, handler) and (lambda: None)
        entered = asyncio.Event()

        async def process(*args):
            entered.set()
            await asyncio.Event().wait()

        with patch.object(bridge, "_bridge_entity_id", return_value=None), \
             patch.object(bridge, "_process_bridge_message", process):
            bridge.async_setup_bridge(self.hass, self.entry, self.c)
            handler = listeners[coordinator_module.EVENT_USER_MESSAGE]
            handler(N(data={"config_entry_id": "other", "text": "Computer test"}))
            self.assertFalse(self.c._owned_tasks)
            handler(N(data={"config_entry_id": "device-a", "text": "Computer test"}))
            await entered.wait()
            self.assertEqual(len(self.c._owned_tasks), 1)
            await self.c.async_stop_realtime()
            handler(N(data={"config_entry_id": "device-a", "text": "Computer test"}))
            self.assertFalse(self.c._owned_tasks)

    async def test_stop_waits_for_websocket_cleanup(self):
        websocket = load_source("websocket_client", **constants)
        ws = websocket.Code27WebSocket(self.hass, self.client)
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def connection():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        ws._task = asyncio.create_task(connection())
        await entered.wait()
        await ws.stop()
        self.assertTrue(cleaned.is_set())
        self.assertIsNone(ws._task)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_burst_outside_latest_window_recovers_full_day(self):
        client = api.Code27Client(None, "device", 8081)
        client.get_chat_history_dates = AsyncMock(return_value=["2026-09-09"])
        recent = [{"chat_id": str(i)} for i in range(21, 51)]
        full = [{"chat_id": str(i)} for i in range(51)]
        client._call = AsyncMock(side_effect=[{"data": recent}, {"data": full}])
        _, messages = await client.get_chat_history_snapshot(1, "2026-09-09", "0")
        self.assertEqual(len(messages), 51)
        self.assertIn("/chat/history?", client._call.call_args.args[1])

    async def test_midnight_includes_previous_day(self):
        client = api.Code27Client(None, "device", 8081)
        client.get_chat_history_dates = AsyncMock(return_value=["2026-09-09", "2026-09-10"])
        client._call = AsyncMock(side_effect=[{"data": [{"chat_id": "late"}]}, {"data": [{"chat_id": "new"}]}])
        date, messages = await client.get_chat_history_snapshot(1, "2026-09-09", "old")
        self.assertEqual(date, "2026-09-10")
        self.assertEqual([m["chat_id"] for m in messages], ["late", "new"])

    async def test_send_returns_server_message_id(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={"success": True, "chat_id": "sent"})
        self.assertEqual(await client.send_chat_text("text", 2), "sent")

    async def test_normal_poll_keeps_small_window(self):
        client = api.Code27Client(None, "device", 8081)
        client.get_chat_history_dates = AsyncMock(return_value=["2026-09-09"])
        client._call = AsyncMock(return_value={"data": [{"chat_id": "old"}, {"chat_id": "new"}]})
        _, messages = await client.get_chat_history_snapshot(1, "2026-09-09", "old")
        self.assertEqual(len(messages), 2)
        client._call.assert_awaited_once()
        self.assertIn("/latest?", client._call.call_args.args[1])

    async def test_initially_empty_history_recovers_multiple_new_days(self):
        client = api.Code27Client(None, "device", 8081)
        client.get_chat_history_dates = AsyncMock(return_value=["2026-09-09", "2026-09-10"])
        client._call = AsyncMock(side_effect=[{"data": [{"chat_id": "first-day"}]}, {"data": [{"chat_id": "second-day"}]}])
        _, messages = await client.get_chat_history_snapshot(1, "")
        self.assertEqual(len(messages), 2)

    async def test_send_chat_text_omits_language_by_default(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={"success": True, "chat_id": "sent"})
        await client.send_chat_text("hi", 2)
        _, _, kwargs = client._call.mock_calls[0]
        self.assertNotIn("language", kwargs["body"])

    async def test_send_chat_text_includes_language_when_given(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={"success": True, "chat_id": "sent"})
        await client.send_chat_text("hi", 2, "de")
        _, _, kwargs = client._call.mock_calls[0]
        self.assertEqual(kwargs["body"]["language"], "de")

    async def test_music_control_raises_on_device_reported_failure(self):
        # Live-confirmed device response shape: HTTP 200, envelope
        # success:true, but the action itself failed - PLAY/SET_VOLUME both
        # return this when the device's music screen isn't open.
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={
            "success": True,
            "data": {"status": "FAILED", "error_code": "MODE_INACTIVE",
                      "error_message": "music mode inactive"},
        })
        with self.assertRaises(api.Code27DeviceError) as ctx:
            await client.music_control("PLAY")
        self.assertEqual(ctx.exception.error_code, "MODE_INACTIVE")

    async def test_music_control_returns_data_on_success(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(
            return_value={"success": True, "data": {"status": "PLAYING"}}
        )
        result = await client.music_control("PLAY")
        self.assertEqual(result, {"status": "PLAYING"})

    def test_parse_json_response_raises_slot_mismatch(self):
        client = api.Code27Client(None, "device", 8081)
        body = json.dumps({
            "error": "slot does not match current slot",
            "current_slot": 1, "request_slot": 2,
        })
        with self.assertRaises(api.Code27SlotMismatchError) as ctx:
            client._parse_json_response(409, body)
        self.assertEqual(ctx.exception.current_slot, 1)
        self.assertEqual(ctx.exception.request_slot, 2)

    def test_parse_json_response_uses_error_field_as_message(self):
        # Previously fell through to the opaque "http_400" - the device's
        # actual error text ("error" key, not "message"/"code") was dropped.
        client = api.Code27Client(None, "device", 8081)
        body = json.dumps({"error": "image must be 1MB or smaller"})
        with self.assertRaises(api.Code27Error) as ctx:
            client._parse_json_response(400, body)
        self.assertEqual(str(ctx.exception), "image must be 1MB or smaller")

    async def test_call_multipart_signs_with_unsigned_payload_sentinel(self):
        client = api.Code27Client(None, "device", 8081, user_id="u1", local_secret="s3cr3t")
        captured = {}

        async def fake_request(method, url, data=None, headers=None):
            captured["method"], captured["url"] = method, url
            captured["data"], captured["headers"] = data, headers
            return N(status=200, text=AsyncMock(
                return_value=json.dumps({"success": True, "chat_id": "x"})
            ))

        client._session = N(request=fake_request)
        result = await client._call_multipart(
            "POST", "/data/user/chat/image-text",
            fields={"text": "hi", "slot": "2"},
            files=[("image", b"bytes", "image.jpg", "image/jpeg")],
        )
        self.assertEqual(result["chat_id"], "x")
        headers = captured["headers"]
        self.assertEqual(headers["X-Local-Payload-Mode"], "multipart")
        self.assertEqual(headers["X-Local-Body-Hash"], "UNSIGNED-PAYLOAD")
        self.assertIsInstance(captured["data"], _FakeFormData)
        self.assertIn(("image", b"bytes", "image.jpg", "image/jpeg"), captured["data"].fields)

        canonical = "|".join([
            "POST", "/v1/api/data/user/chat/image-text", headers["X-Local-Timestamp"],
            headers["X-Local-Nonce"], "multipart", "UNSIGNED-PAYLOAD", "u1",
        ])
        expected_sig = hmac.new(b"s3cr3t", canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        self.assertEqual(headers["X-Local-Sig"], expected_sig)

    async def test_call_binary_returns_content_type_and_bytes(self):
        client = api.Code27Client(None, "device", 8081, user_id="u1", local_secret="s3cr3t")
        client._session = N(request=AsyncMock(
            return_value=N(status=200, content_type="image/png", read=AsyncMock(return_value=b"\x89PNG"))
        ))
        content_type, raw = await client._call_binary("/resources/user/assets/thumbnails/1_2_x.png")
        self.assertEqual(content_type, "image/png")
        self.assertEqual(raw, b"\x89PNG")

    async def test_call_binary_raises_on_error_envelope(self):
        client = api.Code27Client(None, "device", 8081, user_id="u1", local_secret="s3cr3t")
        client._session = N(request=AsyncMock(return_value=N(
            status=404, content_type="application/json",
            read=AsyncMock(return_value=b'{"error":"page not found"}'),
        )))
        with self.assertRaises(api.Code27Error):
            await client._call_binary("/resources/user/chat-images?uuid=missing")

    async def test_get_chat_history_count_returns_data_list(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={
            "success": True,
            "data": [{"slot": 1, "slot_id": "Lilya", "messages": [{"content": "hi"}]}],
        })
        result = await client.get_chat_history_count()
        self.assertEqual(result[0]["slot_id"], "Lilya")
        self.assertIn("/chat/history/count", client._call.call_args.args[1])

    async def test_get_chat_history_count_accepts_real_device_response_shape(self):
        # Regression test: this goes through the REAL _call() (not a mocked
        # one), so it also exercises _validate_response_data - which once
        # rejected this exact, live-confirmed shape as "unexpected data
        # shape" because /chat/history/count wasn't in the shared
        # list-vs-dict allowlist, breaking setup against a real device.
        client = api.Code27Client(None, "device", 8081, user_id="u1", local_secret="s3cr3t")
        body = json.dumps({
            "success": True,
            "data": [{"slot": 1, "slot_id": "Lilya", "messages": [{"content": "hi"}]}],
        })
        client._session = N(request=AsyncMock(
            return_value=N(status=200, text=AsyncMock(return_value=body))
        ))
        result = await client.get_chat_history_count()
        self.assertEqual(result, [{"slot": 1, "slot_id": "Lilya", "messages": [{"content": "hi"}]}])

    async def test_send_chat_image_rejects_oversized_image_without_calling_device(self):
        client = api.Code27Client(None, "device", 8081)
        client._call_multipart = AsyncMock()
        with self.assertRaises(api.Code27Error):
            await client.send_chat_image(b"x" * (client._MAX_CHAT_IMAGE_BYTES + 1), "a.jpg", "image/jpeg")
        client._call_multipart.assert_not_awaited()

    async def test_send_chat_image_sends_multipart_fields_and_file(self):
        client = api.Code27Client(None, "device", 8081)
        client._call_multipart = AsyncMock(
            return_value={"chat_id": "c1", "image_chat_id": "c2", "success": True}
        )
        chat_id, image_chat_id = await client.send_chat_image(
            b"bytes", "a.jpg", "image/jpeg", text="hi", slot=2, language="de"
        )
        self.assertEqual((chat_id, image_chat_id), ("c1", "c2"))
        _, _, kwargs = client._call_multipart.mock_calls[0]
        self.assertEqual(kwargs["fields"], {"text": "hi", "slot": "2", "language": "de"})
        self.assertEqual(kwargs["files"], [("image", b"bytes", "a.jpg", "image/jpeg")])

    async def test_get_chat_image_calls_binary_endpoint_with_uuid(self):
        client = api.Code27Client(None, "device", 8081)
        client._call_binary = AsyncMock(return_value=("image/jpeg", b"data"))
        result = await client.get_chat_image("abc-123")
        self.assertEqual(result, ("image/jpeg", b"data"))
        self.assertIn("uuid=abc-123", client._call_binary.call_args.args[0])

    async def test_get_character_portrait_prefers_custom_thumbnail(self):
        client = api.Code27Client(None, "device", 8081)
        client.get_cloth_uploaded_models = AsyncMock(return_value=[
            {"slot_number": 1, "cloth_id": 2, "filename": "Lylia.vrm"},
        ])
        client._call_binary = AsyncMock(return_value=("image/png", b"portrait"))
        result = await client.get_character_portrait(1, 2)
        self.assertEqual(result, ("image/png", b"portrait"))
        # extension must be stripped before building the thumbnail path
        self.assertIn("1_2_Lylia.png", client._call_binary.call_args.args[0])

    async def test_original_portraits_manifest_shape_is_not_enforced(self):
        # Its real shape isn't confirmed by the audit - accept whatever
        # comes back (here: a list) rather than guessing dict-vs-list and
        # risking the same class of bug as history/count.
        client = api.Code27Client(None, "device", 8081, user_id="u1", local_secret="s3cr3t")
        body = json.dumps({"success": True, "data": [{"slot": 1, "cloth": 1}]})
        client._session = N(request=AsyncMock(
            return_value=N(status=200, text=AsyncMock(return_value=body))
        ))
        result = await client.get_original_portraits_manifest()
        self.assertEqual(result, [{"slot": 1, "cloth": 1}])

    async def test_get_character_portrait_falls_back_to_stock_portrait(self):
        client = api.Code27Client(None, "device", 8081)
        client.get_cloth_uploaded_models = AsyncMock(return_value=[])
        client._call_binary = AsyncMock(return_value=("image/png", b"stock"))
        result = await client.get_character_portrait(3, 1)
        self.assertEqual(result, ("image/png", b"stock"))
        self.assertIn("original-portraits/3_1.png", client._call_binary.call_args.args[0])

    async def test_get_character_portrait_returns_none_when_nothing_exists(self):
        client = api.Code27Client(None, "device", 8081)
        client.get_cloth_uploaded_models = AsyncMock(return_value=[])
        client._call_binary = AsyncMock(side_effect=api.Code27Error("http_404"))
        self.assertIsNone(await client.get_character_portrait(4, 1))

    async def test_update_memory_card_requires_content_or_category(self):
        client = api.Code27Client(None, "device", 8081)
        with self.assertRaises(api.Code27Error):
            await client.update_memory_card("card1", "char1")

    async def test_update_memory_card_sends_only_given_fields(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={"success": True, "data": {"content": "new"}})
        await client.update_memory_card("card1", "char1", content="new")
        _, _, kwargs = client._call.mock_calls[0]
        self.assertEqual(kwargs["body"], {"characterId": "char1", "content": "new"})

    async def test_list_diary_entries_requires_both_slot_and_character(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={"success": True, "data": {"entries": []}})
        await client.list_diary_entries("char1", 2)
        self.assertIn("characterId=char1&slot=2", client._call.call_args.args[1])

    async def test_upload_music_repeats_files_field_for_each_file(self):
        client = api.Code27Client(None, "device", 8081)
        client._call_multipart = AsyncMock(
            return_value={"success": True, "data": {"uploaded": [], "rejected": []}}
        )
        await client.upload_music([
            (b"a", "a.mp3", "audio/mpeg"),
            (b"b", "b.wav", "audio/wav"),
        ])
        _, _, kwargs = client._call_multipart.mock_calls[0]
        self.assertEqual(kwargs["files"], [
            ("files", b"a", "a.mp3", "audio/mpeg"),
            ("files", b"b", "b.wav", "audio/wav"),
        ])

    async def test_delete_music_file_urlencodes_path(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={"success": True})
        await client.delete_music_file("Rockkapella/song one.mp3")
        self.assertIn(
            "path=Rockkapella/song%20one.mp3", client._call.call_args.args[1]
        )

    async def test_update_character_setting_rejects_unknown_fields(self):
        client = api.Code27Client(None, "device", 8081)
        with self.assertRaises(api.Code27Error):
            await client.update_character_setting(2, "machine-1", not_a_real_field="x")

    async def test_call_remote_system_info_uses_remote_host_and_device_id_header(self):
        client = api.Code27Client(None, "device", 8081, user_id="u1", local_secret="s3cr3t", machine_id="mach-1")
        captured = {}

        async def fake_request(method, url, headers=None, **kwargs):
            captured["method"], captured["url"], captured["headers"] = method, url, headers
            return N(status=200, text=AsyncMock(
                return_value=json.dumps({"success": True, "data": {"local_ip": "10.0.0.9", "machine_id": "mach-1"}})
            ))

        client._session = N(request=fake_request)
        data = await client._call_remote_system_info()
        self.assertEqual(data, {"local_ip": "10.0.0.9", "machine_id": "mach-1"})
        self.assertTrue(captured["url"].startswith("https://codie-remote.prod.code27.co/v1/api/data/user/system"))
        self.assertEqual(captured["headers"]["X-DEVICE-ID"], "mach-1")
        self.assertIn("X-Local-Sig", captured["headers"])

    async def test_attempt_remote_recovery_success_updates_host(self):
        client = api.Code27Client(None, "old-host", 8081, user_id="u", local_secret="s", machine_id="m1")
        client._call_remote_system_info = AsyncMock(return_value={"local_ip": "10.0.0.5", "machine_id": "m1"})
        client._call = AsyncMock(return_value={"success": True, "data": {"machine_id": "m1"}})
        new_host = await client.attempt_remote_recovery()
        self.assertEqual(new_host, "10.0.0.5")
        self.assertEqual(client.host, "10.0.0.5")

    async def test_attempt_remote_recovery_without_machine_id_is_a_noop(self):
        client = api.Code27Client(None, "old-host", 8081, user_id="u", local_secret="s")
        client._call_remote_system_info = AsyncMock()
        self.assertIsNone(await client.attempt_remote_recovery())
        client._call_remote_system_info.assert_not_awaited()

    async def test_attempt_remote_recovery_rejects_machine_id_mismatch(self):
        client = api.Code27Client(None, "old-host", 8081, user_id="u", local_secret="s", machine_id="m1")
        client._call_remote_system_info = AsyncMock(
            return_value={"local_ip": "10.0.0.5", "machine_id": "someone-elses-device"}
        )
        self.assertIsNone(await client.attempt_remote_recovery())
        self.assertEqual(client.host, "old-host")

    async def test_attempt_remote_recovery_reverts_host_when_relay_unreachable(self):
        client = api.Code27Client(None, "old-host", 8081, user_id="u", local_secret="s", machine_id="m1")
        client._call_remote_system_info = AsyncMock(side_effect=api.Code27ConnectionError("relay down"))
        self.assertIsNone(await client.attempt_remote_recovery())
        self.assertEqual(client.host, "old-host")

    async def test_attempt_remote_recovery_reverts_host_when_new_address_unreachable(self):
        client = api.Code27Client(None, "old-host", 8081, user_id="u", local_secret="s", machine_id="m1")
        client._call_remote_system_info = AsyncMock(return_value={"local_ip": "10.0.0.5", "machine_id": "m1"})
        client._call = AsyncMock(side_effect=api.Code27ConnectionError("still unreachable"))
        self.assertIsNone(await client.attempt_remote_recovery())
        self.assertEqual(client.host, "old-host")

    async def test_attempt_remote_recovery_reverts_host_when_new_address_is_different_device(self):
        client = api.Code27Client(None, "old-host", 8081, user_id="u", local_secret="s", machine_id="m1")
        client._call_remote_system_info = AsyncMock(return_value={"local_ip": "10.0.0.5", "machine_id": "m1"})
        client._call = AsyncMock(return_value={"success": True, "data": {"machine_id": "not-m1"}})
        self.assertIsNone(await client.attempt_remote_recovery())
        self.assertEqual(client.host, "old-host")

    async def test_attempt_remote_recovery_respects_cooldown(self):
        client = api.Code27Client(None, "old-host", 8081, user_id="u", local_secret="s", machine_id="m1")
        client._call_remote_system_info = AsyncMock(return_value={"local_ip": "10.0.0.5", "machine_id": "m1"})
        client._call = AsyncMock(side_effect=api.Code27ConnectionError("still unreachable"))
        self.assertIsNone(await client.attempt_remote_recovery())
        client._call_remote_system_info.reset_mock()
        # A failed attempt does NOT reset the cooldown - a second attempt
        # right away must not hit the relay again ("einmaliger GET" per
        # cooldown window, not an unbounded retry loop).
        self.assertIsNone(await client.attempt_remote_recovery())
        client._call_remote_system_info.assert_not_awaited()

    async def test_update_character_setting_sends_slot_and_device_id(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={"success": True, "data": {"voice_id": "1"}})
        await client.update_character_setting(2, "machine-1", voice_id="1", group_key="g")
        _, _, kwargs = client._call.mock_calls[0]
        self.assertEqual(
            kwargs["body"],
            {"slot_number": 2, "device_id": "machine-1", "voice_id": "1", "group_key": "g"},
        )


class MediaPlayerTests(unittest.IsolatedAsyncioTestCase):
    def _entity(self, music_storage=None, client=None):
        # Bypasses __init__ (its super().__init__ chain expects the real
        # Code27Entity/CoordinatorEntity, not the empty test doubles this
        # harness loads media_player.py with) - sets just what the methods
        # under test actually read.
        entity = media_player.Code27MediaPlayer.__new__(media_player.Code27MediaPlayer)
        entity.coordinator = N(
            data=N(music_storage=music_storage or {}),
            client=client or N(),
            async_request_refresh=AsyncMock(),
        )
        return entity

    def test_group_music_files_splits_root_and_subfolder(self):
        files = [
            {"path": "song.mp3", "name": "song.mp3"},
            {"path": "Rockkapella/a.mp3", "name": "a.mp3"},
            {"path": "Rockkapella/b.mp3", "name": "b.mp3"},
        ]
        folders, root_files = media_player._group_music_files(files)
        self.assertEqual(folders, ["Rockkapella"])
        self.assertEqual(root_files, [files[0]])

    def test_files_in_folder_filters_by_prefix_not_substring(self):
        files = [
            {"path": "song.mp3"},
            {"path": "Rockkapella/a.mp3"},
            {"path": "RockkapellaExtra/c.mp3"},
        ]
        result = media_player._files_in_folder(files, "Rockkapella")
        self.assertEqual([f["path"] for f in result], ["Rockkapella/a.mp3"])

    def test_track_browse_item_uses_track_id_format(self):
        item = media_player._track_browse_item({"path": "Rockkapella/a.mp3", "name": "A Song"})
        self.assertEqual(item.media_content_id, "local:Rockkapella/a.mp3")
        self.assertEqual(item.title, "A Song")
        self.assertTrue(item.can_play)
        self.assertFalse(item.can_expand)

    def test_track_browse_item_falls_back_to_path_basename_without_name(self):
        item = media_player._track_browse_item({"path": "Rockkapella/a.mp3"})
        self.assertEqual(item.title, "a.mp3")

    async def test_browse_media_root_lists_folders_then_root_files(self):
        entity = self._entity(music_storage={"music_files": [
            {"path": "song.mp3", "name": "song.mp3"},
            {"path": "Rockkapella/a.mp3", "name": "a.mp3"},
        ]})
        result = await entity.async_browse_media()
        titles = [c.title for c in result.children]
        self.assertEqual(titles, ["Rockkapella", "song.mp3"])
        self.assertEqual(result.children[0].media_content_id, "folder:Rockkapella")
        self.assertFalse(result.children[0].can_play)
        self.assertTrue(result.children[1].can_play)

    async def test_browse_media_folder_lists_only_its_own_files(self):
        entity = self._entity(music_storage={"music_files": [
            {"path": "song.mp3", "name": "song.mp3"},
            {"path": "Rockkapella/a.mp3", "name": "a.mp3"},
            {"path": "Rockkapella/b.mp3", "name": "b.mp3"},
        ]})
        result = await entity.async_browse_media(media_content_id="folder:Rockkapella")
        self.assertEqual([c.title for c in result.children], ["a.mp3", "b.mp3"])

    async def test_browse_media_rejects_unknown_content_id(self):
        entity = self._entity()
        with self.assertRaises(RuntimeError):
            await entity.async_browse_media(media_content_id="bogus")

    async def test_play_media_sends_track_id_via_music_control(self):
        client = N(music_control=AsyncMock(return_value={"status": "PLAYING"}))
        entity = self._entity(client=client)
        await entity.async_play_media("music", "local:Rockkapella/a.mp3")
        client.music_control.assert_awaited_once_with("PLAY", track_id="local:Rockkapella/a.mp3")
        entity.coordinator.async_request_refresh.assert_awaited_once()

    async def test_play_media_translates_mode_inactive_error(self):
        client = N(music_control=AsyncMock(
            side_effect=api.Code27DeviceError("MODE_INACTIVE", "music mode inactive")
        ))
        entity = self._entity(client=client)
        with self.assertRaises(RuntimeError):
            await entity.async_play_media("music", "local:x.mp3")


if __name__ == "__main__":
    unittest.main()
