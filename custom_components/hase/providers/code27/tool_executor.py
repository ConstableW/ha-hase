"""Codie (CODE27 firmware >= 1.6.0) external-tool executor.

Registers two *gateway* tools with the device and serves their calls over
the dedicated tool WebSocket, so Codie's own model can cast the Spells of
one HASE MCP Bridge directly - no chat reading, no history cleanup:

    spell_list  - the Bridge's Spell catalog (read)
    spell_cast  - cast one Spell by id with JSON-string arguments

Two tools (limit is 16) keep the whole catalog reachable regardless of
size.

This module is part of the shared CODE27 device code and must stay free of
HASE imports: the catalog and the execution come from an injected
``ToolDispatcher``. Standalone CODE27 without a dispatcher registers
nothing; HASE injects one backed by an MCP Bridge (providers/code27/
tool_bridge.py), for a device it owns as well as for one owned by a
standalone CODE27 entry.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Protocol

import aiohttp

from ...const import DOMAIN
from .api import Code27Client, Code27Error, Code27NotFoundError

_LOGGER = logging.getLogger(f"{DOMAIN}.tool_executor")

_MIN_RECONNECT_DELAY = 2
_MAX_RECONNECT_DELAY = 60
_MAX_DESC = 500
_RESULT_TEXT_MAX = 400

_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "text": {"type": "string", "maxLength": _RESULT_TEXT_MAX},
    },
    "required": ["ok", "text"],
    "additionalProperties": False,
}


class ToolDispatcher(Protocol):
    """Catalog + execution behind the gateway tools (supplied by the host)."""

    async def async_list_spells(self) -> list[dict[str, Any]]:
        """[{spell_id, description, arguments:{name:{...}}, read_only}]"""

    async def async_cast_spell(self, spell_id: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        """(ok, text); must not raise for an unusable backend."""

    # Optional: ``async_home_command(text) -> (ok, text)``. A dispatcher that
    # has it gets the ``home_command`` tool (plain sentence, host routes it).


def integration_id_for(entry_id: str) -> str:
    """Stable lowercase UUID per config entry (the app chooses the ID)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"hase:codie-tools:{entry_id}"))


def _catalog_line(spell: dict[str, Any]) -> str:
    args = ",".join(spell["arguments"]) or "-"
    desc = " ".join(str(spell["description"]).split())[:40]
    return f"{spell['spell_id']}({args}): {desc}"


def _home_command_tool() -> dict[str, Any]:
    return {
        "name": "home_command", "version": "v1",
        "description": "Smart-home control by plain sentence, exactly as the user said it "
                       "(e.g. 'turn off the kitchen light'). Prefer this for any home request. "
                       "If the result is a question, ask the user and call again with their answer.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "maxLength": 300,
                                    "description": "The user's sentence or answer, unchanged"}},
            "required": ["text"], "additionalProperties": False,
        },
        "result_schema": _RESULT_SCHEMA, "risk": "consequential",
        "idempotent": False, "confirmation_required": False, "timeout_ms": 20000,
    }


def build_tools(spells: list[dict[str, Any]], *, home_command: bool = False) -> list[dict[str, Any]]:
    # The device rejects tool descriptions longer than ~512 characters
    # (INVALID_ARGUMENT, probed live on firmware 1.6.0: 500 ok, 600 rejected),
    # so only as many catalog lines as fit are inlined; spell_list has the rest.
    if not spells:  # no Spell catalog behind this dispatcher
        return [_home_command_tool()] if home_command else []
    head = ('Cast a smart-home spell: spell=id, args=JSON object string, '
            'e.g. {"area":"kitchen"} or {}. Spells:\n')
    more = "...more: spell_list"
    lines: list[str] = []
    used = len(head)
    for index, spell in enumerate(spells):
        line = _catalog_line(spell)
        reserve = len(more) + 1 if index < len(spells) - 1 else 0
        if used + len(line) + 1 + reserve > _MAX_DESC:
            lines.append(more)
            break
        lines.append(line)
        used += len(line) + 1
    cast_desc = (head + "\n".join(lines))[:_MAX_DESC]
    return ([_home_command_tool()] if home_command else []) + [
        {
            "name": "spell_cast", "version": "v1", "description": cast_desc,
            "parameters": {
                "type": "object",
                "properties": {
                    "spell": {"type": "string", "maxLength": 64, "description": "Exact spell id"},
                    "args": {"type": "string", "maxLength": 400,
                             "description": "JSON object string with the spell's arguments"},
                },
                "required": ["spell", "args"], "additionalProperties": False,
            },
            "result_schema": _RESULT_SCHEMA, "risk": "consequential",
            "idempotent": False, "confirmation_required": False, "timeout_ms": 15000,
        },
        {
            "name": "spell_list", "version": "v1",
            "description": "List all smart-home spells with their argument names. "
                           "Use when the spell list in spell_cast does not cover the request.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            "result_schema": _RESULT_SCHEMA, "risk": "read",
            "idempotent": True, "confirmation_required": False, "timeout_ms": 8000,
        },
    ]


class CodieToolExecutor:
    """Registers the gateway tools and serves invocations; reconnects forever."""

    def __init__(self, hass, client: Code27Client, dispatcher: ToolDispatcher,
                 integration_id: str, *, name: str = "HASE Spellbook") -> None:
        self._hass = hass
        self._client = client
        self._dispatcher = dispatcher
        self._id = integration_id
        self._name = name
        self._task: asyncio.Task | None = None
        self._stopped = True

    def start(self) -> None:
        if self._task is not None:
            return
        self._stopped = False
        self._task = self._hass.async_create_background_task(self._run(), name="codie_tool_executor")

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            task, self._task = self._task, None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _has_home_command(self) -> bool:
        return callable(getattr(self._dispatcher, "async_home_command", None))

    async def async_register(self) -> None:
        """PUT the (full) tool catalog; optimistic-lock on the current revision."""
        root = f"/data/user/tool-integrations/{self._id}"
        spells = await self._dispatcher.async_list_spells()
        for attempt in range(2):
            revision = 0
            try:
                revision = int((await self._client._call("GET", root)).get("data", {}).get("revision", 0))
            except Code27NotFoundError:
                revision = 0
            tools = build_tools(spells, home_command=self._has_home_command())
            if not tools:
                _LOGGER.info("Codie tools: nothing to register (no spells, no home_command agent)")
                return
            body = {"protocol_version": "v1", "expected_revision": revision, "name": self._name,
                    "enabled": True, "tools": tools}
            try:
                await self._client._call("PUT", root, body=body)
                _LOGGER.info("Codie tools registered (%d spells, revision %s)", len(spells), revision)
                return
            except Code27Error as err:
                if attempt == 0 and "409" in str(err):
                    continue  # re-read the revision once
                raise

    async def _run(self) -> None:
        delay = _MIN_RECONNECT_DELAY
        session = aiohttp.ClientSession()
        try:
            while not self._stopped:
                try:
                    await self.async_register()
                    url = await self._client.build_ws_url(f"/v1/ws/tool-integrations/{self._id}/executor")
                    async with session.ws_connect(url, heartbeat=30) as ws:
                        delay = _MIN_RECONNECT_DELAY
                        async for msg in ws:
                            if self._stopped:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                self._hass.async_create_background_task(
                                    self._handle(ws, msg.data), name="codie_tool_invoke")
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
                                              aiohttp.WSMsgType.ERROR):
                                break
                except asyncio.CancelledError:
                    raise
                except Exception as err:  # noqa: BLE001 - keep retrying
                    _LOGGER.warning("Codie tool executor error, retrying: %s: %s", type(err).__name__, err)
                if self._stopped:
                    break
                await asyncio.sleep(delay)
                delay = min(delay * 2, _MAX_RECONNECT_DELAY)
        finally:
            await session.close()

    async def _handle(self, ws, raw: str) -> None:
        try:
            message = json.loads(raw)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        data = message.get("data") if isinstance(message.get("data"), dict) else {}
        if kind == "error":
            _LOGGER.warning("Codie tool protocol error: %s", data.get("code"))
            return
        if kind != "invoke":
            _LOGGER.debug("Codie tool frame: %s", kind)
            return
        _LOGGER.debug("Codie invoke frame keys: %s", sorted(data))
        invocation_id = data.get("invocation_id")
        ok, text = await self.execute(data)
        await ws.send_str(json.dumps({"type": "result", "data": {
            "invocation_id": invocation_id, "status": "succeeded",
            "data": {"ok": ok, "text": text[:_RESULT_TEXT_MAX]},
        }}, ensure_ascii=False))

    async def execute(self, call: dict[str, Any]) -> tuple[bool, str]:
        """Pure dispatch of one invoke payload -> (ok, text)."""
        tool = next((call[k] for k in ("tool", "tool_name", "name") if isinstance(call.get(k), str)), None)
        args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        if tool is None:  # field name not confirmed by the docs - infer from arguments
            tool = ("home_command" if "text" in args
                    else "spell_cast" if "spell" in args else "spell_list")
        if tool == "home_command":
            text = args.get("text")
            if not self._has_home_command() or not isinstance(text, str) or not text.strip():
                return False, "home_command_unavailable"
            return await self._dispatcher.async_home_command(text.strip())
        if tool == "spell_list":
            spells = await self._dispatcher.async_list_spells()
            return True, "\n".join(_catalog_line(s) for s in spells)[:_RESULT_TEXT_MAX] or "no spells"
        if tool != "spell_cast":
            return False, f"unknown_tool:{tool}"
        spell = args.get("spell")
        if not isinstance(spell, str) or not spell:
            return False, "missing_spell"
        raw_args = args.get("args") or "{}"
        try:
            parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except ValueError:
            return False, "args_not_json"
        if not isinstance(parsed, dict):
            return False, "args_not_object"
        return await self._dispatcher.async_cast_spell(spell, parsed)
