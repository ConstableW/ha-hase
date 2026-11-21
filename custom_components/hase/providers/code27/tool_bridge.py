"""HASE-only: back the Codie tool gateway with HASE.

Like frontend.py this module is HASE glue and is NOT part of the shared/
standalone CODE27 device code. It builds a ToolDispatcher
(tool_executor.ToolDispatcher) from the device options and injects it into
the coordinator, which owns the gateway:

- ``codie_agent``       -> ``home_command(text)``: Codie hands the user's
  sentence to one HA conversation agent (normally a HASE Spellbooker) and
  falls back to HA's default Assist when the agent has no match.
- ``codie_tool_bridge`` -> ``spell_cast`` / ``spell_list``: the Spells of one
  MCP Bridge, same projection, area policy, Assist exposure and generation
  lease as the MCP transport.
"""
from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, HomeAssistant

from ..mcp.credentials import BridgeIdentity
from ..mcp.local_dispatch import async_cast_spell, async_list_spells
from .const import CONF_CODIE_AGENT, CONF_CODIE_TOOL_BRIDGE

_COMMAND_TIMEOUT_SEC = 15.0


class HaToolDispatcher:
    """ToolDispatcher over an optional MCP Bridge and an optional agent."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        identity: BridgeIdentity | None,
        agent_id: str | None,
    ) -> None:
        self._hass = hass
        self._identity = identity
        self._agent_id = agent_id
        self._conversation_id = f"codie-{entry_id}"
        if agent_id:  # only an agent adds the home_command tool
            self.async_home_command = self._async_home_command

    async def async_list_spells(self) -> list[dict[str, Any]]:
        if self._identity is None:
            return []
        return await async_list_spells(self._hass, self._identity)

    async def async_cast_spell(self, spell_id: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        if self._identity is None:
            return False, "spells_unavailable"
        return await async_cast_spell(self._hass, self._identity, spell_id, arguments)

    async def _converse(self, text: str, agent_id: str | None, conversation_id: str | None):
        async with asyncio.timeout(_COMMAND_TIMEOUT_SEC):
            return await conversation.async_converse(
                self._hass, text=text, conversation_id=conversation_id,
                context=Context(), agent_id=agent_id,
                language=self._hass.config.language or "de",
            )

    async def _async_home_command(self, text: str) -> tuple[bool, str]:
        """One sentence -> agent. A stable conversation id per device lets a
        clarification question be answered by the next call."""
        try:
            result = await self._converse(text, self._agent_id, self._conversation_id)
            response = getattr(result, "response", None)
            if str(getattr(response, "error_code", "") or "").endswith("no_intent_match"):
                # The Spellbook has no trigger for this sentence: ordinary
                # Assist (HA's default agent) answers it.
                result = await self._converse(text, None, None)
        except TimeoutError:
            return False, "timeout"
        except Exception as err:  # noqa: BLE001 - tool call must not raise
            return False, f"error:{type(err).__name__}"
        response = getattr(result, "response", None)
        speech = ((getattr(response, "speech", None) or {}).get("plain") or {}).get("speech") or ""
        failed = getattr(getattr(response, "response_type", None), "value", "") == "error"
        if getattr(result, "continue_conversation", False):
            speech = f"QUESTION: {speech}"
        return (not failed), speech or ("failed" if failed else "done")


def _identity_from_option(raw: Any) -> BridgeIdentity | None:
    """"<entry_id>:<subentry_id>" -> identity; anything else -> None."""
    if not isinstance(raw, str) or ":" not in raw:
        return None
    entry_id, _, subentry_id = raw.partition(":")
    if not entry_id or not subentry_id:
        return None
    return BridgeIdentity(entry_id=entry_id, subentry_id=subentry_id)


def dispatcher_from_options(hass: HomeAssistant, entry_id: str, options: Any) -> HaToolDispatcher | None:
    """Device options -> dispatcher; neither option set -> None (tools off)."""
    identity = _identity_from_option(options.get(CONF_CODIE_TOOL_BRIDGE))
    agent_id = options.get(CONF_CODIE_AGENT) or None
    if identity is None and agent_id is None:
        return None
    return HaToolDispatcher(hass, entry_id, identity, agent_id)


async def async_setup_tool_bridge(hass: HomeAssistant, entry: ConfigEntry, coordinator) -> None:
    """Apply the options now and whenever they change (no entry reload needed)."""

    def _state(source: ConfigEntry) -> tuple[str, str]:
        return (source.options.get(CONF_CODIE_TOOL_BRIDGE) or "", source.options.get(CONF_CODIE_AGENT) or "")

    applied = _state(entry)
    await coordinator.async_set_tool_dispatcher(dispatcher_from_options(hass, entry.entry_id, entry.options))

    async def _on_update(hass: HomeAssistant, updated: ConfigEntry) -> None:
        nonlocal applied
        new = _state(updated)
        if new != applied:
            applied = new
            await coordinator.async_set_tool_dispatcher(
                dispatcher_from_options(hass, updated.entry_id, updated.options)
            )

    entry.async_on_unload(entry.add_update_listener(_on_update))
