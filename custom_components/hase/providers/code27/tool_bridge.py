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
from homeassistant.helpers import device_registry as dr

from ...const import CODIE_AREA_LIMITS_KEY, DOMAIN
from ..mcp.credentials import BridgeIdentity
from ..mcp.local_dispatch import async_cast_spell, async_list_spells
from .const import (
    CONF_CODIE_AGENT,
    CONF_CODIE_ALLOWED_AREAS,
    CONF_CODIE_TOOL_BRIDGE,
    CONF_CODIE_UNRESTRICTED,
    CONF_MACHINE_ID,
)

_COMMAND_TIMEOUT_SEC = 15.0


class HaToolDispatcher:
    """ToolDispatcher over an optional MCP Bridge and an optional agent."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        identity: BridgeIdentity | None,
        agent_id: str | None,
        area_limit: frozenset[str] | None = None,
    ) -> None:
        self._hass = hass
        self._identity = identity
        self._entry_id = entry_id
        self._agent_id = agent_id
        self._area_limit = area_limit  # None = no limit beyond the agent's own
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

    def _device_id(self) -> str | None:
        """HA device id of this CODE27: its HA area is the sentence's
        default area ("Licht an" -> the room Codie stands in). Resolved per
        call, so moving the device to another area takes effect at once; a
        device without an area simply gives no default."""
        entry = self._hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return None
        owner = self._hass.config_entries.async_get_entry(entry.data.get("code27_entry_id") or "")
        if owner is not None and owner.domain == "code27":
            domain, source = "code27", owner
        else:
            domain, source = DOMAIN, entry
        machine_id = source.data.get(CONF_MACHINE_ID, source.entry_id)
        device = dr.async_get(self._hass).async_get_device(identifiers={(domain, machine_id)})
        return device.id if device is not None else None

    async def _converse(self, text: str, agent_id: str | None, conversation_id: str | None):
        device_id = self._device_id()
        # The agent (conversation.py) narrows its own area policy by this
        # limit for sentences that come from this device.
        limits = self._hass.data.setdefault(CODIE_AREA_LIMITS_KEY, {})
        if device_id is not None:
            if self._area_limit is None:
                limits.pop(device_id, None)
            else:
                limits[device_id] = self._area_limit
        async with asyncio.timeout(_COMMAND_TIMEOUT_SEC):
            return await conversation.async_converse(
                self._hass, text=text, conversation_id=conversation_id,
                context=Context(), agent_id=agent_id,
                language=self._hass.config.language or "de",
                device_id=device_id,
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
    limit = None
    if not options.get(CONF_CODIE_UNRESTRICTED, True):
        limit = frozenset(options.get(CONF_CODIE_ALLOWED_AREAS) or [])  # empty = no area at all
    return HaToolDispatcher(hass, entry_id, identity, agent_id, limit)


async def async_setup_tool_bridge(hass: HomeAssistant, entry: ConfigEntry, coordinator) -> None:
    """Apply the options now and whenever they change (no entry reload needed)."""

    def _state(source: ConfigEntry) -> tuple:
        return (
            source.options.get(CONF_CODIE_TOOL_BRIDGE) or "",
            source.options.get(CONF_CODIE_AGENT) or "",
            bool(source.options.get(CONF_CODIE_UNRESTRICTED, True)),
            tuple(sorted(source.options.get(CONF_CODIE_ALLOWED_AREAS) or [])),
        )

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
