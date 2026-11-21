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
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ...const import DOMAIN
from .api import (
    Code27AuthError,
    Code27Client,
    Code27ConnectionError,
    Code27Error,
    Code27RequestExpiredError,
)
from .const import (
    CONF_REMOTE_RECOVERY_ENABLED,
    DEFAULT_AMBIENT_BRIGHTNESS,
    DEFAULT_AMBIENT_RGB,
    LIGHT_EFFECT_STATIC,
    LIGHT_EFFECT_TO_TYPE,
)
from .music import merge_music_inventory, usable_music_storage
from .repairs import sync_music_mode_recovery_issue
from .websocket_client import Code27WebSocket

_LOGGER = logging.getLogger(__name__)

EVENT_USER_MESSAGE = f"{DOMAIN}_user_message"
EVENT_ASSISTANT_MESSAGE = f"{DOMAIN}_assistant_message"

CHAT_POLL_INTERVAL = timedelta(seconds=3)
_CHAT_RESERVATION_TTL_SEC = 30.0
_CHAT_DEDUPE_TTL_SEC = 7 * 24 * 60 * 60
_CHAT_DEDUPE_MAX_IDS = 2000

# After a period of no new chat events, reduce polling to save bandwidth (C7).
# Each entry is (idle_seconds_threshold, effective_poll_interval_seconds).
# Checked in order; the first matching threshold applies.
_CHAT_BACKOFF_THRESHOLDS: list[tuple[float, float]] = [
    (15 * 60, 30.0),  # 15+ min idle → poll every 30 s
    (5 * 60, 15.0),   # 5+ min idle → poll every 15 s
]


@dataclass
class Code27Data:
    """Snapshot of everything the integration polls."""

    system: dict[str, Any] = field(default_factory=dict)
    music_page: dict[str, Any] = field(default_factory=dict)
    music_source: dict[str, Any] = field(default_factory=dict)
    music_playback: dict[str, Any] = field(default_factory=dict)
    music_audio_route: dict[str, Any] = field(default_factory=dict)
    bluetooth_state: dict[str, Any] = field(default_factory=dict)
    # TF-card inventory. Two sources resolve into this one field: the
    # `music_storage` WebSocket topic and the REST read of
    # `GET /data/user/music/storage` (see `_poll_music_storage`). The poll
    # merge guarantees that a payload without an inventory never replaces a
    # valid one, so an unrelated update cannot empty the media browser.
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
    # The last text handed to the device's TTS preview endpoint via
    # async_speak_text (hase.speak / the Assist "speak the reply" option) -
    # "Never touches the chat" (see async_speak_text's own docstring), so
    # it is genuinely a separate value from last_assistant_message above,
    # not a duplicate of it (E2E-05: this path had no observable output at
    # all before). Set directly by async_speak_text, carried forward
    # across regular polls exactly like last_user_message/
    # last_assistant_message are.
    last_tts_message: dict[str, Any] = field(default_factory=dict)
    # Proactive-vision config (C27-063, read-only diagnostic, P3-3).
    proactive_config: dict[str, Any] = field(default_factory=dict)
    # Firmware 1.5.3: screen/lock/dialog-gate state and the dialog state.
    # Both stay empty on older firmware.
    device_state: dict[str, Any] = field(default_factory=dict)
    conversation_state: dict[str, Any] = field(default_factory=dict)
    # 1.6.0 GET device/brightness: {"level": 0-100|None, "writable": bool}
    device_brightness: dict[str, Any] = field(default_factory=dict)
    poll_errors: dict[str, str] = field(default_factory=dict)


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
        self._chat_seen: dict[int, set[str]] = {}
        self._chat_seen_at: dict[tuple[int, str], float] = {}
        self._chat_dates: dict[int, str | None] = {}
        self._chat_last_ids: dict[int, str | None] = {}
        self._chat_active_slot: int | None = None
        self._chat_lock = asyncio.Lock()
        self._notification_lock = asyncio.Lock()
        self._music_revisions: dict[str, int] = {}
        self._owned_tasks: set[asyncio.Task] = set()
        self._poll_task: asyncio.Task | None = None
        self.stopping = False
        self._own_chat_ids: set[tuple[int, str]] = set()
        self._own_chat_ids_at: dict[tuple[int, str], float] = {}
        # Each entry: (slot, text, deadline). Expiry is reconciled against
        # history first; an outage must not turn our output into a command.
        self._pending_chat_replies: list[tuple[int, str, float]] = []
        self._chat_write_deadlines: dict[tuple[int, str, float], float] = {}
        # Idle-backoff tracking for the chat poller (C7).
        self._chat_last_event_at: float = time.monotonic()
        self._chat_last_polled_at: float = 0.0
        # E2: cleared on first successful poll to stop any LED notification
        # that was blinking before HA restarted (HA's in-memory flag resets
        # to False on restart, but the device LED keeps blinking).
        self._startup_cleanup_done: bool = False
        self._poll_values: dict[str, Any] = {}
        self._websocket: Code27WebSocket | None = None
        self._tool_executor: Any = None
        self._events_ws: Code27WebSocket | None = None
        self._chat_poll_unsub: Any = None

        # Notification ("Hinweis") state: CODE27 has no concept of this
        # itself, it's purely something we layer on top using the same LED
        # preview mechanism the "Umgebungsfarbe" light uses.
        self.notification_active: bool = False
        self.notification_message: str | None = None
        # True from an outfit switch started here until the device reports
        # the character model loaded again (see async_set_outfit).
        self.model_loading: bool = False
        self.notification_color: tuple[int, int, int] = (255, 0, 0)
        self._pre_notification_light_enabled: bool | None = None

        # What HA itself last asked the ambient-light preview channel to
        # show - CODE27 has no readback for that, so this is the only
        # record of it, shared with light.py so a notification's blink can
        # restore it afterwards instead of just stopping.
        self.ambient_rgb_color: tuple[int, int, int] = DEFAULT_AMBIENT_RGB
        self.ambient_brightness: int = DEFAULT_AMBIENT_BRIGHTNESS
        self.ambient_effect: str = LIGHT_EFFECT_STATIC
        # True while the ambient colour written from here is what the LEDs show.
        # It sits on top of the applied light scene (project), so the scene
        # picker must not claim that scene is what is lit. Cleared when a scene
        # is applied or the preview is stopped. Unknown (False) after a restart.
        self.ambient_override: bool = False

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
                self.ambient_override = True
            else:
                await self.client.stop_light_preview()
                await self.client.set_light_master_switch(False)
                self.ambient_override = False
            self.notification_active = False
            self.notification_message = None
            self._pre_notification_light_enabled = None
            self.async_update_listeners()

    def _poll_once(self):
        """The full-poll's reads, gathered - factored out so a connection
        failure can retry exactly this (side-effect-free) call after IP
        recovery, without duplicating the field list.

        ``_poll_music_storage`` is the one read that swallows its own failures
        (see its docstring): the TF-card inventory is an optional extra source
        and must never be able to fail the whole poll.

        ``return_exceptions=True`` is used so that one failing sub-request
        doesn't immediately cancel the others.  Call
        ``_check_poll_results`` on the returned list to re-raise any fatal
        exception in priority order (S2).
        """
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
            self._poll_proactive_config(),
            self._poll_optional_state("get_device_state"),
            self._poll_optional_state("get_conversation_state"),
            self._poll_optional_state("get_device_brightness"),
            self._poll_music_storage(),
            return_exceptions=True,
        )

    @staticmethod
    def _check_poll_results(results: list[Any]) -> list[Any]:
        """Scan gather results for exceptions and re-raise the most severe one.

        Auth errors and system connectivity failures are fatal.
        Optional endpoint errors are retained for per-field recovery.
        All authentication errors, including the storage endpoint, propagate.
        """
        # Credentials are global, even when only an optional endpoint fails.
        # A clock error must not hide a real credential failure elsewhere.
        for error_type in (Code27AuthError, Code27RequestExpiredError):
            for item in results:
                if isinstance(item, error_type) and (
                    error_type is Code27RequestExpiredError
                    or not isinstance(item, Code27RequestExpiredError)
                ):
                    raise item
        for item in results:
            if isinstance(item, BaseException) and not isinstance(item, Code27Error):
                raise item
        # System is the connectivity anchor; failures of other endpoints are
        # merged individually with their last good value below.
        if results and isinstance(results[0], Code27Error):
            raise results[0]
        return results

    def _merge_poll_results(self, results: list[Any]) -> tuple[list[Any], dict[str, str]]:
        fields = (
            "system", "music_page", "music_source", "music_playback",
            "music_audio_route", "bluetooth_state", "light_master_switch",
            "light_projects", "applied_light_project", "alarms", "memos",
            "character_slots", "current_slot", "history_counts", "proactive_config",
            "device_state", "conversation_state", "device_brightness", "music_storage",
        )
        lists = {"light_projects", "alarms", "memos", "character_slots", "history_counts"}
        merged, errors = [], {}
        for name, value in zip(fields, results, strict=True):
            if isinstance(value, Code27Error):
                errors[name] = type(value).__name__
                value = getattr(
                    self.data, name, self._poll_values.get(name, [] if name in lists else {})
                )
            else:
                self._poll_values[name] = value
            merged.append(value)
        return merged, errors

    async def _poll_proactive_config(self) -> dict[str, Any] | Code27Error:
        """Read proactive-vision config (C27-063, CONFIRMED LIVE, read-only).

        Non-auth errors are merged with the last good value and exposed
        in poll_errors. Auth failures still propagate.
        """
        try:
            return await self.client.get_proactive_config()
        except Code27AuthError:
            raise
        except Exception as err:  # noqa: BLE001 - optional read, never fatal
            _LOGGER.debug("CODE27 proactive/config read unavailable: %s", err)
            return Code27ConnectionError("proactive_config unavailable")

    async def _poll_optional_state(self, method: str) -> dict[str, Any] | Code27Error:
        """An optional state read (1.5.3 device/conversation state): never
        fatal, empty on firmware without the endpoint. Auth failures still
        propagate."""
        try:
            return await getattr(self.client, method)()
        except Code27AuthError:
            raise
        except Exception as err:  # noqa: BLE001 - optional read, never fatal
            _LOGGER.debug("CODE27 %s unavailable: %s", method, err)
            return Code27ConnectionError(f"{method} unavailable")

    async def _poll_music_storage(self) -> dict[str, Any] | Code27Error:
        """REST read of the TF-card inventory (audit C27-061, CONFIRMED LIVE).

        The inventory used to arrive only through the ``music_storage``
        WebSocket push, so a device that had not pushed yet (or stopped
        pushing) left the media browser and the storage sensor empty even
        though a poll worked. Reading it here makes the inventory available
        from the very first poll - including after a Home Assistant restart,
        before any push has been seen.

        Never fatal: a device or firmware without this endpoint, or a
        temporarily failing read, just means "no REST inventory this cycle" and
        leaves the last known inventory (usually the one a push delivered) in
        place. Authentication failures are still propagated, because those must
        reach Home Assistant as a reauth request.
        """
        try:
            return await self.client.get_music_storage()
        except Code27AuthError:
            raise
        except Exception as err:  # noqa: BLE001 - optional read, never fatal
            _LOGGER.debug("CODE27 music/storage read unavailable: %s", err)
            return Code27ConnectionError("music_storage unavailable")

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
            return self._check_poll_results(await self._poll_once())
        except Code27RequestExpiredError as err:
            raise UpdateFailed("REQUEST_EXPIRED (clock skew)") from err
        except Code27AuthError as err:
            raise ConfigEntryAuthFailed(err.code or "auth_error") from err
        except Code27Error as err:
            raise UpdateFailed(
                f"Cannot reach CODE27 device after IP recovery: {err}"
            ) from err

    async def _async_update_data(self) -> Code27Data:
        # Captured before anything below can change it: HA only flips
        # last_update_success to True itself, once this call returns
        # without raising - during the call it still reflects the
        # PREVIOUS cycle, so this is genuinely "were we unavailable a
        # moment ago". Recovering from that boundary is exactly when
        # cached-looking state must not be trusted unconfirmed (see the
        # music_page/last_*_message handling below - musikmodus_aktiv-
        # after-reboot investigation).
        was_unavailable = not self.last_update_success
        if was_unavailable:
            # A live-confirmed clean fix, not a guess at which specific
            # piece of state is stale: recovering from `unavailable` gets
            # the exact same treatment a manual "reload this integration"
            # does (confirmed live to reliably clear the stale music_page
            # reading, where a targeted WebSocket-only reconnect did not -
            # musikmodus_aktiv-after-reboot investigation). Scheduled, not
            # awaited inline: this call is itself running as part of the
            # entry the reload is about to tear down and rebuild, so it
            # must return and let THIS poll cycle finish normally rather
            # than be cancelled out from under itself.
            self.hass.config_entries.async_schedule_reload(self.entry.entry_id)
        if not self._startup_cleanup_done:
            try:
                await self.client.stop_light_preview()
            except Code27Error:
                pass
            else:
                self._startup_cleanup_done = True
        revisions = dict(self._music_revisions)
        try:
            results = self._check_poll_results(await self._poll_once())
        except Code27RequestExpiredError as err:
            # Clock skew between HA and the device — not a credential failure.
            # Log a warning and let the next poll cycle retry; do NOT trigger
            # a re-auth flow (C1).
            _LOGGER.warning(
                "CODE27 poll rejected as REQUEST_EXPIRED (device/HA clock skew); "
                "retrying next cycle"
            )
            raise UpdateFailed("REQUEST_EXPIRED (clock skew)") from err
        except Code27AuthError as err:
            raise ConfigEntryAuthFailed(err.code or "auth_error") from err
        except Code27ConnectionError as err:
            results = await self._recover_and_retry_poll(err)
        except Code27Error as err:
            raise UpdateFailed(str(err)) from err

        results, poll_errors = self._merge_poll_results(results)
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
            proactive_config,
            device_state,
            conversation_state,
            device_brightness,
            polled_music_storage,
        ) = results

        # Chat is polled on its own, much faster, timer - reuse whatever it
        # last saw instead of re-fetching (or wiping!) it here too. NOT
        # reused across a recovery from `unavailable`, though (see
        # was_unavailable above): a pre-outage "last said" is unconfirmed
        # across a device reboot (musikmodus_aktiv-after-reboot
        # investigation found the same device returning a stale cached
        # value there) and is cleared instead, so the UI shows "nothing
        # yet" honestly until the chat poller's own next run reconfirms
        # it - never a silently stale exchange presented as current.
        if was_unavailable:
            last_user_message, last_assistant_message, chat_messages = {}, {}, []
        else:
            last_user_message = self.data.last_user_message if self.data else {}
            last_assistant_message = self.data.last_assistant_message if self.data else {}
            chat_messages = self.data.chat_messages if self.data else []
        last_tts_message = self.data.last_tts_message if self.data else {}
        # Same boundary, different remedy: an "is_open": True read right
        # after recovering from unavailable has been confirmed live to be
        # untrustworthy - sometimes a stale pre-outage cache echo, other
        # times a freshly timestamped but still-wrong value straight from
        # the device across MULTIPLE clean polls, not just the first one
        # (a suspected CODE27 firmware issue, not something HASE can
        # resolve by racing it - see docs/code27's own bug report). HASE
        # cannot know which reading is actually true here, so it reports
        # the inconsistency instead of guessing: a repair issue tells the
        # user to toggle music mode on the device once to force a real
        # resync, same manual fix that already resolved this live.
        if was_unavailable and music_page.get("is_open"):
            sync_music_mode_recovery_issue(self.hass, entry_id=self.entry.entry_id, suspected=True)
        elif music_page.get("is_open") is False:
            # Only cleared on actual evidence of "not open" (the device's
            # own later report, whatever produced it - the user's manual
            # toggle-and-back included) - never just because a later,
            # unrelated poll cycle happened to run without was_unavailable
            # being true again, which would clear it almost immediately
            # and defeat the point of surfacing it at all.
            sync_music_mode_recovery_issue(self.hass, entry_id=self.entry.entry_id, suspected=False)
        # The TF-card inventory has two sources now: the WebSocket push and the
        # REST read above. It is resolved through the same precedence rule as
        # the other music snapshots (newest wins, and a payload without an
        # inventory never destroys the last valid one) rather than by simply
        # overwriting - `music_storage` is deliberately not part of the
        # revision loop below, because its two sources need the explicit merge.
        music_storage = merge_music_inventory(
            polled=polled_music_storage,
            previous=self.data.music_storage if self.data else {},
            push_arrived=(
                self._music_revisions.get("music_storage", 0)
                != revisions.get("music_storage", 0)
            ),
        )

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
            last_tts_message=last_tts_message,
            proactive_config=proactive_config if isinstance(proactive_config, dict) else {},
            device_state=device_state if isinstance(device_state, dict) else {},
            conversation_state=conversation_state if isinstance(conversation_state, dict) else {},
            device_brightness=device_brightness if isinstance(device_brightness, dict) else {},
            poll_errors=poll_errors,
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
            if isinstance(left, (int, float)) and isinstance(right, (int, float)) and left != right:
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

        # 1.6.0 status channel: a `chat_history` sync event means "something
        # new in the chat" - poll right away (and wake from idle backoff)
        # instead of waiting for the next timer tick. The timer stays as the
        # fallback, so a missing/broken events channel only costs latency.
        self._events_ws = Code27WebSocket(
            self.hass, self.client, path="/v1/ws/events",
            subscribe=["chat_history"], name="code27_events_websocket",
        )
        self._events_ws.on("chat_history", self._handle_ws_chat_history)
        self._events_ws.start()

        self._chat_poll_unsub = async_track_time_interval(
            self.hass, self._async_poll_chat, CHAT_POLL_INTERVAL
        )

    async def async_set_tool_dispatcher(self, dispatcher: Any | None) -> None:
        """Firmware >= 1.6.0: (re)start or stop the Codie tool gateway.

        The dispatcher (catalog + execution, see tool_executor.ToolDispatcher)
        comes from the host: HASE injects an MCP-Bridge-backed one, standalone
        CODE27 injects none. None stops the gateway."""
        from .tool_executor import CodieToolExecutor, integration_id_for

        if self._tool_executor is not None:
            await self._tool_executor.stop()
            self._tool_executor = None
        if dispatcher is None or self.stopping:
            return
        self._tool_executor = CodieToolExecutor(
            self.hass, self.client, dispatcher, integration_id_for(self.entry.entry_id)
        )
        self._tool_executor.start()

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

        Returns the written message's `chat_id` (or None on failure). The
        device still reacts to this write with its own new chat turn, like
        any other message."""
        async with self._chat_lock:
            if self.stopping:
                return None
            self._chat_last_event_at = time.monotonic()
            self._chat_last_polled_at = 0.0
            pending = (slot, text, time.monotonic() + _CHAT_RESERVATION_TTL_SEC)
            self._pending_chat_replies.append(pending)
            self._chat_write_deadlines[pending] = time.time() + _CHAT_RESERVATION_TTL_SEC
            # Keep the reservation on ambiguous failure: the device may have
            # accepted the write even if its acknowledgement never reached us.
            # Reconcile against history before expiring; a delayed echo must
            # not become a new command after an outage.
            chat_id = await self.client.send_chat_text(text, slot, language)
            if chat_id is not None:
                self._remember_own_chat_id(slot, chat_id)
                self._pending_chat_replies.remove(pending)
                self._chat_write_deadlines.pop(pending, None)
            return chat_id

    _OUTFIT_LOAD_TIMEOUT_SEC = 30.0

    async def async_set_outfit(self, slot_number: int, cloth: int) -> None:
        """Switch a slot's outfit and follow the model reload.

        The device answers the apply immediately and loads the 3D model in
        the background (about two seconds); `model_loading` stays True until
        `/character/loading/state` says it is done, so automations don't
        speak into a half-loaded character."""
        await self.client.apply_outfit(slot_number, selected_cloth=cloth)
        self.model_loading = True
        self.async_update_listeners()
        try:
            deadline = time.monotonic() + self._OUTFIT_LOAD_TIMEOUT_SEC
            while time.monotonic() < deadline:
                await asyncio.sleep(1.0)
                try:
                    state = await self.client.get_character_loading_state()
                except Code27Error:
                    continue
                if not state.get("is_loading"):
                    break
        finally:
            self.model_loading = False
            self.async_update_listeners()
        await self.async_request_refresh()

    async def async_send_chat_image_reply(
        self,
        image: bytes,
        filename: str,
        content_type: str,
        text: str,
        slot: int,
        language: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Register our image output before polling can observe it as user input.

        Image sends produce two chat entries (caption + image); both are
        pre-registered as own-writes so they are never mistaken for
        user input. Mirrors async_send_chat_reply for the image path (B2)."""
        async with self._chat_lock:
            if self.stopping:
                return None, None
            self._chat_last_event_at = time.monotonic()
            self._chat_last_polled_at = 0.0
            pending_text = (
                (slot, text, time.monotonic() + _CHAT_RESERVATION_TTL_SEC) if text else None
            )
            if pending_text:
                self._pending_chat_replies.append(pending_text)
                self._chat_write_deadlines[pending_text] = time.time() + _CHAT_RESERVATION_TTL_SEC
            chat_id, image_chat_id = await self.client.send_chat_image(
                image, filename, content_type, text, slot, language
            )
            if chat_id is not None:
                self._remember_own_chat_id(slot, chat_id)
                if pending_text:
                    self._pending_chat_replies.remove(pending_text)
                    self._chat_write_deadlines.pop(pending_text, None)
            if image_chat_id is not None:
                self._remember_own_chat_id(slot, image_chat_id)
            return chat_id, image_chat_id

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
        if self._websocket is not None:
            await self._websocket.stop()
            self._websocket = None
        if self._events_ws is not None:
            await self._events_ws.stop()
            self._events_ws = None
        if self._tool_executor is not None:
            await self._tool_executor.stop()
            self._tool_executor = None

    def _handle_ws_chat_history(self, data: dict[str, Any]) -> None:
        if self.stopping or data.get("event") != "sync":
            return
        self._chat_last_event_at = time.monotonic()
        self._chat_last_polled_at = 0.0
        self.async_create_owned_task(self._async_poll_chat(), "code27_chat_poll_event")

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
        """Only a payload that actually describes the TF card may replace the
        inventory.

        A push without a ``music_files`` list says nothing about the library -
        accepting it would replace a valid inventory with an empty one, which is
        exactly what emptied the card's music browser on a real device. An
        explicitly empty *but described* card (``"music_files": []``) is
        accepted normally, so a genuinely emptied card still shows up.
        """
        if not usable_music_storage(data):
            _LOGGER.debug("Ignoring CODE27 music_storage push without an inventory")
            return
        self._handle_music_push("music_storage", data)

    # -- realtime-ish: fast, dedicated chat polling ---------------------------

    def _purge_chat_dedupe(self) -> None:
        """Bound both ID sets without a timer; insertion order is oldest first."""
        cutoff = time.monotonic() - _CHAT_DEDUPE_TTL_SEC
        for stamps, ids in ((self._chat_seen_at, self._chat_seen),
                            (self._own_chat_ids_at, self._own_chat_ids)):
            while stamps:
                key, created = next(iter(stamps.items()))
                if created > cutoff and len(stamps) <= _CHAT_DEDUPE_MAX_IDS:
                    break
                del stamps[key]
                if isinstance(ids, dict):
                    slot, chat_id = key
                    bucket = ids.get(slot)
                    if bucket is not None:
                        bucket.discard(chat_id)
                        if not bucket:
                            del ids[slot]
                else:
                    ids.discard(key)

    def _remember_seen_chat_id(self, slot: int, chat_id: str) -> None:
        key = (slot, chat_id)
        self._chat_seen.setdefault(slot, set()).add(chat_id)
        self._chat_seen_at.setdefault(key, time.monotonic())
        self._purge_chat_dedupe()

    def _remember_own_chat_id(self, slot: int, chat_id: str) -> None:
        key = (slot, chat_id)
        self._own_chat_ids.add(key)
        self._own_chat_ids_at.setdefault(key, time.monotonic())
        self._purge_chat_dedupe()

    def _reservation_can_match(self, reservation, message) -> bool:
        if reservation[2] > time.monotonic():
            return True
        deadline = self._chat_write_deadlines.get(reservation)
        if deadline is None:
            return False
        # Compare device history time with the corresponding wall-clock
        # deadline, adjusted for known device clock skew. A later real message
        # must not be consumed by a stale reservation with the same text.
        created = message.get("created_at")
        try:
            if isinstance(created, str):
                stamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    return True  # cannot safely disambiguate an unzoned echo
                created = stamp.timestamp()
            elif isinstance(created, (int, float)):
                created = created / 1000 if created > 10**11 else created
            else:
                return True  # fail closed for an unidentifiable possible echo
        except (ValueError, TypeError, OverflowError):
            return True
        return created <= deadline + (getattr(self.client, "clock_skew_s", None) or 0)

    async def _async_poll_chat(self, _now: Any = None) -> None:
        self._purge_chat_dedupe()
        if not self.data or self.stopping or self._chat_lock.locked():
            return
        # C7: idle backoff — skip ticks when no new chat events have been seen
        # for a while, reducing signed requests from ~28 k/day to ~3 k/day after
        # 15 min of silence.
        _tick_now = time.monotonic()
        _idle = _tick_now - self._chat_last_event_at
        _effective_interval = CHAT_POLL_INTERVAL.total_seconds()
        for _threshold, _interval in _CHAT_BACKOFF_THRESHOLDS:
            if _idle >= _threshold:
                _effective_interval = _interval
                break
        if _tick_now - self._chat_last_polled_at < _effective_interval:
            return
        self._chat_last_polled_at = _tick_now
        self._poll_task = asyncio.current_task()
        try:
            async with self._chat_lock:
                # Preserve pending writes across outages/backoff until this
                # successful snapshot has had a chance to identify their echoes.
                slot = self.data.current_slot.get("slot_number", 1)
                switched = slot != self._chat_active_slot
                # An already initialized empty history has no date yet;
                # include all newly appearing days, even after an outage.
                # Use .get(slot, "") (not `or ""`) so a stored "" stays ""
                # rather than coercing a previously-stored None back to ""
                # on every poll and triggering a full-history re-scan (E1).
                since_date = None if switched else self._chat_dates.get(slot, "")
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
                # Never store None: a None date from the API means "no date
                # boundary returned", so keep the previous value (or "").
                # Storing None + the get(slot, "") fix above would still
                # return None, breaking the invariant (E1).
                if date is not None:
                    self._chat_dates[slot] = date
                elif slot not in self._chat_dates:
                    self._chat_dates[slot] = ""
                self._chat_last_ids[slot] = (
                    str(messages[-1]["chat_id"])
                    if messages and messages[-1].get("chat_id") is not None else None
                )
                self._chat_active_slot = slot
                for message in messages:
                    chat_id = message.get("chat_id")
                    if chat_id is None:
                        continue
                    chat_id = str(chat_id)
                    if chat_id in seen:
                        continue
                    self._remember_seen_chat_id(slot, chat_id)
                    own = (slot, chat_id) in self._own_chat_ids
                    _content = message.get("content", "")
                    _pending_match = next(
                        (r for r in self._pending_chat_replies
                         if r[0] == slot and r[1] == _content
                         and self._reservation_can_match(r, message)),
                        None,
                    )
                    if not own and message.get("role") == "user" and _pending_match is not None:
                        self._pending_chat_replies.remove(_pending_match)
                        self._chat_write_deadlines.pop(_pending_match, None)
                        self._remember_own_chat_id(slot, chat_id)
                        own = True
                    if baseline or own:
                        continue
                    event_type = {
                        "user": EVENT_USER_MESSAGE,
                        "assistant": EVENT_ASSISTANT_MESSAGE,
                    }.get(message.get("role"))
                    if event_type:
                        self._chat_last_event_at = time.monotonic()
                        self.hass.bus.async_fire(event_type, {
                            "config_entry_id": self.entry.entry_id,
                            "text": message.get("content", ""),
                            "chat_id": chat_id,
                            "history_id": message.get("history_id"),
                            "slot": slot,
                            "language": self.data.system.get("ai_language"),
                            "created_at": message.get("created_at"),
                        })
                deadline_now = time.monotonic()
                self._pending_chat_replies[:] = [
                    r for r in self._pending_chat_replies
                    if r[0] != slot or r[2] > deadline_now
                ]
                self._chat_write_deadlines = {
                    r: deadline for r, deadline in self._chat_write_deadlines.items()
                    if r in self._pending_chat_replies
                }
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
