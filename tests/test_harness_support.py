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
import time
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "hase"


def load_source(name, **adapters):
    provider = ROOT / "providers" / "code27"
    mcp_provider = ROOT / "providers" / "mcp"
    source_path = ROOT / f"{name}.py"
    if name in {"api", "coordinator", "websocket_client", "entity", "media_player", "speech"}:
        source_path = provider / f"{name}.py"
    paths = [source_path]
    if name == "const":
        paths = [ROOT / "const.py", provider / "const.py"]
    elif name == "__init__":
        paths = [provider / "services.py", provider / "__init__.py", source_path]
    elif name == "config_flow":
        # Root config_flow.py also composes providers/mcp/config_flow.py's
        # McpFlowMixin/McpBridgeSubentryFlow/McpServerOptionsFlow/
        # async_mcp_server_exists (see HaseConfigFlow's own class
        # statement) - concatenated here the same way providers/code27's
        # own config_flow.py already is, so those names are defined in the
        # exec'd module instead of raising NameError once their (relative,
        # stripped) import line is removed below.
        paths = [provider / "duplicate.py", provider / "config_flow.py", mcp_provider / "config_flow.py", source_path]
    nodes = [node for path in paths for node in ast.parse(path.read_text(encoding="utf-8")).body]
    nodes = [n for n in nodes if not (isinstance(n, ast.ImportFrom) and n.module == "__future__")]
    tree = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)] + nodes, type_ignores=[])

    class _StripHarnessImports(ast.NodeTransformer):
        """Remove imports supplied by adapters.

        Most harness-loaded modules historically only need their top-level
        imports stripped.  conversation.py is special: optional CODE27
        dependencies are deliberately imported lazily inside methods so that
        the generic HASE core can be imported without the CODE27 provider.
        """

        def visit_ImportFrom(self, node):
            if node.module == "__future__":
                return node
            if node.level or (node.module or "").startswith("homeassistant"):
                return ast.copy_location(ast.Pass(), node)
            return node

        def visit_Import(self, node):
            if any(alias.name in {"aiohttp", "yaml", "voluptuous"} for alias in node.names):
                return ast.copy_location(ast.Pass(), node)
            return node

    if name == "conversation":
        # conversation.py intentionally contains lazy provider imports inside
        # methods.  Strip recursively so the lightweight harness can inject
        # its CODE27 adapters instead.
        tree = _StripHarnessImports().visit(tree)
    else:
        # Preserve the historical harness behaviour for every other module:
        # strip only imports that occur at module level.  Imports inside
        # functions must remain intact (e.g. HA device/entity/area registry
        # imports used at runtime).
        stripped_body = []
        stripper = _StripHarnessImports()
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                replacement = stripper.visit(node)
                if replacement is not None:
                    stripped_body.append(replacement)
            else:
                stripped_body.append(node)
        tree.body = stripped_body

    ast.fix_missing_locations(tree)

    # The provider descriptor is wiring, not behavior under test in this
    # lightweight concatenated harness.  It references provider config-flow
    # classes that deliberately are not loaded for the lifecycle tests below.
    if name == "__init__":
        tree.body = [
            node for node in tree.body
            if not (
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id == "PROVIDER"
                    for target in (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                )
            )
        ]
    module = types.ModuleType(f"code27_test_{name}")
    module.__file__ = str(source_path)
    adapters.setdefault("ProviderConfigFlow", lambda **kwargs: N(**kwargs))
    adapters.setdefault("ProviderDescriptor", lambda **kwargs: N(**kwargs))

    if name in {"__init__", "config_flow"} and "get_provider_registry" not in adapters:
        async def _fake_code27_setup_entry(hass, entry):
            if entry.data.get("entry_kind") == "spellbooker_satellite":
                error_type = module.__dict__.get("ConfigEntryError", RuntimeError)
                raise error_type(
                    "Legacy satellite entries are no longer supported as top-level entries."
                )
            return await module.async_setup_code27_entry(hass, entry)

        async def _fake_code27_unload_entry(hass, entry):
            if entry.data.get("entry_kind") == "spellbooker_satellite":
                return await hass.config_entries.async_unload_platforms(
                    entry, ("conversation",)
                )
            return await module.async_unload_code27_entry(hass, entry)

        def _fake_code27_provider():
            return N(
                id="code27",
                async_setup_entry=_fake_code27_setup_entry,
                async_unload_entry=_fake_code27_unload_entry,
            )

        class FakeProviderRegistry:
            @property
            def providers(self):
                if name == "config_flow":
                    code_config = N(
                        step_id="code27_device",
                        flow_mixin=module.Code27FlowMixin,
                        is_available=lambda hass: True,
                        options_flow_factory=module.Code27OptionsFlow,
                        subentry_flows=(("satellite", module.SatelliteSubentryFlow),),
                    )
                    mcp_config = N(
                        step_id="mcp_server",
                        flow_mixin=module.McpFlowMixin,
                        is_available=lambda hass: not module.async_mcp_server_exists(hass),
                        options_flow_factory=module.McpServerOptionsFlow,
                        subentry_flows=(("mcp_bridge", module.McpBridgeSubentryFlow),),
                    )
                    return (
                        N(id="code27", config_flow=code_config),
                        N(id="mcp", config_flow=mcp_config),
                    )
                return (_fake_code27_provider(),)

            def resolve_entry(self, entry):
                kind = entry.data.get("entry_kind")
                if name == "config_flow":
                    return next(
                        (
                            provider
                            for provider in self.providers
                            if provider.config_flow.step_id == kind
                            or (kind is None and provider.id == "code27" and "host" in entry.data)
                        ),
                        None,
                    )
                if kind in {"code27_device", "spellbooker_satellite"} or (
                    kind is None and "host" in entry.data
                ):
                    return _fake_code27_provider()
                return None

        fake_registry = FakeProviderRegistry()

        def get_provider_registry(hass=None):
            if hass is not None and hasattr(hass, "data"):
                hass.data.setdefault("hase", {}).setdefault(
                    "provider_registry", fake_registry
                )
            return fake_registry

        adapters["get_provider_registry"] = get_provider_registry
    module.__dict__.update(adapters)
    sys.modules[module.__name__] = module
    exec(compile(tree, module.__file__, "exec"), module.__dict__)  # noqa: S102 - trusted repository source in isolated test harness.
    return module


N = types.SimpleNamespace
const = load_source("const")
constants = {key: value for key, value in vars(const).items() if key.isupper()}


class _FakeFormData:
    """Stand-in for aiohttp.FormData - records what would have been sent
    without needing a real aiohttp install in this lightweight harness."""

    def __init__(self, quote_fields: bool = True) -> None:
        self.fields: list[tuple] = []
        self.quote_fields = quote_fields

    def add_field(self, name, value, filename=None, content_type=None):
        self.fields.append((name, value, filename, content_type))


api = load_source("api", aiohttp=N(ClientError=OSError, FormData=_FakeFormData))


class _FakeBrowseMedia:
    """Stand-in for homeassistant.components.media_player.BrowseMedia -
    just records whatever kwargs it was built with."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _load_code27_music():
    """Load the real, Home-Assistant-free CODE27 music modules.

    ``providers/code27/music.py`` owns the music browser tree and the
    device-error text; ``media_player.py`` delegates to it, so the tests below
    exercise the real implementation rather than a stand-in. Both modules are
    pure Python, so they load without any of the adapters the AST harness
    needs - only their sibling package context has to be provided.
    """
    import importlib.util

    provider = ROOT / "providers" / "code27"
    package = types.ModuleType("code27_music_test")
    package.__path__ = [str(provider)]
    sys.modules["code27_music_test"] = package
    for module_name in ("music_model", "music"):
        spec = importlib.util.spec_from_file_location(
            f"code27_music_test.{module_name}", provider / f"{module_name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"code27_music_test.{module_name}"] = module
        spec.loader.exec_module(module)
    return sys.modules["code27_music_test.music"]


code27_music = _load_code27_music()

media_player = load_source(
    "media_player", async_get_music_runtime=lambda hass: None, **constants,
    MediaPlayerEntity=type("MediaPlayerEntity", (), {}),
    Code27Entity=type("Code27Entity", (), {}),
    MediaPlayerEntityFeature=N(
        PLAY=1, PAUSE=2, NEXT_TRACK=4, PREVIOUS_TRACK=8, VOLUME_SET=16,
        SELECT_SOURCE=32, PLAY_MEDIA=64, BROWSE_MEDIA=128, SEEK=256,
    ),
    MediaPlayerState=N(PLAYING="playing", PAUSED="paused", IDLE="idle"),
    MediaClass=N(DIRECTORY="directory", MUSIC="music"),
    MediaType=N(MUSIC="music"),
    BrowseMedia=_FakeBrowseMedia,
    HomeAssistantError=RuntimeError,
    Code27DeviceError=api.Code27DeviceError,
    build_browse_entries=code27_music.build_browse_entries,
    control_error_message=code27_music.control_error_message,
    group_music_files=code27_music.group_music_files,
    music_files_in_folder=code27_music.music_files_in_folder,
    track_from_music_file=code27_music.track_from_music_file,
)

# The live payload shape, taken from the captured device response recorded in
# docs/code27/implemented-api.yaml: `root_path` is the TF card's mount point
# and every `music_files[].path` is absolute below it. The tests used to use
# only relative paths, which is exactly why they could not see the browser
# defect (audit CCF-2 / TD-14).
LIVE_STORAGE = {
    "is_loaded": True,
    "root_path": "/storage/142E2DF92E2DD4900",
    "capacity_total_bytes": 127848673280,
    "music_files": [
        {
            "name": "top.mp3",
            "path": (
                "/storage/142E2DF92E2DD4900/code27/music/top.mp3"
            ),
            "duration_ms": 1000,
        },
        {
            "name": "03 - Stand By Me.mp3",
            "path": (
                "/storage/142E2DF92E2DD4900/code27/music/Rockkapella/"
                "03 - Stand By Me.mp3"
            ),
            "duration_ms": 210000,
        },
        {
            "name": "live.mp3",
            "path": (
                "/storage/142E2DF92E2DD4900/code27/music/Rockkapella/Live/"
                "live.mp3"
            ),
            "duration_ms": 5000,
        },
    ],
}


#: The track `LIVE_STORAGE`'s first root file is played as - the device's
#: `track_id` is `local:<absolute path>`.
TRACK_ID = "local:/storage/142E2DF92E2DD4900/code27/music/top.mp3"


def _playback(**overrides):
    """One `music_playback` reading, in the live payload shape.

    `updated_at_ms`/`version` are the device's own revision markers, not a
    "read at" clock: the captured `music_page` payload reports `0`/`0` until
    something actually happens, which is what lets a re-delivered snapshot be
    recognised as *not* a new reading.
    """
    payload = {
        "status": "PLAYING",
        "track_id": TRACK_ID,
        "track_name": "top.mp3",
        "duration_ms": 210000,
        "position_ms": 5000,
        "is_playing": True,
        "volume_level": 36,
        "volume_max": 100,
        "play_mode": "sequence",
        "updated_at_ms": 1790173365727,
        "version": 1,
    }
    payload.update(overrides)
    return payload


class CoordinatorBase:
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, *args, **kwargs):
        self.hass = hass
        # Matches real DataUpdateCoordinator's own default: a coordinator
        # that has never failed a refresh starts "successful", not
        # "recovering from an outage" - tests that want the latter set
        # this to False themselves before calling _async_update_data.
        self.last_update_success = True

    def async_update_listeners(self):
        pass


coordinator_module = load_source(
    "coordinator", **constants, DataUpdateCoordinator=CoordinatorBase,
    Code27Error=api.Code27Error, Code27AuthError=api.Code27AuthError,
    Code27ConnectionError=api.Code27ConnectionError,
    Code27RequestExpiredError=api.Code27RequestExpiredError, CONF_HOST="host",
    # The TF-card inventory precedence rule lives in the Home-Assistant-free
    # code27/music.py and is used by the poll merge.
    merge_music_inventory=code27_music.merge_music_inventory,
    usable_music_storage=code27_music.usable_music_storage,
    sync_music_mode_recovery_issue=lambda *args, **kwargs: None,
    sync_music_chat_conflict_issue=lambda *args, **kwargs: None,
)
# Import the HA-independent core normally; only HA boundaries are adapted.
import importlib
import importlib.util

core_spec = importlib.util.spec_from_file_location(
    "hase_spellbook_core", ROOT / "core/__init__.py",
    submodule_search_locations=[str(ROOT / "core")],
)
if core_spec.name not in sys.modules:
    core_module = importlib.util.module_from_spec(core_spec)
    sys.modules[core_spec.name] = core_module
    core_spec.loader.exec_module(core_module)
engine = importlib.import_module("hase_spellbook_core.spellbook_engine")
matching = importlib.import_module("hase_spellbook_core.matching")
models = importlib.import_module("hase_spellbook_core.models")
loader = load_source("spellbook_loader", EngineSpellbook=engine.EngineSpellbook,
                     load_spellbook_from_path=engine.load_spellbook_from_path)

class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        self.hass = N(
            config=N(language="de"),
            bus=N(async_fire=lambda kind, data: self.events.append((kind, data))),
            async_create_task=lambda coro, **kwargs: asyncio.create_task(coro),
        )
        self.entry = N(entry_id="device-a", options={
            "codie_agent": "conversation.agent",
        }, data={})
        self.client = N(
            get_chat_history_snapshot=AsyncMock(return_value=("2026-09-09", [])),
            send_chat_text=AsyncMock(return_value="own-1"),
            stop_light_preview=AsyncMock(),
        )
        self.c = coordinator_module.Code27Coordinator(self.hass, self.entry, self.client, 15)
        self.c.data = coordinator_module.Code27Data(current_slot={"slot_number": 1})

    def messages(self, *ids):
        return [{"chat_id": i, "role": "user", "content": f"Computer {i}"} for i in ids]

    async def poll(self, messages):
        self.client.get_chat_history_snapshot.return_value = ("2026-09-09", messages)
        self.c._chat_last_polled_at = 0.0  # bypass C7 backoff in unit tests
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

    async def test_chat_dedupe_is_bounded_and_expires_oldest_first(self):
        await self.poll([])
        for number in range(coordinator_module._CHAT_DEDUPE_MAX_IDS + 1):
            self.c._remember_seen_chat_id(1, str(number))
            self.c._remember_own_chat_id(1, str(number))
        self.assertEqual(len(self.c._chat_seen_at), coordinator_module._CHAT_DEDUPE_MAX_IDS)
        self.assertEqual(len(self.c._own_chat_ids), coordinator_module._CHAT_DEDUPE_MAX_IDS)
        self.assertNotIn("0", self.c._chat_seen[1])
        self.assertNotIn((1, "0"), self.c._own_chat_ids)
        old = time.monotonic() - coordinator_module._CHAT_DEDUPE_TTL_SEC - 1
        self.c._chat_seen_at[(1, "1")] = old
        self.c._own_chat_ids_at[(1, "1")] = old
        self.c._purge_chat_dedupe()
        self.assertNotIn("1", self.c._chat_seen[1])
        self.assertNotIn((1, "1"), self.c._own_chat_ids)

    async def test_music_mode_chat_conflict_issue_is_once_and_clears_on_mode_change(self):
        self.c.data.music_page = {"is_open": True}
        await self.poll([])
        with patch.object(coordinator_module, "sync_music_chat_conflict_issue") as sync:
            await self.poll(self.messages("new-1"))
            await self.poll(self.messages("new-1", "new-2"))
            sync.assert_called_once_with(self.hass, entry_id="device-a", suspected=True)
            self.c._handle_ws_music_page({"is_open": False})
            self.assertEqual(sync.call_count, 2)
            self.assertEqual(sync.call_args.kwargs["suspected"], False)

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
        client._call = AsyncMock(side_effect=[
            {"data": {"items": recent, "has_more": False}},
            {"data": {"items": full, "has_more": False}},
        ])
        _, messages = await client.get_chat_history_snapshot(1, "2026-09-09", "0")
        self.assertEqual(len(messages), 51)
        self.assertIn("/chat/history?", client._call.call_args.args[1])

    async def test_midnight_includes_previous_day(self):
        client = api.Code27Client(None, "device", 8081)
        client.get_chat_history_dates = AsyncMock(return_value=["2026-09-09", "2026-09-10"])
        client._call = AsyncMock(side_effect=[
            {"data": {"items": [{"chat_id": "late"}], "has_more": False}},
            {"data": {"items": [{"chat_id": "new"}], "has_more": False}},
        ])
        date, messages = await client.get_chat_history_snapshot(1, "2026-09-09", "old")
        self.assertEqual(date, "2026-09-10")
        self.assertEqual([m["chat_id"] for m in messages], ["late", "new"])

    async def test_send_chat_text_uses_chat_turns_on_1_5_3(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(side_effect=[
            {"success": True, "data": {"turn_id": "t1", "state": "queued"}},
            {"success": True, "data": {"turn_id": "t1", "state": "running",
                                       "user_chat_ids": ["u1"]}},
        ])
        self.assertEqual(await client.send_chat_text("hello", 2, "de"), "u1")
        method, path = client._call.mock_calls[0].args
        body = client._call.mock_calls[0].kwargs["body"]
        self.assertEqual((method, path), ("POST", "/data/user/chat/turns"))
        self.assertEqual(body["slot"], 2)
        self.assertEqual(body["input"], {"type": "text", "text": "hello"})
        self.assertEqual(body["output"], {"speech": "device"})
        self.assertEqual(len(body["request_id"]), 36)
        self.assertNotIn("language", body)

    async def test_normal_poll_keeps_small_window(self):
        client = api.Code27Client(None, "device", 8081)
        client.get_chat_history_dates = AsyncMock(return_value=["2026-09-09"])
        client._call = AsyncMock(return_value={"data": {
            "items": [{"chat_id": "old"}, {"chat_id": "new"}], "has_more": False,
        }})
        _, messages = await client.get_chat_history_snapshot(1, "2026-09-09", "old")
        self.assertEqual(len(messages), 2)
        client._call.assert_awaited_once()
        path = client._call.call_args.args[1]
        self.assertTrue(path.startswith("/data/user/chat/history?"))
        self.assertIn("limit=30", path)
        self.assertNotIn("/latest", path)

    async def test_full_day_follows_cursor_pages(self):
        client = api.Code27Client(None, "device", 8081)
        client.get_chat_history_dates = AsyncMock(return_value=["2026-09-09"])
        client._call = AsyncMock(side_effect=[
            {"data": {"items": [{"chat_id": "1", "created_at": "a"}],
                      "next_cursor": "c1", "has_more": True}},
            {"data": {"items": [{"chat_id": "2", "created_at": "b"}],
                      "next_cursor": "", "has_more": False}},
        ])
        _, messages = await client.get_chat_history_snapshot(1, None)
        self.assertEqual([m["chat_id"] for m in messages], ["1", "2"])
        second = client._call.mock_calls[1].args[1]
        self.assertIn("cursor=c1", second)
        self.assertIn("direction=newer", second)

    async def test_initially_empty_history_recovers_multiple_new_days(self):
        client = api.Code27Client(None, "device", 8081)
        client.get_chat_history_dates = AsyncMock(return_value=["2026-09-09", "2026-09-10"])
        client._call = AsyncMock(side_effect=[
            {"data": {"items": [{"chat_id": "first-day"}], "has_more": False}},
            {"data": {"items": [{"chat_id": "second-day"}], "has_more": False}},
        ])
        _, messages = await client.get_chat_history_snapshot(1, "")
        self.assertEqual(len(messages), 2)

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

    async def test_get_music_storage_reads_the_tf_card_inventory(self):
        # Audit C27-061: CONFIRMED LIVE but previously unused, which left the
        # media browser dependent on a WebSocket push ever arriving.
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(
            return_value={"success": True, "data": {"is_loaded": True, "music_files": []}}
        )
        result = await client.get_music_storage()
        self.assertEqual(result, {"is_loaded": True, "music_files": []})
        client._call.assert_awaited_once_with("GET", "/data/user/music/storage")

    async def test_get_music_storage_can_force_a_rescan(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={"success": True, "data": {}})
        await client.get_music_storage(refresh=True)
        client._call.assert_awaited_once_with("GET", "/data/user/music/storage?refresh=1")

    def test_music_storage_version_is_validated_as_numeric(self):
        # `version` is a numeric revision counter on every music/* endpoint,
        # including the storage inventory the poll now reads.
        with self.assertRaises(api.Code27Error):
            api.Code27Client._validate_response_data(
                "GET", "/data/user/music/storage", {"data": {"version": "1.2.3"}}
            )

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
        # aiohttp would percent-encode the file name ("a b.mp3" -> "a%20b.mp3")
        # and the device stores that literally - so the name must go out raw.
        self.assertIs(captured["data"].quote_fields, False)
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

    async def test_get_chat_history_count_reads_latest_turn_per_slot_on_1_5_3(self):
        client = api.Code27Client(None, "device", 8081)

        async def call(method, path, body=None, **_):
            slot = int(path.split("slot=")[1].split("&")[0])
            if slot == 3:
                raise api.Code27Error("no history")
            return {"data": {"items": [{"content": f"hi {slot}", "role": "user"}],
                             "has_more": False}}

        client._call = AsyncMock(side_effect=call)
        result = await client.get_chat_history_count()
        self.assertEqual([e["slot"] for e in result], [1, 2, 4, 5])
        self.assertEqual(result[0]["messages"][0]["content"], "hi 1")
        self.assertIn("limit=1", client._call.mock_calls[0].args[1])

    async def test_send_chat_image_rejects_oversized_image_without_calling_device(self):
        client = api.Code27Client(None, "device", 8081)
        client._call_multipart = AsyncMock()
        with self.assertRaises(api.Code27Error):
            await client.send_chat_image(b"x" * (client._MAX_CHAT_IMAGE_BYTES + 1), "a.jpg", "image/jpeg")
        client._call_multipart.assert_not_awaited()

    async def test_send_chat_image_uses_chat_turns_on_1_5_3(self):
        client = api.Code27Client(None, "device", 8081)
        client._call_multipart = AsyncMock(return_value={
            "success": True,
            "data": {"turn_id": "t", "user_chat_ids": ["c1", "c2"]},
        })
        ids = await client.send_chat_image(b"bytes", "a.jpg", "image/jpeg", text="hi", slot=2)
        self.assertEqual(ids, ("c1", "c2"))
        _, _, kwargs = client._call_multipart.mock_calls[0]
        self.assertEqual(client._call_multipart.mock_calls[0].args[1], "/data/user/chat/turns")
        request = json.loads(kwargs["fields"]["request"])
        self.assertEqual(request["slot"], 2)
        self.assertEqual(request["input"], {"type": "image_text", "text": "hi"})
        self.assertEqual(request["output"], {"speech": "device"})
        self.assertEqual(kwargs["files"], [("image", b"bytes", "a.jpg", "image/jpeg")])

    async def test_alarm_toggle_patches(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={"success": True})
        await client.disable_alarm("a1")
        client._call.assert_awaited_once_with(
            "PATCH", "/data/user/alarm/a1", body={"enabled": False}
        )

    async def test_sound_control_uses_single_endpoint_on_1_5_3(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={"success": True})
        await client.play_sound()
        client._call.assert_awaited_once_with(
            "POST", "/data/user/sound/control", body={"action": "play"}
        )

    async def test_delete_chat_messages_includes_slot_when_given(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={"success": True})
        await client.delete_chat_messages(["a"], slot=3)
        client._call.assert_awaited_once_with(
            "DELETE", "/data/user/chat/history/items",
            body={"chat_ids": ["a"], "slot_number": 3},
        )

    async def test_custom_thumbnail_name_comes_from_custom_cloths_on_1_5_3(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(side_effect=api.Code27NotFoundError("http_404"))
        client.list_character_settings = AsyncMock(return_value=[
            {"slot_number": 1, "custom_cloths": "\nLylia.vrm\nLylia_v2.vrm"},
        ])
        client._call_binary = AsyncMock(return_value=("image/png", b"png"))
        result = await client.get_character_portrait(1, 3)
        self.assertEqual(result, ("image/png", b"png"))
        client._call_binary.assert_awaited_once_with(
            "/resources/user/assets/thumbnails/1_3_Lylia_v2.png"
        )

    async def test_get_chat_image_calls_binary_endpoint_with_uuid(self):
        client = api.Code27Client(None, "device", 8081)
        client._call_binary = AsyncMock(return_value=("image/jpeg", b"data"))
        result = await client.get_chat_image("abc-123")
        self.assertEqual(result, ("image/jpeg", b"data"))
        self.assertIn("uuid=abc-123", client._call_binary.call_args.args[0])

    async def test_get_character_portrait_prefers_custom_thumbnail(self):
        client = api.Code27Client(None, "device", 8081)
        client.list_character_settings = AsyncMock(return_value=[
            {"slot_number": 1, "custom_cloths": "\nLylia.vrm"},
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
        client._call_binary = AsyncMock(return_value=("image/png", b"stock"))
        result = await client.get_character_portrait(3, 1)
        self.assertEqual(result, ("image/png", b"stock"))
        self.assertIn("original-portraits/3_1.png", client._call_binary.call_args.args[0])

    async def test_get_character_portrait_returns_none_when_nothing_exists(self):
        client = api.Code27Client(None, "device", 8081)
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

    async def test_update_character_setting_sends_slot_without_device_id(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(return_value={"success": True, "data": {"voice_id": "1"}})
        await client.update_character_setting(2, "machine-1", voice_id="1", group_key="g")
        _, _, kwargs = client._call.mock_calls[0]
        self.assertEqual(
            kwargs["body"], {"slot_number": 2, "voice_id": "1", "group_key": "g"}
        )

    async def test_update_character_setting_retries_with_device_id_on_old_firmware(self):
        client = api.Code27Client(None, "device", 8081)
        client._call = AsyncMock(side_effect=[
            api.Code27Error("unknown field"), {"success": True, "data": {}},
        ])
        await client.update_character_setting(2, "machine-1", voice_id="1")
        self.assertEqual(client._call.mock_calls[1].kwargs["body"]["device_id"], "machine-1")

    async def test_outfit_change_goes_through_outfit_apply_with_current_values(self):
        client = api.Code27Client(None, "device", 8081)
        client.list_character_settings = AsyncMock(return_value=[
            {"slot_number": 2, "selected_cloth": 0, "available_cloths": "0,1",
             "link_other_original_cloth": ""},
        ])
        client._call = AsyncMock(return_value={"success": True, "data": {"selected_cloth": 1}})
        await client.update_character_setting(2, "machine-1", selected_cloth=1)
        client._call.assert_awaited_once_with(
            "POST", "/data/user/character/outfit/apply",
            body={"slot_number": 2, "selected_cloth": 1, "available_cloths": "0,1",
                  "link_other_original_cloth": ""},
        )


class MediaPlayerTests(unittest.IsolatedAsyncioTestCase):
    def _entity(
        self, music_storage=None, client=None, music_playback=None, music_source=None
    ):
        # Bypasses __init__ (its super().__init__ chain expects the real
        # Code27Entity/CoordinatorEntity, not the empty test doubles this
        # harness loads media_player.py with) - sets just what the methods
        # under test actually read. The position bookkeeping the media player
        # keeps per entity is declared as class defaults for exactly this
        # reason.
        entity = media_player.Code27MediaPlayer.__new__(media_player.Code27MediaPlayer)
        entity.coordinator = N(
            data=N(
                music_storage=music_storage or {},
                music_playback=music_playback or {},
                music_source=music_source or {},
            ),
            client=client or N(),
            async_request_refresh=AsyncMock(),
        )
        return entity

    def _reload_playback(self, entity, playback):
        """Simulate one coordinator update: a full poll replaces the snapshot
        with an equal-but-distinct dict, exactly like ``Code27Coordinator``
        building a new ``Code27Data`` every cycle."""
        entity.coordinator.data = N(
            music_storage=entity.coordinator.data.music_storage,
            music_playback=dict(playback),
            music_source=entity.coordinator.data.music_source,
        )

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

    # -- absolute TF-card paths (audit CCF-2 / SA-5 / Q-D, backlog P0-2) -----
    # These use the *live* payload shape. The relative-path tests above are
    # kept because payloads without a declared root must keep working, but on
    # their own they hid the defect: with absolute paths the old grouping
    # produced a single folder with an empty name.

    def test_group_music_files_groups_absolute_paths_relative_to_root(self):
        files = LIVE_STORAGE["music_files"]
        folders, root_files = media_player._group_music_files(
            files, LIVE_STORAGE["root_path"]
        )
        self.assertEqual(folders, ["Rockkapella"])
        self.assertEqual([f["name"] for f in root_files], ["top.mp3"])

    def test_group_music_files_never_returns_an_empty_folder_for_absolute_paths(self):
        # The regression itself: "" as a folder name is what collapsed the
        # library, so it must be impossible with the live payload.
        folders, _root_files = media_player._group_music_files(
            LIVE_STORAGE["music_files"], LIVE_STORAGE["root_path"]
        )
        self.assertNotIn("", folders)
        self.assertEqual(folders, ["Rockkapella"])

    def test_group_music_files_handles_missing_root_path(self):
        # `music_storage` is WebSocket-only and may arrive without root_path;
        # the device's own `code27/music` marker still normalises the paths.
        folders, root_files = media_player._group_music_files(
            LIVE_STORAGE["music_files"], None
        )
        self.assertEqual(folders, ["Rockkapella"])
        self.assertEqual([f["name"] for f in root_files], ["top.mp3"])

    def test_files_in_folder_with_absolute_paths(self):
        result = media_player._files_in_folder(
            LIVE_STORAGE["music_files"], "Rockkapella", LIVE_STORAGE["root_path"]
        )
        self.assertEqual(
            [f["name"] for f in result], ["03 - Stand By Me.mp3", "live.mp3"]
        )

    def test_track_browse_item_keeps_the_absolute_track_id(self):
        item = media_player._track_browse_item(
            LIVE_STORAGE["music_files"][1], LIVE_STORAGE["root_path"]
        )
        self.assertEqual(
            item.media_content_id,
            "local:/storage/142E2DF92E2DD4900/code27/music/Rockkapella/"
            "03 - Stand By Me.mp3",
        )
        self.assertEqual(item.title, "03 - Stand By Me.mp3")
        self.assertTrue(item.can_play)

    async def test_browse_media_root_with_absolute_paths_lists_real_folders(self):
        entity = self._entity(music_storage=LIVE_STORAGE)
        result = await entity.async_browse_media()
        self.assertEqual(
            [(c.title, c.media_content_id) for c in result.children],
            [
                ("Rockkapella", "folder:Rockkapella"),
                ("top.mp3", "local:/storage/142E2DF92E2DD4900/code27/music/top.mp3"),
            ],
        )
        self.assertTrue(result.children[0].can_expand)
        self.assertFalse(result.children[0].can_play)
        self.assertTrue(result.children[1].can_play)

    async def test_browse_media_nested_folder_with_absolute_paths(self):
        entity = self._entity(music_storage=LIVE_STORAGE)
        result = await entity.async_browse_media(media_content_id="folder:Rockkapella")
        self.assertEqual(
            [(c.title, c.can_expand) for c in result.children],
            [("Live", True), ("03 - Stand By Me.mp3", False)],
        )

    async def test_browse_media_nested_child_folder(self):
        entity = self._entity(music_storage=LIVE_STORAGE)
        result = await entity.async_browse_media(
            media_content_id="folder:Rockkapella/Live"
        )
        self.assertEqual([c.title for c in result.children], ["live.mp3"])
        self.assertTrue(result.children[0].media_content_id.endswith("/Live/live.mp3"))

    def test_control_error_message_is_shared_with_the_music_runtime(self):
        # The CODE27 music runtime reports device failures with exactly the media
        # player's wording because both read this one mapping in code27/music.py.
        self.assertEqual(
            media_player.control_error_message("MODE_INACTIVE", "raw"),
            code27_music.CONTROL_ERROR_MESSAGES["MODE_INACTIVE"],
        )
        self.assertEqual(
            media_player.control_error_message("unknown_code", "raw fallback"),
            "raw fallback",
        )

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

    # -- Home Assistant position/seek contract ------------------------------
    # The native media player card extrapolates the progress it displays from
    # `media_position` + `media_position_updated_at` while the entity is
    # PLAYING (PAUSED shows `media_position` as it is). CODE27 reported
    # `position_ms`/`duration_ms` but no timestamp at all, so the card could
    # only ever show the position of the last poll - correct while paused,
    # frozen while playing - and a seek had no device action to call, which is
    # why the card's slider sprang back on release.

    async def test_position_and_stamp_are_reported_while_playing(self):
        entity = self._entity(music_playback=_playback())
        self.assertEqual(entity.state, "playing")
        self.assertEqual(entity.media_position, 5)
        self.assertEqual(entity.media_duration, 210)
        stamp = entity.media_position_updated_at
        self.assertIsInstance(stamp, datetime)
        self.assertIs(stamp.tzinfo, timezone.utc)
        # The stamp has to live in Home Assistant's clock domain, not the
        # device's: the captured device markers are nowhere near UTC now, and
        # the card subtracts `now - media_position_updated_at`.
        self.assertLess(abs((datetime.now(timezone.utc) - stamp).total_seconds()), 10)

    async def test_position_while_paused_keeps_reporting_the_device_value(self):
        # The one case that already looked right must keep looking right: HA
        # ignores the stamp for a paused entity and shows `media_position`.
        entity = self._entity(
            music_playback=_playback(status="PAUSED", is_playing=False, position_ms=74000)
        )
        self.assertEqual(entity.state, "paused")
        self.assertEqual(entity.media_position, 74)
        self.assertIsInstance(entity.media_position_updated_at, datetime)

    async def test_stamp_is_not_renewed_by_a_coordinator_update_without_a_new_reading(self):
        # An unrelated poll cycle (alarms, memos, system) re-delivers the same
        # music_playback snapshot. Restamping here would restart the card's
        # interpolation against a position that is not fresh.
        entity = self._entity(music_playback=_playback())
        first = entity.media_position_updated_at
        for _ in range(3):
            self._reload_playback(entity, _playback())
            self.assertEqual(entity.media_position, 5)
            self.assertIs(entity.media_position_updated_at, first)

    async def test_stamp_is_renewed_by_an_advanced_device_position(self):
        entity = self._entity(music_playback=_playback())
        first = entity.media_position_updated_at
        self._reload_playback(entity, _playback(position_ms=35000, updated_at_ms=1790173400000))
        self.assertEqual(entity.media_position, 35)
        self.assertIsNot(entity.media_position_updated_at, first)

    async def test_stamp_is_renewed_by_a_device_revision_marker_alone(self):
        # `updated_at_ms`/`version` are the device's own change stamps (the
        # captured `music_page` payload reports 0/0 until something happens), so
        # a new marker is a new reading even when the millisecond value repeats.
        entity = self._entity(music_playback=_playback())
        first = entity.media_position_updated_at
        self._reload_playback(entity, _playback(updated_at_ms=1790173400000, version=2))
        self.assertIsNot(entity.media_position_updated_at, first)

    async def test_stamp_is_renewed_by_a_track_change(self):
        entity = self._entity(music_playback=_playback())
        first = entity.media_position_updated_at
        self._reload_playback(
            entity, _playback(track_id="local:/storage/card/code27/music/other.mp3", position_ms=0)
        )
        self.assertEqual(entity.media_position, 0)
        self.assertIsNot(entity.media_position_updated_at, first)

    async def test_stamp_is_renewed_by_a_play_pause_transition(self):
        entity = self._entity(music_playback=_playback())
        first = entity.media_position_updated_at
        self._reload_playback(entity, _playback(status="PAUSED", is_playing=False))
        self.assertIsNot(entity.media_position_updated_at, first)

    async def test_position_and_stamp_are_unknown_without_a_device_position(self):
        for playback in ({}, _playback(position_ms=None)):
            entity = self._entity(music_playback=playback)
            self.assertIsNone(entity.media_position)
            self.assertIsNone(entity.media_position_updated_at)

    def test_seek_is_advertised_without_losing_the_existing_features(self):
        features = media_player.Code27MediaPlayer._attr_supported_features
        self.assertTrue(features & media_player.MediaPlayerEntityFeature.SEEK)
        for name in (
            "PLAY", "PAUSE", "NEXT_TRACK", "PREVIOUS_TRACK", "VOLUME_SET",
            "SELECT_SOURCE", "PLAY_MEDIA", "BROWSE_MEDIA",
        ):
            self.assertTrue(
                features & getattr(media_player.MediaPlayerEntityFeature, name),
                f"{name} must stay advertised",
            )

    async def test_media_seek_converts_seconds_to_progress_ms_and_refreshes(self):
        client = N(music_control=AsyncMock(return_value={"status": "PLAYING"}))
        entity = self._entity(client=client, music_playback=_playback())
        await entity.async_media_seek(61.5)
        # Exactly the device's own payload: `progress_ms` is the millisecond
        # form of the slider value, plus the track the device reported.
        client.music_control.assert_awaited_once_with(
            "SEEK", progress_ms=61500, track_id=TRACK_ID
        )
        entity.coordinator.async_request_refresh.assert_awaited_once()

    async def test_media_seek_clamps_a_negative_position_like_the_device_client(self):
        client = N(music_control=AsyncMock(return_value={"status": "PLAYING"}))
        entity = self._entity(client=client, music_playback=_playback())
        await entity.async_media_seek(-3.0)
        client.music_control.assert_awaited_once_with(
            "SEEK", progress_ms=0, track_id=TRACK_ID
        )

    async def test_media_seek_omits_track_id_when_the_device_reports_none(self):
        # The device's own client sends `track_id: undefined` in that case, and
        # JSON.stringify drops the key - `null` would be a different payload.
        client = N(music_control=AsyncMock(return_value={"status": "PLAYING"}))
        entity = self._entity(client=client, music_playback=_playback(track_id=""))
        await entity.async_media_seek(10.0)
        client.music_control.assert_awaited_once_with("SEEK", progress_ms=10000)

    async def test_media_seek_reports_the_target_until_the_device_confirms_it(self):
        client = N(music_control=AsyncMock(return_value={"status": "PLAYING"}))
        entity = self._entity(client=client, music_playback=_playback())
        before = entity.media_position_updated_at
        await entity.async_media_seek(61.5)
        # Without this the card snaps back to the last polled position: the
        # device has not confirmed the seek yet, and the refreshed snapshot may
        # still be the pre-seek one.
        self.assertEqual(entity.media_position, 61)
        self.assertIsNot(entity.media_position_updated_at, before)
        # The device then reports the seek it accepted - its reading wins, and
        # the override is gone for good.
        self._reload_playback(entity, _playback(position_ms=63000, updated_at_ms=1790173400000))
        self.assertEqual(entity.media_position, 63)

    async def test_media_seek_translates_a_device_failure_and_does_not_refresh(self):
        client = N(music_control=AsyncMock(
            side_effect=api.Code27DeviceError("MODE_INACTIVE", "music mode inactive")
        ))
        entity = self._entity(client=client, music_playback=_playback())
        with self.assertRaises(RuntimeError):
            await entity.async_media_seek(30.0)
        self.assertEqual(entity.media_position, 5)
        entity.coordinator.async_request_refresh.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
