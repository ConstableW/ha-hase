"""Codie tool-gateway (firmware 1.6.0): schema limits and invoke dispatch.

Runs without Home Assistant: the module source is exec'd with its relative
imports replaced by small stubs (same approach as test_harness_support).
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "hase" / "providers" / "code27" / "tool_executor.py"


class _Err(Exception):
    pass


class _Dispatcher:
    def __init__(self, list_spells, cast_spell):
        self.async_list_spells = list_spells
        self.async_cast_spell = cast_spell


def _load(list_spells=None, cast_spell=None):
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    tree.body = [
        n for n in tree.body
        if not (isinstance(n, ast.ImportFrom) and (n.level or n.module == "__future__"))
        and not (isinstance(n, ast.Import) and any(a.name == "aiohttp" for a in n.names))
    ]
    ns = {
        "DOMAIN": "hase", "BridgeIdentity": SimpleNamespace, "Code27Client": object,
        "Code27Error": _Err, "Code27NotFoundError": _Err,
        "aiohttp": SimpleNamespace(),
    }
    exec(compile(ast.fix_missing_locations(tree), str(SRC), "exec"), ns)
    return ns


SPELLS = [
    {"spell_id": "lumos", "description": "Turns the light on", "arguments": {"area": {}}, "read_only": False},
    {"spell_id": "temp", "description": "Reads the temperature", "arguments": {}, "read_only": True},
]


class ToolSchemaTests(unittest.TestCase):
    def test_limits_and_names(self):
        ns = _load()
        tools = ns["build_tools"](SPELLS)
        self.assertLessEqual(len(tools), 16)
        for tool in tools:
            self.assertRegex(tool["name"], r"^[a-z][a-z0-9_]{0,23}$")
            self.assertRegex(tool["version"], r"^[A-Za-z0-9_.-]{1,32}$")
            self.assertLessEqual(len(json.dumps(tool["parameters"])), 8 * 1024)
            self.assertFalse(tool["parameters"].get("additionalProperties", False))
            self.assertLessEqual(tool["timeout_ms"], 20000)
            # device limit probed live (firmware 1.6.0): 500 ok, 600 rejected
            self.assertLessEqual(len(tool["description"]), 500)
        self.assertIn("lumos(area)", tools[0]["description"])

    def test_huge_catalog_is_truncated_and_points_to_spell_list(self):
        ns = _load()
        many = [{"spell_id": f"s{i}", "description": "x" * 70, "arguments": {"a": {}}} for i in range(500)]
        cast = ns["build_tools"](many)[0]["description"]
        self.assertLessEqual(len(cast), 500)
        self.assertIn("spell_list", cast)

    def test_integration_id_is_stable_lowercase_uuid(self):
        ns = _load()
        a = ns["integration_id_for"]("ENTRY1")
        self.assertEqual(a, ns["integration_id_for"]("ENTRY1"))
        self.assertRegex(a, r"^[0-9a-f-]{36}$")


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    def _executor(self):
        self.cast = AsyncMock(return_value=(True, "Licht an"))
        self.lst = AsyncMock(return_value=SPELLS)
        ns = _load()
        return ns["CodieToolExecutor"](None, None, _Dispatcher(self.lst, self.cast), "id")

    async def test_cast_parses_json_args(self):
        ex = self._executor()
        ok, text = await ex.execute({"tool": "spell_cast", "arguments": {"spell": "lumos", "args": '{"area":"kitchen"}'}})
        self.assertEqual((ok, text), (True, "Licht an"))
        self.cast.assert_awaited_once()
        self.assertEqual(self.cast.await_args.args, ("lumos", {"area": "kitchen"}))

    async def test_tool_name_inferred_when_field_missing(self):
        ex = self._executor()
        ok, _ = await ex.execute({"arguments": {"spell": "lumos", "args": "{}"}})
        self.assertTrue(ok)
        ok, text = await ex.execute({"arguments": {}})
        self.assertTrue(ok)
        self.assertIn("lumos(area)", text)

    async def test_bad_input_never_reaches_the_bridge(self):
        ex = self._executor()
        for args, reason in (
            ({"spell": "", "args": "{}"}, "missing_spell"),
            ({"spell": "lumos", "args": "{not json"}, "args_not_json"),
            ({"spell": "lumos", "args": "[1]"}, "args_not_object"),
        ):
            ok, text = await ex.execute({"tool": "spell_cast", "arguments": args})
            self.assertFalse(ok)
            self.assertEqual(text, reason)
        self.cast.assert_not_awaited()

    async def test_home_command_only_with_host_support(self):
        ex = self._executor()
        self.assertFalse(ex._has_home_command())
        ok, text = await ex.execute({"tool": "home_command", "arguments": {"text": "Licht an"}})
        self.assertEqual((ok, text), (False, "home_command_unavailable"))
        self.assertEqual([t["name"] for t in _load()["build_tools"](SPELLS)], ["spell_cast", "spell_list"])

        class WithCommand(_Dispatcher):
            async def async_home_command(self, text):
                return True, f"ok:{text}"

        ns = _load()
        ex = ns["CodieToolExecutor"](None, None, WithCommand(self.lst, self.cast), "id")
        self.assertTrue(ex._has_home_command())
        self.assertEqual(await ex.execute({"arguments": {"text": " Licht an "}}), (True, "ok:Licht an"))
        names = [t["name"] for t in ns["build_tools"](SPELLS, home_command=True)]
        self.assertEqual(names, ["home_command", "spell_cast", "spell_list"])
        for tool in ns["build_tools"](SPELLS, home_command=True):
            self.assertLessEqual(len(tool["description"]), 500)

    async def test_agent_only_registers_just_home_command(self):
        ns = _load()
        self.assertEqual([t["name"] for t in ns["build_tools"]([], home_command=True)], ["home_command"])
        self.assertEqual(ns["build_tools"]([], home_command=False), [])

    async def test_unknown_tool(self):
        ex = self._executor()
        ok, text = await ex.execute({"tool": "rm_rf", "arguments": {}})
        self.assertEqual((ok, text), (False, "unknown_tool:rm_rf"))


class PlatformForwarderTests(unittest.TestCase):
    """HA imports custom_components.hase.<platform>; every platform the
    CODE27 provider forwards needs its root module (a missing one only
    shows up as ModuleNotFoundError at setup on a real instance)."""

    def test_every_forwarded_platform_has_a_root_module(self):
        init = (SRC.parent / "__init__.py").read_text(encoding="utf-8")
        block = init[init.index("PLATFORMS: list"):]
        block = block[block.index("= [") :]
        block = block[: block.index("]")]
        names = [m.lower() for m in re.findall(r"Platform[.]([A-Z_]+)", block)]
        self.assertIn("number", names)
        root = SRC.parents[2]
        for name in names:
            self.assertTrue((root / f"{name}.py").is_file(), f"missing hase/{name}.py")


class ChatTurnOptionTests(unittest.IsolatedAsyncioTestCase):
    async def _body(self, **kwargs):
        import test_harness_support as support

        client = support.api.Code27Client(None, "h", 1, "u", "s", "m")
        client._call = AsyncMock(return_value={"data": {"turn_id": "t"}})
        await client.create_chat_turn("hi", 2, "none", **kwargs)
        return client._call.await_args.kwargs["body"]

    async def test_defaults_are_omitted(self):
        body = await self._body()
        for key in ("interrupt", "retention", "tools"):
            self.assertNotIn(key, body)

    async def test_options_are_sent(self):
        body = await self._body(interrupt=False, retention="none", tools="none")
        self.assertEqual((body["interrupt"], body["retention"], body["tools"]), (False, "none", "none"))

    async def test_invalid_values_rejected(self):
        with self.assertRaises(ValueError):
            await self._body(retention="forever")
        with self.assertRaises(ValueError):
            await self._body(tools="some")


class EventsChannelTests(unittest.IsolatedAsyncioTestCase):
    def _module(self):
        import test_harness_support as support

        stub = SimpleNamespace(WSMsgType=SimpleNamespace(TEXT=1, CLOSED=2, CLOSING=3, ERROR=4), ClientSession=None)
        module = support.load_source("websocket_client", aiohttp=stub, **support.constants)
        module.aiohttp = stub
        return module

    async def test_subscribes_after_ready_with_full_set(self):
        ws_mod = self._module()
        sock = ws_mod.Code27WebSocket(None, None, path="/v1/ws/events", subscribe=["chat_history"])
        ready = SimpleNamespace(type=ws_mod.aiohttp.WSMsgType.TEXT, data='{"type":"ready","data":{}}')
        fake = SimpleNamespace(receive=AsyncMock(return_value=ready), send_str=AsyncMock())
        await sock._send_subscription(fake)
        sent = json.loads(fake.send_str.await_args.args[0])
        self.assertEqual(sent["type"], "subscribe")
        self.assertEqual(sent["data"]["events"], ["chat_history"])

    async def test_missing_ready_is_an_error(self):
        ws_mod = self._module()
        sock = ws_mod.Code27WebSocket(None, None, path="/v1/ws/events", subscribe=["chat_history"])
        bad = SimpleNamespace(type=ws_mod.aiohttp.WSMsgType.TEXT, data='{"type":"error"}')
        fake = SimpleNamespace(receive=AsyncMock(return_value=bad), send_str=AsyncMock())
        with self.assertRaises(ConnectionError):
            await sock._send_subscription(fake)

    def test_dispatch_reaches_chat_history_handler(self):
        ws_mod = self._module()
        sock = ws_mod.Code27WebSocket(None, None, path="/v1/ws/events", subscribe=["chat_history"])
        seen = []
        sock.on("chat_history", seen.append)
        sock._dispatch('{"type":"chat_history","data":{"event":"sync","version":10}}')
        self.assertEqual(seen, [{"event": "sync", "version": 10}])


class StandaloneSyncBoundaryTests(unittest.TestCase):
    """The generated standalone CODE27 must not need HASE-only modules."""

    def test_shared_files_have_no_hase_imports(self):
        import importlib.util

        path = SRC.parents[4] / "scripts" / "sync_code27.py"
        spec = importlib.util.spec_from_file_location("sync_code27_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.shared_import_problems(), [])
        for name in ("tool_executor.py", "number.py"):
            self.assertIn(name, module.CORE_FILES)
        self.assertNotIn("tool_bridge.py", module.CORE_FILES)


if __name__ == "__main__":
    unittest.main()


class SharedCodeBoundaryTests(unittest.TestCase):
    """The shared device code must not import HASE-only modules."""

    SHARED = ("tool_executor.py", "number.py")

    def test_shared_modules_do_not_import_hase_or_mcp(self):
        base = SRC.parent
        for name in self.SHARED:
            tree = ast.parse((base / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    self.assertFalse(
                        mod.startswith("mcp") or "mcp" in mod.split(".") or "tool_bridge" in mod
                        or (node.level >= 2 and mod not in ("const",)),
                        f"{name} imports {'.' * node.level}{mod}",
                    )
