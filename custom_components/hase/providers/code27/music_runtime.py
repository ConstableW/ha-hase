"""CODE27 music runtime: active queue, persistent playlists, autoadvance.

This is the layer between CODE27's own device/music support and whichever
frontend consumes it - the HASEAMP card today, the native media player entity
already in parallel:

    CODE27 provider (coordinator + Code27Client + media_player entity)
        -> this runtime (queue / playlists / playback lifecycle)
        -> hase/code27/music WebSocket contract
        -> custom_components/hase/cards/haseamp-card.js

It reuses the existing CODE27 music infrastructure rather than replacing it:
the playback snapshot comes from ``Code27Data.music_playback`` /
``music_source`` / ``music_storage`` (the same fields the media player entity
projects), and every device write goes through the same
``Code27Client.music_control`` calls ``media_player.py`` makes. What it adds is
exactly what CODE27 lacks for this UI: a HASE-side active queue, persistent
local playlists, playlist CRUD and queue autoadvance.

Provider-owned on purpose: no generic provider framework, no shared music
contract, no second music architecture - this module knows it is CODE27. It has
**no module-level Home Assistant import** so the queue/playlist/lifecycle logic
is testable without a Home Assistant installation; ``Store`` is imported lazily
exactly like ``studio_access.py``.

Scope of persistence: the store key stays ``"hase.haseamp"`` - it is the
already-written Home Assistant ``Store`` namespace holding this feature's data
per CODE27 config entry, so renaming it would silently drop every stored queue
and playlist. It holds the active queue and the stored playlists. Playlists are
the persistent artifact; the queue is persisted too so a Home Assistant restart
does not lose it. Queue mutations only ever copy rows into playlists - they can
never rewrite a stored playlist.
"""
from __future__ import annotations

import asyncio
import base64
import mimetypes
import re
import logging
from collections.abc import Callable
from typing import Any

from ...const import DOMAIN
from .const import DATA_CODE27
from .music import (
    CODE27_MUSIC_FEATURES,
    PROVIDER_ID,
    SOUND_EFFECTS,
    TRACK_END_CONTRACT,
    TRACK_END_TOLERANCE_SECONDS,
    build_browse_entries,
    control_error_message,
    library_is_readable,
    music_library_payload,
    playback_payload,
    playback_track,
    relative_music_path,
    seconds,
    track_from_music_file,
)
from .music_model import (
    REPEAT_MODES,
    ActiveQueue,
    MusicError,
    Playlist,
    PlaylistLibrary,
    Track,
    tracks_match,
)

_LOGGER = logging.getLogger(__name__)

#: The music runtime is provider-owned, so its shared state lives in the CODE27
#: provider's own ``hass.data`` bucket (``DATA_CODE27``) - the same placement the
#: MCP provider already uses for its shared infrastructure. Nothing about the
#: queue, the playlists or autoadvance is added to ``hase/core``, to a HASE-wide
#: music platform or to any generic HASE state.
#:
#: ``STORAGE_KEY`` keeps its original spelling on purpose: it is the already
#: written Home Assistant ``Store`` namespace holding this feature's data, and
#: renaming it would silently drop every stored queue and playlist.
STORAGE_KEY = "hase.haseamp"
STORAGE_VERSION = 1

#: Key of the shared music runtime inside the CODE27 provider bucket.
DATA_MUSIC = "music"

#: Safety bounds for "add this folder to the queue": a pathological tree must
#: never turn one card click into an unbounded walk of the TF card.
MAX_FOLDER_DEPTH = 8
MAX_FOLDER_TRACKS = 1000
#: Largest single file the card may import (held in memory while chunks arrive).
MAX_IMPORT_BYTES = 64 * 1024 * 1024
#: One "delete folder" click never removes more than this many files.
MAX_DELETE_FILES = 500
#: One folder scan never creates more playlists than this.
MAX_SCAN_PLAYLISTS = 200

#: Transport actions. ``seek`` and ``restart`` are the two position controls:
#: ``seek`` moves the position inside the current row through the device's own
#: ``SEEK`` action (``progress_ms``), ``restart`` plays the current queue row
#: again from the beginning (``PLAY`` with its ``track_id``).
TRANSPORT_ACTIONS: tuple[str, ...] = (
    "play",
    "pause",
    "stop",
    "next",
    "previous",
    "restart",
    "seek",
)

#: The complete frontend contract. The WebSocket layer, the backend dispatcher
#: and the frontend/backend contract test all read this one set, so the card
#: can never call an operation the backend does not implement (every name here
#: must have a matching ``_op_<name>`` method).
OPERATIONS: frozenset[str] = frozenset(
    {
        "snapshot",
        "browse",
        "queue_add",
        "queue_play",
        "queue_remove",
        "queue_move",
        "queue_clear",
        "transport",
        "set_volume",
        "set_sound_effect",
        "set_shuffle",
        "set_repeat",
        "set_consume_played",
        "enqueue_folder",
        "import_chunk",
        "inventory",
        "delete_file",
        "delete_folder",
        "playlist_get",
        "playlist_update",
        "playlists_from_folders",
        "playlist_create",
        "playlist_load",
        "playlist_save",
        "playlist_save_as",
        "playlist_rename",
        "playlist_delete",
    }
)


_DISC_FOLDER = re.compile(r"^(cd|disc|disk|teil|part)[\s_.-]*\d+\b", re.IGNORECASE)


def _playlist_name_for(folder: str) -> str:
    """A playlist name from a card folder, within the name length limit."""
    from .music_model import MAX_PLAYLIST_NAME_LENGTH

    name = folder.replace("/", " / ")
    if len(name) > MAX_PLAYLIST_NAME_LENGTH:
        # Prefer the folder's own name over a truncated path.
        own = folder.rpartition("/")[2]
        name = own if own and len(own) <= MAX_PLAYLIST_NAME_LENGTH else name
    if len(name) > MAX_PLAYLIST_NAME_LENGTH:
        name = name[: MAX_PLAYLIST_NAME_LENGTH - 1].rstrip() + "…"
    return name


def _clean_resume(raw: Any) -> dict[str, dict[str, Any]]:
    """Keep only well-formed resume records from stored state."""
    clean: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return clean
    for playlist_id, rec in raw.items():
        if (
            isinstance(playlist_id, str)
            and isinstance(rec, dict)
            and isinstance(rec.get("uri"), str)
            and isinstance(rec.get("position"), (int, float))
            and not isinstance(rec.get("position"), bool)
        ):
            clean[playlist_id] = {
                "uri": rec["uri"],
                "index": rec.get("index") if isinstance(rec.get("index"), int) else 0,
                "title": str(rec.get("title") or ""),
                "position": float(rec["position"]),
                "updated_at": rec.get("updated_at"),
            }
    return clean


#: A resume point is only saved when the position moved at least this far, so
#: normal playback does not rewrite the store every second.
RESUME_SAVE_EVERY_SECONDS = 10.0
#: Resuming starts this much earlier, so the sentence that was interrupted is
#: heard again from its start.
RESUME_REWIND_SECONDS = 3.0
#: A track whose remaining time is shorter than this counts as finished.
RESUME_END_TOLERANCE_SECONDS = 5.0


def _natural_key(text: str) -> list[Any]:
    """Sort "2 b" before "10 a": digits compare as numbers, the rest as text."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", text)
    ]


def _path_of(entry: Any) -> str:
    path = entry.get("path") if isinstance(entry, dict) else None
    return path if isinstance(path, str) else ""


def track_end_evidence(
    previous: Any, current: Any, expected_track: Track | None = None
) -> dict[str, Any]:
    """Decide whether ``previous -> current`` ``music_playback`` means "finished".

    The contract the music runtime relies on, and why it is a *note* rather than a
    guarantee (``music.TRACK_END_CONTRACT``):

    1. the device was genuinely ``PLAYING`` and is now ``IDLE`` - a pause
       reports ``PAUSED`` and therefore never counts as an end;
    2. the device is not now reporting a *different* ``track_id``, which would
       mean it advanced on its own and advancing again would skip a row;
    3. position/duration are recorded as supporting evidence, but a position
       that is not near the duration does **not** veto the advance: the device
       legitimately resets the reported position (or drops the track) when a
       track completes, so requiring ``position >= duration`` would silently
       disable autoadvance on exactly this device.

    ``reason`` is part of the contract - the card's diagnostics panel and the
    tests read it.
    """
    if previous is None:
        return {"detected": False, "reason": "no_previous_state"}
    if not isinstance(current, dict) or not current:
        return {"detected": False, "reason": "device_state_unknown"}

    previous_status = previous.get("status")
    status = current.get("status")
    if previous_status != "PLAYING":
        return {
            "detected": False,
            "reason": "was_not_playing",
            "previous_status": previous_status,
        }
    if status == "PAUSED":
        return {"detected": False, "reason": "paused_not_ended", "status": status}
    if status != "IDLE":
        return {"detected": False, "reason": f"status_{status}", "status": status}

    observed = playback_track(current)
    if observed is not None:
        if expected_track is None:
            return {"detected": False, "reason": "device_reported_untracked_track"}
        if not tracks_match(observed, expected_track):
            return {
                "detected": False,
                "reason": "device_reported_other_track",
                "observed_track": observed.to_dict(),
            }

    position = seconds(current.get("position_ms"))
    if position is None:
        position = seconds(previous.get("position_ms"))
    duration = seconds(current.get("duration_ms")) or seconds(
        previous.get("duration_ms")
    )
    at_end = None
    if duration and position is not None:
        at_end = position >= duration - TRACK_END_TOLERANCE_SECONDS
    return {
        "detected": True,
        "reason": "played_to_end",
        "position": position,
        "duration": duration,
        "position_at_end": at_end,
    }


class MusicRuntime:
    """Queue + playlist + playback-lifecycle state for every CODE27 device."""

    def __init__(self, hass: Any, *, store: Any | None = None) -> None:
        if store is None:
            from homeassistant.helpers.storage import Store

            store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.hass = hass
        self.store = store
        self._queues: dict[str, ActiveQueue] = {}
        self._libraries: dict[str, PlaylistLibrary] = {}
        self._loaded_playlist: dict[str, str] = {}
        # Where each stored playlist was last played: entry -> playlist_id ->
        # {uri, index, title, position, updated_at}. Persisted with the rest.
        self._resume: dict[str, dict[str, dict[str, Any]]] = {}
        self._last_playback: dict[str, dict[str, Any]] = {}
        self._last_evidence: dict[str, dict[str, Any]] = {}
        # The track uri the device last reported *for the queue's current row*.
        # The runtime only corrects a device that has confirmed the row it was asked
        # to play, so a device whose reported track id never matches is never
        # fought over. Cleared whenever HASE starts a track itself.
        self._device_confirmed: dict[str, str | None] = {}
        self._advancing: set[str] = set()
        # In-flight guard for the device-correction path (M1): prevents two
        # concurrent observations from both calling _async_take_over / PLAY.
        self._correcting: set[str] = set()
        self._target_locks: dict[str, asyncio.Lock] = {}
        self._observation_generation: dict[str, int] = {}
        self._listeners: set[Callable[[str], None]] = set()

    # -- persistence -------------------------------------------------------

    async def async_load(self) -> None:
        payload = await self.store.async_load()
        if not isinstance(payload, dict):
            return
        targets = payload.get("targets")
        if not isinstance(targets, dict):
            return
        for entry_id, state in targets.items():
            if not isinstance(state, dict):
                continue
            # M9: quarantine a corrupt per-device entry so one bad slot never
            # prevents the others from loading.
            try:
                self._queues[entry_id] = ActiveQueue.from_dict(state.get("queue"))
                self._libraries[entry_id] = PlaylistLibrary.from_dict(
                    state.get("playlists")
                )
                loaded = state.get("loaded_playlist")
                if isinstance(loaded, str):
                    self._loaded_playlist[entry_id] = loaded
                self._resume[entry_id] = _clean_resume(state.get("resume"))
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "HASEAMP: corrupt stored state for entry %s — resetting to defaults",
                    entry_id,
                )
                self._queues.pop(entry_id, None)
                self._libraries.pop(entry_id, None)
                self._loaded_playlist.pop(entry_id, None)
                self._resume.pop(entry_id, None)

    def storage_payload(self) -> dict[str, Any]:
        entry_ids = (
            set(self._queues) | set(self._libraries) | set(self._loaded_playlist)
        )
        return {
            "version": STORAGE_VERSION,
            "targets": {
                entry_id: {
                    "queue": self._queues.get(entry_id, ActiveQueue()).to_dict(),
                    "playlists": self._libraries.get(
                        entry_id, PlaylistLibrary()
                    ).to_dict(),
                    "loaded_playlist": self._loaded_playlist.get(entry_id),
                    "resume": self._resume.get(entry_id, {}),
                }
                for entry_id in sorted(entry_ids)
            },
        }

    async def async_save(self) -> None:
        await self.store.async_save(self.storage_payload())

    # -- change notification ----------------------------------------------

    def async_add_listener(self, callback: Callable[[str], None]) -> Callable[[], None]:
        """Subscribe to state changes (the WebSocket subscription uses this)."""
        self._listeners.add(callback)

        def _remove() -> None:
            self._listeners.discard(callback)

        return _remove

    def _notify(self, entry_id: str) -> None:
        for callback in tuple(self._listeners):
            try:
                callback(entry_id)
            except Exception:
                _LOGGER.exception("CODE27 music listener failed")

    # -- CODE27 target resolution -----------------------------------------

    def _device_bucket(self) -> dict[str, Any]:
        bucket = getattr(self.hass, "data", {}).get(DOMAIN, {})
        return bucket if isinstance(bucket, dict) else {}

    @staticmethod
    def _is_device_coordinator(value: Any) -> bool:
        """Exclude HASE external bindings, which reuse this bucket but own no music."""
        return (
            value is not None
            and getattr(value, "target_entry_id", None) is None
            and getattr(value, "client", None) is not None
            and getattr(value, "data", None) is not None
        )

    def _coordinator(self, entry_id: str) -> Any:
        coordinator = self._device_bucket().get(entry_id)
        if not self._is_device_coordinator(coordinator):
            raise MusicError(
                f"No loaded CODE27 device for target {entry_id!r} - it may have "
                "been removed or the integration entry is not set up."
            )
        return coordinator

    def _entry(self, entry_id: str) -> Any:
        entries = getattr(
            getattr(self.hass, "config_entries", None), "async_entries", None
        )
        for entry in entries(DOMAIN) if callable(entries) else []:
            if getattr(entry, "entry_id", None) == entry_id:
                return entry
        return None

    def targets(self) -> list[dict[str, Any]]:
        """Every loaded CODE27 device the music runtime can drive.

        The HASE bucket also contains external bindings. They reference a
        standalone runtime and must never appear as HASE-owned music targets.
        """
        entries = getattr(
            getattr(self.hass, "config_entries", None), "async_entries", None
        )
        bucket = self._device_bucket()
        found: list[dict[str, Any]] = []
        for entry in entries(DOMAIN) if callable(entries) else []:
            if not self._is_device_coordinator(bucket.get(entry.entry_id)):
                continue
            found.append(
                {
                    "id": entry.entry_id,
                    "name": getattr(entry, "title", None) or entry.entry_id,
                    "provider": PROVIDER_ID,
                }
            )
        return found

    def default_target(self) -> str | None:
        targets = self.targets()
        return targets[0]["id"] if targets else None

    def queue(self, entry_id: str) -> ActiveQueue:
        queue = self._queues.get(entry_id)
        if queue is None:
            queue = ActiveQueue()
            self._queues[entry_id] = queue
        return queue

    def library(self, entry_id: str) -> PlaylistLibrary:
        library = self._libraries.get(entry_id)
        if library is None:
            library = PlaylistLibrary()
            self._libraries[entry_id] = library
        return library

    # -- payload building --------------------------------------------------

    @staticmethod
    def _empty_queue() -> dict[str, Any]:
        return {
            "items": [],
            "count": 0,
            "index": None,
            "current_item_id": None,
            "current_track": None,
            "playing": False,
            "shuffle": False,
            "repeat": "off",
            "loaded_playlist": None,
        }

    def queue_payload(self, entry_id: str) -> dict[str, Any]:
        queue = self.queue(entry_id)
        played = queue.played_item_ids()
        return {
            "items": [
                entry.to_dict(
                    index=position,
                    current=entry.item_id == queue.current_id,
                    played=entry.item_id in played,
                )
                for position, entry in enumerate(queue.items)
            ],
            "count": queue.count,
            "index": queue.index,
            "current_item_id": queue.current_id,
            "current_track": (
                queue.current_track.to_dict() if queue.current_track else None
            ),
            "playing": queue.playing,
            "shuffle": queue.shuffle,
            "repeat": queue.repeat,
            "consume_played": queue.consume_played,
            "loaded_playlist": self._loaded_playlist.get(entry_id),
        }

    def _snapshot_of(self, coordinator: Any) -> tuple[Any, Any, Any]:
        data = getattr(coordinator, "data", None) if coordinator is not None else None
        playback = getattr(data, "music_playback", None)
        source = getattr(data, "music_source", None)
        storage = getattr(data, "music_storage", None)
        return (
            playback if isinstance(playback, dict) else {},
            source if isinstance(source, dict) else {},
            storage if isinstance(storage, dict) else {},
        )

    def _tree_payload(self, coordinator: Any, node: str | None) -> dict[str, Any]:
        _playback, _source, storage = self._snapshot_of(coordinator)
        files = storage.get("music_files") or []
        folder = None
        if node and node not in {"root", ""}:
            if not node.startswith("folder:"):
                raise MusicError(f"Unknown music browser node {node!r}.")
            folder = node[len("folder:") :]
        root_path = storage.get("root_path")
        entries = build_browse_entries(
            files if isinstance(files, list) else [],
            root=root_path if isinstance(root_path, str) else None,
            folder=folder,
        )
        return {
            "node": node,
            "folder": folder,
            "entries": [entry.to_dict() for entry in entries],
            # An empty browser means "this folder/inventory has nothing", not
            # "the card is broken" - `library_ready` distinguishes an inventory
            # that has not arrived yet from one that is genuinely empty.
            "library_ready": library_is_readable(storage),
            "root_path": root_path if isinstance(root_path, str) else None,
        }

    def _playback_payload(self, entry_id: str, coordinator: Any) -> dict[str, Any]:
        """The card's playback block, with the progress-bar duration filled in.

        The device does not always report ``duration_ms`` for the track it is
        playing, which left the progress bar stuck at zero while the elapsed
        time kept counting. The durations HASE already holds - the queue row, or
        the TF-card inventory for the playing file - fill that in.
        ``duration_source`` says where the number came from, so the card can
        stay honest about a genuinely unknown length.
        """
        playback, source, storage = self._snapshot_of(coordinator)
        payload = playback_payload(
            storage, playback, source, self._last_evidence.get(entry_id)
        )
        if payload.get("duration") is not None:
            payload["duration_source"] = "device"
            return payload

        queue = self.queue(entry_id)
        current = queue.current_track
        playing = playback_track(playback)
        wanted = playing if playing is not None else current
        if wanted is None:
            return payload

        if current is not None and tracks_match(current, wanted) and current.duration:
            payload["duration"] = current.duration
            payload["duration_source"] = "queue_row"
            payload["duration_track"] = current.uri
            return payload

        root_path = storage.get("root_path")
        root = root_path if isinstance(root_path, str) else None
        for entry in storage.get("music_files") or []:
            try:
                candidate = track_from_music_file(entry, root)
            except MusicError:
                continue
            if tracks_match(candidate, wanted) and candidate.duration:
                payload["duration"] = candidate.duration
                payload["duration_source"] = "music_inventory"
                payload["duration_track"] = candidate.uri
                break
        return payload

    async def async_snapshot(
        self,
        entry_id: str | None = None,
        *,
        node: str | None = None,
        include_tree: bool = True,
    ) -> dict[str, Any]:
        """The card's complete view for one CODE27 device.

        The ``tree`` key is **omitted entirely** when ``include_tree=False``
        (the subscription pushes): absent means "unchanged, keep what you
        render", while a present-but-empty tree means "this folder really has
        nothing". That distinction is the contract the music browser depends on
        - a push without a listing must never be read as an empty library.

        ``library`` always describes the inventory itself (``revision``,
        ``ready``, ``file_count``, ``root_path``), so the card can refetch the
        listing exactly when the device reports a different one instead of
        polling blindly.
        """
        targets = self.targets()
        payload: dict[str, Any] = {
            "provider": PROVIDER_ID,
            "features": CODE27_MUSIC_FEATURES,
            "operations": sorted(OPERATIONS),
            "transport_actions": list(TRANSPORT_ACTIONS),
            "repeat_modes": list(REPEAT_MODES),
            "track_end": TRACK_END_CONTRACT,
            "targets": targets,
            "target": None,
            "available": False,
            "reason": None,
            "playback": playback_payload({}, {}, {}),
            "queue": self._empty_queue(),
            "playlists": [],
            "library": music_library_payload({}),
            "music_mode": {"known": False, "active": False},
            "diagnostics": {},
        }

        if entry_id is None:
            entry_id = targets[0]["id"] if targets else None
        if entry_id is None:
            payload["reason"] = "no_code27_device"
            if include_tree:
                payload["tree"] = None
            return payload

        entry = self._entry(entry_id)
        coordinator = self._device_bucket().get(entry_id)
        if not self._is_device_coordinator(coordinator):
            coordinator = None
        payload["target"] = {
            "id": entry_id,
            "name": getattr(entry, "title", None) or entry_id,
        }

        if coordinator is None:
            # The device is not loaded right now, but the queue and the stored
            # playlists are HASE-side: they stay visible and editable instead of
            # turning the whole card into an error.
            payload["reason"] = "device_unavailable"
            payload["queue"] = self.queue_payload(entry_id)
            payload["playlists"] = self._summaries(entry_id)
            if include_tree:
                payload["tree"] = None
            return payload

        _playback, _source, storage = self._snapshot_of(coordinator)
        payload["available"] = True
        payload["playback"] = self._playback_payload(entry_id, coordinator)
        payload["queue"] = self.queue_payload(entry_id)
        payload["playlists"] = self._summaries(entry_id)
        payload["library"] = music_library_payload(storage)
        # Music Mode = the music page is open on the device. Without it the
        # device refuses transport commands (MODE_INACTIVE), so the card says so.
        page = getattr(getattr(coordinator, "data", None), "music_page", None)
        payload["music_mode"] = {
            "known": isinstance(page, dict) and "is_open" in page,
            "active": bool(page.get("is_open")) if isinstance(page, dict) else False,
        }
        if include_tree:
            payload["tree"] = self._tree_payload(coordinator, node)
        reported = _playback if isinstance(_playback, dict) else {}
        payload["diagnostics"] = {
            "last_track_end": self._last_evidence.get(entry_id),
            "last_playback_status": reported.get("status"),
            # Which fields the device actually reports: the only way to tell a
            # missing duration/position from a card bug without the device.
            "playback_fields": sorted(reported.keys()),
            "position_reported": reported.get("position_ms") is not None,
            "duration_reported": reported.get("duration_ms") is not None,
            "device_track_id": reported.get("track_id"),
            "device_play_mode": reported.get("play_mode"),
        }
        return payload

    # -- device writes -----------------------------------------------------

    async def _control(self, coordinator: Any, action: str, **extra: Any) -> None:
        """One ``music_control`` call, with the device's own error contract.

        Deliberately the same call ``media_player.py`` makes - the runtime adds no
        second way to reach the device.
        """
        try:
            await coordinator.client.music_control(action, **extra)
        except Exception as err:
            error_code = getattr(err, "error_code", None)
            if not isinstance(error_code, str):
                raise
            # e.g. MODE_INACTIVE (music screen closed) / INVALID_ARGUMENT
            raise MusicError(
                control_error_message(error_code, str(err))
            ) from err

    def _pending_uploads(self, entry_id: str, storage: Any) -> dict[str, dict[str, Any]]:
        """Files HASE uploaded that the device's own list does not show (yet).

        The device re-indexes its card lazily (observed: not while the music
        screen is closed), so a file uploaded a moment ago can be missing from
        ``music_files``. Without this record the next M3U import would upload
        it again. Entries are dropped as soon as the device lists the file, when
        the file is deleted, or when the card (its ``root_path``) changed.
        """
        pending = self.__dict__.setdefault("_pending", {}).setdefault(entry_id, {})
        root = storage.get("root_path") if isinstance(storage, dict) else None
        listed = {
            _path_of(entry)
            for entry in (storage.get("music_files") or [])
            if isinstance(storage, dict) and isinstance(entry, dict)
        }
        for uri in list(pending):
            record = pending[uri]
            if record.get("root") != root or uri.removeprefix("local:") in listed:
                del pending[uri]
        return pending

    async def _rescan_inventory(self, coordinator: Any) -> None:
        """Ask the device to re-scan its card and adopt the answer.

        After an upload or a delete the routine poll still returns the old
        list for a while, which would make the next M3U import re-upload what
        is already there. A failing re-scan is never fatal: the poll catches up.
        """
        reader = getattr(getattr(coordinator, "client", None), "get_music_storage", None)
        data = getattr(coordinator, "data", None)
        if not callable(reader) or data is None:
            return
        try:
            fresh = await reader(refresh=True)
        except Exception:  # noqa: BLE001 - best effort
            return
        if isinstance(fresh, dict) and isinstance(fresh.get("music_files"), list):
            data.music_storage = fresh
            update = getattr(coordinator, "async_update_listeners", None)
            if callable(update):
                update()

    async def _async_play_track(self, entry_id: str, track: Track) -> None:
        coordinator = self._coordinator(entry_id)
        # A fresh command invalidates the previous confirmation: the device has
        # to report this row back before the runtime corrects anything again.
        self._device_confirmed.pop(entry_id, None)
        queue = self.queue(entry_id)
        queue.set_playing(False)
        self._last_playback.pop(entry_id, None)
        self._observation_generation[entry_id] = self._observation_generation.get(entry_id, 0) + 1
        try:
            await self._control(coordinator, "PLAY", track_id=track.uri)
        except (Exception, asyncio.CancelledError):
            # An ambiguous failed write must never leave persisted playback
            # ownership active. Keep the selected row for an explicit retry.
            await self.async_save()
            self._notify(entry_id)
            raise
        queue.set_playing(True)
        await self._request_refresh(coordinator)

    async def _request_refresh(self, coordinator: Any) -> None:
        refresh = getattr(coordinator, "async_request_refresh", None)
        if callable(refresh):
            await refresh()

    async def _async_play_current(self, entry_id: str) -> bool:
        queue = self.queue(entry_id)
        track = queue.current_track
        if track is None:
            queue.set_playing(False)
            return False
        await self._async_play_track(entry_id, track)
        queue.set_playing(True)
        return True

    # -- queue operations --------------------------------------------------

    def _resolve_tracks(self, payload: Any) -> list[Track]:
        if payload is None:
            raise MusicError("No tracks were given.")
        items = payload if isinstance(payload, (list, tuple)) else [payload]
        tracks = [Track.from_dict(raw, provider=PROVIDER_ID) for raw in items]
        if not tracks:
            raise MusicError("No tracks were given.")
        return tracks

    async def _op_snapshot(self, data: dict[str, Any]) -> dict[str, Any]:
        return await self.async_snapshot(
            self._target(data), node=self._node(data)
        )

    async def _op_browse(self, data: dict[str, Any]) -> dict[str, Any]:
        entry_id = self._target(data, required=True)
        return self._tree_payload(self._coordinator(entry_id), self._node(data))

    async def _op_queue_add(self, data: dict[str, Any]) -> dict[str, Any]:
        entry_id = self._target(data, required=True)
        queue = self.queue(entry_id)
        position = data.get("position")
        added = queue.add_many(
            self._resolve_tracks(data.get("tracks")),
            position=position if isinstance(position, int) else None,
        )
        if data.get("play") and added:
            queue.play_item(added[0].item_id)
        payload = self.queue_payload(entry_id)
        payload["added"] = len(added)
        if data.get("play") and added:
            payload["played"] = await self._async_play_current(entry_id)
        # Save after the device write so playing=True is only persisted when
        # the command actually reached the device (M7).
        await self.async_save()
        self._notify(entry_id)
        return payload

    async def _op_queue_play(self, data: dict[str, Any]) -> dict[str, Any]:
        entry_id = self._target(data, required=True)
        queue = self.queue(entry_id)
        item_id = data.get("item_id")
        index = data.get("index")
        if isinstance(item_id, int) and not isinstance(item_id, bool):
            queue.play_item(item_id)
        elif isinstance(index, int) and not isinstance(index, bool):
            queue.play_index(index)
        elif queue.current_id is None:
            if not queue.items:
                raise MusicError("The queue is empty - add a track first.")
            queue.play_index(0)
        # else: no new position — _async_play_current will call set_playing(True)
        # only after the device write succeeds, so no premature state update (M7).
        played = await self._async_play_current(entry_id)
        await self.async_save()
        self._notify(entry_id)
        payload = self.queue_payload(entry_id)
        payload["played"] = played
        return payload

    async def _op_queue_remove(self, data: dict[str, Any]) -> dict[str, Any]:
        """Remove one row; use ``queue_clear`` to empty the whole queue."""
        entry_id = self._target(data, required=True)
        queue = self.queue(entry_id)
        item_id = data.get("item_id")
        index = data.get("index")
        if isinstance(item_id, int) and not isinstance(item_id, bool):
            if not queue.remove(item_id):
                raise MusicError(f"No queue item with id {item_id}.")
        elif isinstance(index, int) and not isinstance(index, bool):
            if not queue.remove_index(index):
                raise MusicError(f"No queue item at index {index}.")
        else:
            raise MusicError("queue_remove needs an index or an item_id.")
        await self.async_save()
        self._notify(entry_id)
        return self.queue_payload(entry_id)

    async def _op_queue_move(self, data: dict[str, Any]) -> dict[str, Any]:
        entry_id = self._target(data, required=True)
        queue = self.queue(entry_id)
        queue.move(data.get("from_index"), data.get("to_index"))
        await self.async_save()
        self._notify(entry_id)
        return self.queue_payload(entry_id)

    async def _op_queue_clear(self, data: dict[str, Any]) -> dict[str, Any]:
        entry_id = self._target(data, required=True)
        self.queue(entry_id).clear()
        await self.async_save()
        self._notify(entry_id)
        return self.queue_payload(entry_id)

    # -- transport ---------------------------------------------------------

    async def _op_transport(self, data: dict[str, Any]) -> dict[str, Any]:
        """play/pause/stop/next/previous/restart/seek, queue-aware.

        ``next``/``previous`` move the *queue* first and then play the
        resulting row, so HASE's queue stays the single source of truth instead
        of drifting away from the device. ``stop`` ends HASE's playback intent
        and asks the device to stop; CODE27 has no STOP action, so the adapter
        pauses and the response carries ``features.stop = false`` for the card
        to label honestly.

        ``seek`` is the device's own position control and, unlike the queue
        moves, never touches the queue: the row it seeks inside stays the row
        the device is playing.
        """
        entry_id = self._target(data, required=True)
        action = data.get("action")
        if action not in TRANSPORT_ACTIONS:
            raise MusicError(
                f"Unknown transport action {action!r} - expected one of "
                f"{', '.join(TRANSPORT_ACTIONS)}."
            )
        queue = self.queue(entry_id)
        coordinator = self._coordinator(entry_id)

        if action == "play":
            if queue.current_id is None:
                # Nothing selected yet: start the queue from its first row
                # rather than sending a bare PLAY, which the device would
                # reject with INVALID_ARGUMENT.
                if not queue.items:
                    raise MusicError("The queue is empty - add a track first.")
                queue.play_index(0)
                await self._async_play_track(entry_id, queue.current_track)
            else:
                # M2: before issuing a bare PLAY (resume), verify the device is
                # actually playing the track the queue expects. If the device
                # drifted (e.g. continued with the next file on its own while
                # the queue was paused), a bare PLAY resumes the *wrong* track.
                playback, _source, _storage = self._snapshot_of(coordinator)
                device_track = playback_track(playback)
                queue_track = queue.current_track
                if (
                    device_track is not None
                    and queue_track is not None
                    and not tracks_match(device_track, queue_track)
                ):
                    # Re-issue PLAY with explicit track so the device switches.
                    await self._async_play_track(entry_id, queue_track)
                else:
                    # Resuming must not re-issue the track, or playback restarts.
                    await self._control(coordinator, "PLAY")
                    queue.set_playing(True)
                    await self._request_refresh(coordinator)
        elif action == "pause":
            await self._control(coordinator, "PAUSE")
            queue.set_playing(False)
            await self._request_refresh(coordinator)
        elif action == "stop":
            # Emulated: no device STOP action exists.
            await self._control(coordinator, "PAUSE")
            queue.stop()
            await self._request_refresh(coordinator)
        elif action == "next":
            entry = queue.next_track()
            if entry is None:
                raise MusicError("There is no next item in the queue.")
            await self._async_play_track(entry_id, entry.track)
        elif action == "previous":
            entry = queue.previous_track()
            if entry is None:
                raise MusicError("There is no previous item in the queue.")
            await self._async_play_track(entry_id, entry.track)
        elif action == "restart":
            # Play the current queue row again from position 0 - the
            # non-destructive companion to ``seek`` when the goal is simply
            # "start over".
            track = queue.current_track
            if track is None:
                if not queue.items:
                    raise MusicError("The queue is empty - add a track first.")
                track = queue.play_index(0).track
            await self._async_play_track(entry_id, track)
        elif action == "seek":
            # The device's own SEEK action, reached through the same ``_control``
            # every other transport action uses - the media player entity issues
            # the identical ``music_control("SEEK", ...)``, so the provider has
            # exactly one seek path, not a second one next to it.
            position = data.get("position")
            if isinstance(position, bool) or not isinstance(position, (int, float)):
                raise MusicError("A seek needs a 'position' in seconds.")
            playback, _source, _storage = self._snapshot_of(coordinator)
            extra: dict[str, Any] = {
                # The card and the WS contract speak seconds, the device speaks
                # milliseconds - the same conversion the media player entity
                # applies to `media_player.media_seek`'s seconds.
                "progress_ms": max(int(float(position) * 1000), 0)
            }
            # M11: prefer the queue's authoritative track over the device
            # snapshot, which may still name the previous track during a
            # transition (HASE already issued PLAY for the next row but the
            # device has not yet updated its reported track_id).
            queue_track = queue.current_track
            if queue_track is not None:
                extra["track_id"] = queue_track.uri
            elif track_id := playback.get("track_id"):
                extra["track_id"] = track_id
            # Deliberately not ``_async_play_track``: a seek must not re-issue
            # PLAY (which would restart the track) and must not drop the
            # device's row confirmation, because the row does not change.
            self._last_playback.pop(entry_id, None)
            self._observation_generation[entry_id] = self._observation_generation.get(entry_id, 0) + 1
            await self._control(coordinator, "SEEK", **extra)
            await self._request_refresh(coordinator)

        await self.async_save()
        self._notify(entry_id)
        payload = self.queue_payload(entry_id)
        payload["action"] = action
        return payload

    async def _op_set_volume(self, data: dict[str, Any]) -> dict[str, Any]:
        entry_id = self._target(data, required=True)
        volume = data.get("volume")
        if isinstance(volume, bool) or not isinstance(volume, (int, float)):
            raise MusicError("Volume must be a number between 0 and 1.")
        if not 0.0 <= float(volume) <= 1.0:
            raise MusicError("Volume must be between 0 and 1.")
        coordinator = self._coordinator(entry_id)
        await self._control(
            coordinator, "SET_VOLUME", volume_level=round(float(volume) * 100)
        )
        await self._request_refresh(coordinator)
        self._notify(entry_id)
        return {"volume": float(volume)}

    async def _op_set_sound_effect(self, data: dict[str, Any]) -> dict[str, Any]:
        """Select one of the device's sound presets (classical / surround / vocal)."""
        entry_id = self._target(data, required=True)
        effect = data.get("effect")
        if effect not in SOUND_EFFECTS:
            raise MusicError(
                f"The sound effect must be one of {', '.join(SOUND_EFFECTS)}."
            )
        coordinator = self._coordinator(entry_id)
        await self._control(coordinator, "SET_SOUND_EFFECT", sound_effect=effect)
        await self._request_refresh(coordinator)
        self._notify(entry_id)
        return {"sound_effect": effect}

    async def _op_set_shuffle(self, data: dict[str, Any]) -> dict[str, Any]:
        entry_id = self._target(data, required=True)
        self.queue(entry_id).set_shuffle(bool(data.get("shuffle")))
        await self.async_save()
        self._notify(entry_id)
        return self.queue_payload(entry_id)

    async def _op_set_repeat(self, data: dict[str, Any]) -> dict[str, Any]:
        entry_id = self._target(data, required=True)
        self.queue(entry_id).set_repeat(data.get("repeat"))
        await self.async_save()
        self._notify(entry_id)
        return self.queue_payload(entry_id)

    async def _op_set_consume_played(self, data: dict[str, Any]) -> dict[str, Any]:
        """Whether a played row leaves the queue (off by default).

        Off: rows stay and are only marked (Winamp behaviour, `previous`
        still works). On: the played rows are dropped as playback moves on,
        so the queue reads like an "up next" list.
        """
        entry_id = self._target(data, required=True)
        queue = self.queue(entry_id)
        queue.set_consume_played(bool(data.get("consume")))
        if queue.consume_played:
            # Apply it right away, not only on the next track change.
            queue.drop_played()
        await self.async_save()
        self._notify(entry_id)
        return self.queue_payload(entry_id)

    # -- browser -----------------------------------------------------------

    async def _op_enqueue_folder(self, data: dict[str, Any]) -> dict[str, Any]:
        """Add a whole folder to the queue by walking the device's tree."""
        entry_id = self._target(data, required=True)
        node = self._node(data)
        if not node or node in {"root", ""}:
            raise MusicError("Choose a folder in the music browser first.")
        coordinator = self._coordinator(entry_id)
        _playback, _source, storage = self._snapshot_of(coordinator)
        files = storage.get("music_files") or []
        root_path = storage.get("root_path")
        root = root_path if isinstance(root_path, str) else None

        collected: list[Track] = []
        pending: list[tuple[str, int]] = [(node, 0)]
        seen: set[str] = set()
        while pending:
            current, depth = pending.pop(0)
            if current in seen or depth > MAX_FOLDER_DEPTH:
                continue
            seen.add(current)
            folder = current.removeprefix("folder:")
            for entry in build_browse_entries(
                files if isinstance(files, list) else [], root=root, folder=folder
            ):
                if entry.kind == "track" and entry.track is not None:
                    collected.append(entry.track)
                elif entry.can_expand:
                    pending.append((entry.id, depth + 1))
                if len(collected) >= MAX_FOLDER_TRACKS:
                    break
            if len(collected) >= MAX_FOLDER_TRACKS:
                break
        if not collected:
            raise MusicError(f"The folder {node!r} contains no playable tracks.")

        queue = self.queue(entry_id)
        added = queue.add_many(collected)
        if data.get("play") and added:
            queue.play_item(added[0].item_id)
        payload = self.queue_payload(entry_id)
        payload["added"] = len(added)
        payload["folder"] = node
        if data.get("play") and added:
            payload["played"] = await self._async_play_current(entry_id)
        await self.async_save()
        self._notify(entry_id)
        return payload

    # -- playlists ---------------------------------------------------------

    async def _op_import_chunk(self, data: dict[str, Any]) -> dict[str, Any]:
        """Receive one base64 chunk of a file the user picked in the card.

        A WebSocket message is size-limited, so the card sends a file in
        pieces. The pieces are collected here (bounded) and the finished file
        is uploaded to the TF card with the same call ``hase.upload_music``
        makes. The last chunk answers with the uploaded tracks as queue rows.
        """
        entry_id = self._target(data, required=True)
        coordinator = self._coordinator(entry_id)
        name = data.get("name")
        upload_id = data.get("upload_id")
        index, total = data.get("index"), data.get("total")
        if not (isinstance(name, str) and name.strip() and isinstance(upload_id, str)):
            raise MusicError("An import chunk needs 'name' and 'upload_id'.")
        if not (isinstance(index, int) and isinstance(total, int) and 0 <= index < total):
            raise MusicError("An import chunk needs a valid 'index' and 'total'.")
        name = name.replace("\\", "/").rsplit("/", 1)[-1]
        content_type = mimetypes.guess_type(name)[0] or ""
        if not content_type.startswith("audio/"):
            raise MusicError(f"'{name}' is not a recognized audio file.")
        try:
            chunk = base64.b64decode(data.get("data") or "", validate=True)
        except (ValueError, TypeError) as err:
            raise MusicError("The import chunk is not valid base64.") from err

        uploads = self.__dict__.setdefault("_imports", {})
        key = (entry_id, upload_id)
        if index == 0:
            uploads[key] = bytearray()
        buffer = uploads.get(key)
        if buffer is None:
            raise MusicError("Import chunks arrived out of order - start the import again.")
        buffer.extend(chunk)
        if len(buffer) > MAX_IMPORT_BYTES:
            uploads.pop(key, None)
            raise MusicError(
                f"'{name}' is larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MB."
            )
        if index < total - 1:
            return {"received": index + 1, "total": total, "done": False}

        content = bytes(uploads.pop(key))
        try:
            result = await coordinator.client.upload_music([(content, name, content_type)])
        except Exception as err:
            raise MusicError(f"Upload to the device failed: {err}") from err
        rejected = result.get("rejected") or []
        if rejected:
            raise MusicError(f"The device rejected '{name}': {rejected}")
        storage_now = self._snapshot_of(coordinator)[2]
        root_path = storage_now.get("root_path")
        tracks = []
        for item in result.get("uploaded") or []:
            try:
                track = track_from_music_file(
                    item, root_path if isinstance(root_path, str) else None
                )
            except MusicError:
                continue
            row = {
                "uri": track.uri,
                "title": track.title,
                "provider": track.provider,
                "duration": track.duration,
            }
            tracks.append(row)
            size = item.get("size_bytes") if isinstance(item, dict) else None
            self._pending_uploads(entry_id, storage_now)[track.uri] = {
                "name": track.uri.removeprefix("local:").rsplit("/", 1)[-1],
                "size": size if isinstance(size, int) else len(content),
                "track": row,
                "root": root_path,
            }
        await self._rescan_inventory(coordinator)
        await self._request_refresh(coordinator)
        self._notify(entry_id)
        return {"done": True, "tracks": tracks, "total": total}

    async def _op_inventory(self, data: dict[str, Any]) -> dict[str, Any]:
        """Every file on the device's TF card as a queue-ready row.

        The card's playlist import compares an M3U against this list (file name
        and size) so it only uploads what the device does not hold yet.
        """
        entry_id = self._target(data, required=True)
        coordinator = self._coordinator(entry_id)
        storage = self._snapshot_of(coordinator)[2]
        root_path = storage.get("root_path")
        root = root_path if isinstance(root_path, str) else None
        files = []
        for entry in storage.get("music_files") or []:
            try:
                track = track_from_music_file(entry, root)
            except MusicError:
                continue
            path = track.uri.removeprefix("local:")
            size = entry.get("size_bytes") if isinstance(entry, dict) else None
            files.append(
                {
                    "name": path.rsplit("/", 1)[-1],
                    "relative": relative_music_path(path, root),
                    "size": size if isinstance(size, int) else None,
                    "track": {
                        "uri": track.uri,
                        "title": track.title,
                        "provider": track.provider,
                        "duration": track.duration,
                    },
                }
            )
        listed = {row["track"]["uri"] for row in files}
        for uri, record in self._pending_uploads(entry_id, storage).items():
            if uri not in listed:
                files.append(
                    {
                    "name": record["name"],
                    "relative": relative_music_path(uri.removeprefix("local:"), root),
                    "size": record["size"],
                    "track": record["track"],
                }
                )
        return {"files": files, "ready": library_is_readable(storage)}

    def _playing_track(self, coordinator: Any) -> Track | None:
        playback, _source, _storage = self._snapshot_of(coordinator)
        return playback_track(playback)

    def _guard_not_playing(self, coordinator: Any, track: Track) -> None:
        """Refuse to delete the file the device is playing *right now*.

        Only an actively playing file is protected: a paused or stopped one can
        be deleted (the device keeps its current-track id after a pause, so
        guarding on the id alone would make "pause, then delete" impossible).
        """
        playback = self._snapshot_of(coordinator)[0]
        if str(playback.get("status") or "").upper() != "PLAYING":
            return
        playing = self._playing_track(coordinator)
        if playing is not None and tracks_match(track, playing):
            raise MusicError(
                f"'{track.title}' is playing right now - pause it or play another "
                "track before deleting it."
            )

    async def _delete_tracks(self, entry_id: str, coordinator: Any, tracks: list[Track]) -> int:
        """Delete files from the TF card and drop their queue rows.

        Stored playlists are left alone on purpose: a playlist is the user's
        own list, and a missing file simply cannot be played.
        """
        for track in tracks:
            self._guard_not_playing(coordinator, track)
        queue = self.queue(entry_id)
        deleted = 0
        for track in tracks:
            path = track.uri.removeprefix("local:")
            try:
                await coordinator.client.delete_music_file(path)
            except Exception as err:
                raise MusicError(f"Deleting '{track.title}' failed: {err}") from err
            deleted += 1
            self.__dict__.setdefault("_pending", {}).get(entry_id, {}).pop(track.uri, None)
            for entry in [e for e in queue.items if e.track.uri == track.uri]:
                queue.remove(entry.item_id)
        await self._rescan_inventory(coordinator)
        await self.async_save()
        await self._request_refresh(coordinator)
        self._notify(entry_id)
        return deleted

    async def _op_delete_file(self, data: dict[str, Any]) -> dict[str, Any]:
        """Remove one file from the TF card (never the one that is playing)."""
        entry_id = self._target(data, required=True)
        coordinator = self._coordinator(entry_id)
        uri = data.get("uri")
        if not isinstance(uri, str) or not uri.startswith("local:"):
            raise MusicError("Choose a file from the device's music library.")
        storage = self._snapshot_of(coordinator)[2]
        root_path = storage.get("root_path")
        root = root_path if isinstance(root_path, str) else None
        for entry in storage.get("music_files") or []:
            try:
                track = track_from_music_file(entry, root)
            except MusicError:
                continue
            if track.uri == uri:
                deleted = await self._delete_tracks(entry_id, coordinator, [track])
                return {"deleted": deleted, "queue": self.queue_payload(entry_id)}
        record = self._pending_uploads(entry_id, storage).get(uri)
        if record is not None:
            track = Track.make(
                provider=record["track"]["provider"],
                uri=uri,
                title=record["track"]["title"],
                duration=record["track"]["duration"],
            )
            deleted = await self._delete_tracks(entry_id, coordinator, [track])
            return {"deleted": deleted, "queue": self.queue_payload(entry_id)}
        raise MusicError("That file is not on the device's TF card.")

    async def _op_delete_folder(self, data: dict[str, Any]) -> dict[str, Any]:
        """Remove every file below a folder of the music browser."""
        entry_id = self._target(data, required=True)
        coordinator = self._coordinator(entry_id)
        node = self._node(data)
        if not node or not node.startswith("folder:"):
            raise MusicError("Choose a folder in the music browser first.")
        folder = node.removeprefix("folder:").strip("/")
        storage = self._snapshot_of(coordinator)[2]
        root_path = storage.get("root_path")
        root = root_path if isinstance(root_path, str) else None
        tracks: list[Track] = []
        for entry in storage.get("music_files") or []:
            try:
                track = track_from_music_file(entry, root)
            except MusicError:
                continue
            relative = relative_music_path(track.uri.removeprefix("local:"), root)
            if relative.startswith(folder + "/"):
                tracks.append(track)
        if not tracks:
            raise MusicError(f"The folder {node!r} contains no files.")
        if len(tracks) > MAX_DELETE_FILES:
            raise MusicError(
                f"The folder holds {len(tracks)} files - more than the "
                f"{MAX_DELETE_FILES} one delete may remove."
            )
        deleted = await self._delete_tracks(entry_id, coordinator, tracks)
        return {"deleted": deleted, "queue": self.queue_payload(entry_id)}

    async def _op_playlist_get(self, data: dict[str, Any]) -> dict[str, Any]:
        """The tracks of one stored playlist, for the card's playlist editor."""
        entry_id = self._target(data, required=True)
        name = data.get("name")
        if not isinstance(name, str) or not name:
            raise MusicError("Choose a playlist.")
        playlist = self.library(entry_id).resolve(name)
        return {
            "name": playlist.name,
            "tracks": [
                track.to_dict()
                for track in self._remap_to_card(entry_id, playlist.tracks)
            ],
        }

    async def _op_playlist_update(self, data: dict[str, Any]) -> dict[str, Any]:
        """Re-order or thin out a stored playlist without touching the queue.

        ``order`` lists the *current* positions to keep, in their new order -
        so a row can move or disappear, and nothing can be invented.
        """
        entry_id = self._target(data, required=True)
        name = data.get("name")
        order = data.get("order")
        if not isinstance(name, str) or not name:
            raise MusicError("Choose a playlist.")
        if not isinstance(order, list) or not all(isinstance(i, int) for i in order):
            raise MusicError("'order' must be a list of track positions.")
        library = self.library(entry_id)
        playlist = library.resolve(name)
        if any(i < 0 or i >= playlist.count for i in order):
            raise MusicError("'order' refers to a track that does not exist.")
        updated = library.save_to(
            playlist.playlist_id, [playlist.tracks[i] for i in order]
        )
        await self.async_save()
        self._notify(entry_id)
        return {
            "name": updated.name,
            "tracks": [track.to_dict() for track in updated.tracks],
            "playlists": self._summaries(entry_id),
        }

    async def _op_playlists_from_folders(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a stored playlist for every card folder that directly holds music.

        A folder's name (below the music folder; nested folders read
        ``Parent / Child``) is the playlist name and its files, in natural file
        order, are the tracks. A playlist that already exists is left alone;
        one the user deleted is simply built again on the next scan. The queue
        is never touched. Files directly in the music folder belong to no
        folder and are skipped.
        """
        entry_id = self._target(data, required=True)
        coordinator = self._coordinator(entry_id)
        storage = self._snapshot_of(coordinator)[2]
        files = storage.get("music_files")
        if not isinstance(files, list) or not files:
            raise MusicError("The device has not reported its music inventory yet.")
        root_path = storage.get("root_path")
        root = root_path if isinstance(root_path, str) else None
        folders: dict[str, list[tuple[str, Track]]] = {}
        for entry in files:
            try:
                track = track_from_music_file(entry, root)
            except MusicError:
                continue
            relative = relative_music_path(track.uri.removeprefix("local:"), root)
            folder, _, name = relative.rpartition("/")
            if not folder:
                continue
            # "Book/CD 01", "Book/CD 02" ... are parts of one book: they join
            # the playlist of the folder above, in disc order.
            head, _, last = folder.rpartition("/")
            if head and _DISC_FOLDER.match(last):
                folder = head
            folders.setdefault(folder, []).append((relative, track))
        library = self.library(entry_id)
        created: list[str] = []
        skipped = 0
        for folder in sorted(folders, key=lambda f: _natural_key(f)):
            name = _playlist_name_for(folder)
            if library.find_by_name(name) is not None:
                skipped += 1
                continue
            if len(created) >= MAX_SCAN_PLAYLISTS:
                skipped += 1
                continue
            # Natural order over the whole path keeps "CD 2" before "CD 10" and
            # every disc's tracks together.
            rows = sorted(folders[folder], key=lambda row: _natural_key(row[0]))
            try:
                library.create(name, [track for _name, track in rows])
            except MusicError:
                skipped += 1
                continue
            created.append(name)
        if created:
            await self.async_save()
            self._notify(entry_id)
        return {
            "created": created,
            "skipped": skipped,
            "playlists": self._summaries(entry_id),
        }

    async def _op_playlist_create(self, data: dict[str, Any]) -> dict[str, Any]:
        entry_id = self._target(data, required=True)
        tracks = self.queue(entry_id).tracks if data.get("from_queue") else ()
        playlist = self.library(entry_id).create(data.get("name"), tracks)
        self._loaded_playlist[entry_id] = playlist.name
        await self.async_save()
        self._notify(entry_id)
        return self._playlist_payload(entry_id, playlist)

    def _remap_to_card(self, entry_id: str, tracks: Any) -> list[Track]:
        """Point stored tracks at the TF card that is in the device now.

        A track uri carries the card's own id (``/storage/<id>/...``), so after
        the card is swapped every stored playlist would point at a card that is
        gone. A track whose uri is not on the current card is matched by its
        path below the music folder instead; anything without a match stays as
        it is (it simply cannot be played, and the user sees that).
        """
        tracks = list(tracks)
        try:
            storage = self._snapshot_of(self._coordinator(entry_id))[2]
        except MusicError:
            return tracks
        files = storage.get("music_files")
        if not isinstance(files, list) or not files:
            return tracks
        by_uri: set[str] = set()
        by_relative: dict[str, Track] = {}
        for entry in files:
            try:
                candidate = track_from_music_file(entry, None)
            except MusicError:
                continue
            by_uri.add(candidate.uri)
            relative = relative_music_path(candidate.uri.removeprefix("local:"), None)
            by_relative.setdefault(relative.lower(), candidate)
        remapped: list[Track] = []
        for track in tracks:
            if not track.uri.startswith("local:") or track.uri in by_uri:
                remapped.append(track)
                continue
            relative = relative_music_path(track.uri.removeprefix("local:"), None)
            match = by_relative.get(relative.lower())
            if match is None:
                remapped.append(track)
                continue
            remapped.append(
                Track.make(
                    provider=track.provider,
                    uri=match.uri,
                    title=track.title,
                    duration=track.duration or match.duration,
                )
            )
        return remapped

    async def _op_playlist_load(self, data: dict[str, Any]) -> dict[str, Any]:
        """Replace the active queue with a stored playlist.

        The playlist is *copied*, never referenced: later queue mutations
        cannot change it. With ``resume`` the playlist starts again where it was
        last played (see ``_record_resume``): the remembered track is selected,
        started and - on a best-effort basis - moved to the remembered position.
        """
        entry_id = self._target(data, required=True)
        library = self.library(entry_id)
        playlist = library.resolve(data.get("name"))
        queue = self.queue(entry_id)
        queue.clear()
        tracks = self._remap_to_card(entry_id, playlist.tracks)
        added = queue.add_many(tracks)
        self._loaded_playlist[entry_id] = playlist.name
        resumed = None
        started = False
        record = self._resume.get(entry_id, {}).get(playlist.playlist_id)
        if data.get("resume") and record is not None and added:
            resumed = await self._resume_playlist(entry_id, playlist, added, record)
            started = resumed is not None
        if data.get("play") and not started and added:
            # No resume point (or not asked for): play the playlist from its start.
            queue.play_item(added[0].item_id)
            started = await self._async_play_current(entry_id)
        await self.async_save()
        self._notify(entry_id)
        # The queue payload already carries `loaded_playlist`, so no second
        # field is needed to say which playlist is active.
        payload = self.queue_payload(entry_id)
        payload["resumed"] = resumed
        payload["started"] = started
        return payload

    async def _resume_playlist(
        self, entry_id: str, playlist: Playlist, added: Any, record: dict[str, Any]
    ) -> dict[str, Any] | None:
        index = record["index"] if 0 <= record["index"] < playlist.count else None
        if index is None or playlist.tracks[index].uri != record["uri"]:
            index = next(
                (i for i, t in enumerate(playlist.tracks) if t.uri == record["uri"]), None
            )
        if index is None or index >= len(added):
            return None
        position = float(record["position"])
        track = playlist.tracks[index]
        duration = track.duration
        # Finished already (or within seconds of it): go on with the next track.
        if duration and position >= duration - RESUME_END_TOLERANCE_SECONDS:
            if index + 1 >= len(added):
                return None
            index, position = index + 1, 0.0
        position = max(position - RESUME_REWIND_SECONDS, 0.0)
        if position < RESUME_REWIND_SECONDS:
            position = 0.0
        queue = self.queue(entry_id)
        queue.play_item(added[index].item_id)
        started = await self._async_play_current(entry_id)
        sought = False
        if started and position > 0:
            coordinator = self._coordinator(entry_id)
            try:
                self._last_playback.pop(entry_id, None)
                self._observation_generation[entry_id] = (
                    self._observation_generation.get(entry_id, 0) + 1
                )
                await self._control(
                    coordinator,
                    "SEEK",
                    progress_ms=int(position * 1000),
                    track_id=added[index].track.uri,
                )
                sought = True
            except Exception:  # noqa: BLE001 - seeking is best effort
                # The device was not ready to seek yet: the track plays from its
                # start, which is better than failing the whole resume.
                sought = False
        return {
            "index": index,
            "title": added[index].track.title,
            "position": position if sought else 0.0,
            "seeked": sought,
        }

    async def _op_playlist_save(self, data: dict[str, Any]) -> dict[str, Any]:
        """Save the active queue into a stored playlist ("save current queue").

        An existing playlist of that name - or the currently loaded one when no
        name is given - is overwritten. A name that does not exist yet is
        *created* rather than rejected: that is what "save the queue under this
        name" means, and the card asks before overwriting anything else. The
        only remaining error case is "nothing loaded and no name given".
        """
        entry_id = self._target(data, required=True)
        library = self.library(entry_id)
        tracks = self.queue(entry_id).tracks
        name = data.get("name")
        reference = name or self._loaded_playlist.get(entry_id)
        if not reference:
            raise MusicError(
                "No playlist is loaded - give a name to save the queue as a new "
                "playlist."
            )
        existing = library.find_by_name(reference)
        if existing is None:
            playlist = library.create(reference, tracks)
        else:
            playlist = library.save_to(existing.playlist_id, tracks)
        self._loaded_playlist[entry_id] = playlist.name
        await self.async_save()
        self._notify(entry_id)
        return self._playlist_payload(entry_id, playlist)

    async def _op_playlist_save_as(self, data: dict[str, Any]) -> dict[str, Any]:
        """Always create a separate playlist - never silently overwrite."""
        entry_id = self._target(data, required=True)
        name = data.get("name")
        if not name:
            raise MusicError("'save as' needs a playlist name.")
        playlist = self.library(entry_id).save_as(
            name, self.queue(entry_id).tracks
        )
        self._loaded_playlist[entry_id] = playlist.name
        await self.async_save()
        self._notify(entry_id)
        return self._playlist_payload(entry_id, playlist)

    async def _op_playlist_rename(self, data: dict[str, Any]) -> dict[str, Any]:
        entry_id = self._target(data, required=True)
        playlist = self.library(entry_id).rename(data.get("name"), data.get("new_name"))
        if self._loaded_playlist.get(entry_id) is not None:
            self._loaded_playlist[entry_id] = playlist.name
        await self.async_save()
        self._notify(entry_id)
        return self._playlist_payload(entry_id, playlist)

    async def _op_playlist_delete(self, data: dict[str, Any]) -> dict[str, Any]:
        entry_id = self._target(data, required=True)
        playlist = self.library(entry_id).delete(data.get("name"))
        self._resume.get(entry_id, {}).pop(playlist.playlist_id, None)
        if self._loaded_playlist.get(entry_id) == playlist.name:
            self._loaded_playlist.pop(entry_id, None)
        await self.async_save()
        self._notify(entry_id)
        return {
            "deleted": playlist.summary(),
            "playlists": self._summaries(entry_id),
            "loaded_playlist": self._loaded_playlist.get(entry_id),
        }

    def _summaries(self, entry_id: str) -> list[dict[str, Any]]:
        """Playlist summaries, each with where it was last played (or ``None``)."""
        library = self.library(entry_id)
        records = self._resume.get(entry_id, {})
        rows = []
        for playlist in library.list():
            summary = playlist.summary()
            record = records.get(playlist.playlist_id)
            summary["resume"] = (
                {
                    "index": record["index"],
                    "title": record["title"],
                    "position": record["position"],
                    "updated_at": record.get("updated_at"),
                }
                if record is not None
                else None
            )
            rows.append(summary)
        return rows

    def _playlist_payload(
        self, entry_id: str, playlist: Playlist
    ) -> dict[str, Any]:
        return {
            "playlist": playlist.summary(),
            "playlists": self._summaries(entry_id),
            "loaded_playlist": self._loaded_playlist.get(entry_id),
            "queue": self.queue_payload(entry_id),
        }

    # -- dispatch ----------------------------------------------------------

    async def async_dispatch(
        self, operation: str, data: Any = None
    ) -> dict[str, Any]:
        """Single entry point for the WebSocket contract (and any future one)."""
        if not isinstance(operation, str) or operation not in OPERATIONS:
            raise MusicError(f"Unknown music operation {operation!r}.")
        handler = getattr(self, f"_op_{operation}", None)
        if handler is None:  # pragma: no cover - guarded by the contract test
            raise MusicError(f"Music operation {operation!r} is not implemented.")
        payload = dict(data) if isinstance(data, dict) else {}
        entry_id = self._target(payload)
        if entry_id is None:
            return await handler(payload)
        payload["target"] = entry_id
        async with self._target_locks.setdefault(entry_id, asyncio.Lock()):
            return await handler(payload)

    # -- helpers -----------------------------------------------------------

    def _target(self, data: dict[str, Any], *, required: bool = False) -> str | None:
        target = data.get("target")
        if isinstance(target, str) and target:
            return target
        default = self.default_target()
        if default is None and required:
            raise MusicError(
                "No CODE27 device is loaded, so there is no music target."
            )
        return default

    @staticmethod
    def _node(data: dict[str, Any]) -> str | None:
        node = data.get("node")
        return node if isinstance(node, str) and node else None

    # -- playback lifecycle ------------------------------------------------

    async def async_note_playback(self, entry_id: str, playback: Any) -> dict[str, Any]:
        # Capture before waiting: an observation queued behind a new PLAY/SEEK
        # belongs to the old command and must not undo the user's transition.
        generation = self._observation_generation.get(entry_id, 0)
        snapshot = dict(playback) if isinstance(playback, dict) else {}
        async with self._target_locks.setdefault(entry_id, asyncio.Lock()):
            if generation != self._observation_generation.get(entry_id, 0):
                return {"target": entry_id, "advanced": False, "resynced": False,
                        "took_over": False, "reason": "superseded_observation"}
            return await self._async_note_playback_locked(entry_id, snapshot)

    async def _async_note_playback_locked(
        self, entry_id: str, playback: Any
    ) -> dict[str, Any]:
        """Feed one CODE27 playback observation into the queue lifecycle.

        Called by the provider adapter on every real coordinator update (REST
        poll and WebSocket push), so all of this runs in Home Assistant and
        needs no open browser.

        Three things can happen while HASE owns playback (``queue.playing``):

        * the device went ``PLAYING -> IDLE`` - the track ended, so the queue
          advances (``advanced``);
        * the device is already playing the row the queue wanted next - the
          queue adopts it instead of restarting the track (``resynced``);
        * the device switched to anything else - CODE27 continues with the next
          *file in the folder* rather than the next queue row, so HASE takes
          playback back and starts the queue's own next row (``took_over``).
        """
        previous = self._last_playback.get(entry_id)
        snapshot = playback if isinstance(playback, dict) else {}
        self._last_playback[entry_id] = dict(snapshot)

        queue = self.queue(entry_id)
        evidence = track_end_evidence(previous, snapshot, queue.current_track)

        # M3: single_loop mode never fires PLAYING→IDLE so the primary trigger
        # above never detects the end.  A position reset (track looped back to
        # the beginning while still PLAYING) is only evidence with the same
        # track and a known nearby end. External seeks can be indistinguishable.
        if (
            not evidence["detected"]
            and snapshot.get("play_mode") == "single_loop"
            and snapshot.get("status") == "PLAYING"
            and previous is not None
            and previous.get("status") == "PLAYING"
            and tracks_match(playback_track(previous), queue.current_track)
            and tracks_match(playback_track(snapshot), queue.current_track)
        ):
            prev_pos = seconds(previous.get("position_ms"))
            curr_pos = seconds(snapshot.get("position_ms"))
            duration = seconds(previous.get("duration_ms"))
            if duration is None and queue.current_track is not None:
                duration = queue.current_track.duration
            if (
                duration is not None
                and prev_pos is not None
                and curr_pos is not None
                and duration > 3.0
                and prev_pos >= duration - TRACK_END_TOLERANCE_SECONDS
                and curr_pos < 1.0
            ):
                evidence = {
                    "detected": True,
                    "reason": "single_loop_position_reset",
                    "previous_position": prev_pos,
                    "current_position": curr_pos,
                }

        self._last_evidence[entry_id] = evidence

        observed = playback_track(snapshot)
        previous_track = playback_track(previous) if previous else None
        observed_uri = observed.uri if observed else None
        previous_uri = previous_track.uri if previous_track else None

        result: dict[str, Any] = {
            "target": entry_id,
            "evidence": evidence,
            "advanced": False,
            "resynced": False,
            "took_over": False,
        }
        try:
            await self._record_resume(entry_id, snapshot)
        except Exception:  # noqa: BLE001 - never let bookkeeping break playback
            _LOGGER.exception("HASEAMP: could not record the resume point")

        if not queue.playing:
            # The device owns playback (queue stopped): observe only.
            self._notify(entry_id)
            return result

        if observed is not None and tracks_match(observed, queue.current_track):
            # The device confirmed the row HASE started; from here on a
            # deviation from the queue is a deviation worth correcting.
            self._device_confirmed[entry_id] = observed_uri
        elif (
            observed_uri
            and observed_uri != previous_uri
            and self._device_confirmed.get(entry_id)
        ):
            # The device moved to a *different* track on its own.
            #
            # This is the real device's normal continuation: CODE27 keeps
            # playing the next file in the folder, and its status stays
            # PLAYING, so the playing->idle rule below never sees a track end.
            # Only a device that already confirmed the row HASE started is
            # corrected - a device whose reported track id never matches what
            # it was asked to play must not be fought over on every update.
            result.update(await self._async_follow_device(entry_id, observed))

        if evidence["detected"] and not (result["resynced"] or result["took_over"]):
            result["advanced"] = await self._async_advance(entry_id)

        self._notify(entry_id)
        return result

    async def _record_resume(self, entry_id: str, snapshot: dict[str, Any]) -> None:
        """Remember where the loaded playlist is being played.

        Only a track that belongs to the loaded playlist counts, and only while
        the device really plays or is paused on it. The record is persisted when
        the track changed, the position moved by RESUME_SAVE_EVERY_SECONDS, or
        on a pause (so the exact spot survives).
        """
        loaded = self._loaded_playlist.get(entry_id)
        if not loaded:
            return
        status = str(snapshot.get("status") or "").upper()
        if status not in {"PLAYING", "PAUSED"}:
            return
        observed = playback_track(snapshot)
        if observed is None:
            return
        playlist = self.library(entry_id).find_by_name(loaded)
        if playlist is None:
            return
        index = next(
            (i for i, track in enumerate(playlist.tracks) if tracks_match(track, observed)),
            None,
        )
        if index is None:
            return
        position = seconds(snapshot.get("position_ms")) or 0.0
        records = self._resume.setdefault(entry_id, {})
        previous = records.get(playlist.playlist_id)
        track = playlist.tracks[index]
        changed = (
            previous is None
            or previous["uri"] != track.uri
            or abs(position - previous["position"]) >= RESUME_SAVE_EVERY_SECONDS
            or (status == "PAUSED" and abs(position - previous["position"]) >= 1.0)
        )
        if not changed:
            return
        from datetime import datetime, timezone

        records[playlist.playlist_id] = {
            "uri": track.uri,
            "index": index,
            "title": track.title,
            "position": round(float(position), 1),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        await self.async_save()

    async def _async_follow_device(
        self, entry_id: str, observed: Track | None
    ) -> dict[str, Any]:
        """React to the device playing a track HASE did not ask for."""
        if observed is None:
            return {}
        # M1: in-flight guard — two concurrent observations must not both take
        # over playback; the second one defers entirely.
        if entry_id in self._correcting:
            return {}
        self._correcting.add(entry_id)
        try:
            queue = self.queue(entry_id)
            if tracks_match(observed, queue.current_track):
                # Our own previous command - nothing to correct.
                return {}

            expected = queue.peek_advance()
            if expected is not None and tracks_match(observed, expected.track):
                # The device already plays the row the queue wanted next: adopt it
                # rather than sending PLAY for the same track (which would restart
                # it from the beginning).
                queue.advance()
                self._consume_played(queue)
                await self.async_save()
                return {"resynced": True, "reason": "device_played_the_next_queue_row"}

            # Anything else means the device left the queue's order - CODE27
            # continues with the next file in the folder on its own. HASE owns
            # playback here (`queue.playing`), so it takes it back.
            return await self._async_take_over(entry_id)
        finally:
            self._correcting.discard(entry_id)

    async def _async_take_over(self, entry_id: str) -> dict[str, Any]:
        queue = self.queue(entry_id)
        entry = queue.advance()
        if entry is None:
            # The queue is finished; stop claiming to own playback.
            queue.set_playing(False)
            await self.async_save()
            return {"took_over": False, "reason": "queue_finished"}
        self._consume_played(queue)
        try:
            await self._async_play_track(entry_id, entry.track)
        except MusicError as err:
            _LOGGER.warning(
                "CODE27 music could not take playback back for %s: %s", entry_id, err
            )
            queue.set_playing(False)
            await self.async_save()
            return {"took_over": False, "reason": "play_failed"}
        await self.async_save()
        return {
            "took_over": True,
            "reason": "device_left_the_queue",
            "track": entry.track.to_dict(),
        }

    async def _async_advance(self, entry_id: str) -> bool:
        if entry_id in self._advancing:
            return False
        self._advancing.add(entry_id)
        try:
            queue = self.queue(entry_id)
            entry = queue.advance()
            if entry is None:
                # Queue exhausted (repeat=off): playback intent ends, the last
                # row stays visible.
                await self.async_save()
                return False
            self._consume_played(queue)
            try:
                await self._async_play_track(entry_id, entry.track)
            except MusicError as err:
                _LOGGER.warning(
                    "CODE27 music could not start the next queued track for %s: %s",
                    entry_id,
                    err,
                )
                queue.set_playing(False)
                await self.async_save()
                return False
            await self.async_save()
            return True
        finally:
            self._advancing.discard(entry_id)

    @staticmethod
    def _consume_played(queue: ActiveQueue) -> None:
        """Let the queue shrink when the user asked it to.

        Only ever called from the *automatic* paths (track end, device
        correction): a manual "play this row" must never delete the rows the
        user might still want to go back to.
        """
        if queue.consume_played:
            queue.drop_played()


def code27_bucket(hass: Any, *, create: bool = False) -> dict[str, Any]:
    """The CODE27 provider's shared ``hass.data`` bucket.

    ``hass.data[DOMAIN]`` holds the per-device coordinators keyed by entry id
    plus the shared buckets ("provider_registry", "mcp", "code27"). The music
    runtime keeps its one shared instance inside the CODE27 bucket so no
    HASE-wide state is introduced for a provider-owned feature.
    """
    data = getattr(hass, "data", None)
    if not isinstance(data, dict):
        return {}
    domain = data.get(DOMAIN)
    if not isinstance(domain, dict):
        if not create:
            return {}
        domain = data.setdefault(DOMAIN, {})
    if create:
        bucket = domain.setdefault(DATA_CODE27, {})
    else:
        bucket = domain.get(DATA_CODE27, {})
    return bucket if isinstance(bucket, dict) else {}


def async_get_music_runtime(hass: Any) -> MusicRuntime | None:
    backend = code27_bucket(hass).get(DATA_MUSIC)
    return backend if isinstance(backend, MusicRuntime) else None


_DATA_MUSIC_LOCK = f"{DATA_MUSIC}_setup_lock"


async def async_setup_music(hass: Any, *, store: Any | None = None) -> MusicRuntime:
    """Create and load the shared backend once per Home Assistant instance.

    Uses double-checked locking (M4): the fast path avoids the lock; the
    slow path re-checks under the lock to prevent two concurrent callers
    from both creating a MusicRuntime and the second overwriting the first.
    """
    existing = async_get_music_runtime(hass)
    if existing is not None:
        return existing
    bucket = code27_bucket(hass, create=True)
    lock = bucket.get(_DATA_MUSIC_LOCK)
    if lock is None:
        lock = asyncio.Lock()
        bucket[_DATA_MUSIC_LOCK] = lock
    async with lock:
        existing = async_get_music_runtime(hass)
        if existing is not None:
            return existing
        backend = MusicRuntime(hass, store=store)
        await backend.async_load()
        bucket[DATA_MUSIC] = backend
        return backend
