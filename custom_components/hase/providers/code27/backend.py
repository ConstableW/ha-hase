"""Code27ConversationBackend - lets a SATELLITE-mode spellbooker agent
fall back to a real CODE27 device's own chat/persona for anything its
local spellbook doesn't recognize.

Deliberately reuses the EXISTING, already-proven chat mechanism in
coordinator.py (async_send_chat_reply + EVENT_ASSISTANT_MESSAGE) -
not a new invented protocol. That reuse is also this backend's real
limit: coordinator._async_poll_chat only tracks ONE active slot at a
time (self.data.current_slot) and resets pending_correction / treats
messages as baseline the moment the active slot changes. This backend
therefore refuses (status="unavailable") rather than send to a slot
that isn't currently active - it will NOT switch slots on the
device's behalf, since that could disrupt whatever the device owner
is doing.

CODE27's API has no proven structured-action protocol
(docs/spellbook-engine/stage-1/backend-readiness.md: "Kein
implementierter Endpunkt/Parser"), so this backend always returns
proposals=() - conversation-only, honestly. Correlation itself
(matching the right reply to the right request) is UNVERIFIED against
concurrent physical chat use on the device - see stage-1's own
"Minimale nächste Fähigkeitsprüfung vor Etappe 6", which was never
run live. Available through the satellite subentry flow and explicitly marked experimental.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ...core.models import BackendResult
from ...core.session import ConversationScope

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import Code27Coordinator

_LOGGER = logging.getLogger(__name__)

# How long to wait for CODE27's own chat reply before giving up and
# reporting the backend unavailable. Generous - a slow character
# response shouldn't look identical to a genuinely broken device, but
# a voice frontend waiting minutes for a reply isn't acceptable either.
_REPLY_TIMEOUT_SECONDS = 20.0

# Keyed by coordinator config-entry id, not per Code27ConversationBackend
# instance: CODE27's chat protocol carries no request-correlation id of
# its own (backend-readiness.md - "kein implementierter Endpunkt/Parser"),
# so the ONLY thing that stops two overlapping requests for the same
# device from each other's replies is never having two in flight for
# that device at once. A per-instance lock only guarantees that for a
# single backend object; two spellbooker entries (or a spellbooker
# entry and anything else) that both talk to the SAME coordinator would
# otherwise each get their own lock and could still race on the same
# EVENT_ASSISTANT_MESSAGE stream. Never cleaned up - the number of
# distinct CODE27 devices a running instance talks to is small and
# stable, unlike conversation sessions.
_device_locks: dict[str, asyncio.Lock] = {}


def _lock_for_device(entry_id: str) -> asyncio.Lock:
    lock = _device_locks.get(entry_id)
    if lock is None:
        lock = asyncio.Lock()
        _device_locks[entry_id] = lock
    return lock


class Code27ConversationBackend:
    def __init__(
        self, hass: "HomeAssistant", coordinator: "Code27Coordinator", source_area: str | None = None
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._source_area = source_area
        self._lock = _lock_for_device(coordinator.entry.entry_id)

    def _with_area(self, text: str, language: str) -> str:
        """Tell the character where the speaker is (the area of the HA device
        the sentence came from); none known = the sentence unchanged."""
        if not self._source_area:
            return text
        label = "Bereich" if (language or "").lower().startswith("de") else "Area"
        return f"[{label}: {self._source_area}] {text}"

    async def converse(self, text: str, language: str, scope: ConversationScope) -> BackendResult:
        from homeassistant.core import callback

        from . import coordinator as coordinator_module

        try:
            slot = int(scope.profile_id)
        except (TypeError, ValueError):
            # Not "fall back to whatever's currently active" - an
            # unparseable profile_id is a configuration error. Silently
            # routing to the current slot could send someone's words to
            # a completely different character than the one they
            # configured this agent to talk to.
            _LOGGER.warning(
                "spellbooker backend: profile_id %r is not a valid slot number, refusing", scope.profile_id
            )
            return BackendResult(status="unavailable", response_text=None)

        async with self._lock:
            current_slot = self._coordinator.data.current_slot.get("slot_number") if self._coordinator.data else None
            if current_slot is not None and slot != current_slot:
                _LOGGER.debug(
                    "spellbooker backend: profile slot %s is not the active slot (%s), refusing rather than switching",
                    slot, current_slot,
                )
                return BackendResult(status="unavailable", response_text=None)
            target_slot = slot

            reply: str | None = None
            got_reply = asyncio.Event()

            @callback
            def _on_assistant_message(event) -> None:
                nonlocal reply
                if event.data.get("config_entry_id") != self._coordinator.entry.entry_id:
                    return
                if event.data.get("slot") != target_slot:
                    return
                reply = event.data.get("text")
                got_reply.set()

            unsub = self._hass.bus.async_listen(
                coordinator_module.EVENT_ASSISTANT_MESSAGE, _on_assistant_message
            )
            try:
                await self._coordinator.async_send_chat_reply(
                    self._with_area(text, language), target_slot
                )
                try:
                    await asyncio.wait_for(got_reply.wait(), timeout=_REPLY_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    return BackendResult(status="unavailable", response_text=None)
            except Exception as err:  # noqa: BLE001 - never let a device hiccup crash the turn
                _LOGGER.warning("spellbooker backend: CODE27 chat request failed: %s", err)
                return BackendResult(status="error", response_text=None)
            finally:
                unsub()

        return BackendResult(status="reply", response_text=reply, proposals=())
