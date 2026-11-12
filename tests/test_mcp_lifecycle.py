"""Exercise the provider's real listener lifecycle against a local TCP port."""

from __future__ import annotations

import ast
import asyncio
import logging
import socket
import time
import types
import unittest
from pathlib import Path

import uvicorn

SOURCE = Path(__file__).resolve().parents[1] / "custom_components/hase/providers/mcp/__init__.py"
FUNCTIONS = {
    "_hass_mcp_bucket", "_listener_done", "_serve_uvicorn", "_stop_uvicorn",
    "async_start_transport", "async_stop_transport", "async_restart_transport",
    "async_unload_mcp_server_entry",
}
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
selected = ast.Module(
    body=[node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in FUNCTIONS],
    type_ignores=[],
)
code = compile(ast.fix_missing_locations(selected), str(SOURCE), "exec")


class ConfigEntryError(Exception):
    pass


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            event = await receive()
            if event["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            else:
                await send({"type": "lifespan.shutdown.complete"})
                return
    else:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def build_server(hass, port):
    return uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical"))


def module():
    namespace = {
        "asyncio": asyncio, "time": time, "_LOGGER": logging.getLogger(__name__),
        "DOMAIN": "hase", "ConfigEntryError": ConfigEntryError,
        "CONF_MCP_SERVER_PORT": "port", "DEFAULT_MCP_SERVER_PORT": 8815,
        "_TRANSPORT_START_TIMEOUT_SECONDS": 1,
        "_TRANSPORT_STOP_TIMEOUT_SECONDS": 0.1,
        "_build_uvicorn_server": build_server,
        **{name: name for name in (
            "DATA_MCP_UVICORN_SERVER", "DATA_MCP_UVICORN_TASK", "DATA_MCP_LISTENER_PORT",
            "DATA_MCP_LISTENER_RESTART_LOCK", "DATA_MCP_CREDENTIAL_INDEX",
            "DATA_MCP_SERVER_ENTRY_ID", "DATA_MCP_RUNTIME_SUPERVISOR",
        )},
    }
    exec(code, namespace)  # noqa: S102 - compile only selected repository functions
    return types.SimpleNamespace(**namespace)


class Hass:
    def __init__(self):
        self.data = {}

    async def async_add_executor_job(self, func, *args):
        return func(*args)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class McpLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = module()
        self.hass = Hass()

    async def asyncTearDown(self):
        await self.provider.async_stop_transport(self.hass)

    async def test_normal_start_stop_and_reload(self):
        first, second = free_port(), free_port()
        entry = types.SimpleNamespace(options={"port": first})
        await self.provider.async_start_transport(self.hass, entry)
        bucket = self.provider._hass_mcp_bucket(self.hass)
        self.assertEqual(bucket["DATA_MCP_LISTENER_PORT"], first)
        _reader, writer = await asyncio.open_connection("127.0.0.1", first)
        writer.close()
        await writer.wait_closed()
        entry.options["port"] = second
        await self.provider.async_restart_transport(self.hass, entry, reason="test_reload")
        self.assertEqual(bucket["DATA_MCP_LISTENER_PORT"], second)
        self.assertTrue(await self.provider.async_unload_mcp_server_entry(self.hass, entry))
        self.assertNotIn("DATA_MCP_LISTENER_PORT", bucket)
        self.assertNotIn("DATA_MCP_UVICORN_TASK", bucket)

    async def test_occupied_port_does_not_publish_started_listener(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            with self.assertRaises(ConfigEntryError):
                await self.provider.async_start_transport(
                    self.hass, types.SimpleNamespace(options={"port": port})
                )
        bucket = self.provider._hass_mcp_bucket(self.hass)
        self.assertNotIn("DATA_MCP_LISTENER_PORT", bucket)
        self.assertNotIn("DATA_MCP_UVICORN_TASK", bucket)

    async def test_startup_timeout_is_bounded_and_not_published(self):
        class HungServer:
            started = False
            servers = ()
            should_exit = False

            async def serve(self):
                await asyncio.Event().wait()

        globals_ = self.provider.async_start_transport.__globals__
        globals_["_build_uvicorn_server"] = lambda hass, port: HungServer()
        globals_["_TRANSPORT_START_TIMEOUT_SECONDS"] = 0.02
        with self.assertRaises(ConfigEntryError):
            await self.provider.async_start_transport(
                self.hass, types.SimpleNamespace(options={"port": 12345})
            )
        bucket = self.provider._hass_mcp_bucket(self.hass)
        self.assertNotIn("DATA_MCP_LISTENER_PORT", bucket)
        self.assertNotIn("DATA_MCP_UVICORN_TASK", bucket)

    async def test_server_exits_cleanly_before_binding(self):
        class EarlyExitServer:
            started = False
            servers = ()
            should_exit = False

            async def serve(self):
                return None

        self.provider.async_start_transport.__globals__["_build_uvicorn_server"] = (
            lambda hass, port: EarlyExitServer()
        )
        with self.assertRaises(ConfigEntryError):
            await self.provider.async_start_transport(
                self.hass, types.SimpleNamespace(options={"port": 12345})
            )
        bucket = self.provider._hass_mcp_bucket(self.hass)
        self.assertNotIn("DATA_MCP_LISTENER_PORT", bucket)
        self.assertNotIn("DATA_MCP_UVICORN_TASK", bucket)

    async def test_unload_and_restart_fail_bounded_when_task_resists_cancel(self):
        release = asyncio.Event()
        ready = asyncio.Event()

        async def stubborn():
            ready.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue

        task = asyncio.create_task(stubborn())
        await ready.wait()
        bucket = self.provider._hass_mcp_bucket(self.hass)
        bucket["DATA_MCP_UVICORN_SERVER"] = types.SimpleNamespace(should_exit=False, servers=[])
        bucket["DATA_MCP_UVICORN_TASK"] = task
        bucket["DATA_MCP_LISTENER_PORT"] = 12345
        recovery_port = free_port()
        entry = types.SimpleNamespace(options={"port": recovery_port})
        started = time.monotonic()
        self.assertFalse(await self.provider.async_unload_mcp_server_entry(self.hass, entry))
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertNotIn("DATA_MCP_LISTENER_PORT", bucket)
        self.assertIs(bucket["DATA_MCP_UVICORN_TASK"], task)
        with self.assertRaises(ConfigEntryError):
            await self.provider.async_restart_transport(self.hass, entry, reason="test")
        release.set()
        await asyncio.wait_for(task, 1)
        await self.provider.async_restart_transport(self.hass, entry, reason="recovered")
        self.assertEqual(bucket["DATA_MCP_LISTENER_PORT"], recovery_port)
        self.assertTrue(await self.provider.async_unload_mcp_server_entry(self.hass, entry))
