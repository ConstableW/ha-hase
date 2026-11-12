"""CODE27 music runtime tests: the backend behind the HASEAMP card.

Runs without a Home Assistant installation. The three pure CODE27 music
modules (``music_model``, ``music``, ``music_runtime``) are imported through a
synthetic package tree so their real relative imports resolve - no AST
stripping, no adapter injection, so the modules under test are exactly the
shipped ones. The only adapted boundaries are Home Assistant's ``Store`` and
the device client, both replaced by small in-memory doubles.

Covers the queue/playlist semantics, persistence across a backend recreation, the
"queue mutations never touch a stored playlist" guarantee, the CODE27 playback
lifecycle (autoadvance from an observed PLAYING -> IDLE transition), capability
gaps, unavailable-device behaviour, browser-to-queue mapping and the
frontend contract the HASEAMP card follows (``custom_components/hase/cards/haseamp-card.js``).
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import re
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
HASE = ROOT / "custom_components" / "hase"
PROVIDER = HASE / "providers" / "code27"
CARD = ROOT / "custom_components" / "hase" / "cards" / "haseamp-card.js"

ENTRY_ID = "entry-code27-kitchen"
OTHER_ENTRY_ID = "entry-code27-office"
PREFIX = "code27_provider_root"


def _load_pure_provider_modules():
    """Import the CODE27 music modules with their real relative imports.

    ``providers/code27/__init__.py`` imports Home Assistant, so the real package
    cannot be imported here. A synthetic package tree pointing at the same
    directories - plus the two Home-Assistant-free ``const`` modules - lets
    ``from ...const import DOMAIN`` / ``from .music import ...`` resolve for
    real instead of being stripped.
    """
    root = types.ModuleType(PREFIX)
    root.__path__ = [str(HASE)]
    sys.modules[PREFIX] = root
    for package in ("providers", "providers.code27"):
        parent_name, _, leaf = package.rpartition(".")
        module = types.ModuleType(f"{PREFIX}.{package}")
        module.__path__ = [str(HASE.joinpath(*package.split(".")))]
        sys.modules[module.__name__] = module
        setattr(sys.modules[f"{PREFIX}.{parent_name}" if parent_name else PREFIX], leaf, module)
    for name, path in (
        (f"{PREFIX}.const", HASE / "const.py"),
        (f"{PREFIX}.providers.code27.const", PROVIDER / "const.py"),
    ):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return {
        name: importlib.import_module(f"{PREFIX}.providers.code27.{name}")
        for name in ("music_model", "music", "music_runtime")
    }


_modules = _load_pure_provider_modules()
model_module = _modules["music_model"]
music_module = _modules["music"]
runtime = _modules["music_runtime"]

ActiveQueue = model_module.ActiveQueue
MusicError = model_module.MusicError
PlaylistLibrary = model_module.PlaylistLibrary
Track = model_module.Track
MusicRuntime = runtime.MusicRuntime
OPERATIONS = runtime.OPERATIONS


# ---------------------------------------------------------------------------
# doubles
# ---------------------------------------------------------------------------


class FakeStore:
    """In-memory ``homeassistant.helpers.storage.Store`` double.

    Every save round-trips through JSON, so a payload that is not actually
    serialisable fails the test instead of silently "persisting".
    """

    def __init__(self, payload=None):
        self.payload = payload
        self.saves = 0

    async def async_load(self):
        return json.loads(json.dumps(self.payload)) if self.payload is not None else None

    async def async_save(self, data):
        self.payload = json.loads(json.dumps(data))
        self.saves += 1


def make_entry(entry_id=ENTRY_ID, title="Kitchen CODIE"):
    return types.SimpleNamespace(entry_id=entry_id, title=title)


def make_coordinator(playback=None, storage=None, source=None, control=None):
    return types.SimpleNamespace(
        data=types.SimpleNamespace(
            music_playback=dict(playback or {}),
            music_storage=dict(storage or {}),
            music_source=dict(source or {}),
        ),
        client=types.SimpleNamespace(
            music_control=control
            if control is not None
            else AsyncMock(return_value={"status": "PLAYING"})
        ),
        async_request_refresh=AsyncMock(),
    )


def make_hass(entries=None, coordinators=None):
    entries = entries if entries is not None else [make_entry()]
    coordinators = coordinators if coordinators is not None else {ENTRY_ID: make_coordinator()}
    return types.SimpleNamespace(
        data={"hase": dict(coordinators)},
        config_entries=types.SimpleNamespace(
            async_entries=lambda domain: list(entries)
        ),
    )


def storage_fixture():
    """The live TF-card payload shape (audit: absolute paths + root_path)."""
    return {
        "is_loaded": True,
        "root_path": "/storage/142E2DF92E2DD4900",
        "music_files": [
            {
                "name": "top.mp3",
                "path": "/storage/142E2DF92E2DD4900/code27/music/top.mp3",
                "duration_ms": 1000,
            },
            {
                "name": "03 - Stand By Me.mp3",
                "path": (
                    "/storage/142E2DF92E2DD4900/code27/music/Rockkapella/"
                    "03 - Stand By Me.mp3"
                ),
                "duration_ms": 210000,
            },
            {
                "name": "live.mp3",
                "path": (
                    "/storage/142E2DF92E2DD4900/code27/music/Rockkapella/Live/live.mp3"
                ),
                "duration_ms": 5000,
            },
        ],
    }


PLAYING = {"status": "PLAYING", "track_id": "local:/x/a.mp3", "track_name": "a"}
IDLE = {"status": "IDLE"}


class BackendTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = FakeStore()
        self.coordinator = make_coordinator(storage=storage_fixture())
        self.hass = make_hass(coordinators={ENTRY_ID: self.coordinator})
        self.backend = MusicRuntime(self.hass, store=self.store)
        await self.backend.async_load()

    @property
    def control(self):
        """The device's ``music_control`` double, one call per assertion."""
        return self.coordinator.client.music_control

    def track(self, name, uri=None, duration=60.0):
        return Track.make(
            provider="code27",
            uri=uri or f"local:/storage/1/code27/music/{name}.mp3",
            title=name,
            duration=duration,
        )

    def playing(self, name, version=1):
        """A ``music_playback`` observation for one of the queue's own tracks.

        Consistent on purpose: the device reports back the ``track_id`` HASE
        sent it, which is what lets the runtime tell "the device confirmed my row"
        from "the device went somewhere else". Earlier fixtures used a track id
        that could never match, which hid the difference.
        """
        track = self.track(name)
        return {
            "status": "PLAYING",
            "track_id": track.uri,
            "track_name": name,
            "version": version,
        }

    def idle(self, name=None, version=2):
        observation = {"status": "IDLE", "version": version}
        if name:
            observation["track_id"] = self.track(name).uri
        return observation

    async def add(self, *names, play=False):
        return await self.backend.async_dispatch(
            "queue_add",
            {
                "target": ENTRY_ID,
                "tracks": [self.track(name).to_dict() for name in names],
                "play": play,
            },
        )


# ---------------------------------------------------------------------------
# pure queue semantics
# ---------------------------------------------------------------------------


class QueueModelTests(unittest.TestCase):
    def setUp(self):
        self.queue = ActiveQueue()
        self.tracks = [
            Track.make(provider="code27", uri=f"local:/x/{index}.mp3", title=f"t{index}")
            for index in range(4)
        ]
        self.queue.add_many(self.tracks)

    def test_add_appends_in_order_and_starts_without_a_current_row(self):
        self.assertEqual(self.queue.count, 4)
        self.assertIsNone(self.queue.index)
        self.assertIsNone(self.queue.current_track)
        self.assertEqual([t.title for t in self.queue.tracks], ["t0", "t1", "t2", "t3"])

    def test_add_at_position_inserts_without_reordering_the_rest(self):
        entry = self.queue.add(
            Track.make(provider="code27", uri="local:/x/new.mp3", title="new"),
            position=1,
        )
        self.assertEqual(entry.item_id, 5)
        self.assertEqual(
            [t.title for t in self.queue.tracks], ["t0", "new", "t1", "t2", "t3"]
        )

    def test_play_index_sets_current_and_playing(self):
        self.queue.play_index(2)
        self.assertEqual(self.queue.index, 2)
        self.assertEqual(self.queue.current_track.title, "t2")
        self.assertTrue(self.queue.playing)

    def test_play_index_out_of_range_is_rejected(self):
        with self.assertRaises(MusicError):
            self.queue.play_index(9)

    def test_remove_before_current_keeps_the_same_track_current(self):
        self.queue.play_index(2)
        self.assertTrue(self.queue.remove_index(0))
        self.assertEqual(self.queue.index, 1)
        self.assertEqual(self.queue.current_track.title, "t2")

    def test_remove_current_makes_the_following_row_current(self):
        self.queue.play_index(1)
        self.assertTrue(self.queue.remove_index(1))
        self.assertEqual(self.queue.index, 1)
        self.assertEqual(self.queue.current_track.title, "t2")

    def test_remove_last_row_leaves_nothing_current_and_stops(self):
        self.queue.play_index(3)
        self.assertTrue(self.queue.remove_index(3))
        self.assertIsNone(self.queue.index)
        self.assertIsNone(self.queue.current_id)
        self.assertFalse(self.queue.playing)

    def test_remove_unknown_item_reports_false(self):
        self.assertFalse(self.queue.remove(999))

    def test_move_reorders_display_rows_and_keeps_playback_order(self):
        self.queue.play_index(0)
        order_before = list(self.queue._order)
        self.queue.move(0, 3)
        self.assertEqual(
            [t.title for t in self.queue.tracks], ["t1", "t2", "t3", "t0"]
        )
        self.assertEqual(self.queue.current_track.title, "t0")
        self.assertEqual(self.queue.index, 3)
        self.assertEqual(self.queue._order, order_before)

    def test_move_out_of_range_is_rejected(self):
        with self.assertRaises(MusicError):
            self.queue.move(9, 0)

    def test_clear_empties_everything(self):
        self.queue.play_index(1)
        self.queue.clear()
        self.assertEqual(self.queue.count, 0)
        self.assertIsNone(self.queue.index)
        self.assertFalse(self.queue.playing)
        self.assertEqual(self.queue._order, [])

    def test_next_and_previous_do_not_wrap_without_repeat_all(self):
        self.queue.play_index(0)
        self.assertEqual(self.queue.previous_track(), None)
        self.queue.play_index(3)
        self.assertEqual(self.queue.next_track(), None)

    def test_next_and_previous_wrap_with_repeat_all(self):
        self.queue.set_repeat("all")
        self.queue.play_index(3)
        self.assertEqual(self.queue.next_track().track.title, "t0")
        self.queue.play_index(0)
        self.assertEqual(self.queue.previous_track().track.title, "t3")

    def test_next_from_an_unselected_queue_starts_at_the_first_row(self):
        entry = self.queue.next_track()
        self.assertEqual(entry.track.title, "t0")
        self.assertEqual(self.queue.index, 0)

    def test_repeat_one_replays_the_current_row_on_advance(self):
        self.queue.set_repeat("one")
        self.queue.play_index(1)
        entry = self.queue.advance()
        self.assertEqual(entry.track.title, "t1")
        self.assertEqual(self.queue.index, 1)
        self.assertTrue(self.queue.playing)

    def test_repeat_all_wraps_on_advance(self):
        self.queue.set_repeat("all")
        self.queue.play_index(3)
        self.assertEqual(self.queue.advance().track.title, "t0")

    def test_repeat_off_stops_at_the_end_without_clearing_the_row(self):
        self.queue.play_index(3)
        self.assertIsNone(self.queue.advance())
        self.assertFalse(self.queue.playing)
        self.assertEqual(self.queue.index, 3)

    def test_unknown_repeat_mode_is_rejected(self):
        with self.assertRaises(MusicError):
            self.queue.set_repeat("sometimes")

    def test_shuffle_reorders_playback_only_and_anchors_the_current_row(self):
        self.queue.play_index(1)
        self.queue.set_shuffle(True)
        self.assertEqual(self.queue._order[0], self.queue.current_id)
        self.assertEqual(sorted(self.queue._order), sorted(e.item_id for e in self.queue.items))
        self.assertEqual(
            [t.title for t in self.queue.tracks], ["t0", "t1", "t2", "t3"]
        )
        self.assertEqual(self.queue.index, 1)

    def test_turning_shuffle_off_restores_display_order(self):
        self.queue.set_shuffle(True)
        self.queue.set_shuffle(False)
        self.assertEqual(self.queue._order, [e.item_id for e in self.queue.items])

    def test_shuffle_survives_a_mutation_without_resuffling_heard_rows(self):
        self.queue.play_index(0)
        self.queue.set_shuffle(True)
        order = list(self.queue._order)
        self.queue.add(Track.make(provider="code27", uri="local:/x/z.mp3", title="z"))
        order.append(self.queue.items[-1].item_id)
        self.assertEqual(self.queue._order, order)

    def test_removing_a_shuffled_row_drops_it_from_the_playback_order(self):
        self.queue.set_shuffle(True)
        victim = self.queue.items[0].item_id
        self.queue.remove(victim)
        self.assertNotIn(victim, self.queue._order)

    def test_peek_advance_does_not_move_the_queue(self):
        self.queue.play_index(0)
        self.assertEqual(self.queue.peek_advance().track.title, "t1")
        self.assertEqual(self.queue.index, 0)
        self.assertEqual(self.queue.peek_advance().track.title, "t1")

    def test_peek_advance_honours_repeat_modes(self):
        self.queue.play_index(0)
        self.queue.set_repeat("one")
        self.assertEqual(self.queue.peek_advance().track.title, "t0")
        self.queue.set_repeat("all")
        self.queue.play_index(3)
        self.assertEqual(self.queue.peek_advance().track.title, "t0")
        self.queue.set_repeat("off")
        self.assertIsNone(self.queue.peek_advance())

    def test_played_rows_are_reported_and_kept(self):
        self.queue.play_index(0)
        self.assertEqual(self.queue.played_item_ids(), set())
        self.queue.advance()
        self.assertEqual(self.queue.played_item_ids(), {self.queue.items[0].item_id})
        self.queue.advance()
        self.assertEqual(
            self.queue.played_item_ids(),
            {self.queue.items[0].item_id, self.queue.items[1].item_id},
        )
        # Played rows stay in the queue (Winamp behaviour) - only marked.
        self.assertEqual(self.queue.count, 4)

    def test_played_rows_follow_the_playback_order_not_display_order(self):
        self.queue.play_index(0)
        self.queue.set_shuffle(True)
        first_in_order = self.queue._order[0]
        self.queue.advance()
        self.assertEqual(self.queue.played_item_ids(), {first_in_order})

    def test_repeat_all_wrap_clears_the_played_marks(self):
        self.queue.set_repeat("all")
        self.queue.play_index(3)
        self.queue.advance()  # wraps back to the first row
        self.assertEqual(self.queue.played_item_ids(), set())

    def test_consume_played_is_off_by_default(self):
        self.assertFalse(self.queue.consume_played)
        self.assertFalse(self.queue.to_dict()["consume_played"])

    def test_drop_played_removes_only_the_rows_already_passed(self):
        self.queue.set_consume_played(True)
        self.queue.play_index(0)
        self.queue.advance()
        self.queue.advance()
        removed = self.queue.drop_played()
        self.assertEqual([entry.track.title for entry in removed], ["t0", "t1"])
        # The row that is playing now is untouched.
        self.assertEqual([t.title for t in self.queue.tracks], ["t2", "t3"])
        self.assertEqual(self.queue.current_track.title, "t2")
        self.assertEqual(self.queue.index, 0)
        self.assertTrue(self.queue.playing)

    def test_drop_played_is_a_no_op_without_played_rows(self):
        self.assertEqual(self.queue.drop_played(), [])
        self.assertEqual(self.queue.count, 4)

    def test_consume_played_survives_persistence(self):
        self.queue.set_consume_played(True)
        restored = ActiveQueue.from_dict(json.loads(json.dumps(self.queue.to_dict())))
        self.assertTrue(restored.consume_played)

    def test_queue_round_trips_through_persistence(self):
        self.queue.play_index(2)
        self.queue.set_shuffle(True)
        self.queue.set_repeat("all")
        restored = ActiveQueue.from_dict(json.loads(json.dumps(self.queue.to_dict())))
        self.assertEqual(restored.count, self.queue.count)
        self.assertEqual(restored.index, self.queue.index)
        self.assertEqual(restored.current_track.title, self.queue.current_track.title)
        self.assertTrue(restored.shuffle)
        self.assertEqual(restored.repeat, "all")
        self.assertEqual(
            [t.title for t in restored.tracks], [t.title for t in self.queue.tracks]
        )
        self.assertTrue(restored.playing)

    def test_unusable_persistence_payload_degrades_to_an_empty_queue(self):
        self.assertEqual(ActiveQueue.from_dict(None).count, 0)
        self.assertEqual(ActiveQueue.from_dict({"items": "nope"}).count, 0)
        queue = ActiveQueue.from_dict(
            {"items": [{"uri": ""}, {"uri": "local:/x/ok.mp3", "provider": "code27"}]}
        )
        self.assertEqual(queue.count, 1)
        self.assertEqual(queue.tracks[0].uri, "local:/x/ok.mp3")


# ---------------------------------------------------------------------------
# track model
# ---------------------------------------------------------------------------


class TrackModelTests(unittest.TestCase):
    def test_track_id_is_provider_scoped_and_uri_is_kept_verbatim(self):
        track = Track.make(
            provider="code27",
            uri="local:/storage/142E2DF92E2DD4900/code27/music/a b.mp3",
            title="A B",
            duration=12.5,
        )
        self.assertEqual(
            track.uri, "local:/storage/142E2DF92E2DD4900/code27/music/a b.mp3"
        )
        self.assertEqual(track.id, f"code27:{track.uri}")
        self.assertEqual(track.provider, "code27")
        self.assertEqual(track.duration, 12.5)

    def test_title_falls_back_to_the_uri_basename(self):
        track = Track.make(provider="code27", uri="local:/x/y/song.mp3")
        self.assertEqual(track.title, "song.mp3")

    def test_missing_uri_is_rejected(self):
        with self.assertRaises(MusicError):
            Track.make(provider="code27", uri="   ")
        with self.assertRaises(MusicError):
            Track.from_dict({"title": "no uri"}, provider="code27")

    def test_non_object_payload_is_rejected(self):
        with self.assertRaises(MusicError):
            Track.from_dict("local:/x/a.mp3", provider="code27")

    def test_from_dict_prefers_a_payload_provider_but_falls_back(self):
        explicit = Track.from_dict(
            {"uri": "local:/x/a.mp3", "provider": "code27", "title": "a"},
            provider="ignored",
        )
        self.assertEqual(explicit.provider, "code27")
        fallback = Track.from_dict({"uri": "local:/x/a.mp3"}, provider="code27")
        self.assertEqual(fallback.provider, "code27")

    def test_useless_values_never_become_a_duration(self):
        for value in (None, True, "abc", float("nan"), float("inf")):
            with self.subTest(value=value):
                track = Track.make(provider="code27", uri="local:/x/a.mp3", duration=value)
                self.assertIsNone(track.duration)

    def test_uri_comparison_tolerates_the_local_prefix(self):
        left = Track.make(provider="code27", uri="local:/storage/x/a.mp3")
        right = Track.make(provider="code27", uri="/storage/x/a.mp3")
        self.assertTrue(model_module.tracks_match(left, right))
        self.assertFalse(model_module.tracks_match(left, None))

    def test_different_tracks_do_not_match(self):
        left = Track.make(provider="code27", uri="local:/storage/x/a.mp3")
        right = Track.make(provider="code27", uri="local:/storage/x/b.mp3")
        self.assertFalse(model_module.tracks_match(left, right))


# ---------------------------------------------------------------------------
# playlist library
# ---------------------------------------------------------------------------


class PlaylistLibraryTests(unittest.TestCase):
    def setUp(self):
        self.library = PlaylistLibrary()
        self.tracks = [
            Track.make(provider="code27", uri=f"local:/x/{index}.mp3", title=f"t{index}")
            for index in range(3)
        ]

    def test_create_list_and_load(self):
        self.library.create("Rock", self.tracks)
        self.assertEqual([p.name for p in self.library.list()], ["Rock"])
        self.assertEqual([t.title for t in self.library.load("Rock")], ["t0", "t1", "t2"])

    def test_duplicate_name_is_rejected_case_insensitively(self):
        self.library.create("Rock", self.tracks)
        with self.assertRaises(MusicError):
            self.library.create("rock", self.tracks)

    def test_empty_or_oversized_name_is_rejected(self):
        with self.assertRaises(MusicError):
            self.library.create("   ", self.tracks)
        with self.assertRaises(MusicError):
            self.library.create("x" * 65, self.tracks)

    def test_create_makes_a_stable_unique_id(self):
        first = self.library.create("Rock & Roll", self.tracks)
        self.assertEqual(first.playlist_id, "rock-roll")
        second = self.library.create("Rock Roll", self.tracks)
        self.assertNotEqual(first.playlist_id, second.playlist_id)

    def test_save_to_overwrites_an_existing_playlist(self):
        self.library.create("Rock", self.tracks)
        self.library.save_to("Rock", self.tracks[:1])
        self.assertEqual(self.library.get("rock").count, 1)

    def test_save_as_creates_a_separate_playlist_and_never_overwrites(self):
        self.library.create("Rock", self.tracks)
        created = self.library.save_as("Other", self.tracks[:2])
        self.assertEqual(created.name, "Other")
        self.assertEqual(
            [p.name for p in self.library.list()], ["Other", "Rock"]
        )
        self.assertEqual(self.library.get("other").count, 2)
        self.assertEqual(self.library.get("rock").count, 3)
        # Saving *as* an existing name is refused instead of silently
        # replacing that playlist - the card shows the message.
        with self.assertRaises(MusicError):
            self.library.save_as("Rock", self.tracks)

    def test_rename_and_delete(self):
        self.library.create("Rock", self.tracks)
        self.library.rename("Rock", "Rockabilly")
        self.assertEqual([p.name for p in self.library.list()], ["Rockabilly"])
        self.library.delete("Rockabilly")
        self.assertEqual(self.library.list(), [])

    def test_rename_onto_an_existing_name_is_rejected(self):
        self.library.create("Rock", self.tracks)
        self.library.create("Pop", self.tracks)
        with self.assertRaises(MusicError):
            self.library.rename("Pop", "Rock")

    def test_missing_playlist_is_reported_clearly(self):
        with self.assertRaises(MusicError):
            self.library.get("nope")
        with self.assertRaises(MusicError):
            self.library.load("nope")

    def test_queue_mutation_can_never_change_a_stored_playlist(self):
        queue = ActiveQueue()
        queue.add_many(self.tracks)
        self.library.create("Rock", queue.tracks)

        # Every queue mutation the card can perform.
        queue.add(Track.make(provider="code27", uri="local:/x/extra.mp3"))
        queue.move(0, 2)
        queue.remove_index(0)
        queue.clear()

        stored = self.library.resolve("Rock")
        self.assertEqual(stored.count, 3)
        self.assertEqual([t.title for t in stored.tracks], ["t0", "t1", "t2"])
        self.assertEqual(queue.count, 0)

    def test_library_round_trips_through_persistence(self):
        self.library.create("Rock", self.tracks)
        self.library.create("Chill", ())
        restored = PlaylistLibrary.from_dict(
            json.loads(json.dumps(self.library.to_dict()))
        )
        self.assertEqual([p.name for p in restored.list()], ["Chill", "Rock"])
        self.assertEqual(restored.resolve("Rock").count, 3)
        self.assertEqual(restored.get("rock").count, 3)

    def test_unusable_entries_are_dropped_instead_of_crashing(self):
        restored = PlaylistLibrary.from_dict(
            {"ok": {"playlist_id": "ok", "name": "Ok", "tracks": [{"uri": ""}]}, "bad": 5}
        )
        self.assertEqual([p.name for p in restored.list()], ["Ok"])
        self.assertEqual(restored.get("ok").count, 0)


# ---------------------------------------------------------------------------
# browser -> queue mapping
# ---------------------------------------------------------------------------


class BrowserMappingTests(unittest.TestCase):
    def test_root_lists_folders_then_root_tracks(self):
        entries = music_module.build_browse_entries(
            storage_fixture()["music_files"],
            root=storage_fixture()["root_path"],
        )
        self.assertEqual(
            [(e.title, e.kind, e.track_count) for e in entries],
            [("Rockkapella", "folder", 2), ("top.mp3", "track", 0)],
        )

    def test_folder_lists_its_own_subfolder_and_tracks(self):
        entries = music_module.build_browse_entries(
            storage_fixture()["music_files"],
            root=storage_fixture()["root_path"],
            folder="Rockkapella",
        )
        self.assertEqual(
            [(e.title, e.kind) for e in entries],
            [("Live", "folder"), ("03 - Stand By Me.mp3", "track")],
        )

    def test_nested_folder_is_reachable(self):
        entries = music_module.build_browse_entries(
            storage_fixture()["music_files"],
            root=storage_fixture()["root_path"],
            folder="Rockkapella/Live",
        )
        self.assertEqual([e.title for e in entries], ["live.mp3"])

    def test_unknown_node_is_rejected_by_the_backend(self):
        backend = MusicRuntime(make_hass(), store=FakeStore())
        with self.assertRaises(MusicError):
            backend._tree_payload(make_coordinator(storage=storage_fixture()), "bogus")

    def test_browser_track_payload_is_a_valid_queue_row(self):
        entries = music_module.build_browse_entries(
            storage_fixture()["music_files"],
            root=storage_fixture()["root_path"],
            folder="Rockkapella",
        )
        track_entry = next(e for e in entries if e.kind == "track")
        payload = track_entry.to_dict()["track"]
        row = Track.from_dict(payload, provider="code27")
        self.assertEqual(
            row.uri,
            "local:/storage/142E2DF92E2DD4900/code27/music/Rockkapella/"
            "03 - Stand By Me.mp3",
        )
        self.assertEqual(row.title, "03 - Stand By Me.mp3")
        self.assertEqual(row.duration, 210.0)
        self.assertEqual(row.provider, "code27")

    def test_library_is_reported_as_not_ready_until_the_inventory_arrives(self):
        self.assertFalse(music_module.library_is_readable({}))
        self.assertFalse(music_module.library_is_readable({"music_files": []}))
        self.assertTrue(music_module.library_is_readable(storage_fixture()))


# ---------------------------------------------------------------------------
# backend operations
# ---------------------------------------------------------------------------


class BackendOperationTests(BackendTestCase):
    async def test_snapshot_describes_the_device_queue_and_playlists(self):
        snapshot = await self.backend.async_dispatch("snapshot", {})
        self.assertEqual(snapshot["provider"], "code27")
        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["target"]["id"], ENTRY_ID)
        self.assertEqual(snapshot["target"]["name"], "Kitchen CODIE")
        self.assertEqual(snapshot["queue"]["count"], 0)
        self.assertEqual(snapshot["playlists"], [])
        self.assertEqual(sorted(snapshot["operations"]), sorted(OPERATIONS))
        self.assertIn("PLAYING", snapshot["track_end"]["signal"])

    async def test_queue_add_appends_and_persists(self):
        result = await self.add("a", "b")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["added"], 2)
        self.assertEqual([row["track"]["title"] for row in result["items"]], ["a", "b"])
        self.assertEqual(self.store.payload["targets"][ENTRY_ID]["queue"]["items"][0]["title"], "a")

    async def test_queue_add_with_play_starts_the_first_added_track(self):
        result = await self.add("a", "b", play=True)
        self.assertTrue(result["played"])
        self.control.assert_awaited_once()
        args, kwargs = self.control.await_args
        self.assertEqual(args[0], "PLAY")
        self.assertEqual(kwargs["track_id"], self.track("a").uri)
        self.assertTrue(result["playing"])
        self.assertEqual(result["index"], 0)

    @property
    def control(self):
        return self.coordinator.client.music_control

    async def test_queue_play_by_index_and_by_item_id(self):
        await self.add("a", "b", "c")
        by_index = await self.backend.async_dispatch(
            "queue_play", {"target": ENTRY_ID, "index": 2}
        )
        self.assertEqual(by_index["index"], 2)
        self.assertEqual(by_index["current_track"]["title"], "c")

        item_id = by_index["items"][0]["item_id"]
        by_item = await self.backend.async_dispatch(
            "queue_play", {"target": ENTRY_ID, "item_id": item_id}
        )
        self.assertEqual(by_item["index"], 0)
        self.assertEqual(by_item["current_track"]["title"], "a")

    async def test_queue_play_on_an_empty_queue_is_rejected(self):
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch("queue_play", {"target": ENTRY_ID})

    async def test_queue_remove_by_index_and_item_id(self):
        await self.add("a", "b", "c")
        removed = await self.backend.async_dispatch(
            "queue_remove", {"target": ENTRY_ID, "index": 1}
        )
        self.assertEqual([row["track"]["title"] for row in removed["items"]], ["a", "c"])
        item_id = removed["items"][0]["item_id"]
        removed = await self.backend.async_dispatch(
            "queue_remove", {"target": ENTRY_ID, "item_id": item_id}
        )
        self.assertEqual([row["track"]["title"] for row in removed["items"]], ["c"])

    async def test_queue_remove_without_a_selector_is_rejected(self):
        await self.add("a")
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch("queue_remove", {"target": ENTRY_ID})

    async def test_queue_remove_unknown_index_is_rejected(self):
        await self.add("a")
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch(
                "queue_remove", {"target": ENTRY_ID, "index": 9}
            )

    async def test_queue_move_reorders(self):
        await self.add("a", "b", "c")
        moved = await self.backend.async_dispatch(
            "queue_move", {"target": ENTRY_ID, "from_index": 0, "to_index": 2}
        )
        self.assertEqual(
            [row["track"]["title"] for row in moved["items"]], ["b", "c", "a"]
        )

    async def test_queue_clear_empties_the_queue_and_keeps_playlists(self):
        await self.add("a", "b")
        await self.backend.async_dispatch(
            "playlist_create", {"target": ENTRY_ID, "name": "Keep", "from_queue": True}
        )
        cleared = await self.backend.async_dispatch(
            "queue_clear", {"target": ENTRY_ID}
        )
        self.assertEqual(cleared["count"], 0)
        self.assertEqual(self.backend.library(ENTRY_ID).resolve("Keep").count, 2)

    async def test_transport_next_and_previous_follow_the_queue(self):
        await self.add("a", "b", play=True)
        next_result = await self.backend.async_dispatch(
            "transport", {"target": ENTRY_ID, "action": "next"}
        )
        self.assertEqual(next_result["current_track"]["title"], "b")
        previous_result = await self.backend.async_dispatch(
            "transport", {"target": ENTRY_ID, "action": "previous"}
        )
        self.assertEqual(previous_result["current_track"]["title"], "a")

    async def test_transport_next_at_the_end_reports_a_clear_error(self):
        await self.add("a", play=True)
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch(
                "transport", {"target": ENTRY_ID, "action": "next"}
            )

    async def test_transport_stop_pauses_and_ends_the_queue_intent(self):
        await self.add("a", "b", play=True)
        result = await self.backend.async_dispatch(
            "transport", {"target": ENTRY_ID, "action": "stop"}
        )
        self.assertFalse(result["playing"])
        args, _kwargs = self.control.await_args
        self.assertEqual(args[0], "PAUSE")

    async def test_transport_pause_and_play(self):
        await self.add("a", play=True)
        paused = await self.backend.async_dispatch(
            "transport", {"target": ENTRY_ID, "action": "pause"}
        )
        self.assertFalse(paused["playing"])
        resumed = await self.backend.async_dispatch(
            "transport", {"target": ENTRY_ID, "action": "play"}
        )
        self.assertTrue(resumed["playing"])
        self.assertEqual([call.args[0] for call in self.control.await_args_list][-1], "PLAY")

    async def test_transport_with_an_unknown_action_is_rejected(self):
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch(
                "transport", {"target": ENTRY_ID, "action": "rewind"}
            )

    async def test_transport_when_nothing_is_current_plays_the_first_row(self):
        await self.add("a", "b")
        result = await self.backend.async_dispatch(
            "transport", {"target": ENTRY_ID, "action": "play"}
        )
        self.assertEqual(result["index"], 0)
        self.assertTrue(result["playing"])

    async def test_set_volume_maps_to_the_device_scale(self):
        result = await self.backend.async_dispatch(
            "set_volume", {"target": ENTRY_ID, "volume": 0.42}
        )
        self.assertEqual(result["volume"], 0.42)
        _args, kwargs = self.control.await_args
        self.assertEqual(kwargs["volume_level"], 42)

    async def test_set_volume_rejects_out_of_range_and_non_numbers(self):
        for value in (-0.1, 1.5, "loud", True):
            with self.subTest(value=value), self.assertRaises(MusicError):
                await self.backend.async_dispatch(
                    "set_volume", {"target": ENTRY_ID, "volume": value}
                )

    async def test_shuffle_and_repeat_are_queue_modes_and_persist(self):
        await self.add("a", "b")
        shuffled = await self.backend.async_dispatch(
            "set_shuffle", {"target": ENTRY_ID, "shuffle": True}
        )
        self.assertTrue(shuffled["shuffle"])
        repeated = await self.backend.async_dispatch(
            "set_repeat", {"target": ENTRY_ID, "repeat": "all"}
        )
        self.assertEqual(repeated["repeat"], "all")
        self.assertTrue(self.store.payload["targets"][ENTRY_ID]["queue"]["shuffle"])
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch(
                "set_repeat", {"target": ENTRY_ID, "repeat": "twice"}
            )

    async def test_browse_returns_the_device_tree_with_readiness(self):
        tree = await self.backend.async_dispatch("browse", {"target": ENTRY_ID})
        self.assertTrue(tree["library_ready"])
        self.assertEqual(tree["node"], None)
        self.assertEqual(
            [(entry["title"], entry["kind"]) for entry in tree["entries"]],
            [("Rockkapella", "folder"), ("top.mp3", "track")],
        )
        nested = await self.backend.async_dispatch(
            "browse", {"target": ENTRY_ID, "node": "folder:Rockkapella"}
        )
        self.assertEqual(
            [(entry["title"], entry["kind"]) for entry in nested["entries"]],
            [("Live", "folder"), ("03 - Stand By Me.mp3", "track")],
        )

    async def test_browse_reports_an_unready_library_instead_of_failing(self):
        self.coordinator.data.music_storage = {}
        tree = await self.backend.async_dispatch("browse", {"target": ENTRY_ID})
        self.assertFalse(tree["library_ready"])
        self.assertEqual(tree["entries"], [])

    async def test_enqueue_folder_adds_the_whole_subtree(self):
        result = await self.backend.async_dispatch(
            "enqueue_folder", {"target": ENTRY_ID, "node": "folder:Rockkapella"}
        )
        self.assertEqual(result["added"], 2)
        titles = [row["track"]["title"] for row in result["items"]]
        self.assertEqual(titles, ["03 - Stand By Me.mp3", "live.mp3"])

    async def test_enqueue_folder_requires_a_folder(self):
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch(
                "enqueue_folder", {"target": ENTRY_ID, "node": ""}
            )

    async def test_enqueue_folder_without_tracks_is_rejected(self):
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch(
                "enqueue_folder", {"target": ENTRY_ID, "node": "folder:Empty"}
            )

    async def test_playlist_crud_through_the_contract(self):
        await self.add("a", "b")
        created = await self.backend.async_dispatch(
            "playlist_create", {"target": ENTRY_ID, "name": "Rock", "from_queue": True}
        )
        self.assertEqual(created["playlist"]["count"], 2)
        self.assertEqual(created["loaded_playlist"], "Rock")

        await self.backend.async_dispatch("queue_clear", {"target": ENTRY_ID})
        loaded = await self.backend.async_dispatch(
            "playlist_load", {"target": ENTRY_ID, "name": "Rock"}
        )
        self.assertEqual(loaded["count"], 2)
        self.assertEqual(loaded["loaded_playlist"], "Rock")
        self.assertEqual(
            [row["track"]["title"] for row in loaded["items"]], ["a", "b"]
        )

        await self.add("c")
        saved = await self.backend.async_dispatch(
            "playlist_save", {"target": ENTRY_ID}
        )
        self.assertEqual(saved["playlist"]["count"], 3)

        renamed = await self.backend.async_dispatch(
            "playlist_rename",
            {"target": ENTRY_ID, "name": "Rock", "new_name": "Rockabilly"},
        )
        self.assertEqual(renamed["playlist"]["name"], "Rockabilly")
        self.assertEqual(renamed["loaded_playlist"], "Rockabilly")

        await self.add("d")
        saved_as = await self.backend.async_dispatch(
            "playlist_save_as", {"target": ENTRY_ID, "name": "Loud"}
        )
        self.assertEqual(saved_as["playlist"]["count"], 4)
        self.assertEqual(
            [p["name"] for p in saved_as["playlists"]], ["Loud", "Rockabilly"]
        )
        # "save as" makes the new playlist the active one.
        self.assertEqual(saved_as["loaded_playlist"], "Loud")

        deleted = await self.backend.async_dispatch(
            "playlist_delete", {"target": ENTRY_ID, "name": "Loud"}
        )
        self.assertEqual([p["name"] for p in deleted["playlists"]], ["Rockabilly"])
        # Deleting the loaded playlist clears the marker instead of leaving a
        # dangling name behind.
        self.assertIsNone(deleted["loaded_playlist"])

    async def test_playlist_save_without_a_name_or_loaded_playlist_is_rejected(self):
        await self.add("a")
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch("playlist_save", {"target": ENTRY_ID})

    async def test_playlist_save_as_needs_a_name(self):
        await self.add("a")
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch("playlist_save_as", {"target": ENTRY_ID})

    async def test_queue_mutation_through_the_contract_leaves_a_playlist_alone(self):
        await self.add("a", "b")
        await self.backend.async_dispatch(
            "playlist_create", {"target": ENTRY_ID, "name": "Rock", "from_queue": True}
        )
        await self.backend.async_dispatch(
            "queue_move", {"target": ENTRY_ID, "from_index": 0, "to_index": 1}
        )
        await self.backend.async_dispatch(
            "queue_remove", {"target": ENTRY_ID, "index": 0}
        )
        await self.add("zzz")
        await self.backend.async_dispatch("set_shuffle", {"target": ENTRY_ID, "shuffle": True})
        stored = self.backend.library(ENTRY_ID).resolve("Rock")
        self.assertEqual(stored.count, 2)
        self.assertEqual([t.title for t in stored.tracks], ["a", "b"])

    async def test_unknown_operation_is_rejected(self):
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch("queue_explode", {})
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch(None, {})

    async def test_every_advertised_operation_has_an_implementation(self):
        for operation in sorted(OPERATIONS):
            with self.subTest(operation=operation):
                self.assertTrue(
                    callable(getattr(self.backend, f"_op_{operation}", None)),
                    f"{operation} is advertised but not implemented",
                )

    async def test_device_failure_becomes_a_user_facing_message(self):
        error = RuntimeError("boom")
        error.error_code = "MODE_INACTIVE"
        self.coordinator.client.music_control = AsyncMock(side_effect=error)
        await self.add("a")
        with self.assertRaises(MusicError) as caught:
            await self.backend.async_dispatch(
                "transport", {"target": ENTRY_ID, "action": "play"}
            )
        self.assertIn("music mode", str(caught.exception))

    async def test_unexpected_device_error_is_not_masked(self):
        self.coordinator.client.music_control = AsyncMock(
            side_effect=RuntimeError("transport exploded")
        )
        await self.add("a")
        with self.assertRaises(RuntimeError):
            await self.backend.async_dispatch(
                "transport", {"target": ENTRY_ID, "action": "play"}
            )


# ---------------------------------------------------------------------------
# persistence and reload behaviour
# ---------------------------------------------------------------------------


class PersistenceTests(BackendTestCase):
    async def test_queue_and_playlists_survive_a_backend_recreation(self):
        await self.add("a", "b", "c", play=True)
        await self.backend.async_dispatch(
            "playlist_create", {"target": ENTRY_ID, "name": "Rock", "from_queue": True}
        )
        await self.backend.async_dispatch(
            "set_repeat", {"target": ENTRY_ID, "repeat": "all"}
        )

        # Home Assistant restarts: a brand new backend over the same storage.
        revived = MusicRuntime(make_hass(), store=self.store)
        await revived.async_load()
        snapshot = await revived.async_snapshot(ENTRY_ID)
        self.assertEqual(snapshot["queue"]["count"], 3)
        self.assertEqual(snapshot["queue"]["index"], 0)
        self.assertEqual(snapshot["queue"]["current_track"]["title"], "a")
        self.assertTrue(snapshot["queue"]["playing"])
        self.assertEqual(snapshot["queue"]["repeat"], "all")
        self.assertEqual([p["name"] for p in snapshot["playlists"]], ["Rock"])
        self.assertEqual(snapshot["queue"]["loaded_playlist"], "Rock")
        self.assertEqual(revived.library(ENTRY_ID).resolve("Rock").count, 3)

    async def test_a_fresh_card_reload_sees_the_same_queue(self):
        """Reloading the Lovelace page must not lose anything.

        The card holds no queue state at all, so a "reload" is exactly this: a
        brand new snapshot request against the same backend.
        """
        await self.add("a", "b")
        first = await self.backend.async_snapshot(ENTRY_ID)
        second = await self.backend.async_snapshot(ENTRY_ID)
        self.assertEqual(
            [row["track"]["title"] for row in first["queue"]["items"]],
            [row["track"]["title"] for row in second["queue"]["items"]],
        )
        self.assertEqual(first["queue"]["current_item_id"], second["queue"]["current_item_id"])

    async def test_targets_are_kept_apart(self):
        other = make_coordinator(storage=storage_fixture())
        self.hass.config_entries.async_entries = lambda domain: [
            make_entry(),
            make_entry(OTHER_ENTRY_ID, "Office CODIE"),
        ]
        self.hass.data["hase"][OTHER_ENTRY_ID] = other
        await self.add("only-kitchen")
        other_snapshot = await self.backend.async_snapshot(OTHER_ENTRY_ID)
        self.assertEqual(other_snapshot["queue"]["count"], 0)
        self.assertEqual(len(other_snapshot["targets"]), 2)
        self.assertEqual(other_snapshot["target"]["name"], "Office CODIE")

    async def test_storage_unavailable_starts_empty_instead_of_failing(self):
        class Broken:
            async def async_load(self):
                return None

            async def async_save(self, data):
                raise AssertionError("must not be reached")

        backend = MusicRuntime(make_hass(), store=Broken())
        await backend.async_load()
        self.assertEqual((await backend.async_snapshot(ENTRY_ID))["queue"]["count"], 0)


# ---------------------------------------------------------------------------
# unavailable device / capability behaviour
# ---------------------------------------------------------------------------


class AvailabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_binding_is_not_a_hase_music_target(self):
        binding_id = "hase-binding"
        binding = types.SimpleNamespace(
            target_entry_id="code27-standalone",
            data=types.SimpleNamespace(),
        )
        hass = make_hass(
            entries=[make_entry(binding_id, "HASE → CODE27"), make_entry()],
            coordinators={binding_id: binding, ENTRY_ID: make_coordinator()},
        )
        backend = MusicRuntime(hass, store=FakeStore())
        await backend.async_load()
        self.assertEqual([target["id"] for target in backend.targets()], [ENTRY_ID])
        self.assertEqual(backend.default_target(), ENTRY_ID)
        snapshot = await backend.async_snapshot(binding_id)
        self.assertFalse(snapshot["available"])
        self.assertEqual(snapshot["reason"], "device_unavailable")
        with self.assertRaises(MusicError):
            backend._coordinator(binding_id)

    async def test_no_code27_device_yields_an_explicit_empty_snapshot(self):
        backend = MusicRuntime(make_hass(entries=[], coordinators={}), store=FakeStore())
        await backend.async_load()
        snapshot = await backend.async_dispatch("snapshot", {})
        self.assertFalse(snapshot["available"])
        self.assertEqual(snapshot["reason"], "no_code27_device")
        self.assertEqual(snapshot["targets"], [])
        self.assertEqual(snapshot["queue"]["count"], 0)
        self.assertEqual(snapshot["playlists"], [])

    async def test_queue_operations_without_a_device_report_a_clear_error(self):
        backend = MusicRuntime(make_hass(entries=[], coordinators={}), store=FakeStore())
        await backend.async_load()
        with self.assertRaises(MusicError):
            await backend.async_dispatch("queue_add", {"tracks": [{"uri": "local:/x"}]})

    async def test_unloaded_device_keeps_queue_and_playlists_usable(self):
        store = FakeStore()
        hass = make_hass()
        backend = MusicRuntime(hass, store=store)
        await backend.async_load()
        await backend.async_dispatch(
            "queue_add",
            {
                "target": ENTRY_ID,
                "tracks": [{"uri": "local:/x/a.mp3", "title": "a", "provider": "code27"}],
            },
        )
        await backend.async_dispatch(
            "playlist_create", {"target": ENTRY_ID, "name": "Rock", "from_queue": True}
        )

        # The device entry is gone, but the HASE-side state is still there.
        hass.data["hase"].clear()
        snapshot = await backend.async_snapshot(ENTRY_ID)
        self.assertFalse(snapshot["available"])
        self.assertEqual(snapshot["reason"], "device_unavailable")
        self.assertEqual(snapshot["queue"]["count"], 1)
        self.assertEqual([p["name"] for p in snapshot["playlists"]], ["Rock"])
        self.assertEqual(snapshot["tree"], None)

    async def test_unknown_target_is_rejected(self):
        backend = MusicRuntime(make_hass(), store=FakeStore())
        with self.assertRaises(MusicError):
            backend._coordinator("does-not-exist")

    async def test_transport_without_a_loaded_device_is_rejected(self):
        hass = make_hass()
        backend = MusicRuntime(hass, store=FakeStore())
        await backend.async_dispatch(
            "queue_add",
            {
                "target": ENTRY_ID,
                "tracks": [{"uri": "local:/x/a.mp3", "title": "a", "provider": "code27"}],
            },
        )
        hass.data["hase"].clear()
        with self.assertRaises(MusicError):
            await backend.async_dispatch(
                "transport", {"target": ENTRY_ID, "action": "play"}
            )

    async def test_capabilities_declare_the_missing_code27_features(self):
        features = music_module.CODE27_MUSIC_FEATURES
        self.assertTrue(features["play"])
        self.assertTrue(features["pause"])
        self.assertTrue(features["volume"])
        self.assertTrue(features["browser"])
        # The device's own SEEK action (progress_ms) is real and is what the
        # media player entity already uses; only STOP stays absent.
        self.assertTrue(features["seek"])
        self.assertFalse(features["stop"])
        self.assertTrue(features["device_play_modes"])
        self.assertTrue(features["notes"])
        # No note may reintroduce the retired "the device has no seek" claim.
        self.assertFalse(
            [note for note in features["notes"] if "seek action" in note and "No device" in note]
        )

    async def test_playback_payload_reports_unavailable_without_a_snapshot(self):
        payload = music_module.playback_payload({}, {}, {})
        self.assertEqual(payload["state"], "unavailable")
        self.assertFalse(payload["available"])
        self.assertIsNone(payload["track"])

    async def test_playback_payload_is_read_from_the_code27_snapshots(self):
        payload = music_module.playback_payload(
            storage_fixture(),
            {
                "status": "PLAYING",
                "track_id": "local:/storage/x/a.mp3",
                "track_name": "a",
                "duration_ms": 200000,
                "position_ms": 5000,
                "volume_level": 30,
                "volume_max": 100,
            },
            {"source": "LOCAL_TF"},
        )
        self.assertEqual(payload["state"], "playing")
        self.assertEqual(payload["track"]["title"], "a")
        self.assertEqual(payload["duration"], 200.0)
        self.assertEqual(payload["position"], 5.0)
        self.assertAlmostEqual(payload["volume"], 0.3)
        self.assertEqual(payload["source"], "LOCAL_TF")
        self.assertTrue(payload["storage_loaded"])

    async def test_playback_title_survives_a_missing_track_id(self):
        payload = music_module.playback_payload(
            {}, {"status": "PLAYING", "track_name": "only a name"}, {}
        )
        self.assertIsNone(payload["track"])
        self.assertEqual(payload["title"], "only a name")


# ---------------------------------------------------------------------------
# playback lifecycle (autoadvance)
# ---------------------------------------------------------------------------


class TrackEndEvidenceTests(unittest.TestCase):
    def test_playing_to_idle_is_evidence(self):
        evidence = runtime.track_end_evidence(PLAYING, IDLE)
        self.assertTrue(evidence["detected"])
        self.assertEqual(evidence["reason"], "played_to_end")

    def test_no_previous_observation_is_not_evidence(self):
        self.assertEqual(
            runtime.track_end_evidence(None, IDLE)["reason"], "no_previous_state"
        )

    def test_pause_is_never_an_end(self):
        evidence = runtime.track_end_evidence(PLAYING, {"status": "PAUSED"})
        self.assertFalse(evidence["detected"])
        self.assertEqual(evidence["reason"], "paused_not_ended")

    def test_a_still_playing_snapshot_is_not_an_end(self):
        self.assertFalse(runtime.track_end_evidence(PLAYING, PLAYING)["detected"])

    def test_an_idle_device_that_never_played_is_not_an_end(self):
        self.assertEqual(
            runtime.track_end_evidence({"status": "IDLE"}, IDLE)["reason"],
            "was_not_playing",
        )

    def test_unknown_device_state_is_not_an_end(self):
        self.assertEqual(
            runtime.track_end_evidence(PLAYING, {})["reason"], "device_state_unknown"
        )

    def test_a_different_track_from_the_device_is_not_an_end(self):
        expected = Track.make(provider="code27", uri="local:/x/a.mp3")
        evidence = runtime.track_end_evidence(
            PLAYING, {"status": "IDLE", "track_id": "local:/x/other.mp3"}, expected
        )
        self.assertFalse(evidence["detected"])
        self.assertEqual(evidence["reason"], "device_reported_other_track")

    def test_the_same_track_still_counts_as_an_end(self):
        expected = Track.make(provider="code27", uri="local:/x/a.mp3")
        evidence = runtime.track_end_evidence(
            PLAYING, {"status": "IDLE", "track_id": "/x/a.mp3"}, expected
        )
        self.assertTrue(evidence["detected"])

    def test_position_is_recorded_but_does_not_veto_the_advance(self):
        # The device resets the reported position to 0 when a track completes,
        # so requiring position >= duration would disable autoadvance here.
        evidence = runtime.track_end_evidence(
            {"status": "PLAYING", "duration_ms": 200000},
            {"status": "IDLE", "position_ms": 0, "duration_ms": 200000},
        )
        self.assertTrue(evidence["detected"])
        self.assertFalse(evidence["position_at_end"])

    def test_position_at_the_end_is_reported_as_such(self):
        evidence = runtime.track_end_evidence(
            PLAYING, {"status": "IDLE", "position_ms": 199500, "duration_ms": 200000}
        )
        self.assertTrue(evidence["detected"])
        self.assertTrue(evidence["position_at_end"])

    def test_contract_states_that_there_is_no_device_event(self):
        contract = music_module.TRACK_END_CONTRACT
        self.assertFalse(contract["explicit_provider_event"])
        self.assertFalse(contract["requires_browser"])
        self.assertIn("PLAYING", contract["signal"])
        self.assertTrue(contract["gap"])


class MusicInventoryLifecycleTests(BackendTestCase):
    """The real-device sequence: the browser emptied ~1 s after populating.

    Traced cause: the WebSocket subscription push was built with
    ``include_tree=False`` but still carried ``"tree": null``, and the card
    replaces its whole snapshot on a push - so the next unrelated
    ``music_playback`` push (roughly one per second while playing) made the
    browser render "Keine Musik gefunden" even though the backend inventory was
    never empty. These tests pin the contract that made it possible.
    """

    async def test_browser_populates_from_the_device_inventory(self):
        opened = await self.backend.async_dispatch("snapshot", {"target": ENTRY_ID})
        self.assertTrue(opened["tree"]["library_ready"])
        self.assertEqual(
            [(entry["title"], entry["kind"]) for entry in opened["tree"]["entries"]],
            [("Rockkapella", "folder"), ("top.mp3", "track")],
        )

    async def test_playback_update_never_claims_an_empty_library(self):
        await self.backend.async_dispatch("snapshot", {"target": ENTRY_ID})

        # An unrelated playback update is what the subscription pushes.
        self.coordinator.data.music_playback = {
            "status": "PLAYING",
            "track_id": "local:/x/a.mp3",
            "version": 2,
        }
        push = await self.backend.async_snapshot(ENTRY_ID, include_tree=False)
        self.assertEqual(push["playback"]["state"], "playing")
        # Absent means "unchanged" - it must never mean "the library is empty".
        self.assertNotIn("tree", push)
        self.assertTrue(push["library"]["ready"])
        self.assertEqual(push["library"]["file_count"], 3)

    async def test_the_inventory_survives_many_playback_updates(self):
        for version in range(1, 6):
            self.coordinator.data.music_playback = {"status": "PLAYING", "version": version}
            await self.backend.async_note_playback(
                ENTRY_ID, self.coordinator.data.music_playback
            )
        tree = await self.backend.async_dispatch("browse", {"target": ENTRY_ID})
        self.assertTrue(tree["library_ready"])
        self.assertEqual(len(tree["entries"]), 2)

    async def test_library_revision_tracks_the_inventory(self):
        first = await self.backend.async_snapshot(ENTRY_ID, include_tree=False)
        same = await self.backend.async_snapshot(ENTRY_ID, include_tree=False)
        self.assertEqual(first["library"]["revision"], same["library"]["revision"])

        storage = storage_fixture()
        storage["version"] = 99
        storage["music_files"].append(
            {
                "name": "new.mp3",
                "path": "/storage/142E2DF92E2DD4900/code27/music/new.mp3",
                "duration_ms": 1000,
            }
        )
        self.coordinator.data.music_storage = storage
        changed = await self.backend.async_snapshot(ENTRY_ID, include_tree=False)
        self.assertNotEqual(first["library"]["revision"], changed["library"]["revision"])
        self.assertEqual(changed["library"]["file_count"], 4)

    async def test_a_newer_inventory_replaces_the_previous_one(self):
        before = await self.backend.async_dispatch("browse", {"target": ENTRY_ID})
        self.assertEqual(len(before["entries"]), 2)

        self.coordinator.data.music_storage = {
            "is_loaded": True,
            "root_path": "/storage/142E2DF92E2DD4900",
            "version": 12,
            "music_files": [
                {
                    "name": "only.mp3",
                    "path": "/storage/142E2DF92E2DD4900/code27/music/only.mp3",
                    "duration_ms": 1000,
                }
            ],
        }
        after = await self.backend.async_dispatch("browse", {"target": ENTRY_ID})
        self.assertEqual([entry["title"] for entry in after["entries"]], ["only.mp3"])

    async def test_explicitly_empty_but_loaded_inventory_is_respected(self):
        self.coordinator.data.music_storage = {
            "is_loaded": True,
            "root_path": "/storage/142E2DF92E2DD4900",
            "version": 13,
            "music_files": [],
        }
        tree = await self.backend.async_dispatch("browse", {"target": ENTRY_ID})
        self.assertFalse(tree["library_ready"])
        self.assertEqual(tree["entries"], [])
        snapshot = await self.backend.async_snapshot(ENTRY_ID, include_tree=False)
        self.assertFalse(snapshot["library"]["ready"])

    async def test_rest_inventory_before_any_push_fills_the_browser(self):
        """Restart case: the poll's REST read alone must be enough.

        No ``music_storage`` WebSocket push has ever happened here - the only
        inventory present is the one the coordinator's poll read. This is what
        makes the browser work immediately after a Home Assistant restart
        instead of waiting up to ~50 s for the device's next push.
        """
        coordinator = make_coordinator(storage=storage_fixture())
        backend = MusicRuntime(
            make_hass(coordinators={ENTRY_ID: coordinator}), store=FakeStore()
        )
        await backend.async_load()

        snapshot = await backend.async_snapshot(ENTRY_ID, include_tree=False)
        self.assertTrue(snapshot["library"]["ready"])
        self.assertEqual(snapshot["library"]["file_count"], 3)

        tree = await backend.async_dispatch("browse", {"target": ENTRY_ID})
        self.assertTrue(tree["library_ready"])
        self.assertEqual(
            [(entry["title"], entry["kind"]) for entry in tree["entries"]],
            [("Rockkapella", "folder"), ("top.mp3", "track")],
        )

    def test_usable_inventory_detection(self):
        self.assertTrue(music_module.usable_music_storage(storage_fixture()))
        self.assertTrue(
            music_module.usable_music_storage({"is_loaded": True, "music_files": []})
        )
        self.assertTrue(
            music_module.usable_music_storage({"is_loaded": False, "music_files": []})
        )
        for unusable in ({}, None, "nope", {"is_loaded": True}, {"music_files": "nope"}):
            with self.subTest(unusable=unusable):
                self.assertFalse(music_module.usable_music_storage(unusable))


class MusicInventoryMergeTests(unittest.TestCase):
    """The poll/WS precedence rule, tested without a coordinator."""

    def setUp(self):
        self.inventory = storage_fixture()
        self.inventory["version"] = 5

    def test_a_poll_without_inventory_preserves_the_last_valid_one(self):
        merged = music_module.merge_music_inventory(
            polled={}, previous=self.inventory, push_arrived=False
        )
        self.assertIs(merged, self.inventory)

    def test_a_poll_with_an_unusable_inventory_preserves_the_last_valid_one(self):
        for unusable in (None, {}, {"is_loaded": True}, {"music_files": "nope"}):
            with self.subTest(polled=unusable):
                merged = music_module.merge_music_inventory(
                    polled=unusable, previous=self.inventory, push_arrived=False
                )
                self.assertIs(merged, self.inventory)

    def test_a_push_that_raced_the_poll_wins(self):
        pushed = {"is_loaded": True, "music_files": [{"path": "pushed.mp3"}], "version": 6}
        merged = music_module.merge_music_inventory(
            polled=self.inventory, previous=pushed, push_arrived=True
        )
        self.assertIs(merged, pushed)

    def test_a_newer_polled_inventory_is_accepted(self):
        newer = {"is_loaded": True, "music_files": [{"path": "rest.mp3"}], "version": 7}
        merged = music_module.merge_music_inventory(
            polled=newer, previous=self.inventory, push_arrived=False
        )
        self.assertIs(merged, newer)

    def test_an_older_polled_inventory_is_rejected(self):
        older = {"is_loaded": True, "music_files": [{"path": "old.mp3"}], "version": 4}
        merged = music_module.merge_music_inventory(
            polled=older, previous=self.inventory, push_arrived=False
        )
        self.assertIs(merged, self.inventory)

    def test_a_polled_inventory_is_accepted_when_nothing_is_known_yet(self):
        merged = music_module.merge_music_inventory(
            polled=self.inventory, previous={}, push_arrived=False
        )
        self.assertIs(merged, self.inventory)

    def test_a_garbage_previous_value_never_wins(self):
        merged = music_module.merge_music_inventory(
            polled=self.inventory, previous=None, push_arrived=False
        )
        self.assertIs(merged, self.inventory)


class AutoadvanceTests(BackendTestCase):
    async def test_track_end_plays_the_next_queue_row_without_a_browser(self):
        await self.add("a", "b", "c", play=True)
        self.control.reset_mock()
        result = await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        self.assertFalse(result["advanced"])
        result = await self.backend.async_note_playback(ENTRY_ID, self.idle("a"))
        self.assertTrue(result["advanced"])
        self.assertEqual(result["evidence"]["reason"], "played_to_end")
        _args, kwargs = self.control.await_args
        self.assertEqual(kwargs["track_id"], self.track("b").uri)
        snapshot = await self.backend.async_snapshot(ENTRY_ID)
        self.assertEqual(snapshot["queue"]["index"], 1)
        self.assertEqual(snapshot["queue"]["current_track"]["title"], "b")

    async def test_repeat_one_replays_the_same_row(self):
        await self.add("a", "b", play=True)
        await self.backend.async_dispatch(
            "set_repeat", {"target": ENTRY_ID, "repeat": "one"}
        )
        self.control.reset_mock()
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        result = await self.backend.async_note_playback(ENTRY_ID, self.idle("a"))
        self.assertTrue(result["advanced"])
        _args, kwargs = self.control.await_args
        self.assertEqual(kwargs["track_id"], self.track("a").uri)

    async def test_repeat_all_wraps_from_the_last_row(self):
        await self.add("a", "b", play=True)
        await self.backend.async_dispatch(
            "set_repeat", {"target": ENTRY_ID, "repeat": "all"}
        )
        await self.backend.async_dispatch(
            "queue_play", {"target": ENTRY_ID, "index": 1}
        )
        self.control.reset_mock()
        await self.backend.async_note_playback(ENTRY_ID, self.playing("b"))
        result = await self.backend.async_note_playback(ENTRY_ID, self.idle("b"))
        self.assertTrue(result["advanced"])
        _args, kwargs = self.control.await_args
        self.assertEqual(kwargs["track_id"], self.track("a").uri)

    async def test_repeat_off_stops_at_the_end_and_keeps_the_last_row(self):
        await self.add("a", play=True)
        self.control.reset_mock()
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        result = await self.backend.async_note_playback(ENTRY_ID, self.idle("a"))
        self.assertFalse(result["advanced"])
        self.control.assert_not_awaited()
        snapshot = await self.backend.async_snapshot(ENTRY_ID)
        self.assertFalse(snapshot["queue"]["playing"])
        self.assertEqual(snapshot["queue"]["index"], 0)

    async def test_a_stopped_queue_never_autoadvances(self):
        await self.add("a", "b", play=True)
        await self.backend.async_dispatch("transport", {"target": ENTRY_ID, "action": "stop"})
        self.control.reset_mock()
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        result = await self.backend.async_note_playback(ENTRY_ID, self.idle("a"))
        self.assertFalse(result["advanced"])
        self.control.assert_not_awaited()

    async def test_a_device_side_advance_is_followed_not_duplicated(self):
        await self.add("a", "b", play=True)
        self.control.reset_mock()
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        result = await self.backend.async_note_playback(
            ENTRY_ID, {"status": "IDLE", "track_id": self.track("b").uri}
        )
        self.assertFalse(result["advanced"])
        self.assertTrue(result["resynced"])
        self.assertEqual(result["evidence"]["reason"], "device_reported_other_track")
        snapshot = await self.backend.async_snapshot(ENTRY_ID)
        self.assertEqual(snapshot["queue"]["current_track"]["title"], "b")
        self.control.assert_not_awaited()

    async def test_a_failed_autoadvance_stops_the_queue_instead_of_looping(self):
        await self.add("a", "b", play=True)
        self.control.reset_mock()
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        error = RuntimeError("device busy")
        error.error_code = "MODE_INACTIVE"
        self.control.side_effect = error
        result = await self.backend.async_note_playback(ENTRY_ID, self.idle("a"))
        self.assertFalse(result["advanced"])
        snapshot = await self.backend.async_snapshot(ENTRY_ID)
        self.assertFalse(snapshot["queue"]["playing"])

    async def test_autoadvance_state_is_persisted(self):
        await self.add("a", "b", play=True)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        await self.backend.async_note_playback(ENTRY_ID, self.idle("a"))
        revived = MusicRuntime(make_hass(), store=self.store)
        await revived.async_load()
        self.assertEqual(revived.queue(ENTRY_ID).current_track.title, "b")

    async def test_diagnostics_expose_the_last_evidence(self):
        await self.add("a", "b", play=True)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        await self.backend.async_note_playback(ENTRY_ID, self.idle("a"))
        snapshot = await self.backend.async_snapshot(ENTRY_ID)
        self.assertEqual(
            snapshot["diagnostics"]["last_track_end"]["reason"], "played_to_end"
        )
        self.assertEqual(snapshot["playback"]["track_end"]["detected"], True)
        self.assertFalse(snapshot["diagnostics"]["position_reported"])
        self.assertFalse(snapshot["diagnostics"]["duration_reported"])
        self.assertIn("playback_fields", snapshot["diagnostics"])

    async def test_single_loop_position_reset_triggers_advance(self):
        """M3: single_loop never fires PLAYING→IDLE; position reset is the signal."""
        await self.add("a", "b", play=True)
        self.control.reset_mock()
        # Device approaches the known 60-second track end
        playing_mid = {**self.playing("a"), "play_mode": "single_loop", "position_ms": 59_900}
        await self.backend.async_note_playback(ENTRY_ID, playing_mid)
        self.assertFalse(self.control.called)
        # Device loops: same PLAYING status but position resets to near zero
        playing_reset = {**self.playing("a"), "play_mode": "single_loop", "position_ms": 200}
        result = await self.backend.async_note_playback(ENTRY_ID, playing_reset)
        self.assertTrue(result["advanced"])
        self.assertEqual(result["evidence"]["reason"], "single_loop_position_reset")

    async def test_single_loop_with_no_prior_position_does_not_advance(self):
        """M3: position reset requires a known previous position > 3s to fire."""
        await self.add("a", "b", play=True)
        self.control.reset_mock()
        # First observation: no position_ms yet — cannot determine reset
        playing_no_pos = {**self.playing("a"), "play_mode": "single_loop"}
        result = await self.backend.async_note_playback(ENTRY_ID, playing_no_pos)
        self.assertFalse(result["advanced"])

    async def test_play_mode_appears_in_diagnostics(self):
        """M3: device_play_mode is surfaced in the snapshot diagnostics."""
        await self.add("a", play=True)
        playback = {**self.playing("a"), "play_mode": "list_loop"}
        # diagnostics reads from coordinator.data.music_playback, not _last_playback
        self.coordinator.data.music_playback = playback
        await self.backend.async_note_playback(ENTRY_ID, playback)
        snapshot = await self.backend.async_snapshot(ENTRY_ID)
        self.assertEqual(snapshot["diagnostics"]["device_play_mode"], "list_loop")


# ---------------------------------------------------------------------------
# the device continuing in folder order (real-device regression)
# ---------------------------------------------------------------------------


class DeviceSteeringTests(BackendTestCase):
    """CODE27 continues with the next *file in the folder*, not the next queue row.

    On the real device the status stays ``PLAYING`` while the track changes, so
    the ``PLAYING -> IDLE`` rule never sees a track end and the queue silently
    stopped steering. These tests pin the correction - and the safety rule that
    a device which never confirms a row is never fought over.
    """

    async def test_device_folder_playback_is_taken_back_to_the_queue(self):
        await self.add("a", "b", "c", play=True)
        # The device confirms the row HASE started ...
        confirmed = await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        self.assertFalse(confirmed["took_over"])
        self.control.reset_mock()

        # ... then continues with a file that is not in the queue at all.
        result = await self.backend.async_note_playback(
            ENTRY_ID, self.playing("outside-the-queue")
        )
        self.assertTrue(result["took_over"])
        self.assertEqual(result["reason"], "device_left_the_queue")
        _args, kwargs = self.control.await_args
        self.assertEqual(kwargs["track_id"], self.track("b").uri)
        snapshot = await self.backend.async_snapshot(ENTRY_ID)
        self.assertEqual(snapshot["queue"]["current_track"]["title"], "b")
        self.assertTrue(snapshot["queue"]["playing"])

    async def test_device_playing_a_later_queue_row_is_put_back_in_order(self):
        # The device may well land on *a* queue row - just not the next one.
        # Adopting it would silently skip a row, so the queue's own order wins.
        await self.add("a", "b", "c", play=True)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        self.control.reset_mock()
        result = await self.backend.async_note_playback(ENTRY_ID, self.playing("c"))
        self.assertTrue(result["took_over"])
        _args, kwargs = self.control.await_args
        self.assertEqual(kwargs["track_id"], self.track("b").uri)

    async def test_device_playing_the_next_queue_row_is_adopted(self):
        # Here the device happens to be exactly where the queue wanted to go, so
        # no PLAY is sent (that would restart the track from zero).
        await self.add("a", "b", play=True)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        self.control.reset_mock()
        result = await self.backend.async_note_playback(ENTRY_ID, self.playing("b"))
        self.assertFalse(result["took_over"])
        self.assertTrue(result["resynced"])
        self.assertEqual(result["reason"], "device_played_the_next_queue_row")
        self.control.assert_not_awaited()
        snapshot = await self.backend.async_snapshot(ENTRY_ID)
        self.assertEqual(snapshot["queue"]["current_track"]["title"], "b")

    async def test_a_device_that_never_confirms_the_row_is_not_fought_over(self):
        # Safety property: if the device's reported track id never matches what
        # it was asked to play, the runtime cannot tell what the device is doing and
        # must not take playback over on every single update.
        await self.add("a", "b", "c", play=True)
        await self.backend.async_note_playback(
            ENTRY_ID, {"status": "PLAYING", "track_id": "unrelated-1", "version": 1}
        )
        self.control.reset_mock()
        for index in range(2, 6):
            result = await self.backend.async_note_playback(
                ENTRY_ID,
                {
                    "status": "PLAYING",
                    "track_id": f"unrelated-{index}",
                    "version": index,
                },
            )
            self.assertFalse(result["took_over"])
        self.control.assert_not_awaited()

    async def test_the_correction_happens_once_per_device_change(self):
        await self.add("a", "b", "c", play=True)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        self.control.reset_mock()
        await self.backend.async_note_playback(ENTRY_ID, self.playing("outside"))
        # The same observation repeated (every poll re-sends it) must not
        # trigger another takeover.
        result = await self.backend.async_note_playback(
            ENTRY_ID, self.playing("outside")
        )
        self.assertFalse(result["took_over"])
        self.assertEqual(self.control.await_count, 1)

    async def test_a_stopped_queue_never_takes_playback_back(self):
        await self.add("a", "b", play=True)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        await self.backend.async_dispatch(
            "transport", {"target": ENTRY_ID, "action": "stop"}
        )
        self.control.reset_mock()
        result = await self.backend.async_note_playback(
            ENTRY_ID, self.playing("outside")
        )
        self.assertFalse(result["took_over"])
        self.control.assert_not_awaited()

    async def test_taking_over_at_the_end_of_the_queue_ends_the_intent(self):
        await self.add("a", play=True)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        result = await self.backend.async_note_playback(
            ENTRY_ID, self.playing("outside")
        )
        self.assertFalse(result["took_over"])
        self.assertEqual(result["reason"], "queue_finished")
        snapshot = await self.backend.async_snapshot(ENTRY_ID)
        self.assertFalse(snapshot["queue"]["playing"])

    async def test_a_failed_takeover_stops_the_queue(self):
        await self.add("a", "b", play=True)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        error = RuntimeError("device busy")
        error.error_code = "MODE_INACTIVE"
        self.control.side_effect = error
        result = await self.backend.async_note_playback(
            ENTRY_ID, self.playing("outside")
        )
        self.assertFalse(result["took_over"])
        self.assertEqual(result["reason"], "play_failed")
        snapshot = await self.backend.async_snapshot(ENTRY_ID)
        self.assertFalse(snapshot["queue"]["playing"])

    async def test_played_rows_are_marked_in_the_payload_and_kept(self):
        await self.add("a", "b", "c", play=True)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        await self.backend.async_note_playback(ENTRY_ID, self.idle("a"))
        snapshot = await self.backend.async_snapshot(ENTRY_ID)
        rows = snapshot["queue"]["items"]
        self.assertEqual([row["track"]["title"] for row in rows], ["a", "b", "c"])
        self.assertTrue(rows[0]["played"])
        self.assertFalse(rows[1]["played"])
        self.assertTrue(rows[1]["current"])

    async def test_the_restart_action_replays_the_current_row(self):
        await self.add("a", "b", play=True)
        await self.backend.async_dispatch(
            "queue_play", {"target": ENTRY_ID, "index": 1}
        )
        self.control.reset_mock()
        result = await self.backend.async_dispatch(
            "transport", {"target": ENTRY_ID, "action": "restart"}
        )
        self.assertEqual(result["action"], "restart")
        _args, kwargs = self.control.await_args
        self.assertEqual(kwargs["track_id"], self.track("b").uri)
        # Restarting is not a queue move.
        self.assertEqual(result["index"], 1)

    async def test_the_restart_action_starts_the_queue_when_nothing_is_current(self):
        await self.add("a", "b")
        await self.backend.async_dispatch(
            "transport", {"target": ENTRY_ID, "action": "restart"}
        )
        _args, kwargs = self.control.await_args
        self.assertEqual(kwargs["track_id"], self.track("a").uri)

    async def test_the_seek_action_uses_the_device_seek_with_progress_ms(self):
        await self.add("a", "b", play=True)
        await self.backend.async_dispatch(
            "queue_play", {"target": ENTRY_ID, "index": 1}
        )
        # The device reports the row it is playing - that reported track_id is
        # what a seek names.
        self.coordinator.data.music_playback.update(self.playing("b"))
        self.control.reset_mock()
        self.coordinator.async_request_refresh.reset_mock()

        result = await self.backend.async_dispatch(
            "transport", {"target": ENTRY_ID, "action": "seek", "position": 61.5}
        )

        self.assertEqual(result["action"], "seek")
        args, kwargs = self.control.await_args
        # The same device action the media player entity's async_media_seek
        # issues - reached through the runtime's existing _control, so there is no
        # second seek path and no second API call.
        self.assertEqual(args[0], "SEEK")
        self.assertEqual(kwargs, {"progress_ms": 61500, "track_id": self.track("b").uri})
        self.coordinator.async_request_refresh.assert_awaited()
        # Seeking moves inside the current row: unlike next/previous it is not a
        # queue move, and it must not re-issue PLAY (which would restart it).
        self.assertEqual(result["index"], 1)

    async def test_the_seek_action_omits_track_id_without_a_device_track(self):
        # A device playing something HASE knows nothing about still accepts a
        # seek; the field is left out rather than sent as null, exactly like the
        # device's own client does.
        self.control.reset_mock()
        await self.backend.async_dispatch(
            "transport", {"target": ENTRY_ID, "action": "seek", "position": 10}
        )
        _args, kwargs = self.control.await_args
        self.assertEqual(kwargs, {"progress_ms": 10000})

    async def test_the_seek_action_clamps_a_negative_position(self):
        await self.add("a", play=True)
        self.control.reset_mock()
        await self.backend.async_dispatch(
            "transport", {"target": ENTRY_ID, "action": "seek", "position": -3}
        )
        _args, kwargs = self.control.await_args
        self.assertEqual(kwargs["progress_ms"], 0)

    async def test_the_seek_action_rejects_an_unusable_position(self):
        await self.add("a", play=True)
        for payload in ({}, {"position": None}, {"position": "fast"}, {"position": True}):
            with self.subTest(payload=payload):
                self.control.reset_mock()
                with self.assertRaises(MusicError):
                    await self.backend.async_dispatch(
                        "transport",
                        {"target": ENTRY_ID, "action": "seek", **payload},
                    )
                self.control.assert_not_awaited()


class ConsumePlayedTests(BackendTestCase):
    """The switch between "playlist that keeps everything" and "up next list"."""

    async def set_consume(self, consume):
        return await self.backend.async_dispatch(
            "set_consume_played", {"target": ENTRY_ID, "consume": consume}
        )

    async def test_the_switch_is_off_by_default_and_reported(self):
        payload = await self.backend.async_dispatch("snapshot", {"target": ENTRY_ID})
        self.assertFalse(payload["queue"]["consume_played"])

    async def test_enabling_it_clears_already_played_rows_immediately(self):
        await self.add("a", "b", "c", play=True)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        await self.backend.async_note_playback(ENTRY_ID, self.idle("a"))
        payload = await self.set_consume(True)
        self.assertTrue(payload["consume_played"])
        self.assertEqual([row["track"]["title"] for row in payload["items"]], ["b", "c"])
        self.assertEqual(payload["current_track"]["title"], "b")

    async def test_played_rows_leave_the_queue_as_playback_moves_on(self):
        await self.add("a", "b", "c", play=True)
        await self.set_consume(True)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        await self.backend.async_note_playback(ENTRY_ID, self.idle("a"))
        payload = await self.backend.async_dispatch("snapshot", {"target": ENTRY_ID})
        self.assertEqual([row["track"]["title"] for row in payload["queue"]["items"]], ["b", "c"])
        self.assertEqual(payload["queue"]["current_track"]["title"], "b")
        self.assertEqual(payload["queue"]["index"], 0)
        self.assertTrue(payload["queue"]["playing"])

    async def test_it_also_applies_when_the_device_is_corrected(self):
        await self.add("a", "b", "c", play=True)
        await self.set_consume(True)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        await self.backend.async_note_playback(ENTRY_ID, self.playing("outside"))
        payload = await self.backend.async_dispatch("snapshot", {"target": ENTRY_ID})
        self.assertEqual([row["track"]["title"] for row in payload["queue"]["items"]], ["b", "c"])

    async def test_a_manual_row_choice_never_deletes_rows(self):
        # Only the automatic paths consume; jumping to a row by hand must keep
        # the rows the user may still want to go back to.
        await self.add("a", "b", "c", play=True)
        await self.set_consume(True)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        await self.backend.async_note_playback(ENTRY_ID, self.idle("a"))
        await self.backend.async_dispatch("queue_play", {"target": ENTRY_ID, "index": 1})
        payload = await self.backend.async_dispatch("snapshot", {"target": ENTRY_ID})
        self.assertEqual([row["track"]["title"] for row in payload["queue"]["items"]], ["b", "c"])

    async def test_the_switch_persists(self):
        await self.add("a", "b")
        await self.set_consume(True)
        revived = MusicRuntime(make_hass(), store=self.store)
        await revived.async_load()
        self.assertTrue(revived.queue(ENTRY_ID).consume_played)

    async def test_turning_it_off_again_keeps_the_remaining_rows(self):
        await self.add("a", "b", "c", play=True)
        await self.set_consume(True)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        await self.backend.async_note_playback(ENTRY_ID, self.idle("a"))
        payload = await self.set_consume(False)
        self.assertFalse(payload["consume_played"])
        self.assertEqual([row["track"]["title"] for row in payload["items"]], ["b", "c"])


class PlaybackProgressTests(BackendTestCase):
    """The progress bar's duration, which the device does not always report."""

    async def test_the_duration_falls_back_to_the_music_inventory(self):
        # The live TF inventory reports a duration per file; `music_playback`
        # does not always do so, which left the progress bar stuck at zero while
        # the elapsed time kept counting.
        self.coordinator.data.music_playback = {
            "status": "PLAYING",
            "track_id": "local:/storage/142E2DF92E2DD4900/code27/music/top.mp3",
            "track_name": "top.mp3",
            "position_ms": 5000,
        }
        snapshot = await self.backend.async_snapshot(ENTRY_ID)
        playback = snapshot["playback"]
        self.assertEqual(playback["duration"], 1.0)  # the fixture file's length
        self.assertEqual(playback["duration_source"], "music_inventory")
        self.assertEqual(playback["position"], 5.0)
        self.assertFalse(snapshot["diagnostics"]["duration_reported"])
        self.assertTrue(snapshot["diagnostics"]["position_reported"])

    async def test_a_device_reported_duration_wins(self):
        self.coordinator.data.music_playback = {
            "status": "PLAYING",
            "track_id": "local:/storage/142E2DF92E2DD4900/code27/music/top.mp3",
            "duration_ms": 42000,
            "position_ms": 1000,
        }
        snapshot = await self.backend.async_snapshot(ENTRY_ID)
        self.assertEqual(snapshot["playback"]["duration"], 42.0)
        self.assertEqual(snapshot["playback"]["duration_source"], "device")

    async def test_no_duration_anywhere_stays_unknown(self):
        self.coordinator.data.music_playback = {
            "status": "PLAYING",
            "track_id": "local:/storage/unknown/nothing.mp3",
            "position_ms": 1000,
        }
        snapshot = await self.backend.async_snapshot(ENTRY_ID)
        self.assertIsNone(snapshot["playback"]["duration"])
        self.assertNotIn("duration_source", snapshot["playback"])

    async def test_the_queue_row_supplies_the_duration_without_a_track_id(self):
        await self.add("top", play=True)
        self.coordinator.data.music_playback = {"status": "PLAYING", "position_ms": 2000}
        snapshot = await self.backend.async_snapshot(ENTRY_ID)
        self.assertEqual(snapshot["playback"]["duration_source"], "queue_row")
        self.assertEqual(snapshot["playback"]["duration_track"], self.track("top").uri)
        self.assertEqual(snapshot["playback"]["duration"], 60.0)


# ---------------------------------------------------------------------------
# playlist overwrite semantics
# ---------------------------------------------------------------------------


class PlaylistSaveTests(BackendTestCase):
    async def test_saving_a_new_name_creates_it(self):
        await self.add("a", "b")
        result = await self.backend.async_dispatch(
            "playlist_save", {"target": ENTRY_ID, "name": "Fresh"}
        )
        self.assertEqual(result["playlist"]["name"], "Fresh")
        self.assertEqual(result["playlist"]["count"], 2)

    async def test_saving_an_existing_name_overwrites_it(self):
        await self.add("a")
        await self.backend.async_dispatch(
            "playlist_save", {"target": ENTRY_ID, "name": "Keep"}
        )
        await self.add("b")
        result = await self.backend.async_dispatch(
            "playlist_save", {"target": ENTRY_ID, "name": "Keep"}
        )
        self.assertEqual(result["playlist"]["count"], 2)
        self.assertEqual([p["name"] for p in result["playlists"]], ["Keep"])

    async def test_save_as_still_refuses_an_existing_name(self):
        # The card asks the user and then sends playlist_save; the contract
        # itself must never silently replace a playlist.
        await self.add("a")
        await self.backend.async_dispatch(
            "playlist_save_as", {"target": ENTRY_ID, "name": "Keep"}
        )
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch(
                "playlist_save_as", {"target": ENTRY_ID, "name": "Keep"}
            )


# ---------------------------------------------------------------------------
# frontend / backend contract
# ---------------------------------------------------------------------------


def _card_source():
    return CARD.read_text(encoding="utf-8")


#: The card's frontend provider boundary - the one region where a provider's
#: namespace and payload quirks are allowed to be known (see the card's own
#: comment above the table).
_BOUNDARY_START = "frontend provider boundary (start)"
_BOUNDARY_END = "frontend provider boundary (end)"


def _card_code(source):
    """The card source with comments removed.

    The neutrality contract is about code, not prose: a comment explaining that
    this card is *not* CODE27's card must not be mistaken for device logic. The
    scanner is quote-aware, so comment markers inside the card's CSS/HTML
    template literals are only stripped where they really are comments.
    """
    out = []
    index = 0
    length = len(source)
    quote = None
    while index < length:
        char = source[index]
        following = source[index + 1] if index + 1 < length else ""
        if quote:
            out.append(char)
            if char == "\\":
                if following:
                    out.append(following)
                    index += 2
                    continue
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in "\"'`":
            quote = char
            out.append(char)
            index += 1
            continue
        if char == "/" and following == "/":
            while index < length and source[index] != "\n":
                index += 1
            continue
        if char == "/" and following == "*":
            index += 2
            while index < length and not (
                source[index] == "*" and index + 1 < length and source[index + 1] == "/"
            ):
                index += 1
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _provider_boundary_code(source):
    """``(boundary code, everything else)`` - both with comments stripped.

    The markers are themselves comments, so the split happens on the raw source
    by whole lines: that keeps every remaining comment balanced for the
    comment-aware stripper.
    """
    lines = source.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if _BOUNDARY_START in line)
    end = next(i for i, line in enumerate(lines) if _BOUNDARY_END in line)
    if end <= start:
        raise AssertionError("the provider boundary markers are out of order")
    boundary = "".join(lines[start : end + 1])
    rest = "".join(lines[:start] + lines[end + 1 :])
    return _card_code(boundary), _card_code(rest)


def _music_api_constants():
    """Read the WebSocket namespace out of music_api.py without importing it."""
    tree = ast.parse((PROVIDER / "music_api.py").read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if isinstance(node.value, ast.Constant):
                found[target.id] = node.value.value
            elif isinstance(node.value, ast.Name) and node.value.id in found:
                # e.g. COMMAND_TYPE = NAMESPACE
                found[target.id] = found[node.value.id]
            elif isinstance(node.value, ast.JoinedStr):
                parts = []
                for value in node.value.values:
                    if isinstance(value, ast.Constant):
                        parts.append(str(value.value))
                    elif isinstance(value, ast.FormattedValue) and isinstance(
                        value.value, ast.Name
                    ) and value.value.id in found:
                        parts.append(str(found[value.value.id]))
                    else:
                        parts = None
                        break
                if parts is not None:
                    found[target.id] = "".join(parts)
    return found


class FrontendContractTests(unittest.TestCase):
    """The card's action/dispatch contract against the backend's operations.

    The card's own tables are parsed out of the source instead of being
    duplicated here: a test that re-states the mapping can only check its own
    copy, which is exactly how an invalid `playlist-pick` operation slipped
    through (it was listed as "local" without anything verifying that the card
    actually handled it locally). The mechanism is asserted too - the
    dispatcher must refuse anything that is not in the table.
    """

    @staticmethod
    def _action_operations() -> dict[str, str]:
        """``data-act`` -> backend operation, as declared by the card."""
        source = _card_source()
        block = source[
            source.index("const ACTION_OPERATIONS = {") : source.index(
                "const UI_ONLY_ACTIONS"
            )
        ]
        return {
            match.group(1): match.group(2)
            for match in re.finditer(r"^\s{2}([a-z_]+):\s*\"([a-z_]+)\",", block, re.MULTILINE)
        }

    @staticmethod
    def _ui_only_actions() -> set[str]:
        """Card-only interactions that must never reach the backend."""
        source = _card_source()
        block = source[
            source.index("const UI_ONLY_ACTIONS = new Set([") : source.index(
                "const FORM_CONTROL_TAGS"
            )
        ]
        return set(re.findall(r'"([a-z_-]+)"', block))

    @staticmethod
    def _rendered_actions() -> set[str]:
        return set(re.findall(r'data-act="([a-z_-]+)"', _card_source()))

    def test_the_card_declares_its_action_tables(self):
        self.assertTrue(self._action_operations())
        self.assertTrue(self._ui_only_actions())
        self.assertFalse(
            set(self._action_operations()) & self._ui_only_actions(),
            "an action must not be both a backend operation and UI-only",
        )

    def test_every_rendered_action_is_classified(self):
        operations = self._action_operations()
        ui_only = self._ui_only_actions()
        actions = self._rendered_actions()
        self.assertGreater(len(actions), 10)
        for action in sorted(actions):
            with self.subTest(action=action):
                self.assertTrue(
                    action in operations or action in ui_only,
                    f'data-act="{action}" is neither a backend operation nor UI-only',
                )

    def test_every_card_operation_exists_in_the_backend_contract(self):
        for action, operation in sorted(self._action_operations().items()):
            with self.subTest(action=action):
                self.assertIn(operation, OPERATIONS)

    def test_ui_only_actions_have_no_payload_mapping(self):
        # A UI-only action is never dispatched, so a `_payloadFor` case for one
        # would be a sign it is about to be sent after all. (Its *local*
        # handling in `_onClick` uses `case` labels in a different switch, which
        # is why this looks only at `_payloadFor`.)
        payload_map = self._payload_for_body()
        for action in sorted(self._ui_only_actions()):
            with self.subTest(action=action):
                self.assertNotIn(f'case "{action}":', payload_map)

    def test_every_card_operation_has_a_payload_mapping(self):
        payload_map = self._payload_for_body()
        for action in sorted(self._action_operations()):
            with self.subTest(action=action):
                self.assertIn(f'case "{action}":', payload_map)

    @staticmethod
    def _payload_for_body() -> str:
        source = _card_source()
        start = source.index("_payloadFor(act, data) {")
        return source[start : source.index("async _onClick(event) {", start)]

    def test_the_dispatcher_refuses_non_operations(self):
        source = _card_source()
        self.assertIn("const operation = ACTION_OPERATIONS[act];", source)
        self.assertIn("if (!operation || UI_ONLY_ACTIONS.has(act)) {", source)
        # And a mapping is mandatory, so a new table entry cannot silently send
        # an unmapped payload.
        self.assertIn("no payload mapping for action", source)

    def test_click_handling_ignores_form_controls(self):
        source = _card_source()
        self.assertIn("FORM_CONTROL_TAGS.has(trigger.tagName)", source)
        self.assertIn(
            'const FORM_CONTROL_TAGS = new Set(["INPUT", "SELECT", "TEXTAREA", "OPTION"]);',
            source,
        )

    def test_no_action_is_assigned_dynamically(self):
        # The seek bar used to get `dataset.act = "seek"` at render time, which
        # would have leaked an operation the moment a provider reported seek.
        source = _card_source()
        self.assertIsNone(
            re.search(r"dataset\.act\s*=(?!=)", source),
            "an action is assigned at render time; it must be declared statically",
        )
        # Neither is it taken away at render time: whether the bar can be aimed
        # at depends on the length the device reports *now*, and that answer can
        # change (the backend fills a missing length in later), so the click
        # handler decides per click.
        self.assertIn("_seekPosition(trigger, event, snapshot.playback)", source)

    def test_the_card_seeks_through_the_transport_operation(self):
        source = _card_source()
        # The progress bar declares the existing `transport` operation with
        # `action: "seek"` - no new operation name, so the WS contract and the
        # action classification stay unchanged.
        self.assertIn('data-act="transport" data-action="seek"', source)
        # The click position becomes a target position in seconds, which is the
        # unit the rest of the WS contract speaks.
        self.assertIn('this._act("transport", { action: "seek", position });', source)
        self.assertIn('{ action: "seek", position: numeric(data.position) }', source)
        # The ↺ button keeps the plain restart, and is not the bar's action.
        self.assertIn('data-act="restart"', source)

    def test_every_backend_operation_is_reachable_from_the_card(self):
        source = _card_source()
        reachable = set(self._action_operations().values())
        reachable |= set(re.findall(r'this\._call\("([a-z_]+)"', source))
        for operation in sorted(OPERATIONS):
            with self.subTest(operation=operation):
                self.assertIn(operation, reachable)

    def test_card_reaches_the_provider_through_its_transport_table(self):
        constants = _music_api_constants()
        self.assertEqual(constants["NAMESPACE"], "hase/code27/music")
        self.assertEqual(constants["COMMAND_TYPE"], constants["NAMESPACE"])
        self.assertEqual(constants["SUBSCRIBE_TYPE"], "hase/code27/music/subscribe")
        source = _card_source()
        boundary, neutral = _provider_boundary_code(source)
        # The namespace lives in the card's provider boundary and is reached
        # through it - never spelled out at a call site.
        self.assertEqual(boundary.count('"hase/code27/music"'), 1)
        self.assertEqual(boundary.count('"hase/code27/music/subscribe"'), 1)
        self.assertIn("this._transport().command", neutral)
        self.assertIn("this._transport().subscribe", neutral)
        self.assertNotIn("hase/code27/music", neutral)

    def test_card_never_touches_the_code27_api(self):
        source = _card_source()
        for forbidden in (
            "/data/user/",
            "music_control",
            "music_storage",
            "music_playback",
            "fetch(",
            "XMLHttpRequest",
            "localStorage",
            "sessionStorage",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_card_code_stays_provider_neutral_outside_the_boundary(self):
        """The frontend boundary is the only place a provider may be known.

        Guards the split this card is built on: HASEAMP is the UI, one frontend
        provider is a backend it talks to. Device vocabulary, the device's own
        command units, namespaces and reason codes must not creep back into the
        UI code - only into the marked table.
        """
        _boundary, neutral = _provider_boundary_code(_card_source())
        for token in (
            "code27",           # the provider id itself
            "hase/code27",      # its WebSocket namespace
            "no_code27_device", # its "no device loaded" reason code
            "progress_ms",      # the device's own position unit
            "music_control",    # the device client call
            "MODE_INACTIVE",    # device error codes
            "INVALID_ARGUMENT",
            "PLAYING",          # device status vocabulary
            "IDLE",
            "local:",           # device track-id prefix
            "/data/user/",      # device REST API
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, neutral)

    def test_card_declares_its_provider_boundary_exactly_once(self):
        source = _card_source()
        self.assertEqual(source.count(_BOUNDARY_START), 1)
        self.assertEqual(source.count(_BOUNDARY_END), 1)
        self.assertLess(source.index(_BOUNDARY_START), source.index(_BOUNDARY_END))
        # A provider without music features has nothing the card can talk to,
        # so the table must be able to be empty of entries and still parse.
        self.assertIn("const PROVIDER_TRANSPORTS = {", source)
        self.assertIn("function transportFor(provider) {", source)
        self.assertIn('const DEFAULT_PROVIDER = "code27";', source)

    def test_card_holds_no_queue_state(self):
        # The queue lives in Home Assistant; the card only remembers which row
        # the user selected last.
        source = _card_source()
        self.assertIn("this._selected", source)
        self.assertNotIn("this._queue =", source)
        self.assertNotIn("this._playlists =", source)

    def test_card_renders_the_capability_gated_controls(self):
        source = _card_source()
        # Optional controls are gated on the backend's feature flags.
        for flag in ("features.seek", "features.volume", "features.stop", "features.browser"):
            with self.subTest(flag=flag):
                self.assertIn(flag, source)

    def test_card_registers_itself_as_a_lovelace_card(self):
        source = _card_source()
        self.assertIn('customElements.define("haseamp-card", HaseAmpCard)', source)
        self.assertIn('type: "haseamp-card"', source)
        self.assertIn("setConfig(config)", source)
        self.assertIn("getCardSize()", source)
        self.assertIn("getConfigElement()", source)
        self.assertIn("getStubConfig()", source)

    def test_card_layout_is_responsive(self):
        source = _card_source()
        # Single-column tabbed layout: the panels stack, and the clock
        # shrinks on a phone.
        self.assertIn("@media (max-width: 480px)", source)
        self.assertIn("flex-wrap:wrap", source)

    def test_card_does_not_claim_audio_spectrum_data(self):
        source = _card_source()
        self.assertIn("no audio spectrum data", source)
        self.assertIn("Dekorative Wiedergabe-Anzeige", source)

    def test_ws_layer_uses_the_documented_error_codes(self):
        source = (PROVIDER / "music_api.py").read_text(encoding="utf-8")
        self.assertIn('"music_validation"', source)
        self.assertIn('"music_error"', source)
        self.assertIn('"music_unavailable"', source)

    def test_code27_music_modules_stay_inside_the_code27_provider(self):
        """The architectural boundary: HASE stays a Spellbook Engine.

        CODE27's music implementation belongs to the CODE27 provider - it must
        never become a HASE-wide music module, and HASE core must not grow a
        music layer. Each provider brings its own music interface if it has one.
        """
        for name in ("music.py", "music_runtime.py", "music_api.py", "music_model.py"):
            with self.subTest(module=name):
                self.assertFalse((HASE / name).exists())
                self.assertFalse((HASE / "core" / name).exists())
                self.assertTrue((PROVIDER / name).is_file(), name)
        self.assertTrue((PROVIDER / "media_player.py").is_file())

    def test_haseamp_names_the_card_only(self):
        """HASEAMP is the Winamp-inspired card - not a CODE27 backend module.

        The card is the one artifact that legitimately carries the product name;
        everything implementing CODE27's music side is named after what it does.
        """
        for path in (HASE / "providers").rglob("*.py"):
            with self.subTest(module=path.name):
                self.assertNotIn("haseamp", path.name.lower())
        self.assertTrue(CARD.is_file())

    def test_no_code27_module_or_symbol_is_named_haseamp(self):
        for path in PROVIDER.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                # The class/constant spelling, and the retired module names.
                self.assertNotIn("HaseAmp", source)
                for retired in ("haseamp.py", "haseamp_api", "haseamp_queue", "haseamp_api.py"):
                    self.assertNotIn(retired, source)
        # The single legitimate lowercase use: the already-written Store
        # namespace holding this feature's data, which must not be renamed.
        runtime_source = (PROVIDER / "music_runtime.py").read_text(encoding="utf-8")
        self.assertIn('STORAGE_KEY = "hase.haseamp"', runtime_source)

    def test_no_hase_core_module_was_touched_for_code27_music(self):
        for path in (HASE / "core").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotIn("playlist", source.lower())
                for token in ("haseamp", "music_runtime", "music_api", "music_model"):
                    self.assertNotIn(token, source.lower())

    def test_provider_descriptor_exposes_no_music_contract(self):
        """No generic provider music contract was introduced."""
        source = (HASE / "providers" / "provider.py").read_text(encoding="utf-8")
        self.assertNotIn("music", source.lower())

    def test_card_asset_is_tracked_next_to_the_existing_card(self):
        self.assertTrue(CARD.is_file())
        self.assertTrue((ROOT / "custom_components" / "hase" / "cards" / "code27-card.js").is_file())


class CorrectionLockTests(BackendTestCase):
    """Two concurrent observations must not both trigger a takeover (M1)."""

    async def test_concurrent_device_change_observations_only_correct_once(self):
        """If _async_follow_device is re-entered while still running, the
        second call must be a no-op (guard via _correcting set)."""
        import asyncio as _asyncio

        await self.add("a", "b", play=True)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        self.control.reset_mock()

        # Simulate two concurrent observations arriving in the same event loop tick.
        r1, r2 = await _asyncio.gather(
            self.backend.async_note_playback(ENTRY_ID, self.playing("outside")),
            self.backend.async_note_playback(ENTRY_ID, self.playing("outside")),
        )
        # Only one takeover should have fired, not two.
        took_over_count = sum(1 for r in (r1, r2) if r.get("took_over"))
        self.assertEqual(took_over_count, 1, "exactly one takeover expected, not two")
        self.assertEqual(self.control.await_count, 1, "only one PLAY command expected")


class ResumeWithDeviceCheckTests(BackendTestCase):
    """play action must re-issue PLAY with explicit track when device drifted (M2)."""

    async def test_resume_when_device_plays_expected_track_sends_bare_play(self):
        """Normal resume: device reports the queue's track → bare PLAY, no re-issue."""
        await self.add("a", "b", play=True)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        # Simulate pause
        self.coordinator.data.music_playback = {
            "status": "PAUSED", "track_id": self.track("a").uri, "track_name": "a"
        }
        self.control.reset_mock()
        await self.backend.async_dispatch("transport", {"target": ENTRY_ID, "action": "play"})
        _args, kwargs = self.control.await_args
        self.assertEqual(_args[0], "PLAY")
        self.assertNotIn("track_id", kwargs)

    async def test_resume_when_device_drifted_reissues_play_with_track(self):
        """Device plays 'outside' while queue thinks 'a' → PLAY with track_id."""
        await self.add("a", "b", play=True)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        # Device drifted to a different track
        self.coordinator.data.music_playback = {
            "status": "PLAYING", "track_id": "local:/x/outside.mp3", "track_name": "outside"
        }
        self.control.reset_mock()
        await self.backend.async_dispatch("transport", {"target": ENTRY_ID, "action": "play"})
        _args, kwargs = self.control.await_args
        self.assertEqual(_args[0], "PLAY")
        self.assertEqual(kwargs["track_id"], self.track("a").uri)


class SeekTransitionTests(BackendTestCase):
    """Seek must use the queue's current track_id, not the snapshot (M11)."""

    async def test_seek_uses_queue_track_id_not_stale_snapshot(self):
        """During a transition the device snapshot still reports the old track.
        The seek command must reference the queue's current row, not the snapshot."""
        await self.add("a", "b", play=True)
        # Device snapshot still reports the old track 'a' (transition in progress)
        self.coordinator.data.music_playback = {
            "status": "PLAYING",
            "track_id": self.track("a").uri,  # stale: device hasn't updated yet
            "track_name": "a",
        }
        # Queue is already on 'b' (HASE advanced the queue)
        await self.backend.async_note_playback(ENTRY_ID, self.playing("a"))
        # Manually advance the queue to simulate HASE having already issued PLAY for b
        queue = self.backend.queue(ENTRY_ID)
        queue.advance()  # now current_track == b

        self.control.reset_mock()
        await self.backend.async_dispatch(
            "transport", {"target": ENTRY_ID, "action": "seek", "position": 10.0}
        )
        _args, kwargs = self.control.await_args
        self.assertEqual(_args[0], "SEEK")
        # Must reference b (queue's current), NOT the snapshot's stale 'a'
        self.assertEqual(kwargs["track_id"], self.track("b").uri)

    async def test_seek_falls_back_to_snapshot_when_queue_is_empty(self):
        """If the queue has no current track, fall back to the device snapshot."""
        # No queue setup — use a bare coordinator with a playback snapshot
        self.coordinator.data.music_playback = {
            "status": "PLAYING",
            "track_id": "local:/x/standalone.mp3",
            "track_name": "standalone",
        }
        self.control.reset_mock()
        await self.backend.async_dispatch(
            "transport", {"target": ENTRY_ID, "action": "seek", "position": 5.0}
        )
        _args, kwargs = self.control.await_args
        self.assertEqual(_args[0], "SEEK")
        self.assertEqual(kwargs["track_id"], "local:/x/standalone.mp3")


class SetupSingletonTests(unittest.IsolatedAsyncioTestCase):
    """async_setup_music must only ever create one MusicRuntime (M4)."""

    def _fresh_hass(self):
        hass = make_hass(coordinators={})
        bucket = runtime.code27_bucket(hass, create=True)
        bucket.pop(runtime.DATA_MUSIC, None)
        bucket.pop(runtime._DATA_MUSIC_LOCK, None)
        return hass

    async def test_concurrent_setup_returns_same_instance(self):
        """Two concurrent calls must not create two separate runtimes."""
        import asyncio as _asyncio
        hass = self._fresh_hass()

        store_a = FakeStore()
        store_b = FakeStore()

        # Run both setup calls concurrently
        a, b = await _asyncio.gather(
            runtime.async_setup_music(hass, store=store_a),
            runtime.async_setup_music(hass, store=store_b),
        )
        self.assertIs(a, b, "concurrent setup must return the same instance")
        # Only one backend should be in the bucket
        self.assertIs(a, runtime.async_get_music_runtime(hass))

    async def test_repeated_setup_returns_same_instance(self):
        """Sequential repeated calls also return the same instance."""
        hass = self._fresh_hass()
        first = await runtime.async_setup_music(hass, store=FakeStore())
        second = await runtime.async_setup_music(hass, store=FakeStore())
        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
