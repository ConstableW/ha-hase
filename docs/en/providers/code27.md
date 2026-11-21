🇩🇪 [Deutsch](../../de/providers/code27.md) | 🇬🇧 English

# CODE27 provider

CODE27 is an optional companion provider, not HASE's identity. A standalone HASE-Agent, Spellbooks, Studio and MCP do not require CODE27 hardware.

The embedded provider pairs Home Assistant with a local CODE27 device and uses its local REST/WebSocket API. It exposes device entities for music, ambient light, character selection, alarms, notes, chat/status data and supported diagnostics; it also provides `hase.*` services, chat events and the optional `custom:code27-card` and `custom:haseamp-card` dashboard cards. Exact service schemas are in `custom_components/hase/services.yaml`.

## Set up a device and use a Spellbook

There are two paths for a physical device:

1. **Embedded in HASE (`domain=hase`):** Open **Settings → Devices & services → Add integration → HASE → Connect a CODE27 device**. Enter the local IP and port (usually `8081`), then confirm the six-digit code shown on the device. The HASE entry owns the runtime, device and entities.
2. **Standalone CODE27 with HASE (`domain=code27`):** First pair the device through the standalone [CODE27 integration](https://github.com/ConstableW/ha-code27). Then choose **Add integration → HASE → Bind existing standalone CODE27** and select the loaded CODE27 entry. HASE creates only a binding entry. Runtime, device, entities and credentials remain with the `code27` entry; no second pairing takes place.

In **either** case, open **Configure** on the corresponding **HASE hub** and choose the *Conversation agent for Codie's sentences* (normally a HASE Spellbooker with your Spellbook; custom books live in `<config>/hase_spellbooks/`). Codie's own model then hands what you say to that agent - no codeword. The standalone CODE27 entry does not carry this setting; it belongs to the HASE binding.

If a standalone CODE27 entry is deleted and recreated, HASE shows a repair issue for the old binding. Set up the device under CODE27, then use **Reconfigure** on the HASE binding to select the new entry ID, or delete the HASE binding if it is no longer needed. Binding changes neither CODE27 entry data nor registry ownership. The same identifiable `machine_id` cannot also be configured as an embedded HASE device.

The available HASE features were tested with a physical CODE27 device on a local Home Assistant installation, including embedded and external binding, the Spellbooker, and MCP through hasechan. The technical limits below concern feature scope or specific edge cases.

## CODE27 Music and the HASEAMP card

Two names, two things: **HASEAMP** is the Winamp-inspired Lovelace card (the user interface, `custom_components/hase/cards/haseamp-card.js`). **CODE27 Music** is the music implementation CODE27 owns inside this provider: queue, playlists, persistence and autoadvance, plus the WebSocket contract the card talks to. There is no HASE music platform and no generic provider music contract - HASE stays a Spellbook Engine, each frontend provider brings its own music interface if it has one, and the card itself knows no device: it is driven by the backend's capability flags (`features.*`) and reaches the backend through a small provider table in its own source. Queue, playlists, persistence and autoadvance live entirely inside the CODE27 provider (`custom_components/hase/providers/code27/`), and the router, matcher and executor are untouched.

```
CODE27 provider (coordinator + Code27Client + media_player entity)  = device authority
      └─ CODE27 music runtime (providers/code27/music_runtime.py)   = queue + playlists + autoadvance
            └─ WebSocket hase/code27/music  (music_api.py)
                  └─ custom_components/hase/cards/haseamp-card.js  ->  /hase-cards/haseamp-card.js
```

### Module layout

| Module | Responsibility |
|---|---|
| `music.py` | CODE27 music library access: the row/browser tree a TF-card path becomes, the device-error wording and the playback projection. Shared with the media player entity. |
| `music_model.py` | CODE27's own domain model: `Track`, `ActiveQueue`, `Playlist`, `PlaylistLibrary`, `MusicError`. Pure Python - no Home Assistant, no device access. |
| `music_runtime.py` | The runtime: queue orchestration, persistence, autoadvance, device correction and the transport operations (including `seek`), the TF-card management operations (inventory, import, delete) and stored-playlist editing. |
| `music_api.py` | CODE27's WebSocket contract `hase/code27/music` (+ `/subscribe`) and its registration for a provider entry. |
| `media_player.py` | The native Home Assistant `MediaPlayerEntity` - a second consumer of the same music code, independent of the card. |

```
                 ┌── media_player.py ──► native HA media player
CODE27 Music ────┤
                 └── music_api.py ─────► HASEAMP card
```

The module names are architecture-descriptive on purpose: HASEAMP is the card, so no CODE27 backend module carries that name, and none of these modules is a HASE-wide contract. Cards in general - and the frontend boundary a provider registers there - are documented in [HASEAMP (dashboard card)](../cards.md).

Installation: HASE ships and loads `haseamp-card.js` automatically; just add `custom:haseamp-card`. Optional keys are `target` (the config_entry_id of a real CODE27 device entry), `name`, `show_browser`, `show_queue`, `show_playlists`, `show_import`, `show_animation`, `show_diagnostics`, and `follow_light` (default on: the card's colours follow the device's ambient light). The editor lists real device entries under either owner, excluding HASE External Hub bindings; the selected target determines the music namespace. Select a target when more than one exists.

The card is pure frontend: it **never** talks to the CODE27 device REST/WebSocket API directly, only through the owner's WebSocket contract (`hase/code27/music` or `code27/music`). The active queue and stored playlists live in that Home Assistant runtime, not in the browser: reloading the dashboard cannot lose them, and a queue mutation never implicitly changes a stored playlist.

What CODE27 cannot do, and what the music runtime therefore does not claim: `/data/user/music/control` has **no** STOP action (confirmed action set: `PLAY | PAUSE | PREVIOUS | NEXT | SET_PLAY_MODE | SEEK | SET_VOLUME | SET_SOUND_EFFECT`). A stop is therefore a queue stop plus a device pause - the card labels that from the capability flag, not from hard-wired knowledge of the device. Shuffle and repeat are queue modes in the respective owner's runtime, not device play modes. `SEEK` *is* a real device action (`progress_ms`) and is used by both the media player entity and the music runtime.

### The queue steers playback, despite the device's folder continuation

When a track ends, CODE27 keeps playing **the next file in the folder** rather than the next queue row, and its status stays `PLAYING` throughout - so the `PLAYING` to `IDLE` contract never sees a track end at all. The music runtime therefore watches for the **change of the `track_id` the device reports** and corrects it:

* if the device already plays exactly the row the queue wanted next, that row is adopted (no `PLAY`, which would restart the track from zero);
* if it plays anything else, the runtime takes playback back and starts the queue's own next row;
* it only ever corrects a device that previously **confirmed** the row HASE started (by reporting the same `track_id`). A device that never reports a matching `track_id` cannot be interpreted, so the runtime does not fight it - the card remains the only control then.

While the queue is playing (`queue.playing`) playback belongs to HASE; Stop/Pause in the card ends that, after which the runtime only observes. The reason for the last decision is shown in the card's diagnostics line (`last: …`), and `diagnostics.device_track_id` / `position_reported` / `duration_reported` show what the device actually reports.

### Controls in detail

* **Progress bar**: clicking the bar **seeks** to the clicked position. The card sends the click position in seconds as `transport` with `action: "seek"`, and the backend passes it on as the device's own `SEEK` action (`progress_ms`, plus the `track_id` the device reports) - the same `music_control` call the media player entity's `async_media_seek` issues, so there is no second seek path. Clicking the separate **↺ button** still *restarts* the current track (`transport: restart`, `PLAY` for the current queue row); it is not the bar's action. The bar has two indicators, a fill **and** a position cursor, both driven from `position`/`duration`. When the device reports no track length the bar turns striped and animated, says so plainly and takes no click action at all - there would be nothing to aim at, and this stays deliberately distinguishable from "progress stuck at zero". The length is filled in from the queue row or the TF inventory where possible (`duration_source`: `device` / `queue_row` / `music_inventory`).
* **Control layout**: transport buttons (⏮ ▶/⏸ ⏹ ⏭ ↺) and the options (volume, source, SHF, RPT, "Remove played") sit side by side under the display and wrap on narrow screens, so state changes never push the volume slider around.
* **"Remove played"** (default: off) is the switch between the two queue concepts:
  * **off** - the queue keeps everything; played rows are only dimmed and the header shows the position (`3/12`). `previous` keeps working.
  * **on** - played rows **leave** the queue as playback moves on, which reads like an "up next" list; going back to something already played is inherently gone.
  It is applied only on the **automatic** paths (track end, device correction) - a manual row jump never deletes anything. The mode is backend state, persisted per CODE27 target, and survives reload and restart.
* **Overwriting asks first**: "Save"/"Save as" onto an existing name raises a confirmation; "yes" sends `playlist_save` (overwrite), "no" changes nothing. `playlist_save_as` itself never overwrites - a "yes" deliberately switches operation, so creating can never silently become replacing.
* **Typing**: subscription pushes arrive about once a second. The card repaints a section only when its content changed, and leaves the section alone while the user is typing or dragging a control.

### Layout, music mode and colours

* **One window, four tabs**: the player (clock, title, progress bar, transport, volume, SHF/RPT) sits on top; **Queue**, **Library**, **Playlists** and **Import** share the panel below. Double-clicking a queue row plays it; double-clicking a playlist loads it into the queue. Tab choice and selections are card state only - they never reach the backend.
* **"MusicMode is off!"**: while the device's music screen is closed (`binary_sensor` *Music mode active* / `snapshot.music_mode.active == false`) the device refuses playback commands (`MODE_INACTIVE`). The card says so in the display instead of showing a dead player, and disables the transport buttons. Queue, playlists and import stay usable. `music_mode.known` is false until the device has reported its page state - the card then shows nothing special rather than guessing.
* **Colours follow the device light**: the card reads the colour of the device's *ambient colour* light entity (read-only) and tints the whole window - title bar, bevels, buttons, tabs, text - with its hue, like a classic player skin. Lightness is pinned per part so the text stays readable, and blue/violet hues get a lighter text colour. A light that is off or unknown leaves the default green. The colour is only what Home Assistant last wrote to the light (the device has no live read-back), and it is purely cosmetic. Turn it off with `follow_light: false`.

### Music management

Everything here goes through the existing WebSocket contract; the card builds nothing the backend does not own.

| Operation | What it does |
|---|---|
| `inventory` | Every file on the TF card as `{name, size, track}`, plus files HASE uploaded that the device has not listed yet. |
| `import_chunk` | Receives one base64 piece of a file; the last piece uploads the assembled file to the card (`POST /data/user/music/upload`, audio types only, at most 64 MB per file). |
| `delete_file` / `delete_folder` | Remove one file or everything below a folder from the card (`DELETE /data/user/music/file`). The playing file is never deleted; one folder delete removes at most 500 files. Queue rows of deleted files are dropped; stored playlists keep their entries on purpose. |
| `playlist_load` + `resume: true` | Load a stored playlist and continue where it was last played: the remembered track is selected, started and moved to the remembered position (3 seconds earlier; a track that was within 5 seconds of its end continues with the next one). Without a resume point, or without `resume`, it only loads. |
| `playlist_get` / `playlist_update` | Read a stored playlist's tracks; re-order or remove rows by position (`order` = the current positions to keep, in their new order). The active queue is untouched. |
| `playlists_from_folders` | Create a stored playlist for every card folder that directly holds music (name = folder path, nested folders read `Parent / Child`; files in natural order). Folders named like a disc (`CD 1`, `Disc 02`, `Teil 3`, `Part 4`) join the playlist of the folder above, in disc order - a multi-CD audiobook becomes **one** playlist. Names are cut to the 64-character limit (the folder's own name is preferred over a truncated path). Existing playlists are skipped, deleted ones are built again, the queue is untouched. |

* **Import tab**: choose or drop audio files, an M3U **or a whole folder**, optionally name a playlist. A dropped (or chosen) folder names the playlist after itself (unless a name was typed), keeps the files in natural order and, like an M3U, uploads only what is not on the card yet. *Import only* uploads them; *Import & build playlist* uploads, replaces the queue with the imported tracks and saves it as a playlist (`queue_clear` + `queue_add` + `playlist_save_as`). The card checks the free space (`library.capacity_*`) before uploading anything.
* **M3U**: drop an `.m3u`/`.m3u8` **together with the audio files**. A browser can only read files the user hands over, so only the *file name* of each line is used (folders and drive letters are ignored). Each line is compared with the card by name and size: already on the card - added to the playlist without uploading; missing - uploaded from the chosen files; same name but different size - the version on the card is used and the row is marked `*`; neither - reported as missing; `http(s)` lines - listed but not fetched. A summary is shown first and nothing is uploaded until the user confirms. The playlist keeps the M3U's order and is named after the file.
* **Scan folders & create playlists** (Playlists tab) builds a playlist for every card folder that holds music, for card contents that did not arrive through the card (card reader, the device's own page). Existing playlists stay untouched; a deleted one is simply built again on the next scan.
* **Sound presets** (player options row): *Classical / Surround / Vocal* are the device's own presets (`SET_SOUND_EFFECT`), shown like an equalizer switch; the active one follows what the device reports (`sound_effect`). It is an equalizer replacement - the card has no access to the audio itself. The device's own play mode (`sequence`, `list_loop`, `single_loop`, `shuffle`) is deliberately **not** offered: HASE runs the queue, and a device loop mode would fight it. The device's dance setting is not part of the API (see the feedback document).
* **Resume** (Playlists tab): while a stored playlist is loaded, HASE remembers per playlist which track was playing and at which position - saved when the track changes, every 10 seconds of progress and exactly on a pause, in the runtime's store, so it survives a restart. A playlist row shows `▶ Track 5 · 12:34`; the **Resume** button (and a double click on such a row) loads the playlist and continues there. *Load* keeps loading without playing. Deleting a playlist forgets its resume point. Seeking is best effort: if the device is not ready, the track starts from its beginning.
* **Start or resume from a button or a tag**: the service `hase.play_playlist` (`config_entry_id`, `playlist`, optional `resume`, default on) loads a stored playlist and plays it - from its remembered spot, or from the first track if it was never played (or with `resume: false`). The music screen must be open on the device; otherwise the automation trace shows why it was refused, and an unknown playlist name is a clear error. A dashboard button:

  ```yaml
  type: button
  name: Audiobook
  tap_action:
    action: perform-action
    perform_action: hase.play_playlist
    data:
      config_entry_id: <the device's config entry id>
      playlist: "Walter Moers - Die 13½ Leben des Käpt'n Blaubär"
  ```

  The same call as an automation action with a tag (RFID/NFC) trigger gives a "scan the figure, continue the story" setup:

  ```yaml
  triggers:
    - trigger: tag
      tag_id: <your tag id>
  actions:
    - action: hase.play_playlist
      data:
        config_entry_id: <the device's config entry id>
        playlist: "Walter Moers - Die 13½ Leben des Käpt'n Blaubär"
  ```
* **Light scene vs. ambient colour**: the scene picker (*Lichtszene*) shows the scene the device has applied. An ambient colour written from here lies on top of it, so while it is shown the picker is **empty** until a scene is chosen again (after a restart it shows the applied scene, because the device cannot report the ambient colour back).
* **Playlist editor** (Playlists tab): selecting a playlist lists its tracks with move up/down and remove, and *Save as M3U* downloads the playlist in a form the importer reads back. A folder in the Library tab has a *Save folder as playlist* button for files that reached the card by other means.
* **TF card swap**: track uris carry the card's id (`/storage/<id>/...`). When a stored playlist is shown or loaded, tracks whose uri is not on the current card are matched by their path below `code27/music` instead; tracks without a match stay as they are and simply cannot be played.
* **Device limits worth knowing**: the device stores every upload in the **root** of its music folder - a folder name in the file name is ignored, so a dropped folder becomes a *playlist*, not a folder on the card. File names are sent unchanged (an earlier version let the HTTP client percent-encode them, which showed up as `%20` on the card; the card now displays such names decoded). The device has no file download and no playlist concept, so HASE cannot read an `.m3u` that was put on the card by other means, and the device re-indexes its file list lazily (observed: not while the music screen is closed). HASE therefore remembers its own uploads until the device lists them, and forgets them when the card changes.

The full track-end contract is `TRACK_END_CONTRACT` in `providers/code27/music.py` and is shipped to the card.

The TF-card inventory has two sources: the `music_storage` WebSocket push and the REST read of `GET /data/user/music/storage`, which is part of every poll. Both are merged by the same precedence rule (newest valid inventory wins), so an update cycle without an inventory **never** deletes the last known one, and the browser is populated after the first poll - including directly after a Home Assistant restart. Until any inventory has arrived at all, the browser says "waiting for the music inventory" instead of pretending the library is empty.

The card stores no library data of its own: a subscription push deliberately does not repeat the folder listing, it reports through `library.revision` whether the inventory changed. A message without a listing means "unchanged" - never "empty" - and the backend stays the authority.

A **Satellite** is an experimental Conversation subentry attached to one CODE27 device. It tries its local Spellbook first and falls back to that device's character backend on no match. Reply correlation during concurrent physical-device chat has not been verified; do not use it where a wrong or delayed reply matters.

Known limitations include no capability-based feature gating, no proven dynamic voice list endpoint, backend-only image chat, read-only diary support, and some vacuum/device profiles that are examples rather than physically verified integrations. The classified internal CODE27 reports live in the internal index (`../../internal/INDEX.md`, a maintainer document that is not part of the public repository).

## Firmware 1.6.0: Codie tools, device controls, events

Only firmware 1.6.0 and newer is supported; the pre-1.6 fallbacks are gone.

**Codie tool gateway.** HASE registers tools with the device and Codie's own model calls them over the device's tool WebSocket - HASE no longer reads the chat, uses no codeword and cleans no history. Two device options control it: *Conversation agent for Codie's sentences* adds `home_command`, *Codie tool bridge* (one MCP Bridge) adds `spell_cast` and `spell_list`. Leave both empty and no tools are registered.

| Tool | Purpose |
| --- | --- |
| `home_command` | The user's sentence, unchanged, goes to the chosen conversation agent: exact and fuzzy trigger matching, area policy and clarification questions of a HASE Spellbooker. A question comes back as `QUESTION: ...`; Codie asks the user and calls again with the answer. Sentences without a Spellbook trigger fall back to Home Assistant's normal Assist. |
| `spell_cast` | Cast a Spell: `spell` (id) and `args` (JSON object as a string). Runs through the same projection, area policy, Assist exposure and generation lease as MCP. |
| `spell_list` | The Bridge's full Spell catalog with argument names. |

The device allows 16 tools, 8 KiB per schema and about 512 characters per description; `spell_cast` inlines as many catalog lines as fit, `spell_list` has the rest. `home_command` has no catalog limit because the host does the matching, so it is the tool for everyday sentences.

**Device controls.** *Display brightness* (number, 0-100), *Display* (switch) and *Device mode* (LIVE / MUSIC) are entities of the device and have their own *Device* section on the dashboard card (`show_device`); chat turns can carry `interrupt`, `retention` and `tools` (`create_chat_turn`).

**Events.** The integration also listens to the device's status channel `/v1/ws/events`; a chat-history event triggers an immediate chat poll instead of waiting for the 3 s timer, which stays as the fallback.

**Standalone CODE27.** Everything above except the HASE-backed dispatcher is shared device code and also runs in the standalone CODE27 integration (`scripts/sync_code27.py` regenerates it). Standalone alone registers no tools; HASE supplies the dispatcher, also for a device owned by a standalone entry (see `docs/code27/CODE27-SPLIT-EXTERNAL-HUB-CONTRACT-0.1.md`).
