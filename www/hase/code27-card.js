/* CODE27 dashboard card. Vanilla web component, no build step / dependencies. */

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

// The light entity's effect *values* are fixed German words (that's what
// light.py's service call expects, unrelated to viewer language) - only the
// displayed label is translated.
const EFFECT_VALUES = ["Statisch", "Atmen", "Pulsieren", "Blinken", "Lauflicht"];

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
    character_title: "Charakter",
    alarms_title: "Alarme",
    alarms_empty: "Keine Alarme",
    notes_title: "Offene Notizen",
    notes_empty: "Keine offenen Notizen",
    notes_delete: "Löschen",
    chat_title: "Chat",
    chat_placeholder: "Nachricht an CODE27…",
    chat_send: "Senden",
    chat_empty: "Noch keine Nachrichten",
    chat_you: "Du",
    chat_character: "Charakter",
    feed_title: "Aktivität (Assist-Bridge)",
    chip_language: "Sprache",
    chip_timezone: "Zeitzone",
    chip_alarms: "Alarm(e)",
    chip_notes: "Notiz(en)",
    effect_labels: {
      Statisch: "Statisch",
      Atmen: "Atmen",
      Pulsieren: "Pulsieren",
      Blinken: "Blinken",
      Lauflicht: "Lauflicht",
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
    character_title: "Character",
    alarms_title: "Alarms",
    alarms_empty: "No alarms",
    notes_title: "Open notes",
    notes_empty: "No open notes",
    notes_delete: "Delete",
    chat_title: "Chat",
    chat_placeholder: "Message to CODE27…",
    chat_send: "Send",
    chat_empty: "No messages yet",
    chat_you: "You",
    chat_character: "Character",
    feed_title: "Activity (Assist bridge)",
    chip_language: "Language",
    chip_timezone: "Timezone",
    chip_alarms: "alarm(s)",
    chip_notes: "note(s)",
    effect_labels: {
      Statisch: "Static",
      Atmen: "Breathe",
      Pulsieren: "Pulse",
      Blinken: "Blink",
      Lauflicht: "Chase",
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
  if (!hass || !hass.entities) return [];
  const devices = hass.devices || {};
  const results = [];
  for (const [entityId, entry] of Object.entries(hass.entities)) {
    if (entry.translation_key !== "active_character") continue;
    const device = entry.device_id ? devices[entry.device_id] : null;
    // "prefix" is only used as a human-readable YAML config value and as
    // the legacy string-concatenation fallback in _eid() below, never as
    // the primary lookup anymore - so an exact reconstruction isn't
    // critical, but both supported translations of "active character"
    // ("aktiver_charakter", "active_character") happen to be exactly two
    // underscore-separated words, so stripping two trailing segments
    // recovers it correctly for both.
    const prefix = entityId.replace(/^sensor\./, "").replace(/(_[^_]+){2}$/, "");
    results.push({
      prefix,
      device_id: entry.device_id || "",
      config_entry_id: (device && device.config_entries && device.config_entries[0]) || "",
      name: (device && (device.name_by_user || device.name)) || prefix,
    });
  }
  return results;
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
          .c27-feed { max-height:140px; overflow-y:auto; font-size:11px; font-family: var(--code-font-family, monospace); }
          .c27-feed-line { padding:3px 0; opacity:.8; border-bottom:1px dashed rgba(255,255,255,0.06); }
          .c27-feed-line .t { opacity:.45; margin-right:6px; }
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

          <div class="c27-section">
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

          <div class="c27-section">
            <div class="c27-label">${esc(this._t("scene_title"))}</div>
            <select class="c27-select" data-el="scene" data-action="scene"></select>
          </div>

          <div class="c27-section">
            <div class="c27-label">${esc(this._t("character_title"))}</div>
            <div class="c27-charlist" data-el="charlist"></div>
          </div>

          <div class="c27-section">
            <div class="c27-label">${esc(this._t("alarms_title"))}</div>
            <div data-el="alarms"></div>
          </div>

          <div class="c27-section">
            <div class="c27-label">${esc(this._t("notes_title"))}</div>
            <div data-el="notes"></div>
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

          <div class="c27-section" data-el="feedsection" hidden>
            <div class="c27-label">${esc(this._t("feed_title"))}</div>
            <div class="c27-chat c27-feed" data-el="feed"></div>
          </div>
        </div>
      </ha-card>
    `;

    const root = this;

    root.querySelector('[data-el="swatches"]').innerHTML = PRESET_COLORS.map(
      (c) => `<span class="c27-swatch" data-action="swatch" style="background: rgb(${c.join(",")})" data-color="${c.join(",")}"></span>`
    ).join("");

    const effectLabels = this._t("effect_labels");
    root.querySelector('[data-el="effect"]').innerHTML = EFFECT_VALUES.map(
      (e) => `<option value="${esc(e)}">${esc(effectLabels[e] || e)}</option>`
    ).join("");

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
    root.querySelector('[data-el="scene"]').addEventListener("change", (ev) =>
      this._call("select", "select_option", {
        entity_id: this._eid("select", "lichtszene"),
        option: ev.target.value,
      })
    );
    root.querySelector('[data-el="effect"]').addEventListener("change", (ev) =>
      this._setLight({ effect: ev.target.value })
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

    this._feedLines = [];
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
    if (!this._unsubFeed) this._subscribeFeed();
  }

  connectedCallback() {
    if (this._built) this._startBackgroundWork();
  }

  disconnectedCallback() {
    if (this._progressTimer) clearInterval(this._progressTimer);
    this._progressTimer = null;
    if (this._notesTimer) clearInterval(this._notesTimer);
    this._notesTimer = null;
    if (this._unsubFeed) this._unsubFeed();
    this._unsubFeed = null;
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
    } else if (action === "note-delete") {
      this._call("todo", "remove_item", {
        entity_id: this._eid("todo", "notizen_erinnerungen"),
        item: [el.dataset.uid],
      });
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
    // Chat and the music controls are mutually exclusive on screen - CODIE
    // is either chatting or its music screen is open, never showing both
    // as far as this card's layout goes - so music mode wins outright
    // regardless of the user's own chat show/hide preference.
    const musicOpen = this._musicOpen === true;
    section.hidden = musicOpen;
    toggle.disabled = musicOpen;
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

  _setLight(extra) {
    const lightId = this._eid("light", "umgebungsfarbe");
    const st = this._state(lightId);
    const data = { entity_id: lightId, ...extra };
    if (!extra.rgb_color && st && st.attributes.rgb_color) data.rgb_color = st.attributes.rgb_color;
    if (!extra.brightness && st && st.attributes.brightness) data.brightness = st.attributes.brightness;
    if (!extra.effect) {
      const effectSel = this.querySelector('[data-el="effect"]');
      if (effectSel && effectSel.value) data.effect = effectSel.value;
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
    this._call("hase", "send_chat_message", {
      config_entry_id: this._config.config_entry_id,
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
    if (document.activeElement !== effectSel && light && light.attributes.effect) {
      effectSel.value = light.attributes.effect;
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
    this.querySelector('[data-el="musicsection"]').hidden = !musicOpen;
    this._musicOpen = musicOpen;
    this._applyChatVisibility();

    this._fillSelect("scene", this._eid("select", "lichtszene"));
    this._updateCharacterOverview(characters);

    this._updateHistory();
    this._updateAlarms();
    if (!this._notesLoaded) {
      this._notesLoaded = true;
      this._updateNotes();
    }
    if (!this._unsubFeed) this._subscribeFeed();
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
  _portraitUrlForSlot(slotNumber) {
    if (!this._hass || !this._hass.entities || this._deviceId == null) return null;
    for (const [entityId, entry] of Object.entries(this._hass.entities)) {
      if (!entityId.startsWith("image.") || entry.device_id !== this._deviceId) continue;
      const st = this._state(entityId);
      if (st && st.attributes && Number(st.attributes.slot_number) === Number(slotNumber)) {
        return st.attributes.entity_picture || null;
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

  // -- alarms ------------------------------------------------------------------

  _updateAlarms() {
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

  // -- open notes (via direct todo/item/list WS call) -------------------------

  async _updateNotes() {
    if (!this._hass || !this._hass.connection) return;
    let items;
    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: "todo/item/list",
        entity_id: this._eid("todo", "notizen_erinnerungen"),
      });
      items = result.items || [];
    } catch (err) {
      return;
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

  // -- live activity feed (Assist-Bridge logbook entries) ----------------------

  _subscribeFeed() {
    if (this._config.show_feed === false) return;
    if (!this._hass || !this._hass.connection) return;
    // domain/name alone don't identify *which* CODE27 device logged the
    // entry - bridge.py uses the same "CODE27 Assist-Bridge" logbook name
    // for every device, so two cards for two different devices used to
    // both accept every entry (see the entity_id-less check this used to
    // be). bridge.py attributes each entry to that device's own
    // last-user-message sensor (_bridge_entity_id()), so filtering on
    // that entity_id scopes the feed back to just this card's device.
    let connected = true;
    this._unsubFeed = () => {
      connected = false;
    };
    const ownEntityId = this._eid("sensor", "zuletzt_gesagtes_du");
    this._hass.connection
      .subscribeEvents((event) => {
        const data = event.data || {};
        if (data.domain !== "hase" || data.name !== "CODE27 Assist-Bridge") return;
        if (data.entity_id !== ownEntityId) return;
        this._feedLines.push({ t: new Date(), msg: data.message });
        if (this._feedLines.length > 30) this._feedLines.shift();
        this._renderFeed();
      }, "logbook_entry")
      .then((unsub) => {
        // The subscription can resolve after disconnectedCallback already
        // ran (e.g. the card was removed from the DOM while this promise
        // was still in flight) - unsubscribe immediately instead of
        // leaving a live subscription this card can no longer track or
        // stop, rather than overwriting the no-op set by disconnect.
        if (!connected) {
          unsub();
          return;
        }
        this._unsubFeed = unsub;
      });
    this.querySelector('[data-el="feedsection"]').hidden = false;
  }

  _renderFeed() {
    const el = this.querySelector('[data-el="feed"]');
    if (!el) return;
    el.innerHTML = this._feedLines
      .map(
        (l) =>
          `<div class="c27-feed-line"><span class="t">${l.t.toLocaleTimeString()}</span>${esc(l.msg)}</div>`
      )
      .join("");
    el.scrollTop = el.scrollHeight;
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
  { key: "show_feed", label: "Show activity feed (Assist bridge log)" },
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
          <input data-el="portrait" type="text" placeholder="/local/hase/portrait.png" style="${fieldStyle}">
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

customElements.define("code27-card", Code27Card);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "code27-card",
  name: "CODE27",
  description: "Steuerung, Chat und Status für ein CODE27-Gerät.",
});
