"""Speak a fixed text on the device (`tts/utterances`, firmware >= 1.5.3).

Shared CODE27 device code: used by the `speak` / `notify` services, by a
HASE binding to a standalone-owned device (through the external hub) and by
automations. The character speaks in its own voice; nothing touches the chat
or the long-term context.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .api import Code27Error

_LOGGER = logging.getLogger(__name__)
_UTTERANCE_DONE = {"completed", "failed", "cancelled"}
_POLL_INTERVAL = 0.35
_TIMEOUT = 60.0  # the device itself gives up after 180 s; long texts take a while


async def async_speak_text(hass, entry, coordinator, text, entity_id=None, *, expected_slot=None):
    # A coordinator-like owner may provide its own speech (HASE bound to a
    # device a standalone CODE27 entry owns).
    external_speak = getattr(coordinator, "async_speak_text", None)
    if callable(external_speak):
        try:
            await external_speak(text, expected_slot=expected_slot)
        except Exception as err:  # noqa: BLE001 - keep a foreign device failure local
            _LOGGER.warning("External CODE27 speech failed: %s", err)
        return
    if coordinator.stopping or not coordinator.data:
        return
    slot = coordinator.data.current_slot.get("slot_number", 1)
    if expected_slot is not None:
        # Speech has no slot-addressed endpoint: never speak an old request
        # through a newly selected character.
        try:
            active = await coordinator.client.get_current_slot()
        except Code27Error as err:
            _LOGGER.debug("Cannot verify character for speech: %s", err)
            return
        if not isinstance(active, dict) or active.get("slot_number") != expected_slot or coordinator.stopping:
            return
        slot = expected_slot
    try:
        utterance = await coordinator.client.speak_text(text, slot)
    except Code27Error as err:
        _LOGGER.warning("Could not start CODE27 speech: %s", err)
        return
    if not isinstance(utterance, dict):
        return
    coordinator.data.last_tts_message = {
        "content": text,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    coordinator.async_update_listeners()
    utterance_id = utterance.get("utterance_id")
    state = str(utterance.get("state") or "").lower()
    try:
        async with asyncio.timeout(_TIMEOUT):
            while utterance_id and not coordinator.stopping and state not in _UTTERANCE_DONE:
                await asyncio.sleep(_POLL_INTERVAL)
                try:
                    state = str(
                        (await coordinator.client.get_utterance(str(utterance_id))).get("state") or ""
                    ).lower()
                except Code27Error as err:  # a missed status poll does not undo accepted speech
                    _LOGGER.debug("CODE27 speech status poll failed: %s", err)
    except TimeoutError:
        _LOGGER.debug("CODE27 speech completion not confirmed before deadline")
