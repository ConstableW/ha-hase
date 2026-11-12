"""CODE27 music domain model: tracks, active queue, playlists.

This is CODE27's own model, owned by the CODE27 provider rather than a
HASE-wide music contract, and it holds what is worth testing on its own:

* the **active queue** (add / remove / move / clear / play item /
  next / previous / shuffle / repeat),
* **persistent playlists** (create / rename / delete / save / save as / load),
* the small track row model both of them store.

Pure Python (no Home Assistant, no device access) so the queue semantics are
directly unit testable; ``music_runtime.py`` adds persistence, the CODE27 coordinator
and automatic advance on top.
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

# -- repeat modes -----------------------------------------------------------

REPEAT_OFF = "off"
REPEAT_ALL = "all"
REPEAT_ONE = "one"
REPEAT_MODES: tuple[str, ...] = (REPEAT_OFF, REPEAT_ALL, REPEAT_ONE)

MAX_PLAYLIST_NAME_LENGTH = 64
MAX_QUEUE_ITEMS = 5000


class MusicError(ValueError):
    """A user-facing music validation failure.

    Raised for every rejected input (no such queue row, duplicate playlist
    name, unusable track payload, ...) so the WebSocket layer has exactly one
    error type to translate instead of leaking raw KeyError/TypeError.
    """


# ---------------------------------------------------------------------------
# Track row model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Track:
    """One queue/playlist row.

    Deliberately minimal: ``uri`` is CODE27's own playback handle
    (``local:<absolute TF path>``) and is passed to the device untouched,
    ``provider`` records which provider the handle belongs to (always
    ``code27`` today, kept so stored playlists stay interpretable),
    and ``id`` is a derived stable identity for the row.
    """

    id: str
    uri: str
    title: str
    artist: str | None = None
    duration: float | None = None
    provider: str = ""

    @staticmethod
    def make(
        *,
        provider: str,
        uri: str,
        title: str | None = None,
        artist: str | None = None,
        duration: float | None = None,
    ) -> Track:
        uri = (uri or "").strip()
        if not uri:
            raise MusicError("A track needs a non-empty uri.")
        if not provider:
            raise MusicError("A track needs a provider id.")
        clean_title = (title or "").strip() or _title_from_uri(uri)
        return Track(
            id=f"{provider}:{uri}",
            uri=uri,
            title=clean_title,
            artist=artist or None,
            duration=_optional_float(duration),
            provider=provider,
        )

    @staticmethod
    def from_dict(payload: Any, *, provider: str) -> Track:
        """Build a row from a card/service payload, tolerating omissions.

        A missing title falls back to the uri basename, so a partially filled
        payload from the browser is usable instead of failing the request; a
        missing uri is refused, because a row that cannot be played is worse
        than a clear error.
        """
        if isinstance(payload, Track):
            return payload
        if not isinstance(payload, dict):
            raise MusicError("A track must be an object.")
        uri = payload.get("uri") or payload.get("media_content_id") or ""
        if not uri:
            raise MusicError("A track needs a 'uri'.")
        return Track.make(
            provider=(payload.get("provider") or provider or "").strip(),
            uri=str(uri),
            title=payload.get("title") or payload.get("name"),
            artist=payload.get("artist"),
            duration=payload.get("duration"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "uri": self.uri,
            "title": self.title,
            "artist": self.artist,
            "duration": self.duration,
            "provider": self.provider,
        }


def _title_from_uri(uri: str) -> str:
    value = str(uri or "").replace("\\", "/")
    value = value.removeprefix("local:")
    return value.rsplit("/", 1)[-1] or value or "Unknown track"


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def normalize_track_uri(uri: Any) -> str:
    """Comparable form of a playback uri.

    The device is free to echo a handle back with or without the ``local:``
    transport prefix, so equality checks compare this normalised form - never
    the other way round: the uri the device is *asked* to play is always passed
    through untouched.
    """
    value = str(uri or "").replace("\\", "/").strip()
    value = value.removeprefix("local:")
    return value.lstrip("/")


def tracks_match(left: Track | None, right: Track | None) -> bool:
    """Whether two rows denote the same media, tolerating uri spelling."""
    if left is None or right is None:
        return False
    if left.id == right.id:
        return True
    normalized = normalize_track_uri(left.uri)
    return bool(normalized) and normalized == normalize_track_uri(right.uri)


# ---------------------------------------------------------------------------
# Active queue
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class QueueEntry:
    """One row of the active queue.

    ``item_id`` is the queue's own row identity and stays stable across
    reorders, so the card never has to address rows by bare list position.
    """

    item_id: int
    track: Track

    def to_dict(
        self, *, index: int, current: bool, played: bool = False
    ) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "index": index,
            "current": current,
            #: Already passed in the current playback pass. Played rows stay in
            #: the queue (Winamp behaviour) and are only marked.
            "played": played,
            "track": self.track.to_dict(),
        }


@dataclass
class ActiveQueue:
    """The active queue, with deterministic next/previous semantics.

    Display order (``items``) and playback order (``_order``) are separate, so
    reordering rows in the card never silently rewrites what shuffle plays
    next.

    Index semantics the card and the tests rely on:

    * ``index`` is the position of the current row in ``items``, ``None`` when
      nothing is current.
    * Removing a row *before* the current one shifts ``index`` down by one, so
      the same track stays current.
    * Removing the current row makes the following row current; removing the
      last row leaves nothing current instead of silently wrapping.
    * ``next_track()``/``previous_track()`` are explicit user actions and
      ignore repeat-one; ``advance()`` is the automatic track-end step and
      honours repeat-one/all/off.
    """

    items: list[QueueEntry] = field(default_factory=list)
    current_id: int | None = None
    playing: bool = False
    shuffle: bool = False
    repeat: str = REPEAT_OFF
    #: Remove a row once it has been played (off: keep and mark it).
    consume_played: bool = False
    _order: list[int] = field(default_factory=list)
    _next_item_id: int = 1

    # -- read-only views ---------------------------------------------------

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def index(self) -> int | None:
        if self.current_id is None:
            return None
        for position, entry in enumerate(self.items):
            if entry.item_id == self.current_id:
                return position
        return None

    @property
    def current(self) -> QueueEntry | None:
        position = self.index
        return None if position is None else self.items[position]

    @property
    def current_track(self) -> Track | None:
        entry = self.current
        return None if entry is None else entry.track

    @property
    def tracks(self) -> tuple[Track, ...]:
        return tuple(entry.track for entry in self.items)

    def entry(self, item_id: int) -> QueueEntry | None:
        for entry in self.items:
            if entry.item_id == item_id:
                return entry
        return None

    def played_item_ids(self) -> set[int]:
        """Rows the current playback pass has already moved past.

        The queue keeps played rows (Winamp-style playlist behaviour) unless
        ``consume_played`` is on, so the card needs to be able to tell them
        apart from what is still ahead. Read-only: it never mutates the queue.
        """
        position = self._order_position()
        if position is None:
            return set()
        return set(self._order[:position])

    def set_consume_played(self, enabled: bool) -> None:
        """Whether a played row leaves the queue.

        Off (default) keeps every row and only marks it - the Winamp behaviour,
        which still allows going back. On makes the queue shrink as it plays,
        which is closer to a radio-style "up next" list but gives up
        ``previous`` over played rows (they are gone).
        """
        self.consume_played = bool(enabled)

    def drop_played(self) -> list[QueueEntry]:
        """Remove the rows the current pass already moved past.

        Returns the removed rows. The current row is never touched: played rows
        are strictly *before* it in the playback order, so removing them shifts
        the display indices without changing what is playing.
        """
        removed: list[QueueEntry] = []
        for item_id in sorted(self.played_item_ids()):
            entry = self.entry(item_id)
            if entry is not None and self.remove(item_id):
                removed.append(entry)
        return removed

    # -- playback order ----------------------------------------------------

    def _reindex_order(self) -> None:
        """Keep ``_order`` a permutation of the item ids that still exist.

        Shuffle off: playback order follows display order. Shuffle on: the
        relative order of surviving rows is preserved, so adding a track does
        not re-shuffle what the user already listened to.
        """
        ids = [entry.item_id for entry in self.items]
        if not self.shuffle:
            self._order = ids
            return
        surviving = [item_id for item_id in self._order if item_id in ids]
        fresh = [item_id for item_id in ids if item_id not in surviving]
        random.Random().shuffle(fresh)
        self._order = surviving + fresh

    def _order_position(self) -> int | None:
        if self.current_id is None:
            return None
        try:
            return self._order.index(self.current_id)
        except ValueError:
            return None

    # -- mutation ----------------------------------------------------------

    def add(self, track: Track, *, position: int | None = None) -> QueueEntry:
        if len(self.items) >= MAX_QUEUE_ITEMS:
            raise MusicError(f"The queue is full ({MAX_QUEUE_ITEMS} items).")
        entry = QueueEntry(item_id=self._next_item_id, track=track)
        self._next_item_id += 1
        if position is None:
            self.items.append(entry)
        else:
            self.items.insert(_clamp_index(position, len(self.items)), entry)
        self._reindex_order()
        return entry

    def add_many(
        self, tracks: Iterable[Track], *, position: int | None = None
    ) -> list[QueueEntry]:
        added: list[QueueEntry] = []
        cursor = position
        for track in tracks:
            added.append(self.add(track, position=cursor))
            if cursor is not None:
                cursor += 1
        return added

    def remove(self, item_id: int) -> bool:
        position = next(
            (
                offset
                for offset, entry in enumerate(self.items)
                if entry.item_id == item_id
            ),
            None,
        )
        if position is None:
            return False

        self.items.pop(position)
        if self.current_id == item_id:
            if position < len(self.items):
                self.current_id = self.items[position].item_id
            else:
                self.current_id = None
        self._order = [value for value in self._order if value != item_id]
        self._reindex_order()
        if self.current_id is None:
            self.playing = False
        return True

    def remove_index(self, index: int) -> bool:
        position = _clamp_index(index, len(self.items), allow_end=True)
        if position is None or position >= len(self.items):
            return False
        return self.remove(self.items[position].item_id)

    def move(self, from_index: int, to_index: int) -> None:
        """Reorder one display row. Playback order is deliberately untouched.

        ``to_index`` is a position in the *original* row list, so moving row 0
        to the last position really ends up last (popping first would shift
        every later index down by one).
        """
        source = _clamp_index(from_index, len(self.items), allow_end=True)
        if source is None or source >= len(self.items):
            raise MusicError(f"No queue item at index {from_index}.")
        target = _clamp_index(to_index, len(self.items), allow_end=True)
        if target is None:
            raise MusicError(f"No queue position {to_index}.")
        if target == source:
            return
        entry = self.items.pop(source)
        self.items.insert(target, entry)

    def clear(self) -> None:
        self.items.clear()
        self._order.clear()
        self.current_id = None
        self.playing = False

    def set_playing(self, playing: bool) -> None:
        self.playing = bool(playing) and self.current_id is not None

    def stop(self) -> None:
        """HASE-side stop: playback intent ends, the current row is kept."""
        self.playing = False

    def set_shuffle(self, enabled: bool, *, rng: random.Random | None = None) -> None:
        enabled = bool(enabled)
        if enabled == self.shuffle:
            return
        self.shuffle = enabled
        ids = [entry.item_id for entry in self.items]
        if not enabled:
            self._order = ids
            return
        rest = [item_id for item_id in ids if item_id != self.current_id]
        (rng or random.Random()).shuffle(rest)
        # The current row anchors the shuffled order, so "next" continues from
        # what is playing instead of jumping backwards.
        self._order = ([self.current_id] if self.current_id is not None else []) + rest

    def set_repeat(self, mode: str) -> None:
        if mode not in REPEAT_MODES:
            raise MusicError(f"Unknown repeat mode: {mode!r}.")
        self.repeat = mode

    # -- selection ---------------------------------------------------------

    def play_index(self, index: int) -> QueueEntry:
        position = _clamp_index(index, len(self.items), allow_end=True)
        if position is None or position >= len(self.items):
            raise MusicError(f"No queue item at index {index}.")
        self.current_id = self.items[position].item_id
        self.playing = True
        return self.items[position]

    def play_item(self, item_id: int) -> QueueEntry:
        entry = self.entry(item_id)
        if entry is None:
            raise MusicError(f"No queue item with id {item_id}.")
        self.current_id = item_id
        self.playing = True
        return entry

    # -- navigation --------------------------------------------------------

    def next_track(self) -> QueueEntry | None:
        """Explicit user "next": wraps only on repeat-all."""
        return self._step(1, wrap=self.repeat == REPEAT_ALL)

    def previous_track(self) -> QueueEntry | None:
        """Explicit user "previous": wraps to the last row only on repeat-all."""
        return self._step(-1, wrap=self.repeat == REPEAT_ALL)

    def advance(self) -> QueueEntry | None:
        """Automatic track-end step.

        ``repeat=one`` replays the current row, ``repeat=all`` wraps around,
        ``repeat=off`` stops at the end and reports "nothing to play" (``None``)
        without clearing the current row.
        """
        if self.repeat == REPEAT_ONE:
            entry = self.current
            if entry is None:
                return None
            self.playing = True
            return entry
        entry = self._step(1, wrap=self.repeat == REPEAT_ALL)
        if entry is None:
            self.playing = False
        return entry

    def peek_advance(self) -> QueueEntry | None:
        """The row :meth:`advance` would move to, **without** moving.

        Used to recognise a device that already plays the row the queue wanted
        next - re-issuing ``PLAY`` for it would restart the track from zero.
        """
        if self.repeat == REPEAT_ONE:
            return self.current
        return self._peek(1, wrap=self.repeat == REPEAT_ALL)

    def _step(self, direction: int, *, wrap: bool) -> QueueEntry | None:
        entry = self._peek(direction, wrap=wrap)
        if entry is None:
            return None
        self.current_id = entry.item_id
        self.playing = True
        return entry

    def _peek(self, direction: int, *, wrap: bool) -> QueueEntry | None:
        if not self.items:
            return None
        fallback = self.items[0] if direction > 0 else self.items[-1]
        if self.current_id is None:
            return fallback
        position = self._order_position()
        if position is None:
            return fallback

        target = position + direction
        if target < 0 or target >= len(self._order):
            if not wrap:
                return None
            target %= len(self._order)
        return self.entry(self._order[target])

    # -- persistence -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [entry.track.to_dict() for entry in self.items],
            "item_ids": [entry.item_id for entry in self.items],
            "current_id": self.current_id,
            "playing": self.playing,
            "shuffle": self.shuffle,
            "repeat": self.repeat,
            "consume_played": self.consume_played,
            "order": list(self._order),
            "next_item_id": self._next_item_id,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> ActiveQueue:
        """Restore a queue, degrading to an empty one on unusable input."""
        queue = cls()
        if not isinstance(payload, dict):
            return queue
        raw_items = payload.get("items")
        tracks = raw_items if isinstance(raw_items, list) else []
        stored_ids = [
            value for value in payload.get("item_ids") or [] if isinstance(value, int)
        ]
        for offset, raw_track in enumerate(tracks):
            provider = ""
            if isinstance(raw_track, dict):
                provider = str(raw_track.get("provider") or "")
            try:
                track = Track.from_dict(raw_track, provider=provider)
            except MusicError:
                continue
            item_id = stored_ids[offset] if offset < len(stored_ids) else queue._next_item_id
            queue.items.append(QueueEntry(item_id=item_id, track=track))
        queue._next_item_id = max(
            [entry.item_id for entry in queue.items] + [queue._next_item_id - 1]
        ) + 1
        next_id = payload.get("next_item_id")
        if isinstance(next_id, int) and next_id > queue._next_item_id:
            queue._next_item_id = next_id

        current_id = payload.get("current_id")
        queue.current_id = (
            current_id
            if any(entry.item_id == current_id for entry in queue.items)
            else None
        )
        queue.shuffle = bool(payload.get("shuffle"))
        queue.consume_played = bool(payload.get("consume_played"))
        repeat = payload.get("repeat")
        queue.repeat = repeat if repeat in REPEAT_MODES else REPEAT_OFF
        queue._order = [
            value for value in payload.get("order") or [] if isinstance(value, int)
        ]
        queue._reindex_order()
        queue.playing = bool(payload.get("playing")) and queue.current_id is not None
        return queue


def _clamp_index(index: Any, length: int, *, allow_end: bool = False) -> int | None:
    """Clamp a caller-supplied index, returning ``None`` for non-numbers."""
    if isinstance(index, bool) or not isinstance(index, int):
        return None
    upper = length if allow_end else max(length - 1, 0)
    return max(0, min(index, upper))


# ---------------------------------------------------------------------------
# Persistent playlists
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Playlist:
    playlist_id: str
    name: str
    tracks: tuple[Track, ...] = ()
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def count(self) -> int:
        return len(self.tracks)

    def summary(self) -> dict[str, Any]:
        return {
            "playlist_id": self.playlist_id,
            "name": self.name,
            "count": self.count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "playlist_id": self.playlist_id,
            "name": self.name,
            "tracks": [track.to_dict() for track in self.tracks],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> Playlist | None:
        if not isinstance(payload, dict):
            return None
        playlist_id = payload.get("playlist_id")
        name = payload.get("name")
        if not isinstance(playlist_id, str) or not playlist_id:
            return None
        if not isinstance(name, str) or not name.strip():
            return None
        tracks: list[Track] = []
        for raw_track in payload.get("tracks") or []:
            provider = ""
            if isinstance(raw_track, dict):
                provider = str(raw_track.get("provider") or "")
            try:
                tracks.append(Track.from_dict(raw_track, provider=provider))
            except MusicError:
                continue
        return cls(
            playlist_id=playlist_id,
            name=name.strip(),
            tracks=tuple(tracks),
            created_at=payload.get("created_at"),
            updated_at=payload.get("updated_at"),
        )


class PlaylistLibrary:
    """Stored playlists for one CODE27 target, independent of the queue.

    The library only ever receives *copies* of the queue's rows (``Track`` is
    frozen, so sharing the objects is safe), which is what makes "a queue
    mutation must never silently change a stored playlist" true by
    construction rather than by convention.
    """

    def __init__(
        self,
        playlists: dict[str, Playlist] | None = None,
        *,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._playlists: dict[str, Playlist] = dict(playlists or {})
        self._now = now or _utc_now

    # -- views -------------------------------------------------------------

    def list(self) -> list[Playlist]:
        return sorted(self._playlists.values(), key=lambda item: item.name.casefold())

    def summaries(self) -> list[dict[str, Any]]:
        return [playlist.summary() for playlist in self.list()]

    def get(self, playlist_id: str) -> Playlist:
        playlist = self._playlists.get(playlist_id)
        if playlist is None:
            raise MusicError(f"No playlist with id {playlist_id!r}.")
        return playlist

    def find_by_name(self, name: str) -> Playlist | None:
        wanted = name.strip().casefold()
        for playlist in self._playlists.values():
            if playlist.name.casefold() == wanted:
                return playlist
        return None

    def resolve(self, reference: str) -> Playlist:
        """Look a playlist up by id or by name (the card uses names)."""
        if reference in self._playlists:
            return self._playlists[reference]
        playlist = self.find_by_name(reference)
        if playlist is None:
            raise MusicError(f"No playlist named {reference!r}.")
        return playlist

    # -- mutation ----------------------------------------------------------

    def create(self, name: str, tracks: Sequence[Track] = ()) -> Playlist:
        clean = _playlist_name(name)
        if self.find_by_name(clean) is not None:
            raise MusicError(f"A playlist named {clean!r} already exists.")
        timestamp = self._now()
        playlist = Playlist(
            playlist_id=_new_playlist_id(clean, self._playlists),
            name=clean,
            tracks=tuple(tracks),
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._playlists[playlist.playlist_id] = playlist
        return playlist

    def save_as(self, name: str, tracks: Sequence[Track]) -> Playlist:
        """Always create a new playlist - never silently overwrite."""
        return self.create(name, tracks)

    def save_to(self, reference: str, tracks: Sequence[Track]) -> Playlist:
        """Overwrite an existing playlist with a snapshot of the given rows."""
        playlist = self.resolve(reference)
        updated = replace(playlist, tracks=tuple(tracks), updated_at=self._now())
        self._playlists[playlist.playlist_id] = updated
        return updated

    def rename(self, reference: str, name: str) -> Playlist:
        playlist = self.resolve(reference)
        clean = _playlist_name(name)
        existing = self.find_by_name(clean)
        if existing is not None and existing.playlist_id != playlist.playlist_id:
            raise MusicError(f"A playlist named {clean!r} already exists.")
        updated = replace(playlist, name=clean, updated_at=self._now())
        self._playlists[playlist.playlist_id] = updated
        return updated

    def delete(self, reference: str) -> Playlist:
        playlist = self.resolve(reference)
        del self._playlists[playlist.playlist_id]
        return playlist

    def load(self, reference: str) -> tuple[Track, ...]:
        """The rows to copy into the active queue."""
        return tuple(self.resolve(reference).tracks)

    # -- persistence -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {playlist.playlist_id: playlist.to_dict() for playlist in self.list()}

    @classmethod
    def from_dict(
        cls, payload: Any, *, now: Callable[[], str] | None = None
    ) -> PlaylistLibrary:
        library = cls(now=now)
        if not isinstance(payload, dict):
            return library
        for raw in payload.values():
            playlist = Playlist.from_dict(raw)
            if playlist is not None:
                library._playlists[playlist.playlist_id] = playlist
        return library


def _playlist_name(name: Any) -> str:
    if not isinstance(name, str):
        raise MusicError("A playlist name must be a string.")
    clean = name.strip()
    if not clean:
        raise MusicError("A playlist name must not be empty.")
    if len(clean) > MAX_PLAYLIST_NAME_LENGTH:
        raise MusicError(
            f"A playlist name must be at most {MAX_PLAYLIST_NAME_LENGTH} characters."
        )
    return clean


def _new_playlist_id(name: str, existing: dict[str, Playlist]) -> str:
    slug = "".join(
        character if character.isalnum() else "-" for character in name.casefold()
    )
    slug = "-".join(part for part in slug.split("-") if part) or "playlist"
    candidate = slug
    suffix = 2
    while candidate in existing:
        candidate = f"{slug}-{suffix}"
        suffix += 1
    return candidate


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
