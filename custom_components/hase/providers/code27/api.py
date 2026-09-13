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
import hashlib
import hmac
import json
import logging
import random
import string
import time
from typing import Any
from urllib.parse import quote

import aiohttp

_LOGGER = logging.getLogger(__name__)

_NONCE_ALPHABET = string.digits + string.ascii_lowercase
_UNSIGNED_PATHS = ("/auth/bootstrap", "/auth/pair/request", "/auth/pair/confirm")

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


class Code27AuthError(Code27Error):
    """Raised when the stored pairing credentials are no longer valid."""

    def __init__(self, code: str | None = None) -> None:
        super().__init__(code or "auth_error")
        self.code = code


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

    async def create_alarm(self, alarm: dict[str, Any]) -> dict[str, Any]:
        return (await self._call("POST", "/data/user/alarm", body={"alarm": alarm})).get("data", {})

    async def update_alarm(self, alarm_id: str, alarm: dict[str, Any]) -> dict[str, Any]:
        return (
            await self._call("PUT", f"/data/user/alarm/{alarm_id}", body={"alarm": alarm})
        ).get("data", {})

    async def delete_alarm(self, alarm_id: str) -> None:
        await self._call("DELETE", f"/data/user/alarm/{alarm_id}")

    async def enable_alarm(self, alarm_id: str) -> None:
        await self._call("POST", f"/data/user/alarm/{alarm_id}/enable")

    async def disable_alarm(self, alarm_id: str) -> None:
        await self._call("POST", f"/data/user/alarm/{alarm_id}/disable")

    # -- memos (notes & reminders) ------------------------------------------

    async def list_memos(self) -> list[dict[str, Any]]:
        return (await self._call("GET", "/data/user/memos")).get("data", [])

    async def create_memo(self, memo: dict[str, Any]) -> dict[str, Any]:
        return (await self._call("POST", "/data/user/memos", body={"memo": memo})).get("data", {})

    async def update_memo(self, memo_id: str, memo: dict[str, Any]) -> dict[str, Any]:
        return (
            await self._call("PUT", f"/data/user/memos/{memo_id}", body={"memo": memo})
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
    }

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
        body = {"slot_number": slot_number, "device_id": device_id, **updates}
        return (await self._call("PUT", "/data/user/character", body=body)).get("data", {})

    async def get_current_slot(self, scope: str = "chat") -> dict[str, Any]:
        return (await self._call("GET", f"/data/user/slot?scope={scope}")).get("data", {})

    async def update_selected_slot(self, slot_number: int, scope: str = "chat") -> None:
        await self._call(
            "PUT", "/data/user/slot", body={"slot_number": slot_number, "scope": scope}
        )

    # -- portraits -------------------------------------------------------------
    # Custom-cloth thumbnail (rendered VRM portrait) first, falling back to the
    # device-wide stock portrait, exactly the resolution order confirmed live
    # against a real device (see the audit's "Portrait-Picker" findings) -
    # not every slot/cloth combination has either.

    async def get_cloth_uploaded_models(self) -> list[dict[str, Any]]:
        """Custom-cloth filenames keyed by slot/cloth-id, straight from the
        device instead of having to parse a character's `custom_cloths` CSV
        string ourselves."""
        return (await self._call("GET", "/data/user/cloth/uploaded-models")).get(
            "data", {}
        ).get("models", [])

    async def get_original_portraits_manifest(self) -> list[dict[str, Any]]:
        """Which stock slot/cloth combinations have a bundled portrait at
        all - device-wide, not account/slot-specific. Only covers the
        factory-default characters (confirmed live: slots 1-3, not 4-5)."""
        return (await self._call("GET", "/resources/system/original-portraits/manifest")).get(
            "data", []
        )

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
        for model in await self.get_cloth_uploaded_models():
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
        try:
            return await self._call_binary(
                f"/resources/system/assets/original-portraits/{slot_number}_{cloth_id}.png"
            )
        except Code27Error:
            return None

    # -- chat ------------------------------------------------------------------

    async def get_chat_history_dates(self, slot: int = 1) -> list[str]:
        return (
            await self._call("GET", f"/data/user/chat/history/dates?slot={slot}")
        ).get("data", [])

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
        result = await self._call(
            "GET",
            f"/data/user/chat/history/latest?date={latest_date}&slot={slot}&limit={limit}",
        )
        return result.get("data", [])

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
            recent = await self._call(
                "GET",
                f"/data/user/chat/history/latest?date={dates[-1]}&slot={slot}&limit=30",
            )
            messages = recent.get("data", [])
            if any(str(m.get("chat_id")) == after_chat_id for m in messages):
                return dates[-1], sorted(messages, key=lambda m: str(m.get("created_at") or ""))
        selected = [date for date in dates if date >= (dates[-1] if since_date is None else since_date)]
        messages = []
        for date in selected:
            result = await self._call(
                "GET", f"/data/user/chat/history?date={date}&slot={slot}"
            )
            messages.extend(result.get("data", []))
        return dates[-1], sorted(messages, key=lambda m: str(m.get("created_at") or ""))

    async def send_chat_text(
        self, text: str, slot: int = 1, language: str | None = None
    ) -> str | None:
        body: dict[str, Any] = {"text": text, "slot": slot}
        if language:
            body["language"] = language
        result = await self._call("POST", "/data/user/chat/text", body=body)
        chat_id = result.get("chat_id")
        return str(chat_id) if chat_id is not None else None

    async def get_chat_history_count(self) -> list[dict[str, Any]]:
        """One call, every slot's state at once: `[{slot, slot_id,
        messages: [...]}...]`. Confirmed live at `/chat/history/count` (the
        GDScript-derived `chat_history_batch_path`/`/batch` guess was wrong).
        Only for state/card sync (which character last said what, at a
        glance) - the normal per-active-slot event pipeline
        (`get_chat_history_snapshot`, polled in coordinator.py) is
        untouched by this and stays the only source of
        `EVENT_USER_MESSAGE`/`EVENT_ASSISTANT_MESSAGE`."""
        return (await self._call("GET", "/data/user/chat/history/count")).get("data", [])

    async def delete_chat_messages(self, chat_ids: list[str]) -> dict[str, Any]:
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
        wrapper always uses it.
        """
        return await self._call(
            "DELETE",
            "/data/user/chat/history/items",
            body={"chat_ids": chat_ids},
        )

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
        fields = {"text": text, "slot": str(slot)}
        if language:
            fields["language"] = language
        result = await self._call_multipart(
            "POST",
            "/data/user/chat/image-text",
            fields=fields,
            files=[("image", image, filename, content_type)],
        )
        chat_id = result.get("chat_id")
        image_chat_id = result.get("image_chat_id")
        return (
            str(chat_id) if chat_id is not None else None,
            str(image_chat_id) if image_chat_id is not None else None,
        )

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
        await self._call("POST", "/data/user/sound/play")

    async def stop_sound(self) -> None:
        await self._call("POST", "/data/user/sound/stop")

    # -- realtime (WebSocket) ---------------------------------------------

    async def build_ws_url(self) -> str:
        """A freshly-signed URL for the device's push-update WebSocket.

        Same HMAC scheme as REST calls, just passed as query parameters
        since a WebSocket handshake can't carry custom headers - exactly how
        the device's own web UI authenticates its live channel.
        """
        if not self.local_secret or not self.user_id:
            raise Code27AuthError("not_paired")

        path = "/v1/ws/channel"
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

        data = self._parse_json_response(resp.status, text)
        self._validate_response_data(method, path, data)
        return data

    def _parse_json_response(self, status: int, text: str) -> dict[str, Any]:
        try:
            data = json.loads(text) if text else {}
        except ValueError as err:
            if status == 401:
                raise Code27AuthError() from err
            raise Code27Error(f"invalid_response: http_{status}") from err
        if status == 401:
            code = data.get("code") if isinstance(data, dict) else None
            raise Code27AuthError(code if isinstance(code, str) else None)
        if not isinstance(data, dict):
            raise Code27Error("invalid_response: expected object envelope")
        if status >= 400 or data.get("success") is False:
            current_slot, request_slot = data.get("current_slot"), data.get("request_slot")
            if isinstance(current_slot, int) and isinstance(request_slot, int):
                raise Code27SlotMismatchError(current_slot, request_slot)
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

        form = aiohttp.FormData()
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
        "/data/user/music/bluetooth",
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
        if method != "GET" and "data" not in response:
            return  # write acknowledgements may have no data (including 204)
        value = response.get("data")
        list_paths = {
            "/data/user/character/list", "/data/user/alarm/list", "/data/user/memos",
            "/data/user/light-projects", "/data/user/chat/history",
            "/data/user/chat/history/latest", "/data/user/chat/history/dates",
            "/data/user/chat/history/count",
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
