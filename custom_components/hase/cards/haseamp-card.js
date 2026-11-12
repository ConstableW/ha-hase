/* HASEAMP — the Winamp-inspired music card.
 *
 * Vanilla web component, no build step, no dependencies, no third-party
 * assets: every pixel, glyph and colour here is HASEAMP's own design. It is
 * inspired by the compact 90s software-player look (LCD readout, small
 * transport buttons, playlist-style rows) but copies no skin, graphic or logo.
 *
 * The card is provider-neutral. It is not CODE27's card and knows nothing about
 * a CODE27 device, its paths, its status vocabulary or its command payloads:
 *   - it holds no queue and no playlist state itself (only which row the user
 *     clicked last), so reloading the dashboard cannot destroy anything,
 *   - every action goes through the WebSocket namespace of the frontend
 *     provider selected in `PROVIDER_TRANSPORTS` below — the only place in this
 *     file where anything provider-specific may live,
 *   - queue autoadvance runs in the Home Assistant backend, so no browser has
 *     to stay open,
 *   - what it may offer is driven by the backend's capability flags
 *     (`features.*`), never by hard-wired knowledge of one device.
 */

/* ==== frontend provider boundary (start) ==== */
/* CODE27 owner resolver: generated from scripts/code27_owner_shared.js (start) */
// Shared, registry-based owner resolution for the CODE27 dashboard cards.
// A device is owned by the integration that created its entities. HASE's
// external binding never changes that owner or creates another device.
const OWNERS = new Set(["hase", "code27"]);

function code27Devices(hass) {
  const entities = hass?.entities || {};
  const devices = hass?.devices || {};
  const found = [];
  for (const [entityId, entity] of Object.entries(entities)) {
    if (entity.translation_key !== "active_character" || !entity.device_id) continue;
    const device = devices[entity.device_id];
    if (!device) continue;
    const owners = (device.identifiers || []).map(([domain]) => domain).filter((domain) => OWNERS.has(domain));
    if (owners.length !== 1) continue;
    const entryIds = device.config_entries || [];
    const entryId = entity.config_entry_id || (entryIds.length === 1 ? entryIds[0] : null);
    if (!entryId || !entryIds.includes(entryId)) continue;
    const prefix = entityId.replace(/^sensor\./, "").replace(/(_[^_]+){2}$/, "");
    found.push({
      prefix, device_id: entity.device_id, config_entry_id: entryId,
      domain: owners[0], name: device.name_by_user || device.name || prefix,
    });
  }
  return found;
}

function code27Owner(hass, selector = {}) {
  const matches = code27Devices(hass).filter((device) =>
    (selector.device_id && device.device_id === selector.device_id) ||
    (!selector.device_id && selector.config_entry_id && device.config_entry_id === selector.config_entry_id) ||
    (!selector.device_id && !selector.config_entry_id && selector.device_prefix && device.prefix === selector.device_prefix)
  );
  return matches.length === 1 ? matches[0] : null;
}

// Music runtimes expose their own targets. Probe both namespaces and route only
// to the runtime that advertises the selected entry; no HASE proxy is created.
async function code27MusicTargets(hass, transports) {
  const results = await Promise.all(Object.entries(transports).map(async ([provider, transport]) => {
    try {
      const snapshot = await hass.callWS({ type: transport.command, operation: "snapshot", data: {} });
      return (snapshot.targets || []).map((target) => ({ ...target, provider }));
    } catch (_) {
      return [];
    }
  }));
  return results.flat();
}

async function code27MusicOwner(hass, target, transports) {
  if (target) {
    const owner = code27Devices(hass).find((device) => device.config_entry_id === target);
    if (owner) {
      const provider = owner.domain === "hase" ? "code27" : "code27_standalone";
      if (transports[provider]) return { id: target, provider };
    }
  }
  const targets = await code27MusicTargets(hass, transports);
  const matches = target ? targets.filter((item) => item.id === target) : targets;
  if (matches.length !== 1) {
    throw new Error(matches.length ? "Select a CODE27 target" : "CODE27 music target unavailable");
  }
  return matches[0];
}

const musicOwnerFor = code27MusicOwner;
const musicTargetsFor = code27MusicTargets;
/* CODE27 owner resolver: generated from scripts/code27_owner_shared.js (end) */
/* Frontend provider transports.
 *
 * This is the card's provider boundary and the *only* place in this file that
 * may know a provider's namespace or its payload quirks: which WebSocket
 * command carries the operations, which subscription pushes snapshots, which
 * reason code means "no device of this provider is loaded", and what to call
 * the device in user-facing hints. Everything outside these markers has to stay
 * provider-neutral - the contract test in `tests/test_code27_music.py` checks
 * exactly that on the comment-stripped source.
 *
 * It is deliberately a frontend table and not a contract: there is no HASE core
 * module, no Python interface and no shared music model behind it. A provider
 * that wants to feed this card brings its own backend, its own namespace and
 * its own payload, and registers them here; a provider without music features
 * simply has no entry and therefore nothing this card can talk to. */
const PROVIDER_TRANSPORTS = {
  code27: {
    command: "hase/code27/music",
    subscribe: "hase/code27/music/subscribe",
    // Reason code the backend uses when no CODE27 device is loaded yet.
    noDeviceReason: "no_code27_device",
    // How this provider's device is named in user-facing hints.
    deviceLabel: "CODE27",
  },
  code27_standalone: {
    command: "code27/music",
    subscribe: "code27/music/subscribe",
    noDeviceReason: "no_code27_device",
    deviceLabel: "CODE27",
  },
};

/* The ambient light of the selected device as [r, g, b], or null when it is
 * off, unknown or not found. Read-only: the card never writes to the light. */
function deviceLightRgb(hass, selector) {
  if (!hass || !hass.entities || !hass.states) return null;
  const owner = code27Owner(hass, selector);
  if (!owner) return null;
  for (const [entityId, entity] of Object.entries(hass.entities)) {
    if (entity.device_id !== owner.device_id || entity.translation_key !== "preview_light") continue;
    const state = hass.states[entityId];
    const rgb = state && state.state === "on" && state.attributes && state.attributes.rgb_color;
    if (Array.isArray(rgb) && rgb.length === 3 && rgb.every((v) => Number.isFinite(Number(v)))) {
      return rgb.map(Number);
    }
  }
  return null;
}

/* Explicit provider remains available for other transports. CODE27's two
 * owners are selected from the targets advertised by their own runtimes. */
const DEFAULT_PROVIDER = "code27";

function transportFor(provider) {
  return PROVIDER_TRANSPORTS[provider] || PROVIDER_TRANSPORTS[DEFAULT_PROVIDER];
}
function isOwnerAwareProvider(provider) {
  return provider === "code27" || provider === "code27_standalone";
}
/* ==== frontend provider boundary (end) ==== */

/* Every `data-act` that may reach the backend, mapped to the WebSocket
 * operation it sends. This is the card's half of the frontend/backend
 * contract: the backend schema only accepts these operations.
 *
 * An action missing here cannot be sent at all (`_act` refuses it), which is
 * what keeps UI-only interactions out of the operation namespace. Before this
 * table existed, `_onClick` forwarded any `data-act` it found, so simply
 * opening the playlist dropdown sent `operation: "playlist-pick"` and Home
 * Assistant rejected the request. */
const ACTION_OPERATIONS = {
  browse: "browse",
  enqueue_folder: "enqueue_folder",
  queue_add: "queue_add",
  queue_play: "queue_play",
  queue_remove: "queue_remove",
  queue_move: "queue_move",
  queue_clear: "queue_clear",
  transport: "transport",
  // The ↺ button: play the current queue row again from the start. The progress
  // bar is deliberately *not* in this table - it is a position control whose
  // click position becomes a `transport` seek (see the `seekbar` case in
  // `_onClick`), so it needs no operation name of its own.
  restart: "transport",
  volume: "set_volume",
  set_shuffle: "set_shuffle",
  set_repeat: "set_repeat",
  set_consume_played: "set_consume_played",
  set_sound_effect: "set_sound_effect",
  playlist_create: "playlist_create",
  playlist_load: "playlist_load",
  playlist_save: "playlist_save",
  playlist_save_as: "playlist_save_as",
  playlist_rename: "playlist_rename",
  playlist_delete: "playlist_delete",
  playlist_update: "playlist_update",
  delete_file: "delete_file",
  delete_folder: "delete_folder",
};

/* Card-only interactions: they change what the card shows (which row is
 * marked, which playlist is selected, what name is typed) and must never
 * produce a WebSocket operation. Deliberately disjoint from
 * ACTION_OPERATIONS. */
const UI_ONLY_ACTIONS = new Set([
  "browse-select",
  "queue-select",
  "playlist-pick",
  "playlist-name",
  "confirm-yes",
  "confirm-no",
  "tab",
  "pl-select",
  "import-files",
  "import-name",
  "import-run",
  "import-clear",
  "delete-ask",
  "delete-yes",
  "delete-no",
  "pl-export",
  "folder-playlist",
  "folders-scan",
]);

/* Form controls own their value through `change`/`input`. A click on them is
 * just focus/dropdown opening and must not be treated as an action, or
 * selecting an option would fire a request. */
const FORM_CONTROL_TAGS = new Set(["INPUT", "SELECT", "TEXTAREA", "OPTION"]);

/* Double-click shortcuts (`data-dbl`): they reuse a real operation from
 * ACTION_OPERATIONS and are dispatched through the same `_act` choke point. */
const DOUBLE_CLICK_ACTIONS = new Set(["queue_play", "playlist_load"]);

/* Import goes up in pieces because one WebSocket message is size-limited. Raw
 * bytes per piece; base64 makes that roughly a third bigger on the wire. */
const IMPORT_CHUNK_BYTES = 1024 * 1024;

/* The file names an M3U/M3U8 playlist refers to, in order. Paths in the file
 * are meaningless to a browser (it can only read files the user hands over), so
 * only the file name is kept; http(s) lines cannot be fetched and are listed
 * separately. */
function parseM3u(text) {
  const entries = [];
  for (const rawLine of String(text || "").replace(/^﻿/, "").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    if (/^https?:[/][/]/i.test(line)) {
      entries.push({ raw: line, name: "", url: true });
      continue;
    }
    let path = line.replace(/^file:[/][/][/]?/i, "");
    if (path !== line) {
      try { path = decodeURIComponent(path); } catch (_) { /* keep as written */ }
    }
    const name = path.split(/[\\/]/).pop();
    if (name) entries.push({ raw: line, name, url: false });
  }
  return entries;
}

function fmtBytes(bytes) {
  if (!Number.isFinite(bytes)) return "-";
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(0, Math.round(bytes / 1024))} KB`;
}

/* The M3U text for a list of tracks: one file name per line, which is exactly
 * what the card's own M3U import reads back. */
function buildM3u(tracks) {
  const lines = ["#EXTM3U"];
  for (const track of tracks) {
    const name = String(track.uri || "").split(/[:/]/).pop();
    if (!name) continue;
    const seconds = track.duration > 0 ? Math.round(track.duration) : -1;
    lines.push(`#EXTINF:${seconds},${track.title || name}`, name);
  }
  return lines.join("\r\n") + "\r\n";
}

/* File names that arrived percent-encoded (an upload that encoded the name
 * instead of sending it) are shown decoded. Display only - the stored name and
 * every comparison stay exactly as they are. */
function nice(text) {
  const value = String(text == null ? "" : text);
  if (!/%[0-9a-f]{2}/i.test(value)) return value;
  try {
    return decodeURIComponent(value);
  } catch (_) {
    return value;
  }
}

const AUDIO_NAME = /\.(mp3|wav|flac|ogg|m4a|aac)$/i;

/* Walk the entries of a drop (files and folders, folders recursively) and
 * return the audio/playlist files plus the name of the dropped folder. */
async function readDroppedEntries(entries) {
  const files = [];
  let folder = null;
  const readAll = (reader) => new Promise((resolve, reject) => {
    const all = [];
    const next = () => reader.readEntries((batch) => {
      if (!batch.length) resolve(all);
      else { all.push(...batch); next(); }
    }, reject);
    next();
  });
  const walk = async (entry, top) => {
    if (entry.isFile) {
      const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
      files.push(file);
    } else if (entry.isDirectory) {
      if (top && folder === null) folder = entry.name;
      for (const child of await readAll(entry.createReader())) await walk(child, false);
    }
  };
  for (const entry of entries) await walk(entry, true);
  return { files, folder };
}

function isPlaylistFile(file) {
  return /\.m3u8?$/i.test((file && file.name) || "");
}

function toBase64(bytes) {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode.apply(null, bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

/* Fallback refresh interval used only when the WebSocket subscription is not
 * available. It is a pull triggered by Home Assistant's own `hass` updates,
 * never a timer that drives playback logic. */
const FALLBACK_REFRESH_MS = 2000;
/* Display-only progress interpolation while the backend reports "playing".
 * This never advances the queue — the backend does that from real playback
 * state transitions. */
const PROGRESS_TICK_MS = 1000;

const STRINGS = {
  de: {
    title: "HASEAMP",
    no_device: "Kein {device}-Gerät geladen.",
    no_device_hint:
      "Richte die HASE-Integration mit einem {device}-Gerät ein, dann verbindet sich HASEAMP automatisch.",
    device_unavailable: "Gerät nicht erreichbar - Queue und Playlists bleiben nutzbar.",
    load_error: "HASEAMP konnte die Anfrage nicht ausführen.",
    lcd_idle: "Kein Track ausgewählt",
    selected: "Ausgewählt",
    browser: "Musik",
    browser_loading: "Lade Ordner…",
    browser_waiting: "Warte auf das Musik-Inventar des Geräts…",
    browser_empty: "Keine Musik gefunden.",
    browser_up: "Übergeordneter Ordner",
    browser_root: "TF-Karte",
    browser_unavailable: "Medienbrowser steht für dieses Gerät nicht zur Verfügung.",
    add_folder: "Ordner in Queue",
    add_track: "In Queue",
    play_now: "Jetzt spielen",
    queue: "aktive Queue",
    queue_empty: "Die Queue ist leer - füge Musik aus dem Browser hinzu.",
    queue_count: "Titel",
    queue_clear: "Queue leeren",
    queue_total: "Gesamt",
    playlists: "Playlists",
    playlists_empty: "Noch keine gespeicherten Playlists.",
    playlists_none: "— keine —",
    playlist_name: "Name",
    playlist_new: "Neu",
    playlist_load: "Laden",
    playlist_save: "Queue speichern",
    playlist_save_as: "Speichern als",
    playlist_rename: "Umbenennen",
    playlist_delete: "Löschen",
    playlist_create_from_queue: "Aus Queue anlegen",
    playlist_loaded: "Geladen",
    playlist_overwrite: "„{name}“ existiert bereits. Überschreiben?",
    overwrite: "Überschreiben",
    cancel: "Abbrechen",
    prev: "Zurück",
    play: "Wiedergabe",
    pause: "Pause",
    stop: "Stopp",
    next: "Weiter",
    volume: "Lautstärke",
    source: "Quelle",
    shuffle: "Shuffle",
    repeat: "Repeat",
    repeat_off: "aus",
    repeat_all: "alle",
    repeat_one: "eins",
    consume: "Gespielte entfernen",
    consume_hint:
      "Aus: gespielte Titel bleiben in der Queue und werden nur markiert. An: sie verlassen die Queue beim Weiterspielen.",
    transport: "Wiedergabe",
    options: "Optionen",
    seek_position: "Klick spult an diese Stelle.",
    seek_restart: "Klick startet den Titel neu - dieses Gerät kennt kein Spulen.",
    seek_unknown_duration: "Das Gerät meldet keine Titellänge - nur die Laufzeit läuft mit.",
    restart: "Titel neu starten",
    stop_emulated: "Stoppt die HASE-Queue und pausiert das Gerät (kein Geräte-STOP).",
    vu_hint: "Dekorative Wiedergabe-Anzeige - HASEAMP hat keine Audiospektrum-Daten.",
    auto_advance: "Auto-Advance",
    auto_advance_gap:
      "Das Gerät meldet kein Track-Ende - HASE erkennt es am Statuswechsel der Wiedergabe.",
    move_up: "Nach oben",
    move_down: "Nach unten",
    remove: "Entfernen",
    unavailable_feature: "Dieses Gerät unterstützt die Funktion nicht.",
    music_mode_off: "MusicMode ist off!",
    music_mode_hint:
      "Öffne die Musik-Ansicht am Gerät - bis dahin nimmt es keine Wiedergabe-Befehle an.",
    tab_queue: "Queue",
    tab_library: "Bibliothek",
    tab_lists: "Playlists",
    tab_import: "Import",
    pl_manager: "Playlist-Manager",
    pl_select_hint: "Klick wählt aus, Doppelklick lädt die Playlist in die Queue.",
    pl_tracks: "Titel",
    pl_loaded: "geladen",
    queue_dbl_hint: "Doppelklick spielt den Titel.",
    import_title: "Dateien importieren & Playlist bauen",
    import_drop: "Audiodateien, eine M3U oder einen Ordner hierher ziehen - oder klicken",
    import_pick_folder: "Ordner wählen…",
    import_folder_hint: "Ordner „{name}“: Der Ordnername wird zum Playlist-Namen.",
    import_exists: "Eine Playlist „{name}“ gibt es schon - wähle einen anderen Namen.",
    import_pick: "Dateien wählen…",
    import_name: "Name der neuen Playlist",
    import_build: "Importieren & Playlist bauen",
    import_only: "Nur importieren",
    import_build_hint:
      "Lädt die Dateien auf die TF-Karte. „Playlist bauen“ ersetzt danach die Queue durch die importierten Titel und speichert sie als Playlist.",
    import_need_name: "Gib der neuen Playlist einen Namen.",
    import_no_files: "Keine Dateien gewählt.",
    import_done: "Fertig: {count} Titel importiert.",
    import_built: "Fertig: {count} Titel importiert, Playlist „{name}“ gespeichert.",
    import_state_wait: "wartet",
    import_state_up: "lädt",
    import_state_ok: "ok",
    import_state_err: "Fehler",
    import_clear: "Liste leeren",
    plan_summary: "{card} schon auf der Karte · {upload} werden hochgeladen · {missing} fehlen · {url} Web-Links (nicht ladbar)",
    plan_size_differs: "{count} mit anderer Größe - die Version auf der Karte wird verwendet.",
    plan_reading: "Lese M3U und vergleiche mit der TF-Karte…",
    plan_no_inventory: "Das Gerät hat noch kein Musik-Inventar gemeldet - bitte kurz warten und die Dateien erneut wählen.",
    plan_nothing: "In der M3U ist nichts Verwendbares: keine Datei auf der Karte oder unter den gewählten.",
    plan_state_card: "auf der Karte",
    plan_state_upload: "wird hochgeladen",
    plan_state_missing: "fehlt",
    plan_state_url: "Web-Link",
    sound: "Klang",
    sound_hint: "Klangvoreinstellung des Geräts (Ersatz für einen Equalizer).",
    sound_classical: "Klassik",
    sound_surround: "Surround",
    sound_vocal: "Stimme",
    pl_resume: "Fortsetzen",
    pl_resume_hint: "Lädt die Playlist und spielt bei Titel {n} ({title}) ab {time} weiter.",
    pl_resume_none: "Noch nicht abgespielt - nichts zum Fortsetzen.",
    pl_resume_row: "▶ Titel {n} · {time}",
    scan_playlists: "Ordner scannen & Playlists anlegen",
    scan_done: "{created} Playlists angelegt, {skipped} gab es schon.",
    scan_hint: "Legt für jeden Ordner mit Musik eine Playlist an. Bestehende bleiben unberührt, gelöschte entstehen neu.",
    deleting: "Lösche…",
    delete_failed: "Nicht gelöscht:",
    ok: "OK",
    folder_playlist: "Ordner als Playlist speichern",
    folder_playlist_done: "Playlist „{name}“ aus dem Ordner gespeichert.",
    storage: "Speicher",
    storage_free: "frei",
    storage_full: "Nicht genug Platz auf der TF-Karte: {need} nötig, {free} frei.",
    delete_file: "Von der TF-Karte löschen",
    delete_folder: "Ordner von der TF-Karte löschen",
    delete_ask_file: "„{name}“ endgültig von der TF-Karte löschen?",
    delete_ask_folder: "Ordner „{name}“ mit allen Dateien endgültig von der TF-Karte löschen?",
    delete_hint: "Playlists behalten den Eintrag; die Datei kann dann nur nicht mehr gespielt werden.",
    delete_yes: "Löschen",
    pl_edit_empty: "Wähle eine Playlist, um ihre Titel zu bearbeiten.",
    pl_export: "Als M3U speichern",
    pl_track_up: "Nach oben",
    pl_track_down: "Nach unten",
    pl_track_remove: "Aus Playlist entfernen",
    pl_loading: "Lade Titel…",
  },
  en: {
    title: "HASEAMP",
    no_device: "No {device} device loaded.",
    no_device_hint:
      "Set up the HASE integration with a {device} device and HASEAMP connects automatically.",
    device_unavailable: "Device unreachable - queue and playlists stay usable.",
    load_error: "HASEAMP could not complete the request.",
    lcd_idle: "No track selected",
    selected: "Selected",
    browser: "Music",
    browser_loading: "Loading folder…",
    browser_waiting: "Waiting for the device's music inventory…",
    browser_empty: "No music found.",
    browser_up: "Parent folder",
    browser_root: "TF card",
    browser_unavailable: "The media browser is not available for this device.",
    add_folder: "Add folder to queue",
    add_track: "Add to queue",
    play_now: "Play now",
    queue: "active queue",
    queue_empty: "The queue is empty - add music from the browser.",
    queue_count: "tracks",
    queue_clear: "Clear queue",
    queue_total: "Total",
    playlists: "Playlists",
    playlists_empty: "No stored playlists yet.",
    playlists_none: "— none —",
    playlist_name: "Name",
    playlist_new: "New",
    playlist_load: "Load",
    playlist_save: "Save queue",
    playlist_save_as: "Save as",
    playlist_rename: "Rename",
    playlist_delete: "Delete",
    playlist_create_from_queue: "Create from queue",
    playlist_loaded: "Loaded",
    playlist_overwrite: "“{name}” already exists. Overwrite?",
    overwrite: "Overwrite",
    cancel: "Cancel",
    prev: "Previous",
    play: "Play",
    pause: "Pause",
    stop: "Stop",
    next: "Next",
    volume: "Volume",
    source: "Source",
    shuffle: "Shuffle",
    repeat: "Repeat",
    repeat_off: "off",
    repeat_all: "all",
    repeat_one: "one",
    consume: "Remove played",
    consume_hint:
      "Off: played tracks stay in the queue and are only marked. On: they leave the queue as playback moves on.",
    transport: "Playback",
    options: "Options",
    seek_position: "Click seeks to this position.",
    seek_restart: "Click restarts the track - this device has no seek.",
    seek_unknown_duration: "The device reports no track length - only elapsed time advances.",
    restart: "Restart track",
    stop_emulated: "Stops the HASE queue and pauses the device (no device STOP).",
    vu_hint: "Decorative playback animation - HASEAMP has no audio spectrum data.",
    auto_advance: "Auto-advance",
    auto_advance_gap:
      "The device reports no track end - HASE derives it from the playback status changing.",
    move_up: "Move up",
    move_down: "Move down",
    remove: "Remove",
    unavailable_feature: "This device does not support the feature.",
    music_mode_off: "MusicMode is off!",
    music_mode_hint:
      "Open the music screen on the device - until then it accepts no playback commands.",
    tab_queue: "Queue",
    tab_library: "Library",
    tab_lists: "Playlists",
    tab_import: "Import",
    pl_manager: "Playlist manager",
    pl_select_hint: "Click selects, double-click loads the playlist into the queue.",
    pl_tracks: "tracks",
    pl_loaded: "loaded",
    queue_dbl_hint: "Double-click plays the track.",
    import_title: "Import files & build playlist",
    import_drop: "Drop audio files, an M3U or a folder here - or click",
    import_pick_folder: "Choose folder…",
    import_folder_hint: "Folder “{name}”: the folder name becomes the playlist name.",
    import_exists: "A playlist “{name}” already exists - pick another name.",
    import_pick: "Choose files…",
    import_name: "Name of the new playlist",
    import_build: "Import & build playlist",
    import_only: "Import only",
    import_build_hint:
      "Uploads the files to the TF card. “Build playlist” then replaces the queue with the imported tracks and saves it as a playlist.",
    import_need_name: "Give the new playlist a name.",
    import_no_files: "No files chosen.",
    import_done: "Done: {count} tracks imported.",
    import_built: "Done: {count} tracks imported, playlist “{name}” saved.",
    import_state_wait: "waiting",
    import_state_up: "uploading",
    import_state_ok: "ok",
    import_state_err: "error",
    import_clear: "Clear list",
    plan_summary: "{card} already on the card · {upload} will be uploaded · {missing} missing · {url} web links (cannot be fetched)",
    plan_size_differs: "{count} differ in size - the version on the card is used.",
    plan_reading: "Reading the M3U and comparing with the TF card…",
    plan_no_inventory: "The device has not reported its music inventory yet - wait a moment and choose the files again.",
    plan_nothing: "Nothing usable in the M3U: no file on the card or among the chosen ones.",
    plan_state_card: "on the card",
    plan_state_upload: "will upload",
    plan_state_missing: "missing",
    plan_state_url: "web link",
    sound: "Sound",
    sound_hint: "The device's sound preset (an equalizer replacement).",
    sound_classical: "Classical",
    sound_surround: "Surround",
    sound_vocal: "Vocal",
    pl_resume: "Resume",
    pl_resume_hint: "Loads the playlist and continues at track {n} ({title}) from {time}.",
    pl_resume_none: "Not played yet - nothing to resume.",
    pl_resume_row: "▶ Track {n} · {time}",
    scan_playlists: "Scan folders & create playlists",
    scan_done: "{created} playlists created, {skipped} already existed.",
    scan_hint: "Creates a playlist for every folder that holds music. Existing ones stay untouched, deleted ones are built again.",
    deleting: "Deleting…",
    delete_failed: "Not deleted:",
    ok: "OK",
    folder_playlist: "Save folder as playlist",
    folder_playlist_done: "Playlist “{name}” saved from the folder.",
    storage: "Storage",
    storage_free: "free",
    storage_full: "Not enough room on the TF card: {need} needed, {free} free.",
    delete_file: "Delete from the TF card",
    delete_folder: "Delete folder from the TF card",
    delete_ask_file: "Delete “{name}” from the TF card for good?",
    delete_ask_folder: "Delete folder “{name}” and all its files from the TF card for good?",
    delete_hint: "Playlists keep the entry; the file just can no longer be played.",
    delete_yes: "Delete",
    pl_edit_empty: "Pick a playlist to edit its tracks.",
    pl_export: "Save as M3U",
    pl_track_up: "Move up",
    pl_track_down: "Move down",
    pl_track_remove: "Remove from playlist",
    pl_loading: "Loading tracks…",
  },
};

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[character]));
}

function fmtTime(seconds) {
  if (seconds == null || isNaN(seconds)) return "--:--";
  const total = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    return `${hours}:${String(minutes % 60).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
  }
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

function repeatLabel(mode, t) {
  if (mode === "all") return t("repeat_all");
  if (mode === "one") return t("repeat_one");
  return t("repeat_off");
}

class HaseAmpCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._target = this._config.target || null;
    this._name = this._config.name || null;
    this._provider = this._config.provider || DEFAULT_PROVIDER;
    this._resolvedProvider = null;
    this._resolvedTarget = null;
    this._resolvingOwner = null;
    this._show = {
      browser: this._config.show_browser !== false,
      queue: this._config.show_queue !== false,
      playlists: this._config.show_playlists !== false,
      diagnostics: this._config.show_diagnostics !== false,
      animation: this._config.show_animation !== false,
      import: this._config.show_import !== false,
      follow_light: this._config.follow_light !== false,
    };
    this._tab = "queue";
    this._importFiles = [];
    this._importName = "";
    this._importPlan = null;
    this._importNameAuto = false;
    this._deleteConfirm = null;
    this._deleteBusy = false;
    this._deleteError = "";
    this._plTracks = null;
    this._import = { busy: false, items: [], message: "", error: false };
    this._built = false;
    this._snapshot = null;
    this._error = null;
    this._selected = null;
    this._node = null;
    /* The music browser listing is kept separately from the snapshot, because
     * a subscription push deliberately does not repeat it (see `_apply`): an
     * absent listing means "unchanged", never "empty". */
    this._tree = null;
    this._treeNode = null;
    this._treeRevision = null;
    this._loadingTree = false;
    this._playlistName = "";
    this._playlistPick = "";
    //: Pending "overwrite this playlist?" question: {action, dataset, name}.
    this._confirm = null;
    this._volumeDraft = null;
    this._lastFetch = 0;
    this._fetching = false;
    this._lastPosition = null;
    this._lastPositionAt = 0;
  }

  static getConfigElement() {
    return document.createElement("haseamp-card-editor");
  }

  static getStubConfig() {
    return {};
  }

  getCardSize() {
    return 14;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) {
      this._build();
      this._built = true;
    }
    this._ensureSubscription();
    const now = Date.now();
    if (!this._subscribed && now - this._lastFetch >= FALLBACK_REFRESH_MS) {
      this._refresh();
    }
  }

  connectedCallback() {
    if (this._built) {
      this._startClock();
      this._ensureSubscription();
    }
  }

  disconnectedCallback() {
    this._subscriptionGeneration = (this._subscriptionGeneration || 0) + 1;
    this._stopClock();
    if (this._unsubscribe) {
      try {
        this._unsubscribe();
      } catch (err) {
        /* the connection is already gone */
      }
      this._unsubscribe = null;
    }
    this._subscribed = false;
  }

  _startClock() {
    if (this._clock) return;
    this._clock = setInterval(() => this._tick(), PROGRESS_TICK_MS);
  }

  _stopClock() {
    if (this._clock) clearInterval(this._clock);
    this._clock = null;
  }

  _lang() {
    const language = (this._hass && this._hass.language) || "en";
    return language.toLowerCase().startsWith("de") ? "de" : "en";
  }

  _t(key) {
    return STRINGS[this._lang()][key] || STRINGS.en[key] || key;
  }

  /* A card string with the provider's device name substituted. The device name
   * belongs to the provider (`PROVIDER_TRANSPORTS`), so the card never spells
   * out a device of its own. */
  _tDevice(key) {
    return this._t(key).replace("{device}", this._transport().deviceLabel);
  }

  /* The frontend transport of the provider this card instance talks to. */
  _transport() {
    return transportFor(this._resolvedProvider || this._provider);
  }

  async _resolveOwner() {
    if (this._config.provider && !isOwnerAwareProvider(this._config.provider)) return;
    if (this._resolvedProvider && this._resolvedTarget === this._target) return;
    if (!this._resolvingOwner) {
      this._resolvingOwner = musicOwnerFor(this._hass, this._target, PROVIDER_TRANSPORTS)
        .then((target) => {
          this._resolvedProvider = target.provider;
          this._resolvedTarget = target.id;
          if (!this._target) this._target = target.id;
        })
        .finally(() => { this._resolvingOwner = null; });
    }
    await this._resolvingOwner;
  }

  // -- backend access ------------------------------------------------------

  async _call(operation, data) {
    if (!this._hass || !this._hass.callWS) throw new Error("no connection");
    await this._resolveOwner();
    return this._hass.callWS({
      type: this._transport().command,
      operation,
      data: Object.assign({ target: this._target }, data || {}),
    });
  }

  async _refresh() {
    if (this._fetching) return;
    this._fetching = true;
    this._lastFetch = Date.now();
    try {
      const snapshot = await this._call("snapshot", { node: this._node });
      this._apply(snapshot);
    } catch (err) {
      this._error = (err && err.message) || this._t("load_error");
      this._render();
    } finally {
      this._fetching = false;
    }
  }

  async _ensureSubscription() {
    if (this._subscribed || this._subscribing) return;
    const connection = this._hass && this._hass.connection;
    if (!connection || typeof connection.subscribeMessage !== "function") return;
    this._subscribing = true;
    const generation = this._subscriptionGeneration || 0;
    try {
      await this._resolveOwner();
    } catch (_) {
      this._subscribing = false;
      return;
    }
    if (generation !== (this._subscriptionGeneration || 0)) {
      this._subscribing = false;
      return;
    }
    let cancelled = false;
    this._unsubscribe = () => {
      cancelled = true;
    };
    connection
      .subscribeMessage(
        (message) => {
          if (!message || message.type !== "snapshot" || !message.snapshot) return;
          this._apply(message.snapshot);
        },
        { type: this._transport().subscribe, target: this._target }
      )
      .then((unsubscribe) => {
        if (cancelled) {
          // The card was removed from the DOM while this was in flight.
          unsubscribe();
          return;
        }
        this._unsubscribe = unsubscribe;
        this._subscribed = true;
        this._subscribing = false;
        this._startClock();
        this._refresh();
      })
      .catch(() => {
        // Older frontends without subscribeMessage, or no backend yet: the
        // `hass`-driven fallback refresh keeps the card working.
        this._subscribing = false;
      });
  }

  // -- snapshot plumbing ---------------------------------------------------

  _apply(snapshot) {
    if (!snapshot || typeof snapshot !== "object") return;
    const previousLibrary = this._snapshot && this._snapshot.library;
    this._snapshot = snapshot;
    this._error = null;
    if (!this._target && snapshot.target && snapshot.target.id) {
      this._target = snapshot.target.id;
    }
    const playback = snapshot.playback || {};
    if (playback.available && playback.state === "playing") {
      if (this._lastPosition !== playback.position) {
        this._lastPosition = playback.position;
        this._lastPositionAt = Date.now();
      }
    } else {
      this._lastPosition = playback.position != null ? playback.position : null;
      this._lastPositionAt = Date.now();
    }
    if (snapshot.queue && snapshot.queue.loaded_playlist && !this._playlistPick) {
      this._playlistPick = snapshot.queue.loaded_playlist;
    }
    /* Only an explicit `tree` replaces the listing. The subscription pushes
     * omit the key entirely (the backend says so with `library.revision`), and
     * a missing key must never be read as an empty library - that is exactly
     * what emptied the browser on a real device. */
    if (Object.prototype.hasOwnProperty.call(snapshot, "tree")) {
      this._tree = snapshot.tree || null;
      this._treeNode = this._node;
      this._treeRevision = this._libraryRevision(snapshot);
    }
    this._render();
    this._maybeReloadTree(previousLibrary);
  }

  _libraryRevision(snapshot) {
    const library = snapshot && snapshot.library;
    return library && library.revision != null ? String(library.revision) : null;
  }

  /* The backend owns the inventory revision: the card refetches its current
   * node only when that revision actually changed (or when it has no listing
   * yet), instead of polling or guessing. */
  _maybeReloadTree(previousLibrary) {
    if (!this._show.browser || this._loadingTree) return;
    const snapshot = this._snapshot || {};
    const features = snapshot.features || {};
    if (features.browser !== true) return;
    const library = snapshot.library;
    if (!library || !library.ready) return;
    const revision = this._libraryRevision(snapshot);
    if (this._tree && this._treeNode === this._node && this._treeRevision === revision) {
      return;
    }
    if (previousLibrary && this._tree && this._treeRevision === revision) return;
    this._loadTree();
  }

  async _loadTree() {
    if (this._loadingTree) return;
    this._loadingTree = true;
    const node = this._node;
    try {
      const tree = await this._call("browse", { node });
      if (this._node !== node) return; // the user navigated while loading
      this._tree = tree || null;
      this._treeNode = node;
      this._treeRevision = this._libraryRevision(this._snapshot);
      this._renderBrowser(this._snapshot, (this._snapshot || {}).features || {});
    } catch (err) {
      this._error = (err && err.message) || this._t("load_error");
      this._render();
    } finally {
      this._loadingTree = false;
    }
  }

  _crumbTrail() {
    // Derived from the current node, so it can never go stale.
    if (!this._node || !this._node.startsWith("folder:")) return [];
    const parts = this._node.slice("folder:".length).split("/").filter(Boolean);
    const trail = [];
    let path = "";
    for (const part of parts) {
      path = path ? `${path}/${part}` : part;
      trail.push({ node: `folder:${path}`, title: part });
    }
    return trail;
  }

  _elapsed(playback) {
    if (!playback || playback.position == null) return null;
    if (playback.state !== "playing") return playback.position;
    const drift = (Date.now() - this._lastPositionAt) / 1000;
    const value = playback.position + Math.max(0, drift);
    return playback.duration != null ? Math.min(value, playback.duration) : value;
  }

  _tick() {
    const playback = (this._snapshot && this._snapshot.playback) || null;
    if (!playback || playback.state !== "playing") return;
    // Pure repaint: both the fill and the knob move, nothing is requested.
    this._paintProgress(playback);
  }

  // -- interaction ---------------------------------------------------------

  async _act(act, dataset) {
    /* The single choke point between card interactions and the backend. An
     * action without an entry in ACTION_OPERATIONS is UI-only state and is
     * refused here - never sent. This is the guard that stops a UI-only
     * `data-act` (e.g. picking a playlist in a dropdown) from reaching the
     * WebSocket schema, which only accepts the real operations. */
    const operation = ACTION_OPERATIONS[act];
    if (!operation || UI_ONLY_ACTIONS.has(act)) {
      return null;
    }
    const data = Object.assign({}, dataset || {});
    delete data.act;
    try {
      const result = await this._call(operation, this._payloadFor(act, data));
      if (result && result.items) {
        // Queue operations return the queue payload; fold it into the snapshot
        // so the UI reacts immediately instead of waiting for the push.
        this._apply(
          Object.assign({}, this._snapshot || {}, {
            queue: result,
            playlists: result.playlists || (this._snapshot || {}).playlists,
          })
        );
      } else if (act === "playlist_update" && result && result.tracks) {
        this._plTracks = { name: result.name, tracks: result.tracks };
        this._apply(
          Object.assign({}, this._snapshot || {}, { playlists: result.playlists || (this._snapshot || {}).playlists })
        );
      } else if (result && (result.playlists || result.playlist)) {
        this._apply(
          Object.assign({}, this._snapshot || {}, {
            playlists: result.playlists || (this._snapshot || {}).playlists,
            queue: result.queue || (this._snapshot || {}).queue,
          })
        );
      } else {
        await this._refresh();
      }
    } catch (err) {
      this._error = (err && err.message) || this._t("load_error");
      this._render();
    }
  }

  _payloadFor(act, data) {
    const numeric = (raw) => {
      const value = Number(raw);
      return Number.isFinite(value) ? value : undefined;
    };
    switch (act) {
      case "queue_add":
        return {
          tracks: [
            {
              uri: data.uri,
              title: data.title,
              provider: data.provider,
              duration: data.duration ? Number(data.duration) : undefined,
            },
          ],
          play: data.play === "1",
        };
      case "enqueue_folder":
        return { node: data.node, play: data.play === "1" };
      case "browse":
        return { node: data.node || null };
      case "queue_play":
        return data.item_id != null
          ? { item_id: numeric(data.item_id) }
          : { index: numeric(data.index) };
      case "queue_remove":
        return data.item_id != null
          ? { item_id: numeric(data.item_id) }
          : { index: numeric(data.index) };
      case "queue_clear":
        return {};
      case "queue_move":
        return { from_index: numeric(data.from_index), to_index: numeric(data.to_index) };
      case "transport":
        // A seek carries its target position in seconds (the unit the whole WS
        // contract uses); every other transport action is just its name.
        return data.action === "seek"
          ? { action: "seek", position: numeric(data.position) }
          : { action: data.action };
      case "restart":
        return { action: "restart" };
      case "volume":
        return { volume: numeric(data.volume) / 100 };
      case "set_shuffle":
        return { shuffle: data.shuffle === "1" };
      case "set_repeat":
        return { repeat: data.repeat };
      case "set_consume_played":
        return { consume: data.consume === "1" };
      case "set_sound_effect":
        return { effect: data.effect };
      case "playlist_create":
        return { name: this._playlistName, from_queue: data.from_queue === "1" };
      case "playlist_save":
        return { name: this._playlistPick || this._playlistName || undefined };
      case "playlist_save_as":
        return { name: this._playlistName };
      case "playlist_load":
        return data.resume === "1"
          ? { name: data.name || this._playlistPick, resume: true }
          : { name: data.name || this._playlistPick };
      case "playlist_delete":
        return { name: this._playlistPick };
      case "playlist_rename":
        return { name: this._playlistPick, new_name: this._playlistName };
      case "delete_file":
        return { uri: data.uri };
      case "delete_folder":
        return { node: data.node };
      case "playlist_update": {
        const total = this._plTracks ? this._plTracks.tracks.length : 0;
        const order = Array.from({ length: total }, (_, index) => index);
        const position = numeric(data.pos);
        if (position === undefined || position < 0 || position >= total) {
          throw new Error("HASEAMP: playlist row out of range");
        }
        if (data.op === "remove") order.splice(position, 1);
        else {
          const target = data.op === "up" ? position - 1 : position + 1;
          if (target < 0 || target >= total) return { name: this._plTracks.name, order };
          [order[position], order[target]] = [order[target], order[position]];
        }
        return { name: this._plTracks.name, order };
      }
      default:
        // Unreachable: `_act` only dispatches actions listed in
        // ACTION_OPERATIONS and every one of those has a case above. Failing
        // loudly beats sending a payload the backend would misinterpret.
        throw new Error(`HASEAMP: no payload mapping for action '${act}'`);
    }
  }

  async _onClick(event) {
    const trigger = event.target.closest("[data-act]");
    if (!trigger || !this.contains(trigger)) return;
    /* A click on a form control is focus / opening a dropdown, not an action.
     * Their value is handled in `_onChange` / `_onInput`, so nothing is sent
     * from here - this is the guard for the playlist selector, the playlist
     * name field and the volume slider alike. */
    if (FORM_CONTROL_TAGS.has(trigger.tagName)) return;
    const dataset = Object.assign({}, trigger.dataset);
    const act = dataset.act;
    event.preventDefault();

    switch (act) {
      case "browse": {
        // Navigation replaces the listing on purpose, so the loading state is
        // shown until the backend answers for the new node.
        this._node = dataset.node || null;
        this._tree = null;
        this._treeRevision = null;
        this._renderBrowser(this._snapshot, (this._snapshot || {}).features || {});
        await this._loadTree();
        return;
      }
      case "browse-select":
      case "queue-select":
        // Selection is purely visual: it marks the row the user is looking at
        // and never becomes backend state.
        this._selected = { uri: dataset.uri || "", title: dataset.title || "" };
        this._render();
        return;
      case "tab":
        this._tab = dataset.tab || "queue";
        this._render();
        return;
      case "pl-select":
        this._playlistPick = dataset.name || "";
        this._plTracks = null;
        this._renderPlaylists(this._snapshot);
        await this._loadPlaylistTracks(this._playlistPick);
        return;
      case "pl-export":
        this._exportPlaylist();
        return;
      case "folder-playlist":
        await this._folderToPlaylist(dataset.node, dataset.title);
        return;
      case "folders-scan":
        await this._scanFolders();
        return;
      case "delete-ask":
        this._deleteConfirm = {
          kind: dataset.kind,
          action: dataset.kind === "folder" ? "delete_folder" : "delete_file",
          dataset: dataset.kind === "folder" ? { node: dataset.node } : { uri: dataset.uri },
          title: dataset.title || "",
        };
        this._renderBrowser(this._snapshot, (this._snapshot || {}).features || {});
        return;
      case "delete-no":
        if (this._deleteBusy) return;
        this._deleteConfirm = null;
        this._deleteError = "";
        this._renderBrowser(this._snapshot, (this._snapshot || {}).features || {});
        return;
      case "delete-yes": {
        const pending = this._deleteConfirm;
        if (!pending || this._deleteBusy) return;
        /* The result is shown right where the question was asked: the error
         * banner sits at the top of the card, out of sight when the user has
         * scrolled down to the library. */
        this._deleteBusy = true;
        this._deleteError = "";
        this._error = null;
        this._renderBrowser(this._snapshot, (this._snapshot || {}).features || {});
        await this._act(pending.action, pending.dataset);
        this._deleteBusy = false;
        if (this._error) {
          this._deleteError = this._error;
          this._error = null;
        } else {
          this._deleteConfirm = null;
        }
        this._render();
        return;
      }
      case "import-clear":
        if (!this._import.busy) {
          this._importFiles = [];
          this._import = { busy: false, items: [], message: "", error: false };
          this._renderImport(this._snapshot);
        }
        return;
      case "import-run":
        await this._runImport(dataset.build === "1");
        return;
      case "confirm-yes": {
        const pending = this._confirm;
        this._confirm = null;
        this._renderPlaylists(this._snapshot);
        if (pending) await this._act(pending.action, pending.dataset);
        return;
      }
      case "confirm-no":
        this._confirm = null;
        this._renderPlaylists(this._snapshot);
        return;
      case "transport": {
        /* Every transport control but the progress bar names its action in the
         * markup and is dispatched as-is. The bar is the exception: its
         * `data-action="seek"` asks for a position, and only the click itself
         * knows it - so that one action is turned into a target position here
         * before it is sent. Keeping it on the existing `transport` operation
         * (instead of inventing a `seek` operation) is what keeps the WS
         * contract unchanged; `ACTION_OPERATIONS` needs no new entry either. */
        if (dataset.action !== "seek") {
          await this._act(act, dataset);
          return;
        }
        const snapshot = this._snapshot || {};
        const features = snapshot.features || {};
        if (features.seek !== true) {
          // A device without the SEEK action keeps the old behaviour: the only
          // position control left is playing the row again from the start.
          await this._act("restart", dataset);
          return;
        }
        const position = this._seekPosition(trigger, event, snapshot.playback);
        // A click that cannot be aimed (no reported length, no measurable bar)
        // must send nothing instead of guessing a position.
        if (position == null) return;
        await this._act("transport", { action: "seek", position });
        return;
      }
      default: {
        // Overwriting an existing playlist is a question, not an error.
        const question = this._overwrite_question(act);
        if (question) {
          this._confirm = question;
          this._renderPlaylists(this._snapshot);
          return;
        }
        // `_act` refuses anything that is UI-only or not in the operation
        // table, so an unknown `data-act` can never reach the backend.
        await this._act(act, dataset);
      }
    }
  }

  async _onDoubleClick(event) {
    const trigger = event.target.closest("[data-dbl]");
    if (!trigger || !this.contains(trigger)) return;
    const act = trigger.dataset.dbl;
    if (!DOUBLE_CLICK_ACTIONS.has(act)) return;
    event.preventDefault();
    if (act === "playlist_load") this._playlistPick = trigger.dataset.name || this._playlistPick;
    await this._act(act, Object.assign({}, trigger.dataset));
  }

  _onDragOver(event) {
    if (event.target.closest && event.target.closest('[data-el="import-drop"]')) {
      event.preventDefault();
    }
  }

  _onDrop(event) {
    if (!(event.target.closest && event.target.closest('[data-el="import-drop"]'))) return;
    event.preventDefault();
    const transfer = event.dataTransfer;
    if (!transfer) return;
    // The entries must be taken synchronously: the list is invalid after the
    // handler yields.
    const entries = Array.from(transfer.items || [])
      .map((item) => (typeof item.webkitGetAsEntry === "function" ? item.webkitGetAsEntry() : null))
      .filter(Boolean);
    if (entries.some((entry) => entry.isDirectory)) {
      readDroppedEntries(entries).then(
        ({ files, folder }) => this._setImportFiles(files, folder),
        (err) => {
          this._import = { busy: false, items: [], error: true, message: (err && err.message) || this._t("load_error") };
          this._renderImport(this._snapshot);
        }
      );
      return;
    }
    if (transfer.files) this._setImportFiles(Array.from(transfer.files));
  }

  _setImportFiles(files, folder = null) {
    if (this._import.busy) return;
    const playlist = files.find(isPlaylistFile);
    const seen = new Set();
    const audio = files.filter((file) => {
      if (isPlaylistFile(file)) return false;
      if (file.type && !file.type.startsWith("audio/") && !AUDIO_NAME.test(file.name)) return false;
      const key = `${file.name}|${file.size}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    // A folder's own file order (1., 2., ... 10.) is the playlist order.
    if (folder) audio.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    this._importPlan = null;
    // A dropped folder names the playlist - unless the user already typed one.
    const folderName = folder || (files[0] && files[0].webkitRelativePath
      ? files[0].webkitRelativePath.split("/")[0] : null);
    if (folderName && (!this._importName || this._importNameAuto)) {
      this._importName = folderName;
      this._importNameAuto = true;
    }
    if (folderName && !playlist && audio.length) {
      this._importFiles = [];
      this._import = { busy: true, error: false, message: this._t("plan_reading"), items: [] };
      this._renderImport(this._snapshot);
      this._planEntries(audio.map((file) => ({ name: file.name, url: false })), audio, folderName);
      return;
    }
    if (playlist) {
      this._importFiles = [];
      this._import = { busy: true, error: false, message: this._t("plan_reading"), items: [] };
      this._renderImport(this._snapshot);
      this._planImport(playlist, audio);
      return;
    }
    this._importFiles = audio;
    this._import = {
      busy: false,
      error: false,
      message: "",
      items: this._importFiles.map((file) => ({ name: file.name, size: file.size, state: "wait", pct: 0 })),
    };
    this._renderImport(this._snapshot);
  }

  /* Compare an M3U with what the TF card already holds (file name + size) and
   * decide per line: use the card's copy, upload the chosen local file, or
   * report it missing. Nothing is uploaded until the user confirms. */
  async _planImport(playlist, audio) {
    let entries;
    try {
      entries = parseM3u(await playlist.text());
    } catch (err) {
      this._importPlan = null;
      this._import = { busy: false, items: [], error: true, message: (err && err.message) || this._t("load_error") };
      this._renderImport(this._snapshot);
      return;
    }
    await this._planEntries(entries, audio, playlist.name.replace(/\.m3u8?$/i, ""));
  }

  async _planEntries(entries, audio, title) {
    const t = (key) => this._t(key);
    try {
      const inventory = await this._call("inventory", {});
      if (inventory && inventory.ready === false) throw new Error(t("plan_no_inventory"));
      const onCard = new Map();
      // The device stores every upload in the music root (it ignores any
      // folder in the file name), so a file is matched by its name.
      for (const file of (inventory && inventory.files) || []) {
        onCard.set(String(file.name).toLowerCase(), file);
      }
      const local = new Map(audio.map((file) => [file.name.toLowerCase(), file]));
      const seen = new Set();
      const plan = [];
      let differs = 0;
      for (const entry of entries) {
        if (entry.url) { plan.push({ name: entry.raw, state: "url" }); continue; }
        const key = entry.name.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        const card = onCard.get(key);
        const file = local.get(key);
        if (card && (!file || card.size == null || card.size === file.size)) {
          plan.push({ name: entry.name, state: "card", track: card.track });
        } else if (card) {
          differs += 1;
          plan.push({ name: entry.name, state: "card", track: card.track, differs: true });
        } else if (file) {
          plan.push({ name: entry.name, state: "upload", file });
        } else {
          plan.push({ name: entry.name, state: "missing" });
        }
      }
      const count = (state) => plan.filter((row) => row.state === state).length;
      const usable = count("card") + count("upload");
      this._importPlan = { title, plan, differs };
      this._importFiles = plan.filter((row) => row.state === "upload").map((row) => row.file);
      if (!this._importName) { this._importName = title; this._importNameAuto = true; }
      this._import = {
        busy: false,
        error: usable === 0,
        items: [],
        message: usable === 0
          ? t("plan_nothing")
          : t("plan_summary")
            .replace("{card}", String(count("card")))
            .replace("{upload}", String(count("upload")))
            .replace("{missing}", String(count("missing")))
            .replace("{url}", String(count("url")))
            + (differs ? " " + t("plan_size_differs").replace("{count}", String(differs)) : ""),
      };
    } catch (err) {
      this._importPlan = null;
      this._import = { busy: false, items: [], error: true, message: (err && err.message) || t("load_error") };
    }
    this._renderImport(this._snapshot);
  }

  /* Upload the chosen files in pieces, then (optionally) turn the uploaded
   * tracks into a stored playlist. Everything here goes through the existing
   * backend operations: `import_chunk` for the bytes, `queue_clear` /
   * `queue_add` / `playlist_save_as` for the playlist - the card builds nothing
   * the backend does not already own. */
  async _runImport(build) {
    if (this._import.busy) return;
    const t = (key) => this._t(key);
    const name = this._importName.trim();
    const plan = this._importPlan ? this._importPlan.plan : null;
    const planUsable = !!plan && plan.some((row) => row.state === "card" || row.state === "upload");
    if (!this._importFiles.length && !planUsable) {
      this._import = Object.assign({}, this._import, { message: t("import_no_files"), error: true });
      this._renderImport(this._snapshot);
      return;
    }
    if (build && !name) {
      this._import = Object.assign({}, this._import, { message: t("import_need_name"), error: true });
      this._renderImport(this._snapshot);
      return;
    }
    if (build) {
      const taken = ((this._snapshot && this._snapshot.playlists) || []).some(
        (playlist) => String(playlist.name).toLowerCase() === name.toLowerCase()
      );
      if (taken) {
        this._import = Object.assign({}, this._import, {
          message: t("import_exists").replace("{name}", name),
          error: true,
        });
        this._renderImport(this._snapshot);
        return;
      }
    }
    const storage = this._storage(this._snapshot);
    const need = this._importFiles.reduce((sum, file) => sum + (file.size || 0), 0);
    if (storage && need > storage.free) {
      this._import = Object.assign({}, this._import, {
        message: t("storage_full").replace("{need}", fmtBytes(need)).replace("{free}", fmtBytes(storage.free)),
        error: true,
      });
      this._renderImport(this._snapshot);
      return;
    }
    const items = this._importFiles.map((file) => ({ name: file.name, size: file.size, state: "wait", pct: 0 }));
    this._import = { busy: true, items, message: "", error: false };
    this._renderImport(this._snapshot);
    const tracks = [];
    const uploaded = new Map();
    try {
      for (let index = 0; index < this._importFiles.length; index += 1) {
        const file = this._importFiles[index];
        const item = items[index];
        item.state = "up";
        const total = Math.max(1, Math.ceil(file.size / IMPORT_CHUNK_BYTES));
        const uploadId = `${Date.now().toString(36)}-${index}-${Math.random().toString(36).slice(2, 8)}`;
        let last = null;
        for (let piece = 0; piece < total; piece += 1) {
          const slice = file.slice(piece * IMPORT_CHUNK_BYTES, (piece + 1) * IMPORT_CHUNK_BYTES);
          const bytes = new Uint8Array(await slice.arrayBuffer());
          const chunk = {
            name: file.name,
            upload_id: uploadId,
            index: piece,
            total,
            data: toBase64(bytes),
          };
          last = await this._call("import_chunk", chunk);
          item.pct = Math.round(((piece + 1) / total) * 100);
          this._renderImport(this._snapshot);
        }
        item.state = "ok";
        for (const track of (last && last.tracks) || []) tracks.push(track);
        if (last && last.tracks && last.tracks[0]) uploaded.set(file.name.toLowerCase(), last.tracks[0]);
      }
      /* With an M3U the playlist keeps the M3U's order: tracks already on the
       * card come from the inventory, the others from this upload. */
      const ordered = plan
        ? plan
          .map((row) => (row.state === "card" ? row.track : row.state === "upload" ? uploaded.get(row.name.toLowerCase()) : null))
          .filter(Boolean)
        : tracks;
      let message = t("import_done").replace("{count}", String(ordered.length));
      if (build) {
        if (!ordered.length) throw new Error(t("import_no_files"));
        await this._call("queue_clear", {});
        await this._call("queue_add", { tracks: ordered, play: false });
        await this._call("playlist_save_as", { name });
        message = t("import_built").replace("{count}", String(ordered.length)).replace("{name}", name);
        this._playlistPick = name;
        this._importName = "";
      }
      this._importFiles = [];
      this._importPlan = null;
      this._import = { busy: false, items, message, error: false };
    } catch (err) {
      const failing = items.find((item) => item.state === "up");
      if (failing) failing.state = "err";
      this._import = {
        busy: false,
        items,
        message: (err && err.message) || t("load_error"),
        error: true,
      };
    }
    await this._refresh();
    this._renderImport(this._snapshot);
  }

  /* Where a click on the progress bar lands, in seconds into the current track.
   *
   * Returns `null` whenever the click cannot be interpreted - no reported track
   * length, no measurable bar, or a synthetic event without coordinates. An
   * unaimable seek sends nothing rather than guessing a position, which is also
   * what keeps a click on a bar that is merely *displaying* progress from
   * turning into an unintended request. */
  _seekPosition(element, event, playback) {
    const duration = Number((playback || {}).duration);
    if (!Number.isFinite(duration) || duration <= 0) return null;
    const rect =
      typeof element.getBoundingClientRect === "function"
        ? element.getBoundingClientRect()
        : null;
    const width = rect ? Number(rect.width) : 0;
    const x = Number(event && event.clientX);
    if (!(width > 0) || !Number.isFinite(x)) return null;
    const fraction = Math.min(Math.max((x - Number(rect.left)) / width, 0), 1);
    // A rect without a usable origin yields NaN, which is not a position the
    // backend could act on - returning `null` is what keeps it from being sent.
    if (!Number.isFinite(fraction)) return null;
    // Millisecond precision - the resolution the contract speaks in seconds.
    return Math.round(fraction * duration * 1000) / 1000;
  }

  /* Saving onto a playlist that already exists asks first.
   *
   * The backend keeps its strict rule (``playlist_save_as`` never overwrites,
   * ``playlist_save`` does), and the card expresses the user's "yes, overwrite"
   * by switching to the overwrite operation - so a confirmation can never turn
   * a "create" into a silent replacement.
   */
  _overwrite_question(act) {
    if (act !== "playlist_save" && act !== "playlist_save_as") return null;
    const name = (act === "playlist_save_as"
      ? this._playlistName
      : (this._playlistPick || this._playlistName || "")).trim();
    if (!name) return null;
    const snapshot = this._snapshot || {};
    const playlists = snapshot.playlists || [];
    const existing = playlists.find(
      (playlist) => playlist.name.toLowerCase() === name.toLowerCase()
    );
    if (!existing) return null;
    const loaded = snapshot.queue && snapshot.queue.loaded_playlist;
    // Saving the playlist that is already loaded is not overwriting *another*
    // one - that is the normal "save my changes" case and must not nag.
    if (act === "playlist_save" && loaded && loaded.toLowerCase() === name.toLowerCase()) {
      return null;
    }
    return {
      name: existing.name,
      action: "playlist_save",
      dataset: { name: existing.name },
    };
  }

  async _onChange(event) {
    const trigger = event.target.closest("[data-act]");
    if (!trigger) return;
    const dataset = Object.assign({}, trigger.dataset);
    switch (dataset.act) {
      case "volume":
        // The slider sends only once the value is committed (release), never
        // while dragging.
        this._volumeDraft = Number(trigger.value);
        await this._act(
          "volume",
          Object.assign(dataset, { volume: trigger.value })
        );
        return;
      case "playlist-pick":
        // Picking a playlist in the dropdown is card state only: it becomes the
        // reference the Load / Save / Rename / Delete buttons act on, and no
        // request is made. Re-rendering here would also close the open
        // dropdown, so the panel is deliberately left alone.
        this._playlistPick = trigger.value || "";
        return;
      case "import-files":
        this._setImportFiles(Array.from(trigger.files || []));
        return;
      default:
        return;
    }
  }

  _onInput(event) {
    const trigger = event.target.closest("[data-act]");
    if (!trigger) return;
    if (trigger.dataset.act === "volume") {
      const label = this.querySelector('[data-el="volume-label"]');
      if (label) label.textContent = `${trigger.value}%`;
    }
    if (trigger.dataset.act === "playlist-name") {
      this._playlistName = trigger.value;
    }
    if (trigger.dataset.act === "import-name") {
      this._importName = trigger.value;
      this._importNameAuto = false;
    }
  }

  // -- rendering -----------------------------------------------------------

  /* Sections are repainted only when their own content changed.
   *
   * A subscription push arrives roughly once a second while playing, and
   * rebuilding a section with innerHTML destroys and recreates its children -
   * which threw focus out of the playlist name field after every keystroke and
   * could reset the volume slider mid-drag. `sig` is a cheap signature of
   * everything the section renders; if it is unchanged, nothing is touched. */
  _paint(element, signature, html) {
    if (!element) return false;
    if (element.dataset && element.dataset.sig === signature) return false;
    element.innerHTML = html;
    if (element.dataset) element.dataset.sig = signature;
    return true;
  }

  /* Never repaint a section the user is currently working in, even if its
   * signature changed - the text being typed must survive. */
  _editing(element) {
    const active = typeof document !== "undefined" ? document.activeElement : null;
    if (!active || !element || typeof element.contains !== "function") return false;
    return active !== element && element.contains(active);
  }

  _build() {
    this.innerHTML = `
      <ha-card>
        <style>
          :host { display:block; }
          .ha-shell {
            --ha-metal: #3a3f4b;
            --ha-metal-2: #1d2027;
            --ha-lcd: #000;
            --ha-lcd-ink: #00e000;
            --ha-lcd-dim: #00740f;
            --ha-fill: #006a10;
            --ha-tint: #0a2a0e;
            --ha-glow: rgba(0,224,0,.55);
            --ha-pl-sel: #0000c6;
            --ha-m1:#4a5060; --ha-m2:#3a3f4b; --ha-m3:#1d2027;
            --ha-hi:#9aa3bc; --ha-hi2:#8089a2; --ha-edge:#0b0d14;
            --ha-b1:#6b7285; --ha-b2:#3a3f4b; --ha-b3:#2b2f3a;
            --ha-bh1:#7c849a; --ha-bh2:#464c5b; --ha-bh3:#343946;
            --ha-s1:#5d6a8f; --ha-s2:#2b3250;
            --ha-r1:#5b6275; --ha-r2:#323744; --ha-r3:#4b5266;
            background: linear-gradient(180deg, var(--ha-m1), var(--ha-m2) 8%, var(--ha-m3));
            border: 2px solid var(--ha-edge);
            border-top-color: var(--ha-b1); border-left-color: var(--ha-b1);
            border-radius: 3px;
            padding: 0 0 8px;
            color: #cfd5e0;
            font-family: var(--code-font-family, "DejaVu Sans Mono", Consolas, monospace);
            box-shadow: 0 6px 18px rgba(0,0,0,.45);
          }
          .ha-title {
            display:flex; align-items:center; gap:8px; padding:3px 6px; margin-bottom:8px;
            background: repeating-linear-gradient(90deg, var(--ha-s1) 0 2px, var(--ha-s2) 2px 4px);
            border-bottom:1px solid var(--ha-edge);
          }
          .ha-logo {
            font-size:11px; font-weight:700; letter-spacing:.3em; color:#e9efff;
            padding:0 8px; background:var(--ha-s2); text-shadow:0 1px 0 #000;
          }
          .ha-target { flex:1; min-width:0; font-size:10px; color:#dfe6ff; text-align:right;
            white-space:nowrap; overflow:hidden; text-overflow:ellipsis; text-shadow:0 1px 0 #000; }
          .ha-led { width:8px; height:8px; border-radius:1px; background:#5a6169; flex:0 0 auto;
            border:1px solid #000; }
          .ha-led.is-on { background:var(--ha-lcd-ink); box-shadow:0 0 6px var(--ha-lcd-ink); }
          .ha-led.is-warn { background:#e0a33a; box-shadow:0 0 6px #e0a33a; }
          .ha-main { padding:0 8px; }

          .ha-lcd {
            background: var(--ha-lcd);
            border:2px solid var(--ha-edge); border-bottom-color:var(--ha-b1); border-right-color:var(--ha-b1);
            padding: 6px 8px;
          }
          .ha-lcd-row { display:flex; align-items:flex-start; gap:10px; }
          .ha-clock {
            font-size:30px; line-height:1; letter-spacing:.04em; color: var(--ha-lcd-ink);
            font-weight:700; text-shadow:0 0 8px var(--ha-glow); min-width:92px;
          }
          .ha-vu { display:flex; align-items:flex-end; gap:2px; height:26px; margin-left:auto; }
          .ha-vu i {
            display:block; width:5px; height:3px; background: var(--ha-lcd-dim); transition: height .18s linear;
          }
          .ha-vu.is-playing i { background: var(--ha-lcd-ink); animation: ha-vu 1s infinite ease-in-out; }
          .ha-vu.is-playing i:nth-child(2) { animation-delay:.12s; }
          .ha-vu.is-playing i:nth-child(3) { animation-delay:.24s; }
          .ha-vu.is-playing i:nth-child(4) { animation-delay:.36s; }
          .ha-vu.is-playing i:nth-child(5) { animation-delay:.48s; }
          @keyframes ha-vu { 0%,100% { height:3px } 50% { height:24px } }
          .ha-lcd-title {
            font-size:12px; color: var(--ha-lcd-ink); margin-top:6px;
            white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
          }
          .ha-lcd-sub { font-size:10px; color: var(--ha-lcd-dim); margin-top:2px;
            white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
          .ha-mm-off .ha-lcd-title { color:#ff4d3a; font-weight:700; font-size:15px;
            text-shadow:0 0 8px rgba(255,77,58,.6); }
          .ha-mm-off .ha-clock { color:#7a2a22; text-shadow:none; }
          .ha-track-bar { position:relative; height:10px; margin-top:8px; cursor:pointer;
            background:#05060a; border:1px solid var(--ha-hi2); border-bottom-color:#000; border-right-color:#000; }
          .ha-track-bar.is-unknown {
            background-image: repeating-linear-gradient(90deg,
              color-mix(in srgb, var(--ha-lcd-ink) 20%, transparent) 0 8px, color-mix(in srgb, var(--ha-lcd-ink) 4%, transparent) 8px 16px);
            animation: ha-scan 1.6s linear infinite;
          }
          .ha-track-bar.is-unknown .ha-track-fill,
          .ha-track-bar.is-unknown .ha-knob { display:none; }
          @keyframes ha-scan { from { background-position:0 0 } to { background-position:64px 0 } }
          .ha-track-fill { height:100%; width:0%; background: linear-gradient(90deg, var(--ha-fill), var(--ha-lcd-ink)); }
          .ha-knob { position:absolute; top:-2px; bottom:-2px; left:0%; width:12px; margin-left:-6px;
            background: linear-gradient(180deg,#f2f4fa,var(--ha-hi)); border:1px solid #000; }
          .ha-hint { font-size:9px; opacity:.6; margin-top:4px; }
          .ha-timebar { display:flex; justify-content:flex-end; font-size:10px;
            color: var(--ha-lcd-dim); margin-top:3px; }

          .ha-deck { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-top:8px; }
          .ha-controls { display:flex; gap:2px; }
          .ha-options { display:flex; flex-wrap:wrap; align-items:center; gap:6px; flex:1 1 220px; }
          .ha-btn {
            min-width:34px; height:24px; padding:0 8px; cursor:pointer;
            background: linear-gradient(180deg,var(--ha-b1),var(--ha-b2) 55%,var(--ha-b3));
            color:#f0f3fa; border:1px solid var(--ha-edge); border-top-color:var(--ha-hi); border-left-color:var(--ha-hi2);
            font-family:inherit; font-size:11px; line-height:1;
          }
          .ha-btn:hover { background: linear-gradient(180deg,var(--ha-bh1),var(--ha-bh2) 55%,var(--ha-bh3)); }
          .ha-btn:active { background:var(--ha-b3); }
          .ha-btn[disabled] { opacity:.35; cursor:not-allowed; }
          .ha-btn.is-primary { color:#ffd27a; }
          .ha-btn.is-on { background: linear-gradient(180deg,var(--ha-fill),var(--ha-tint)); color:var(--ha-lcd-ink); }
          .ha-btn.is-emulated { border-style:dashed; }
          .ha-eq { display:inline-flex; align-items:center; gap:2px; flex:0 0 auto; }
          .ha-eq label { font-size:9px; text-transform:uppercase; letter-spacing:.08em; opacity:.7; margin-right:4px; }
          .ha-eq .ha-btn { min-width:0; padding:0 6px; font-size:10px; }
          .ha-slider { flex:1 1 140px; min-width:0; display:flex; align-items:center; gap:6px; }
          .ha-slider label { font-size:9px; text-transform:uppercase; letter-spacing:.08em; opacity:.7; }
          .ha-slider input[type=range] { flex:1 1 60px; min-width:0; width:60px; accent-color: var(--ha-lcd-ink); height:14px; }
          .ha-slider span { font-size:10px; opacity:.8; min-width:34px; text-align:right; }

          .ha-source-display { display:inline-flex; align-items:center; gap:4px; flex:0 0 auto; white-space:nowrap; }
          .ha-source-display label { font-size:9px; text-transform:uppercase; letter-spacing:.08em; opacity:.7; }
          .ha-tabs { display:flex; gap:2px; padding:0 8px; margin-top:10px; }
          .ha-tab {
            flex:1; height:22px; cursor:pointer; font-family:inherit; font-size:10px;
            text-transform:uppercase; letter-spacing:.1em; color:#cfd5e0;
            background: linear-gradient(180deg,var(--ha-m1),var(--ha-b3));
            border:1px solid var(--ha-edge); border-bottom:none; border-top-color:var(--ha-hi2);
          }
          .ha-tab.is-active { background:#000; color: var(--ha-lcd-ink); }
          .ha-panels { margin:0 8px; background:#000; border:2px solid var(--ha-edge); border-top-color:var(--ha-b1); }
          .ha-panel { min-width:0; background:#000; }
          .ha-panel[hidden] { display:none; }
          .ha-panel-head { display:flex; align-items:center; gap:6px; padding:3px 6px;
            font-size:10px; text-transform:uppercase; letter-spacing:.12em; color: var(--ha-lcd-dim);
            border-bottom:1px solid var(--ha-tint); }
          .ha-panel-head .ha-grow { flex:1; }
          .ha-crumbs { display:flex; flex-wrap:wrap; gap:4px; padding:3px 6px; font-size:10px; }
          .ha-crumb { cursor:pointer; color:var(--ha-lcd-ink); text-decoration:underline; }
          .ha-rows { max-height:300px; min-height:120px; overflow-y:auto; scrollbar-color:var(--ha-b1) var(--ha-m3); }
          .ha-row {
            display:flex; align-items:center; gap:6px; padding:1px 6px;
            font-size:11px; color: var(--ha-lcd-ink); line-height:18px;
          }
          .ha-row:hover { background: var(--ha-tint); }
          .ha-row.is-current { color:#fff; }
          .ha-row.is-current .ha-row-title::before { content:"\\25B6  "; }
          .ha-row.is-played { color:var(--ha-lcd-dim); }
          .ha-row.is-selected { background: var(--ha-pl-sel); color:#fff; }
          .ha-row-title { flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; cursor:pointer; }
          .ha-row-meta { font-size:10px; opacity:.8; white-space:nowrap; }
          .ha-row-btn {
            height:18px; min-width:20px; padding:0 5px; cursor:pointer; font-size:10px;
            background: linear-gradient(180deg,var(--ha-r1),var(--ha-r2)); color:#f1f3f8;
            border:1px solid var(--ha-edge); border-top-color:var(--ha-hi2); border-left-color:var(--ha-hi2);
            font-family:inherit; line-height:1;
          }
          .ha-row-btn:hover { background:var(--ha-r3); }
          .ha-row-btn[disabled] { opacity:.3; cursor:not-allowed; }
          .ha-row-btn.is-primary { color:#ffd27a; }
          .ha-row-btn.is-danger { color:#ffb0b0; }
          .ha-row .ha-row-btn { visibility:hidden; }
          .ha-row:hover .ha-row-btn, .ha-row.is-selected .ha-row-btn, .ha-row.is-current .ha-row-btn { visibility:visible; }
          .ha-empty { font-size:11px; color: var(--ha-lcd-dim); padding:8px; }
          .ha-toolbar { display:flex; flex-wrap:wrap; gap:3px; padding:4px 6px; background:var(--ha-m3);
            border-top:1px solid var(--ha-edge); }
          .ha-field { display:flex; gap:4px; align-items:center; flex:1 1 auto; }
          .ha-input {
            flex:1; min-width:0; height:20px; background:#000; color: var(--ha-lcd-ink);
            border:1px solid var(--ha-hi2); border-bottom-color:#000; border-right-color:#000;
            padding:0 6px; font-family:inherit; font-size:11px;
          }
          .ha-confirm {
            display:flex; flex-wrap:wrap; align-items:center; gap:6px; margin:4px 6px;
            padding:6px 7px; font-size:11px; background:#3a2a00; border:1px solid #b07a00; color:#ffd79a;
          }
          .ha-confirm span { flex:1 1 100%; }
          .ha-drop { display:block; margin:6px; padding:18px 8px; text-align:center; font-size:11px; cursor:pointer;
            color: var(--ha-lcd-ink); border:1px dashed var(--ha-lcd-dim); }
          .ha-drop:hover { background:var(--ha-tint); }
          .ha-drop input[type=file] { display:none; }
          .ha-imp { padding:3px 6px; font-size:10px; color: var(--ha-lcd-ink); }
          .ha-imp-bar { height:4px; background:#0d0f16; margin-top:2px; }
          .ha-imp-bar i { display:block; height:100%; background: var(--ha-lcd-ink); }
          .ha-imp-msg { margin:4px 6px; padding:5px 7px; font-size:11px; color: var(--ha-lcd-ink);
            border:1px solid var(--ha-lcd-dim); }
          .ha-imp-msg.is-error { color:#ffb0a0; border-color:#a03a2a; }
          .ha-banner {
            margin:0 8px 8px; padding:6px 8px; font-size:11px;
            background:#3a2a00; border:1px solid #b07a00; color:#ffd79a;
          }
          .ha-foot { margin:6px 8px 0; font-size:9px; opacity:.5; }
          @media (max-width: 480px) { .ha-clock { font-size:24px; min-width:72px; } }
        </style>
        <div class="ha-shell" data-el="shell">
          <div class="ha-title">
            <span class="ha-led" data-el="led"></span>
            <span class="ha-logo">HASEAMP</span>
            <span class="ha-target" data-el="target"></span>
          </div>
          <div data-el="banner"></div>
          <div class="ha-main">
            <div class="ha-lcd" data-el="lcd">
              <div class="ha-lcd-row">
                <div class="ha-clock" data-el="elapsed">--:--</div>
                <span class="ha-vu" data-el="vu" title="" aria-hidden="true">
                  <i></i><i></i><i></i><i></i><i></i>
                </span>
              </div>
              <div class="ha-lcd-title" data-el="lcd-title"></div>
              <div class="ha-lcd-sub" data-el="lcd-sub"></div>
              <div class="ha-track-bar" data-el="seekbar" data-act="transport" data-action="seek">
                <div class="ha-track-fill" data-el="progress"></div>
                <span class="ha-knob" data-el="knob"></span>
              </div>
              <div class="ha-timebar"><span data-el="duration">--:--</span></div>
              <div class="ha-hint" data-el="seek-hint" hidden></div>
            </div>
            <div class="ha-deck">
              <div class="ha-controls" data-el="controls"></div>
              <div class="ha-options" data-el="options"></div>
            </div>
          </div>
          <div class="ha-tabs" data-el="tabs"></div>
          <div class="ha-panels" data-el="panels">
            <div class="ha-panel" data-el="queue-panel">
              <div class="ha-panel-head">
                <span data-el="queue-title"></span><span class="ha-grow"></span>
                <span class="ha-row-meta" data-el="queue-meta"></span>
                <button class="ha-row-btn" data-act="queue_clear" title="" data-el="queue-clear">CLR</button>
              </div>
              <div class="ha-rows" data-el="queue"></div>
            </div>
            <div class="ha-panel" data-el="browser-panel">
              <div class="ha-panel-head"><span data-el="browser-title"></span><span class="ha-grow"></span><span class="ha-row-meta" data-el="storage"></span></div>
              <div data-el="browser-confirm"></div>
              <div class="ha-crumbs" data-el="crumbs"></div>
              <div class="ha-rows" data-el="browser"></div>
            </div>
            <div class="ha-panel" data-el="playlist-panel">
              <div class="ha-panel-head"><span data-el="playlist-title"></span></div>
              <div data-el="playlist-body"></div>
            </div>
            <div class="ha-panel" data-el="import-panel">
              <div class="ha-panel-head"><span data-el="import-title"></span></div>
              <div data-el="import-body"></div>
            </div>
          </div>
          <div class="ha-foot" data-el="foot"></div>
        </div>
      </ha-card>
    `;

    this.addEventListener("click", (event) => this._onClick(event));
    this.addEventListener("input", (event) => this._onInput(event));
    this.addEventListener("change", (event) => this._onChange(event));
    this.addEventListener("dblclick", (event) => this._onDoubleClick(event));
    this.addEventListener("dragover", (event) => this._onDragOver(event));
    this.addEventListener("drop", (event) => this._onDrop(event));
    const vu = this.querySelector('[data-el="vu"]');
    if (vu) vu.title = this._t("vu_hint");
    this._applyStaticLabels();
    this._startClock();
    this._render();
  }

  _applyStaticLabels() {
    const set = (name, value, title) => {
      const element = this.querySelector(`[data-el="${name}"]`);
      if (!element) return;
      if (value != null) element.textContent = value;
      if (title != null) element.title = title;
    };
    set("browser-title", this._t("browser"));
    set("queue-title", this._t("queue"));
    set("playlist-title", this._t("pl_manager"));
    set("import-title", this._t("import_title"));
    set("queue-clear", this._t("queue_clear"), this._t("queue_clear"));
  }

  _render() {
    if (!this._built) return;
    const snapshot = this._snapshot;
    const features = (snapshot && snapshot.features) || {};
    const playback = (snapshot && snapshot.playback) || {};
    const queue = (snapshot && snapshot.queue) || { items: [] };
    const language = this._lang();

    // -- header
    const targetName = this._name
      || (snapshot && snapshot.target && snapshot.target.name)
      || (snapshot && snapshot.provider) || "";
    const targetEl = this.querySelector('[data-el="target"]');
    if (targetEl) targetEl.textContent = targetName;
    const led = this.querySelector('[data-el="led"]');
    if (led) {
      led.classList.toggle("is-on", !!(snapshot && snapshot.available));
      led.classList.toggle(
        "is-warn",
        !!(
          snapshot
          && !snapshot.available
          && snapshot.reason !== this._transport().noDeviceReason
        )
      );
    }
    const vu = this.querySelector('[data-el="vu"]');
    if (vu) {
      vu.classList.toggle(
        "is-playing",
        !!this._show.animation && playback.state === "playing"
      );
    }

    // -- banner
    const banner = this.querySelector('[data-el="banner"]');
    if (banner) {
      let message = "";
      if (this._error) message = this._error;
      else if (snapshot && snapshot.reason === "device_unavailable") {
        message = this._t("device_unavailable");
      }
      banner.innerHTML = message
        ? `<div class="ha-banner">${esc(message)}</div>`
        : "";
    }

    // -- LCD
    const current = queue.current_track || playback.track || null;
    const mode = (snapshot && snapshot.music_mode) || {};
    const modeOff = !!(snapshot && snapshot.available && mode.known && mode.active === false);
    const shell = this.querySelector('[data-el="shell"]');
    if (shell) shell.classList.toggle("ha-mm-off", modeOff);
    const title = modeOff
      ? this._t("music_mode_off")
      : current
        ? nice(current.title)
        : nice(playback.title || this._t("lcd_idle"));
    const lcdTitle = this.querySelector('[data-el="lcd-title"]');
    if (lcdTitle) lcdTitle.textContent = title;

    const sub = [];
    if (modeOff) sub.push(this._t("music_mode_hint"));
    else if (current && current.artist) sub.push(current.artist);
    if (
      snapshot
      && snapshot.available === false
      && snapshot.reason === this._transport().noDeviceReason
    ) {
      sub.push(this._tDevice("no_device"));
    }
    if (modeOff) {
      /* the hint above already says what to do */
    } else if (this._selected) sub.push(`${this._t("selected")}: ${this._selected.title}`);
    else if (playback.source) sub.push(`${this._t("source")}: ${playback.source}`);
    const lcdSub = this.querySelector('[data-el="lcd-sub"]');
    if (lcdSub) lcdSub.textContent = sub.join("  ·  ");

    const elapsedKnown = this._paintProgress(playback);
    const known = elapsedKnown;
    const seekBar = this.querySelector('[data-el="seekbar"]');
    if (seekBar) {
      /* The bar seeks: its markup declares `data-action="seek"` and `_onClick`
       * turns the click position into a `transport` seek, while the ↺ button
       * stays the plain "start over" (`transport: restart`). A device without the
       * SEEK action keeps the old click-restarts behaviour. The declared action
       * is deliberately never taken away here: whether a click can be aimed at
       * all depends on the length the device reports *now*, and an unknown length
       * is transient - the backend fills it in from the queue row or the TF
       * inventory - so the click handler decides per click rather than the action
       * being removed once and never coming back. */
      seekBar.style.cursor = known ? "pointer" : "default";
      seekBar.title = !known
        ? this._t("seek_unknown_duration")
        : (features.seek === true ? this._t("seek_position") : this._t("seek_restart"));
    }
    const seekHint = this.querySelector('[data-el="seek-hint"]');
    if (seekHint) {
      // The hint only says something the tooltip does not already say, so it
      // stays quiet during normal playback - and a missing track length is the
      // one reason the bar cannot show progress, seek support or not.
      const hint = known ? "" : this._t("seek_unknown_duration");
      seekHint.hidden = hint === "";
      seekHint.textContent = hint;
    }

    this._applyAccent();
    this._renderTabs();
    this._renderControls(snapshot, features, playback, queue, language);
    this._renderBrowser(snapshot, features);
    this._renderQueue(snapshot, queue);
    this._renderPlaylists(snapshot);
    this._renderImport(snapshot);
    this._renderFoot(snapshot);
  }

  _renderControls(snapshot, features, playback, queue, language) {
    /* Two rows on purpose: the transport buttons stay together in the middle,
     * while volume/source/modes sit on their own line, separated by a rule.
     * One long wrapping row mixed them up and pushed the volume slider around
     * with every state change. */
    const controls = this.querySelector('[data-el="controls"]');
    const options = this.querySelector('[data-el="options"]');
    if (!controls || !options) return;
    const mode = (snapshot && snapshot.music_mode) || {};
    const modeOff = !!(snapshot && snapshot.available && mode.known && mode.active === false);
    // No music screen on the device = no transport; treat it like no device.
    const hasDevice = !!(snapshot && snapshot.available) && !modeOff;
    const playing = playback.state === "playing";
    const disabled = hasDevice ? "" : " disabled";
    const notSupported = () =>
      ` disabled title="${esc(this._t("unavailable_feature"))}"`;

    const repeatMode = queue.repeat || "off";
    const shuffleOn = !!queue.shuffle;
    const consumeOn = !!queue.consume_played;
    const volume = playback.volume != null
      ? Math.round(playback.volume * 100)
      : (this._volumeDraft != null ? this._volumeDraft : 0);

    // -- transport row
    const transportSignature = JSON.stringify([
      hasDevice, playing, features.previous_track, features.next_track,
      features.stop, this._lang(), modeOff,
    ]);
    if (!this._editing(controls) && controls.dataset.sig !== transportSignature) {
      const parts = [];
      parts.push(`<button class="ha-btn" data-act="transport" data-action="previous"${
        hasDevice ? (features.previous_track === false ? notSupported() : "") : disabled
      } title="${esc(this._t("prev"))}">&#9198;</button>`);
      parts.push(`<button class="ha-btn is-primary" data-act="transport" data-action="${
        playing ? "pause" : "play"
      }"${hasDevice ? "" : disabled} title="${esc(
        playing ? this._t("pause") : this._t("play")
      )}">${playing ? "&#10074;&#10074;" : "&#9654;"}</button>`);
      parts.push(`<button class="ha-btn${features.stop === true ? "" : " is-emulated"}" data-act="transport" data-action="stop"${
        hasDevice ? "" : disabled
      } title="${esc(
        features.stop === true ? this._t("stop") : this._t("stop_emulated")
      )}">&#9632;</button>`);
      parts.push(`<button class="ha-btn" data-act="transport" data-action="next"${
        hasDevice ? (features.next_track === false ? notSupported() : "") : disabled
      } title="${esc(this._t("next"))}">&#9197;</button>`);
      // Start the current row over from the beginning (the bar above it seeks).
      parts.push(`<button class="ha-btn" data-act="restart"${
        hasDevice ? "" : disabled
      } title="${esc(this._t("restart"))}">&#8634;</button>`);
      this._paint(controls, transportSignature, parts.join(""));
    }

    // -- options row
    const optionsSignature = JSON.stringify([
      features.volume, features.source, playback.source, volume,
      repeatMode, shuffleOn, consumeOn, this._lang(),
      features.sound_effect, playback.sound_effect, hasDevice,
    ]);
    if (!this._editing(options) && options.dataset.sig !== optionsSignature) {
      const parts = [];
      if (features.volume) {
        parts.push(`<span class="ha-slider">
        <label>${esc(this._t("volume"))}</label>
        <input type="range" min="0" max="100" value="${volume}" data-act="volume">
        <span data-el="volume-label">${volume}%</span>
      </span>`);
      }
      if (features.source && playback.source) {
        // Read-only: the card shows the source the backend reports. Switching
        // it is not part of any capability the backend advertises, so the card
        // does not pretend it can.
        parts.push(`<span class="ha-source-display"><label>${esc(
          this._t("source")
        )}</label><span class="ha-row-meta" data-el="source">${esc(
          playback.source
        )}</span></span>`);
      }
      if (features.sound_effect === true) {
        // The device's sound presets, shown like an equalizer preset switch.
        const effects = Array.isArray(features.sound_effects) ? features.sound_effects : [];
        parts.push(`<span class="ha-eq" title="${esc(this._t("sound_hint"))}"><label>${esc(this._t("sound"))}</label>${effects
          .map(
            (effect) => `<button class="ha-btn${playback.sound_effect === effect ? " is-on" : ""}" data-act="set_sound_effect" data-effect="${esc(
              effect
            )}"${hasDevice ? "" : " disabled"}>${esc(this._t(`sound_${effect}`))}</button>`
          )
          .join("")}</span>`);
      }
      parts.push(`<button class="ha-btn${shuffleOn ? " is-on" : ""}" data-act="set_shuffle" data-shuffle="${
        shuffleOn ? "0" : "1"
      }" title="${esc(this._t("shuffle"))}">SHF</button>`);
      const nextRepeat = repeatMode === "off" ? "all" : repeatMode === "all" ? "one" : "off";
      parts.push(`<button class="ha-btn${repeatMode === "off" ? "" : " is-on"}" data-act="set_repeat" data-repeat="${nextRepeat}" title="${esc(
        this._t("repeat")
      )}">RPT ${esc(repeatLabel(repeatMode, (key) => this._t(key)))}</button>`);
      // Whether a played row leaves the queue - the switch between "playlist
      // that keeps everything" and "up next list that shrinks".
      parts.push(`<button class="ha-btn${consumeOn ? " is-on" : ""}" data-act="set_consume_played" data-consume="${
        consumeOn ? "0" : "1"
      }" title="${esc(this._t("consume_hint"))}">${esc(this._t("consume"))}</button>`);
      this._paint(options, optionsSignature, parts.join(""));
    }
  }

  /* The one place that turns playback state into the progress display.
   *
   * Both the fill width and the position knob are set, and an unknown length
   * switches the bar into its striped state - so "no progress" can never be
   * confused with "the device reported no length". */
  _paintProgress(playback) {
    const elapsed = this._elapsed(playback);
    const duration = playback && playback.duration > 0 ? playback.duration : null;
    const known = duration !== null && elapsed != null;
    const ratio = known
      ? Math.max(0, Math.min(100, (elapsed / duration) * 100))
      : 0;
    const bar = this.querySelector('[data-el="seekbar"]');
    const fill = this.querySelector('[data-el="progress"]');
    const knob = this.querySelector('[data-el="knob"]');
    if (bar) bar.classList.toggle("is-unknown", !known);
    if (fill) fill.style.width = `${ratio.toFixed(2)}%`;
    if (knob) knob.style.left = `${ratio.toFixed(2)}%`;
    const time = this.querySelector('[data-el="elapsed"]');
    if (time) time.textContent = fmtTime(elapsed);
    const total = this.querySelector('[data-el="duration"]');
    if (total) total.textContent = fmtTime(duration);
    return known;
  }

  /* The device's ambient light colour, read-only. How that light is found is
   * provider knowledge and lives in the provider boundary above. */
  _lightRgb() {
    return deviceLightRgb(this._hass, { config_entry_id: this._resolvedTarget || this._target });
  }

  /* Turn a light colour into a skin, like the classic player's colour themes:
   * the hue drives the text, the bevels, the buttons, the tabs and the title
   * bar. Lightness is pinned per part so the card stays readable whatever the
   * light is doing - and blue/violet hues, which look dark at the same
   * lightness, get a lighter text colour. */
  _accentFor(rgb) {
    const [r, g, b] = rgb.map((v) => Math.min(255, Math.max(0, v)) / 255);
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const delta = max - min;
    let hue = 0;
    if (delta > 0) {
      if (max === r) hue = ((g - b) / delta) % 6;
      else if (max === g) hue = (b - r) / delta + 2;
      else hue = (r - g) / delta + 4;
      hue = Math.round(hue * 60);
      if (hue < 0) hue += 360;
    }
    const lightness = (max + min) / 2;
    const saturation = delta === 0 ? 0 : delta / (1 - Math.abs(2 * lightness - 1));
    // Near-grey lights (white) keep a neutral skin instead of a random hue.
    const sat = saturation < 0.12 ? 0 : Math.round(Math.max(saturation, 0.35) * 100);
    const dark = hue >= 210 && hue <= 310;
    const hsl = (satScale, light) => `hsl(${hue} ${Math.round(sat * satScale)}% ${light}%)`;
    return {
      ink: hsl(1, dark ? 70 : 58),
      dim: hsl(0.85, dark ? 56 : 38),
      fill: hsl(1, dark ? 36 : 28),
      tint: hsl(0.7, 12),
      glow: `hsl(${hue} ${sat}% ${dark ? 70 : 58}% / .55)`,
      "--ha-m1": hsl(0.45, 30), "--ha-m2": hsl(0.45, 23), "--ha-m3": hsl(0.4, 13),
      "--ha-hi": hsl(0.4, 66), "--ha-hi2": hsl(0.4, 54), "--ha-edge": hsl(0.4, 5),
      "--ha-b1": hsl(0.45, 44), "--ha-b2": hsl(0.45, 28), "--ha-b3": hsl(0.45, 19),
      "--ha-bh1": hsl(0.45, 54), "--ha-bh2": hsl(0.45, 34), "--ha-bh3": hsl(0.45, 25),
      "--ha-s1": hsl(0.6, 44), "--ha-s2": hsl(0.6, 22),
      "--ha-r1": hsl(0.45, 40), "--ha-r2": hsl(0.45, 24), "--ha-r3": hsl(0.45, 33),
    };
  }

  _applyAccent() {
    const shell = this.querySelector('[data-el="shell"]');
    if (!shell || !shell.style || typeof shell.style.setProperty !== "function") return;
    const rgb = this._show.follow_light ? this._lightRgb() : null;
    const key = rgb ? rgb.join(",") : "";
    if (this._accentKey === key) return;
    this._accentKey = key;
    const accent = rgb ? this._accentFor(rgb) : null;
    const vars = {
      "--ha-lcd-ink": accent && accent.ink,
      "--ha-lcd-dim": accent && accent.dim,
      "--ha-fill": accent && accent.fill,
      "--ha-tint": accent && accent.tint,
      "--ha-glow": accent && accent.glow,
    };
    for (const name of ["--ha-m1", "--ha-m2", "--ha-m3", "--ha-hi", "--ha-hi2", "--ha-edge", "--ha-b1", "--ha-b2",
      "--ha-b3", "--ha-bh1", "--ha-bh2", "--ha-bh3", "--ha-s1", "--ha-s2", "--ha-r1", "--ha-r2", "--ha-r3"]) {
      vars[name] = accent && accent[name];
    }
    for (const [name, value] of Object.entries(vars)) {
      if (value) shell.style.setProperty(name, value);
      else if (typeof shell.style.removeProperty === "function") shell.style.removeProperty(name);
    }
  }

  async _loadPlaylistTracks(name) {
    if (!name) return;
    try {
      const result = await this._call("playlist_get", { name });
      if (this._playlistPick !== name) return; // the user picked another one meanwhile
      this._plTracks = { name: result.name, tracks: result.tracks || [] };
    } catch (err) {
      this._plTracks = null;
      this._error = (err && err.message) || this._t("load_error");
    }
    this._render();
  }

  /* Files put on the card by other means (the device's own page, a card reader)
   * carry no playlist - the device has no playlist concept and HASE cannot read
   * file contents back. A folder is the natural grouping, so one click turns it
   * into a stored playlist through the existing operations. */
  async _folderToPlaylist(node, title) {
    const name = String(title || "").trim();
    if (!node || !name) return;
    try {
      await this._call("queue_clear", {});
      await this._call("enqueue_folder", { node, play: false });
      await this._call("playlist_save_as", { name });
      this._playlistPick = name;
      this._plTracks = null;
      this._error = null;
      await this._refresh();
      this._tab = "lists";
      this._render();
      await this._loadPlaylistTracks(name);
    } catch (err) {
      this._error = (err && err.message) || this._t("load_error");
      this._render();
    }
  }

  async _scanFolders() {
    try {
      const result = await this._call("playlists_from_folders", {});
      this._scanMessage = this._t("scan_done")
        .replace("{created}", String((result.created || []).length))
        .replace("{skipped}", String(result.skipped || 0));
      this._error = null;
      this._apply(
        Object.assign({}, this._snapshot || {}, { playlists: result.playlists || (this._snapshot || {}).playlists })
      );
    } catch (err) {
      this._scanMessage = "";
      this._error = (err && err.message) || this._t("load_error");
      this._render();
    }
  }

  _exportPlaylist() {
    if (!this._plTracks || typeof document === "undefined" || typeof Blob === "undefined") return;
    const blob = new Blob([buildM3u(this._plTracks.tracks)], { type: "audio/x-mpegurl" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${this._plTracks.name}.m3u`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  _storage(snapshot) {
    const library = (snapshot && snapshot.library) || {};
    const total = library.capacity_total_bytes;
    const used = library.capacity_used_bytes;
    if (!Number.isFinite(total) || !(total > 0) || !Number.isFinite(used)) return null;
    return { total, used, free: Math.max(0, total - used), pct: Math.min(100, Math.round((used / total) * 100)) };
  }

  _renderTabs() {
    const tabs = this.querySelector('[data-el="tabs"]');
    if (!tabs) return;
    const entries = [
      ["queue", "tab_queue", this._show.queue],
      ["library", "tab_library", this._show.browser],
      ["lists", "tab_lists", this._show.playlists],
      ["import", "tab_import", this._show.import],
    ].filter((entry) => entry[2]);
    if (!entries.some((entry) => entry[0] === this._tab) && entries.length) {
      this._tab = entries[0][0];
    }
    this._paint(
      tabs,
      JSON.stringify([entries.map((entry) => entry[0]), this._tab, this._lang()]),
      entries
        .map(
          ([id, key]) =>
            `<button class="ha-tab${this._tab === id ? " is-active" : ""}" data-act="tab" data-tab="${id}">${esc(
              this._t(key)
            )}</button>`
        )
        .join("")
    );
  }

  _renderBrowser(snapshot, features) {
    const panel = this.querySelector('[data-el="browser-panel"]');
    if (!panel) return;
    if (!this._show.browser) {
      panel.hidden = true;
      return;
    }
    panel.hidden = this._tab !== "library";
    const body = this.querySelector('[data-el="browser"]');
    const crumbs = this.querySelector('[data-el="crumbs"]');
    const storage = this._storage(snapshot);
    const storageEl = this.querySelector('[data-el="storage"]');
    if (storageEl) {
      storageEl.textContent = storage
        ? `${this._t("storage")} ${fmtBytes(storage.used)} / ${fmtBytes(storage.total)} (${storage.pct}%)`
        : "";
      storageEl.title = storage ? `${fmtBytes(storage.free)} ${this._t("storage_free")}` : "";
    }
    const confirmBox = this.querySelector('[data-el="browser-confirm"]');
    if (confirmBox) {
      const pending = this._deleteConfirm;
      const state = this._deleteBusy ? "busy" : this._deleteError ? "error" : "ask";
      let html = "";
      if (pending && state === "busy") {
        html = `<div class="ha-confirm"><span>${esc(this._t("deleting"))} ${esc(pending.title)}</span></div>`;
      } else if (pending && state === "error") {
        html = `<div class="ha-confirm"><span>${esc(this._t("delete_failed"))} ${esc(this._deleteError)}</span>
          <button class="ha-row-btn" data-act="delete-no">${esc(this._t("ok"))}</button></div>`;
      } else if (pending) {
        html = `<div class="ha-confirm"><span>${esc(
          this._t(pending.kind === "folder" ? "delete_ask_folder" : "delete_ask_file").replace("{name}", pending.title)
        )} ${esc(this._t("delete_hint"))}</span>
          <button class="ha-row-btn is-danger" data-act="delete-yes">${esc(this._t("delete_yes"))}</button>
          <button class="ha-row-btn" data-act="delete-no">${esc(this._t("cancel"))}</button></div>`;
      }
      this._paint(
        confirmBox,
        JSON.stringify([pending ? [pending.kind, pending.title] : null, state, this._deleteError, this._lang()]),
        html
      );
    }
    if (crumbs) {
      const trail = this._crumbTrail();
      const parts = [`<span class="ha-crumb" data-act="browse" data-node="">${esc(
        this._t("browser_root")
      )}</span>`];
      for (const crumb of trail) {
        parts.push(`<span>&#8250;</span><span class="ha-crumb" data-act="browse" data-node="${esc(
          crumb.node
        )}">${esc(crumb.title)}</span>`);
      }
      this._paint(crumbs, JSON.stringify(trail), parts.join(" "));
    }
    if (!body) return;

    const library = (snapshot && snapshot.library) || null;
    const tree = this._tree;
    const entries = tree && Array.isArray(tree.entries) ? tree.entries : null;
    const signature = JSON.stringify([
      features.browser,
      library && library.ready,
      tree ? tree.node : null,
      tree ? tree.library_ready : null,
      this._treeRevision,
      this._lang(),
      entries ? entries.map((e) => [e.id, e.title, e.kind, e.track_count]) : null,
    ]);
    if (this._editing(body)) return;
    if (body.dataset.sig === signature) return;

    if (!features.browser) {
      this._paint(body, signature, `<div class="ha-empty">${esc(this._t("browser_unavailable"))}</div>`);
      return;
    }
    if (library && library.ready === false && !tree) {
      // No inventory has arrived yet - say that instead of claiming the
      // library is empty.
      this._paint(body, signature, `<div class="ha-empty">${esc(this._t("browser_waiting"))}</div>`);
      return;
    }
    if (!tree) {
      this._paint(body, signature, `<div class="ha-empty">${esc(this._t("browser_loading"))}</div>`);
      return;
    }
    if (tree.library_ready === false) {
      this._paint(body, signature, `<div class="ha-empty">${esc(this._t("browser_waiting"))}</div>`);
      return;
    }
    if (!entries) {
      // A listing the card cannot read is not an empty folder - never claim
      // the library is empty on a payload the backend did not describe.
      this._paint(body, signature, `<div class="ha-empty">${esc(this._t("browser_loading"))}</div>`);
      return;
    }
    if (!entries.length) {
      this._paint(body, signature, `<div class="ha-empty">${esc(this._t("browser_empty"))}</div>`);
      return;
    }

    this._paint(
      body,
      signature,
      entries
        .map((entry) => {
          if (entry.can_expand) {
            return `<div class="ha-row">
            <span class="ha-row-title" data-act="browse" data-node="${esc(entry.id)}">${esc(
                entry.title
              )}/</span>
            <span class="ha-row-meta">${entry.track_count}</span>
            <button class="ha-row-btn" data-act="browse" data-node="${esc(entry.id)}" title="${esc(
                this._t("browser")
              )}">&#9654;</button>
            <button class="ha-row-btn" data-act="enqueue_folder" data-node="${esc(entry.id)}" title="${esc(
                this._t("add_folder")
              )}">&#43;</button>
            <button class="ha-row-btn" data-act="folder-playlist" data-node="${esc(entry.id)}" data-title="${esc(
                entry.title
              )}" title="${esc(this._t("folder_playlist"))}">&#9835;</button>
            <button class="ha-row-btn is-danger" data-act="delete-ask" data-kind="folder" data-node="${esc(
                entry.id
              )}" data-title="${esc(entry.title)}" title="${esc(this._t("delete_folder"))}">&#10005;</button>
          </div>`;
          }
          const track = entry.track || {};
          return `<div class="ha-row">
          <span class="ha-row-title" data-act="browse-select" data-uri="${esc(track.uri)}" data-title="${esc(
              entry.title
            )}">${esc(entry.title)}</span>
          <span class="ha-row-meta">${esc(fmtTime(track.duration))}</span>
          <button class="ha-row-btn" data-act="queue_add" data-uri="${esc(track.uri)}" data-title="${esc(
              entry.title
            )}" data-provider="${esc(track.provider)}" data-duration="${
              track.duration == null ? "" : track.duration
            }" data-play="1" title="${esc(this._t("play_now"))}">&#9654;</button>
          <button class="ha-row-btn" data-act="queue_add" data-uri="${esc(track.uri)}" data-title="${esc(
              entry.title
            )}" data-provider="${esc(track.provider)}" data-duration="${
              track.duration == null ? "" : track.duration
            }" data-play="0" title="${esc(this._t("add_track"))}">&#43;</button>
          <button class="ha-row-btn is-danger" data-act="delete-ask" data-kind="file" data-uri="${esc(
              track.uri
            )}" data-title="${esc(entry.title)}" title="${esc(this._t("delete_file"))}">&#10005;</button>
        </div>`;
        })
        .join("")
    );
  }

  _renderQueue(snapshot, queue) {
    const panel = this.querySelector('[data-el="queue-panel"]');
    if (!panel) return;
    if (!this._show.queue) {
      panel.hidden = true;
      return;
    }
    panel.hidden = this._tab !== "queue";
    const body = this.querySelector('[data-el="queue"]');
    const items = queue.items || [];
    const meta = this.querySelector('[data-el="queue-meta"]');
    if (meta) {
      const total = items.reduce(
        (sum, row) => sum + (row.track && row.track.duration ? row.track.duration : 0),
        0
      );
      // The position within the queue, so a queued-but-not-yet-played list and
      // a finished one are distinguishable at a glance.
      const position = queue.index == null ? "-" : queue.index + 1;
      meta.textContent = `${position}/${queue.count || 0} · ${this._t(
        "queue_total"
      )} ${fmtTime(total)}`;
    }
    if (!body) return;

    const signature = JSON.stringify([
      items.map((row) => [row.item_id, row.current, row.played, row.track && row.track.title]),
      this._selected ? this._selected.uri : null,
      this._lang(),
    ]);
    if (this._editing(body)) return;
    if (body.dataset.sig === signature) return;

    if (!items.length) {
      this._paint(body, signature, `<div class="ha-empty">${esc(this._t("queue_empty"))}</div>`);
      return;
    }
    this._paint(
      body,
      signature,
      items
        .map((row, position) => {
          const track = row.track || {};
          const selected = this._selected && this._selected.uri === track.uri;
          // Played rows stay in the queue (a queue is not consumed by playing
          // it) - they are only marked, so "what is still ahead" stays visible.
          const classes = [
            "ha-row",
            row.current ? "is-current" : "",
            row.played && !row.current ? "is-played" : "",
            selected ? "is-selected" : "",
          ].filter(Boolean).join(" ");
          return `<div class="${classes}" data-dbl="queue_play" data-item_id="${row.item_id}" title="${esc(
            this._t("queue_dbl_hint")
          )}">
          <span class="ha-row-meta">${position + 1}.</span>
          <span class="ha-row-title" data-act="queue-select" data-uri="${esc(track.uri)}" data-title="${esc(
            track.title
          )}" title="${esc(track.uri || "")}">${esc(nice(track.title))}</span>
          <span class="ha-row-meta">${esc(fmtTime(track.duration))}</span>
          <button class="ha-row-btn" data-act="queue_play" data-item_id="${row.item_id}" title="${esc(
            this._t("play_now")
          )}">&#9654;</button>
          <button class="ha-row-btn" data-act="queue_move" data-from_index="${position}" data-to_index="${
            position - 1
          }" title="${esc(this._t("move_up"))}"${position === 0 ? " disabled" : ""}>&#9650;</button>
          <button class="ha-row-btn" data-act="queue_move" data-from_index="${position}" data-to_index="${
            position + 1
          }" title="${esc(this._t("move_down"))}"${
            position === items.length - 1 ? " disabled" : ""
          }>&#9660;</button>
          <button class="ha-row-btn" data-act="queue_remove" data-item_id="${row.item_id}" title="${esc(
            this._t("remove")
          )}">&#10005;</button>
        </div>`;
        })
        .join("")
    );
  }

  _renderPlaylists(snapshot) {
    const panel = this.querySelector('[data-el="playlist-panel"]');
    if (!panel) return;
    if (!this._show.playlists) {
      panel.hidden = true;
      return;
    }
    panel.hidden = this._tab !== "lists";
    const body = this.querySelector('[data-el="playlist-body"]');
    if (!body) return;

    const playlists = (snapshot && snapshot.playlists) || [];
    const loaded = snapshot && snapshot.queue && snapshot.queue.loaded_playlist;
    const confirm = this._confirm;

    /* The signature deliberately excludes the typed name: the field's content
     * is card state, so a push must not repaint (and thereby unfocus) it. */
    const signature = JSON.stringify([
      playlists.map((p) => [p.name, p.count, p.resume ? [p.resume.index, Math.round(p.resume.position / 10)] : null]),
      loaded,
      this._playlistPick,
      confirm ? confirm.name : null,
      this._lang(),
      this._plTracks ? [this._plTracks.name, this._plTracks.tracks.map((t) => t.uri)] : null,
      this._scanMessage || "",
    ]);
    if (this._editing(body)) return;
    if (body.dataset.sig === signature) return;

    const pickedPlaylist = playlists.find((playlist) => playlist.name === this._playlistPick) || null;
    const pickedResume = pickedPlaylist ? pickedPlaylist.resume || null : null;
    const resumeHint = pickedResume
      ? this._t("pl_resume_hint")
        .replace("{n}", String((pickedResume.index || 0) + 1))
        .replace("{title}", nice(pickedResume.title))
        .replace("{time}", fmtTime(pickedResume.position))
      : this._t("pl_resume_none");
    const rows = playlists
      .map((playlist) => {
        const isLoaded = !!loaded && loaded === playlist.name;
        const picked = this._playlistPick === playlist.name;
        const resume = playlist.resume || null;
        const resumeText = resume
          ? this._t("pl_resume_row")
            .replace("{n}", String((resume.index || 0) + 1))
            .replace("{time}", fmtTime(resume.position))
          : "";
        return `<div class="ha-row${picked ? " is-selected" : ""}${isLoaded ? " is-current" : ""}" data-act="pl-select" data-name="${esc(
          playlist.name
        )}" data-dbl="playlist_load"${resume ? ' data-resume="1"' : ""} title="${esc(this._t("pl_select_hint"))}">
          <span class="ha-row-title">${esc(nice(playlist.name))}</span>
          <span class="ha-row-meta">${resume ? `${esc(resumeText)} · ` : ""}${isLoaded ? `${esc(this._t("pl_loaded"))} · ` : ""}${playlist.count} ${esc(
            this._t("pl_tracks")
          )}</span>
        </div>`;
      })
      .join("");

    const edited = this._plTracks && this._plTracks.name === this._playlistPick ? this._plTracks.tracks : null;
    const editor = !this._playlistPick
      ? `<div class="ha-empty">${esc(this._t("pl_edit_empty"))}</div>`
      : edited === null
        ? `<div class="ha-empty">${esc(this._t("pl_loading"))}</div>`
        : `<div class="ha-panel-head"><span>${esc(this._playlistPick)}</span><span class="ha-grow"></span>
          <button class="ha-row-btn" data-act="pl-export"${edited.length ? "" : " disabled"}>${esc(this._t("pl_export"))}</button></div>
          <div class="ha-rows">${edited
            .map(
              (track, position) => `<div class="ha-row">
            <span class="ha-row-meta">${position + 1}.</span>
            <span class="ha-row-title" title="${esc(track.uri)}">${esc(nice(track.title))}</span>
            <span class="ha-row-meta">${esc(fmtTime(track.duration))}</span>
            <button class="ha-row-btn" data-act="playlist_update" data-op="up" data-pos="${position}" title="${esc(
              this._t("pl_track_up")
            )}"${position === 0 ? " disabled" : ""}>&#9650;</button>
            <button class="ha-row-btn" data-act="playlist_update" data-op="down" data-pos="${position}" title="${esc(
              this._t("pl_track_down")
            )}"${position === edited.length - 1 ? " disabled" : ""}>&#9660;</button>
            <button class="ha-row-btn is-danger" data-act="playlist_update" data-op="remove" data-pos="${position}" title="${esc(
              this._t("pl_track_remove")
            )}">&#10005;</button>
          </div>`
            )
            .join("")}</div>`;

    this._paint(
      body,
      signature,
      `
      <div class="ha-rows">${
        playlists.length ? rows : `<div class="ha-empty">${esc(this._t("playlists_empty"))}</div>`
      }</div>
      <div class="ha-toolbar">
        <button class="ha-row-btn" data-act="playlist_load" data-el="playlist-load" title="${esc(
          this._t("pl_select_hint")
        )}">${esc(this._t("playlist_load"))}</button>
        <button class="ha-row-btn is-primary" data-act="playlist_load" data-resume="1" data-name="${esc(
          this._playlistPick
        )}" title="${esc(resumeHint)}"${pickedResume ? "" : " disabled"}>${esc(this._t("pl_resume"))}</button>
        <button class="ha-row-btn" data-act="playlist_create" data-from_queue="0">${esc(
          this._t("playlist_new")
        )}</button>
        <button class="ha-row-btn" data-act="playlist_create" data-from_queue="1">${esc(
          this._t("playlist_create_from_queue")
        )}</button>
        <button class="ha-row-btn" data-act="playlist_save">${esc(
          this._t("playlist_save")
        )}</button>
        <button class="ha-row-btn" data-act="playlist_rename">${esc(
          this._t("playlist_rename")
        )}</button>
        <button class="ha-row-btn is-danger" data-act="playlist_delete">${esc(
          this._t("playlist_delete")
        )}</button>
      </div>
      ${editor}
      <div class="ha-toolbar">
        <button class="ha-row-btn is-primary" data-act="folders-scan" title="${esc(this._t("scan_hint"))}">${esc(
          this._t("scan_playlists")
        )}</button>
        ${this._scanMessage ? `<span class="ha-row-meta">${esc(this._scanMessage)}</span>` : ""}
      </div>
      <div class="ha-toolbar">
        <div class="ha-field">
          <input class="ha-input" data-act="playlist-name" placeholder="${esc(
            this._t("playlist_name")
          )}" value="${esc(this._playlistName)}">
          <button class="ha-row-btn" data-act="playlist_save_as" title="${esc(
            this._t("playlist_save_as")
          )}">${esc(this._t("playlist_save_as"))}</button>
        </div>
      </div>
      ${
        confirm
          ? `<div class="ha-confirm">
        <span>${esc(
          this._t("playlist_overwrite").replace("{name}", confirm.name)
        )}</span>
        <button class="ha-row-btn is-primary" data-act="confirm-yes">${esc(
          this._t("overwrite")
        )}</button>
        <button class="ha-row-btn" data-act="confirm-no">${esc(this._t("cancel"))}</button>
      </div>`
          : ""
      }
    `
    );
  }

  _renderImport(snapshot) {
    const panel = this.querySelector('[data-el="import-panel"]');
    if (!panel) return;
    if (!this._show.import) {
      panel.hidden = true;
      return;
    }
    panel.hidden = this._tab !== "import";
    const body = this.querySelector('[data-el="import-body"]');
    if (!body) return;
    const state = this._import;
    const ready = !!(snapshot && snapshot.available) && !state.busy;
    const signature = JSON.stringify([
      state.busy, state.message, state.error, state.items, ready, this._lang(),
      this._importPlan ? this._importPlan.plan.map((row) => [row.name, row.state, !!row.differs]) : null,
    ]);
    // The name field keeps its text and focus: it is only rebuilt when the
    // signature above changes, and never while the user is typing in it.
    if (this._editing(body)) return;
    if (body.dataset.sig === signature) return;
    const stateLabel = { wait: "import_state_wait", up: "import_state_up", ok: "import_state_ok", err: "import_state_err" };
    const list = state.items
      .map(
        (item) => `<div class="ha-imp"><div class="ha-row-title">${esc(item.name)} · ${esc(
          this._t(stateLabel[item.state] || "import_state_wait")
        )}${item.state === "up" ? ` ${item.pct}%` : ""}</div><div class="ha-imp-bar"><i style="width:${
          item.state === "ok" ? 100 : item.pct
        }%"></i></div></div>`
      )
      .join("");
    const planLabel = { card: "plan_state_card", upload: "plan_state_upload", missing: "plan_state_missing", url: "plan_state_url" };
    const planList = this._importPlan
      ? `<div class="ha-rows">${this._importPlan.plan
        .map(
          (row, position) => `<div class="ha-row${row.state === "missing" || row.state === "url" ? " is-played" : ""}">
          <span class="ha-row-meta">${position + 1}.</span>
          <span class="ha-row-title" title="${esc(row.name)}">${esc(row.name)}</span>
          <span class="ha-row-meta">${esc(this._t(planLabel[row.state]))}${row.differs ? " *" : ""}</span>
        </div>`
        )
        .join("")}</div>`
      : "";
    const disabled = ready ? "" : " disabled";
    this._paint(
      body,
      signature,
      `
      <label class="ha-drop" data-el="import-drop">
        ${esc(this._t("import_drop"))}
        <input type="file" multiple accept="audio/*,.mp3,.wav,.flac,.ogg,.m4a,.aac,.m3u,.m3u8" data-act="import-files"${
          state.busy ? " disabled" : ""
        }>
      </label>
      <div class="ha-toolbar">
        <label class="ha-row-btn" style="display:inline-flex;align-items:center;cursor:pointer">${esc(this._t("import_pick_folder"))}
          <input type="file" webkitdirectory multiple data-act="import-files" style="display:none"${
            state.busy ? " disabled" : ""
          }>
        </label>
      </div>
      ${planList}
      ${list}
      ${
        state.message
          ? `<div class="ha-imp-msg${state.error ? " is-error" : ""}">${esc(state.message)}</div>`
          : ""
      }
      <div class="ha-toolbar">
        <div class="ha-field">
          <input class="ha-input" data-act="import-name" placeholder="${esc(
            this._t("import_name")
          )}" value="${esc(this._importName)}">
        </div>
      </div>
      <div class="ha-toolbar">
        <button class="ha-row-btn is-primary" data-act="import-run" data-build="1"${disabled}>${esc(
          this._t("import_build")
        )}</button>
        <button class="ha-row-btn" data-act="import-run" data-build="0"${disabled}>${esc(
          this._t("import_only")
        )}</button>
        <button class="ha-row-btn" data-act="import-clear"${state.busy ? " disabled" : ""}>${esc(
          this._t("import_clear")
        )}</button>
      </div>
      <div class="ha-empty">${esc(this._t("import_build_hint"))}</div>
    `
    );
  }

  _renderFoot(snapshot) {
    const foot = this.querySelector('[data-el="foot"]');
    if (!foot) return;
    if (!this._show.diagnostics) {
      foot.textContent = "";
      return;
    }
    const diagnostics = (snapshot && snapshot.diagnostics) || {};
    const playback = (snapshot && snapshot.playback) || {};
    const contract = (snapshot && snapshot.track_end) || {};
    const seconds = (value) => (value == null ? "n/a" : `${Number(value).toFixed(1)}s`);
    const parts = [`${this._t("auto_advance")}: ${contract.signal || "—"}`];
    if (diagnostics.last_track_end) {
      parts.push(`last: ${diagnostics.last_track_end.reason}`);
    }
    // The raw values, so "no progress" can be told apart from "the device
    // reported no length" without touching the device.
    parts.push(
      `dev pos ${seconds(playback.position)} / dur ${seconds(playback.duration)}` +
        (playback.duration_source ? ` (${playback.duration_source})` : "")
    );
    foot.textContent = parts.join(" · ");
    const detail = [
      `fields: ${(diagnostics.playback_fields || []).join(", ") || "—"}`,
      `device track_id: ${diagnostics.device_track_id || "—"}`,
      `queue mode: consume=${!!(snapshot && snapshot.queue && snapshot.queue.consume_played)}`,
    ];
    foot.title = [contract.gap || this._t("auto_advance_gap"), ...detail].join("\n\n");
  }
}

class HaseAmpCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._fetched && hass && hass.callWS) {
      this._fetched = true;
      this._loadTargets();
    }
    this._render();
  }

  async _loadTargets() {
    try {
      this._targets = await musicTargetsFor(this._hass, PROVIDER_TRANSPORTS);
    } catch (err) {
      this._targets = [];
    }
    this._render();
  }

  _emit(config) {
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config },
        bubbles: true,
        composed: true,
      })
    );
  }

  _render() {
    const config = this._config || {};
    const targets = this._targets || [];
    const options = [`<option value="">— ${esc("default")} —</option>`]
      .concat(
        targets.map(
          (target) =>
            `<option value="${esc(target.id)}"${
              config.target === target.id ? " selected" : ""
            }>${esc(target.name)}</option>`
        )
      )
      .join("");
    this.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:10px;padding:4px;">
        <label style="font-size:12px;">Target
          <select data-el="target" style="width:100%;">${options}</select>
        </label>
        <label style="font-size:12px;">Name
          <input data-el="name" type="text" style="width:100%;" value="${esc(
            config.name || ""
          )}">
        </label>
        <label style="font-size:12px;display:flex;gap:8px;align-items:center;">
          <input data-el="show_browser" type="checkbox"${config.show_browser !== false ? " checked" : ""}>
          Music browser
        </label>
        <label style="font-size:12px;display:flex;gap:8px;align-items:center;">
          <input data-el="show_queue" type="checkbox"${config.show_queue !== false ? " checked" : ""}>
          Active queue
        </label>
        <label style="font-size:12px;display:flex;gap:8px;align-items:center;">
          <input data-el="show_playlists" type="checkbox"${config.show_playlists !== false ? " checked" : ""}>
          Playlists
        </label>
        <label style="font-size:12px;display:flex;gap:8px;align-items:center;">
          <input data-el="show_import" type="checkbox"${config.show_import !== false ? " checked" : ""}>
          Import
        </label>
        <label style="font-size:12px;display:flex;gap:8px;align-items:center;">
          <input data-el="follow_light" type="checkbox"${config.follow_light !== false ? " checked" : ""}>
          Colour follows the device light
        </label>
        <label style="font-size:12px;display:flex;gap:8px;align-items:center;">
          <input data-el="show_animation" type="checkbox"${config.show_animation !== false ? " checked" : ""}>
          Playback animation
        </label>
      </div>
    `;
    this.querySelector('[data-el="target"]').addEventListener("change", (event) => {
      const next = Object.assign({}, this._config);
      if (event.target.value) next.target = event.target.value;
      else delete next.target;
      this._emit(next);
    });
    this.querySelector('[data-el="name"]').addEventListener("input", (event) => {
      const next = Object.assign({}, this._config);
      if (event.target.value) next.name = event.target.value;
      else delete next.name;
      this._emit(next);
    });
    for (const flag of ["show_browser", "show_queue", "show_playlists", "show_import", "follow_light", "show_animation"]) {
      this.querySelector(`[data-el="${flag}"]`).addEventListener("change", (event) => {
        const next = Object.assign({}, this._config);
        if (event.target.checked) delete next[flag];
        else next[flag] = false;
        this._emit(next);
      });
    }
  }
}

if (!customElements.get("haseamp-card-editor")) {
  customElements.define("haseamp-card-editor", HaseAmpCardEditor);
}
if (!customElements.get("haseamp-card")) {
  customElements.define("haseamp-card", HaseAmpCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "haseamp-card")) {
  window.customCards.push({
    type: "haseamp-card",
    name: "HASEAMP",
    description:
      "Kompakter Musik-Player: Wiedergabe, aktive Queue und persistente Playlists.",
  });
}
