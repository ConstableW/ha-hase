"""Media player for CODE27's built-in music playback."""
from __future__ import annotations

import logging

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ...const import DOMAIN
from .api import Code27DeviceError
from .const import MUSIC_SOURCES
from .coordinator import Code27Coordinator
from .entity import Code27Entity

_LOGGER = logging.getLogger(__name__)

_STATUS_MAP = {
    "PLAYING": MediaPlayerState.PLAYING,
    "PAUSED": MediaPlayerState.PAUSED,
    "IDLE": MediaPlayerState.IDLE,
}

# error_code values the device is confirmed (live) to return from
# /data/user/music/control - translated into messages that explain what to
# actually do, instead of a raw device code surfacing as "Unknown error".
_CONTROL_ERROR_MESSAGES = {
    "MODE_INACTIVE": (
        "CODIE's music mode isn't open on the device itself - open it on "
        "the device's own screen first, HA cannot do this remotely."
    ),
    "INVALID_ARGUMENT": (
        "CODIE has no active track to resume - select one first (e.g. via "
        "play_media) before pressing play."
    ),
}


async def _run_control(action_coro) -> None:
    """Run a music_control() call, turning a device-reported failure into a
    clear HomeAssistantError instead of the button silently doing nothing."""
    try:
        await action_coro
    except Code27DeviceError as err:
        message = _CONTROL_ERROR_MESSAGES.get(err.error_code, str(err))
        raise HomeAssistantError(message) from err


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Code27Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([Code27MediaPlayer(coordinator)])


class Code27MediaPlayer(Code27Entity, MediaPlayerEntity):
    """The music playback exposed by the CODE27 device."""

    _attr_translation_key = "music"
    _attr_source_list = MUSIC_SOURCES
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.BROWSE_MEDIA
    )

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_music"

    @property
    def state(self) -> MediaPlayerState:
        status = self.coordinator.data.music_playback.get("status", "IDLE")
        return _STATUS_MAP.get(status, MediaPlayerState.IDLE)

    @property
    def media_title(self) -> str | None:
        return self.coordinator.data.music_playback.get("track_name") or None

    @property
    def media_duration(self) -> int | None:
        duration_ms = self.coordinator.data.music_playback.get("duration_ms")
        return int(duration_ms / 1000) if duration_ms else None

    @property
    def media_position(self) -> int | None:
        position_ms = self.coordinator.data.music_playback.get("position_ms")
        return int(position_ms / 1000) if position_ms is not None else None

    @property
    def volume_level(self) -> float | None:
        playback = self.coordinator.data.music_playback
        volume = playback.get("volume_level")
        volume_max = playback.get("volume_max") or 100
        if volume is None:
            return None
        return volume / volume_max

    @property
    def source(self) -> str | None:
        return self.coordinator.data.music_source.get("source")

    async def async_media_play(self) -> None:
        await _run_control(self.coordinator.client.music_control("PLAY"))
        await self.coordinator.async_request_refresh()

    async def async_media_pause(self) -> None:
        await _run_control(self.coordinator.client.music_control("PAUSE"))
        await self.coordinator.async_request_refresh()

    async def async_media_next_track(self) -> None:
        await _run_control(self.coordinator.client.music_control("NEXT"))
        await self.coordinator.async_request_refresh()

    async def async_media_previous_track(self) -> None:
        await _run_control(self.coordinator.client.music_control("PREVIOUS"))
        await self.coordinator.async_request_refresh()

    async def async_set_volume_level(self, volume: float) -> None:
        await _run_control(
            self.coordinator.client.music_control(
                "SET_VOLUME", volume_level=round(volume * 100)
            )
        )
        await self.coordinator.async_request_refresh()

    async def async_select_source(self, source: str) -> None:
        await self.coordinator.client.set_music_source(source)
        await self.coordinator.async_request_refresh()

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs
    ) -> None:
        """Play a specific track by its `track_id` (the "local:<path>"
        string confirmed live) - usually reached via the media browser
        below, but works with any track_id passed directly too."""
        await _run_control(
            self.coordinator.client.music_control("PLAY", track_id=media_id)
        )
        await self.coordinator.async_request_refresh()

    async def async_browse_media(
        self, media_content_type: str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        storage = self.coordinator.data.music_storage
        music_files = storage.get("music_files") or []

        if not media_content_id or media_content_id == "root":
            folders, root_files = _group_music_files(music_files)
            children = [
                BrowseMedia(
                    title=folder,
                    media_class=MediaClass.DIRECTORY,
                    media_content_id=f"folder:{folder}",
                    media_content_type=MediaType.MUSIC,
                    can_play=False,
                    can_expand=True,
                )
                for folder in folders
            ] + [_track_browse_item(f) for f in root_files]
            return BrowseMedia(
                title="CODE27 Music",
                media_class=MediaClass.DIRECTORY,
                media_content_id="root",
                media_content_type=MediaType.MUSIC,
                can_play=False,
                can_expand=True,
                children=children,
            )

        if media_content_id.startswith("folder:"):
            folder = media_content_id[len("folder:"):]
            children = [
                _track_browse_item(f) for f in _files_in_folder(music_files, folder)
            ]
            return BrowseMedia(
                title=folder,
                media_class=MediaClass.DIRECTORY,
                media_content_id=media_content_id,
                media_content_type=MediaType.MUSIC,
                can_play=False,
                can_expand=True,
                children=children,
            )

        raise HomeAssistantError(f"Unknown media_content_id: {media_content_id}")


# -- media browser tree building (pure, unit-testable without BrowseMedia) --
# `path` grouping is based on the one confirmed live example
# ("Rockkapella/song.mp3" for a subfolder, "song.mp3" for root-level) - not
# verified against `music_storage.root_path` possibly being an absolute
# prefix instead. Degrades gracefully either way (one oddly-named top-level
# "folder" instead of a crash) - flagged for live verification.

def _group_music_files(music_files: list[dict]) -> tuple[list[str], list[dict]]:
    """(sorted subfolder names, root-level files)."""
    folders: set[str] = set()
    root_files: list[dict] = []
    for entry in music_files:
        path = entry.get("path") or ""
        if "/" in path:
            folders.add(path.split("/", 1)[0])
        else:
            root_files.append(entry)
    return sorted(folders), root_files


def _files_in_folder(music_files: list[dict], folder: str) -> list[dict]:
    prefix = f"{folder}/"
    return [f for f in music_files if (f.get("path") or "").startswith(prefix)]


def _track_browse_item(file: dict) -> BrowseMedia:
    path = file.get("path") or ""
    name = file.get("name") or path.rsplit("/", 1)[-1] or path
    return BrowseMedia(
        title=name,
        media_class=MediaClass.MUSIC,
        media_content_id=f"local:{path}",
        media_content_type=MediaType.MUSIC,
        can_play=True,
        can_expand=False,
    )
