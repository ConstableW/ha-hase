"""Data update coordinator for CODE27.

Two update paths run side by side:
- A slow full poll (DataUpdateCoordinator's normal interval) that covers
  everything, including fields with no push equivalent (alarms, memos,
  system, light projects, character slots) - the reliable fallback.
- A realtime path: the device's own WebSocket channel pushes music/light-page
  state instantly (see websocket_client.py), and chat messages are polled
  separately on a much shorter interval than the main scan_interval, since
  CODE27 has no push event for new chat messages at all.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Code27AuthError, Code27Client, Code27ConnectionError, Code27Error
from ...const import (DOMAIN)
from .const import (CONF_REMOTE_RECOVERY_ENABLED, DEFAULT_AMBIENT_BRIGHTNESS, DEFAULT_AMBIENT_RGB, LIGHT_EFFECT_STATIC, LIGHT_EFFECT_TO_TYPE)
from .websocket_client import Code27WebSocket

_LOGGER = logging.getLogger(__name__)

EVENT_USER_MESSAGE = f"{DOMAIN}_user_message"
EVENT_ASSISTANT_MESSAGE = f"{DOMAIN}_assistant_message"

CHAT_POLL_INTERVAL = timedelta(seconds=3)


@dataclass
class Code27Data:
    """Snapshot of everything the integration polls."""

    system: dict[str, Any] = field(default_factory=dict)
    music_page: dict[str, Any] = field(default_factory=dict)
    music_source: dict[str, Any] = field(default_factory=dict)
    music_playback: dict[str, Any] = field(default_factory=dict)
    music_audio_route: dict[str, Any] = field(default_factory=dict)
    bluetooth_state: dict[str, Any] = field(default_factory=dict)
    # No confirmed REST endpoint exists for this yet (only the WebSocket
    # push topic is live-verified) - populated solely via `_handle_ws_*`,
    # so it stays empty until the first push after connect.
    music_storage: dict[str, Any] = field(default_factory=dict)
    light_master_switch: dict[str, Any] = field(default_factory=dict)
    light_projects: list[dict[str, Any]] = field(default_factory=list)
    applied_light_project: dict[str, Any] = field(default_factory=dict)
    alarms: list[dict[str, Any]] = field(default_factory=list)
    memos: list[dict[str, Any]] = field(default_factory=list)
    character_slots: list[dict[str, Any]] = field(default_factory=list)
    current_slot: dict[str, Any] = field(default_factory=dict)
    # Per-character last-message snapshot for a "show every character, not
    # just the active one" card - built from history/count, entirely
    # separate from the event pipeline below (last_user_message/
    # last_assistant_message/chat_messages), which stays the only source
    # of EVENT_USER_MESSAGE/EVENT_ASSISTANT_MESSAGE.
    character_overview: list[dict[str, Any]] = field(default_factory=list)
    last_user_message: dict[str, Any] = field(default_factory=dict)
    last_assistant_message: dict[str, Any] = field(default_factory=dict)
    chat_messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PendingCorrection:
    """An open "Meintest du...?" entity-correction dialog for this
    device's Assist bridge (see bridge.py). Lives only in memory on the
    coordinator - lost on reload/restart, which is fine, a stale
    multi-turn dialog shouldn't survive that anyway. At most one open
    dialog per device at a time."""

    original_command: str
    failed_phrase: str
    tried_candidates: list[str] = field(default_factory=list)
    last_suggested: str | None = None
    catchphrase: str | None = None
    include_areas: bool = False
    entity_id: str | None = None
    slot: int | None = None
    language: str | None = None
    created_at: float = field(default_factory=time.monotonic)
    # Set instead of the fields above when this dialog is a spellbook
    # "which one did you mean" choice (see bridge.py's
    # _begin_spell_choice_correction) rather than an entity/area name
    # correction - each is (resolved_command, catchphrase, trigger_text).
    spell_option_a: tuple[str, str | None, str] | None = None
    spell_option_b: tuple[str, str | None, str] | None = None

    def is_expired(self, timeout_seconds: float) -> bool:
        return time.monotonic() - self.created_at > timeout_seconds

    def touch(self) -> None:
        """Reset the idle timer - called on every reply the dialog
        actually consumes, even one that didn't resolve to a candidate,
        since replying at all means the user is still engaged."""
        self.created_at = time.monotonic()


class Code27Coordinator(DataUpdateCoordinator[Code27Data]):
    """Polls the CODE27 device on a fixed interval, plus realtime push."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: Code27Client, scan_interval: int
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="CODE27",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.entry = entry
        self.client = client
        self.pending_correction: PendingCorrection | None = None
        self._chat_seen: dict[int, set[str]] = {}
        self._chat_dates: dict[int, str | None] = {}
        self._chat_last_ids: dict[int, str | None] = {}
        self._chat_active_slot: int | None = None
        self._chat_lock = asyncio.Lock()
        self.bridge_lock = asyncio.Lock()
        self._notification_lock = asyncio.Lock()
        self._music_revisions: dict[str, int] = {}
        self._owned_tasks: set[asyncio.Task] = set()
        self._poll_task: asyncio.Task | None = None
        self.stopping = False
        self._own_chat_ids: set[tuple[int, str]] = set()
        self._pending_chat_replies: list[tuple[int, str]] = []
        self._websocket: Code27WebSocket | None = None
        self._chat_poll_unsub: Any = None

        # Notification ("Hinweis") state: CODE27 has no concept of this
        # itself, it's purely something we layer on top using the same LED
        # preview mechanism the "Umgebungsfarbe" light uses.
        self.notification_active: bool = False
        self.notification_message: str | None = None
        self.notification_color: tuple[int, int, int] = (255, 0, 0)
        self._pre_notification_light_enabled: bool | None = None

        # What HA itself last asked the ambient-light preview channel to
        # show - CODE27 has no readback for that, so this is the only
        # record of it, shared with light.py so a notification's blink can
        # restore it afterwards instead of just stopping.
        self.ambient_rgb_color: tuple[int, int, int] = DEFAULT_AMBIENT_RGB
        self.ambient_brightness: int = DEFAULT_AMBIENT_BRIGHTNESS
        self.ambient_effect: str = LIGHT_EFFECT_STATIC

    def _led_count(self) -> int:
        for project in self.data.light_projects if self.data else []:
            if project.get("ledCount"):
                return int(project["ledCount"])
        return 17

    async def async_trigger_notification(
        self, message: str | None, color: tuple[int, int, int]
    ) -> None:
        async with self._notification_lock:
            was_enabled = self._pre_notification_light_enabled
            if was_enabled is None:
                was_enabled = bool(self.data and self.data.light_master_switch.get("enabled"))
                # Preserve the original state even if a later write fails.
                self._pre_notification_light_enabled = was_enabled
            if self.data and not self.data.light_master_switch.get("enabled"):
                await self.client.set_light_master_switch(True)
            red, green, blue = color
            await self.client.preview_light_color(
                red, green, blue, brightness=255, effect="BLINK", led_count=self._led_count()
            )
            self.notification_active = True
            self.notification_message = message
            self.notification_color = color
            self.async_update_listeners()

    async def async_acknowledge_notification(self) -> None:
        """Commit local acknowledgement only after restoration succeeds."""
        async with self._notification_lock:
            if not self.notification_active and self._pre_notification_light_enabled is None:
                return
            if self._pre_notification_light_enabled:
                red, green, blue = self.ambient_rgb_color
                await self.client.preview_light_color(
                    red, green, blue, brightness=self.ambient_brightness,
                    effect=LIGHT_EFFECT_TO_TYPE[self.ambient_effect], led_count=self._led_count(),
                )
            else:
                await self.client.stop_light_preview()
                await self.client.set_light_master_switch(False)
            self.notification_active = False
            self.notification_message = None
            self._pre_notification_light_enabled = None
            self.async_update_listeners()

    def _poll_once(self):
        """The full-poll's reads, gathered - factored out so a connection
        failure can retry exactly this (side-effect-free) call after IP
        recovery, without duplicating the field list."""
        return asyncio.gather(
            self.client.get_system(),
            self.client.get_music_page(),
            self.client.get_music_source(),
            self.client.get_music_playback(),
            self.client.get_music_audio_route(),
            self.client.get_bluetooth_state(),
            self.client.get_light_master_switch(),
            self.client.list_light_projects(),
            self.client.get_applied_light_project(),
            self.client.list_alarms(),
            self.client.list_memos(),
            self.client.list_character_settings(),
            self.client.get_current_slot(),
            self.client.get_chat_history_count(),
        )

    async def _recover_and_retry_poll(self, original_err: Code27ConnectionError):
        """Phase 8: on a LAN connection failure, optionally try the cloud
        relay's IP recovery and retry this one poll before giving up.

        Opt-in via CONF_REMOTE_RECOVERY_ENABLED (off by default - this
        routes the device's identity through a third-party cloud we don't
        control). Only ever applied to this read-only poll, never to a
        write - see Code27Client.attempt_remote_recovery's own docstring
        for why blindly retrying a write after an ambiguous timeout would
        risk a duplicate action.
        """
        if not self.entry.options.get(CONF_REMOTE_RECOVERY_ENABLED, False):
            raise UpdateFailed(f"Cannot reach CODE27 device: {original_err}") from original_err
        new_host = await self.client.attempt_remote_recovery()
        if new_host is None:
            raise UpdateFailed(f"Cannot reach CODE27 device: {original_err}") from original_err
        _LOGGER.info(
            "CODE27 device recovered via remote IP lookup, updating stored host to %s",
            new_host,
        )
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, CONF_HOST: new_host}
        )
        try:
            return await self._poll_once()
        except Code27Error as err:
            raise UpdateFailed(
                f"Cannot reach CODE27 device after IP recovery: {err}"
            ) from err

    async def _async_update_data(self) -> Code27Data:
        revisions = dict(self._music_revisions)
        try:
            results = await self._poll_once()
        except Code27AuthError as err:
            raise ConfigEntryAuthFailed(err.code or "auth_error") from err
        except Code27ConnectionError as err:
            results = await self._recover_and_retry_poll(err)
        except Code27Error as err:
            raise UpdateFailed(str(err)) from err

        (
            system,
            music_page,
            music_source,
            music_playback,
            music_audio_route,
            bluetooth_state,
            light_master_switch,
            light_projects,
            applied_light_project,
            alarms,
            memos,
            character_slots,
            current_slot,
            history_counts,
        ) = results

        # Chat is polled on its own, much faster, timer - reuse whatever it
        # last saw instead of re-fetching (or wiping!) it here too.
        last_user_message = self.data.last_user_message if self.data else {}
        last_assistant_message = self.data.last_assistant_message if self.data else {}
        chat_messages = self.data.chat_messages if self.data else []
        # No REST endpoint is confirmed for this - it only ever arrives via
        # the WebSocket push, so each full poll just carries forward
        # whatever the last push delivered instead of resetting it to {}.
        music_storage = self.data.music_storage if self.data else {}

        snapshot = Code27Data(
            system=system,
            music_page=music_page,
            music_source=music_source,
            music_playback=music_playback,
            music_audio_route=music_audio_route,
            bluetooth_state=bluetooth_state,
            music_storage=music_storage,
            light_master_switch=light_master_switch,
            light_projects=light_projects,
            applied_light_project=applied_light_project,
            alarms=alarms,
            memos=memos,
            character_slots=character_slots,
            current_slot=current_slot,
            character_overview=self._build_character_overview(
                character_slots, history_counts, current_slot
            ),
            last_user_message=last_user_message,
            last_assistant_message=last_assistant_message,
            chat_messages=chat_messages,
        )
        if self.data:
            for field in ("music_page", "music_source", "music_playback",
                          "music_audio_route", "bluetooth_state"):
                current = getattr(self.data, field)
                incoming = getattr(snapshot, field)
                if (self._music_revisions.get(field, 0) != revisions.get(field, 0)
                        or self._music_is_older(incoming, current)):
                    setattr(snapshot, field, current)
        return snapshot

    @staticmethod
    def _build_character_overview(
        character_slots: list[dict[str, Any]],
        history_counts: list[dict[str, Any]],
        current_slot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """One entry per character slot with its last message, for a
        "show every character" card - see `Code27Data.character_overview`."""
        by_slot = {
            entry.get("slot"): entry
            for entry in history_counts
            if isinstance(entry, dict) and entry.get("slot") is not None
        }
        active_slot = current_slot.get("slot_number")
        overview = []
        for character in character_slots:
            slot_number = character.get("slot_number")
            messages = (by_slot.get(slot_number) or {}).get("messages") or []
            last = messages[-1] if messages else {}
            overview.append({
                "slot_number": slot_number,
                "name": character.get("name"),
                "active": slot_number is not None and slot_number == active_slot,
                "last_message": last.get("content"),
                "last_role": last.get("role"),
                "last_created_at": last.get("created_at"),
            })
        return overview

    @staticmethod
    def _music_is_older(incoming: dict, current: dict) -> bool:
        # Prefer timestamps so a newer state can reset the version counter.
        for key in ("updated_at_ms", "version"):
            left, right = incoming.get(key), current.get(key)
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                if left != right:
                    return left < right
        return False

    def _handle_music_push(self, field: str, data: dict) -> None:
        if not self.data or self.stopping or not isinstance(data, dict):
            return
        current = getattr(self.data, field)
        if data == current or self._music_is_older(data, current):
            return
        setattr(self.data, field, data)
        self._music_revisions[field] = self._music_revisions.get(field, 0) + 1
        # A partial push neither reschedules the full poll nor repairs its
        # error state (alarms/memos/system may still be unreachable).
        self.async_update_listeners()

    # -- realtime: WebSocket push for music/light-page state -----------------

    def async_start_realtime(self) -> None:
        """Start the WebSocket push channel and the fast chat poller."""
        self._websocket = Code27WebSocket(self.hass, self.client)
        self._websocket.on("music_page", self._handle_ws_music_page)
        self._websocket.on("music_source", self._handle_ws_music_source)
        self._websocket.on("music_playback", self._handle_ws_music_playback)
        self._websocket.on("music_audio_route", self._handle_ws_music_audio_route)
        self._websocket.on("bluetooth_state", self._handle_ws_bluetooth_state)
        self._websocket.on("music_storage", self._handle_ws_music_storage)
        self._websocket.start()

        self._chat_poll_unsub = async_track_time_interval(
            self.hass, self._async_poll_chat, CHAT_POLL_INTERVAL
        )

    def async_create_owned_task(self, coroutine: Any, name: str) -> None:
        """Keep work cancellable even when its event listener is removed."""
        if self.stopping:
            coroutine.close()
            return
        task = self.hass.async_create_task(coroutine, name=name)
        self._owned_tasks.add(task)
        task.add_done_callback(self._owned_tasks.discard)

    async def async_send_chat_reply(
        self, text: str, slot: int, language: str | None = None
    ) -> str | None:
        """Register our output before polling can observe it as user input.

        Returns the written message's `chat_id` (or None on failure) so a
        caller that wants it cleaned up later (see
        `async_cleanup_assist_exchange`) can track it - the device itself
        still reacts to this write with its own new chat turn, exactly
        like any other message (see CONF_BRIDGE_RELAY_RESPONSE's
        strings.json description)."""
        async with self._chat_lock:
            if self.stopping:
                return None
            pending = (slot, text)
            self._pending_chat_replies.append(pending)
            # Keep the reservation on ambiguous failure: the device may have
            # accepted the write even if its acknowledgement never reached us.
            chat_id = await self.client.send_chat_text(text, slot, language)
            if chat_id is not None:
                self._own_chat_ids.add((slot, chat_id))
                self._pending_chat_replies.remove(pending)
            return chat_id

    async def async_cleanup_assist_exchange(
        self,
        slot: int,
        seed_chat_ids: list[str],
        timeout: float = 9.0,
        poll_interval: float = 1.5,
    ) -> int:
        """Remove an Assist-bridge command exchange from the device's own
        chat history (both on-device and CODE27's cloud copy - see
        `Code27Client.delete_chat_messages`) once it's played out, so
        routine smart-home chatter never lands in the character's
        long-term memory/context (CONF_BRIDGE_HISTORY_CLEANUP_ENABLED).

        `seed_chat_ids` is the triggering command's own `chat_id` (always
        present the moment EVENT_USER_MESSAGE fires) plus, if
        CONF_BRIDGE_RELAY_RESPONSE also wrote into the chat, those
        `chat_id`s too - the device reacts to those the same way (see
        that option's strings.json description). Each seed is deleted by
        `chat_id` directly (see `Code27Client.delete_chat_messages` for
        why that's the only identifier that reliably works, unlike
        `history_id`). For each seed, this also looks for the single
        chronologically-next assistant message - the device's own
        spontaneous in-character reaction, if any - and deletes that too;
        it has no id we're handed in advance, so has to be found by
        polling. Confirmed live: a recognized Assist-bridge command
        (spellbook or codeword, spoken or typed) never actually gets one
        of these - CODE27 classifies it as a command, not conversation -
        so in practice this poll loop usually just runs out its full
        `timeout` finding nothing further to delete beyond the seeds
        themselves, which is normal, not a failure. Best-effort and
        conservative - only pins a reply to its seed by strict "next
        after" ordering, and simply times out (leaving anything
        unresolved in place) rather than guess, e.g. if an unrelated
        message arrives in the same window. Returns how many messages
        were actually deleted, purely for logging.
        """
        seed_chat_ids = list(dict.fromkeys(cid for cid in seed_chat_ids if cid))
        if not seed_chat_ids:
            return 0
        reply_chat_ids: set[str] = set()
        seed_created_at: dict[str, str] = {}
        found_replies: set[str] = set()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(poll_interval)
            try:
                messages = await self.client.get_latest_chat_messages(slot=slot, limit=20)
            except Code27Error as err:
                _LOGGER.debug("Assist-history cleanup: poll failed, retrying: %s", err)
                continue

            by_chat_id = {str(m.get("chat_id")): m for m in messages}
            for cid in seed_chat_ids:
                if cid not in seed_created_at:
                    message = by_chat_id.get(cid)
                    if message is not None:
                        seed_created_at[cid] = str(message.get("created_at") or "")

            for cid, created_at in seed_created_at.items():
                if cid in found_replies:
                    continue
                reply = self._next_assistant_reply(messages, created_at)
                if reply is not None:
                    reply_chat_id = reply.get("chat_id")
                    if reply_chat_id:
                        reply_chat_ids.add(str(reply_chat_id))
                    found_replies.add(cid)

            if len(seed_created_at) == len(seed_chat_ids) and len(found_replies) == len(seed_chat_ids):
                break

        all_chat_ids = sorted(set(seed_chat_ids) | reply_chat_ids)
        try:
            await self.client.delete_chat_messages(all_chat_ids)
        except Code27Error as err:
            # Confirmed live: the device can reject an otherwise-valid
            # delete outright (generic device-side failure, not "message
            # not found") - a single retry after a short backoff is cheap
            # insurance against that kind of one-off hiccup.
            _LOGGER.debug("Assist-history cleanup: delete failed, retrying once: %s", err)
            await asyncio.sleep(2.0)
            try:
                await self.client.delete_chat_messages(all_chat_ids)
            except Code27Error as retry_err:
                _LOGGER.warning("Could not clean up Assist chat exchange: %s", retry_err)
                raise
        return len(all_chat_ids)

    @staticmethod
    def _next_assistant_reply(
        messages: list[dict[str, Any]], after_created_at: str | None
    ) -> dict[str, Any] | None:
        if not after_created_at:
            return None
        candidates = [
            m for m in messages
            if m.get("role") == "assistant" and str(m.get("created_at") or "") > after_created_at
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda m: str(m.get("created_at") or ""))

    async def async_stop_realtime(self) -> None:
        self.stopping = True
        if self._chat_poll_unsub is not None:
            self._chat_poll_unsub()
            self._chat_poll_unsub = None
        tasks = set(self._owned_tasks)
        if self._poll_task is not None:
            tasks.add(self._poll_task)
        tasks.discard(asyncio.current_task())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.pending_correction = None
        if self._websocket is not None:
            await self._websocket.stop()
            self._websocket = None

    def _handle_ws_music_page(self, data: dict[str, Any]) -> None:
        self._handle_music_push("music_page", data)

    def _handle_ws_music_source(self, data: dict[str, Any]) -> None:
        self._handle_music_push("music_source", data)

    def _handle_ws_music_playback(self, data: dict[str, Any]) -> None:
        self._handle_music_push("music_playback", data)

    def _handle_ws_music_audio_route(self, data: dict[str, Any]) -> None:
        self._handle_music_push("music_audio_route", data)

    def _handle_ws_bluetooth_state(self, data: dict[str, Any]) -> None:
        self._handle_music_push("bluetooth_state", data)

    def _handle_ws_music_storage(self, data: dict[str, Any]) -> None:
        self._handle_music_push("music_storage", data)

    # -- realtime-ish: fast, dedicated chat polling ---------------------------

    async def _async_poll_chat(self, _now: Any = None) -> None:
        if not self.data or self.stopping or self._chat_lock.locked():
            return
        self._poll_task = asyncio.current_task()
        try:
            async with self._chat_lock:
                slot = self.data.current_slot.get("slot_number", 1)
                switched = slot != self._chat_active_slot
                # An already initialized empty history has no date yet;
                # include all newly appearing days, even after an outage.
                since_date = None if switched else self._chat_dates.get(slot) or ""
                try:
                    date, messages = await self.client.get_chat_history_snapshot(
                        slot, since_date, None if switched else self._chat_last_ids.get(slot)
                    )
                except Code27Error as err:
                    _LOGGER.debug("Could not fetch chat history: %s", err)
                    return
                if self.stopping or slot != self.data.current_slot.get("slot_number", 1):
                    return  # a late response must not replace another character's state
                seen = self._chat_seen.setdefault(slot, set())
                baseline = switched or slot not in self._chat_dates
                self._chat_dates[slot] = date
                self._chat_last_ids[slot] = (
                    str(messages[-1]["chat_id"])
                    if messages and messages[-1].get("chat_id") is not None else None
                )
                self._chat_active_slot = slot
                if switched:
                    self.pending_correction = None
                for message in messages:
                    chat_id = message.get("chat_id")
                    if chat_id is None:
                        continue
                    chat_id = str(chat_id)
                    if chat_id in seen:
                        continue
                    seen.add(chat_id)
                    own = (slot, chat_id) in self._own_chat_ids
                    pending = (slot, message.get("content", ""))
                    if not own and message.get("role") == "user" and pending in self._pending_chat_replies:
                        self._pending_chat_replies.remove(pending)
                        self._own_chat_ids.add((slot, chat_id))
                        own = True
                    if baseline or own:
                        continue
                    event_type = {
                        "user": EVENT_USER_MESSAGE,
                        "assistant": EVENT_ASSISTANT_MESSAGE,
                    }.get(message.get("role"))
                    if event_type:
                        self.hass.bus.async_fire(event_type, {
                            "config_entry_id": self.entry.entry_id,
                            "text": message.get("content", ""),
                            "chat_id": chat_id,
                            "history_id": message.get("history_id"),
                            "slot": slot,
                            "language": self.data.system.get("ai_language"),
                            "created_at": message.get("created_at"),
                        })
                self.data.last_user_message = next(
                    (m for m in reversed(messages) if m.get("role") == "user"), {}
                )
                self.data.last_assistant_message = next(
                    (m for m in reversed(messages) if m.get("role") == "assistant"), {}
                )
                self.data.chat_messages = messages[-30:]
                self.async_update_listeners()
        finally:
            self._poll_task = None
