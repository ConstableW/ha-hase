"""Lightweight async client for the CODE27 (CODIE) local HTTP API.

The device exposes a Vue single-page app at ``http://<host>:8081`` which talks
to a local REST API under ``/v1/api``. Requests (other than the three auth
bootstrap/pairing endpoints) must be signed with an HMAC-SHA256 signature
derived from a per-installation secret that is handed out during a one-time
"pairing" flow (a 6-digit code is shown on the device's own screen).

This module re-implements that signing scheme exactly as the web app does it
(reverse engineered from the served JS bundle), so Home Assistant can act as
just another paired "browser" without needing anything CODE27 doesn't already
provide.
"""
from __future__ import annotations

import asyncio
import email.utils
import hashlib
import hmac
import json
import logging
import random
import string
import time
import uuid
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp

_LOGGER = logging.getLogger(__name__)

_NONCE_ALPHABET = string.digits + string.ascii_lowercase
_UNSIGNED_PATHS = (
    "/auth/ping", "/auth/bootstrap", "/auth/pair/request", "/auth/pair/confirm",
    "/auth/pair/cancel",
)

# CODE27's own cloud relay - confirmed live (2026-09-12) to accept the exact
# same LAN-HMAC signature as a direct device request, plus an X-DEVICE-ID
# header the relay needs to know which device to route to. Used exclusively
# for IP recovery (see Code27Client.attempt_remote_recovery) - never as a
# general request fallback: this is real third-party production
# infrastructure we don't control, not our own.
_REMOTE_BASE_URL = "https://codie-remote.prod.code27.co"
_REMOTE_RECOVERY_COOLDOWN_SEC = 30.0


class Code27Error(Exception):
    """Generic error talking to the CODE27 device."""


class Code27NotFoundError(Code27Error):
    """The device answered HTTP 404/405 - on a firmware generation that
    dropped or moved an endpoint (1.5.3 replaced several), this is the
    signal to fall back to the other generation's path."""


class Code27AuthError(Code27Error):
    """Raised when the stored pairing credentials are no longer valid."""

    def __init__(self, code: str | None = None) -> None:
        super().__init__(code or "auth_error")
        self.code = code


class Code27RequestExpiredError(Code27AuthError):
    """Raised when the device rejects a request with REQUEST_EXPIRED.

    The device clock and HA clock are too far apart (>30s typical).
    This is retryable after re-syncing time; unlike a real auth failure
    it does not mean the pairing credentials are invalid (C3).
    """

    def __init__(self) -> None:
        super().__init__("REQUEST_EXPIRED")


class Code27ConnectionError(Code27Error):
    """Raised when the device cannot be reached."""


class Code27SlotMismatchError(Code27Error):
    """A chat write was rejected because its target slot is not the one
    currently active on the device - CODE27 only ever runs one character
    slot at a time (confirmed live: HTTP 409 with `current_slot`/
    `request_slot` in the body)."""

    def __init__(self, current_slot: int, request_slot: int) -> None:
        super().__init__(
            f"slot {request_slot} is not active (device is on slot {current_slot})"
        )
        self.current_slot = current_slot
        self.request_slot = request_slot


class Code27DeviceError(Code27Error):
    """The device accepted the request (HTTP 2xx, envelope `success: true`)
    but reports the underlying action failed via a nested `status`/
    `error_code` field - e.g. `music_control` returning
    `{"status":"FAILED","error_code":"MODE_INACTIVE",...}`. Callers need
    this distinguished from a transport-level Code27Error so they can show
    a specific, actionable message instead of silently doing nothing."""

    def __init__(self, error_code: str | None, error_message: str | None) -> None:
        super().__init__(error_message or error_code or "device_error")
        self.error_code = error_code


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _hmac_sha256_hex(secret: str, message: str) -> str:
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _nonce() -> str:
    return "".join(random.choice(_NONCE_ALPHABET) for _ in range(16))


class Code27Client:
    """Talks to a single CODE27 device over the local LAN."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        user_id: str | None = None,
        local_secret: str | None = None,
        machine_id: str | None = None,
    ) -> None:
        self._session = session
        self.host = host
        self.port = port
        self.user_id = user_id
        self.local_secret = local_secret
        self.machine_id = machine_id
        self._last_recovery_attempt: float | None = None
        self.clock_skew_s: float | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # -- auth / pairing -----------------------------------------------

    async def bootstrap(self) -> dict[str, Any]:
        """Public, unauthenticated info about the device."""
        return await self._call("GET", "/auth/bootstrap", signed=False)

    async def pair_request(self) -> str:
        """Ask the device to show a 6-digit pairing code on its screen.

        Returns the challenge_id that must be passed back to pair_confirm.
        """
        result = await self._call("POST", "/auth/pair/request", signed=False)
        challenge_id = (result.get("data") or {}).get("challenge_id")
        if not challenge_id:
            raise Code27Error("pairing_no_challenge")
        return challenge_id

    async def pair_confirm(self, pairing_code: str, challenge_id: str) -> tuple[str, str]:
        """Exchange a pairing code for permanent local credentials.

        Returns (user_id, local_secret).
        """
        result = await self._call(
            "POST",
            "/auth/pair/confirm",
            body={"pairing_code": pairing_code, "challenge_id": challenge_id},
            signed=False,
        )
        data = result.get("data") or {}
        secret = data.get("local_secret")
        if not secret:
            raise Code27Error("pairing_no_secret")
        # The bootstrap user_id is the one used for signing, not any id echoed here.
        if not self.user_id:
            boot = await self.bootstrap()
            self.user_id = (boot.get("data") or {}).get("user_id")
        self.local_secret = secret
        return self.user_id or "", secret

    # -- system ----------------------------------------------------------

    async def get_system(self) -> dict[str, Any]:
        return (await self._call("GET", "/data/user/system")).get("data", {})

    async def get_proactive_config(self) -> dict[str, Any]:
        """Read proactive-vision config (C27-063, CONFIRMED LIVE, read-only)."""
        return (await self._call("GET", "/data/user/proactive/config")).get("data", {})

    # -- music -------------------------------------------------------------

    async def get_music_page(self) -> dict[str, Any]:
        return (await self._call("GET", "/data/user/music/page")).get("data", {})

    async def get_music_source(self) -> dict[str, Any]:
        return (await self._call("GET", "/data/user/music/source")).get("data", {})

    async def set_music_source(self, source: str) -> None:
        await self._call(
            "POST",
            "/data/user/music/source",
            body={"source": source, "request_id": f"ha-{int(time.time() * 1000)}"},
        )

    async def get_music_playback(self) -> dict[str, Any]:
        return (await self._call("GET", "/data/user/music/playback")).get("data", {})

    async def get_music_audio_route(self) -> dict[str, Any]:
        return (await self._call("GET", "/data/user/music/audio-route")).get("data", {})

    async def get_music_storage(self, *, refresh: bool = False) -> dict[str, Any]:
        """The TF card's music inventory (audit C27-061, CONFIRMED LIVE).

        Returns ``is_loaded``, ``root_path``, ``music_files[]`` (each with
        ``name``/``path``/``size_bytes``/``duration_ms``), ``capacity_*`` and a
        numeric ``version`` revision counter. ``music_files[].path`` is an
        **absolute** device path, and ``root_path`` is the card's mount point
        (the music lives under ``<root_path>/code27/music/``).

        ``refresh=True`` adds ``?refresh=1`` to force a re-scan; the plain read
        is used by the poll so routine cycles stay cheap. This endpoint used to
        be unused, which left the media browser and the storage sensor
        dependent on a WebSocket push ever arriving.
        """
        path = "/data/user/music/storage"
        if refresh:
            path += "?refresh=1"
        return (await self._call("GET", path)).get("data", {})

    async def get_bluetooth_state(self) -> dict[str, Any]:
        return (await self._call("GET", "/data/user/music/bluetooth")).get("data", {})

    async def music_control(self, action: str, **extra: Any) -> dict[str, Any]:
        body = {
            "action": action,
            "source": "LOCAL_TF",
            "request_id": f"ha-{int(time.time() * 1000)}",
            **extra,
        }
        result = (await self._call("POST", "/data/user/music/control", body=body)).get(
            "data", {}
        )
        # The HTTP call itself can succeed (200, envelope `success: true`)
        # while the action still failed - the device reports that via a
        # nested `status`/`error_code`, not via HTTP status or `success`.
        # Confirmed live: SET_VOLUME/PLAY both return HTTP 200 with
        # {"status":"FAILED","error_code":"MODE_INACTIVE",...} when the
        # device's music screen isn't open.
        if isinstance(result, dict) and result.get("status") == "FAILED":
            raise Code27DeviceError(result.get("error_code"), result.get("error_message"))
        return result

    async def upload_music(
        self, files: list[tuple[bytes, str, str]]
    ) -> dict[str, Any]:
        """Upload one or more audio files to the TF card - confirmed live
        to accept more than just MP3 (tested with .wav). `files` is
        (content, filename, content_type) tuples; the device's form field
        for each is "files", repeated for multiple files.

        Returns ``{"uploaded": [...], "rejected": [...], "snapshot": {...}}``
        - each uploaded entry has ``name``/``size_bytes``/``path``/
        ``duration_ms``, confirmed live, and its ``path`` is exactly what
        `music_control("PLAY", track_id=f"local:{path}")` expects.
        """
        result = await self._call_multipart(
            "POST",
            "/data/user/music/upload",
            fields={},
            files=[
                ("files", content, filename, content_type)
                for content, filename, content_type in files
            ],
        )
        return result.get("data", {})

    async def delete_music_file(self, path: str) -> None:
        """Confirmed live: removes a file from the TF card, even while it
        is the one currently playing (playback continues uninterrupted -
        the device streams from an already-loaded buffer)."""
        await self._call("DELETE", f"/data/user/music/file?path={quote(path)}")

    # -- lights ------------------------------------------------------------

    async def get_light_master_switch(self) -> dict[str, Any]:
        return (await self._call("GET", "/data/user/light-master-switch")).get("data", {})

    async def set_light_master_switch(self, enabled: bool) -> None:
        await self._call("POST", "/data/user/light-master-switch", body={"enabled": enabled})

    async def list_light_projects(self) -> list[dict[str, Any]]:
        return (await self._call("GET", "/data/user/light-projects")).get("data", [])

    async def get_applied_light_project(self) -> dict[str, Any]:
        return (await self._call("GET", "/data/user/light-projects/applied")).get("data", {})

    async def apply_light_project(self, project_id: str) -> None:
        await self._call("POST", f"/data/user/light-projects/{project_id}/apply")

    # -- alarms --------------------------------------------------------------

    async def list_alarms(self) -> list[dict[str, Any]]:
        return (await self._call("GET", "/data/user/alarm/list")).get("data", [])

    # Firmware 1.5.3 rejects an alarm body that lacks any of these
    # ("invalid request parameters"), so callers may send just the fields
    # they care about (hour/minute/label/repeat_mask/enabled).
    _ALARM_DEFAULTS: dict[str, Any] = {
        "tag": "", "ringtone": "ringtone_default",
        "snooze_enabled": True, "snooze_minutes": 10, "snooze_max_count": 3,
    }

    async def create_alarm(self, alarm: dict[str, Any]) -> dict[str, Any]:
        body = {**self._ALARM_DEFAULTS, **alarm}
        return (await self._call("POST", "/data/user/alarm", body={"alarm": body})).get("data", {})

    async def update_alarm(self, alarm_id: str, alarm: dict[str, Any]) -> dict[str, Any]:
        """Update an alarm. Fields not given keep the alarm's current values
        (ringtone, snooze settings, tag), because the device's PUT replaces
        the whole alarm and requires all of them."""
        current = next(
            (a for a in await self.list_alarms() if a.get("id") == alarm_id), {}
        )
        kept = {k: current[k] for k in self._ALARM_DEFAULTS if k in current}
        body = {**self._ALARM_DEFAULTS, **kept, **alarm}
        return (
            await self._call("PUT", f"/data/user/alarm/{alarm_id}", body={"alarm": body})
        ).get("data", {})

    async def delete_alarm(self, alarm_id: str) -> None:
        await self._call("DELETE", f"/data/user/alarm/{alarm_id}")

    async def enable_alarm(self, alarm_id: str) -> None:
        await self._set_alarm_enabled(alarm_id, True)

    async def disable_alarm(self, alarm_id: str) -> None:
        await self._set_alarm_enabled(alarm_id, False)

    async def _set_alarm_enabled(self, alarm_id: str, enabled: bool) -> None:
        # PATCH /alarm/{id} {"enabled": bool}
        await self._call(
            "PATCH", f"/data/user/alarm/{alarm_id}", body={"enabled": enabled}
        )

    # -- memos (notes & reminders) ------------------------------------------

    async def list_memos(self) -> list[dict[str, Any]]:
        return (await self._call("GET", "/data/user/memos")).get("data", [])

    # Firmware 1.5.3 requires all of these on create and update. The defaults
    # describe a plain note (`static`, no schedule); a reminder passes its own
    # `memo_type`/`schedule_type`/date/time.
    _MEMO_DEFAULTS: dict[str, Any] = {
        "memo_type": "static", "schedule_type": "none", "interval_days": 1,
        "trigger_year": 0, "trigger_month": 0, "trigger_day": 0,
        "hour": 0, "minute": 0, "timezone": "UTC", "ringtone": "ringtone_default",
        "repeat_mask": 0, "title": "", "body": "",
    }

    async def create_memo(self, memo: dict[str, Any]) -> dict[str, Any]:
        body = {**self._MEMO_DEFAULTS, **memo}
        return (await self._call("POST", "/data/user/memos", body={"memo": body})).get("data", {})

    async def update_memo(self, memo_id: str, memo: dict[str, Any]) -> dict[str, Any]:
        """Update a note/reminder. Fields not given keep the memo's current
        values (including its schedule), because the device's PUT replaces
        the whole memo."""
        current = next((m for m in await self.list_memos() if m.get("id") == memo_id), {})
        kept = {k: current[k] for k in self._MEMO_DEFAULTS if k in current}
        body = {**self._MEMO_DEFAULTS, **kept, **memo}
        return (
            await self._call("PUT", f"/data/user/memos/{memo_id}", body={"memo": body})
        ).get("data", {})

    async def delete_memo(self, memo_id: str) -> None:
        await self._call("DELETE", f"/data/user/memos/{memo_id}")

    # -- character -----------------------------------------------------------

    async def list_character_settings(self) -> list[dict[str, Any]]:
        return (await self._call("GET", "/data/user/character/list")).get("data", [])

    _CHARACTER_UPDATE_FIELDS = {
        "name", "description", "sex", "animation_intensity", "selected_cloth",
        "available_cloths", "original_cloths", "custom_cloths",
        "link_other_original_cloth", "character_prompt", "voice_id", "group_key",
        "tts_volume",
    }
    # 1.5.3 refuses these on PUT /character (OUTFIT_APPLY_REQUIRED) - they go
    # through POST /character/outfit/apply, which only takes the first three.
    _OUTFIT_FIELDS = {"selected_cloth", "available_cloths", "link_other_original_cloth"}
    _OUTFIT_READONLY_FIELDS = {"original_cloths", "custom_cloths"}

    async def update_character_setting(
        self, slot_number: int, device_id: str, **updates: Any
    ) -> dict[str, Any]:
        """Persistently update fields on a character slot - confirmed live
        end-to-end (animation_intensity and voice_id/group_key both
        round-tripped against a real device and read back via
        `list_character_settings()`, see the audit's Section 6). Only the
        fields the device is confirmed to accept are allowed here, so a
        typo fails fast instead of silently being ignored by the device.

        `device_id` is the machine_id from `get_system()`, not our own
        user_id/local_secret pairing identity - a different id namespace.
        The device can reject this with `CLOTH_UPLOAD_IN_PROGRESS` while a
        cloth upload is running (confirmed in the decompiled client, exact
        HTTP shape not live-tested) - callers should surface that
        specifically rather than a generic failure.
        """
        unknown = set(updates) - self._CHARACTER_UPDATE_FIELDS
        if unknown:
            raise Code27Error(
                f"unsupported character update field(s): {', '.join(sorted(unknown))}"
            )
        outfit = {k: updates.pop(k) for k in list(updates) if k in self._OUTFIT_FIELDS}
        if self._OUTFIT_READONLY_FIELDS & set(updates):
            raise Code27Error(
                "original_cloths/custom_cloths cannot be changed through the "
                "local API (firmware 1.5.3 only accepts outfit apply)"
            )
        result: dict[str, Any] = {}
        if updates:
            body = {"slot_number": slot_number, **updates}
            try:
                result = (await self._call("PUT", "/data/user/character", body=body)).get(
                    "data", {}
                )
            except (Code27AuthError, Code27ConnectionError, Code27SlotMismatchError):
                raise
            except Code27Error:
                # Older firmware wanted the machine id in the body.
                result = (
                    await self._call(
                        "PUT", "/data/user/character", body={**body, "device_id": device_id}
                    )
                ).get("data", {})
        if outfit:
            result = await self.apply_outfit(slot_number, **outfit)
        return result

    async def apply_outfit(
        self,
        slot_number: int,
        selected_cloth: int | None = None,
        available_cloths: str | None = None,
        link_other_original_cloth: str | None = None,
    ) -> dict[str, Any]:
        """Switch a slot's outfit (1.5.3 `POST /character/outfit/apply`).

        All three fields are required by the device; any left out are filled
        from the slot's current settings, so ``apply_outfit(1, selected_cloth=2)``
        just selects outfit 2. Model loading continues afterwards - poll
        `get_character_loading_state`. 409 means an upload/load is running."""
        current = next(
            (c for c in await self.list_character_settings()
             if c.get("slot_number") == slot_number),
            {},
        )
        body = {
            "slot_number": slot_number,
            "selected_cloth": (
                selected_cloth if selected_cloth is not None else current.get("selected_cloth")
            ),
            "available_cloths": (
                available_cloths if available_cloths is not None
                else current.get("available_cloths", "")
            ),
            "link_other_original_cloth": (
                link_other_original_cloth if link_other_original_cloth is not None
                else current.get("link_other_original_cloth", "")
            ),
        }
        if not isinstance(body["selected_cloth"], int) or isinstance(body["selected_cloth"], bool):
            raise Code27Error("apply_outfit: selected_cloth unknown for this slot")
        return (
            await self._call("POST", "/data/user/character/outfit/apply", body=body)
        ).get("data", {})

    async def get_current_slot(self, scope: str = "chat") -> dict[str, Any]:
        return (await self._call("GET", f"/data/user/slot?scope={scope}")).get("data", {})

    async def update_selected_slot(self, slot_number: int, scope: str = "chat") -> None:
        await self._call("PUT", "/data/user/slot", body={"slot_number": slot_number})

    # -- portraits -------------------------------------------------------------
    # Custom-cloth thumbnail (rendered VRM portrait) first, falling back to the
    # device-wide stock portrait, exactly the resolution order confirmed live
    # against a real device (see the audit's "Portrait-Picker" findings) -
    # not every slot/cloth combination has either.

    async def get_original_portraits_manifest(self) -> list[dict[str, Any]]:
        """Which stock slot/cloth combinations have a bundled portrait at
        all - device-wide, not account/slot-specific. Only covers the
        factory-default characters (confirmed live: slots 1-3, not 4-5).
        1.5.3 wraps the entries as ``{exported_at_ms, portraits: [...]}``
        (``slot_number``/``cloth_id``/``filename``/``portrait_path``)."""
        data = (await self._call("GET", "/resources/system/original-portraits/manifest")).get(
            "data", []
        )
        if isinstance(data, dict):
            data = data.get("portraits") or []
        return [entry for entry in data if isinstance(entry, dict)]

    @staticmethod
    def _resolve_outfit_link(
        characters: list[dict[str, Any]], slot_number: int, cloth_id: int
    ) -> tuple[int, int]:
        """Follow ``link_other_original_cloth`` to the outfit that is really
        shown. The field holds one ``slot:cloth`` entry per outfit (comma
        separated, empty where the outfit is not a link); links can chain, so
        follow up to a few hops and stop on a loop or a malformed entry."""
        seen = {(slot_number, cloth_id)}
        for _ in range(5):
            character = next((c for c in characters if c.get("slot_number") == slot_number), None)
            parts = str((character or {}).get("link_other_original_cloth") or "").split(",")
            entry = parts[cloth_id - 1].strip() if 0 < cloth_id <= len(parts) else ""
            if ":" not in entry:
                break
            target_slot, _, target_cloth = entry.partition(":")
            try:
                target = (int(target_slot), int(target_cloth))
            except ValueError:
                break
            if target in seen:
                break
            seen.add(target)
            slot_number, cloth_id = target
        return slot_number, cloth_id

    async def get_character_portrait(
        self, slot_number: int, cloth_id: int
    ) -> tuple[str, bytes] | None:
        """Resolve and fetch a character's portrait image, or ``None`` if
        neither a custom thumbnail nor a stock portrait exists for this
        slot/cloth (confirmed live: some combinations have no image at
        all - e.g. a non-default character with no custom model).

        The stock-portrait manifest's own JSON field names aren't
        confirmed (the audit only verified the resulting image URL
        pattern), so rather than guess field names to pre-check
        membership, the stock path is just attempted directly and a 404
        is treated as "no portrait for this combo" - self-verifying
        against the real device instead of a possibly-misread manifest.
        """
        try:
            characters = await self.list_character_settings()
        except Code27Error:
            characters = []
        if not isinstance(characters, list):
            characters = []
        # An outfit can be a link to another character's *original* (factory)
        # outfit - the device's cloth page shows "Lilya slot:1 outfit:2 LINK"
        # with the stock portrait, not the picture of a custom model that
        # happens to sit in the same slot. A link therefore skips custom
        # thumbnails and goes straight to the stock portrait.
        requested = (slot_number, cloth_id)
        slot_number, cloth_id = self._resolve_outfit_link(characters, slot_number, cloth_id)
        linked = (slot_number, cloth_id) != requested
        models: list[dict[str, Any]] = []
        if not linked:
            # The filenames are in the slot's `custom_cloths`: one line per
            # cloth id (line 1 = cloth 1), empty where the cloth is a stock one.
            for character in characters:
                if character.get("slot_number") == slot_number:
                    lines = str(character.get("custom_cloths") or "").split("\n")
                    models = [
                        {"slot_number": slot_number, "cloth_id": i + 1, "filename": name.strip()}
                        for i, name in enumerate(lines) if name.strip()
                    ]
                    break
        for model in models:
            if model.get("slot_number") == slot_number and model.get("cloth_id") == cloth_id:
                filename = model.get("filename")
                if filename:
                    # The device rejects the request if the extension is
                    # included (confirmed live) - only the bare stem is used.
                    stem = filename.rsplit(".", 1)[0]
                    try:
                        return await self._call_binary(
                            f"/resources/user/assets/thumbnails/{slot_number}_{cloth_id}_{stem}.png"
                        )
                    except Code27Error:
                        pass
                break
        # Stock portrait: the 1.5.3 manifest names the file; the bare
        # `{slot}_{cloth}.png` convention is the pre-1.5.3 fallback.
        candidates: list[str] = []
        try:
            for entry in await self.get_original_portraits_manifest():
                if entry.get("slot_number") == slot_number and entry.get("cloth_id") == cloth_id:
                    for key in ("filename", "portrait_path"):
                        name = str(entry.get(key) or "").lstrip("/")
                        name = name.removeprefix("resources/system/assets/original-portraits/")
                        if name and name not in candidates:
                            candidates.append(name)
                    break
        except Code27Error:
            pass
        legacy_name = f"{slot_number}_{cloth_id}.png"
        if legacy_name not in candidates:
            candidates.append(legacy_name)
        for name in candidates:
            try:
                return await self._call_binary(
                    f"/resources/system/assets/original-portraits/{name}"
                )
            except Code27Error:
                continue
        return None

    # -- chat ------------------------------------------------------------------
    # History is paged (`{items, next_cursor, has_more}`) and messages are sent
    # as "chat turns"; `/history/latest`, `/history/count`, `/chat/text` and
    # `/chat/image-text` no longer exist (firmware >= 1.5.3; only >= 1.6.0 is
    # supported).

    _TURN_TERMINAL_STATES = {"completed", "failed", "cancelled"}

    async def get_chat_history_dates(self, slot: int = 1) -> list[str]:
        return (
            await self._call("GET", f"/data/user/chat/history/dates?slot={slot}")
        ).get("data", [])

    async def _history_page(
        self,
        slot: int,
        *,
        date: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
        direction: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None, bool]:
        """One page of `GET /chat/history` (1.5.3 shape). Items inside a page
        are always in ascending message order."""
        params: dict[str, Any] = {"slot": slot, "limit": limit}
        if date:
            params["date"] = date
        if cursor:
            params["cursor"] = cursor
        if direction:
            params["direction"] = direction
        data = (
            await self._call("GET", f"/data/user/chat/history?{urlencode(params)}")
        ).get("data", {})
        items = data.get("items") or []
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise Code27Error("invalid_response: expected object list")
        cursor_out = data.get("next_cursor") or None
        return items, cursor_out, bool(data.get("has_more"))

    @staticmethod
    def _by_time(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(messages, key=lambda m: str(m.get("created_at") or ""))

    async def get_latest_chat_messages(
        self, slot: int = 1, limit: int = 10
    ) -> list[dict[str, Any]]:
        """The most recent messages (both roles) for a character slot.

        Chronological order (oldest first, like the API returns them).
        """
        dates = await self.get_chat_history_dates(slot)
        if not dates:
            return []
        latest_date = max(dates)

        items, _, _ = await self._history_page(
            slot, date=latest_date, limit=min(max(limit, 1), 100)
        )
        return items

    async def get_chat_history_snapshot(
        self, slot: int, since_date: str | None = None, after_chat_id: str | None = None
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Read complete days, including the previous poll's day at midnight.

        The bounded latest endpoint is unsuitable for command delivery: a
        burst of messages can move unseen commands outside its window.
        """
        dates = sorted(await self.get_chat_history_dates(slot))
        if not dates:
            return since_date, []
        if since_date == dates[-1] and after_chat_id is not None:
            messages = await self.get_latest_chat_messages(slot, limit=30)
            if any(str(m.get("chat_id")) == after_chat_id for m in messages):
                return dates[-1], self._by_time(messages)
        selected = [date for date in dates if date >= (dates[-1] if since_date is None else since_date)]
        messages = []
        for date in selected:
            messages.extend(await self._history_day(slot, date))
        return dates[-1], self._by_time(messages)

    async def _history_day(self, slot: int, date: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(100):  # hard cap: 10k turns on one day is not real
            items, cursor, more = await self._history_page(
                slot, date=date, limit=100, cursor=cursor, direction="newer"
            )
            out.extend(items)
            if not more or not cursor:
                break
        return out

    # -- chat turns (1.5.3) -----------------------------------------------------

    async def create_chat_turn(
        self,
        text: str,
        slot: int = 1,
        speech: str = "device",
        *,
        interrupt: bool | None = None,
        retention: str | None = None,
        tools: str | None = None,
    ) -> dict[str, Any]:
        """Start a text turn. ``speech="device"`` lets the character speak the
        reply (completed then also means playback finished); ``"none"`` only
        generates it. 202 means accepted - poll `get_chat_turn`.

        1.6.0: ``interrupt=False`` fails with DEVICE_BUSY instead of ending a
        running session; ``retention`` ("full"/"chat"/"none") decides what the
        turn leaves behind; ``tools="none"`` keeps the model from calling
        device or external tools (e.g. the Spell gateway) for this turn."""
        body = {
            "request_id": str(uuid.uuid4()),
            "slot": slot,
            "input": {"type": "text", "text": text},
            "output": {"speech": speech},
        }
        # 1.6.0 options; omitted = device defaults (interrupt=true,
        # retention="full", tools="all").
        if interrupt is not None:
            body["interrupt"] = bool(interrupt)
        if retention is not None:
            if retention not in {"full", "chat", "none"}:
                raise ValueError("retention must be full, chat or none")
            body["retention"] = retention
        if tools is not None:
            if tools not in {"all", "none"}:
                raise ValueError("tools must be all or none")
            body["tools"] = tools
        return (await self._call("POST", "/data/user/chat/turns", body=body)).get("data", {})

    async def get_chat_turn(self, turn_id: str) -> dict[str, Any]:
        return (
            await self._call("GET", f"/data/user/chat/turns/{quote(turn_id, safe='')}")
        ).get("data", {})

    async def cancel_chat_turn(self, turn_id: str) -> None:
        await self._call("POST", f"/data/user/chat/turns/{quote(turn_id, safe='')}/cancel")

    async def _turn_user_chat_ids(
        self, turn: dict[str, Any], timeout: float = 4.0
    ) -> list[str]:
        """The accepted turn's own user-message ids. They appear on the turn
        shortly after acceptance, so poll briefly; an empty list means "not
        known yet" and the caller keeps its text-based echo reservation."""
        deadline = time.monotonic() + timeout
        while True:
            ids = [str(i) for i in (turn.get("user_chat_ids") or []) if i]
            if ids or turn.get("state") in self._TURN_TERMINAL_STATES:
                return ids
            turn_id = turn.get("turn_id")
            if not turn_id or time.monotonic() >= deadline:
                return []
            await asyncio.sleep(0.4)
            try:
                turn = await self.get_chat_turn(str(turn_id))
            except Code27Error:
                return []

    async def send_chat_text(
        self, text: str, slot: int = 1, language: str | None = None
    ) -> str | None:
        """Write a user message into a slot's chat; the device reacts to it
        exactly like to a spoken one. Returns the message's ``chat_id`` when
        known. (``language`` is ignored - turns use the device's AI language.)"""

        turn = await self.create_chat_turn(text, slot)
        ids = await self._turn_user_chat_ids(turn)
        return ids[0] if ids else None

    async def get_chat_history_count(self) -> list[dict[str, Any]]:
        """Every slot's latest message at once: `[{slot, messages: [...]}...]`
        - only for state/card sync (which character last said what); the
        per-active-slot event pipeline (`get_chat_history_snapshot`, polled in
        coordinator.py) stays the only source of
        `EVENT_USER_MESSAGE`/`EVENT_ASSISTANT_MESSAGE`.

        1.5.3 has no all-slot endpoint any more, so this reads the newest
        turn of each slot (up to five small requests)."""

        async def one(slot: int) -> dict[str, Any] | None:
            try:
                items, _, _ = await self._history_page(slot, limit=1)
            except Code27NotFoundError:
                raise
            except Code27Error:
                return None  # an empty/unused slot must not fail the poll
            return {"slot": slot, "messages": self._by_time(items)}

        results = await asyncio.gather(*(one(s) for s in range(1, 6)))
        return [r for r in results if r is not None]

    async def get_chat_history_slot_count(self, slot: int) -> int:
        return int(
            (await self._call("GET", f"/data/user/chat/history/count/slot/{slot}"))
            .get("data", {})
            .get("count", 0)
        )

    async def delete_chat_messages(
        self, chat_ids: list[str], slot: int | None = None
    ) -> dict[str, Any]:
        """Delete one or more chat messages, both on-device and in CODE27's
        cloud backup where one exists (confirmed live - the response
        carries independent `local_deleted`/`cloud_deleted` counts, so a
        message can vanish from the device while its cloud copy fails,
        or vice versa).

        Takes each message's `chat_id` (a string - the same id every
        other method here already works with), not its numeric
        `history_id`. The Web UI's own History -> right-click -> Delete
        (added in app v1.5.1) sends `history_id`, which works identically
        *when the message has one* - but not every message does: a
        message the CODE27 backend classifies as a command rather than
        conversation (confirmed live: e.g. any Assist-bridge command
        recognized via spellbook or codeword, spoken or typed) never
        gets a `history_id` assigned - it stays permanently `0`, same as
        a message that predates the app's v1.5.1 history-id rollout, and
        such a message also never gets a spontaneous character reply.
        Sending `{"history_ids": [0]}` for one of these deletes *every*
        currently-zero-id message at once (a blunt, dangerous wildcard -
        confirmed live) rather than the one intended, so never do that;
        `chat_id` has no such ambiguity and is the only identifier
        guaranteed to work for both message classes, which is why this
        wrapper always uses it. 1.5.3 additionally takes the owning
        ``slot_number``.
        """
        body: dict[str, Any] = {"chat_ids": chat_ids}
        if slot is not None:
            body["slot_number"] = slot
        return await self._call("DELETE", "/data/user/chat/history/items", body=body)

    _MAX_CHAT_IMAGE_BYTES = 1_000_000

    async def send_chat_image(
        self,
        image: bytes,
        filename: str,
        content_type: str,
        text: str = "",
        slot: int = 1,
        language: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Send an image (optionally captioned) to a character slot.

        Returns ``(chat_id, image_chat_id)`` - the device records the
        caption and the image as two separate messages (confirmed live).
        Enforced client-side ahead of the upload: the device's own hard
        limit is 1MB (confirmed live, HTTP 400 otherwise) and isn't
        mentioned anywhere in the decompiled client code.
        """
        if len(image) > self._MAX_CHAT_IMAGE_BYTES:
            raise Code27Error(
                f"image is {len(image)} bytes, device limit is "
                f"{self._MAX_CHAT_IMAGE_BYTES} bytes (1MB)"
            )

        request: dict[str, Any] = {
            "request_id": str(uuid.uuid4()),
            "slot": slot,
            "input": {"type": "image_text"},
            "output": {"speech": "device"},
        }
        if text:
            request["input"]["text"] = text
        result = await self._call_multipart(
            "POST",
            "/data/user/chat/turns",
            fields={"request": json.dumps(request, separators=(",", ":"))},
            files=[("image", image, filename, content_type)],
        )
        ids = await self._turn_user_chat_ids(result.get("data") or {})
        return (ids[0] if ids else None, ids[1] if len(ids) > 1 else None)

    async def get_chat_image(self, uuid: str) -> tuple[str, bytes]:
        """Fetch a previously sent/received chat image by its `content`
        field (only messages with `content_type == "image"` carry a
        fetchable uuid there - confirmed live, see the audit's chat-image
        findings)."""
        return await self._call_binary(f"/resources/user/chat-images?uuid={quote(uuid)}")

    # -- text-to-speech audition (also usable as a plain announcement) --------

    async def tts_audition(self, voice_id: str, text: str, group_key: str) -> None:
        """Speak arbitrary text through the device using a character's voice.

        This is the same endpoint the web UI's Voice settings "PREVIEW"
        button uses, just with a caller-supplied ``text`` instead of the
        UI's fixed preview sentence - the API itself doesn't restrict it.
        """
        await self._call(
            "POST",
            "/data/user/tts_audition",
            body={"voice_id": voice_id, "text": text, "group_key": group_key},
        )

    async def get_tts_audition_state(self) -> dict[str, Any]:
        """Current playback state: STARTING/WAITING/PLAYING/ENDED/FAILED."""
        return (await self._call("GET", "/data/user/tts_audition/state")).get("data", {})

    # Dynamic voice-group listing (`ability_group_keys_path`) is deliberately
    # NOT implemented yet: the audit only confirms the *config key name* and
    # its live behavior via a real device test, never the literal resolved
    # REST path it points to (unlike every other endpoint in this file) - so
    # there is nothing here to call without guessing a path outright.
    # Needs that path confirmed (e.g. from the device's own config dump)
    # before this can be added.

    # -- memory cards ------------------------------------------------------
    # Base path and all fields confirmed live against a real device
    # (see the audit's Memory Cards section) - `PUT .../{id}` was
    # end-to-end tested, list/get/delete share its base by construction.

    async def list_memory_cards(
        self, character_id: str, page: int = 1, page_size: int = 20
    ) -> dict[str, Any]:
        return (
            await self._call(
                "GET",
                f"/data/user/memory/cards?characterId={quote(character_id)}"
                f"&page={page}&pageSize={page_size}",
            )
        ).get("data", {})

    async def get_memory_card(self, card_id: str, character_id: str) -> dict[str, Any]:
        return (
            await self._call(
                "GET", f"/data/user/memory/cards/{card_id}?characterId={quote(character_id)}"
            )
        ).get("data", {})

    async def update_memory_card(
        self,
        card_id: str,
        character_id: str,
        content: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        """Edit a memory card's content/category.

        The device marks the card `isUserEdited: true` permanently as soon
        as this is called, even if the new content is identical to the old
        one (confirmed live) - there is no way to undo that flag, so
        callers should warn the user before an edit, not after.
        """
        if content is None and category is None:
            raise Code27Error("update_memory_card needs content and/or category")
        body: dict[str, Any] = {"characterId": character_id}
        if content is not None:
            body["content"] = content
        if category is not None:
            body["category"] = category
        return (
            await self._call("PUT", f"/data/user/memory/cards/{card_id}", body=body)
        ).get("data", {})

    # -- diary (read-only on this firmware) ---------------------------------
    # Confirmed live: no favorite/reaction/delete endpoint works on the
    # device's local API (all HTTP 404) - only list/get are implemented
    # here, deliberately, so no write action can be offered that would
    # silently do nothing.

    async def list_diary_entries(
        self,
        character_id: str,
        slot: int,
        date: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        query = f"characterId={quote(character_id)}&slot={slot}&page={page}&pageSize={page_size}"
        if date:
            query += f"&date={quote(date)}"
        return (
            await self._call("GET", f"/data/user/diary/entries?{query}")
        ).get("data", {})

    async def get_diary_entry(self, entry_id: str, character_id: str) -> dict[str, Any]:
        return (
            await self._call(
                "GET", f"/data/user/diary/entries/{entry_id}?characterId={quote(character_id)}"
            )
        ).get("data", {})

    # -- light preview (ad-hoc solid color / simple effects) -------------------

    async def preview_light_color(
        self,
        red: int,
        green: int,
        blue: int,
        brightness: int = 220,
        effect: str = "STATIC",
        led_count: int = 17,
    ) -> None:
        """Render a single-color procedural layer via the (non-persisted) preview
        channel the Light Editor itself uses while designing a project.

        This never touches any saved light project; it's the same mechanism
        the device's own "preview" button in Lab -> Light Editor uses.
        """
        project = {
            "projectId": "ha_preview",
            "name": "Home Assistant",
            "version": 2,
            "mode": "normal",
            "ledCount": led_count,
            "durationMs": 3000,
            "segments": [
                {
                    "segmentId": "ha_segment",
                    "startMs": 0,
                    "durationMs": 3000,
                    "layers": [
                        {
                            "layerId": "ha_layer",
                            "owner": "ha_layer",
                            "kind": "procedural",
                            "ledRange": {"start": 0, "endInclusive": led_count - 1},
                            "enabled": True,
                            "priority": "primary",
                            "type": effect,
                            "brightness": brightness,
                            "speed": 60,
                            "primaryColor": {"r": red, "g": green, "b": blue},
                            "secondaryColor": {"r": 0, "g": 0, "b": 0},
                        }
                    ],
                }
            ],
            "normal": {},
            "reactive": {},
            "updatedAt": 1,
        }
        await self._call(
            "POST", "/data/user/light-projects/preview", body={"project": project}
        )

    async def stop_light_preview(self) -> None:
        await self._call("POST", "/data/user/light-projects/preview/stop")

    # -- misc ------------------------------------------------------------------

    async def play_sound(self) -> None:
        await self._sound_control("play")

    async def stop_sound(self) -> None:
        await self._sound_control("stop")

    async def _sound_control(self, action: str) -> None:
        # 1.5.3: POST /sound/control {"action": "play"|"stop"}.
        await self._call("POST", "/data/user/sound/control", body={"action": action})

    # -- device / conversation state, speech (1.5.3) ----------------------------

    async def get_device_state(self) -> dict[str, Any]:
        """``screen_on``, ``locked``, ``conversation_blocked`` and, while a
        block applies, ``block_reason`` / ``display_key``. Empty on firmware
        older than 1.5.3, which has no such endpoint."""
        try:
            return (await self._call("GET", "/data/user/device/state")).get("data", {})
        except Code27NotFoundError:
            return {}

    # -- device brightness / screen / mode (1.6.0) -------------------------------

    async def get_device_brightness(self) -> dict[str, Any]:
        """``level`` (0-100 or None) and ``writable``. Empty on firmware < 1.6.0."""
        try:
            return (await self._call("GET", "/data/user/device/brightness")).get("data", {})
        except Code27NotFoundError:
            return {}

    async def set_device_brightness(self, level: int) -> dict[str, Any]:
        level = max(0, min(100, int(level)))
        return await self._call("PUT", "/data/user/device/brightness", body={"level": level})

    async def get_device_screen(self) -> dict[str, Any]:
        """``on`` (bool/None), ``channels`` and ``command_sent``. Empty on firmware < 1.6.0."""
        try:
            return (await self._call("GET", "/data/user/device/screen")).get("data", {})
        except Code27NotFoundError:
            return {}

    async def set_device_screen(self, on: bool) -> dict[str, Any]:
        return await self._call("PUT", "/data/user/device/screen", body={"on": bool(on)})

    async def set_device_mode(self, game_mode: str) -> dict[str, Any]:
        """``game_mode`` is ``LIVE`` or ``MUSIC`` (1.6.0)."""
        mode = str(game_mode).upper()
        if mode not in {"LIVE", "MUSIC"}:
            raise ValueError("game_mode must be LIVE or MUSIC")
        return await self._call("PUT", "/data/user/device/mode", body={"game_mode": mode})

    async def get_conversation_state(self) -> dict[str, Any]:
        """``state`` (e.g. Idle / Thinking) and ``updated_at_ms``. Empty on
        firmware older than 1.5.3."""
        try:
            return (await self._call("GET", "/data/user/conversation/state")).get("data", {})
        except Code27NotFoundError:
            return {}

    async def get_ai_language(self) -> str | None:
        data = (await self._call("GET", "/data/user/ai-language")).get("data", {})
        value = data.get("ai_language") or data.get("language")
        return str(value) if value else None

    async def set_ai_language(self, language: str) -> None:
        await self._call("PUT", "/data/user/ai-language", body={"ai_language": language})

    async def get_character_loading_state(self) -> dict[str, Any]:
        return (await self._call("GET", "/data/user/character/loading/state")).get("data", {})

    async def speak_text(self, text: str, slot: int = 1) -> dict[str, Any] | None:
        """Let the current character read a fixed text aloud - no LLM call,
        no chat history; the device uses the character's own voice. Returns
        the accepted utterance (poll `get_utterance`), or ``None`` on
        firmware without such an endpoint."""
        body = {"request_id": str(uuid.uuid4()), "slot": slot, "text": text}
        try:
            result = await self._call("POST", "/data/user/tts/utterances", body=body)
        except Code27NotFoundError:
            return None
        return result.get("data", {})

    async def get_utterance(self, utterance_id: str) -> dict[str, Any]:
        return (
            await self._call(
                "GET", f"/data/user/tts/utterances/{quote(utterance_id, safe='')}"
            )
        ).get("data", {})

    async def cancel_utterance(self, utterance_id: str) -> None:
        await self._call(
            "POST", f"/data/user/tts/utterances/{quote(utterance_id, safe='')}/cancel"
        )

    # -- realtime (WebSocket) ---------------------------------------------

    async def build_ws_url(self, path: str = "/v1/ws/channel") -> str:
        """A freshly-signed URL for one of the device's WebSockets
        (default: the push-update channel; 1.6.0 adds the tool executor
        channel ``/v1/ws/tool-integrations/{id}/executor``).

        Same HMAC scheme as REST calls, just passed as query parameters
        since a WebSocket handshake can't carry custom headers - exactly how
        the device's own web UI authenticates its live channel.
        """
        if not self.local_secret or not self.user_id:
            raise Code27AuthError("not_paired")

        timestamp = str(int(time.time()))
        nonce = _nonce()
        body_hash = _sha256_hex("")

        canonical = "|".join(
            ["GET", path, timestamp, nonce, "empty", body_hash, self.user_id]
        )
        signature = _hmac_sha256_hex(self.local_secret, canonical)

        params = {
            "sig": signature,
            "nonce": nonce,
            "timestamp": timestamp,
            "payload_mode": "empty",
            "body_hash": body_hash,
            "user_id": self.user_id,
        }
        query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
        return f"ws://{self.host}:{self.port}{path}?{query}"

    # -- remote IP recovery (Phase 8) ---------------------------------------
    # Deliberately narrow: read-only, single remote request, and the
    # returned address is verified locally (reachable + confirmed same
    # machine_id) before ever being trusted - see the audit's own
    # recommended design ("Remote nur zur IP-Recovery, nicht als
    # genereller Fallback"). Chat, images and every other feature call
    # stay LAN-only regardless of whether this ever runs.

    async def _call_remote_system_info(self) -> dict[str, Any]:
        """GET .../data/user/system against the cloud relay instead of the
        LAN. Confirmed live: accepts the exact same LAN-HMAC signature as a
        direct device request, plus X-DEVICE-ID so the relay knows which
        device to answer for."""
        path = "/data/user/system"
        headers = {"Accept": "application/json"}
        headers.update(self._sign("GET", path, None))
        headers["X-DEVICE-ID"] = self.machine_id or ""

        url = _REMOTE_BASE_URL + "/v1/api" + path
        try:
            async with asyncio.timeout(10.0):
                resp = await self._session.request("GET", url, headers=headers)
                text = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise Code27ConnectionError(str(err)) from err
        return self._parse_json_response(resp.status, text).get("data", {})

    async def attempt_remote_recovery(self) -> str | None:
        """Try to recover this device's current LAN IP via the cloud relay
        after a connection failure. Returns the new, already-verified host
        on success, or ``None`` if recovery didn't happen or couldn't be
        confirmed - never raises, since to the caller both just mean
        "still unreachable".

        Gated by a cooldown (matching the native app's own LAN_COOLDOWN_SEC
        pattern) so a burst of failing requests doesn't hammer a production
        cloud we don't control with repeated recovery attempts - at most
        one single remote request per cooldown window, exactly as the
        audit's design calls for ("einmaliger GET").

        On success, `self.host` is updated so subsequent calls use the new
        address; the caller is responsible for persisting it (e.g. to the
        config entry) and for retrying whatever read triggered this - never
        blindly retry a *write* after this, since an ambiguous timeout
        could mean the original write already reached the device.
        """
        now = time.monotonic()
        if (
            self._last_recovery_attempt is not None
            and now - self._last_recovery_attempt < _REMOTE_RECOVERY_COOLDOWN_SEC
        ):
            return None
        self._last_recovery_attempt = now

        if not self.machine_id:
            return None  # can't ask "is this really my device?" without it

        try:
            remote_info = await self._call_remote_system_info()
        except Code27Error as err:
            _LOGGER.debug("Remote IP recovery: relay unreachable: %s", err)
            return None

        new_host = remote_info.get("local_ip")
        if not new_host or remote_info.get("machine_id") != self.machine_id:
            _LOGGER.debug("Remote IP recovery: relay gave no usable/matching address")
            return None

        previous_host = self.host
        self.host = new_host
        try:
            local_info = (await self._call("GET", "/data/user/system")).get("data", {})
        except Code27Error as err:
            _LOGGER.debug("Remote IP recovery: new address %s not reachable: %s", new_host, err)
            self.host = previous_host
            return None
        if local_info.get("machine_id") != self.machine_id:
            _LOGGER.debug("Remote IP recovery: new address %s answers as a different device", new_host)
            self.host = previous_host
            return None

        self._last_recovery_attempt = None  # a verified success resets the cooldown
        return new_host

    # -- low level ---------------------------------------------------------

    def _sign(
        self, method: str, path: str, body_str: str | None
    ) -> dict[str, str]:
        if not self.local_secret or not self.user_id:
            raise Code27AuthError("not_paired")

        url = ("/v1/api" + path).split("?", 1)[0]
        while "//" in url:
            url = url.replace("//", "/")

        timestamp = str(int(time.time()))
        nonce = _nonce()

        if body_str is None:
            payload_mode = "empty"
            body_hash = _sha256_hex("")
        else:
            payload_mode = "json"
            body_hash = _sha256_hex(body_str)

        canonical = "|".join(
            [method.upper(), url, timestamp, nonce, payload_mode, body_hash, self.user_id]
        )
        signature = _hmac_sha256_hex(self.local_secret, canonical)

        return {
            "X-Local-Sig": signature,
            "X-Local-Nonce": nonce,
            "X-Local-Timestamp": timestamp,
            "X-Local-Payload-Mode": payload_mode,
            "X-Local-Body-Hash": body_hash,
            "X-Local-User-Id": self.user_id,
        }

    async def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        signed: bool = True,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        url = self.base_url + "/v1/api" + path
        headers = {"Accept": "application/json"}

        # Sign (and later send) the *exact* same bytes: the server recomputes
        # the body hash from what it actually receives on the wire, so the
        # hash we sign has to match byte-for-byte, not just semantically.
        body_str: str | None = None
        if body is not None:
            body_str = json.dumps(body, separators=(",", ":"))
            headers["Content-Type"] = "application/json"

        if signed and not any(path.startswith(p) for p in _UNSIGNED_PATHS):
            headers.update(self._sign(method, path, body_str))

        try:
            async with asyncio.timeout(timeout):
                resp = await self._session.request(
                    method,
                    url,
                    data=body_str.encode("utf-8") if body_str is not None else None,
                    headers=headers,
                )
                text = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise Code27ConnectionError(str(err)) from err

        _hdrs = getattr(resp, "headers", None)
        self._update_clock_skew(_hdrs.get("Date") if _hdrs is not None else None)
        data = self._parse_json_response(resp.status, text)
        self._validate_response_data(method, path, data)
        return data

    def _update_clock_skew(self, date_header: str | None) -> None:
        if not date_header:
            return
        try:
            server_ts = email.utils.mktime_tz(email.utils.parsedate_tz(date_header))
        except (TypeError, ValueError):
            return
        skew = server_ts - time.time()
        self.clock_skew_s = skew
        if abs(skew) > 5:
            _LOGGER.warning(
                "CODE27 clock skew: device is %+.1f s from HA time; "
                "REQUEST_EXPIRED errors will occur above ~30 s (C11)",
                skew,
            )

    def _parse_json_response(self, status: int, text: str) -> dict[str, Any]:
        try:
            data = json.loads(text) if text else {}
        except ValueError as err:
            if status == 401:
                raise Code27AuthError() from err
            if status in (404, 405):
                raise Code27NotFoundError(f"http_{status}") from err
            raise Code27Error(f"invalid_response: http_{status}") from err
        if status == 401:
            code = data.get("code") if isinstance(data, dict) else None
            code_str = code if isinstance(code, str) else None
            if code_str == "REQUEST_EXPIRED":
                raise Code27RequestExpiredError()
            raise Code27AuthError(code_str)
        if not isinstance(data, dict):
            raise Code27Error("invalid_response: expected object envelope")
        if status >= 400 or data.get("success") is False:
            current_slot, request_slot = data.get("current_slot"), data.get("request_slot")
            if isinstance(current_slot, int) and isinstance(request_slot, int):
                raise Code27SlotMismatchError(current_slot, request_slot)
            if status in (404, 405):
                raise Code27NotFoundError(
                    data.get("message") or data.get("error") or data.get("code")
                    or f"http_{status}"
                )
            raise Code27Error(
                data.get("message") or data.get("error") or data.get("code")
                or f"http_{status}"
            )
        return data

    async def _call_multipart(
        self,
        method: str,
        path: str,
        fields: dict[str, str],
        files: list[tuple[str, bytes, str, str]],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """POST/PUT a multipart/form-data body.

        ``files`` is a list of ``(field_name, content, filename,
        content_type)`` - a list rather than a dict so the same field name
        can repeat (confirmed needed for `/music/upload`'s "files" field
        with more than one file; every other caller just uses one entry).
        Signed with the device's dedicated multipart mode: unlike JSON
        requests, the body itself is *not* hashed - the canonical string
        uses the fixed sentinel ``UNSIGNED-PAYLOAD`` as `body_hash`
        (confirmed live against a real device; reverse-engineered from the
        web bundle's request-signing code, which uses the same sentinel for
        every multipart upload regardless of actual body content).
        """
        if not self.local_secret or not self.user_id:
            raise Code27AuthError("not_paired")

        url = self.base_url + "/v1/api" + path
        canonical_path = ("/v1/api" + path).split("?", 1)[0]
        while "//" in canonical_path:
            canonical_path = canonical_path.replace("//", "/")

        timestamp = str(int(time.time()))
        nonce = _nonce()
        payload_mode = "multipart"
        body_hash = "UNSIGNED-PAYLOAD"
        canonical = "|".join(
            [method.upper(), canonical_path, timestamp, nonce, payload_mode, body_hash,
             self.user_id]
        )
        signature = _hmac_sha256_hex(self.local_secret, canonical)
        headers = {
            "Accept": "application/json",
            "X-Local-Sig": signature,
            "X-Local-Nonce": nonce,
            "X-Local-Timestamp": timestamp,
            "X-Local-Payload-Mode": payload_mode,
            "X-Local-Body-Hash": body_hash,
            "X-Local-User-Id": self.user_id,
        }

        # quote_fields=False: aiohttp would otherwise percent-encode the file
        # name ("My Song.mp3" -> "My%20Song.mp3") and the device stores it
        # literally. Browsers send the raw UTF-8 name, and so do we.
        form = aiohttp.FormData(quote_fields=False)
        for name, value in fields.items():
            form.add_field(name, value)
        for name, content, filename, content_type in files:
            form.add_field(name, content, filename=filename, content_type=content_type)

        try:
            async with asyncio.timeout(timeout):
                resp = await self._session.request(method, url, data=form, headers=headers)
                text = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise Code27ConnectionError(str(err)) from err

        return self._parse_json_response(resp.status, text)

    async def _call_binary(
        self, path: str, timeout: float = 15.0
    ) -> tuple[str, bytes]:
        """GET a binary resource (image), returning ``(content_type, body)``.

        On failure the device replies with the usual JSON error envelope
        instead of bytes, so a non-2xx response is parsed and raised the
        same way as a normal API error.
        """
        url = self.base_url + "/v1/api" + path
        headers = {"Accept": "*/*"}
        headers.update(self._sign("GET", path, None))

        try:
            async with asyncio.timeout(timeout):
                resp = await self._session.request("GET", url, headers=headers)
                raw = await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise Code27ConnectionError(str(err)) from err

        if resp.status >= 400:
            self._parse_json_response(resp.status, raw.decode("utf-8", errors="replace"))
        return resp.content_type or "application/octet-stream", raw

    _MUSIC_PATHS = {
        "/data/user/music/page", "/data/user/music/source",
        "/data/user/music/playback", "/data/user/music/audio-route",
        "/data/user/music/bluetooth", "/data/user/music/storage",
    }

    # Paths whose response *shape* the audit never actually confirmed (only
    # the resulting behavior/URL pattern) - guessing list-vs-dict here would
    # repeat the exact mistake that broke /chat/history/count against a real
    # device (its shape was implemented correctly in the wrapper method but
    # never added to `list_paths` below, so this same validator rejected the
    # real, live-confirmed response as "unexpected"). Left unvalidated on
    # purpose until a real device confirms the shape, rather than asserting
    # either way.
    _UNCONFIRMED_SHAPE_PATHS = {
        "/resources/system/original-portraits/manifest",
    }

    @staticmethod
    def _validate_response_data(method: str, path: str, response: dict) -> None:
        """Validate the resource shapes consumed by our typed wrappers."""
        path = path.split("?", 1)[0]
        if path in Code27Client._UNCONFIRMED_SHAPE_PATHS:
            return
        if path == "/data/user/chat/history":
            return  # list (<1.5.3) or {items,...} (1.5.3); checked by _history_page
        if method != "GET" and "data" not in response:
            return  # write acknowledgements may have no data (including 204)
        value = response.get("data")
        list_paths = {
            "/data/user/character/list", "/data/user/alarm/list", "/data/user/memos",
            "/data/user/light-projects", "/data/user/chat/history",
            "/data/user/chat/history/dates",
        }
        expect_list = method == "GET" and path in list_paths
        if not isinstance(value, list if expect_list else dict):
            raise Code27Error(f"invalid_response: unexpected data shape for {path}")
        if expect_list:
            if path.endswith("/dates"):
                if not all(isinstance(item, str) for item in value):
                    raise Code27Error("invalid_response: expected date strings")
            elif not all(isinstance(item, dict) for item in value):
                raise Code27Error("invalid_response: expected object list")
            if path in {"/data/user/alarm/list", "/data/user/memos", "/data/user/light-projects"}:
                if any(not isinstance(item.get("id"), str) or not item["id"] for item in value):
                    raise Code27Error("invalid_response: missing resource id")
        objects = value if expect_list and not path.endswith("/dates") else [value]
        # "version" means two different things depending on the endpoint:
        # a numeric revision counter on the music/* endpoints (what
        # _music_is_older compares), but a firmware version *string* on
        # /data/user/system - same JSON key, unrelated meaning. Only
        # enforce the numeric shape where it's actually used numerically.
        numeric_keys = ["slot_number", "hour", "minute", "ledCount", "next_fire_at_ms",
                         "updated_at_ms"]
        if path in Code27Client._MUSIC_PATHS:
            numeric_keys.append("version")
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            for key in numeric_keys:
                if key in obj and (not isinstance(obj[key], (int, float)) or isinstance(obj[key], bool)):
                    raise Code27Error(f"invalid_response: invalid {key}")
            for key in ("machine_id", "user_id", "challenge_id", "local_secret", "name",
                        "content", "role", "voice_id", "group_key", "ai_language", "state"):
                if key in obj and obj[key] is not None and not isinstance(obj[key], str):
                    raise Code27Error(f"invalid_response: invalid {key}")
