/*
 * HASEAMP card behaviour tests.
 *
 * Same technique as the existing Studio suites: `node:test` plus a hand-written
 * minimal DOM, no jsdom and no browser. The card is captured through
 * `customElements.define`, driven with a scripted Home Assistant connection,
 * and asserted on the HTML it renders and the WebSocket calls it makes.
 *
 * What this suite deliberately proves:
 *   - the card only ever talks to the selected frontend provider's namespace,
 *   - it never drives playback on its own (no browser-side autoadvance and no
 *     polling of its own beyond the subscription),
 *   - optional controls follow the backend's feature flags, and a device
 *     without them still yields a usable card.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const SOURCE_URL = new URL("../custom_components/hase/cards/haseamp-card.js", import.meta.url);

// -- minimal DOM ------------------------------------------------------------

function makeClassList() {
  const set = new Set();
  return {
    add: (name) => set.add(name),
    remove: (name) => set.delete(name),
    contains: (name) => set.has(name),
    toggle: (name, on) => {
      if (on === undefined) {
        if (set.has(name)) set.delete(name);
        else set.add(name);
      } else if (on) {
        set.add(name);
      } else {
        set.delete(name);
      }
      return set.has(name);
    },
    _set: set,
  };
}

class FakeElement {
  constructor(tagName = "div") {
    this.tagName = tagName;
    this._html = "";
    this._els = new Map();
    this.style = {};
    this.dataset = {};
    this.hidden = false;
    this.textContent = "";
    this.title = "";
    this.value = "";
    this.disabled = false;
    this.classList = makeClassList();
    this.listeners = new Map();
    this.events = [];
  }

  set innerHTML(value) {
    this._html = String(value);
    this._syncElements();
  }

  get innerHTML() {
    return this._html;
  }

  _syncElements() {
    // Setting innerHTML replaces the children, exactly like a real browser;
    // reusing the old nodes would silently accumulate event listeners across
    // renders.
    this._els = new Map();
    for (const match of this._html.matchAll(/<[^>]*data-el="([^"]+)"[^>]*>/g)) {
      const element = new FakeElement();
      // Carry the tag's own data-* attributes over, so a test can assert on an
      // action that was declared in the markup (e.g. the progress bar).
      for (const attribute of match[0].matchAll(/data-([a-z_-]+)="([^"]*)"/g)) {
        if (attribute[1] === "el") continue;
        element.dataset[attribute[1]] = attribute[2];
      }
      this._els.set(match[1], element);
    }
  }

  querySelector(selector) {
    const byEl = /^\[data-el="([^"]+)"\]$/.exec(selector);
    if (byEl) {
      if (!this._els.has(byEl[1])) this._els.set(byEl[1], new FakeElement());
      return this._els.get(byEl[1]);
    }
    if (selector === "ha-card") return new FakeElement("ha-card");
    return null;
  }

  querySelectorAll() {
    return [];
  }

  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }

  removeEventListener() {}

  contains() {
    return true;
  }

  dispatchEvent(event) {
    this.events.push(event);
    return true;
  }

  async dispatch(type, event) {
    for (const handler of this.listeners.get(type) || []) await handler(event);
  }

  fire(type, event) {
    for (const handler of this.listeners.get(type) || []) handler(event);
  }
}

const timers = { intervals: [], cleared: 0 };
globalThis.HTMLElement = FakeElement;
globalThis.document = { createElement: (tag) => new FakeElement(tag) };
globalThis.CustomEvent = class CustomEvent {
  constructor(type, init) {
    this.type = type;
    this.detail = init && init.detail;
  }
};
globalThis.setInterval = (fn, ms) => {
  const handle = { fn, ms };
  timers.intervals.push(handle);
  return handle;
};
globalThis.clearInterval = (handle) => {
  timers.cleared += 1;
  timers.intervals = timers.intervals.filter((entry) => entry !== handle);
};

const registered = [];
globalThis.customElements = {
  get: () => undefined,
  define: (name, ctor) => registered.push({ name, ctor }),
};

let modulePromise;
async function loadCard() {
  if (!modulePromise) {
    globalThis.window = globalThis;
    globalThis.window.customCards = [];
    modulePromise = import(SOURCE_URL.href).then(() => ({
      card: registered.find((entry) => entry.name === "haseamp-card").ctor,
      editor: registered.find((entry) => entry.name === "haseamp-card-editor").ctor,
      customCards: globalThis.window.customCards,
    }));
  }
  return modulePromise;
}

// -- fixtures ---------------------------------------------------------------

function playbackFixture(overrides = {}) {
  return Object.assign(
    {
      state: "playing",
      available: true,
      track: {
        id: "code27:local:/storage/x/a.mp3",
        uri: "local:/storage/x/a.mp3",
        title: "Stand By Me",
        artist: null,
        duration: 200,
        provider: "code27",
      },
      title: "Stand By Me",
      artist: null,
      position: 40,
      duration: 200,
      volume: 0.4,
      source: "LOCAL_TF",
      is_playing: true,
      device_play_mode: null,
      storage_loaded: true,
      track_end: null,
    },
    overrides
  );
}

function queueFixture(overrides = {}) {
  return Object.assign(
    {
      items: [
        {
          item_id: 1,
          index: 0,
          current: true,
          track: {
            id: "code27:local:/storage/x/a.mp3",
            uri: "local:/storage/x/a.mp3",
            title: "Stand By Me",
            artist: null,
            duration: 200,
            provider: "code27",
          },
        },
        {
          item_id: 2,
          index: 1,
          current: false,
          track: {
            id: "code27:local:/storage/x/b.mp3",
            uri: "local:/storage/x/b.mp3",
            title: "Second Song",
            artist: null,
            duration: 100,
            provider: "code27",
          },
        },
      ],
      count: 2,
      index: 0,
      current_item_id: 1,
      current_track: {
        uri: "local:/storage/x/a.mp3",
        title: "Stand By Me",
        duration: 200,
        provider: "code27",
      },
      playing: true,
      shuffle: false,
      repeat: "off",
      loaded_playlist: null,
    },
    overrides
  );
}

function snapshotFixture(overrides = {}) {
  return Object.assign(
    {
      provider: "code27",
      features: {
        provider: "code27",
        play: true,
        pause: true,
        stop: false,
        next_track: true,
        previous_track: true,
        volume: true,
        source: true,
        seek: true,
        browser: true,
        enqueue_folder: true,
        device_play_modes: false,
        notes: [],
      },
      operations: [],
      transport_actions: ["play", "pause", "stop", "next", "previous"],
      repeat_modes: ["off", "all", "one"],
      track_end: {
        mechanism: "code27_music_playback_status_transition",
        signal: "music_playback.status: PLAYING -> IDLE",
        explicit_provider_event: false,
        requires_browser: false,
        gap: "CODE27 publishes no 'track finished' event.",
      },
      targets: [{ id: "entry-1", name: "Kitchen CODIE", provider: "code27" }],
      target: { id: "entry-1", name: "Kitchen CODIE" },
      available: true,
      reason: null,
      playback: playbackFixture(),
      queue: queueFixture(),
      playlists: [
        { playlist_id: "rock", name: "Rock", count: 2, created_at: null, updated_at: null },
      ],
      library: {
        revision: "7|3|/storage/x",
        ready: true,
        root_path: "/storage/x",
        file_count: 3,
        is_loaded: true,
      },
      tree: {
        node: null,
        folder: null,
        entries: [
          { id: "folder:Rockkapella", title: "Rockkapella", kind: "folder", can_play: false, can_expand: true, track_count: 1, track: null },
          {
            id: "local:/storage/x/a.mp3",
            title: "Top Song",
            kind: "track",
            can_play: true,
            can_expand: false,
            track_count: 0,
            track: {
              id: "code27:local:/storage/x/a.mp3",
              uri: "local:/storage/x/a.mp3",
              title: "Top Song",
              artist: null,
              duration: 61,
              provider: "code27",
            },
          },
        ],
        library_ready: true,
        root_path: "/storage/x",
      },
      diagnostics: { last_track_end: { detected: true, reason: "played_to_end" } },
    },
    overrides
  );
}

const ownerRegistry = {
  devices: { "device-1": { identifiers: [["hase", "machine-1"]], config_entries: ["entry-1"] } },
  entities: { "sensor.codie_active_character": {
    device_id: "device-1", config_entry_id: "entry-1", translation_key: "active_character",
  } },
};

function makeCard(Card, config = {}, snapshot = snapshotFixture()) {
  const card = new Card();
  card.setConfig(Object.assign({ target: "entry-1" }, config));
  card._hass = { ...ownerRegistry, language: "de", callWS: async () => snapshot };
  // `_build` registers the delegated listeners and renders the static shell;
  // `_built` is what normally makes `_render` run (the `hass` setter sets it
  // after `_build`). The display clock is stubbed below, so nothing keeps the
  // process alive.
  card._built = true;
  card._build();
  if (snapshot) card._apply(snapshot);
  return card;
}

function scriptedHass(Card, { snapshot = snapshotFixture(), onCall, language = "de" } = {}) {
  const calls = [];
  const hass = {
    language,
    ...ownerRegistry,
    callWS: async (message) => {
      calls.push(message);
      if (onCall) return onCall(message);
      return snapshot;
    },
    connection: {},
  };
  return { hass, calls };
}

function clickTarget(dataset, value, tagName, extra = {}) {
  return {
    preventDefault() {},
    // Only a click on the progress bar needs coordinates; every other action
    // ignores them.
    clientX: extra.clientX,
    target: {
      closest: () =>
        Object.assign(
          {
            dataset: Object.assign({}, dataset),
            value: value === undefined ? "" : value,
            tagName: tagName || "BUTTON",
          },
          extra.element || {}
        ),
    },
  };
}

// -- tests ------------------------------------------------------------------

test("registers the card and its editor as Lovelace custom elements", async () => {
  const { card, editor, customCards } = await loadCard();
  assert.equal(typeof card, "function");
  assert.equal(typeof editor, "function");
  const entry = customCards.find((item) => item.type === "haseamp-card");
  assert.ok(entry, "card must be announced to the Lovelace card picker");
  assert.equal(entry.name, "HASEAMP");
  assert.equal(typeof card.prototype.setConfig, "function");
  assert.equal(typeof card.prototype.getCardSize, "function");
  assert.equal(typeof card.getStubConfig, "function");
  assert.ok(card.getConfigElement() instanceof FakeElement);
});

test("setConfig applies defaults and honours the show flags", async () => {
  const { card: Card } = await loadCard();
  const plain = new Card();
  plain.setConfig({ target: "entry-1" });
  assert.deepEqual(plain._show, {
    browser: true,
    queue: true,
    playlists: true,
    diagnostics: true,
    animation: true,
    import: true,
    follow_light: true,
  });
  const minimal = new Card();
  minimal.setConfig({
    target: "entry-1",
    name: "Küche",
    show_browser: false,
    show_queue: false,
    show_playlists: false,
    show_animation: false,
  });
  assert.equal(minimal._show.browser, false);
  assert.equal(minimal._show.queue, false);
  assert.equal(minimal._show.playlists, false);
  assert.equal(minimal._show.animation, false);
  assert.equal(minimal._name, "Küche");
});

test("the card talks to the configured frontend provider", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card, { provider: "code27" });
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  await card._refresh();
  assert.equal(calls[0].type, "hase/code27/music");
});

test("standalone CODE27 target routes music commands and pushes to code27", async () => {
  const { card: Card } = await loadCard();
  const card = new Card();
  card.setConfig({ target: "entry-1" });
  const calls = [];
  const subscriptions = [];
  card._hass = {
    ...ownerRegistry,
    devices: { "device-1": { identifiers: [["code27", "machine-1"]], config_entries: ["entry-1"] } },
    callWS: async (message) => { calls.push(message); return snapshotFixture(); },
    connection: { subscribeMessage: async (_, message) => {
      subscriptions.push(message);
      return () => {};
    } },
  };
  await card._call("snapshot", {});
  await card._ensureSubscription();
  assert.equal(calls[0].type, "code27/music");
  assert.deepEqual(subscriptions[0], { type: "code27/music/subscribe", target: "entry-1" });
  card.disconnectedCallback();
});

test("an unknown provider falls back to the registered transport", async () => {
  const { card: Card } = await loadCard();
  // A card configured for a provider that registered no transport still works
  // against the one that exists - the boundary, not the UI, decides where the
  // request goes.
  const card = makeCard(Card, { provider: "not-registered" });
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  await card._refresh();
  assert.equal(calls[0].type, "hase/code27/music");
});

test("the provider boundary is the only place a device is named", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  assert.equal(card._transport().deviceLabel, "CODE27");
  assert.equal(card._transport().noDeviceReason, "no_code27_device");
  // The UI resolves the device name through the boundary (`_tDevice`) instead
  // of spelling it out.
  assert.match(card._tDevice("no_device"), /Kein CODE27-Gerät geladen/);
  assert.match(card._t("stop_emulated"), /kein Geräte-STOP/);
});

test("renders the LCD readout from the snapshot", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  assert.equal(card.querySelector('[data-el="lcd-title"]').textContent, "Stand By Me");
  assert.equal(card.querySelector('[data-el="target"]').textContent, "Kitchen CODIE");
  assert.ok(card.querySelector('[data-el="led"]').classList.contains("is-on"));
  assert.ok(card.querySelector('[data-el="vu"]').classList.contains("is-playing"));
  // 40 s of a 200 s track.
  assert.equal(card.querySelector('[data-el="progress"]').style.width, "20.00%");
});

test("falls back to the queue row when the device reports no track title", async () => {
  const { card: Card } = await loadCard();
  const snapshot = snapshotFixture({
    playback: playbackFixture({ track: null, title: null, state: "idle" }),
  });
  const card = makeCard(Card, {}, snapshot);
  assert.equal(card.querySelector('[data-el="lcd-title"]').textContent, "Stand By Me");
});

test("shows an explicit empty state without a device", async () => {
  const { card: Card } = await loadCard();
  const snapshot = snapshotFixture({
    available: false,
    reason: "no_code27_device",
    target: null,
    targets: [],
    queue: { items: [], count: 0, index: null, current_item_id: null, current_track: null, playing: false, shuffle: false, repeat: "off", loaded_playlist: null },
    playlists: [],
    tree: null,
    playback: playbackFixture({ state: "unavailable", available: false, track: null, title: null }),
  });
  const card = makeCard(Card, {}, snapshot);
  assert.match(card.querySelector('[data-el="lcd-sub"]').textContent, /Kein CODE27-Gerät/);
  assert.ok(!card.querySelector('[data-el="led"]').classList.contains("is-on"));
  // The transport buttons are still rendered (disabled), so the card never
  // turns into an unusable shell.
  assert.match(card.querySelector('[data-el="controls"]').innerHTML, /data-action="play"/);
  assert.match(card.querySelector('[data-el="controls"]').innerHTML, /disabled/);
});

test("warns when the device is unreachable but keeps the queue visible", async () => {
  const { card: Card } = await loadCard();
  const snapshot = snapshotFixture({
    available: false,
    reason: "device_unavailable",
    tree: null,
    playback: playbackFixture({ state: "unavailable", available: false }),
  });
  const card = makeCard(Card, {}, snapshot);
  assert.match(card.querySelector('[data-el="banner"]').innerHTML, /Gerät nicht erreichbar/);
  assert.match(card.querySelector('[data-el="queue"]').innerHTML, /Stand By Me/);
});

test("renders the browser with folders, tracks and their actions", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const browser = card.querySelector('[data-el="browser"]').innerHTML;
  assert.match(browser, /data-act="browse" data-node="folder:Rockkapella"/);
  assert.match(browser, /data-act="enqueue_folder" data-node="folder:Rockkapella"/);
  assert.match(browser, /data-act="queue_add"[^>]*data-play="1"/);
  assert.match(browser, /data-act="queue_add"[^>]*data-play="0"/);
  assert.match(browser, /Rockkapella\//);
});

test("reports an unready library instead of an empty one", async () => {
  const { card: Card } = await loadCard();
  const snapshot = snapshotFixture({
    tree: { node: null, folder: null, entries: [], library_ready: false, root_path: null },
  });
  const card = makeCard(Card, {}, snapshot);
  assert.match(
    card.querySelector('[data-el="browser"]').innerHTML,
    /Warte auf das Musik-Inventar/
  );
});

test("hides the browser when the feature is unavailable", async () => {
  const { card: Card } = await loadCard();
  const snapshot = snapshotFixture({
    features: Object.assign(snapshotFixture().features, { browser: false }),
  });
  const card = makeCard(Card, {}, snapshot);
  assert.match(
    card.querySelector('[data-el="browser"]').innerHTML,
    /Medienbrowser steht für dieses Gerät nicht zur Verfügung/
  );
});

test("marks the queue's current row and never mixes it with the selection", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const queue = card.querySelector('[data-el="queue"]').innerHTML;
  const currentRow = queue.split("<div class=\"ha-row").find((row) => row.includes("is-current"));
  assert.match(currentRow, /Stand By Me/);
  assert.match(queue, /Second Song/);
  assert.ok(!card.querySelector('[data-el="queue"]').innerHTML.includes("is-selected"));

  card._selected = { uri: "local:/storage/x/b.mp3", title: "Second Song" };
  card._render();
  const rendered = card.querySelector('[data-el="queue"]').innerHTML;
  assert.match(rendered, /is-selected/);
  assert.match(rendered, /is-current/);
  assert.match(card.querySelector('[data-el="lcd-sub"]').textContent, /Ausgewählt: Second Song/);
});

test("disables reorder buttons at the queue edges", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const rows = card.querySelector('[data-el="queue"]').innerHTML.split('<div class="ha-row');
  assert.match(rows[1], /data-to_index="-1"[^>]*disabled/);
  assert.match(rows[2], /data-to_index="2"[^>]*disabled/);
});

test("renders an empty queue message", async () => {
  const { card: Card } = await loadCard();
  const snapshot = snapshotFixture({ queue: queueFixture({ items: [], count: 0, index: null, current_item_id: null, current_track: null, playing: false }) });
  const card = makeCard(Card, {}, snapshot);
  assert.match(card.querySelector('[data-el="queue"]').innerHTML, /Die Queue ist leer/);
});

test("gates optional controls on the backend feature flags", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const controls = card.querySelector('[data-el="controls"]').innerHTML;
  const options = card.querySelector('[data-el="options"]').innerHTML;
  // CODE27: volume/source/seek yes, stop only as an emulation.
  assert.match(options, /data-act="volume"/);
  assert.match(options, /data-el="source"/);
  assert.match(controls, /class="ha-btn is-emulated" data-act="transport" data-action="stop"/);
  assert.match(controls, /Stoppt die HASE-Queue/);
  // The device has SEEK, so the bar is a seek control and says so; restarting
  // stays the ↺ button's job.
  const seekBar = card.querySelector('[data-el="seekbar"]');
  assert.equal(seekBar.dataset.act, "transport");
  assert.equal(seekBar.dataset.action, "seek");
  assert.match(seekBar.title, /spult an diese Stelle/);
  assert.match(controls, /data-act="restart"/);
});

test("transport and options live in separate rows", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const controls = card.querySelector('[data-el="controls"]').innerHTML;
  const options = card.querySelector('[data-el="options"]').innerHTML;
  // Transport row: only playback controls.
  assert.match(controls, /data-action="previous"/);
  assert.match(controls, /data-action="next"/);
  assert.ok(!controls.includes('data-act="volume"'));
  assert.ok(!controls.includes('data-act="set_shuffle"'));
  // Options row: volume, source, queue modes - and the new consume switch.
  assert.match(options, /data-act="set_shuffle"/);
  assert.match(options, /data-act="set_repeat"/);
  assert.match(options, /data-act="set_consume_played" data-consume="1"/);
  assert.match(options, /Gespielte entfernen/);
});

test("the consume switch reports and toggles the queue mode", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  // Off by default: the button asks to turn it on.
  await card.dispatch("click", clickTarget({ act: "set_consume_played", consume: "1" }, "", "BUTTON"));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].operation, "set_consume_played");
  assert.deepEqual(calls[0].data, { target: "entry-1", consume: true });

  const on = makeCard(
    Card,
    {},
    snapshotFixture({ queue: queueFixture({ consume_played: true }) })
  );
  const options = on.querySelector('[data-el="options"]').innerHTML;
  assert.match(options, /class="ha-btn is-on" data-act="set_consume_played" data-consume="0"/);
});

test("the progress bar says why it cannot show progress", async () => {
  const { card: Card } = await loadCard();
  const known = makeCard(Card);
  assert.equal(known.querySelector('[data-el="seek-hint"]').hidden, true);
  assert.equal(known.querySelector('[data-el="seekbar"]').classList.contains("is-unknown"), false);

  const unknown = makeCard(
    Card,
    {},
    snapshotFixture({ playback: playbackFixture({ duration: null }) })
  );
  const bar = unknown.querySelector('[data-el="seekbar"]');
  assert.ok(bar.classList.contains("is-unknown"));
  assert.match(bar.title, /keine Titellänge/);
  assert.equal(unknown.querySelector('[data-el="seek-hint"]').hidden, false);
  assert.match(unknown.querySelector('[data-el="seek-hint"]').textContent, /keine Titellänge/);
  assert.equal(unknown.querySelector('[data-el="duration"]').textContent, "--:--");
});

test("clicking the progress bar seeks to the clicked position", async () => {
  const { card: Card } = await loadCard();
  // The default fixture plays a 200 s track, so the bar's full width is 200 s.
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;

  // A click three quarters along a 200 s track is a 150 s seek, sent as the
  // existing `transport` operation in seconds - the device's own unit stays a
  // backend detail.
  await card.dispatch(
    "click",
    clickTarget({ act: "transport", action: "seek" }, "", "DIV", {
      clientX: 150,
      element: { getBoundingClientRect: () => ({ left: 0, width: 200 }) },
    })
  );
  assert.equal(calls.length, 1);
  assert.equal(calls[0].operation, "transport");
  assert.deepEqual(calls[0].data, { target: "entry-1", action: "seek", position: 150 });
});

test("the progress bar never guesses a position", async () => {
  const { card: Card } = await loadCard();

  // No measurable bar (e.g. a synthetic click): nothing is sent rather than a
  // made-up position.
  const unmeasurable = makeCard(Card);
  const unmeasurableHass = scriptedHass(Card, { snapshot: snapshotFixture() });
  unmeasurable._hass = unmeasurableHass.hass;
  await unmeasurable.dispatch(
    "click",
    clickTarget({ act: "transport", action: "seek" }, "", "DIV")
  );
  assert.deepEqual(unmeasurableHass.calls, []);

  // A rect without a usable origin (NaN) is not a position either.
  const degenerate = makeCard(Card);
  const degenerateHass = scriptedHass(Card, { snapshot: snapshotFixture() });
  degenerate._hass = degenerateHass.hass;
  await degenerate.dispatch(
    "click",
    clickTarget({ act: "transport", action: "seek" }, "", "DIV", {
      clientX: 100,
      element: { getBoundingClientRect: () => ({ width: 200 }) },
    })
  );
  assert.deepEqual(degenerateHass.calls, []);

  // A device that reports no track length: there is nothing to aim at, so the
  // bar stays display-only and a click sends nothing (the backend fills the
  // length in from the queue row or the TF inventory where it can).
  const noLength = makeCard(
    Card,
    {},
    snapshotFixture({ playback: playbackFixture({ duration: null }) })
  );
  const noLengthHass = scriptedHass(Card, { snapshot: snapshotFixture() });
  noLength._hass = noLengthHass.hass;
  await noLength.dispatch(
    "click",
    clickTarget({ act: "transport", action: "seek" }, "", "DIV", {
      clientX: 100,
      element: { getBoundingClientRect: () => ({ left: 0, width: 200 }) },
    })
  );
  assert.deepEqual(noLengthHass.calls, []);

  // Clicking outside the bar's own geometry clamps to the track's edges instead
  // of producing a negative or over-length position.
  const edge = makeCard(Card);
  const edgeHass = scriptedHass(Card, { snapshot: snapshotFixture() });
  edge._hass = edgeHass.hass;
  await edge.dispatch(
    "click",
    clickTarget({ act: "transport", action: "seek" }, "", "DIV", {
      clientX: -40,
      element: { getBoundingClientRect: () => ({ left: 0, width: 200 }) },
    })
  );
  assert.equal(edgeHass.calls.length, 1);
  assert.deepEqual(edgeHass.calls[0].data, {
    target: "entry-1",
    action: "seek",
    position: 0,
  });
});

test("a transport button is still dispatched as its own action", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  // The seek branch must not swallow the transport buttons that share the
  // operation.
  await card.dispatch("click", clickTarget({ act: "transport", action: "next" }, "", "BUTTON"));
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].data, { target: "entry-1", action: "next" });
});

test("the ↺ button restarts the current track", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  await card.dispatch("click", clickTarget({ act: "restart" }, "", "BUTTON"));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].operation, "transport");
  assert.deepEqual(calls[0].data, { target: "entry-1", action: "restart" });
});

test("a device without seek keeps the bar's restart behaviour", async () => {
  const { card: Card } = await loadCard();
  const snapshot = snapshotFixture({
    features: Object.assign(snapshotFixture().features, { seek: false }),
  });
  const card = makeCard(Card, {}, snapshot);
  const { hass, calls } = scriptedHass(Card, { snapshot });
  card._hass = hass;
  assert.match(card.querySelector('[data-el="seekbar"]').title, /kennt kein Spulen/);
  await card.dispatch("click", clickTarget({ act: "transport", action: "seek" }, "", "DIV"));
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].data, { target: "entry-1", action: "restart" });
});

test("renders playlists with load, save and delete actions", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const body = card.querySelector('[data-el="playlist-body"]').innerHTML;
  assert.match(body, /data-act="pl-select" data-name="Rock"[^>]*data-dbl="playlist_load"/);
  assert.match(body, /2 Titel/);
  assert.match(body, /data-act="playlist_load"/);
  assert.match(body, /data-act="playlist_save_as"/);
  assert.match(body, /data-act="playlist_create" data-from_queue="1"/);
  assert.match(body, /data-act="playlist_create" data-from_queue="0"/);
  assert.match(body, /data-act="playlist_save"/);
  assert.match(body, /data-act="playlist_rename"/);
  assert.match(body, /data-act="playlist_delete"/);
});

test("a device without volume control still renders a usable player", async () => {
  const { card: Card } = await loadCard();
  const snapshot = snapshotFixture({
    features: Object.assign(snapshotFixture().features, { volume: false, source: false }),
    playback: playbackFixture({ volume: null, source: null, state: "idle" }),
  });
  const card = makeCard(Card, {}, snapshot);
  const controls = card.querySelector('[data-el="controls"]').innerHTML;
  assert.ok(!controls.includes('data-act="volume"'));
  assert.ok(!controls.includes('data-el="source"'));
  assert.match(controls, /data-action="play"/);
  assert.match(controls, /data-action="next"/);
});

test("marks the play button according to the playback state", async () => {
  const { card: Card } = await loadCard();
  const playing = makeCard(Card);
  assert.match(playing.querySelector('[data-el="controls"]').innerHTML, /data-action="pause"/);
  const paused = makeCard(Card, {}, snapshotFixture({ playback: playbackFixture({ state: "paused" }) }));
  assert.match(paused.querySelector('[data-el="controls"]').innerHTML, /data-action="play"/);
});

test("shows shuffle and repeat state as queue modes", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(
    Card,
    {},
    snapshotFixture({ queue: queueFixture({ shuffle: true, repeat: "one" }) })
  );
  const options = card.querySelector('[data-el="options"]').innerHTML;
  assert.match(options, /class="ha-btn is-on" data-act="set_shuffle" data-shuffle="0"/);
  assert.match(options, /data-act="set_repeat" data-repeat="off"/);
  assert.match(options, /RPT eins/);
});

test("renders playlists with load, save and delete actions", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const body = card.querySelector('[data-el="playlist-body"]').innerHTML;
  assert.match(body, /data-act="pl-select" data-name="Rock"[^>]*data-dbl="playlist_load"/);
  assert.match(body, /2 Titel/);
  assert.match(body, /data-act="playlist_load"/);
  assert.match(body, /data-act="playlist_save_as"/);
  assert.match(body, /data-act="playlist_create" data-from_queue="1"/);
  assert.match(body, /data-act="playlist_create" data-from_queue="0"/);
  assert.match(body, /data-act="playlist_save"/);
  assert.match(body, /data-act="playlist_rename"/);
  assert.match(body, /data-act="playlist_delete"/);
});

test("maps every card action onto a backend operation payload", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);

  const add = card._payloadFor("queue_add", {
    uri: "local:/storage/x/a.mp3",
    title: "A",
    provider: "code27",
    duration: "12.5",
    play: "1",
  });
  assert.deepEqual(add, {
    tracks: [
      {
        uri: "local:/storage/x/a.mp3",
        title: "A",
        provider: "code27",
        duration: 12.5,
      },
    ],
    play: true,
  });

  assert.deepEqual(card._payloadFor("enqueue_folder", { node: "folder:Rock", play: "0" }), {
    node: "folder:Rock",
    play: false,
  });
  assert.deepEqual(card._payloadFor("queue_play", { item_id: "2" }), { item_id: 2 });
  assert.deepEqual(card._payloadFor("queue_play", { index: "0" }), { index: 0 });
  assert.deepEqual(card._payloadFor("queue_remove", { index: "1" }), { index: 1 });
  assert.deepEqual(card._payloadFor("queue_clear", {}), {});
  assert.deepEqual(card._payloadFor("queue_move", { from_index: "0", to_index: "3" }), {
    from_index: 0,
    to_index: 3,
  });
  assert.deepEqual(card._payloadFor("transport", { action: "next" }), { action: "next" });
  // The slider is a percentage; the backend contract is 0..1.
  assert.deepEqual(card._payloadFor("volume", { volume: "42" }), { volume: 0.42 });
  assert.deepEqual(card._payloadFor("set_shuffle", { shuffle: "1" }), { shuffle: true });
  assert.deepEqual(card._payloadFor("set_repeat", { repeat: "all" }), { repeat: "all" });
  // A payload mapping is mandatory for every dispatchable action, so an
  // accidental new action fails loudly instead of sending a guess.
  assert.throws(() => card._payloadFor("not_an_action", {}), /no payload mapping/);
});

test("maps playlist actions onto the names the user sees", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  card._playlistName = "Road Trip";
  card._playlistPick = "Rock";
  assert.deepEqual(card._payloadFor("playlist_create", { from_queue: "1" }), {
    name: "Road Trip",
    from_queue: true,
  });
  assert.deepEqual(card._payloadFor("playlist_save", {}), { name: "Rock" });
  assert.deepEqual(card._payloadFor("playlist_save_as", {}), { name: "Road Trip" });
  assert.deepEqual(card._payloadFor("playlist_load", {}), { name: "Rock" });
  assert.deepEqual(card._payloadFor("playlist_delete", {}), { name: "Rock" });
  assert.deepEqual(card._payloadFor("playlist_rename", {}), {
    name: "Rock",
    new_name: "Road Trip",
  });
});

test("clicking a transport control sends the provider-scoped operation", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;

  await card.dispatch("click", clickTarget({ act: "transport", action: "next" }));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].type, "hase/code27/music");
  assert.equal(calls[0].operation, "transport");
  assert.equal(calls[0].data.target, "entry-1");
  assert.equal(calls[0].data.action, "next");
});

test("queue actions carry the row identity, not a DOM position", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;

  await card.dispatch("click", clickTarget({ act: "queue_play", item_id: "2" }));
  await card.dispatch("click", clickTarget({ act: "queue_remove", item_id: "1" }));
  await card.dispatch("click", clickTarget({ act: "queue_move", from_index: "1", to_index: "0" }));
  await card.dispatch("click", clickTarget({ act: "queue_clear" }));

  assert.deepEqual(
    calls.map((call) => [call.operation, call.data]),
    [
      ["queue_play", { target: "entry-1", item_id: 2 }],
      ["queue_remove", { target: "entry-1", item_id: 1 }],
      ["queue_move", { target: "entry-1", from_index: 1, to_index: 0 }],
      ["queue_clear", { target: "entry-1" }],
    ]
  );
});

test("row selection stays local and never reaches the backend", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;

  await card.dispatch(
    "click",
    clickTarget({ act: "queue-select", uri: "local:/storage/x/b.mp3", title: "Second Song" })
  );
  await card.dispatch(
    "click",
    clickTarget({ act: "browse-select", uri: "local:/storage/x/a.mp3", title: "Top Song" })
  );
  assert.equal(calls.length, 0);
  assert.equal(card._selected.title, "Top Song");
});

test("adding a track from the browser keeps the device's exact uri", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;

  await card.dispatch(
    "click",
    clickTarget({
      act: "queue_add",
      uri: "local:/storage/142E2DF92E2DD4900/code27/music/Rockkapella/a b.mp3",
      title: "A B",
      provider: "code27",
      duration: "210",
      play: "0",
    })
  );
  assert.deepEqual(calls[0].data.tracks, [
    {
      uri: "local:/storage/142E2DF92E2DD4900/code27/music/Rockkapella/a b.mp3",
      title: "A B",
      provider: "code27",
      duration: 210,
    },
  ]);
  assert.equal(calls[0].data.play, false);
});

test("folder enqueue sends the node the backend can walk", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  await card.dispatch("click", clickTarget({ act: "enqueue_folder", node: "folder:Rockkapella" }));
  assert.deepEqual(calls[0].data, { target: "entry-1", node: "folder:Rockkapella", play: false });
});

test("volume changes are sent on release, not on every drag step", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;

  card._onInput({ target: { closest: () => ({ dataset: { act: "volume" } }) } , value: 55});
  assert.equal(calls.length, 0);
  await card.dispatch("change", clickTarget({ act: "volume" }, "55"));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].operation, "set_volume");
  assert.equal(calls[0].data.volume, 0.55);
});

test("a queue result from the backend updates the view immediately", async () => {
  const { card: Card } = await loadCard();
  const updated = queueFixture({
    items: [
      {
        item_id: 7,
        index: 0,
        current: true,
        track: { uri: "local:/storage/x/z.mp3", title: "Fresh", duration: 10, provider: "code27" },
      },
    ],
    count: 1,
    index: 0,
    current_item_id: 7,
    current_track: { uri: "local:/storage/x/z.mp3", title: "Fresh", duration: 10, provider: "code27" },
  });
  const { hass } = scriptedHass(Card, { onCall: () => updated });
  const card = makeCard(Card);
  card._hass = hass;
  await card.dispatch("click", clickTarget({ act: "queue_play", item_id: "7" }));
  assert.match(card.querySelector('[data-el="queue"]').innerHTML, /Fresh/);
});

test("reports backend errors without breaking the card", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  card._hass = {
    ...ownerRegistry,
    language: "de",
    callWS: async () => {
      throw new Error("Musikmodus ist auf dem Gerät nicht geöffnet");
    },
  };
  await card.dispatch("click", clickTarget({ act: "transport", action: "play" }));
  assert.match(card.querySelector('[data-el="banner"]').innerHTML, /Musikmodus/);
  assert.match(card.querySelector('[data-el="controls"]').innerHTML, /data-action="pause"/);
});

test("progress is interpolated for display only while playing", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const playback = snapshotFixture().playback;
  card._lastPositionAt = Date.now() - 5000;
  const elapsed = card._elapsed(playback);
  assert.ok(elapsed >= 44.9 && elapsed <= 45.1, `expected ~45s, got ${elapsed}`);

  const paused = makeCard(Card, {}, snapshotFixture({ playback: playbackFixture({ state: "paused" }) }));
  paused._lastPositionAt = Date.now() - 5000;
  assert.equal(paused._elapsed(playbackFixture({ state: "paused" })), 40);
});

test("the display clock never talks to the backend", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  // `_tick` is what the interval calls; it must stay a pure repaint.
  card._tick();
  card._tick();
  assert.equal(calls.length, 0);
  assert.equal(card._snapshot.queue.index, 0);
});

test("subscribes to the provider-scoped push channel", async () => {
  const { card: Card } = await loadCard();
  const card = new Card();
  card.setConfig({ target: "entry-1" });
  const subscriptions = [];
  let pushed;
  card._hass = {
    ...ownerRegistry,
    language: "en",
    callWS: async () => snapshotFixture(),
    connection: {
      subscribeMessage: async (callback, message) => {
        subscriptions.push(message);
        pushed = callback;
        return () => subscriptions.push("unsubscribed");
      },
    },
  };
  card._ensureSubscription();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(subscriptions.length, 1);
  assert.deepEqual(subscriptions[0], { type: "hase/code27/music/subscribe", target: "entry-1" });

  pushed({
    type: "snapshot",
    snapshot: snapshotFixture({ queue: queueFixture({ count: 9 }) }),
  });
  assert.equal(card._snapshot.queue.count, 9);

  card.disconnectedCallback();
  assert.equal(subscriptions[subscriptions.length - 1], "unsubscribed");
});

test("falls back to a hass-driven refresh without a push channel", async () => {
  const { card: Card } = await loadCard();
  const card = new Card();
  card.setConfig({ target: "entry-1" });
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card.hass = hass;
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].operation, "snapshot");
  // A second `hass` update within the throttle window must not refetch.
  card.hass = hass;
  assert.equal(calls.length, 1);
});

test("unsubscribes immediately when the card is removed mid-subscribe", async () => {
  const { card: Card } = await loadCard();
  const card = new Card();
  card.setConfig({ target: "entry-1" });
  const events = [];
  card._hass = {
    ...ownerRegistry,
    language: "en",
    callWS: async () => snapshotFixture(),
    connection: {
      subscribeMessage: async () => {
        await new Promise((resolve) => setImmediate(resolve));
        return () => events.push("late-unsubscribe");
      },
    },
  };
  card._ensureSubscription();
  card.disconnectedCallback();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  // Removal during owner resolution prevents a late subscription entirely.
  assert.deepEqual(events, []);
});

test("the display clock is stopped when the card is removed", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  card._startClock();
  assert.ok(card._clock);
  const before = timers.cleared;
  card.disconnectedCallback();
  assert.equal(card._clock, null);
  assert.ok(timers.cleared > before);
});

test("diagnostics state what autoadvance actually relies on", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const foot = card.querySelector('[data-el="foot"]');
  assert.match(foot.textContent, /PLAYING -> IDLE/);
  assert.match(foot.textContent, /last: played_to_end/);
  assert.match(foot.title, /no 'track finished' event/);
});

test("the VU animation is labelled as decorative", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  assert.match(card.querySelector('[data-el="vu"]').title, /keine Audiospektrum-Daten/);
});

// -- music inventory lifecycle (real-device regression) ---------------------

/*
 * The reported defect: the browser populated, then ~1 s later showed
 * "Keine Musik gefunden". Cause: the WebSocket subscription push was built with
 * `include_tree=False` but still carried `"tree": null`, and `_apply` replaced
 * the whole snapshot with it, so `_renderBrowser` took its empty branch. The
 * next unrelated `music_playback` push (roughly one per second while playing)
 * is what made it happen about a second later.
 */
test("a push without a listing never empties the browser", async () => {
  const { card: Card } = await loadCard();
  const card = new Card();
  card.setConfig({ target: "entry-1" });
  card._hass = { language: "de", callWS: async () => snapshotFixture() };
  card._built = true;
  card._build();

  // 1. The initial snapshot carries the listing: the browser is populated.
  card._apply(snapshotFixture());
  const before = card.querySelector('[data-el="browser"]').innerHTML;
  assert.match(before, /Rockkapella/);
  assert.match(before, /Top Song/);

  // 2. An unrelated playback update arrives - exactly what the subscription
  //    push looks like (no `tree` key at all).
  const push = snapshotFixture({ playback: playbackFixture({ position: 41 }) });
  delete push.tree;
  card._apply(push);

  // 3. The browser must still show the same tracks.
  const after = card.querySelector('[data-el="browser"]').innerHTML;
  assert.match(after, /Rockkapella/);
  assert.match(after, /Top Song/);
  assert.ok(!after.includes("Keine Musik gefunden"));
  assert.match(after, /data-act="queue_add"/);
});

test("many playback pushes in a row keep the listing", async () => {
  const { card: Card } = await loadCard();
  const card = new Card();
  card.setConfig({ target: "entry-1" });
  card._hass = { language: "de", callWS: async () => snapshotFixture() };
  card._built = true;
  card._build();
  card._apply(snapshotFixture());
  for (let position = 41; position < 50; position += 1) {
    const push = snapshotFixture({ playback: playbackFixture({ position }) });
    delete push.tree;
    card._apply(push);
  }
  assert.match(card.querySelector('[data-el="browser"]').innerHTML, /Top Song/);
});

test("a changed inventory revision refetches the current node", async () => {
  const { card: Card } = await loadCard();
  const calls = [];
  const refreshedTree = {
    node: null,
    folder: null,
    entries: [
      {
        id: "local:/storage/x/new.mp3",
        title: "Brand New",
        kind: "track",
        can_play: true,
        can_expand: false,
        track_count: 0,
        track: { uri: "local:/storage/x/new.mp3", title: "Brand New", duration: 10, provider: "code27" },
      },
    ],
    library_ready: true,
    root_path: "/storage/x",
  };
  const card = new Card();
  card.setConfig({ target: "entry-1" });
  card._hass = {
    ...ownerRegistry,
    language: "de",
    callWS: async (message) => {
      calls.push(message);
      if (message.operation === "browse") return refreshedTree;
      return snapshotFixture();
    },
  };
  card._built = true;
  card._build();
  card._apply(snapshotFixture());
  assert.match(card.querySelector('[data-el="browser"]').innerHTML, /Top Song/);
  assert.equal(calls.length, 0);

  // The device re-sends its inventory: same shape, new revision, no listing.
  const push = snapshotFixture({ library: { revision: "9|1|/storage/x", ready: true, file_count: 1 } });
  delete push.tree;
  card._apply(push);
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(calls.length, 1);
  assert.equal(calls[0].operation, "browse");
  assert.equal(calls[0].data.node, null);
  assert.match(card.querySelector('[data-el="browser"]').innerHTML, /Brand New/);
});

test("an unchanged inventory revision never refetches", async () => {
  const { card: Card } = await loadCard();
  const calls = [];
  const card = new Card();
  card.setConfig({ target: "entry-1" });
  card._hass = {
    ...ownerRegistry,
    language: "de",
    callWS: async (message) => {
      calls.push(message);
      return snapshotFixture();
    },
  };
  card._built = true;
  card._build();
  card._apply(snapshotFixture());
  for (let position = 0; position < 5; position += 1) {
    const push = snapshotFixture({ playback: playbackFixture({ position }) });
    delete push.tree;
    card._apply(push);
  }
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, []);
});

test("the current folder is refetched too, not just the root", async () => {
  const { card: Card } = await loadCard();
  const calls = [];
  const folderTree = {
    node: "folder:Rockkapella",
    folder: "Rockkapella",
    entries: [
      {
        id: "local:/storage/x/a.mp3",
        title: "Inside Song",
        kind: "track",
        can_play: true,
        can_expand: false,
        track_count: 0,
        track: { uri: "local:/storage/x/a.mp3", title: "Inside Song", duration: 5, provider: "code27" },
      },
    ],
    library_ready: true,
    root_path: "/storage/x",
  };
  const card = new Card();
  card.setConfig({ target: "entry-1" });
  card._hass = {
    ...ownerRegistry,
    language: "de",
    callWS: async (message) => {
      calls.push(message);
      if (message.operation === "browse") return folderTree;
      return snapshotFixture();
    },
  };
  card._built = true;
  card._build();
  card._apply(snapshotFixture());
  await card.dispatch("click", clickTarget({ act: "browse", node: "folder:Rockkapella" }));
  assert.match(card.querySelector('[data-el="browser"]').innerHTML, /Inside Song/);
  assert.match(card.querySelector('[data-el="crumbs"]').innerHTML, /Rockkapella/);

  calls.length = 0;
  const push = snapshotFixture({ library: { revision: "11|4|/storage/x", ready: true, file_count: 4 } });
  delete push.tree;
  card._apply(push);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].data.node, "folder:Rockkapella");
});

test("an inventory that has not arrived says so instead of looking empty", async () => {
  const { card: Card } = await loadCard();
  const snapshot = snapshotFixture({
    library: { revision: null, ready: false, file_count: 0, is_loaded: false },
  });
  delete snapshot.tree;
  const card = makeCard(Card, {}, snapshot);
  const browser = card.querySelector('[data-el="browser"]').innerHTML;
  assert.match(browser, /Warte auf das Musik-Inventar/);
  assert.ok(!browser.includes("Keine Musik gefunden"));
});

test("an empty folder still reports that it is empty", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(
    Card,
    {},
    snapshotFixture({
      tree: { node: null, folder: null, entries: [], library_ready: true, root_path: "/storage/x" },
    })
  );
  assert.match(card.querySelector('[data-el="browser"]').innerHTML, /Keine Musik gefunden/);
});

test("a tree-less push does not fake an empty folder", async () => {
  const { card: Card } = await loadCard();
  const card = new Card();
  card.setConfig({ target: "entry-1" });
  card._hass = { language: "de", callWS: async () => snapshotFixture() };
  card._built = true;
  card._build();
  // No listing has ever arrived, and the inventory is ready: the card must not
  // claim the folder is empty.
  const snapshot = snapshotFixture();
  delete snapshot.tree;
  card._apply(snapshot);
  const browser = card.querySelector('[data-el="browser"]').innerHTML;
  assert.match(browser, /Lade Ordner/);
  assert.ok(!browser.includes("Keine Musik gefunden"));
});

test("the browser source declares no listing cache of its own", async () => {
  const source = await readFile(SOURCE_URL, "utf8");
  assert.ok(!source.includes("localStorage"));
  assert.ok(source.includes('hasOwnProperty.call(snapshot, "tree")'));
});

test("a literal null listing is never rendered as an empty library", async () => {
  // This is the exact pre-fix payload (`include_tree=False` still emitted
  // `"tree": null`). It used to render "Keine Musik gefunden"; now it may only
  // ever show a loading/waiting state, and the card heals itself by refetching.
  const { card: Card } = await loadCard();
  const calls = [];
  const card = new Card();
  card.setConfig({ target: "entry-1" });
  card._hass = {
    ...ownerRegistry,
    language: "de",
    callWS: async (message) => {
      calls.push(message);
      return message.operation === "browse" ? snapshotFixture().tree : snapshotFixture();
    },
  };
  card._built = true;
  card._build();
  card._apply(snapshotFixture());

  card._apply(snapshotFixture({ tree: null }));
  const browser = card.querySelector('[data-el="browser"]').innerHTML;
  assert.ok(!browser.includes("Keine Musik gefunden"));

  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].operation, "browse");
  assert.match(card.querySelector('[data-el="browser"]').innerHTML, /Top Song/);
});

// -- end music inventory lifecycle -----------------------------------------

// -- action dispatch audit -------------------------------------------------

/*
 * Reported defect: merely opening/changing the playlist dropdown sent
 * `operation: "playlist-pick"`, which the `hase/code27/music` schema rejects
 * because it only accepts the real operations. `_onClick` forwarded any
 * `data-act` it found, and a `<select>` fires a click as well as a change.
 */
test("selecting a playlist sends nothing, and Load sends exactly one playlist_load", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  assert.match(card.querySelector('[data-el="playlist-body"]').innerHTML, /data-act="pl-select"/);

  // 1+2. The user clicks a playlist row (and, redundantly, changes a select).
  await card.dispatch("click", clickTarget({ act: "pl-select", name: "Rock" }, "", "DIV"));
  await card.dispatch("change", clickTarget({ act: "playlist-pick" }, "Rock", "SELECT"));

  // 3. Selecting only reads the playlist's tracks (for the editor); nothing
  // is changed on the backend.
  assert.deepEqual(calls.map((call) => call.operation), ["playlist_get"]);
  assert.equal(card._playlistPick, "Rock");
  calls.length = 0;

  // 4. Load.
  await card.dispatch("click", clickTarget({ act: "playlist_load" }, "", "BUTTON"));

  // 5. Exactly one playlist_load, carrying the selected reference.
  assert.equal(calls.length, 1);
  assert.equal(calls[0].type, "hase/code27/music");
  assert.equal(calls[0].operation, "playlist_load");
  assert.deepEqual(calls[0].data, { target: "entry-1", name: "Rock" });
});

test("no UI-only control ever produces a backend operation", async () => {
  const { card: Card } = await loadCard();
  // Genuinely UI-only: neither click nor change may reach the backend.
  const scenarios = [
    { act: "playlist-pick", tag: "SELECT", value: "Rock" },
    { act: "playlist-name", tag: "INPUT", value: "Road Trip" },
    { act: "browse-select", tag: "SPAN", value: "" },
    { act: "queue-select", tag: "SPAN", value: "" },
    { act: "seekbar", tag: "DIV", value: "" },
    { act: "seek", tag: "DIV", value: "" },
    { act: "source", tag: "SELECT", value: "LOCAL_TF" },
  ];
  for (const scenario of scenarios) {
    const card = makeCard(Card);
    const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
    card._hass = hass;
    await card.dispatch("click", clickTarget({ act: scenario.act }, scenario.value, scenario.tag));
    await card.dispatch("change", clickTarget({ act: scenario.act }, scenario.value, scenario.tag));
    card._onInput({
      target: { closest: () => ({ dataset: { act: scenario.act }, value: scenario.value }) },
    });
    assert.deepEqual(calls, [], `${scenario.act} reached the backend`);
  }
});

test("the volume slider only sends on change, never on click", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;

  // Clicking (or dragging) the slider is not an action.
  await card.dispatch("click", clickTarget({ act: "volume" }, "55", "INPUT"));
  card._onInput({ target: { closest: () => ({ dataset: { act: "volume" }, value: "55" }) } });
  assert.deepEqual(calls, []);

  // Releasing it is.
  await card.dispatch("change", clickTarget({ act: "volume" }, "55", "INPUT"));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].operation, "set_volume");
  assert.deepEqual(calls[0].data, { target: "entry-1", volume: 0.55 });
});

test("the dispatcher refuses anything that is not a backend operation", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  for (const act of ["playlist-pick", "playlist-name", "volume-unknown", "undefined-action", ""]) {
    await card._act(act, {});
  }
  assert.deepEqual(calls, []);
});

test("each playlist button sends its own operation", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  // A name that does not exist yet, so no overwrite question interferes - the
  // confirmation flow has its own test below.
  card._playlistPick = "New Set";
  card._playlistName = "Road Trip";

  const buttons = [
    ["playlist_create", { from_queue: "0" }, { name: "Road Trip", from_queue: false }],
    ["playlist_create", { from_queue: "1" }, { name: "Road Trip", from_queue: true }],
    ["playlist_save", {}, { name: "New Set" }],
    ["playlist_save_as", {}, { name: "Road Trip" }],
    ["playlist_rename", {}, { name: "New Set", new_name: "Road Trip" }],
    ["playlist_delete", {}, { name: "New Set" }],
    ["playlist_load", {}, { name: "New Set" }],
  ];
  for (const [act, extra, expected] of buttons) {
    calls.length = 0;
    await card.dispatch("click", clickTarget(Object.assign({ act }, extra), "", "BUTTON"));
    assert.equal(calls.length, 1, act);
    assert.equal(calls[0].operation, act);
    assert.deepEqual(calls[0].data, Object.assign({ target: "entry-1" }, expected));
  }
});

// -- playlist overwrite confirmation ---------------------------------------

test("saving onto an existing playlist asks before overwriting", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  card._playlistPick = "Rock"; // exists in the fixture
  card._playlistName = "";

  // 1. "Speichern" onto another, already existing playlist asks first.
  await card.dispatch("click", clickTarget({ act: "playlist_save" }, "", "BUTTON"));
  assert.deepEqual(calls, []);
  const question = card.querySelector('[data-el="playlist-body"]').innerHTML;
  assert.match(question, /Rock/);
  assert.match(question, /existiert bereits. Überschreiben\?/);
  assert.match(question, /data-act="confirm-yes"/);
  assert.match(question, /data-act="confirm-no"/);

  // 2. Declining sends nothing and clears the question.
  await card.dispatch("click", clickTarget({ act: "confirm-no" }, "", "BUTTON"));
  assert.deepEqual(calls, []);
  assert.ok(!card.querySelector('[data-el="playlist-body"]').innerHTML.includes("Überschreiben?"));

  // 3. Confirming overwrites through playlist_save (never save_as, which is
  //    contractually the "create a new one" operation).
  await card.dispatch("click", clickTarget({ act: "playlist_save" }, "", "BUTTON"));
  await card.dispatch("click", clickTarget({ act: "confirm-yes" }, "", "BUTTON"));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].operation, "playlist_save");
  assert.deepEqual(calls[0].data, { target: "entry-1", name: "Rock" });
});

test("saving as an existing name offers to overwrite instead of failing", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  card._playlistName = "Rock";
  await card.dispatch("click", clickTarget({ act: "playlist_save_as" }, "", "BUTTON"));
  assert.deepEqual(calls, []);
  assert.match(card.querySelector('[data-el="playlist-body"]').innerHTML, /Überschreiben\?/);

  await card.dispatch("click", clickTarget({ act: "confirm-yes" }, "", "BUTTON"));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].operation, "playlist_save");
  assert.deepEqual(calls[0].data, { target: "entry-1", name: "Rock" });
});

test("saving the loaded playlist does not nag", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(
    Card,
    {},
    snapshotFixture({ queue: queueFixture({ loaded_playlist: "Rock" }) })
  );
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  card._playlistPick = "Rock";
  await card.dispatch("click", clickTarget({ act: "playlist_save" }, "", "BUTTON"));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].operation, "playlist_save");
});

test("a brand new name saves without any question", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  card._playlistName = "Brand New";
  await card.dispatch("click", clickTarget({ act: "playlist_save_as" }, "", "BUTTON"));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].operation, "playlist_save_as");
  assert.deepEqual(calls[0].data, { target: "entry-1", name: "Brand New" });
});

// -- typing protection -----------------------------------------------------

test("a push while typing never rebuilds the playlist name field", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;

  const body = card.querySelector('[data-el="playlist-body"]');
  const input = { dataset: { act: "playlist-name" }, value: "Road" };
  // The user has focus in the name field.
  globalThis.document.activeElement = input;
  try {
    // Three keystrokes, each of which sets card state.
    for (const value of ["R", "Ro", "Roa"]) {
      input.value = value;
      card._onInput({ target: { closest: () => input } });
    }
    // ... and meanwhile a playback push arrives.
    const push = snapshotFixture({ playback: playbackFixture({ position: 45 }) });
    delete push.tree;
    card._apply(push);

    // The section was left untouched, so the input keeps focus and its content.
    assert.notEqual(body.innerHTML, "");
    assert.equal(card._playlistName, "Roa");
    assert.deepEqual(calls, []);
  } finally {
    globalThis.document.activeElement = null;
  }
});

test("unchanged sections are not repainted on every push", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const sections = {
    queue: card.querySelector('[data-el="queue"]'),
    browser: card.querySelector('[data-el="browser"]'),
    playlists: card.querySelector('[data-el="playlist-body"]'),
    controls: card.querySelector('[data-el="controls"]'),
  };
  // A repaint destroys and recreates the section's children - which is what
  // made typing impossible and could reset a dragged slider. Watch the paint
  // helper directly instead of guessing from the markup.
  const repainted = new Set();
  const original = card._paint.bind(card);
  card._paint = (element, signature, html) => {
    const changed = !(element.dataset && element.dataset.sig === signature);
    if (changed) {
      for (const [name, node] of Object.entries(sections)) {
        if (node === element) repainted.add(name);
      }
    }
    return original(element, signature, html);
  };

  for (let position = 41; position < 46; position += 1) {
    const push = snapshotFixture({ playback: playbackFixture({ position }) });
    delete push.tree;
    card._apply(push);
  }
  assert.deepEqual([...repainted], [], "an unchanged section was repainted");
});

test("a changed value still repaints its section", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const before = card.querySelector('[data-el="queue"]').innerHTML;
  // A real queue change must still be reflected.
  const shorter = queueFixture();
  shorter.items = [shorter.items[0]];
  shorter.count = 1;
  card._apply(snapshotFixture({ queue: shorter }));
  assert.notEqual(card.querySelector('[data-el="queue"]').innerHTML, before);

  const controlsBefore = card.querySelector('[data-el="controls"]').innerHTML;
  card._apply(
    snapshotFixture({ playback: playbackFixture({ state: "paused" }) })
  );
  assert.notEqual(card.querySelector('[data-el="controls"]').innerHTML, controlsBefore);
});

// -- played queue rows -----------------------------------------------------

test("played rows are marked but stay in the queue", async () => {
  const { card: Card } = await loadCard();
  const queue = queueFixture({
    items: [
      Object.assign({}, queueFixture().items[0], { current: false, played: true }),
      Object.assign({}, queueFixture().items[1], { current: true, played: false }),
    ],
    index: 1,
  });
  const card = makeCard(Card, {}, snapshotFixture({ queue }));
  const html = card.querySelector('[data-el="queue"]').innerHTML;
  assert.match(html, /is-played/);
  assert.match(html, /is-current/);
  // Both rows are still rendered - a played row is marked, never removed.
  assert.match(html, /Stand By Me/);
  assert.match(html, /Second Song/);
  assert.match(card.querySelector('[data-el="queue-meta"]').textContent, /2\/2/);
});

test("the queue header shows the position within the queue", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  assert.match(card.querySelector('[data-el="queue-meta"]').textContent, /1\/2/);
});

test("every rendered data-act is either a backend operation or UI-only", async () => {
  const source = await readFile(SOURCE_URL, "utf8");
  const actions = new Set([...source.matchAll(/data-act="([a-zA-Z_-]+)"/g)].map((m) => m[1]));
  assert.ok(actions.size > 10, "expected the card to render many actions");

  const table = source.slice(
    source.indexOf("const ACTION_OPERATIONS = {"),
    source.indexOf("const UI_ONLY_ACTIONS")
  );
  const operations = new Set([...table.matchAll(/^\s{2}([a-z_]+):/gm)].map((m) => m[1]));
  const uiOnlyBlock = source.slice(
    source.indexOf("const UI_ONLY_ACTIONS = new Set(["),
    source.indexOf("const FORM_CONTROL_TAGS")
  );
  const uiOnly = new Set([...uiOnlyBlock.matchAll(/"([a-z_-]+)"/g)].map((m) => m[1]));

  for (const action of actions) {
    const known = operations.has(action) || uiOnly.has(action);
    assert.ok(known, `data-act="${action}" is neither a backend operation nor UI-only`);
  }
  for (const action of uiOnly) {
    assert.ok(!operations.has(action), `${action} must not be both`);
  }
  // Every operation the table can send must have a payload mapping.
  for (const action of operations) {
    assert.ok(source.includes(`case "${action}":`), `no _payloadFor case for ${action}`);
  }
});

test("the click handler ignores form controls", async () => {
  const source = await readFile(SOURCE_URL, "utf8");
  assert.ok(source.includes("FORM_CONTROL_TAGS.has(trigger.tagName)"));
  assert.ok(source.includes('const FORM_CONTROL_TAGS = new Set(["INPUT", "SELECT", "TEXTAREA", "OPTION"]);'));
  assert.ok(source.includes("const operation = ACTION_OPERATIONS[act];"));
  assert.ok(source.includes("if (!operation || UI_ONLY_ACTIONS.has(act))"));
});

// -- end action dispatch audit ---------------------------------------------

test("the editor emits config-changed for every field", async () => {
  const { editor: Editor } = await loadCard();
  const editor = new Editor();
  editor.setConfig({ target: "entry-1" });
  editor._targets = [
    { id: "entry-1", name: "Kitchen CODIE" },
    { id: "entry-2", name: "Office CODIE" },
  ];
  editor._render();
  const changes = [];
  editor.dispatchEvent = (event) => changes.push(event.detail.config);

  editor.querySelector('[data-el="target"]').fire("change", { target: { value: "entry-2" } });
  assert.equal(changes.length, 1);
  assert.equal(changes[0].target, "entry-2");

  editor.querySelector('[data-el="name"]').fire("input", { target: { value: "Küche" } });
  assert.equal(changes[1].name, "Küche");

  editor.querySelector('[data-el="show_queue"]').fire("change", { target: { checked: false } });
  assert.equal(changes[2].show_queue, false);
  editor.querySelector('[data-el="show_queue"]').fire("change", { target: { checked: true } });
  assert.equal(changes[3].show_queue, undefined);
});

test("the card source declares no Code27 API access and no persisted state", async () => {
  const source = await readFile(SOURCE_URL, "utf8");
  for (const forbidden of ["/data/user/", "music_control", "localStorage", "sessionStorage", "fetch("]) {
    assert.ok(!source.includes(forbidden), `card must not contain ${forbidden}`);
  }
});

test("every provider namespace stays inside the card's provider boundary", async () => {
  const source = await readFile(SOURCE_URL, "utf8");
  const start = source.indexOf("frontend provider boundary (start)");
  const end = source.indexOf("frontend provider boundary (end)");
  assert.ok(start > 0 && end > start, "the provider boundary markers must exist in order");
  const occurrences = [...source.matchAll(/hase\/code27\/music/g)];
  // Exactly the configured command and its subscription, both inside the
  // boundary - never at a call site.
  assert.equal(occurrences.length, 2);
  for (const occurrence of occurrences) {
    assert.ok(
      occurrence.index > start && occurrence.index < end,
      `provider namespace outside the boundary at offset ${occurrence.index}`
    );
  }
  assert.ok(source.includes("const PROVIDER_TRANSPORTS = {"));
  assert.ok(source.includes("this._transport().command"));
});

test("shows MusicMode ist off! and disables the transport when the music screen is closed", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(
    Card,
    {},
    snapshotFixture({ music_mode: { known: true, active: false } })
  );
  assert.equal(card.querySelector('[data-el="lcd-title"]').textContent, "MusicMode ist off!");
  assert.ok(card.querySelector('[data-el="shell"]').classList.contains("ha-mm-off"));
  assert.match(card.querySelector('[data-el="controls"]').innerHTML, /data-action="pause" disabled/);
  const on = makeCard(Card, {}, snapshotFixture({ music_mode: { known: true, active: true } }));
  assert.notEqual(on.querySelector('[data-el="lcd-title"]').textContent, "MusicMode ist off!");
  const unknown = makeCard(Card, {}, snapshotFixture());
  assert.notEqual(unknown.querySelector('[data-el="lcd-title"]').textContent, "MusicMode ist off!");
});

test("tabs show one panel at a time and switching is card state only", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  assert.equal(card.querySelector('[data-el="queue-panel"]').hidden, false);
  assert.equal(card.querySelector('[data-el="playlist-panel"]').hidden, true);
  await card.dispatch("click", clickTarget({ act: "tab", tab: "lists" }, "", "BUTTON"));
  assert.equal(card.querySelector('[data-el="queue-panel"]').hidden, true);
  assert.equal(card.querySelector('[data-el="playlist-panel"]').hidden, false);
  assert.deepEqual(calls, []);
});

test("a double click plays the queue row through the normal operation", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  const event = clickTarget({ dbl: "queue_play", item_id: "7" }, "", "DIV");
  await card.dispatch("dblclick", event);
  assert.equal(calls[0].operation, "queue_play");
  assert.deepEqual(calls[0].data, { target: "entry-1", item_id: 7 });
});

test("import uploads in chunks, then builds the playlist from the queue operations", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const uploaded = [{ uri: "local:/storage/a.mp3", title: "a", provider: "code27", duration: 3 }];
  const { hass, calls } = scriptedHass(Card, {
    snapshot: snapshotFixture(),
    onCall: (message) => {
      if (message.operation === "import_chunk") {
        const last = message.data.index === message.data.total - 1;
        return last ? { done: true, tracks: uploaded, total: message.data.total } : { done: false };
      }
      return snapshotFixture();
    },
  });
  card._hass = hass;
  const bytes = new Uint8Array(1024 * 1024 + 10);
  const file = {
    name: "a.mp3", size: bytes.length, type: "audio/mpeg",
    slice: (from, to) => ({ arrayBuffer: async () => bytes.slice(from, to).buffer }),
  };
  card._setImportFiles([file]);
  card._importName = "Neu";
  await card._runImport(true);
  const operations = calls.map((call) => call.operation).filter((name) => name !== "snapshot");
  assert.deepEqual(operations, ["import_chunk", "import_chunk", "queue_clear", "queue_add", "playlist_save_as"]);
  assert.equal(calls[0].data.total, 2);
  assert.equal(calls[0].data.name, "a.mp3");
  assert.match(card._import.message, /Neu/);
  assert.equal(card._import.error, false);
});

test("import asks for a playlist name before uploading anything", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  card._setImportFiles([{ name: "a.mp3", size: 3, type: "audio/mpeg", slice: () => ({}) }]);
  await card._runImport(true);
  assert.deepEqual(calls, []);
  assert.equal(card._import.error, true);
});

test("the accent follows the device light colour and falls back to the default", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const shell = card.querySelector('[data-el="shell"]');
  const set = {};
  shell.style.setProperty = (name, value) => { set[name] = value; };
  shell.style.removeProperty = (name) => { delete set[name]; };
  card._hass = {
    ...ownerRegistry,
    entities: {
      ...ownerRegistry.entities,
      "light.codie_umgebungsfarbe": { device_id: "device-1", translation_key: "preview_light" },
    },
    states: { "light.codie_umgebungsfarbe": { state: "on", attributes: { rgb_color: [255, 0, 0] } } },
    language: "de",
    callWS: async () => snapshotFixture(),
  };
  card._render();
  assert.match(set["--ha-lcd-ink"], /^hsl\(0 /);
  assert.match(set["--ha-b2"], /^hsl\(0 /);
  // Light off -> back to the stylesheet default.
  card._hass.states["light.codie_umgebungsfarbe"].state = "off";
  card._render();
  assert.equal(set["--ha-lcd-ink"], undefined);
  assert.equal(set["--ha-b2"], undefined);
  // The switch in the config turns the behaviour off entirely.
  const off = makeCard(Card, { follow_light: false });
  assert.equal(off._lightRgb !== undefined, true);
  assert.equal(off._show.follow_light, false);
});

test("a near-black or white light still yields a readable accent", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  assert.match(card._accentFor([1, 1, 1]).ink, /58%\)$/);
  // Blue/violet looks dark at the same lightness, so its text is lighter.
  assert.match(card._accentFor([90, 20, 255]).ink, /70%\)$/);
  // The whole chrome is tinted, not just the text.
  const skin = card._accentFor([255, 0, 0]);
  assert.match(skin["--ha-b1"], /^hsl\(0 /);
  assert.match(skin["--ha-s1"], /^hsl\(0 /);
  assert.match(card._accentFor([255, 255, 255]).ink, /^hsl\(0 0%/);
});

test("an M3U is matched against the TF card and only missing files are uploaded", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const track = (name) => ({ uri: `local:/storage/x/${name}`, title: name, provider: "code27", duration: 3 });
  const { hass, calls } = scriptedHass(Card, {
    snapshot: snapshotFixture(),
    onCall: (message) => {
      if (message.operation === "inventory") {
        return { ready: true, files: [
          { name: "a.mp3", size: 10, track: track("a.mp3") },
          { name: "same-name.mp3", size: 99, track: track("same-name.mp3") },
        ] };
      }
      if (message.operation === "import_chunk") {
        return { done: true, tracks: [track(message.data.name)], total: 1 };
      }
      return snapshotFixture();
    },
  });
  card._hass = hass;
  const m3u = {
    name: "Road Trip.m3u",
    text: async () => "#EXTM3U\r\nC:\\Musik\\Rock\\A.MP3\r\n/home/me/b.mp3\r\nsame-name.mp3\r\nhttp://example.com/x.mp3\r\nmissing.mp3\r\n",
  };
  const local = (name, size) => ({ name, size, type: "audio/mpeg", slice: () => ({ arrayBuffer: async () => new ArrayBuffer(size) }) });
  card._setImportFiles([m3u, local("b.mp3", 4), local("same-name.mp3", 5)]);
  await new Promise((resolve) => setTimeout(resolve, 20));
  const states = card._importPlan.plan.map((row) => [row.name, row.state]);
  assert.deepEqual(states, [
    ["A.MP3", "card"], ["b.mp3", "upload"], ["same-name.mp3", "card"],
    ["http://example.com/x.mp3", "url"], ["missing.mp3", "missing"],
  ]);
  assert.equal(card._importPlan.plan[2].differs, true);
  assert.equal(card._importName, "Road Trip");
  await card._runImport(true);
  const uploads = calls.filter((call) => call.operation === "import_chunk");
  assert.equal(uploads.length, 1);
  assert.equal(uploads[0].data.name, "b.mp3");
  const add = calls.find((call) => call.operation === "queue_add");
  assert.deepEqual(add.data.tracks.map((row) => row.title), ["a.mp3", "b.mp3", "same-name.mp3"]);
  assert.equal(calls.find((call) => call.operation === "playlist_save_as").data.name, "Road Trip");
});

test("M3U lines are reduced to file names and web links are flagged", async () => {
  const source = await readFile(SOURCE_URL, "utf8");
  assert.match(source, /function parseM3u\(text\)/);
});

test("deleting a file asks first and then sends exactly one delete_file", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  await card.dispatch("click", clickTarget({ act: "delete-ask", kind: "file", uri: "local:/s/a.mp3", title: "a" }, "", "BUTTON"));
  assert.deepEqual(calls, []);
  assert.match(card.querySelector('[data-el="browser-confirm"]').innerHTML, /data-act="delete-yes"/);
  await card.dispatch("click", clickTarget({ act: "delete-no" }, "", "BUTTON"));
  assert.deepEqual(calls, []);
  await card.dispatch("click", clickTarget({ act: "delete-ask", kind: "folder", node: "folder:Rock", title: "Rock" }, "", "BUTTON"));
  await card.dispatch("click", clickTarget({ act: "delete-yes" }, "", "BUTTON"));
  assert.equal(calls[0].operation, "delete_folder");
  assert.deepEqual(calls[0].data, { target: "entry-1", node: "folder:Rock" });
});

test("playlist editing sends the new order, never invented tracks", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, {
    snapshot: snapshotFixture(),
    onCall: (message) => (message.operation === "playlist_update"
      ? { name: "Rock", tracks: [{ uri: "local:/c.mp3", title: "c" }], playlists: [] }
      : snapshotFixture()),
  });
  card._hass = hass;
  card._plTracks = { name: "Rock", tracks: [{ uri: "local:/a.mp3" }, { uri: "local:/b.mp3" }, { uri: "local:/c.mp3" }] };
  assert.deepEqual(card._payloadFor("playlist_update", { op: "down", pos: "0" }), { name: "Rock", order: [1, 0, 2] });
  assert.deepEqual(card._payloadFor("playlist_update", { op: "remove", pos: "1" }), { name: "Rock", order: [0, 2] });
  assert.deepEqual(card._payloadFor("playlist_update", { op: "up", pos: "0" }), { name: "Rock", order: [0, 1, 2] });
  assert.throws(() => card._payloadFor("playlist_update", { op: "up", pos: "9" }));
  await card.dispatch("click", clickTarget({ act: "playlist_update", op: "remove", pos: "0" }, "", "BUTTON"));
  assert.equal(calls[0].operation, "playlist_update");
  assert.deepEqual(card._plTracks.tracks.map((t) => t.title), ["c"]);
});

test("a playlist exports as an M3U the importer can read back", async () => {
  const source = (await readFile(SOURCE_URL, "utf8")).replace(/\r\n/g, "\n");
  const fn = (name) => new Function(`${source.slice(source.indexOf(`function ${name}`), source.indexOf("\n}\n", source.indexOf(`function ${name}`)) + 3)}; return ${name};`)();
  const buildM3u = fn("buildM3u");
  const parseM3u = fn("parseM3u");
  const text = buildM3u([{ uri: "local:/storage/x/Song A.mp3", title: "A", duration: 61.2 }]);
  assert.match(text, /^#EXTM3U\r\n#EXTINF:61,A\r\nSong A\.mp3\r\n$/);
  assert.deepEqual(parseM3u(text).map((row) => row.name), ["Song A.mp3"]);
});

test("a folder becomes a playlist through the existing operations", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  await card.dispatch("click", clickTarget({ act: "folder-playlist", node: "folder:Rock", title: "Rock" }, "", "BUTTON"));
  const operations = calls.map((call) => call.operation).filter((name) => name !== "snapshot");
  assert.deepEqual(operations.slice(0, 3), ["queue_clear", "enqueue_folder", "playlist_save_as"]);
  assert.equal(calls.find((call) => call.operation === "playlist_save_as").data.name, "Rock");
});

test("a full TF card blocks an import before anything is uploaded", async () => {
  const { card: Card } = await loadCard();
  const snapshot = snapshotFixture({ library: { revision: "r", ready: true, capacity_total_bytes: 1000, capacity_used_bytes: 900 } });
  const card = makeCard(Card, {}, snapshot);
  const { hass, calls } = scriptedHass(Card, { snapshot });
  card._hass = hass;
  card._setImportFiles([{ name: "a.mp3", size: 500, type: "audio/mpeg", slice: () => ({}) }]);
  await card._runImport(false);
  assert.deepEqual(calls, []);
  assert.equal(card._import.error, true);
});

test("a dropped folder names the playlist and keeps its file order", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const track = (name) => ({ uri: `local:/storage/x/Road Trip/${name}`, title: name, provider: "code27", duration: 3 });
  const { hass, calls } = scriptedHass(Card, {
    snapshot: snapshotFixture(),
    onCall: (message) => {
      if (message.operation === "inventory") {
        return { ready: true, files: [{ name: "2 b.mp3", relative: "2 b.mp3", size: 7, track: track("2 b.mp3") }] };
      }
      if (message.operation === "import_chunk") return { done: true, tracks: [track(message.data.name)], total: 1 };
      return snapshotFixture();
    },
  });
  card._hass = hass;
  const local = (name, size) => ({ name, size, type: "audio/mpeg", slice: () => ({ arrayBuffer: async () => new ArrayBuffer(size) }) });
  card._setImportFiles([local("10 c.mp3", 3), local("2 b.mp3", 7), local("1 a.mp3", 5)], "Road Trip");
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(card._importName, "Road Trip");
  assert.deepEqual(card._importPlan.plan.map((row) => [row.name, row.state]), [
    ["1 a.mp3", "upload"], ["2 b.mp3", "card"], ["10 c.mp3", "upload"],
  ]);
  await card._runImport(true);
  const uploads = calls.filter((call) => call.operation === "import_chunk");
  // The device keeps every upload in its music root, so no folder is sent.
  assert.deepEqual(uploads.map((call) => [call.data.name, "folder" in call.data]), [["1 a.mp3", false], ["10 c.mp3", false]]);
  assert.equal(calls.find((call) => call.operation === "playlist_save_as").data.name, "Road Trip");
});

test("a name the user typed is never replaced by a folder name", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  card._importName = "Mine";
  card._importNameAuto = false;
  card._setImportFiles([{ name: "a.mp3", size: 1, type: "audio/mpeg", webkitRelativePath: "Folder/a.mp3", slice: () => ({}) }]);
  assert.equal(card._importName, "Mine");
});

test("a playlist that already exists is refused before anything is uploaded", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, { snapshot: snapshotFixture() });
  card._hass = hass;
  card._setImportFiles([{ name: "a.mp3", size: 1, type: "audio/mpeg", slice: () => ({}) }]);
  card._importName = "Rock";
  await card._runImport(true);
  assert.deepEqual(calls, []);
  assert.equal(card._import.error, true);
});

test("percent-encoded names are shown decoded and left alone otherwise", async () => {
  const source = (await readFile(SOURCE_URL, "utf8")).replace(/\r\n/g, "\n");
  const start = source.indexOf("function nice(");
  const nice = new Function(`${source.slice(start, source.indexOf("\n}\n", start) + 3)}; return nice;`)();
  assert.equal(nice("Cliff%20Richard-%20Wine.mp3"), "Cliff Richard- Wine.mp3");
  assert.equal(nice("100% Real.mp3"), "100% Real.mp3");
  assert.equal(nice("bad%E0%A4%A.mp3"), "bad%E0%A4%A.mp3");
});

test("scan & create asks the backend once and reports what it made", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass, calls } = scriptedHass(Card, {
    snapshot: snapshotFixture(),
    onCall: (message) => (message.operation === "playlists_from_folders"
      ? { created: ["A", "B"], skipped: 1, playlists: [] }
      : snapshotFixture()),
  });
  card._hass = hass;
  await card.dispatch("click", clickTarget({ act: "folders-scan" }, "", "BUTTON"));
  assert.deepEqual(calls.map((call) => call.operation).filter((n) => n !== "snapshot"), ["playlists_from_folders"]);
  assert.match(card._scanMessage, /2 Playlists angelegt, 1 gab es schon/);
});

test("a refused delete shows its reason next to the question, not in the top banner", async () => {
  const { card: Card } = await loadCard();
  const card = makeCard(Card);
  const { hass } = scriptedHass(Card, {
    snapshot: snapshotFixture(),
    onCall: (message) => {
      if (message.operation === "delete_folder") throw new Error("'x' is playing right now - pause it first");
      return snapshotFixture();
    },
  });
  card._hass = hass;
  await card.dispatch("click", clickTarget({ act: "delete-ask", kind: "folder", node: "folder:Rock", title: "Rock" }, "", "BUTTON"));
  await card.dispatch("click", clickTarget({ act: "delete-yes" }, "", "BUTTON"));
  const box = card.querySelector('[data-el="browser-confirm"]').innerHTML;
  assert.match(box, /Nicht gelöscht: .*playing right now/);
  assert.match(box, /data-act="delete-no"/);
  assert.doesNotMatch(box, /data-act="delete-yes"/);
  assert.equal(card._error, null);
  // OK dismisses it.
  await card.dispatch("click", clickTarget({ act: "delete-no" }, "", "BUTTON"));
  assert.equal(card.querySelector('[data-el="browser-confirm"]').innerHTML, "");
});

test("a playlist that was played before offers Resume with the remembered spot", async () => {
  const { card: Card } = await loadCard();
  const base = snapshotFixture();
  const snapshot = snapshotFixture({
    playlists: [{ name: "Book", count: 18, resume: { index: 4, title: "Kapitel 5", position: 754, updated_at: "x" } }],
  });
  const card = makeCard(Card, {}, snapshot);
  const { hass, calls } = scriptedHass(Card, { snapshot });
  card._hass = hass;
  card._playlistPick = "Book";
  card._renderPlaylists(card._snapshot);
  const html = card.querySelector('[data-el="playlist-body"]').innerHTML;
  assert.match(html, /data-act="pl-select" data-name="Book"[^>]*data-dbl="playlist_load" data-resume="1"/);
  assert.match(html, /▶ Titel 5 · 12:34/);
  assert.match(html, /data-act="playlist_load" data-resume="1" data-name="Book"[^>]*title="[^"]*Titel 5[^"]*12:34/);
  // The button sends playlist_load with resume.
  await card.dispatch("click", clickTarget({ act: "playlist_load", resume: "1", name: "Book" }, "", "BUTTON"));
  assert.equal(calls[0].operation, "playlist_load");
  assert.deepEqual(calls[0].data, { target: "entry-1", name: "Book", resume: true });
  // Plain Load never resumes.
  calls.length = 0;
  await card.dispatch("click", clickTarget({ act: "playlist_load" }, "", "BUTTON"));
  assert.deepEqual(calls[0].data, { target: "entry-1", name: "Book" });
  void base;
});

test("Resume is disabled for a playlist that was never played", async () => {
  const { card: Card } = await loadCard();
  const snapshot = snapshotFixture({ playlists: [{ name: "New", count: 3, resume: null }] });
  const card = makeCard(Card, {}, snapshot);
  card._playlistPick = "New";
  card._renderPlaylists(card._snapshot);
  const html = card.querySelector('[data-el="playlist-body"]').innerHTML;
  assert.match(html, /data-resume="1" data-name="New"[^>]*disabled/);
  assert.doesNotMatch(html, /data-dbl="playlist_load" data-resume="1"/);
});

test("the sound presets are an equalizer-style switch that follows the device", async () => {
  const { card: Card } = await loadCard();
  const features = Object.assign(snapshotFixture().features, { sound_effect: true, sound_effects: ["classical", "surround", "vocal"] });
  const snapshot = snapshotFixture({ features, playback: playbackFixture({ sound_effect: "surround" }) });
  const card = makeCard(Card, {}, snapshot);
  const html = card.querySelector('[data-el="options"]').innerHTML;
  assert.match(html, /class="ha-btn is-on" data-act="set_sound_effect" data-effect="surround"/);
  assert.match(html, /data-act="set_sound_effect" data-effect="classical"/);
  assert.match(html, />Klassik</);
  const { hass, calls } = scriptedHass(Card, { snapshot });
  card._hass = hass;
  await card.dispatch("click", clickTarget({ act: "set_sound_effect", effect: "vocal" }, "", "BUTTON"));
  assert.equal(calls[0].operation, "set_sound_effect");
  assert.deepEqual(calls[0].data, { target: "entry-1", effect: "vocal" });
  // A device without presets gets no switch.
  const plain = makeCard(Card);
  assert.doesNotMatch(plain.querySelector('[data-el="options"]').innerHTML, /set_sound_effect/);
});
