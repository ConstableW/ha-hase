"""CODE27 device services exposed under the HASE domain."""

from __future__ import annotations

import mimetypes
from pathlib import Path

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from ...const import DOMAIN
from .api import Code27Error, Code27SlotMismatchError
from .bridge import async_speak_text
from .const import ALARM_DAY_BITS
from .coordinator import Code27Coordinator

SERVICE_CREATE_ALARM = "create_alarm"
SERVICE_CREATE_ALARM_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
        vol.Required("hour"): vol.All(int, vol.Range(min=0, max=23)),
        vol.Required("minute"): vol.All(int, vol.Range(min=0, max=59)),
        vol.Optional("label", default=""): str,
        vol.Optional("days", default=[]): vol.All(
            cv.ensure_list, [vol.In(ALARM_DAY_BITS)]
        ),
        vol.Optional("enabled", default=True): bool,
    }
)

SERVICE_UPDATE_ALARM = "update_alarm"
SERVICE_UPDATE_ALARM_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
        vol.Required("alarm_id"): str,
        vol.Required("hour"): vol.All(int, vol.Range(min=0, max=23)),
        vol.Required("minute"): vol.All(int, vol.Range(min=0, max=59)),
        vol.Optional("label", default=""): str,
        vol.Optional("days", default=[]): vol.All(
            cv.ensure_list, [vol.In(ALARM_DAY_BITS)]
        ),
        vol.Optional("enabled", default=True): bool,
    }
)

SERVICE_DELETE_ALARM = "delete_alarm"
SERVICE_DELETE_ALARM_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
        vol.Required("alarm_id"): str,
    }
)

SERVICE_SEND_CHAT_MESSAGE = "send_chat_message"
SERVICE_SEND_CHAT_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
        vol.Required("message"): str,
        vol.Optional("slot"): int,
        vol.Optional("language"): str,
    }
)

SERVICE_SEND_CHAT_IMAGE = "send_chat_image"
SERVICE_SEND_CHAT_IMAGE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
        vol.Required("filename"): str,
        vol.Optional("message", default=""): str,
        vol.Optional("slot"): int,
        vol.Optional("language"): str,
    }
)

SERVICE_SET_CHARACTER_VOICE = "set_character_voice"
SERVICE_SET_CHARACTER_VOICE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
        vol.Required("slot"): int,
        vol.Required("voice_id"): str,
        vol.Optional("group_key"): str,
    }
)

SERVICE_UPDATE_MEMORY_CARD = "update_memory_card"
SERVICE_UPDATE_MEMORY_CARD_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
        vol.Required("character_id"): str,
        vol.Required("card_id"): str,
        vol.Optional("content"): str,
        vol.Optional("category"): str,
    }
)

SERVICE_GET_MEMORY_CARDS = "get_memory_cards"
SERVICE_GET_MEMORY_CARDS_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
        vol.Required("character_id"): str,
        vol.Optional("page", default=1): int,
        vol.Optional("page_size", default=20): int,
    }
)

SERVICE_GET_DIARY_ENTRIES = "get_diary_entries"
SERVICE_GET_DIARY_ENTRIES_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
        vol.Required("character_id"): str,
        vol.Required("slot"): int,
        vol.Optional("date"): str,
        vol.Optional("page", default=1): int,
        vol.Optional("page_size", default=20): int,
    }
)

SERVICE_GET_DIARY_ENTRY = "get_diary_entry"
SERVICE_GET_DIARY_ENTRY_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
        vol.Required("character_id"): str,
        vol.Required("entry_id"): str,
    }
)

SERVICE_UPLOAD_MUSIC = "upload_music"
SERVICE_UPLOAD_MUSIC_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
        vol.Required("filename"): str,
        vol.Optional("play", default=False): bool,
    }
)

SERVICE_DELETE_MUSIC_FILE = "delete_music_file"
SERVICE_DELETE_MUSIC_FILE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
        vol.Required("path"): str,
    }
)

SERVICE_NOTIFY = "notify"
SERVICE_NOTIFY_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
        vol.Optional("message"): str,
        vol.Optional("rgb_color", default=[255, 0, 0]): vol.All(
            cv.ensure_list,
            vol.Length(min=3, max=3),
            [vol.All(int, vol.Range(min=0, max=255))],
        ),
        vol.Optional("speak_text"): str,
    }
)

SERVICE_NOTIFY_CLEAR = "notify_clear"
SERVICE_NOTIFY_CLEAR_SCHEMA = vol.Schema({vol.Required("config_entry_id"): str})

SERVICE_SPEAK = "speak"
SERVICE_SPEAK_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
        vol.Required("text"): str,
    }
)


def async_register_services(hass: HomeAssistant) -> None:
    def _get_coordinator(call: ServiceCall) -> Code27Coordinator:
        """Look up the target device for a service call. Raises a clean,
        user-facing ServiceValidationError (shown in the UI/automation
        trace as a normal validation failure) instead of letting a
        removed/unloaded/mistyped config_entry_id fall through as a raw
        KeyError - a bare dict lookup gave no indication of what went
        wrong or which services even take this field."""
        entry_id = call.data["config_entry_id"]
        coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            raise ServiceValidationError(
                f"No CODE27 device found for config_entry_id '{entry_id}' "
                "- it may have been removed, or the integration entry is "
                "not currently loaded."
            )
        return coordinator

    async def _handle_create_alarm(call: ServiceCall) -> None:
        target_coordinator = _get_coordinator(call)
        days_mask = 0
        for day in call.data.get("days", []):
            days_mask |= ALARM_DAY_BITS[day]
        await target_coordinator.client.create_alarm(
            {
                "hour": call.data["hour"],
                "minute": call.data["minute"],
                "label": call.data.get("label", ""),
                "repeat_mask": days_mask,
                "enabled": call.data.get("enabled", True),
            }
        )
        await target_coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, SERVICE_CREATE_ALARM):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CREATE_ALARM,
            _handle_create_alarm,
            schema=SERVICE_CREATE_ALARM_SCHEMA,
        )

    async def _handle_update_alarm(call: ServiceCall) -> None:
        target_coordinator = _get_coordinator(call)
        days_mask = 0
        for day in call.data.get("days", []):
            days_mask |= ALARM_DAY_BITS[day]
        await target_coordinator.client.update_alarm(
            call.data["alarm_id"],
            {
                "hour": call.data["hour"],
                "minute": call.data["minute"],
                "label": call.data.get("label", ""),
                "repeat_mask": days_mask,
                "enabled": call.data.get("enabled", True),
            },
        )
        await target_coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, SERVICE_UPDATE_ALARM):
        hass.services.async_register(
            DOMAIN,
            SERVICE_UPDATE_ALARM,
            _handle_update_alarm,
            schema=SERVICE_UPDATE_ALARM_SCHEMA,
        )

    async def _handle_delete_alarm(call: ServiceCall) -> None:
        target_coordinator = _get_coordinator(call)
        await target_coordinator.client.delete_alarm(call.data["alarm_id"])
        await target_coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, SERVICE_DELETE_ALARM):
        hass.services.async_register(
            DOMAIN,
            SERVICE_DELETE_ALARM,
            _handle_delete_alarm,
            schema=SERVICE_DELETE_ALARM_SCHEMA,
        )

    async def _handle_send_chat_message(call: ServiceCall) -> None:
        target_coordinator = _get_coordinator(call)
        slot = call.data.get("slot") or target_coordinator.data.current_slot.get(
            "slot_number", 1
        )
        try:
            await target_coordinator.client.send_chat_text(
                call.data["message"], slot, call.data.get("language")
            )
        except Code27SlotMismatchError as err:
            raise ServiceValidationError(
                f"Cannot send to character slot {err.request_slot}: CODIE is "
                f"currently on slot {err.current_slot}. Switch the active "
                "slot first (e.g. via the slot select entity)."
            ) from err

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_CHAT_MESSAGE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_CHAT_MESSAGE,
            _handle_send_chat_message,
            schema=SERVICE_SEND_CHAT_MESSAGE_SCHEMA,
        )

    async def _handle_send_chat_image(call: ServiceCall) -> None:
        target_coordinator = _get_coordinator(call)
        path = Path(call.data["filename"])
        if not hass.config.is_allowed_path(str(path)):
            raise ServiceValidationError(
                f"'{path}' is not in an allowed directory - add its folder "
                "under homeassistant: allowlist_external_dirs."
            )
        if not await hass.async_add_executor_job(path.is_file):
            raise ServiceValidationError(f"File not found: {path}")
        content_type = mimetypes.guess_type(path.name)[0] or ""
        if not content_type.startswith("image/"):
            raise ServiceValidationError(
                f"'{path.name}' is not a recognized image file (guessed type: "
                f"'{content_type or 'unknown'}')."
            )
        image = await hass.async_add_executor_job(path.read_bytes)
        slot = call.data.get("slot") or target_coordinator.data.current_slot.get(
            "slot_number", 1
        )
        try:
            await target_coordinator.client.send_chat_image(
                image, path.name, content_type, call.data["message"], slot,
                call.data.get("language"),
            )
        except Code27SlotMismatchError as err:
            raise ServiceValidationError(
                f"Cannot send to character slot {err.request_slot}: CODIE is "
                f"currently on slot {err.current_slot}. Switch the active "
                "slot first (e.g. via the slot select entity)."
            ) from err
        except Code27Error as err:
            raise ServiceValidationError(str(err)) from err

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_CHAT_IMAGE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_CHAT_IMAGE,
            _handle_send_chat_image,
            schema=SERVICE_SEND_CHAT_IMAGE_SCHEMA,
        )

    async def _handle_set_character_voice(call: ServiceCall) -> None:
        target_coordinator = _get_coordinator(call)
        device_id = target_coordinator.data.system.get("machine_id")
        if not device_id:
            raise ServiceValidationError(
                "Device's machine_id is not known yet - the integration may "
                "still be starting up."
            )
        updates: dict[str, str] = {"voice_id": call.data["voice_id"]}
        if "group_key" in call.data:
            updates["group_key"] = call.data["group_key"]
        try:
            await target_coordinator.client.update_character_setting(
                call.data["slot"], device_id, **updates
            )
        except Code27Error as err:
            if "CLOTH_UPLOAD_IN_PROGRESS" in str(err):
                raise ServiceValidationError(
                    "Cannot change the voice right now: a cloth (outfit) "
                    "upload is in progress on this character slot. Try "
                    "again once it finishes."
                ) from err
            raise ServiceValidationError(str(err)) from err
        await target_coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, SERVICE_SET_CHARACTER_VOICE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_CHARACTER_VOICE,
            _handle_set_character_voice,
            schema=SERVICE_SET_CHARACTER_VOICE_SCHEMA,
        )

    async def _handle_update_memory_card(call: ServiceCall) -> None:
        target_coordinator = _get_coordinator(call)
        try:
            await target_coordinator.client.update_memory_card(
                call.data["card_id"], call.data["character_id"],
                call.data.get("content"), call.data.get("category"),
            )
        except Code27Error as err:
            raise ServiceValidationError(str(err)) from err

    if not hass.services.has_service(DOMAIN, SERVICE_UPDATE_MEMORY_CARD):
        hass.services.async_register(
            DOMAIN,
            SERVICE_UPDATE_MEMORY_CARD,
            _handle_update_memory_card,
            schema=SERVICE_UPDATE_MEMORY_CARD_SCHEMA,
        )

    async def _handle_get_memory_cards(call: ServiceCall) -> ServiceResponse:
        target_coordinator = _get_coordinator(call)
        try:
            return await target_coordinator.client.list_memory_cards(
                call.data["character_id"], call.data["page"], call.data["page_size"]
            )
        except Code27Error as err:
            raise ServiceValidationError(str(err)) from err

    if not hass.services.has_service(DOMAIN, SERVICE_GET_MEMORY_CARDS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_MEMORY_CARDS,
            _handle_get_memory_cards,
            schema=SERVICE_GET_MEMORY_CARDS_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    async def _handle_get_diary_entries(call: ServiceCall) -> ServiceResponse:
        target_coordinator = _get_coordinator(call)
        try:
            return await target_coordinator.client.list_diary_entries(
                call.data["character_id"], call.data["slot"], call.data.get("date"),
                call.data["page"], call.data["page_size"],
            )
        except Code27Error as err:
            raise ServiceValidationError(str(err)) from err

    if not hass.services.has_service(DOMAIN, SERVICE_GET_DIARY_ENTRIES):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_DIARY_ENTRIES,
            _handle_get_diary_entries,
            schema=SERVICE_GET_DIARY_ENTRIES_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    async def _handle_get_diary_entry(call: ServiceCall) -> ServiceResponse:
        target_coordinator = _get_coordinator(call)
        try:
            return await target_coordinator.client.get_diary_entry(
                call.data["entry_id"], call.data["character_id"]
            )
        except Code27Error as err:
            raise ServiceValidationError(str(err)) from err

    if not hass.services.has_service(DOMAIN, SERVICE_GET_DIARY_ENTRY):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_DIARY_ENTRY,
            _handle_get_diary_entry,
            schema=SERVICE_GET_DIARY_ENTRY_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    async def _handle_upload_music(call: ServiceCall) -> None:
        target_coordinator = _get_coordinator(call)
        path = Path(call.data["filename"])
        if not hass.config.is_allowed_path(str(path)):
            raise ServiceValidationError(
                f"'{path}' is not in an allowed directory - add its folder "
                "under homeassistant: allowlist_external_dirs."
            )
        if not await hass.async_add_executor_job(path.is_file):
            raise ServiceValidationError(f"File not found: {path}")
        content_type = mimetypes.guess_type(path.name)[0] or ""
        if not content_type.startswith("audio/"):
            raise ServiceValidationError(
                f"'{path.name}' is not a recognized audio file (guessed type: "
                f"'{content_type or 'unknown'}')."
            )
        content = await hass.async_add_executor_job(path.read_bytes)
        try:
            result = await target_coordinator.client.upload_music(
                [(content, path.name, content_type)]
            )
        except Code27Error as err:
            raise ServiceValidationError(str(err)) from err
        rejected = result.get("rejected") or []
        if rejected:
            raise ServiceValidationError(f"Device rejected the upload: {rejected}")
        uploaded = result.get("uploaded") or []
        if call.data["play"] and uploaded:
            device_path = uploaded[0].get("path")
            if device_path:
                try:
                    await target_coordinator.client.music_control(
                        "PLAY", track_id=f"local:{device_path}"
                    )
                except Code27Error as err:
                    raise ServiceValidationError(str(err)) from err
        await target_coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, SERVICE_UPLOAD_MUSIC):
        hass.services.async_register(
            DOMAIN,
            SERVICE_UPLOAD_MUSIC,
            _handle_upload_music,
            schema=SERVICE_UPLOAD_MUSIC_SCHEMA,
        )

    async def _handle_delete_music_file(call: ServiceCall) -> None:
        target_coordinator = _get_coordinator(call)
        try:
            await target_coordinator.client.delete_music_file(call.data["path"])
        except Code27Error as err:
            raise ServiceValidationError(str(err)) from err
        await target_coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, SERVICE_DELETE_MUSIC_FILE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_DELETE_MUSIC_FILE,
            _handle_delete_music_file,
            schema=SERVICE_DELETE_MUSIC_FILE_SCHEMA,
        )

    async def _handle_notify(call: ServiceCall) -> None:
        target_coordinator = _get_coordinator(call)
        color = tuple(call.data.get("rgb_color", [255, 0, 0]))
        await target_coordinator.async_trigger_notification(
            call.data.get("message"), color
        )

        speak_text = call.data.get("speak_text")
        if speak_text:
            await async_speak_text(
                hass,
                target_coordinator.entry,
                target_coordinator,
                speak_text,
            )

    if not hass.services.has_service(DOMAIN, SERVICE_NOTIFY):
        hass.services.async_register(
            DOMAIN, SERVICE_NOTIFY, _handle_notify, schema=SERVICE_NOTIFY_SCHEMA
        )

    async def _handle_notify_clear(call: ServiceCall) -> None:
        target_coordinator = _get_coordinator(call)
        await target_coordinator.async_acknowledge_notification()

    if not hass.services.has_service(DOMAIN, SERVICE_NOTIFY_CLEAR):
        hass.services.async_register(
            DOMAIN,
            SERVICE_NOTIFY_CLEAR,
            _handle_notify_clear,
            schema=SERVICE_NOTIFY_CLEAR_SCHEMA,
        )

    async def _handle_speak(call: ServiceCall) -> None:
        target_coordinator = _get_coordinator(call)
        await async_speak_text(
            hass,
            target_coordinator.entry,
            target_coordinator,
            call.data["text"],
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SPEAK):
        hass.services.async_register(
            DOMAIN, SERVICE_SPEAK, _handle_speak, schema=SERVICE_SPEAK_SCHEMA
        )


def async_unregister_services(hass: HomeAssistant) -> None:
    hass.services.async_remove(DOMAIN, SERVICE_CREATE_ALARM)
    hass.services.async_remove(DOMAIN, SERVICE_UPDATE_ALARM)
    hass.services.async_remove(DOMAIN, SERVICE_DELETE_ALARM)
    hass.services.async_remove(DOMAIN, SERVICE_SEND_CHAT_MESSAGE)
    hass.services.async_remove(DOMAIN, SERVICE_SEND_CHAT_IMAGE)
    hass.services.async_remove(DOMAIN, SERVICE_SET_CHARACTER_VOICE)
    hass.services.async_remove(DOMAIN, SERVICE_UPDATE_MEMORY_CARD)
    hass.services.async_remove(DOMAIN, SERVICE_GET_MEMORY_CARDS)
    hass.services.async_remove(DOMAIN, SERVICE_GET_DIARY_ENTRIES)
    hass.services.async_remove(DOMAIN, SERVICE_GET_DIARY_ENTRY)
    hass.services.async_remove(DOMAIN, SERVICE_UPLOAD_MUSIC)
    hass.services.async_remove(DOMAIN, SERVICE_DELETE_MUSIC_FILE)
    hass.services.async_remove(DOMAIN, SERVICE_NOTIFY)
    hass.services.async_remove(DOMAIN, SERVICE_NOTIFY_CLEAR)
    hass.services.async_remove(DOMAIN, SERVICE_SPEAK)
