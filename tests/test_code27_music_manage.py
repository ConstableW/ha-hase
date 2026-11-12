"""Music management operations: inventory, import, delete, playlist editing."""

from __future__ import annotations

import base64
import os
import sys
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(__file__))

import test_code27_music as base  # noqa: E402

MusicError = base.MusicError
BackendTestCase = base.BackendTestCase
ROOT = "/storage/142E2DF92E2DD4900/code27/music"


def storage_with_sizes():
    storage = base.storage_fixture()
    storage["capacity_total_bytes"] = 1000
    storage["capacity_used_bytes"] = 250
    for index, entry in enumerate(storage["music_files"]):
        entry["size_bytes"] = 100 + index
    return storage


class ManageTestCase(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.coordinator.data.music_storage = storage_with_sizes()
        self.delete = AsyncMock()
        self.coordinator.client.delete_music_file = self.delete


class InventoryTests(ManageTestCase):
    async def test_inventory_lists_name_size_and_a_queue_ready_track(self):
        result = await self.backend.async_dispatch("inventory", {})
        by_name = {row["name"]: row for row in result["files"]}
        self.assertEqual(by_name["top.mp3"]["size"], 100)
        self.assertEqual(by_name["top.mp3"]["track"]["uri"], f"local:{ROOT}/top.mp3")
        self.assertTrue(result["ready"])

    async def test_library_reports_the_card_capacity(self):
        snapshot = await self.backend.async_dispatch("snapshot", {})
        self.assertEqual(snapshot["library"]["capacity_total_bytes"], 1000)
        self.assertEqual(snapshot["library"]["capacity_used_bytes"], 250)


class DeleteTests(ManageTestCase):
    async def test_delete_file_removes_it_from_the_card_and_the_queue(self):
        uri = f"local:{ROOT}/top.mp3"
        await self.backend.async_dispatch(
            "queue_add", {"tracks": [{"uri": uri, "title": "top", "provider": "code27"}]}
        )
        result = await self.backend.async_dispatch("delete_file", {"uri": uri})
        self.assertEqual(result["deleted"], 1)
        self.delete.assert_awaited_once_with(f"{ROOT}/top.mp3")
        self.assertEqual(result["queue"]["count"], 0)

    async def test_the_playing_file_is_never_deleted(self):
        uri = f"local:{ROOT}/top.mp3"
        self.coordinator.data.music_playback = {"status": "PLAYING", "track_id": uri}
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch("delete_file", {"uri": uri})
        self.delete.assert_not_awaited()

    async def test_a_paused_file_can_be_deleted(self):
        uri = f"local:{ROOT}/top.mp3"
        self.coordinator.data.music_playback = {"status": "PAUSED", "track_id": uri}
        result = await self.backend.async_dispatch("delete_file", {"uri": uri})
        self.assertEqual(result["deleted"], 1)

    async def test_unknown_files_and_foreign_uris_are_refused(self):
        for uri in (f"local:{ROOT}/nope.mp3", "spotify:track:1", None):
            with self.subTest(uri=uri):
                with self.assertRaises(MusicError):
                    await self.backend.async_dispatch("delete_file", {"uri": uri})
        self.delete.assert_not_awaited()

    async def test_delete_folder_removes_everything_below_it(self):
        result = await self.backend.async_dispatch(
            "delete_folder", {"node": "folder:Rockkapella"}
        )
        self.assertEqual(result["deleted"], 2)
        paths = sorted(call.args[0] for call in self.delete.await_args_list)
        self.assertEqual(
            paths,
            [
                f"{ROOT}/Rockkapella/03 - Stand By Me.mp3",
                f"{ROOT}/Rockkapella/Live/live.mp3",
            ],
        )

    async def test_delete_folder_stops_before_touching_the_playing_file(self):
        self.coordinator.data.music_playback = {
            "status": "PLAYING",
            "track_id": f"local:{ROOT}/Rockkapella/Live/live.mp3",
        }
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch(
                "delete_folder", {"node": "folder:Rockkapella"}
            )
        self.delete.assert_not_awaited()

    async def test_delete_folder_needs_a_folder_node(self):
        for node in (None, "", "root", "track:x"):
            with self.subTest(node=node):
                with self.assertRaises(MusicError):
                    await self.backend.async_dispatch("delete_folder", {"node": node})


class PlaylistEditTests(ManageTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.add("a", "b", "c")
        await self.backend.async_dispatch("playlist_save_as", {"name": "Mix"})

    async def add(self, *names):
        return await self.backend.async_dispatch(
            "queue_add",
            {
                "tracks": [
                    {"uri": self.track(n).uri, "title": n, "provider": "code27"}
                    for n in names
                ]
            },
        )

    async def test_get_returns_the_tracks_in_order(self):
        result = await self.backend.async_dispatch("playlist_get", {"name": "Mix"})
        self.assertEqual([t["title"] for t in result["tracks"]], ["a", "b", "c"])

    async def test_update_reorders_and_drops_without_touching_the_queue(self):
        result = await self.backend.async_dispatch(
            "playlist_update", {"name": "Mix", "order": [2, 0]}
        )
        self.assertEqual([t["title"] for t in result["tracks"]], ["c", "a"])
        snapshot = await self.backend.async_dispatch("snapshot", {})
        self.assertEqual(snapshot["queue"]["count"], 3)
        self.assertEqual(snapshot["playlists"][0]["count"], 2)

    async def test_update_rejects_positions_that_do_not_exist(self):
        for order in ([3], [-1], ["a"], "nope"):
            with self.subTest(order=order):
                with self.assertRaises(MusicError):
                    await self.backend.async_dispatch(
                        "playlist_update", {"name": "Mix", "order": order}
                    )


class CardSwapTests(ManageTestCase):
    async def test_a_playlist_follows_the_music_to_a_swapped_card(self):
        old = "local:/storage/OLDCARD/code27/music/Rockkapella/Live/live.mp3"
        gone = "local:/storage/OLDCARD/code27/music/weg.mp3"
        await self.backend.async_dispatch(
            "queue_add",
            {"tracks": [
                {"uri": old, "title": "live", "provider": "code27"},
                {"uri": gone, "title": "weg", "provider": "code27"},
            ]},
        )
        await self.backend.async_dispatch("playlist_save_as", {"name": "Mix"})
        shown = await self.backend.async_dispatch("playlist_get", {"name": "Mix"})
        self.assertEqual(
            [t["uri"] for t in shown["tracks"]],
            [f"local:{ROOT}/Rockkapella/Live/live.mp3", gone],
        )
        loaded = await self.backend.async_dispatch("playlist_load", {"name": "Mix"})
        self.assertEqual(
            loaded["items"][0]["track"]["uri"], f"local:{ROOT}/Rockkapella/Live/live.mp3"
        )

    async def test_tracks_already_on_the_card_are_left_alone(self):
        uri = f"local:{ROOT}/top.mp3"
        await self.backend.async_dispatch(
            "queue_add", {"tracks": [{"uri": uri, "title": "top", "provider": "code27"}]}
        )
        await self.backend.async_dispatch("playlist_save_as", {"name": "Mix"})
        shown = await self.backend.async_dispatch("playlist_get", {"name": "Mix"})
        self.assertEqual(shown["tracks"][0]["uri"], uri)


class ImportChunkTests(ManageTestCase):
    def setUp(self):
        pass

    async def test_chunks_are_assembled_and_uploaded_once(self):
        upload = AsyncMock(
            return_value={
                "uploaded": [
                    {"name": "x.wav", "path": f"{ROOT}/x.wav", "duration_ms": 1000}
                ],
                "rejected": [],
            }
        )
        self.coordinator.client.upload_music = upload
        first = await self.backend.async_dispatch(
            "import_chunk",
            {
                "name": "x.wav",
                "upload_id": "u1",
                "index": 0,
                "total": 2,
                "data": base64.b64encode(b"AB").decode(),
            },
        )
        self.assertFalse(first["done"])
        upload.assert_not_awaited()
        last = await self.backend.async_dispatch(
            "import_chunk",
            {
                "name": "x.wav",
                "upload_id": "u1",
                "index": 1,
                "total": 2,
                "data": base64.b64encode(b"CD").decode(),
            },
        )
        self.assertTrue(last["done"])
        content, name, content_type = upload.await_args.args[0][0]
        self.assertEqual((content, name), (b"ABCD", "x.wav"))
        self.assertTrue(content_type.startswith("audio/"))
        self.assertEqual(last["tracks"][0]["uri"], f"local:{ROOT}/x.wav")

    async def test_non_audio_and_out_of_order_chunks_are_refused(self):
        self.coordinator.client.upload_music = AsyncMock()
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch(
                "import_chunk",
                {"name": "x.txt", "upload_id": "u", "index": 0, "total": 1, "data": ""},
            )
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch(
                "import_chunk",
                {"name": "x.wav", "upload_id": "u", "index": 1, "total": 2, "data": ""},
            )
        self.coordinator.client.upload_music.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()


class RescanTests(ManageTestCase):
    async def test_the_inventory_is_rescanned_after_an_upload_and_a_delete(self):
        fresh = dict(storage_with_sizes())
        fresh["music_files"] = fresh["music_files"][:1]
        reader = AsyncMock(return_value=fresh)
        self.coordinator.client.get_music_storage = reader
        self.coordinator.client.upload_music = AsyncMock(
            return_value={"uploaded": [], "rejected": []}
        )
        await self.backend.async_dispatch(
            "import_chunk",
            {"name": "x.wav", "upload_id": "u", "index": 0, "total": 1, "data": ""},
        )
        reader.assert_awaited_with(refresh=True)
        self.assertEqual(len(self.coordinator.data.music_storage["music_files"]), 1)
        reader.reset_mock()
        await self.backend.async_dispatch(
            "delete_file", {"uri": f"local:{ROOT}/top.mp3"}
        )
        reader.assert_awaited_with(refresh=True)

    async def test_a_failing_rescan_is_not_fatal(self):
        self.coordinator.client.get_music_storage = AsyncMock(side_effect=RuntimeError("x"))
        result = await self.backend.async_dispatch(
            "delete_file", {"uri": f"local:{ROOT}/top.mp3"}
        )
        self.assertEqual(result["deleted"], 1)


class PendingUploadTests(ManageTestCase):
    async def _upload(self, name="new.wav"):
        self.coordinator.client.upload_music = AsyncMock(
            return_value={
                "uploaded": [{"name": name, "path": f"{ROOT}/{name}", "duration_ms": 1000, "size_bytes": 4}],
                "rejected": [],
            }
        )
        return await self.backend.async_dispatch(
            "import_chunk",
            {"name": name, "upload_id": "u", "index": 0, "total": 1,
             "data": base64.b64encode(b"ABCD").decode()},
        )

    async def test_an_uploaded_but_unlisted_file_still_counts_as_on_the_card(self):
        await self._upload()
        names = {f["name"] for f in (await self.backend.async_dispatch("inventory", {}))["files"]}
        self.assertIn("new.wav", names)

    async def test_the_record_goes_once_the_device_lists_the_file(self):
        await self._upload()
        self.coordinator.data.music_storage["music_files"].append(
            {"name": "new.wav", "path": f"{ROOT}/new.wav", "duration_ms": 1000, "size_bytes": 4}
        )
        files = (await self.backend.async_dispatch("inventory", {}))["files"]
        self.assertEqual([f["name"] for f in files].count("new.wav"), 1)

    async def test_the_record_goes_when_the_card_is_swapped(self):
        await self._upload()
        self.coordinator.data.music_storage["root_path"] = "/storage/OTHERCARD"
        names = {f["name"] for f in (await self.backend.async_dispatch("inventory", {}))["files"]}
        self.assertNotIn("new.wav", names)

    async def test_an_unlisted_upload_can_still_be_deleted(self):
        await self._upload()
        result = await self.backend.async_dispatch("delete_file", {"uri": f"local:{ROOT}/new.wav"})
        self.assertEqual(result["deleted"], 1)
        names = {f["name"] for f in (await self.backend.async_dispatch("inventory", {}))["files"]}
        self.assertNotIn("new.wav", names)


class InventoryPathTests(ManageTestCase):
    async def test_inventory_reports_the_path_below_the_music_folder(self):
        rows = {
            row["name"]: row["relative"]
            for row in (await self.backend.async_dispatch("inventory", {}))["files"]
        }
        self.assertEqual(rows["live.mp3"], "Rockkapella/Live/live.mp3")
        self.assertEqual(rows["top.mp3"], "top.mp3")


class FolderScanTests(ManageTestCase):
    async def test_every_folder_with_music_becomes_a_playlist(self):
        result = await self.backend.async_dispatch("playlists_from_folders", {})
        self.assertEqual(sorted(result["created"]), ["Rockkapella", "Rockkapella / Live"])
        self.assertEqual(result["skipped"], 0)
        shown = await self.backend.async_dispatch("playlist_get", {"name": "Rockkapella"})
        self.assertEqual([t["title"] for t in shown["tracks"]], ["03 - Stand By Me.mp3"])

    async def test_files_in_the_music_root_belong_to_no_playlist(self):
        result = await self.backend.async_dispatch("playlists_from_folders", {})
        names = [p["name"] for p in result["playlists"]]
        self.assertFalse(any("top" in name for name in names))

    async def test_existing_playlists_are_kept_and_deleted_ones_come_back(self):
        await self.backend.async_dispatch("playlists_from_folders", {})
        again = await self.backend.async_dispatch("playlists_from_folders", {})
        self.assertEqual((again["created"], again["skipped"]), ([], 2))
        await self.backend.async_dispatch("playlist_delete", {"name": "Rockkapella"})
        back = await self.backend.async_dispatch("playlists_from_folders", {})
        self.assertEqual((back["created"], back["skipped"]), (["Rockkapella"], 1))

    async def test_tracks_keep_natural_file_order(self):
        self.coordinator.data.music_storage["music_files"] = [
            {"name": n, "path": f"{ROOT}/Album/{n}", "duration_ms": 1000}
            for n in ("10 c.mp3", "2 b.mp3", "1 a.mp3")
        ]
        await self.backend.async_dispatch("playlists_from_folders", {})
        shown = await self.backend.async_dispatch("playlist_get", {"name": "Album"})
        self.assertEqual([t["title"] for t in shown["tracks"]], ["1 a.mp3", "2 b.mp3", "10 c.mp3"])

    async def test_the_queue_is_untouched(self):
        await self.backend.async_dispatch(
            "queue_add",
            {"tracks": [{"uri": f"local:{ROOT}/top.mp3", "title": "top", "provider": "code27"}]},
        )
        await self.backend.async_dispatch("playlists_from_folders", {})
        snapshot = await self.backend.async_dispatch("snapshot", {})
        self.assertEqual(snapshot["queue"]["count"], 1)

    async def test_without_an_inventory_it_says_so(self):
        self.coordinator.data.music_storage = {}
        with self.assertRaises(MusicError):
            await self.backend.async_dispatch("playlists_from_folders", {})


class ResumeTests(ManageTestCase):
    """Remember where a playlist was last played and pick it up again."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.names = ("a", "b", "c")
        await self.backend.async_dispatch(
            "queue_add",
            {"tracks": [
                {"uri": self.track(n).uri, "title": n, "provider": "code27", "duration": 300}
                for n in self.names
            ]},
        )
        await self.backend.async_dispatch("playlist_save_as", {"name": "Book"})
        self.pid = self.backend.library(base.ENTRY_ID).find_by_name("Book").playlist_id

    def observe(self, name, position, status="PLAYING", version=1):
        playback = self.playing(name, version)
        playback.update(status=status, position_ms=int(position * 1000), duration_ms=300000)
        return playback

    async def note(self, name, position, status="PLAYING"):
        self.coordinator.data.music_playback = self.observe(name, position, status)
        await self.backend.async_note_playback(base.ENTRY_ID, self.coordinator.data.music_playback)

    def record(self):
        return self.backend._resume.get(base.ENTRY_ID, {}).get(self.pid)

    async def test_playback_of_the_loaded_playlist_is_remembered(self):
        await self.note("b", 42.0)
        self.assertEqual(
            (self.record()["uri"], self.record()["index"], self.record()["position"]),
            (self.track("b").uri, 1, 42.0),
        )

    async def test_small_position_changes_do_not_rewrite_the_store(self):
        await self.note("b", 42.0)
        saves = self.store.saves
        await self.note("b", 45.0)
        self.assertEqual(self.store.saves, saves)
        self.assertEqual(self.record()["position"], 42.0)
        await self.note("b", 60.0)
        self.assertEqual(self.record()["position"], 60.0)

    async def test_a_pause_stores_the_exact_spot(self):
        await self.note("b", 42.0)
        await self.note("b", 47.3, "PAUSED")
        self.assertEqual(self.record()["position"], 47.3)

    async def test_tracks_outside_the_playlist_and_idle_are_ignored(self):
        await self.note("zzz", 10.0)
        await self.note("b", 0.0, "IDLE")
        self.assertIsNone(self.record())

    async def test_the_summary_carries_the_resume_point(self):
        await self.note("c", 90.0)
        snapshot = await self.backend.async_dispatch("snapshot", {})
        resume = snapshot["playlists"][0]["resume"]
        self.assertEqual((resume["index"], resume["title"], resume["position"]), (2, "c", 90.0))

    async def test_loading_with_resume_selects_plays_and_seeks(self):
        await self.note("b", 100.0)
        self.control.reset_mock()
        result = await self.backend.async_dispatch("playlist_load", {"name": "Book", "resume": True})
        self.assertEqual(result["resumed"]["title"], "b")
        self.assertEqual(result["resumed"]["position"], 97.0)  # 3 s rewind
        self.assertTrue(result["resumed"]["seeked"])
        calls = [(c.args[0], c.kwargs) for c in self.control.await_args_list]
        self.assertEqual(calls[0][0], "PLAY")
        self.assertEqual(calls[0][1]["track_id"], self.track("b").uri)
        self.assertEqual(calls[1][0], "SEEK")
        self.assertEqual(calls[1][1]["progress_ms"], 97000)
        self.assertEqual(result["items"][result["index"]]["track"]["title"], "b")

    async def test_loading_without_resume_does_not_play(self):
        await self.note("b", 100.0)
        self.control.reset_mock()
        result = await self.backend.async_dispatch("playlist_load", {"name": "Book"})
        self.assertIsNone(result["resumed"])
        self.control.assert_not_awaited()

    async def test_a_playlist_that_was_never_played_just_loads(self):
        result = await self.backend.async_dispatch("playlist_load", {"name": "Book", "resume": True})
        self.assertIsNone(result["resumed"])
        self.assertEqual(result["count"], 3)

    async def test_a_track_that_had_just_ended_continues_with_the_next_one(self):
        await self.note("a", 298.0)
        result = await self.backend.async_dispatch("playlist_load", {"name": "Book", "resume": True})
        self.assertEqual((result["resumed"]["title"], result["resumed"]["position"]), ("b", 0.0))

    async def test_the_start_of_a_track_is_not_rewound_into_nothing(self):
        await self.note("b", 2.0)
        result = await self.backend.async_dispatch("playlist_load", {"name": "Book", "resume": True})
        self.assertEqual(result["resumed"]["position"], 0.0)
        self.assertFalse(result["resumed"]["seeked"])

    async def test_a_failing_seek_still_resumes_from_the_start_of_the_track(self):
        await self.note("b", 100.0)

        async def control(action, **extra):
            if action == "SEEK":
                raise RuntimeError("not ready")
            return {"status": "PLAYING"}

        self.coordinator.client.music_control = AsyncMock(side_effect=control)
        result = await self.backend.async_dispatch("playlist_load", {"name": "Book", "resume": True})
        self.assertEqual(result["resumed"]["title"], "b")
        self.assertFalse(result["resumed"]["seeked"])

    async def test_the_resume_point_survives_a_restart(self):
        await self.note("b", 100.0)
        other = base.MusicRuntime(self.hass, store=self.store)
        await other.async_load()
        self.assertEqual(other._resume[base.ENTRY_ID][self.pid]["position"], 100.0)

    async def test_deleting_a_playlist_forgets_its_resume_point(self):
        await self.note("b", 100.0)
        await self.backend.async_dispatch("playlist_delete", {"name": "Book"})
        self.assertIsNone(self.record())


class AudiobookScanTests(ManageTestCase):
    async def test_disc_folders_join_one_playlist_in_disc_order(self):
        book = "Autor - Das Buch"
        self.coordinator.data.music_storage["music_files"] = [
            {"name": n, "path": f"{ROOT}/{book}/{cd}/{n}", "duration_ms": 1000}
            for cd in ("CD 10", "CD 2", "CD 1")
            for n in ("02.mp3", "01.mp3")
        ]
        result = await self.backend.async_dispatch("playlists_from_folders", {})
        self.assertEqual(result["created"], [book])
        shown = await self.backend.async_dispatch("playlist_get", {"name": book})
        self.assertEqual(len(shown["tracks"]), 6)
        self.assertEqual(
            [t["uri"].split("/")[-2] for t in shown["tracks"]],
            ["CD 1", "CD 1", "CD 2", "CD 2", "CD 10", "CD 10"],
        )

    async def test_a_very_long_path_still_gets_a_playlist_name(self):
        long_name = "Autor " + "x" * 80
        self.coordinator.data.music_storage["music_files"] = [
            {"name": "a.mp3", "path": f"{ROOT}/Reihe/{long_name}/a.mp3", "duration_ms": 1000}
        ]
        result = await self.backend.async_dispatch("playlists_from_folders", {})
        self.assertEqual(len(result["created"]), 1)
        self.assertLessEqual(len(result["created"][0]), 64)


class SoundEffectTests(ManageTestCase):
    async def test_the_presets_are_sent_to_the_device_as_its_own_action(self):
        for effect in ("classical", "surround", "vocal"):
            self.control.reset_mock()
            result = await self.backend.async_dispatch("set_sound_effect", {"effect": effect})
            self.assertEqual(result["sound_effect"], effect)
            args, kwargs = self.control.await_args
            self.assertEqual((args[0], kwargs["sound_effect"]), ("SET_SOUND_EFFECT", effect))

    async def test_anything_else_is_refused_before_the_device_is_asked(self):
        for effect in ("bass", "", None, 3):
            with self.subTest(effect=effect):
                with self.assertRaises(MusicError):
                    await self.backend.async_dispatch("set_sound_effect", {"effect": effect})
        self.control.assert_not_awaited()

    async def test_the_snapshot_offers_the_presets_and_reports_the_current_one(self):
        self.coordinator.data.music_playback = {"status": "PAUSED", "sound_effect": "surround"}
        snapshot = await self.backend.async_dispatch("snapshot", {})
        self.assertTrue(snapshot["features"]["sound_effect"])
        self.assertEqual(list(snapshot["features"]["sound_effects"]), ["classical", "surround", "vocal"])
        self.assertEqual(snapshot["playback"]["sound_effect"], "surround")

    async def test_an_unknown_value_from_the_device_is_not_passed_on(self):
        self.coordinator.data.music_playback = {"status": "PAUSED", "sound_effect": "mystery"}
        snapshot = await self.backend.async_dispatch("snapshot", {})
        self.assertIsNone(snapshot["playback"]["sound_effect"])


class PlayPlaylistTests(ResumeTests):
    """What `hase.play_playlist` relies on: ``playlist_load`` with ``play``."""

    async def test_a_playlist_that_was_never_played_starts_from_its_first_track(self):
        self.control.reset_mock()
        result = await self.backend.async_dispatch(
            "playlist_load", {"name": "Book", "resume": True, "play": True}
        )
        self.assertTrue(result["started"])
        self.assertIsNone(result["resumed"])
        self.assertEqual(self.control.await_args_list[0].args[0], "PLAY")
        self.assertEqual(self.control.await_args_list[0].kwargs["track_id"], self.track("a").uri)

    async def test_a_played_playlist_continues_where_it_stopped(self):
        await self.note("b", 100.0)
        self.control.reset_mock()
        result = await self.backend.async_dispatch(
            "playlist_load", {"name": "Book", "resume": True, "play": True}
        )
        self.assertEqual(result["resumed"]["title"], "b")
        self.assertTrue(result["started"])
        self.assertEqual(self.control.await_args_list[0].kwargs["track_id"], self.track("b").uri)

    async def test_resume_off_plays_from_the_start_even_if_it_was_played(self):
        await self.note("b", 100.0)
        self.control.reset_mock()
        result = await self.backend.async_dispatch(
            "playlist_load", {"name": "Book", "resume": False, "play": True}
        )
        self.assertIsNone(result["resumed"])
        self.assertEqual(self.control.await_args_list[0].kwargs["track_id"], self.track("a").uri)

    async def test_plain_load_still_does_not_play(self):
        self.control.reset_mock()
        result = await self.backend.async_dispatch("playlist_load", {"name": "Book"})
        self.assertFalse(result["started"])
        self.control.assert_not_awaited()
