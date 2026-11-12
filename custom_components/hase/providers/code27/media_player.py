"""Media player for CODE27's built-in music playback."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

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
from .music import (
    build_browse_entries,
    control_error_message,
    group_music_files,
    music_files_in_folder,
    track_from_music_file,
)
from .music_runtime import async_get_music_runtime

_LOGGER = logging.getLogger(__name__)

_STATUS_MAP = {
    "PLAYING": MediaPlayerState.PLAYING,
    "PAUSED": MediaPlayerState.PAUSED,
    "IDLE": MediaPlayerState.IDLE,
}

#: The ``music_playback`` fields that together make up one *position reading*.
#: A change in any of them means the device published a new sample, and that is
#: the only thing allowed to refresh ``media_position_updated_at``:
#: ``track_id`` (a new track re-bases the position), ``status`` (a
#: play/pause/stop transition re-bases it), ``position_ms``/``duration_ms``
#: (the reading itself) and the device's own ``updated_at_ms``/``version``
#: revision markers.
#:
#: The markers are the same device-side revision pair the coordinator already
#: uses to order competing snapshots (``coordinator._music_is_older``), and the
#: captured payloads show them as *change* stamps rather than "now" stamps
#: (`music_page` reports ``updated_at_ms: 0, version: 0`` until something
#: happens). They therefore prove a sample is new even when it repeats the same
#: ``position_ms`` - and, just as important, an unchanged payload carries no
#: such evidence, so a coordinator cycle that merely re-delivers the identical
#: snapshot cannot restamp the position.
_POSITION_SAMPLE_FIELDS = (
    "track_id",
    "status",
    "position_ms",
    "duration_ms",
    "updated_at_ms",
    "version",
)


async def _run_control(action_coro) -> None:
    """Run a music_control() call, turning a device-reported failure into a
    clear HomeAssistantError instead of the button silently doing nothing.

    The error-code-to-message contract itself lives in ``music.py`` so the
    CODE27 music runtime
    reports the same device failure with the same wording.
    """
    try:
        await action_coro
    except Code27DeviceError as err:
        raise HomeAssistantError(
            control_error_message(err.error_code, str(err))
        ) from err


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
        | MediaPlayerEntityFeature.SEEK
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.BROWSE_MEDIA
    )

    #: Position bookkeeping, declared as class defaults (not only in
    #: ``__init__``) so the position properties can never raise on a partially
    #: constructed entity. ``_playback_sample`` identifies the device reading
    #: ``media_position`` was last stamped from, ``_position_updated_at`` is its
    #: Home-Assistant-facing stamp, and ``_pending_seek`` holds the
    #: ``(progress_ms, sample, stamp)`` of a seek this entity issued and the
    #: device has not confirmed yet.
    _playback_sample: tuple[Any, ...] | None = None
    _position_updated_at: datetime | None = None
    _pending_seek: tuple[int, tuple[Any, ...], datetime] | None = None

    def __init__(self, coordinator: Code27Coordinator) -> None:
        super().__init__(coordinator)
        machine_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{machine_id}_music"

    @property
    def _playback(self) -> dict[str, Any]:
        """The device's current ``music_playback`` snapshot.

        Empty before the first successful poll - and when there is no data at
        all - so the position properties below degrade to "unknown" instead of
        raising.
        """
        data = self.coordinator.data
        playback = getattr(data, "music_playback", None) if data is not None else None
        return playback if isinstance(playback, dict) else {}

    @staticmethod
    def _playback_sample_of(playback: dict[str, Any]) -> tuple[Any, ...]:
        """Identity of one device position reading (see
        ``_POSITION_SAMPLE_FIELDS``)."""
        return tuple(playback.get(field) for field in _POSITION_SAMPLE_FIELDS)

    def _position_reading(self) -> tuple[int, datetime] | None:
        """``(position_ms, when that value was valid)``, or ``None``.

        Two sources, in that order:

        1. a seek this entity issued whose confirmation has not arrived yet -
           reported with the stamp of the seek, so the card does not snap back
           to the last polled position while the device is still executing it.
           It is dropped as soon as the device publishes a different reading,
           so it can never outlive one round trip;
        2. the device's own ``position_ms``, stamped with the moment this exact
           sample was first seen.
        """
        playback = self._playback
        position_ms = playback.get("position_ms")
        if position_ms is None:
            self._pending_seek = None
            return None

        sample = self._playback_sample_of(playback)
        pending = self._pending_seek
        if pending is not None:
            progress_ms, sample_at_seek, stamped_at = pending
            if sample == sample_at_seek:
                return progress_ms, stamped_at
            self._pending_seek = None

        if sample != self._playback_sample or self._position_updated_at is None:
            self._playback_sample = sample
            self._position_updated_at = datetime.now(timezone.utc)
        return position_ms, self._position_updated_at

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
        reading = self._position_reading()
        return None if reading is None else int(reading[0] / 1000)

    @property
    def media_position_updated_at(self) -> datetime | None:
        """When ``media_position`` was valid - Home Assistant's interpolation base.

        Home Assistant extrapolates the progress it displays from this pair
        (``media_position + now - media_position_updated_at``) while the entity
        is PLAYING. PAUSED ignores it and shows ``media_position`` as it is,
        which is why the position only ever looked correct while paused: with no
        stamp at all the card can never move past the value of the last poll.

        The stamp deliberately follows the *device's* readings and not the
        coordinator's update cycle. It moves only when ``music_playback`` itself
        changed (see ``_POSITION_SAMPLE_FIELDS``), so a poll cycle that
        re-delivers the identical snapshot leaves it untouched - restarting the
        interpolation against a stale position is exactly the harm this avoids -
        while a genuinely new reading (a new track, a new status, an advanced
        ``position_ms``, or a new device revision marker) always re-bases it.

        The value is a timezone-aware UTC ``datetime`` - the instant
        ``homeassistant.util.dt.utcnow()`` names, taken from the standard
        library so this module still loads in the Home-Assistant-free test
        harness.
        """
        reading = self._position_reading()
        return None if reading is None else reading[1]

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

    async def async_media_seek(self, position: float) -> None:
        """Seek within the current track - ``position`` is in seconds.

        CODE27's own client seeks with exactly
        ``{action:"SEEK", track_id, progress_ms}`` on
        ``POST /data/user/music/control``: the device's local-TF panel wires its
        progress slider to ``music_control`` as
        ``{action:"SEEK", track_id: <current track or omitted>, progress_ms:
        max(floor(seconds * 1000), 0)}`` and sends no other field for this
        action. Those two fields are mirrored here - nothing is added.
        """
        progress_ms = max(int(position * 1000), 0)
        extra: dict[str, Any] = {"progress_ms": progress_ms}
        track_id = self._playback.get("track_id")
        if track_id:
            # The device's client omits the field entirely when it has no track
            # to name, so an absent track id is left out rather than sent as
            # null.
            extra["track_id"] = track_id
        runtime = async_get_music_runtime(getattr(self, "hass", None))
        entry_id = self.coordinator.entry.entry_id if runtime is not None else None
        if runtime is not None and runtime.queue(entry_id).playing:
            await runtime.async_dispatch("transport", {
                "target": entry_id, "action": "seek", "position": position,
            })
        else:
            await _run_control(self.coordinator.client.music_control("SEEK", **extra))
        # Report the requested position immediately and with a fresh stamp: the
        # device confirms the seek in its own time, and until it does the card
        # would snap back to the last polled position. The override is dropped
        # by `_position_reading` as soon as the device reports a different
        # reading.
        self._pending_seek = (
            progress_ms,
            self._playback_sample_of(self._playback),
            datetime.now(timezone.utc),
        )
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
        root_path = storage.get("root_path")

        if not media_content_id or media_content_id == "root":
            folder = None
            node_id = "root"
            title = "CODE27 Music"
        elif media_content_id.startswith("folder:"):
            folder = media_content_id[len("folder:"):]
            node_id = media_content_id
            title = folder
        else:
            raise HomeAssistantError(f"Unknown media_content_id: {media_content_id}")

        children = [
            _browse_item(entry)
            for entry in build_browse_entries(
                music_files, root=root_path, folder=folder
            )
        ]
        return BrowseMedia(
            title=title,
            media_class=MediaClass.DIRECTORY,
            media_content_id=node_id,
            media_content_type=MediaType.MUSIC,
            can_play=False,
            can_expand=True,
            children=children,
        )


# -- media browser tree building -------------------------------------------
# The tree is built by `music.py`, which both this entity and the CODE27 music
# runtime use, so
# the two can never disagree about the library's shape. The grouping and path
# normalisation that used to live here assumed *relative* `music_files[].path`
# values; the live payload is absolute
# (`/storage/<vol>/code27/music/<folder>/<file>.mp3`, see
# `music_storage.root_path`), which collapsed the whole library into one
# empty-named folder. That is resolved, not merely flagged: the audit records
# it as CCF-2 / backlog P0-2, and `tests/test_bridge_delivery.py` now pins the
# absolute-path shape with a fixture taken from the captured payload.


def _group_music_files(
    music_files: list[dict], root_path: str | None = None
) -> tuple[list[str], list[dict]]:
    """(sorted subfolder names, root-level files)."""
    return group_music_files(music_files, root_path)


def _files_in_folder(
    music_files: list[dict], folder: str, root_path: str | None = None
) -> list[dict]:
    return music_files_in_folder(music_files, folder, root_path)


def _track_browse_item(file: dict, root_path: str | None = None) -> BrowseMedia:
    """One playable child.

    ``media_content_id`` stays ``local:<path>`` with the path exactly as the
    device reported it - that string is the ``track_id`` the device expects
    back, so it must never be rewritten to a root-relative form.
    """
    track = track_from_music_file(file, root_path)
    return BrowseMedia(
        title=track.title,
        media_class=MediaClass.MUSIC,
        media_content_id=track.uri,
        media_content_type=MediaType.MUSIC,
        can_play=True,
        can_expand=False,
    )


def _browse_item(entry) -> BrowseMedia:
    """Generic browser entry (folder or track) -> Home Assistant BrowseMedia."""
    if entry.can_expand:
        return BrowseMedia(
            title=entry.title,
            media_class=MediaClass.DIRECTORY,
            media_content_id=entry.id,
            media_content_type=MediaType.MUSIC,
            can_play=False,
            can_expand=True,
        )
    return BrowseMedia(
        title=entry.title,
        media_class=MediaClass.MUSIC,
        media_content_id=entry.id,
        media_content_type=MediaType.MUSIC,
        can_play=entry.can_play,
        can_expand=False,
    )
