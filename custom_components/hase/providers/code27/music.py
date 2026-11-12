"""CODE27 music library access for the provider's music runtime.

Scope note: this module is the CODE27 side of that music support - it reads the CODE27 music snapshots the integration already polls and
pushes (``music_storage`` / ``music_playback`` / ``music_source``) and turns
them into the small, CODE27-owned row/browser model both consumers need. It is
not a
HASE-wide music abstraction and it deliberately does not replace the existing
media player entity or the ``hase/studio``-style provider architecture.

Pure Python (no Home Assistant imports) so the browser/path semantics can be
unit tested directly; ``media_player.py`` and ``music_runtime.py`` both use it,
which
is what keeps one single implementation of "how a TF-card path becomes a
folder and a track".

The one defect fixed here is the audit's CCF-2 / backlog P0-2: the live
``music_storage.music_files[].path`` values are **absolute**
(``/storage/<vol>/code27/music/<folder>/<file>.mp3``), while the previous
grouping code assumed paths relative to the music root. Splitting an absolute
path on the first ``/`` yields an empty folder name, which collapsed the whole
library into one unnamed folder. ``relative_music_path`` normalises against the
declared ``root_path`` and the device's own ``code27/music`` marker, and the
path handed to the device is never rewritten.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .music_model import MusicError, Track

PROVIDER_ID = "code27"

#: The device's fixed sub-directory under the TF card's mount point.
#: ``music_storage.root_path`` is the *volume* root (``/storage/<vol>``), so a
#: live file path reads
#: ``/storage/<vol>/code27/music/<folder>/<file>.mp3``.
MUSIC_PATH_MARKER = "code27/music"

KIND_FOLDER = "folder"
KIND_TRACK = "track"

#: Device ``music_playback.status`` -> the runtime's playback vocabulary.
#: ``media_player.py`` maps the same three device values onto
#: ``MediaPlayerState``; the device statuses themselves stay the authority.
PLAYBACK_STATUS = {
    "PLAYING": "playing",
    "PAUSED": "paused",
    "IDLE": "idle",
}

#: What CODE27 can actually do, as consumed by the card. Everything the device
#: does not expose stays ``False`` so the card renders it as unavailable
#: instead of pretending: the ``/data/user/music/control`` action set is
#: ``PLAY | PAUSE | PREVIOUS | NEXT | SET_PLAY_MODE | SEEK | SET_VOLUME |
#: SET_SOUND_EFFECT``. There is therefore no device ``STOP`` (the runtime's stop
#: is a
#: queue stop plus a device pause), while ``SEEK`` - taking ``progress_ms`` -
#: is real and is reached through the very same ``music_control`` call the media
#: player entity makes. Repeat/shuffle are runtime queue modes and therefore
#: always available, and they are not device features.
#: The presets the device accepts for ``SET_SOUND_EFFECT`` (OpenAPI enum).
SOUND_EFFECTS: tuple[str, ...] = ("classical", "surround", "vocal")

CODE27_MUSIC_FEATURES: dict[str, Any] = {
    "provider": PROVIDER_ID,
    "play": True,
    "pause": True,
    "stop": False,
    "next_track": True,
    "previous_track": True,
    "volume": True,
    "source": True,
    "seek": True,
    "browser": True,
    "enqueue_folder": True,
    "device_play_modes": True,
    # The device's own sound presets (its SET_SOUND_EFFECT action) - the card's
    # equalizer replacement. Offered only because the device really has them.
    "sound_effect": True,
    "sound_effects": SOUND_EFFECTS,
    "notes": (
        "No device STOP action: stop ends the queue and pauses the device.",
        "Seek is the device's own SEEK action (progress_ms), the same music_control call the media player entity issues.",
        "Repeat/shuffle are queue modes of this runtime, not CODE27 play modes.",
    ),
}

#: How close the reported position must be to the reported duration for the
#: "reached the end" note to count. Recorded as supporting evidence only - see
#: ``music_runtime.track_end_evidence``.
TRACK_END_TOLERANCE_SECONDS = 2.0

#: The device reports no end-of-track event; the runtime therefore advances the
#: queue on an observed ``playing -> idle`` transition of ``music_playback``.
#: Sent to the card so the UI can state what it relies on.
TRACK_END_CONTRACT: dict[str, Any] = {
    "mechanism": "code27_music_playback_status_transition",
    "explicit_provider_event": False,
    "signal": "music_playback.status: PLAYING -> IDLE",
    "requires_browser": False,
    "gap": (
        "CODE27 publishes no 'track finished' event. The music runtime advances the "
        "on the observed PLAYING -> IDLE transition of the device's own "
        "music_playback snapshot (REST poll plus WebSocket push), so it needs no "
        "open browser, but it can miss or delay the transition when the device "
        "reports a coarse snapshot. A device-side advance is followed instead of "
        "duplicated."
    ),
}


def normalize_path(value: Any) -> str:
    """Normalise separators only - never the meaning of a path."""
    return str(value or "").replace("\\", "/").strip()


def relative_music_path(path: Any, root: str | None = None) -> str:
    """A TF-card music path relative to the device's music root.

    ``root`` is ``music_storage.root_path`` (the card's mount point) and the
    marker is the device's own ``code27/music`` sub-directory. Paths that
    already arrive relative (older payload shapes, hand-written fixtures) are
    returned unchanged, so both shapes group identically.

    The result is used for grouping and display only; the uri the device is
    asked to play always keeps its original absolute path.
    """
    value = normalize_path(path).lstrip("/")
    clean_root = normalize_path(root).strip("/")
    marker = MUSIC_PATH_MARKER
    candidates = []
    if clean_root:
        candidates.append(f"{clean_root}/{marker}/")
    candidates.append(f"{marker}/")
    if clean_root:
        candidates.append(f"{clean_root}/")
    for prefix in candidates:
        if value == prefix.rstrip("/"):
            return ""
        if value.startswith(prefix):
            return value[len(prefix) :]
    # No declared root matched (or none was reported yet): locate the device's
    # own marker anywhere in the path instead of returning an unusable
    # absolute string - `music_storage` can arrive with `root_path` absent.
    index = value.find(f"{marker}/")
    if index > 0:
        return value[index + len(marker) + 1 :]
    return value


def group_music_files(
    files: Iterable[Any], root: str | None = None
) -> tuple[list[str], list[Any]]:
    """(sorted subfolder names, root-level file entries)."""
    folders: set[str] = set()
    root_files: list[Any] = []
    for entry in files:
        relative = relative_music_path(_file_path(entry), root)
        if "/" in relative:
            head = relative.split("/", 1)[0]
            if head:
                folders.add(head)
        else:
            root_files.append(entry)
    return sorted(folders), root_files


def music_files_in_folder(
    files: Iterable[Any], folder: str, root: str | None = None
) -> list[Any]:
    prefix = f"{normalize_path(folder).strip('/')}/"
    return [
        entry
        for entry in files
        if relative_music_path(_file_path(entry), root).startswith(prefix)
    ]


def _file_path(file: Any) -> str:
    return file.get("path") or "" if isinstance(file, dict) else ""


def _file_display_name(file: Any, relative: str) -> str:
    if isinstance(file, dict):
        name = (file.get("name") or "").strip()
        if name:
            return name
    basename = relative.rsplit("/", 1)[-1]
    return basename or relative


def track_from_music_file(file: Any, root: str | None = None) -> Track:
    """Turn one ``music_files[]`` entry into a queue row.

    ``track.uri`` is ``local:<path>`` with the path *exactly* as the device
    reported it, because that string is the ``track_id``
    ``music_control("PLAY", track_id=...)`` expects - and the same absolute
    path ``hase.delete_music_file`` takes.
    """
    raw_path = normalize_path(_file_path(file))
    if not raw_path:
        raise MusicError("A music file entry needs a 'path'.")
    relative = relative_music_path(raw_path, root)
    duration_ms = file.get("duration_ms") if isinstance(file, dict) else None
    duration = None
    if isinstance(duration_ms, (int, float)) and duration_ms > 0:
        duration = float(duration_ms) / 1000.0
    return Track.make(
        provider=PROVIDER_ID,
        uri=f"local:{raw_path}",
        title=_file_display_name(file, relative),
        duration=duration,
    )


@dataclass(frozen=True, slots=True)
class BrowseEntry:
    """One level of the CODE27 music tree: a folder or a playable track."""

    id: str
    title: str
    kind: str
    track: Track | None = None
    track_count: int = 0

    @property
    def can_expand(self) -> bool:
        return self.kind == KIND_FOLDER

    @property
    def can_play(self) -> bool:
        return self.kind == KIND_TRACK and self.track is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "can_play": self.can_play,
            "can_expand": self.can_expand,
            "track_count": self.track_count,
            "track": self.track.to_dict() if self.track else None,
        }


def build_browse_entries(
    files: Iterable[Any],
    *,
    root: str | None = None,
    folder: str | None = None,
) -> list[BrowseEntry]:
    """One browser level: subfolders first, then playable tracks.

    ``folder=None`` is the library root. Used by the card's browser and by the
    media player entity's ``async_browse_media``, so the two can never
    disagree about the library's shape.
    """
    file_list = list(files)
    if not folder:
        folders, root_files = group_music_files(file_list, root)
        entries = [
            BrowseEntry(
                id=f"folder:{name}",
                title=name,
                kind=KIND_FOLDER,
                track_count=len(music_files_in_folder(file_list, name, root)),
            )
            for name in folders
        ]
        entries.extend(_track_entry(entry, root) for entry in root_files)
        return entries

    clean_folder = normalize_path(folder).strip("/")
    prefix = f"{clean_folder}/"
    subfolders: list[str] = []
    direct_files: list[Any] = []
    for entry in file_list:
        relative = relative_music_path(_file_path(entry), root)
        if not relative.startswith(prefix):
            continue
        remainder = relative[len(prefix) :]
        if "/" in remainder:
            head = remainder.split("/", 1)[0]
            if head and head not in subfolders:
                subfolders.append(head)
        else:
            direct_files.append(entry)

    entries = [
        BrowseEntry(
            id=f"folder:{clean_folder}/{name}",
            title=name,
            kind=KIND_FOLDER,
            track_count=len(
                music_files_in_folder(file_list, f"{clean_folder}/{name}", root)
            ),
        )
        for name in sorted(subfolders)
    ]
    entries.extend(_track_entry(entry, root) for entry in direct_files)
    return entries


def _track_entry(file: Any, root: str | None) -> BrowseEntry:
    track = track_from_music_file(file, root)
    return BrowseEntry(
        id=track.uri, title=track.title, kind=KIND_TRACK, track=track
    )


# ---------------------------------------------------------------------------
# Device control error contract
# ---------------------------------------------------------------------------

#: ``error_code`` values the device is confirmed (live) to return from
#: ``/data/user/music/control`` - translated into messages that explain what to
#: actually do, instead of a raw device code surfacing as "Unknown error".
#: Shared by the media player entity and the music runtime so both report a
#: device-side
#: failure identically.
CONTROL_ERROR_MESSAGES = {
    "MODE_INACTIVE": (
        "CODIE's music mode isn't open on the device itself - open it on "
        "the device's own screen first, HA cannot do this remotely."
    ),
    "INVALID_ARGUMENT": (
        "CODIE has no active track to resume - select one first (e.g. via "
        "play_media) before pressing play."
    ),
}


def control_error_message(error_code: Any, fallback: str) -> str:
    """User-facing text for a device-reported music control failure."""
    return CONTROL_ERROR_MESSAGES.get(error_code, fallback)


# ---------------------------------------------------------------------------
# Playback snapshot projection
# ---------------------------------------------------------------------------


def playback_status(playback: Any) -> str:
    """Device status -> runtime vocabulary; unknown values read as idle, which
    is the same graceful degradation ``media_player.py`` already applies."""
    if not isinstance(playback, dict) or not playback:
        return "unavailable"
    return PLAYBACK_STATUS.get(playback.get("status"), "idle")


def playback_track(playback: Any) -> Track | None:
    """The device's current track, when it reports a usable ``track_id``.

    A ``track_name`` alone is not a playback handle, so it never becomes a
    Track - the card falls back to the queue row for a title instead.
    """
    if not isinstance(playback, dict):
        return None
    track_id = playback.get("track_id")
    if not track_id:
        return None
    duration_ms = playback.get("duration_ms")
    duration = (
        float(duration_ms) / 1000.0
        if isinstance(duration_ms, (int, float)) and duration_ms > 0
        else None
    )
    return Track.make(
        provider=PROVIDER_ID,
        uri=str(track_id),
        title=playback.get("track_name") or None,
        duration=duration,
    )


def seconds(value: Any) -> float | None:
    """Milliseconds -> seconds for the optional numeric playback fields."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) / 1000.0


def volume_fraction(playback: Any) -> float | None:
    if not isinstance(playback, dict):
        return None
    level = playback.get("volume_level")
    maximum = playback.get("volume_max") or 100
    if isinstance(level, bool) or not isinstance(level, (int, float)) or not maximum:
        return None
    return float(level) / float(maximum)


def playback_payload(
    storage: Any, playback: Any, source: Any, last_track_end: Any = None
) -> dict[str, Any]:
    """The card's playback block, read straight from the CODE27 snapshots.

    No separate playback model is introduced: these are the same
    ``Code27Data`` fields ``media_player.py`` exposes as entity attributes.
    """
    status = playback_status(playback)
    track = playback_track(playback)
    raw = playback if isinstance(playback, dict) else {}
    duration = track.duration if track and track.duration else seconds(
        raw.get("duration_ms")
    )
    return {
        "state": status,
        "available": status != "unavailable",
        "track": track.to_dict() if track else None,
        # The device reports a title for the active track even when no
        # track_id is present; kept so the LCD is not blank in that case.
        "title": (track.title if track else raw.get("track_name")),
        "artist": None,
        "position": seconds(raw.get("position_ms")),
        "duration": duration,
        "volume": volume_fraction(raw),
        "source": (source or {}).get("source") if isinstance(source, dict) else None,
        "is_playing": bool(raw.get("is_playing")),
        "device_play_mode": raw.get("play_mode"),
        "sound_effect": raw.get("sound_effect") if raw.get("sound_effect") in SOUND_EFFECTS else None,
        "storage_loaded": bool(isinstance(storage, dict) and storage.get("is_loaded")),
        "track_end": last_track_end,
    }


def library_is_readable(storage: Any) -> bool:
    """Whether the TF-card inventory actually lists files.

    The browser legitimately has nothing to show until an inventory arrives -
    which the card says so instead of looking broken. Since the REST read of
    ``/data/user/music/storage`` was added to the coordinator poll, an inventory
    no longer depends on the WebSocket push arriving first.
    """
    return bool(isinstance(storage, dict) and storage.get("music_files"))


def usable_music_storage(candidate: Any) -> bool:
    """Whether a payload actually *describes* the TF card.

    A usable payload always carries a ``music_files`` list - empty is fine (an
    empty or unloaded card) but absent is not, because "the answer does not
    contain an inventory" must never be read as "the inventory is now empty".
    This is the guard that keeps an unrelated update from erasing the music
    browser.
    """
    if not isinstance(candidate, dict) or not candidate:
        return False
    return isinstance(candidate.get("music_files"), list)


def merge_music_inventory(
    *, polled: Any, previous: Any, push_arrived: bool
) -> dict[str, Any]:
    """Decide which TF-card inventory HASE keeps.

    Precedence, highest first:

    1. a WebSocket push that arrived while the poll was in flight - it is by
       definition newer than the HTTP read it raced;
    2. a polled inventory that is usable and not older than the current one
       (same ``version``/``updated_at_ms`` rule the coordinator applies to the
       other ``music/*`` snapshots);
    3. the last known valid inventory.

    An unusable polled payload therefore *preserves* the previous inventory
    instead of replacing it, which is what stops an unrelated coordinator
    update from emptying the browser.
    """
    current = previous if isinstance(previous, dict) else {}
    if push_arrived:
        return current
    if not usable_music_storage(polled):
        return current
    return current if _inventory_is_older(polled, current) else polled


def _inventory_is_older(incoming: dict[str, Any], current: dict[str, Any]) -> bool:
    for key in ("updated_at_ms", "version"):
        left, right = incoming.get(key), current.get(key)
        if (
            isinstance(left, (int, float))
            and isinstance(right, (int, float))
            and not isinstance(left, bool)
            and not isinstance(right, bool)
            and left != right
        ):
            return left < right
    return False


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def music_library_payload(storage: Any) -> dict[str, Any]:
    """What the card needs to know about the inventory itself.

    ``revision`` is a stable identity of the inventory: the card refetches its
    folder listing exactly when this changes, instead of treating a snapshot
    that does not repeat the listing as an empty library.
    """
    data = storage if isinstance(storage, dict) else {}
    files = data.get("music_files")
    files = files if isinstance(files, list) else []
    root_path = data.get("root_path")
    root = root_path if isinstance(root_path, str) else None
    sig = "|".join(
        f"{f.get('path', '')}:{f.get('size_bytes', '')}:{f.get('duration_ms', '')}"
        for f in sorted(files, key=lambda x: x.get("path", ""))
    )
    revision = f"{root or ''}|{sig}" if (files or root is not None) else None
    return {
        "revision": revision,
        "ready": bool(files),
        "root_path": root,
        "file_count": len(files),
        "is_loaded": data.get("is_loaded"),
        "capacity_total_bytes": _int_or_none(data.get("capacity_total_bytes")),
        "capacity_used_bytes": _int_or_none(data.get("capacity_used_bytes")),
    }
