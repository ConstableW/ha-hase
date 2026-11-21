/* CODE27 dashboard card. Vanilla web component, no build step. */
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

const LOGO_SVG = `<svg viewBox="0 0 145 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M6 0V23.999L0 17.999V0H6ZM18 0L20 2V6H8V0H18Z" fill="currentColor" fill-opacity="0.92"/><path d="M6.00012 23.9998V18H20.0001V21.9999L18.0001 23.9998H6.00012Z" fill="currentColor" fill-opacity="0.92"/><path d="M44.9999 2V17.999L38.9999 23.999H26.9999L24.9999 21.999V6L30.9999 0H42.9999L44.9999 2ZM38.3807 7.0918C37.9596 7.37252 37.6261 7.81423 37.3749 8.25293C37.1819 8.58994 37.031 8.93797 36.9218 9.23535C36.3969 9.03013 35.7689 8.93069 35.1698 8.93066C34.6049 8.93068 34.0363 9.01854 33.5858 9.20898C33.5378 9.03311 33.4688 8.79663 33.3807 8.5459C33.2905 8.28908 33.1784 8.01095 33.0477 7.76465C32.9199 7.52391 32.7609 7.28739 32.5663 7.13867L32.452 7.05176L32.3182 7.10742C31.7329 7.3522 31.2633 7.93865 30.8827 8.66113C30.4976 9.39214 30.1813 10.3062 29.9247 11.2705C29.4112 13.2003 29.126 15.3724 29.0204 16.7852L28.9999 17.0537H41.243L41.2186 16.7812C41.1182 15.6495 40.8799 13.5138 40.4774 11.5322C40.2763 10.542 40.032 9.58201 39.7411 8.80273C39.5957 8.4132 39.4368 8.06225 39.2616 7.77344C39.0878 7.48705 38.8884 7.24534 38.6581 7.0918L38.5194 7L38.3807 7.0918Z" fill="currentColor" fill-opacity="0.92"/><path d="M31.7694 12.0547C31.7694 11.7785 31.9933 11.5547 32.2694 11.5547H32.5694C32.8456 11.5547 33.0694 11.7785 33.0694 12.0547V13.6546C33.0694 13.9308 32.8456 14.1546 32.5694 14.1546H32.2694C31.9933 14.1546 31.7694 13.9308 31.7694 13.6546V12.0547Z" fill="currentColor" fill-opacity="0.92"/><path d="M35.7694 12.0547C35.7694 11.7785 35.9933 11.5547 36.2694 11.5547H36.5694C36.8455 11.5547 37.0694 11.7785 37.0694 12.0547V13.6546C37.0694 13.9308 36.8455 14.1546 36.5694 14.1546H36.2694C35.9933 14.1546 35.7694 13.9308 35.7694 13.6546V12.0547Z" fill="currentColor" fill-opacity="0.92"/><path d="M49.9999 23.9993V0H55.9999V17.9995H61.9999V23.9993H49.9999Z" fill="currentColor" fill-opacity="0.92"/><path d="M57.9998 5.99983V0H63.9998V5.99983H57.9998Z" fill="currentColor" fill-opacity="0.92"/><path d="M63.9999 0V23.9993L69.9999 17.9995V5.99983L63.9999 0Z" fill="currentColor" fill-opacity="0.92"/><path d="M74.9998 17.9995V0H80.9998V23.9993L74.9998 17.9995Z" fill="currentColor" fill-opacity="0.92"/><path d="M95.0001 18H81.0001V23.9998H93.0001L95.0001 21.9999V18Z" fill="currentColor" fill-opacity="0.92"/><path d="M83 14.9989V8.99902H91V14.9989H83Z" fill="currentColor" fill-opacity="0.92"/><path d="M83 5.99983V0H93L95 1.99994V5.99983H83Z" fill="currentColor" fill-opacity="0.92"/><path d="M99.9996 7.99977H106V5.99983H120V1.99994L118 0H102L99.9996 1.99994V7.99977Z" fill="currentColor" fill-opacity="0.92"/><path d="M99.9998 23.9991H118L120 21.9991V15.9993H114V17.9992H106V17.4992L120 7.99951H111.75L99.9998 15.9993V23.9991Z" fill="currentColor" fill-opacity="0.92"/><path d="M138 13.4994V23.9991H132V11.9994L135.733 7.99951H143.133L138 13.4994Z" fill="currentColor" fill-opacity="0.92"/><path d="M125 1.99994V5.99983H145L139 0H127L125 1.99994Z" fill="currentColor" fill-opacity="0.92"/></svg>`;

const PRESET_COLORS = [
  [255, 138, 76],
  [255, 60, 60],
  [255, 200, 40],
  [60, 220, 130],
  [60, 170, 255],
  [170, 90, 255],
  [255, 255, 255],
];

// The light entity's effect *values* are fixed keys (const.py's
// LIGHT_EFFECT_*), unrelated to viewer language - only the displayed label is
// translated. The older German words stay accepted by the backend and are
// mapped here so an effect reported under either name is found.
const EFFECT_VALUES = ["static", "breathe", "pulse", "blink", "chase"];
const EFFECT_ALIASES = {
  Statisch: "static", Atmen: "breathe", Pulsieren: "pulse", Blinken: "blink", Lauflicht: "chase",
};

// Weekday keys as the alarm services/attributes use them (Monday first).
const DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

// Sections the user can fold away (the chat has its own, older toggle).
const SECTION_KEYS = ["alarms", "notes", "outfit"];

const STRINGS = {
  de: {
    ack: "Quittieren",
    notification_default: "Hinweis aktiv",
    light_title: "Umgebungslicht",
    brightness: "Helligkeit",
    vol: "Vol",
    music_title: "Musik",
    source_tf: "TF-Karte",
    source_bt: "Bluetooth",
    music_open: "Musikseite offen",
    music_closed: "Musikseite zu",
    scene_title: "Lichtszene",
    light_group_ambient: "Umgebungsfarbe",
    light_group_scenes: "Szenen",
    character_title: "Charakter",
    alarms_title: "Alarme",
    alarms_empty: "Keine Alarme",
    notes_title: "Offene Notizen",
    notes_empty: "Keine offenen Notizen",
    notes_delete: "Löschen",
    add: "Hinzufügen",
    outfit_title: "Outfit",
    language_title: "KI-Sprache",
    device_title: "Gerät",
    mode_live: "Live",
    mode_music: "Musik",
    display_label: "Display",
    display_brightness: "Display-Helligkeit",
    outfit_loading: "lädt …",
    count_active: "aktiv",
    toggle_section: "Ein-/ausklappen",
    edit: "Bearbeiten",
    save: "Speichern",
    cancel: "Abbrechen",
    confirm_delete: "Wirklich löschen?",
    alarm_time: "Uhrzeit",
    alarm_label: "Bezeichnung (optional)",
    alarm_once: "einmalig",
    alarm_daily: "täglich",
    alarm_no_time: "Bitte eine Uhrzeit angeben.",
    memo_title: "Titel",
    memo_body: "Text",
    memo_reminder: "Erinnerung",
    memo_date: "Datum",
    memo_time: "Uhrzeit",
    memo_no_title: "Bitte einen Titel angeben.",
    memo_no_date: "Bitte ein Datum angeben.",
    days: { mon: "Mo", tue: "Di", wed: "Mi", thu: "Do", fri: "Fr", sat: "Sa", sun: "So" },
    chat_title: "Chat",
    chat_placeholder: "Nachricht an CODE27…",
    chat_send: "Senden",
    chat_empty: "Noch keine Nachrichten",
    chat_you: "Du",
    chat_character: "Charakter",
    chip_language: "Sprache",
    chip_timezone: "Zeitzone",
    chip_alarms: "Alarm(e)",
    chip_notes: "Notiz(en)",
    effect_labels: {
      static: "Statisch",
      breathe: "Atmen",
      pulse: "Pulsieren",
      blink: "Blinken",
      chase: "Lauflicht",
    },
  },
  en: {
    ack: "Acknowledge",
    notification_default: "Notification active",
    light_title: "Ambient light",
    brightness: "Brightness",
    vol: "Vol",
    music_title: "Music",
    source_tf: "TF card",
    source_bt: "Bluetooth",
    music_open: "Music page open",
    music_closed: "Music page closed",
    scene_title: "Light scene",
    light_group_ambient: "Ambient colour",
    light_group_scenes: "Scenes",
    character_title: "Character",
    alarms_title: "Alarms",
    alarms_empty: "No alarms",
    notes_title: "Open notes",
    notes_empty: "No open notes",
    notes_delete: "Delete",
    add: "Add",
    outfit_title: "Outfit",
    language_title: "AI language",
    device_title: "Device",
    mode_live: "Live",
    mode_music: "Music",
    display_label: "Display",
    display_brightness: "Display brightness",
    outfit_loading: "loading …",
    count_active: "active",
    toggle_section: "Expand/collapse",
    edit: "Edit",
    save: "Save",
    cancel: "Cancel",
    confirm_delete: "Really delete?",
    alarm_time: "Time",
    alarm_label: "Label (optional)",
    alarm_once: "once",
    alarm_daily: "daily",
    alarm_no_time: "Please enter a time.",
    memo_title: "Title",
    memo_body: "Text",
    memo_reminder: "Reminder",
    memo_date: "Date",
    memo_time: "Time",
    memo_no_title: "Please enter a title.",
    memo_no_date: "Please enter a date.",
    days: { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" },
    chat_title: "Chat",
    chat_placeholder: "Message to CODE27…",
    chat_send: "Send",
    chat_empty: "No messages yet",
    chat_you: "You",
    chat_character: "Character",
    chip_language: "Language",
    chip_timezone: "Timezone",
    chip_alarms: "alarm(s)",
    chip_notes: "note(s)",
    effect_labels: {
      static: "Static",
      breathe: "Breathe",
      pulse: "Pulse",
      blink: "Blink",
      chase: "Chase",
    },
  },
};

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Every CODE27 device always has exactly one entity with translation_key
// "active_character" (the current-character sensor, see sensor.py), so
// it's a reliable per-device anchor - reliable because translation_key is
// the same fixed, English, backend-assigned string regardless of viewer
// language, unlike the entity_id (which is derived from the *translated*
// display name and so is "aktiver_charakter" on a German instance,
// "active_character" only on an English one - matching only the latter,
// as a previous version of this function did via entity_id regex, silently
// found zero devices on every non-English instance). Used both to build
// the visual-editor device list and, via _resolveDeviceId() below, to
// anchor this card instance's own entity lookups.
function findCode27Devices(hass) {
  return code27Devices(hass);
}

// suffix (as used throughout this file, e.g. _eid("light", "umgebungsfarbe"))
// -> the entity's actual translation_key (see the _attr_translation_key /
// translation_key= assignments in sensor.py, binary_sensor.py, button.py,
// light.py, media_player.py, select.py, todo.py). Two different suffixes
// can map to the same translation_key on different domains (there is no
// collision here since _eid is always called with both), and one German
// word can appear on two *different* entities entirely - "aktiver_charakter"
// is both a sensor (translation_key "active_character", read-only, last
// spoken-to character) and a select (translation_key "character_slot",
// the picker) - kept as separate map keys below since _eid's cache key
// already includes the domain.
const SUFFIX_TRANSLATION_KEYS = {
  "media_player.musik": "music",
  "select.lichtszene": "light_project",
  "select.aktiver_charakter": "character_slot",
  "button.hinweis_quittieren": "acknowledge_notification",
  "light.umgebungsfarbe": "preview_light",
  "todo.notizen_erinnerungen": "memos",
  "sensor.build_typ": "build_type",
  "binary_sensor.hinweis_aktiv": "notification_active",
  "binary_sensor.musikmodus_aktiv": "music_mode_active",
  "sensor.zuletzt_gesagtes_charakter": "last_assistant_message",
  "sensor.zuletzt_gesagtes_du": "last_user_message",
  "sensor.assistentensprache": "ai_language",
  "sensor.zeitzone": "timezone",
  "sensor.aktive_alarme": "active_alarms",
  "sensor.notizen": "memo_count",
  "sensor.charakter_uebersicht": "character_overview",
  "select.outfit": "character_outfit",
  "select.ki_sprache": "ai_language_select",
  "select.geratemodus": "game_mode",
  "number.display_helligkeit": "display_brightness",
  "switch.display": "display_screen",
  "binary_sensor.model_loading": "model_loading",
};

class Code27Card extends HTMLElement {
  setConfig(config) {
    if (!config.device_prefix) {
      throw new Error("code27-card: 'device_prefix' is required, e.g. code27_192_168_32_197");
    }
    this._config = config;
    this._p = config.device_prefix;
    this._deviceId = config.device_id || undefined;
    this._eidCache = {};
    this._draft = "";
    this._built = false;
    this._notesConnection = null;
    this._notesInFlight = false;
    this._notesRetryAt = 0;
    this._notesFailures = 0;
    this._notesEpoch = 0;
    // Purely a client-side display preference (which is why it's
    // localStorage, not an entity) - remembered per browser/device so a
    // collapsed chat doesn't reset on every dashboard reload.
    const storedVisible = this._loadChatVisible();
    this._chatVisible = storedVisible === null ? true : storedVisible;
  }

  static getConfigElement() {
    return document.createElement("code27-card-editor");
  }

  static getStubConfig(hass) {
    const found = findCode27Devices(hass);
    const first = found[0];
    return {
      device_prefix: first ? first.prefix : "",
      device_id: first ? first.device_id : "",
      config_entry_id: first ? first.config_entry_id : "",
    };
  }

  getCardSize() {
    return 8;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._notesConnection !== hass.connection) {
      this._notesConnection = hass.connection;
      this._notesInFlight = false;
      this._notesRetryAt = 0;
      this._notesFailures = 0;
      this._notesEpoch++;
      this._notesLoaded = false;
    }
    // A device_id saved in the card YAML goes stale when the device is removed
    // and added again. Data lookups still worked through their entity-name
    // fallback, but the owner lookup (services) could not - so drop an id the
    // registry no longer knows and resolve the device afresh.
    if (this._deviceId && hass.devices && !hass.devices[this._deviceId]) {
      this._deviceId = undefined;
      this._eidCache = {};
    }
    if (this._deviceId === undefined) this._resolveDeviceId();
    if (!this._built) {
      this._build();
      this._built = true;
    }
    this._update();
  }

  // Resolves this card's device_id once hass is available, so _eid() can
  // look entities up by registry membership instead of guessing an
  // entity_id from string concatenation (see findCode27Devices above for
  // why device_prefix alone is not a safe technical key). Prefers an
  // explicit config.device_id (set by the visual editor going forward);
  // falls back to matching config.config_entry_id against the device
  // registry, then to the legacy device_prefix substring match, for
  // dashboards configured before this existed. Caches the *attempt*, not
  // just success (null, not undefined, on failure) so this never re-scans
  // the whole registry on every hass update once it has answered once.
  _resolveDeviceId() {
    if (this._deviceId !== undefined) return;
    this._deviceId = null;
    const hass = this._hass;
    if (!hass || !hass.entities) return;
    if (this._config.config_entry_id) {
      const devices = hass.devices || {};
      for (const [id, device] of Object.entries(devices)) {
        if ((device.config_entries || []).includes(this._config.config_entry_id)) {
          this._deviceId = id;
          return;
        }
      }
    }
    for (const [entityId, entry] of Object.entries(hass.entities)) {
      if (entry.translation_key === "active_character" && entityId.includes(this._p)) {
        this._deviceId = entry.device_id || null;
        return;
      }
    }
  }

  // Resolves via device_id + translation_key (language-independent, see
  // SUFFIX_TRANSLATION_KEYS above) whenever possible; falls back to the
  // legacy "domain.prefix_suffix" string concatenation when the registry
  // lookup can't be done (older HA frontend without translation_key on
  // hass.entities, unresolved device_id, or a suffix this file doesn't
  // know the translation_key for) - never a hard failure, same
  // never-block-the-card philosophy as the backend's own fallbacks.
  _eid(domain, suffix) {
    const cacheKey = `${domain}.${suffix}`;
    const cached = this._eidCache[cacheKey];
    if (cached) return cached;
    const fallback = `${domain}.${this._p}_${suffix}`;
    const translationKey = SUFFIX_TRANSLATION_KEYS[cacheKey];
    if (translationKey && this._deviceId && this._hass && this._hass.entities) {
      for (const [entityId, entry] of Object.entries(this._hass.entities)) {
        if (
          entry.device_id === this._deviceId &&
          entry.translation_key === translationKey &&
          entityId.startsWith(`${domain}.`)
        ) {
          this._eidCache[cacheKey] = entityId;
          return entityId;
        }
      }
    }
    return fallback;
  }

  _state(entityId) {
    return this._hass && this._hass.states[entityId];
  }

  _call(domain, service, data) {
    this._hass.callService(domain, service, data);
  }

  _lang() {
    const l = (this._hass && this._hass.language) || "en";
    return l.toLowerCase().startsWith("de") ? "de" : "en";
  }

  _t(key) {
    return STRINGS[this._lang()][key] || STRINGS.en[key] || key;
  }

  _build() {
    this.innerHTML = `
      <ha-card>
        <style>
          :host, ha-card { --c27-glow: 255,138,76; }
          .c27-wrap { padding: 16px; }
          .c27-head { display:flex; align-items:center; gap:14px; margin-bottom:14px; }
          .c27-portrait {
            width:56px; height:56px; border-radius:50%; overflow:hidden; flex:0 0 auto;
            background: radial-gradient(circle at 50% 30%, rgba(var(--c27-glow),0.55), rgba(var(--c27-glow),0.08) 70%);
            box-shadow: 0 0 0 2px rgba(var(--c27-glow),0.5), 0 0 18px rgba(var(--c27-glow),0.45);
            display:flex; align-items:center; justify-content:center;
            transition: box-shadow .4s ease, background .4s ease;
          }
          .c27-portrait img { width:100%; height:100%; object-fit:cover; object-position: top center; opacity:.96; }
          .c27-titles { flex:1; min-width:0; }
          .c27-logo { height:14px; width:84px; overflow:hidden; color: rgb(var(--c27-glow)); opacity:.85; transition: color .4s ease; }
          .c27-logo svg { display:block; height:14px; width:auto; }
          .c27-name { font-size:15px; font-weight:600; margin-top:6px; display:flex; align-items:center; }
          .c27-ip { font-size:11px; opacity:.55; margin-top:2px; }
          .c27-dot { width:8px; height:8px; border-radius:50%; display:inline-block; margin-right:6px; flex:0 0 auto; }
          .c27-online { background:#4caf50; box-shadow:0 0 6px #4caf50; }
          .c27-offline { background:#666; }
          .c27-alert[hidden] { display:none; }
          .c27-alert {
            display:flex; align-items:center; justify-content:space-between; gap:10px;
            background:rgba(255,70,70,0.12); border:1px solid rgba(255,70,70,0.4);
            border-radius:10px; padding:10px 12px; margin-bottom:14px; font-size:13px;
          }
          .c27-alert button {
            background:rgba(255,70,70,0.85); color:#fff; border:none; border-radius:8px;
            padding:6px 12px; font-size:12px; font-weight:600; cursor:pointer;
          }
          .c27-section { margin-bottom:16px; }
          .c27-row { display:flex; align-items:center; gap:10px; margin-bottom:8px; flex-wrap:wrap; }
          .c27-label { font-size:11px; text-transform:uppercase; letter-spacing:.06em; opacity:.6; margin-bottom:6px; }
          .c27-swatches { display:flex; gap:6px; flex-wrap:wrap; }
          .c27-swatch {
            width:22px; height:22px; border-radius:50%; cursor:pointer; border:2px solid transparent;
            box-shadow: inset 0 0 0 1px rgba(0,0,0,0.15);
          }
          .c27-swatch.is-active { border-color: var(--primary-text-color); }
          .c27-btn.is-active { border-color: rgb(var(--c27-glow)); background:rgba(var(--c27-glow),0.14); }
          select.c27-select {
            flex:1; min-width:120px; background:var(--card-background-color, #1c1c1c);
            color:var(--primary-text-color); border:1px solid var(--divider-color,#444);
            border-radius:8px; padding:6px 8px; font-size:13px;
          }
          .c27-btn {
            background:var(--secondary-background-color,#2a2a2a); color:var(--primary-text-color);
            border:1px solid var(--divider-color,#444); border-radius:8px; padding:6px 10px;
            font-size:13px; cursor:pointer; display:flex; align-items:center; gap:4px;
          }
          .c27-btn:hover { border-color: rgb(var(--c27-glow)); }
          .c27-btn.is-on { background:rgba(var(--c27-glow),0.25); border-color: rgb(var(--c27-glow)); }
          .c27-music-title { font-size:13px; opacity:.85; flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
          input[type="range"] { flex:1; accent-color: rgb(var(--c27-glow)); }
          .c27-chat { background:var(--secondary-background-color,#222); border-radius:10px; padding:10px; }
          .c27-history { max-height: var(--c27-history-max-height, 220px); overflow-y:auto; }
          .c27-bubble { font-size:13px; margin-bottom:8px; line-height:1.35; }
          .c27-bubble .who { font-size:10px; text-transform:uppercase; letter-spacing:.05em; opacity:.55; margin-bottom:2px; }
          .c27-bubble.user { text-align:right; }
          .c27-empty { font-size:12px; opacity:.5; text-align:center; padding:8px 0; }
          .c27-chat-input-row { display:flex; gap:8px; margin-top:6px; }
          .c27-chat-input-row input {
            flex:1; background:var(--card-background-color,#1c1c1c); color:var(--primary-text-color);
            border:1px solid var(--divider-color,#444); border-radius:8px; padding:8px 10px; font-size:13px;
          }
          .c27-send {
            background: rgb(var(--c27-glow)); color:#111; border:none; border-radius:8px;
            padding:8px 14px; font-size:13px; font-weight:700; cursor:pointer;
          }
          .c27-chips { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:14px; }
          .c27-chip {
            font-size:10px; padding:3px 9px; border-radius:20px; background:var(--secondary-background-color,#2a2a2a);
            border:1px solid var(--divider-color,#444); opacity:.75; white-space:nowrap;
          }
          .c27-badge {
            font-size:10px; padding:2px 8px; border-radius:20px; margin-left:8px;
            background:rgba(120,120,120,0.2); opacity:.7;
          }
          .c27-badge.is-on { background:rgba(var(--c27-glow),0.3); opacity:1; }
          .c27-progress-row { display:flex; align-items:center; gap:8px; font-size:10px; opacity:.6; margin-bottom:4px; }
          .c27-progress-bar { flex:1; height:4px; border-radius:4px; background:rgba(255,255,255,0.1); overflow:hidden; }
          .c27-progress-fill { height:100%; background: rgb(var(--c27-glow)); width:0%; transition: width 1s linear; }
          .c27-list-row {
            display:flex; align-items:center; justify-content:space-between; gap:10px;
            padding:8px 0; border-bottom:1px solid var(--divider-color,#2e2e2e); font-size:13px;
          }
          .c27-list-row:last-child { border-bottom:none; }
          .c27-toggle {
            width:36px; height:20px; border-radius:20px; border:none; cursor:pointer; position:relative;
            background:rgba(255,255,255,0.15); flex:0 0 auto;
          }
          .c27-toggle::after {
            content:""; position:absolute; top:2px; left:2px; width:16px; height:16px; border-radius:50%;
            background:#fff; transition: left .2s ease;
          }
          .c27-toggle.is-on { background: rgb(var(--c27-glow)); }
          .c27-toggle.is-on::after { left:18px; }
          .c27-toggle:disabled { opacity:.35; cursor:not-allowed; }
          .c27-del-btn {
            width:24px; height:24px; border-radius:6px; border:1px solid var(--divider-color,#444);
            background:transparent; color:var(--primary-text-color); opacity:.5; cursor:pointer;
            flex:0 0 auto; font-size:12px; line-height:1;
          }
          .c27-del-btn:hover { opacity:1; border-color:#e05252; color:#e05252; }
          .c27-charlist { display:flex; gap:8px; overflow-x:auto; padding:2px 2px 6px; }
          .c27-char-chip {
            display:flex; flex-direction:column; align-items:center; gap:4px;
            flex:0 0 auto; width:72px; padding:6px 4px; border-radius:10px;
            cursor:pointer; border:1px solid transparent; background:transparent;
            text-align:center;
          }
          .c27-char-chip:hover { background:var(--secondary-background-color,#2a2a2a); }
          .c27-char-chip.is-active { border-color: rgb(var(--c27-glow)); background:rgba(var(--c27-glow),0.14); }
          .c27-char-avatar {
            width:40px; height:40px; border-radius:50%; overflow:hidden; flex:0 0 auto;
            background: radial-gradient(circle at 50% 30%, rgba(var(--c27-glow),0.4), rgba(var(--c27-glow),0.06) 70%);
            display:flex; align-items:center; justify-content:center; font-size:15px; font-weight:600;
          }
          .c27-char-avatar img { width:100%; height:100%; object-fit:cover; object-position: top center; }
          .c27-char-name {
            font-size:11px; font-weight:600; white-space:nowrap; overflow:hidden;
            text-overflow:ellipsis; max-width:70px;
          }
          .c27-section-head { display:flex; align-items:center; justify-content:space-between; }
          .c27-section-head .c27-label { margin-bottom:0; }
          .c27-icon-btn {
            width:26px; height:26px; border-radius:6px; border:1px solid var(--divider-color,#444);
            background:transparent; color:var(--primary-text-color); opacity:.6; cursor:pointer;
            flex:0 0 auto; font-size:13px; line-height:1; padding:0;
          }
          .c27-icon-btn:hover { opacity:1; border-color: rgb(var(--c27-glow)); }
          .c27-row-actions { display:flex; align-items:center; gap:6px; flex:0 0 auto; }
          .c27-item-main { display:flex; flex-direction:column; min-width:0; flex:1; }
          .c27-item-title { font-size:13px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
          .c27-item-title.is-time { font-size:16px; font-weight:600; letter-spacing:.02em; }
          .c27-item-sub { font-size:11px; opacity:.6; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
          .c27-form {
            margin-top:8px; padding:10px; border-radius:10px; display:flex; flex-direction:column; gap:8px;
            background:var(--secondary-background-color,#222);
          }
          .c27-form-row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
          .c27-form input[type="text"], .c27-form input[type="time"], .c27-form input[type="date"], .c27-form textarea {
            flex:1; min-width:0; background:var(--card-background-color,#1c1c1c); color:var(--primary-text-color);
            border:1px solid var(--divider-color,#444); border-radius:8px; padding:7px 9px; font-size:13px;
            font-family:inherit; box-sizing:border-box;
          }
          .c27-form textarea { width:100%; min-height:54px; resize:vertical; }
          .c27-daychips { display:flex; gap:4px; flex-wrap:wrap; }
          .c27-daychip {
            padding:5px 8px; font-size:11px; border-radius:16px; cursor:pointer;
            border:1px solid var(--divider-color,#444); background:transparent; color:var(--primary-text-color);
          }
          .c27-daychip.is-on { background:rgba(var(--c27-glow),0.3); border-color: rgb(var(--c27-glow)); }
          .c27-form-error { font-size:11px; color:#e05252; min-height:0; }
          [data-slot] > .c27-form-host, [data-slot] > [data-el$="form"] { margin-bottom:8px; }
          .c27-check { display:flex; align-items:center; gap:6px; font-size:12px; }
          .c27-section-title { cursor:pointer; user-select:none; }
          .c27-count { opacity:.55; font-weight:400; margin-left:6px; text-transform:none; letter-spacing:0; }
          .c27-outfits { display:flex; gap:8px; overflow-x:auto; padding:2px 2px 6px; }
          .c27-outfits.is-loading { opacity:.45; pointer-events:none; }
        </style>
        <div class="c27-wrap">
          <div class="c27-head">
            <div class="c27-portrait" data-el="portrait"></div>
            <div class="c27-titles">
              <div class="c27-logo">${LOGO_SVG}</div>
              <div class="c27-name"><span class="c27-dot" data-el="onlinedot"></span><span data-el="name"></span></div>
              <div class="c27-ip" data-el="ip"></div>
            </div>
          </div>

          <div class="c27-chips" data-el="chips"></div>

          <div class="c27-alert" data-el="alertbox" hidden>
            <span data-el="alerttext"></span>
            <button data-action="ack">${esc(this._t("ack"))}</button>
          </div>

          <div class="c27-section" data-show="show_light">
            <div class="c27-row">
              <button class="c27-btn" data-action="light-toggle" data-el="lighttoggle">${esc(this._t("light_title"))}</button>
              <select class="c27-select" data-el="effect" data-action="effect"></select>
            </div>
            <div class="c27-row">
              <div class="c27-swatches" data-el="swatches"></div>
            </div>
            <div class="c27-row">
              <span style="font-size:11px;opacity:.6;">${esc(this._t("brightness"))}</span>
              <input type="range" min="1" max="255" data-el="brightness" data-action="brightness">
            </div>
          </div>

          <div class="c27-section" data-el="musicsection">
            <div class="c27-row" style="justify-content:space-between;">
              <div class="c27-label" style="margin-bottom:0;">${esc(this._t("music_title"))}</div>
              <span data-el="musikmodus"></span>
            </div>
            <div class="c27-row">
              <button class="c27-btn" data-action="prev">⏮</button>
              <button class="c27-btn" data-action="playpause" data-el="playpause">▶</button>
              <button class="c27-btn" data-action="next">⏭</button>
              <span class="c27-music-title" data-el="track"></span>
            </div>
            <div class="c27-progress-row">
              <span data-el="postime">0:00</span>
              <div class="c27-progress-bar"><div class="c27-progress-fill" data-el="progressfill"></div></div>
              <span data-el="durtime">0:00</span>
            </div>
            <div class="c27-row">
              <span style="font-size:11px;opacity:.6;">${esc(this._t("vol"))}</span>
              <input type="range" min="0" max="1" step="0.01" data-el="volume" data-action="volume">
              <select class="c27-select" data-el="source" data-action="source" style="flex:0 0 auto;">
                <option value="LOCAL_TF">${esc(this._t("source_tf"))}</option>
                <option value="BLUETOOTH">${esc(this._t("source_bt"))}</option>
              </select>
            </div>
          </div>

          <div class="c27-section" data-show="show_characters">
            <div class="c27-label">${esc(this._t("character_title"))}</div>
            <div class="c27-charlist" data-el="charlist"></div>
          </div>

          <div class="c27-section" data-el="outfitsection" hidden>
            <div class="c27-section-head">
              <div class="c27-label c27-section-title" data-action="section-toggle" data-section="outfit">${esc(this._t("outfit_title"))}<span class="c27-count" data-el="outfitcount"></span></div>
              <div class="c27-row-actions">
                <button class="c27-toggle" data-action="section-toggle" data-section="outfit" data-el="outfittoggle" title="${esc(this._t("toggle_section"))}"></button>
              </div>
            </div>
            <div data-el="outfitbody" hidden>
              <div class="c27-outfits" data-el="outfitlist"></div>
            </div>
          </div>

          <div class="c27-section" data-el="devicesection" hidden>
            <div class="c27-row" style="justify-content:space-between;">
              <div class="c27-label" style="margin-bottom:0;">${esc(this._t("device_title"))}</div>
              <div class="c27-row" style="gap:6px;">
                <button class="c27-btn" data-action="device-mode" data-mode="LIVE" data-el="modelive">${esc(this._t("mode_live"))}</button>
                <button class="c27-btn" data-action="device-mode" data-mode="MUSIC" data-el="modemusic">${esc(this._t("mode_music"))}</button>
                <button class="c27-btn" data-action="display-toggle" data-el="displaytoggle">${esc(this._t("display_label"))}</button>
              </div>
            </div>
            <div class="c27-row" data-el="displaybrightnessrow">
              <span style="font-size:11px;opacity:.6;">${esc(this._t("display_brightness"))}</span>
              <input type="range" min="0" max="100" step="1" data-el="displaybrightness">
            </div>
          </div>

          <div class="c27-section" data-el="languagesection" hidden>
            <div class="c27-row" style="justify-content:space-between;">
              <div class="c27-label" style="margin-bottom:0;">${esc(this._t("language_title"))}</div>
              <select class="c27-select" data-el="language" style="flex:0 0 auto;"></select>
            </div>
          </div>

          <div class="c27-section" data-el="alarmssection" data-show="show_alarms">
            <div class="c27-section-head">
              <div class="c27-label c27-section-title" data-action="section-toggle" data-section="alarms">${esc(this._t("alarms_title"))}<span class="c27-count" data-el="alarmscount"></span></div>
              <div class="c27-row-actions">
                <button class="c27-icon-btn" data-action="alarm-new" title="${esc(this._t("add"))}">＋</button>
                <button class="c27-toggle" data-action="section-toggle" data-section="alarms" data-el="alarmstoggle" title="${esc(this._t("toggle_section"))}"></button>
              </div>
            </div>
            <div data-el="alarmsbody" hidden>
              <div data-el="alarms"></div>
              <div data-el="alarmform" hidden></div>
            </div>
          </div>

          <div class="c27-section" data-el="notessection" data-show="show_notes">
            <div class="c27-section-head">
              <div class="c27-label c27-section-title" data-action="section-toggle" data-section="notes">${esc(this._t("notes_title"))}<span class="c27-count" data-el="notescount"></span></div>
              <div class="c27-row-actions">
                <button class="c27-icon-btn" data-action="memo-new" title="${esc(this._t("add"))}">＋</button>
                <button class="c27-toggle" data-action="section-toggle" data-section="notes" data-el="notestoggle" title="${esc(this._t("toggle_section"))}"></button>
              </div>
            </div>
            <div data-el="notesbody" hidden>
              <div data-el="notes"></div>
              <div data-el="memoform" hidden></div>
            </div>
          </div>

          ${this._config.show_chat === false ? "" : `
          <div class="c27-section" data-el="chatsection">
            <div class="c27-section-head">
              <div class="c27-label">${esc(this._t("chat_title"))}</div>
              <button class="c27-toggle" data-action="chat-toggle" data-el="chattoggle" title="${esc(this._t("chat_title"))}"></button>
            </div>
            <div data-el="chatbody">
              <div class="c27-chat">
                <div class="c27-history" data-el="history"></div>
                <div class="c27-chat-input-row">
                  <input type="text" placeholder="${esc(this._t("chat_placeholder"))}" data-el="chatinput">
                  <button class="c27-send" data-action="send">${esc(this._t("chat_send"))}</button>
                </div>
              </div>
            </div>
          </div>
          `}

        </div>
      </ha-card>
    `;

    const root = this;

    root.querySelector('[data-el="swatches"]').innerHTML = PRESET_COLORS.map(
      (c) => `<span class="c27-swatch" data-action="swatch" style="background: rgb(${c.join(",")})" data-color="${c.join(",")}"></span>`
    ).join("");

    this._fillLightModes();

    root.addEventListener("click", (ev) => this._onClick(ev));
    root.querySelector('[data-el="brightness"]').addEventListener("change", (ev) =>
      this._setLight({ brightness: Number(ev.target.value) })
    );
    root.querySelector('[data-el="volume"]').addEventListener("change", (ev) =>
      this._call("media_player", "volume_set", {
        entity_id: this._eid("media_player", "musik"),
        volume_level: Number(ev.target.value),
      })
    );
    // One dropdown for everything the LEDs can show: the ambient colour's
    // effects and the saved light scenes. Two separate pickers could both
    // claim to be "current" at the same time.
    root.querySelector('[data-el="effect"]').addEventListener("change", (ev) => {
      const value = ev.target.value;
      if (value.startsWith("scene:")) {
        this._call("select", "select_option", {
          entity_id: this._eid("select", "lichtszene"),
          option: value.slice("scene:".length),
        });
      } else if (value.startsWith("fx:")) {
        this._setLight({ effect: value.slice("fx:".length) });
      }
    });
    for (const btn of root.querySelectorAll('[data-action="device-mode"]')) {
      btn.addEventListener("click", () =>
        this._call("select", "select_option", {
          entity_id: this._eid("select", "geratemodus"),
          option: btn.dataset.mode,
        })
      );
    }
    root.querySelector('[data-el="displaytoggle"]').addEventListener("click", () =>
      this._call("switch", "toggle", { entity_id: this._eid("switch", "display") })
    );
    root.querySelector('[data-el="displaybrightness"]').addEventListener("change", (ev) =>
      this._call("number", "set_value", {
        entity_id: this._eid("number", "display_helligkeit"),
        value: Number(ev.target.value),
      })
    );
    root.querySelector('[data-el="language"]').addEventListener("change", (ev) =>
      this._call("select", "select_option", {
        entity_id: this._eid("select", "ki_sprache"),
        option: ev.target.value,
      })
    );
    root.querySelector('[data-el="source"]').addEventListener("change", (ev) =>
      this._call("media_player", "select_source", {
        entity_id: this._eid("media_player", "musik"),
        source: ev.target.value,
      })
    );
    const chatInput = root.querySelector('[data-el="chatinput"]');
    if (chatInput) {
      chatInput.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") this._sendChat();
      });
    }

    this._applyChatVisibility();
    this._applySections();
    this._applyVisibility();

    this._startBackgroundWork();
  }

  // Split out of _build() (DOM construction, runs once ever) so
  // disconnectedCallback()/connectedCallback() can stop and restart just
  // the timers without rebuilding the card - a card element can be
  // detached and reattached to the DOM without being recreated (view
  // switch, drag-reorder), and _built staying true across that must not
  // mean "background work still running" (it never resumed before this -
  // see _built's other guard in `set hass`, which is exactly why a
  // reattach used to leave progress interpolation, note polling and the
  // activity feed dead for the rest of the card's life).
  _startBackgroundWork() {
    if (!this._progressTimer) this._progressTimer = setInterval(() => this._tickProgress(), 1000);
    if (!this._notesTimer) this._notesTimer = setInterval(() => this._updateNotes(), 5000);
  }

  connectedCallback() {
    if (this._built) this._startBackgroundWork();
  }

  disconnectedCallback() {
    if (this._progressTimer) clearInterval(this._progressTimer);
    this._progressTimer = null;
    if (this._notesTimer) clearInterval(this._notesTimer);
    this._notesTimer = null;
  }

  _onClick(ev) {
    const el = ev.target.closest("[data-action]");
    if (!el) return;
    const action = el.dataset.action;
    if (action === "ack") {
      this._call("button", "press", { entity_id: this._eid("button", "hinweis_quittieren") });
    } else if (action === "light-toggle") {
      const lightId = this._eid("light", "umgebungsfarbe");
      const isOn = this._state(lightId) && this._state(lightId).state === "on";
      this._call("light", isOn ? "turn_off" : "turn_on", { entity_id: lightId });
    } else if (el.classList.contains("c27-swatch")) {
      const [r, g, b] = el.dataset.color.split(",").map(Number);
      this._setLight({ rgb_color: [r, g, b] });
    } else if (action === "prev") {
      this._call("media_player", "media_previous_track", { entity_id: this._eid("media_player", "musik") });
    } else if (action === "next") {
      this._call("media_player", "media_next_track", { entity_id: this._eid("media_player", "musik") });
    } else if (action === "playpause") {
      const mpId = this._eid("media_player", "musik");
      const playing = this._state(mpId) && this._state(mpId).state === "playing";
      this._call("media_player", playing ? "media_pause" : "media_play", { entity_id: mpId });
    } else if (action === "send") {
      this._sendChat();
    } else if (action === "alarm-toggle") {
      const entityId = el.dataset.entity;
      const isOn = this._state(entityId) && this._state(entityId).state === "on";
      this._call("switch", isOn ? "turn_off" : "turn_on", { entity_id: entityId });
    } else if (action === "section-toggle") {
      this._toggleSection(el.dataset.section);
    } else if (action === "outfit-select") {
      this._call("select", "select_option", {
        entity_id: this._eid("select", "outfit"),
        option: el.dataset.option,
      });
    } else if (action === "alarm-enable") {
      const alarm = (this._alarmsFromSensor() || []).find((a) => a.id === el.dataset.id);
      if (alarm) this._saveAlarm({ ...alarm, enabled: !alarm.enabled });
    } else if (action === "alarm-new") {
      this._openAlarmForm(null);
    } else if (action === "alarm-edit") {
      this._openAlarmForm((this._alarmsFromSensor() || []).find((a) => a.id === el.dataset.id) || null);
    } else if (action === "alarm-delete") {
      if (window.confirm(this._t("confirm_delete"))) {
        this._ownerCall("delete_alarm", { alarm_id: el.dataset.id });
      }
    } else if (action === "alarm-day") {
      el.classList.toggle("is-on");
    } else if (action === "alarm-save") {
      this._submitAlarmForm();
    } else if (action === "alarm-cancel") {
      this._closeForm("alarmform");
    } else if (action === "memo-new") {
      this._openMemoForm(null);
    } else if (action === "memo-edit") {
      this._openMemoForm((this._memosFromSensor() || []).find((m) => m.id === el.dataset.id) || null);
    } else if (action === "memo-delete") {
      if (window.confirm(this._t("confirm_delete"))) {
        this._ownerCall("delete_memo", { memo_id: el.dataset.id });
      }
    } else if (action === "memo-save") {
      this._submitMemoForm();
    } else if (action === "memo-cancel") {
      this._closeForm("memoform");
    } else if (action === "memo-reminder-toggle") {
      this._syncMemoReminderFields();
    } else if (action === "note-delete") {
      Promise.resolve(this._hass.callService("todo", "remove_item", {
        entity_id: this._eid("todo", "notizen_erinnerungen"),
        item: [el.dataset.uid],
      })).then(() => this._updateNotes()).catch(() => {});
    } else if (action === "character-select") {
      const option = this._findCharacterOption(el.dataset.slot, el.dataset.name);
      if (option) {
        this._call("select", "select_option", {
          entity_id: this._eid("select", "aktiver_charakter"),
          option,
        });
      }
    } else if (action === "chat-toggle") {
      this._chatVisible = !this._chatVisible;
      this._saveChatVisible(this._chatVisible);
      this._applyChatVisibility();
    }
  }

  // -- chat visibility (client-side only, no backend equivalent) ------------

  _chatVisibleKey() {
    return `code27-card-chat-visible:${this._p}`;
  }

  _loadChatVisible() {
    try {
      const raw = localStorage.getItem(this._chatVisibleKey());
      return raw === null ? null : raw === "1";
    } catch (err) {
      return null; // private browsing / storage disabled - just don't persist
    }
  }

  _saveChatVisible(visible) {
    try {
      localStorage.setItem(this._chatVisibleKey(), visible ? "1" : "0");
    } catch (err) {
      // ignore - still works for this session via this._chatVisible
    }
  }

  _applyChatVisibility() {
    const section = this.querySelector('[data-el="chatsection"]');
    const body = this.querySelector('[data-el="chatbody"]');
    const toggle = this.querySelector('[data-el="chattoggle"]');
    if (!section || !body || !toggle) return; // show_chat: false - nothing to toggle
    // The chat stays visible in music mode too (firmware >= 1.6.0 keeps
    // tools and chat working there); only the user's own toggle hides it.
    section.hidden = false;
    toggle.disabled = false;
    body.hidden = !this._chatVisible;
    toggle.classList.toggle("is-on", this._chatVisible);
  }

  // The select entity's options are character *names*, de-duplicated with
  // a trailing "(slot)" only when two characters share a name (see
  // select.py's _dedupe_names) - there's no dedicated "switch to slot N"
  // service, so a chip click has to reconstruct the exact option string
  // the select entity is actually using right now.
  _findCharacterOption(slotNumber, name) {
    const st = this._state(this._eid("select", "aktiver_charakter"));
    const options = (st && st.attributes.options) || [];
    const withSlot = `${name} (${slotNumber})`;
    if (options.includes(withSlot)) return withSlot;
    if (options.includes(name)) return name;
    return null;
  }

  // The single light dropdown: the ambient colour's effects, then the scenes.
  _fillLightModes() {
    const el = this.querySelector('[data-el="effect"]');
    if (!el) return;
    const labels = this._t("effect_labels");
    const scene = this._state(this._eid("select", "lichtszene"));
    const scenes = this._show("show_scene") && scene ? (scene.attributes.options || []) : [];
    const sig = `${this._lang ? this._lang() : ""}|${scenes.join("|")}`;
    if (el.dataset.sig === sig) return;
    const fx = EFFECT_VALUES.map((e) => `<option value="fx:${esc(e)}">${esc(labels[e] || e)}</option>`).join("");
    const sc = scenes.map((o) => `<option value="scene:${esc(o)}">${esc(o)}</option>`).join("");
    el.innerHTML = `<option value="" hidden></option><optgroup label="${esc(this._t("light_group_ambient"))}">${fx}</optgroup>`
      + (sc ? `<optgroup label="${esc(this._t("light_group_scenes"))}">${sc}</optgroup>` : "");
    el.dataset.sig = sig;
  }

  _setLight(extra) {
    const lightId = this._eid("light", "umgebungsfarbe");
    const st = this._state(lightId);
    const data = { entity_id: lightId, ...extra };
    if (!extra.rgb_color && st && st.attributes.rgb_color) data.rgb_color = st.attributes.rgb_color;
    if (!extra.brightness && st && st.attributes.brightness) data.brightness = st.attributes.brightness;
    if (!extra.effect) {
      // Keep the ambient colour's own effect (a chosen scene is not an effect).
      const reported = st && st.attributes.effect;
      const effect = EFFECT_ALIASES[reported] || reported;
      if (EFFECT_VALUES.includes(effect)) data.effect = effect;
    }
    this._call("light", "turn_on", data);
  }

  _ipFromPrefix() {
    const m = this._p.match(/(\d+)_(\d+)_(\d+)_(\d+)$/);
    return m ? `${m[1]}.${m[2]}.${m[3]}.${m[4]}` : "";
  }

  _sendChat() {
    const input = this.querySelector('[data-el="chatinput"]');
    if (!input) return; // show_chat: false
    const text = (input.value || "").trim();
    if (!text) return;
    const owner = this._owner();
    if (!owner) return;
    this._call(owner.domain, "send_chat_message", {
      config_entry_id: owner.config_entry_id,
      message: text,
    });
    input.value = "";
  }

  _update() {
    if (!this._hass) return;
    const h = this._hass;

    const light = this._state(this._eid("light", "umgebungsfarbe"));
    const rgb = (light && light.attributes.rgb_color) || [255, 138, 76];
    this.querySelector("ha-card").style.setProperty("--c27-glow", rgb.join(","));

    const overviewState = this._state(this._eid("sensor", "charakter_uebersicht"));
    const characters = (overviewState && overviewState.attributes && overviewState.attributes.characters) || [];

    const portrait = this.querySelector('[data-el="portrait"]');
    // The configured static image always wins here (a deliberate,
    // explicit choice - some users have their own portrait and don't want
    // it replaced), falling back to the active character's own portrait
    // entity only when nothing is configured.
    let portraitUrl = this._config.portrait_image;
    if (!portraitUrl) {
      const activeChar = characters.find((c) => c.active);
      if (activeChar) portraitUrl = this._portraitUrlForSlot(activeChar.slot_number);
    }
    if (portraitUrl && portrait.dataset.src !== portraitUrl) {
      portrait.innerHTML = `<img src="${esc(portraitUrl)}">`;
      portrait.dataset.src = portraitUrl;
    } else if (!portraitUrl && portrait.dataset.src) {
      portrait.innerHTML = "";
      delete portrait.dataset.src;
    }

    this.querySelector('[data-el="name"]').textContent =
      this._config.name || "CODE27";
    this.querySelector('[data-el="ip"]').textContent =
      this._config.ip || this._ipFromPrefix();

    const sysOnline = h.states[this._eid("sensor", "build_typ")];
    const dot = this.querySelector('[data-el="onlinedot"]');
    dot.className = "c27-dot " + (sysOnline && sysOnline.state !== "unavailable" ? "c27-online" : "c27-offline");

    this._updateChips();

    const notif = this._state(this._eid("binary_sensor", "hinweis_aktiv"));
    const alertBox = this.querySelector('[data-el="alertbox"]');
    if (notif && notif.state === "on") {
      alertBox.hidden = false;
      this.querySelector('[data-el="alerttext"]').textContent =
        (notif.attributes.message) || this._t("notification_default");
    } else {
      alertBox.hidden = true;
    }

    const lightToggle = this.querySelector('[data-el="lighttoggle"]');
    lightToggle.classList.toggle("is-on", !!(light && light.state === "on"));

    this.querySelectorAll(".c27-swatch").forEach((sw) => {
      const c = sw.dataset.color.split(",").map(Number);
      sw.classList.toggle("is-active", light && light.state === "on" && c.join(",") === rgb.join(","));
    });

    const brightness = this.querySelector('[data-el="brightness"]');
    if (document.activeElement !== brightness) {
      brightness.value = (light && light.attributes.brightness) || 200;
    }

    const effectSel = this.querySelector('[data-el="effect"]');
    const reportedEffect = light && light.attributes.effect;
    const effect = EFFECT_ALIASES[reportedEffect] || reportedEffect;
    this._fillLightModes();
    if (document.activeElement !== effectSel) {
      // The scene picker is empty while an ambient colour is shown on top of
      // the scene, so a real scene state means "a scene is what is lit".
      const scene = this._state(this._eid("select", "lichtszene"));
      const sceneOn = scene && scene.state && !["unknown", "unavailable", "none"].includes(scene.state)
        && (scene.attributes.options || []).includes(scene.state);
      if (sceneOn && this._show("show_scene")) effectSel.value = `scene:${scene.state}`;
      else if (EFFECT_VALUES.includes(effect)) effectSel.value = `fx:${effect}`;
      else effectSel.value = "";
    }

    const mp = this._state(this._eid("media_player", "musik"));
    this.querySelector('[data-el="track"]').textContent =
      (mp && mp.attributes.media_title) || (mp ? mp.state : "–");
    this.querySelector('[data-el="playpause"]').textContent =
      mp && mp.state === "playing" ? "⏸" : "▶";
    const volEl = this.querySelector('[data-el="volume"]');
    if (document.activeElement !== volEl) {
      volEl.value = (mp && mp.attributes.volume_level != null) ? mp.attributes.volume_level : 0.5;
    }
    const sourceEl = this.querySelector('[data-el="source"]');
    if (document.activeElement !== sourceEl && mp && mp.attributes.source) {
      sourceEl.value = mp.attributes.source;
    }
    this._syncProgress(mp);

    const musikmodus = this._state(this._eid("binary_sensor", "musikmodus_aktiv"));
    const musicOpen = !!(musikmodus && musikmodus.state === "on");
    const badge = this.querySelector('[data-el="musikmodus"]');
    badge.textContent = musicOpen ? this._t("music_open") : this._t("music_closed");
    badge.className = "c27-badge" + (musicOpen ? " is-on" : "");
    // Controls are useless while the device's music screen isn't open -
    // every command fails with MODE_INACTIVE (confirmed live) - so hide
    // the whole section instead of showing buttons that just error out.
    this.querySelector('[data-el="musicsection"]').hidden = !musicOpen || !this._show("show_music");
    this._musicOpen = musicOpen;
    this._applyChatVisibility();

    this._updateCharacterOverview(characters);

    this._updateHistory();
    this._applyVisibility();
    this._updateDevice();
    this._updateLanguage();
    this._updateAlarms();
    this._updateMemosFromSensor();
    this._updateOutfits();
    if (!this._notesLoaded) {
      this._notesLoaded = true;
      this._updateNotes();
    }
  }

  _updateHistory() {
    const container = this.querySelector('[data-el="history"]');
    if (!container) return; // show_chat: false - section wasn't rendered

    const lastChar = this._state(this._eid("sensor", "zuletzt_gesagtes_charakter"));
    const all = (lastChar && lastChar.attributes && lastChar.attributes.history) || [];
    const count = Number(this._config.history_count) || 8;
    const shown = all.slice(-count);

    const sig = shown.map((m) => m.chat_id).join(",");
    if (container.dataset.sig === sig) return;
    container.dataset.sig = sig;

    if (!shown.length) {
      container.innerHTML = `<div class="c27-empty">${esc(this._t("chat_empty"))}</div>`;
      return;
    }

    container.innerHTML = shown
      .map((m) => {
        const isUser = m.role === "user";
        return `<div class="c27-bubble${isUser ? " user" : ""}">
          <div class="who">${isUser ? esc(this._t("chat_you")) : esc(this._t("chat_character"))}</div>
          <div>${esc(m.content)}</div>
        </div>`;
      })
      .join("");
    container.scrollTop = container.scrollHeight;
  }

  // -- character overview (all slots, not just the active one) --------------

  // Looks up this device's own per-slot portrait image entity (image.py
  // creates one per character slot, each carrying its slot_number as a
  // plain attribute) rather than guessing an entity_id/translation_key -
  // slot numbers, unlike names, are stable and language-independent.
  // An image entity keeps the same `entity_picture` URL (only its access token
  // rotates, every few minutes) while its picture changes, and the browser
  // caches that URL - so after a character switch the old picture could stay
  // for minutes. The entity's state is the time its picture last changed;
  // adding it to the URL makes every new picture a new URL.
  _imageUrl(st) {
    const url = st && st.attributes && st.attributes.entity_picture;
    if (!url) return null;
    const version = st.state && st.state !== "unknown" && st.state !== "unavailable" ? st.state : "";
    return version ? `${url}${url.includes("?") ? "&" : "?"}v=${encodeURIComponent(version)}` : url;
  }

  _portraitUrlForSlot(slotNumber) {
    if (!this._hass || !this._hass.entities || this._deviceId == null) return null;
    for (const [entityId, entry] of Object.entries(this._hass.entities)) {
      if (!entityId.startsWith("image.") || entry.device_id !== this._deviceId) continue;
      const st = this._state(entityId);
      if (st && st.attributes && Number(st.attributes.slot_number) === Number(slotNumber)) {
        return this._imageUrl(st);
      }
    }
    return null;
  }

  _updateCharacterOverview(characters) {
    const el = this.querySelector('[data-el="charlist"]');
    if (!el) return;

    const sig = characters
      .map((c) => `${c.slot_number}:${c.name}:${c.active}:${this._portraitUrlForSlot(c.slot_number)}`)
      .join("|");
    if (el.dataset.sig === sig) return;
    el.dataset.sig = sig;

    if (!characters.length) {
      el.innerHTML = "";
      return;
    }
    el.innerHTML = characters
      .map((c) => {
        const name = c.name || "?";
        const initial = name.trim().charAt(0).toUpperCase() || "?";
        const portraitUrl = this._portraitUrlForSlot(c.slot_number);
        const avatar = portraitUrl
          ? `<img src="${esc(portraitUrl)}">`
          : esc(initial);
        return `<div class="c27-char-chip${c.active ? " is-active" : ""}" data-action="character-select"
            data-slot="${esc(c.slot_number)}" data-name="${esc(name)}">
          <div class="c27-char-avatar">${avatar}</div>
          <div class="c27-char-name">${esc(name)}</div>
        </div>`;
      })
      .join("");
  }

  _fillSelect(key, entityId) {
    const st = this._state(entityId);
    const el = this.querySelector(`[data-el="${key}"]`);
    if (!st) return;
    const options = st.attributes.options || [];
    const sig = options.join("|");
    if (el.dataset.sig !== sig) {
      el.innerHTML = options.map((o) => `<option value="${esc(o)}">${esc(o)}</option>`).join("");
      el.dataset.sig = sig;
    }
    if (document.activeElement !== el) el.value = st.state;
  }

  // -- diagnostics chips -----------------------------------------------------

  _updateChips() {
    const el = this.querySelector('[data-el="chips"]');
    if (this._config.show_chips === false) {
      el.hidden = true;
      return;
    }
    const lang = this._state(this._eid("sensor", "assistentensprache"));
    const tz = this._state(this._eid("sensor", "zeitzone"));
    const alarms = this._state(this._eid("sensor", "aktive_alarme"));
    const notes = this._state(this._eid("sensor", "notizen"));
    const chips = [
      lang && `${this._t("chip_language")}: ${lang.state.toUpperCase()}`,
      tz && `${this._t("chip_timezone")}: ${tz.state}`,
      alarms && `${alarms.state} ${this._t("chip_alarms")}`,
      notes && `${notes.state} ${this._t("chip_notes")}`,
    ].filter(Boolean);
    const sig = chips.join("|");
    if (el.dataset.sig === sig) return;
    el.dataset.sig = sig;
    el.innerHTML = chips.map((c) => `<span class="c27-chip">${esc(c)}</span>`).join("");
  }

  // -- music progress (client-side interpolated between polls) --------------

  _syncProgress(mp) {
    const position = mp && mp.attributes.media_position;
    const duration = mp && mp.attributes.media_duration;
    const playing = !!(mp && mp.state === "playing");
    const key = `${position}|${duration}|${playing}`;
    if (this._progressKey === key) return;
    this._progressKey = key;
    this._progressSync = {
      position: Number(position) || 0,
      duration: Number(duration) || 0,
      playing,
      at: Date.now(),
    };
    this._tickProgress();
  }

  _tickProgress() {
    const sync = this._progressSync;
    const fill = this.querySelector('[data-el="progressfill"]');
    const posEl = this.querySelector('[data-el="postime"]');
    const durEl = this.querySelector('[data-el="durtime"]');
    if (!sync || !fill) return;
    let pos = sync.position;
    if (sync.playing) {
      pos += Math.floor((Date.now() - sync.at) / 1000);
    }
    pos = Math.min(pos, sync.duration || pos);
    fill.style.width = sync.duration ? `${(pos / sync.duration) * 100}%` : "0%";
    posEl.textContent = fmtTime(pos);
    durEl.textContent = fmtTime(sync.duration);
  }

  // Every show_* flag is "on unless explicitly false" (see the editor).
  _show(key) {
    return !this._config || this._config[key] !== false;
  }

  // Sections that nothing else manages (light, scene, characters, alarms,
  // notes) are shown/hidden here; music, outfit, language, chips, chat and
  // the feed check their flag where they are updated.
  _applyVisibility() {
    if (!this.querySelectorAll) return;
    for (const el of this.querySelectorAll("[data-show]")) {
      el.hidden = !this._show(el.dataset.show);
    }
  }

  // -- Device (firmware 1.6.0: mode LIVE/MUSIC, display on/off, display brightness) --

  _updateDevice() {
    const section = this.querySelector('[data-el="devicesection"]');
    if (!section || !this._hass || !this._hass.states) return;
    const mode = this._state(this._eid("select", "geratemodus"));
    const screen = this._state(this._eid("switch", "display"));
    const bright = this._state(this._eid("number", "display_helligkeit"));
    const usable = (s) => s && s.state !== "unavailable" && s.state !== "unknown";
    if (!this._show("show_device") || !(usable(mode) || usable(screen) || usable(bright))) {
      section.hidden = true;
      return;
    }
    section.hidden = false;
    for (const [el, value] of [["modelive", "LIVE"], ["modemusic", "MUSIC"]]) {
      const btn = this.querySelector(`[data-el="${el}"]`);
      btn.hidden = !usable(mode);
      btn.classList.toggle("is-active", usable(mode) && mode.state === value);
    }
    const toggle = this.querySelector('[data-el="displaytoggle"]');
    toggle.hidden = !usable(screen);
    toggle.classList.toggle("is-active", usable(screen) && screen.state === "on");
    const row = this.querySelector('[data-el="displaybrightnessrow"]');
    row.hidden = !usable(bright);
    const slider = this.querySelector('[data-el="displaybrightness"]');
    if (usable(bright) && this.getRootNode().activeElement !== slider) {
      slider.value = bright.state;
    }
  }

  // -- AI language (needs firmware 1.5.3's language select; hidden without it) --

  _updateLanguage() {
    const section = this.querySelector('[data-el="languagesection"]');
    if (!section || !this._hass || !this._hass.states) return;
    const select = this._state(this._eid("select", "ki_sprache"));
    const options = select && select.attributes && select.attributes.options;
    if (!this._show("show_language") || !select || select.state === "unavailable" ||
        !Array.isArray(options) || !options.length) {
      section.hidden = true;
      return;
    }
    section.hidden = false;
    const el = this.querySelector('[data-el="language"]');
    const sig = JSON.stringify(options);
    if (el.dataset.sig !== sig) {
      el.dataset.sig = sig;
      el.innerHTML = options.map((o) => `<option value="${esc(o)}">${esc(o)}</option>`).join("");
    }
    el.value = select.state;
  }

  // -- foldable sections ----------------------------------------------------------
  // Alarms, notes and outfits start folded; the choice is remembered per
  // device in this browser (like the chat's toggle). A section with an open
  // edit form stays open, and opening a form unfolds its section for now
  // without saving that as the user's choice.

  _sectionStorageKey(key) {
    return `code27-card-section:${this._p}:${key}`;
  }

  _sectionOpen(key) {
    if (!this._sectionState) this._sectionState = {};
    if (!(key in this._sectionState)) {
      let stored = null;
      try { stored = localStorage.getItem(this._sectionStorageKey(key)); } catch (_) { /* private mode */ }
      this._sectionState[key] = stored === "1";
    }
    return this._sectionState[key];
  }

  _formOpen(key) {
    const form = this.querySelector(`[data-el="${key === "alarms" ? "alarmform" : "memoform"}"]`);
    return Boolean(form && !form.hidden);
  }

  _setSectionOpen(key, open, persist = true) {
    this._sectionOpen(key);
    this._sectionState[key] = open;
    if (persist) {
      try { localStorage.setItem(this._sectionStorageKey(key), open ? "1" : "0"); } catch (_) { /* session only */ }
    }
    this._applySections();
    if (open && key === "outfit") this._updateOutfits();
  }

  _toggleSection(key) {
    if (!SECTION_KEYS.includes(key)) return;
    const body = this.querySelector(`[data-el="${key}body"]`);
    const visible = body && !body.hidden;
    if (visible) {
      // Folding a section also drops its unsaved form.
      if (key === "alarms") this._closeForm("alarmform");
      if (key === "notes") this._closeForm("memoform");
    }
    this._setSectionOpen(key, !visible);
  }

  _applySections() {
    for (const key of SECTION_KEYS) {
      const body = this.querySelector(`[data-el="${key}body"]`);
      const toggle = this.querySelector(`[data-el="${key}toggle"]`);
      if (!body || !toggle) continue;
      const open = this._sectionOpen(key) || ((key === "alarms" || key === "notes") && this._formOpen(key));
      body.hidden = !open;
      toggle.classList.toggle("is-on", open);
    }
  }

  _setCount(key, text) {
    const el = this.querySelector(`[data-el="${key}count"]`);
    if (el) el.textContent = text ? `· ${text}` : "";
  }

  // -- outfits (needs firmware 1.5.3's outfit select; hidden without it) -------

  // The outfit preview image entities (image.py) carry the outfit's display
  // name as `label` - the same text the outfit select offers.
  _outfitPreviews() {
    const previews = {};
    if (!this._hass || !this._hass.entities || this._deviceId == null) return previews;
    for (const [entityId, entry] of Object.entries(this._hass.entities)) {
      if (!entityId.startsWith("image.") || entry.device_id !== this._deviceId) continue;
      const st = this._state(entityId);
      const attrs = st && st.attributes;
      if (attrs && attrs.cloth_id != null && attrs.label && attrs.entity_picture) {
        previews[attrs.label] = this._imageUrl(st);
      }
    }
    return previews;
  }

  _updateOutfits() {
    const section = this.querySelector('[data-el="outfitsection"]');
    if (!section || !this._hass || !this._hass.states) return;
    const select = this._state(this._eid("select", "outfit"));
    const options = select && select.attributes && select.attributes.options;
    if (!this._show("show_outfit") || !select || select.state === "unavailable" ||
        !Array.isArray(options) || !options.length) {
      section.hidden = true;
      return;
    }
    section.hidden = false;
    const loadingSensor = this._state(this._eid("binary_sensor", "model_loading"));
    const loading = Boolean(loadingSensor && loadingSensor.state === "on");
    this._setCount("outfit", loading ? this._t("outfit_loading") : select.state);

    const body = this.querySelector('[data-el="outfitbody"]');
    if (!body || body.hidden) return; // folded: load no images, render nothing
    const list = this.querySelector('[data-el="outfitlist"]');
    const previews = this._outfitPreviews();
    const sig = JSON.stringify([options, select.state, previews]);
    if (list.dataset.sig !== sig) {
      list.dataset.sig = sig;
      list.innerHTML = options
        .map((name) => {
          const url = previews[name];
          const avatar = url ? `<img src="${esc(url)}">` : esc(String(name).slice(0, 1).toUpperCase());
          return `<button class="c27-char-chip${name === select.state ? " is-active" : ""}" data-action="outfit-select" data-option="${esc(name)}">
            <div class="c27-char-avatar">${avatar}</div>
            <div class="c27-char-name">${esc(name)}</div>
          </button>`;
        })
        .join("");
    }
    if (list.classList) list.classList.toggle("is-loading", loading);
  }

  // -- alarms ------------------------------------------------------------------
  // Read from the "active alarms" sensor's `alarms` attribute (every alarm,
  // structured) and edited through the create/update/delete_alarm services;
  // older backends without that attribute fall back to per-alarm switches.

  // Tries the card's identifiers one after the other (device id, config entry,
  // device prefix): a stale one must not hide a device the others still find.
  _owner() {
    const selectors = [
      { device_id: this._deviceId },
      { config_entry_id: this._config && this._config.config_entry_id },
      { device_prefix: this._p },
    ];
    for (const selector of selectors) {
      if (!Object.values(selector)[0]) continue;
      const owner = code27Owner(this._hass, selector);
      if (owner) return owner;
    }
    return null;
  }

  // Calls a service of this device's owning integration; resolves to null on
  // success or to the error message, so forms can show why a save failed.
  _ownerCall(service, data) {
    const owner = this._owner();
    if (!owner) return Promise.resolve(this._ownerError());
    return Promise.resolve(
      this._hass.callService(owner.domain, service, { config_entry_id: owner.config_entry_id, ...data })
    ).then(() => null, (err) => (err && err.message) || String(err));
  }

  // Says why the owner lookup failed, so a dashboard that cannot reach its
  // device can be diagnosed from the message alone.
  _ownerError() {
    let devices = [];
    try { devices = code27Devices(this._hass); } catch (_) { /* no registry yet */ }
    const summary = devices.map((d) => `${d.domain}:${d.device_id}`).join(", ") || "none";
    return `CODE27 device not found (registry: ${summary}; card device_id: ${this._deviceId || "-"}, ` +
      `config_entry_id: ${(this._config && this._config.config_entry_id) || "-"}, prefix: ${this._p || "-"})`;
  }

  _alarmsFromSensor() {
    const sensor = this._hass && this._hass.states ? this._state(this._eid("sensor", "aktive_alarme")) : null;
    const alarms = sensor && sensor.attributes && sensor.attributes.alarms;
    return Array.isArray(alarms) ? alarms : null;
  }

  _daysText(days) {
    if (!days || !days.length) return this._t("alarm_once");
    if (days.length === 7) return this._t("alarm_daily");
    const labels = this._t("days");
    return DAY_KEYS.filter((d) => days.includes(d)).map((d) => labels[d]).join(" ");
  }

  _updateAlarms() {
    const alarms = this._alarmsFromSensor();
    if (alarms === null) {
      this._updateAlarmsLegacy();
      return;
    }
    const el = this.querySelector('[data-el="alarms"]');
    const enabled = alarms.filter((a) => a.enabled).length;
    this._setCount("alarms", alarms.length ? `${enabled} ${this._t("count_active")}` : "");
    const sig = JSON.stringify(alarms);
    if (el.dataset.sig === sig) return;
    el.dataset.sig = sig;
    this._undockForm("alarmform");
    if (!alarms.length) {
      el.innerHTML = `<div class="c27-empty">${esc(this._t("alarms_empty"))}</div>`;
      return;
    }
    el.innerHTML = alarms
      .map((a) => `<div data-slot="${esc(a.id)}"><div class="c27-list-row">
          <div class="c27-item-main">
            <span class="c27-item-title is-time">${esc(a.time)}</span>
            <span class="c27-item-sub">${esc([a.label, this._daysText(a.days)].filter(Boolean).join(" · "))}</span>
          </div>
          <div class="c27-row-actions">
            <button class="c27-toggle${a.enabled ? " is-on" : ""}" data-action="alarm-enable" data-id="${esc(a.id)}"></button>
            <button class="c27-icon-btn" data-action="alarm-edit" data-id="${esc(a.id)}" title="${esc(this._t("edit"))}">✎</button>
            <button class="c27-del-btn" data-action="alarm-delete" data-id="${esc(a.id)}" title="${esc(this._t("notes_delete"))}">✕</button>
          </div>
        </div></div>`)
      .join("");
    this._dockForm("alarmform", "alarms");
  }


  _updateAlarmsLegacy() {
    const el = this.querySelector('[data-el="alarms"]');
    const prefix = `switch.${this._p}_alarm_`;
    const entries = Object.values(this._hass.states)
      .filter((s) => s.entity_id.startsWith(prefix))
      .sort((a, b) => a.entity_id.localeCompare(b.entity_id));

    const sig = entries.map((s) => `${s.entity_id}:${s.state}`).join(",");
    if (el.dataset.sig === sig) return;
    el.dataset.sig = sig;

    if (!entries.length) {
      el.innerHTML = `<div class="c27-empty">${esc(this._t("alarms_empty"))}</div>`;
      return;
    }
    el.innerHTML = entries
      .map((s) => {
        const isOn = s.state === "on";
        const label = s.attributes.friendly_name || s.entity_id;
        return `<div class="c27-list-row">
          <span>${esc(label)}</span>
          <button class="c27-toggle${isOn ? " is-on" : ""}" data-action="alarm-toggle" data-entity="${s.entity_id}"></button>
        </div>`;
      })
      .join("");
  }


  // An edit form normally sits below its list ("new" item). While an existing
  // item is being edited it is docked into that item's slot, directly under
  // its row. A list re-render would destroy a docked form, so the form is
  // moved back home first and docked again afterwards.
  _undockForm(name) {
    const form = this.querySelector(`[data-el="${name}"]`);
    if (form && form._home && form.parentNode !== form._home) form._home.appendChild(form);
  }

  _dockForm(name, listName) {
    const form = this.querySelector(`[data-el="${name}"]`);
    const list = this.querySelector(`[data-el="${listName}"]`);
    if (!form || !list || form.hidden) return;
    const id = form.dataset.editing;
    if (!id || !list.children) return;
    const slot = Array.from(list.children).find((c) => c.dataset && c.dataset.slot === id);
    if (slot) slot.appendChild(form);
  }

  _openForm(name, listName, id, html) {
    const el = this.querySelector(`[data-el="${name}"]`);
    if (!el._home) el._home = el.parentNode;
    this._undockForm(name);
    el.hidden = false;
    el.dataset.editing = id || "";
    el.innerHTML = html;
    this._dockForm(name, listName);
    if (el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
    return el;
  }

  _closeForm(name) {
    const el = this.querySelector(`[data-el="${name}"]`);
    if (!el) return;
    this._undockForm(name);
    el.hidden = true;
    el.innerHTML = "";
    delete el.dataset.editing;
  }

  _openAlarmForm(alarm) {
    this._setSectionOpen("alarms", true, false);
    const labels = this._t("days");
    const days = alarm ? alarm.days || [] : [];
    const el = this._openForm("alarmform", "alarms", alarm ? alarm.id : "", `<div class="c27-form">
      <div class="c27-form-row">
        <input type="time" data-el="alarmtime" value="${esc(alarm ? alarm.time : "07:00")}" title="${esc(this._t("alarm_time"))}">
        <input type="text" data-el="alarmlabel" value="${esc(alarm ? alarm.label : "")}" placeholder="${esc(this._t("alarm_label"))}">
      </div>
      <div class="c27-daychips">${DAY_KEYS.map(
        (d) => `<button type="button" class="c27-daychip${days.includes(d) ? " is-on" : ""}" data-action="alarm-day" data-day="${d}">${esc(labels[d])}</button>`
      ).join("")}</div>
      <div class="c27-form-error" data-el="alarmerror"></div>
      <div class="c27-form-row">
        <button class="c27-send" data-action="alarm-save">${esc(this._t("save"))}</button>
        <button class="c27-btn" data-action="alarm-cancel">${esc(this._t("cancel"))}</button>
      </div>
    </div>`);
    el.dataset.enabled = alarm ? String(alarm.enabled) : "true";
  }

  async _submitAlarmForm() {
    const el = this.querySelector('[data-el="alarmform"]');
    const error = el.querySelector('[data-el="alarmerror"]');
    const time = el.querySelector('[data-el="alarmtime"]').value;
    if (!/^[0-9]{1,2}:[0-9]{2}$/.test(time || "")) {
      error.textContent = this._t("alarm_no_time");
      return;
    }
    const [hour, minute] = time.split(":").map(Number);
    const message = await this._saveAlarm({
      id: el.dataset.editing || null,
      hour,
      minute,
      label: el.querySelector('[data-el="alarmlabel"]').value.trim(),
      days: Array.from(el.querySelectorAll(".c27-daychip.is-on")).map((b) => b.dataset.day),
      enabled: el.dataset.enabled !== "false",
    });
    if (message) error.textContent = message;
    else this._closeForm("alarmform");
  }

  _saveAlarm(alarm) {
    const data = {
      hour: alarm.hour,
      minute: alarm.minute,
      label: alarm.label || "",
      days: alarm.days || [],
      enabled: alarm.enabled !== false,
    };
    if (alarm.id) return this._ownerCall("update_alarm", { alarm_id: alarm.id, ...data });
    return this._ownerCall("create_alarm", data);
  }

  // -- open notes (via direct todo/item/list WS call) -------------------------

  _memosFromSensor() {
    const sensor = this._hass && this._hass.states ? this._state(this._eid("sensor", "notizen")) : null;
    const memos = sensor && sensor.attributes && sensor.attributes.memos;
    return Array.isArray(memos) ? memos : null;
  }

  // Preferred path: the notes sensor's `memos` attribute (structured, so the
  // list can show reminder dates and be edited in place). Returns false on
  // older backends, which fall back to the to-do WS call below.
  _updateMemosFromSensor() {
    const memos = this._memosFromSensor();
    if (memos === null) return false;
    const open = memos.filter((m) => !m.expired);
    this._setCount("notes", open.length ? String(open.length) : "");
    const el = this.querySelector('[data-el="notes"]');
    const sig = JSON.stringify(open);
    if (el.dataset.sig === sig) return true;
    el.dataset.sig = sig;
    this._undockForm("memoform");
    if (!open.length) {
      el.innerHTML = `<div class="c27-empty">${esc(this._t("notes_empty"))}</div>`;
      return true;
    }
    el.innerHTML = open
      .map((m) => {
        const sub = m.type === "reminder" && m.date
          ? `📅 ${m.date} ${m.time || ""}`.trim()
          : String(m.body || "").slice(0, 80);
        return `<div data-slot="${esc(m.id)}"><div class="c27-list-row">
          <div class="c27-item-main">
            <span class="c27-item-title">${esc(m.title)}</span>
            ${sub ? `<span class="c27-item-sub">${esc(sub)}</span>` : ""}
          </div>
          <div class="c27-row-actions">
            <button class="c27-icon-btn" data-action="memo-edit" data-id="${esc(m.id)}" title="${esc(this._t("edit"))}">✎</button>
            <button class="c27-del-btn" data-action="memo-delete" data-id="${esc(m.id)}" title="${esc(this._t("notes_delete"))}">✕</button>
          </div>
        </div></div>`;
      })
      .join("");
    this._dockForm("memoform", "notes");
    return true;
  }

  _openMemoForm(memo) {
    this._setSectionOpen("notes", true, false);
    const reminder = Boolean(memo && memo.type === "reminder" && memo.date);
    this._openForm("memoform", "notes", memo ? memo.id : "", `<div class="c27-form">
      <input type="text" data-el="memotitle" value="${esc(memo ? memo.title : "")}" placeholder="${esc(this._t("memo_title"))}">
      <textarea data-el="memobody" placeholder="${esc(this._t("memo_body"))}">${esc(memo ? memo.body : "")}</textarea>
      <label class="c27-check"><input type="checkbox" data-el="memoreminder" data-action="memo-reminder-toggle"${reminder ? " checked" : ""}>${esc(this._t("memo_reminder"))}</label>
      <div class="c27-form-row" data-el="memoschedule"${reminder ? "" : " hidden"}>
        <input type="date" data-el="memodate" value="${esc(reminder ? memo.date : "")}" title="${esc(this._t("memo_date"))}">
        <input type="time" data-el="memotime" value="${esc(reminder ? memo.time || "09:00" : "09:00")}" title="${esc(this._t("memo_time"))}">
      </div>
      <div class="c27-form-error" data-el="memoerror"></div>
      <div class="c27-form-row">
        <button class="c27-send" data-action="memo-save">${esc(this._t("save"))}</button>
        <button class="c27-btn" data-action="memo-cancel">${esc(this._t("cancel"))}</button>
      </div>
    </div>`);
  }

  _syncMemoReminderFields() {
    const el = this.querySelector('[data-el="memoform"]');
    const on = el.querySelector('[data-el="memoreminder"]').checked;
    el.querySelector('[data-el="memoschedule"]').hidden = !on;
  }

  async _submitMemoForm() {
    const el = this.querySelector('[data-el="memoform"]');
    const error = el.querySelector('[data-el="memoerror"]');
    const title = el.querySelector('[data-el="memotitle"]').value.trim();
    if (!title) {
      error.textContent = this._t("memo_no_title");
      return;
    }
    const reminder = el.querySelector('[data-el="memoreminder"]').checked;
    const date = el.querySelector('[data-el="memodate"]').value;
    if (reminder && !date) {
      error.textContent = this._t("memo_no_date");
      return;
    }
    const data = { title, body: el.querySelector('[data-el="memobody"]').value };
    if (reminder) {
      data.date = date;
      data.time = el.querySelector('[data-el="memotime"]').value || "09:00";
    }
    const id = el.dataset.editing;
    // An empty date turns an edited reminder back into a plain note.
    if (id && !reminder) data.date = "";
    const message = id
      ? await this._ownerCall("update_memo", { memo_id: id, ...data })
      : await this._ownerCall("create_memo", data);
    if (message) error.textContent = message;
    else this._closeForm("memoform");
  }

  async _updateNotes() {
    if (this._updateMemosFromSensor()) return;
    if (!this._hass || !this._hass.connection) return;
    if (this._notesInFlight || Date.now() < this._notesRetryAt) return;
    const connection = this._hass.connection;
    const epoch = this._notesEpoch;
    this._notesInFlight = true;
    let items;
    try {
      const result = await connection.sendMessagePromise({
        type: "todo/item/list",
        entity_id: this._eid("todo", "notizen_erinnerungen"),
      });
      if (epoch !== this._notesEpoch) return;
      items = result.items || [];
      this._notesFailures = 0;
      this._notesRetryAt = 0;
    } catch (err) {
      if (epoch === this._notesEpoch) {
        this._notesFailures = Math.min(this._notesFailures + 1, 5);
        this._notesRetryAt = Date.now() + Math.min(60000, 5000 * 2 ** this._notesFailures);
      }
      return;
    } finally {
      if (epoch === this._notesEpoch) this._notesInFlight = false;
    }
    const open = items.filter((i) => i.status === "needs_action");
    const el = this.querySelector('[data-el="notes"]');
    const sig = open.map((i) => i.uid + i.summary).join(",");
    if (el.dataset.sig === sig) return;
    el.dataset.sig = sig;

    if (!open.length) {
      el.innerHTML = `<div class="c27-empty">${esc(this._t("notes_empty"))}</div>`;
      return;
    }
    el.innerHTML = open
      .map(
        (i) => `<div class="c27-list-row">
          <span>${esc(i.summary)}</span>
          <button class="c27-del-btn" data-action="note-delete" data-uid="${esc(i.uid)}" title="${esc(this._t("notes_delete"))}">✕</button>
        </div>`
      )
      .join("");
  }
}

function fmtTime(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}

// Visual editor for the Lovelace card-picker UI, so device_prefix and
// config_entry_id don't have to be hand-typed in YAML: pick a device from a
// dropdown, everything else is derived (see findCode27Devices above). name
// and portrait_image stay as plain text fields - they're cosmetic overrides
// with no registry equivalent to pick from.
// Boolean flags this card understands, all "on unless explicitly false" -
// checkbox is checked (feature visible) whenever the key is absent/true.
const EDITOR_SHOW_FLAGS = [
  { key: "show_chips", label: "Show diagnostic chips (language, timezone, alarms, notes)" },
  { key: "show_light", label: "Show ambient light (colour, effect, brightness)" },
  { key: "show_music", label: "Show music player (while the music page is open)" },
  { key: "show_scene", label: "Show light scene" },
  { key: "show_characters", label: "Show character picker" },
  { key: "show_outfit", label: "Show outfit picker (firmware 1.5.3)" },
  { key: "show_language", label: "Show AI language selector (firmware 1.5.3)" },
  { key: "show_device", label: "Show device controls (mode, display, display brightness)" },
  { key: "show_alarms", label: "Show alarms" },
  { key: "show_notes", label: "Show notes and reminders" },
  { key: "show_chat", label: "Show chat section" },
];

class Code27CardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _emitChange(newConfig) {
    this._config = newConfig;
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: newConfig },
        bubbles: true,
        composed: true,
      })
    );
  }

  // setConfig() and the hass setter can arrive in either order (in
  // particular, hass can be set before the real saved config comes in
  // via setConfig on an existing card) - building the DOM once but then
  // *always* re-syncing values from this._config on every call is what
  // makes editing an existing card actually show its current values
  // instead of freezing on whatever was there at the first render.
  _render() {
    if (!this._hass) return;
    this._config = this._config || {};
    if (!this._built) {
      this._buildSkeleton();
      this._built = true;
    }
    this._syncDeviceOptions();
    this._syncFieldValues();
  }

  _portraitPlaceholder() {
    const hass = this._hass;
    const components = hass?.config?.components;
    const haseAvailable = components?.includes?.("hase") || Boolean(hass?.services?.hase);
    return haseAvailable ? "/hase-cards/portrait.png" : "/code27-cards/portrait.png";
  }

  _buildSkeleton() {
    const fieldStyle =
      "padding:8px;border-radius:4px;border:1px solid var(--divider-color,#444);" +
      "background:var(--card-background-color,#1c1c1c);color:var(--primary-text-color);";
    const labelStyle =
      "display:flex;flex-direction:column;gap:4px;font-size:14px;color:var(--primary-text-color);";

    this.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:16px;padding:16px 0;">
        <label style="${labelStyle}">
          CODE27 device
          <select data-el="device" style="${fieldStyle}"></select>
        </label>
        <label style="${labelStyle}">
          Display name (optional)
          <input data-el="name" type="text" placeholder="CODE27" style="${fieldStyle}">
        </label>
        <label style="${labelStyle}">
          Fallback portrait image URL (optional)
          <input data-el="portrait" type="text" placeholder="${esc(this._portraitPlaceholder())}" style="${fieldStyle}">
          <span style="font-size:12px;opacity:.6;">Only used if the active character's portrait entity isn't available yet.</span>
        </label>
        <label style="${labelStyle}">
          Chat history length (messages shown)
          <input data-el="history_count" type="number" min="1" max="50" placeholder="8" style="${fieldStyle}">
        </label>
        <div style="display:flex;flex-direction:column;gap:10px;">
          ${EDITOR_SHOW_FLAGS.map(
            (flag) => `
            <label style="display:flex;align-items:center;gap:8px;font-size:14px;color:var(--primary-text-color);cursor:pointer;">
              <input data-el="${flag.key}" type="checkbox">
              ${esc(flag.label)}
            </label>`
          ).join("")}
        </div>
      </div>
    `;

    this.querySelector('[data-el="device"]').addEventListener("change", (ev) => {
      const prefix = ev.target.value;
      const match = (this._devices || []).find((d) => d.prefix === prefix);
      this._emitChange({
        ...this._config,
        device_prefix: prefix,
        device_id: match ? match.device_id : this._config.device_id,
        config_entry_id: match ? match.config_entry_id : this._config.config_entry_id,
      });
    });
    this.querySelector('[data-el="name"]').addEventListener("input", (ev) => {
      this._emitChange({ ...this._config, name: ev.target.value });
    });
    this.querySelector('[data-el="portrait"]').addEventListener("input", (ev) => {
      this._emitChange({ ...this._config, portrait_image: ev.target.value });
    });
    this.querySelector('[data-el="history_count"]').addEventListener("input", (ev) => {
      const value = ev.target.value === "" ? undefined : Number(ev.target.value);
      const newConfig = { ...this._config };
      if (value === undefined) delete newConfig.history_count;
      else newConfig.history_count = value;
      this._emitChange(newConfig);
    });
    for (const flag of EDITOR_SHOW_FLAGS) {
      this.querySelector(`[data-el="${flag.key}"]`).addEventListener("change", (ev) => {
        const newConfig = { ...this._config };
        if (ev.target.checked) delete newConfig[flag.key];
        else newConfig[flag.key] = false;
        this._emitChange(newConfig);
      });
    }
  }

  // Rebuilt every render (not just once): a device can appear/disappear
  // from the registry after this editor is already open, and - unlike the
  // other fields - this list depends on `hass`, not just `this._config`.
  // Skipped while the dropdown itself has focus so an open selection
  // isn't yanked out from under the user mid-click.
  _syncDeviceOptions() {
    const select = this.querySelector('[data-el="device"]');
    if (!select || document.activeElement === select) return;
    const devices = findCode27Devices(this._hass);
    this._devices = devices;
    const current = this._config.device_prefix || "";
    const hasCurrent = devices.some((d) => d.prefix === current);
    select.innerHTML = `
      <option value="">${devices.length ? "Select a device…" : "No CODE27 device found"}</option>
      ${devices
        .map(
          (d) =>
            `<option value="${esc(d.prefix)}" ${d.prefix === current ? "selected" : ""}>${esc(d.name)}</option>`
        )
        .join("")}
      ${
        current && !hasCurrent
          ? `<option value="${esc(current)}" selected>${esc(current)} (not found)</option>`
          : ""
      }
    `;
  }

  // The actual bugfix: this used to only ever run once (guarded by
  // `_built`), so an existing card's real config - which can arrive via
  // setConfig() *after* hass already triggered the first (config-less)
  // render - never made it into the form. Now it re-applies on every
  // setConfig()/hass update, skipping whichever field the user is
  // currently typing in so it doesn't fight their cursor.
  _syncFieldValues() {
    const name = this.querySelector('[data-el="name"]');
    if (name && document.activeElement !== name) name.value = this._config.name || "";

    const portrait = this.querySelector('[data-el="portrait"]');
    if (portrait && document.activeElement !== portrait) {
      portrait.placeholder = this._portraitPlaceholder();
      portrait.value = this._config.portrait_image || "";
    }

    const historyCount = this.querySelector('[data-el="history_count"]');
    if (historyCount && document.activeElement !== historyCount) {
      historyCount.value = this._config.history_count != null ? this._config.history_count : "";
    }

    for (const flag of EDITOR_SHOW_FLAGS) {
      const checkbox = this.querySelector(`[data-el="${flag.key}"]`);
      if (checkbox) checkbox.checked = this._config[flag.key] !== false;
    }
  }
}

customElements.define("code27-card-editor", Code27CardEditor);

if (!customElements.get("code27-card")) {
  customElements.define("code27-card", Code27Card);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "code27-card")) {
  window.customCards.push({
    type: "code27-card",
    name: "CODE27",
    description: "Steuerung, Chat und Status für ein CODE27-Gerät.",
  });
}
