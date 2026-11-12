import test from "node:test";
import assert from "node:assert/strict";
import { code27Devices, code27Owner, code27MusicOwner, code27MusicTargets } from "../scripts/code27_owner_shared.js";

function registry(domain, entryId = `${domain}-entry`) {
  return {
    devices: { "device-1": { identifiers: [[domain, "machine-1"]], config_entries: [entryId] } },
    entities: { "sensor.codie_active_character": {
      device_id: "device-1", config_entry_id: entryId, translation_key: "active_character",
    } },
  };
}

const transports = {
  code27: { command: "hase/code27/music" },
  code27_standalone: { command: "code27/music" },
};

for (const domain of ["hase", "code27"]) {
  test(`${domain} owner resolves its own chat service and music target`, async () => {
    const hass = registry(domain);
    hass.callWS = async () => { throw new Error("discovery should not be needed"); };
    const selected = code27Owner(hass, { device_id: "device-1", config_entry_id: "stale-entry" });
    assert.equal(selected.domain, domain);
    assert.equal(selected.config_entry_id, `${domain}-entry`);
    assert.equal(code27Devices(hass).length, 1);
    const music = await code27MusicOwner(hass, selected.config_entry_id, transports);
    assert.equal(music.provider, domain === "hase" ? "code27" : "code27_standalone");
  });
}

test("missing or ambiguous registry ownership is never guessed", () => {
  const hass = registry("code27");
  hass.devices["device-1"].identifiers.push(["hase", "machine-1"]);
  assert.equal(code27Owner(hass, { device_id: "device-1" }), null);
  hass.devices["device-1"].identifiers = [["code27", "machine-1"]];
  hass.devices["device-1"].config_entries.push("other-entry");
  delete hass.entities["sensor.codie_active_character"].config_entry_id;
  assert.equal(code27Owner(hass, { device_id: "device-1" }), null);
});

test("music discovery finds a standalone target without a HASE music runtime", async () => {
  const calls = [];
  const hass = { callWS: async (message) => {
    calls.push(message.type);
    if (message.type === "hase/code27/music") throw new Error("not installed");
    return { targets: [{ id: "standalone-entry", name: "CODIE" }] };
  } };
  const found = await code27MusicTargets(hass, transports);
  assert.equal(found[0].provider, "code27_standalone");
  assert.equal((await code27MusicOwner(hass, "standalone-entry", transports)).provider, "code27_standalone");
  assert.ok(calls.includes("code27/music"));
});

test("unloaded music target reports unavailable without sending an action", async () => {
  const hass = { callWS: async () => ({ targets: [] }) };
  await assert.rejects(code27MusicOwner(hass, "missing-entry", transports), /unavailable/);
});

test("CODE27 card chat uses registry owner even with a stale YAML entry ID", async () => {
  globalThis.HTMLElement = class {};
  const registered = new Map();
  globalThis.customElements = { get: (name) => registered.get(name), define: (name, type) => registered.set(name, type) };
  globalThis.window = { customCards: [] };
  await import("../custom_components/hase/cards/code27-card.js");
  const Card = registered.get("code27-card");
  const Editor = registered.get("code27-card-editor");
  const editor = new Editor();
  editor._hass = { config: { components: ["code27"] } };
  assert.equal(editor._portraitPlaceholder(), "/code27-cards/portrait.png");
  editor._hass = { config: { components: ["hase", "code27"] } };
  assert.equal(editor._portraitPlaceholder(), "/hase-cards/portrait.png");
  editor._hass = { services: { hase: {} } };
  assert.equal(editor._portraitPlaceholder(), "/hase-cards/portrait.png");
  for (const domain of ["hase", "code27"]) {
    const card = new Card();
    card._hass = registry(domain);
    card._deviceId = "device-1";
    card._config = { config_entry_id: "stale-entry" };
    card._p = "codie";
    const input = { value: "Hallo" };
    card.querySelector = () => input;
    const calls = [];
    card._call = (...args) => calls.push(args);
    card._sendChat();
    assert.deepEqual(calls, [[domain, "send_chat_message", { config_entry_id: `${domain}-entry`, message: "Hallo" }]]);
    assert.equal(input.value, "");
  }
});

test("CODE27 todo refresh keeps one request in flight and backs off after failure", async () => {
  const Card = globalThis.customElements.get("code27-card");
  const card = new Card();
  let resolveRequest;
  let calls = 0;
  const connection = { sendMessagePromise: () => { calls++; return new Promise((resolve) => { resolveRequest = resolve; }); } };
  card._hass = { connection };
  card._eid = () => "todo.notes";
  card.querySelector = () => ({ dataset: {}, innerHTML: "" });
  card._t = (key) => key;
  card._notesEpoch = 0;
  const pending = card._updateNotes();
  await card._updateNotes();
  assert.equal(calls, 1);
  resolveRequest({ items: [] });
  await pending;
  connection.sendMessagePromise = async () => { calls++; throw new Error("offline"); };
  await card._updateNotes();
  await card._updateNotes();
  assert.equal(calls, 2);
  assert.ok(card._notesRetryAt > Date.now());
});

test("CODE27 card lists alarms and notes from the sensors and edits through the owner's services", async () => {
  const Card = globalThis.customElements.get("code27-card");
  const card = new Card();
  const calls = [];
  card._hass = {
    ...registry("hase"),
    states: {
      "sensor.codie_aktive_alarme": { attributes: { alarms: [
        { id: "a1", hour: 7, minute: 30, time: "07:30", label: "Aufstehen", days: ["mon", "tue"], enabled: true },
      ] } },
      "sensor.codie_notizen": { attributes: { memos: [
        { id: "m1", title: "Paket", body: "", type: "reminder", date: "2026-12-24", time: "08:15", expired: false },
        { id: "m2", title: "Alt", body: "x", type: "reminder", date: "2020-01-01", time: "09:00", expired: true },
        { id: "m3", title: "Einkauf", body: "Milch, Brot", type: "note", date: null, time: null, expired: false },
      ] } },
    },
    callService: async (domain, service, data) => { calls.push([domain, service, data]); },
  };
  card._deviceId = "device-1";
  card._config = {};
  card._p = "codie";
  card._eid = (domain, suffix) => `${domain}.codie_${suffix}`;
  card._t = (key) => (key === "days" ? { mon: "Mo", tue: "Di" } : key);
  const elements = {};
  card.querySelector = (selector) => (elements[selector] ||= { dataset: {}, innerHTML: "" });

  card._updateAlarms();
  const alarms = elements['[data-el="alarms"]'].innerHTML;
  assert.match(alarms, /07:30/);
  assert.match(alarms, /Aufstehen · Mo Di/);
  assert.match(alarms, /data-action="alarm-enable" data-id="a1"/);

  assert.equal(card._updateMemosFromSensor(), true);
  const notes = elements['[data-el="notes"]'].innerHTML;
  assert.match(notes, /Paket/);
  assert.match(notes, /2026-12-24 08:15/);
  assert.match(notes, /Milch, Brot/);
  assert.doesNotMatch(notes, /Alt/, "expired reminders are not listed");

  assert.equal(await card._saveAlarm({ id: "a1", hour: 7, minute: 30, label: "Aufstehen", days: ["mon", "tue"], enabled: false }), null);
  assert.deepEqual(calls.at(-1), ["hase", "update_alarm", {
    config_entry_id: "hase-entry", alarm_id: "a1", hour: 7, minute: 30, label: "Aufstehen",
    days: ["mon", "tue"], enabled: false,
  }]);
  await card._saveAlarm({ hour: 6, minute: 0, label: "", days: [], enabled: true });
  assert.equal(calls.at(-1)[1], "create_alarm");
  assert.equal("alarm_id" in calls.at(-1)[2], false);
});

test("CODE27 card shows the service error instead of closing the form", async () => {
  const Card = globalThis.customElements.get("code27-card");
  const card = new Card();
  card._hass = { ...registry("hase"), callService: async () => { throw new Error("device said no"); } };
  card._deviceId = "device-1";
  card._config = {};
  card._p = "codie";
  assert.equal(await card._ownerCall("delete_alarm", { alarm_id: "a1" }), "device said no");
});

test("CODE27 card keeps the old switch list and to-do path on backends without the attributes", () => {
  const Card = globalThis.customElements.get("code27-card");
  const card = new Card();
  card._hass = { ...registry("hase"), states: {} };
  card._eid = (domain, suffix) => `${domain}.codie_${suffix}`;
  assert.equal(card._alarmsFromSensor(), null);
  assert.equal(card._memosFromSensor(), null);
  assert.equal(card._updateMemosFromSensor(), false);
});

test("CODE27 card finds its owner and heals when the YAML device_id is stale", () => {
  const Card = globalThis.customElements.get("code27-card");
  const card = new Card();
  card._hass = registry("hase");
  card._config = { device_id: "gone", config_entry_id: "also-gone" };
  card._deviceId = "gone";
  card._p = "codie";
  const owner = card._owner();
  assert.equal(owner.domain, "hase");
  assert.equal(owner.config_entry_id, "hase-entry");
  // `set hass` drops an id the registry no longer has and re-resolves.
  card._built = true;
  card._update = () => {};
  card._eidCache = { cached: "x" };
  card.hass = registry("hase");
  assert.equal(card._deviceId, "device-1");
  assert.deepEqual(card._eidCache, {});
});

test("CODE27 owner error explains what the registry contains", async () => {
  const Card = globalThis.customElements.get("code27-card");
  const card = new Card();
  card._hass = { devices: {}, entities: {} };
  card._config = {};
  card._deviceId = "gone";
  card._p = "codie";
  const message = await card._ownerCall("delete_alarm", { alarm_id: "a" });
  assert.match(message, /CODE27 device not found \(registry: none; card device_id: gone/);
});

function outfitCard(overrides = {}) {
  const Card = globalThis.customElements.get("code27-card");
  const card = new Card();
  const select = overrides.select ?? { state: "Lylia", attributes: { options: ["Outfit 1", "Lylia", "Lylia_v2"] } };
  card._hass = {
    entities: {
      "image.preview1": { device_id: "device-1" },
      "image.portrait": { device_id: "device-1" },
    },
    states: {
      "select.outfit": select,
      "binary_sensor.model_loading": { state: overrides.loading ? "on" : "off" },
      "image.preview1": { attributes: { cloth_id: 1, label: "Outfit 1", entity_picture: "/api/image_proxy/image.preview1?token=t" } },
      // A character portrait carries slot_number, not cloth_id: never an outfit preview.
      "image.portrait": { attributes: { slot_number: 1, entity_picture: "/api/image_proxy/image.portrait?token=p" } },
    },
  };
  card._deviceId = "device-1";
  card._p = "codie";
  card._eid = (domain, suffix) => `${domain}.${suffix}`;
  card._t = (key) => key;
  const elements = {
    '[data-el="outfitsection"]': { hidden: true },
    '[data-el="outfitbody"]': { hidden: overrides.folded ?? false },
    '[data-el="outfitlist"]': { dataset: {}, innerHTML: "", classList: { toggle(name, on) { this[name] = on; } } },
    '[data-el="outfitcount"]': { textContent: "" },
  };
  card.querySelector = (selector) => (elements[selector] ||= { dataset: {}, innerHTML: "" });
  return { card, elements };
}

test("CODE27 outfit chips show a preview image and name, with a letter fallback", () => {
  const { card, elements } = outfitCard();
  card._updateOutfits();
  const html = elements['[data-el="outfitlist"]'].innerHTML;
  assert.equal(elements['[data-el="outfitsection"]'].hidden, false);
  assert.match(html, /<img src="\/api\/image_proxy\/image\.preview1\?token=t">/);
  assert.match(html, /class="c27-char-chip is-active" data-action="outfit-select" data-option="Lylia"/);
  assert.match(html, /<div class="c27-char-avatar">L<\/div>\s*<div class="c27-char-name">Lylia<\/div>/);
  assert.doesNotMatch(html, /image\.portrait/, "character portraits are not outfit previews");
  assert.equal(elements['[data-el="outfitcount"]'].textContent, "· Lylia");
});

test("CODE27 outfit section loads nothing while folded and hides without the select", () => {
  const folded = outfitCard({ folded: true });
  folded.card._updateOutfits();
  assert.equal(folded.elements['[data-el="outfitlist"]'].innerHTML, "");
  assert.equal(folded.elements['[data-el="outfitcount"]'].textContent, "· Lylia");

  const missing = outfitCard({ select: { state: "unavailable", attributes: {} } });
  missing.card._updateOutfits();
  assert.equal(missing.elements['[data-el="outfitsection"]'].hidden, true);
});

test("CODE27 outfit section shows the loading state and picks an outfit through the select", () => {
  const { card, elements } = outfitCard({ loading: true });
  card._updateOutfits();
  assert.equal(elements['[data-el="outfitcount"]'].textContent, "· outfit_loading");
  assert.equal(elements['[data-el="outfitlist"]'].classList["is-loading"], true);
  const calls = [];
  card._call = (...args) => calls.push(args);
  card._onClick({ target: { closest: () => ({ dataset: { action: "outfit-select", option: "Lylia_v2" }, classList: { contains: () => false } }) } });
  assert.deepEqual(calls, [["select", "select_option", { entity_id: "select.outfit", option: "Lylia_v2" }]]);
});

test("CODE27 sections start folded, remember the choice and drop a form when folded", () => {
  const store = new Map();
  globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v) };
  const Card = globalThis.customElements.get("code27-card");
  const make = () => {
    const card = new Card();
    card._p = "codie";
    const elements = {};
    for (const key of ["alarms", "notes", "outfit"]) {
      elements[`[data-el="${key}body"]`] = { hidden: true };
      elements[`[data-el="${key}toggle"]`] = { classList: { toggle(name, on) { this.on = on; } } };
    }
    elements['[data-el="alarmform"]'] = { hidden: false };
    card.querySelector = (selector) => (elements[selector] ||= { hidden: true, dataset: {}, innerHTML: "" });
    return { card, elements };
  };
  const first = make();
  assert.equal(first.card._sectionOpen("alarms"), false);
  first.card._setSectionOpen("notes", true);
  first.card._applySections();
  assert.equal(first.elements['[data-el="notesbody"]'].hidden, false);
  assert.equal(first.elements['[data-el="notestoggle"]'].classList.on, true);
  // An open alarm form keeps its section unfolded even though it is not saved as open.
  assert.equal(first.elements['[data-el="alarmsbody"]'].hidden, false);
  assert.equal(store.has("code27-card-section:codie:alarms"), false);

  const second = make();
  assert.equal(second.card._sectionOpen("notes"), true, "remembered in this browser");
  assert.equal(second.card._sectionOpen("outfit"), false);

  let closed = null;
  first.card._closeForm = (name) => { closed = name; first.elements['[data-el="alarmform"]'].hidden = true; };
  first.elements['[data-el="alarmsbody"]'].hidden = false;
  first.card._toggleSection("alarms");
  assert.equal(closed, "alarmform");
  assert.equal(first.elements['[data-el="alarmsbody"]'].hidden, true);
  assert.equal(store.get("code27-card-section:codie:alarms"), "0");
});

import { readFileSync } from "node:fs";

test("CODE27 card hides every section whose show_* flag is false", () => {
  const Card = globalThis.customElements.get("code27-card");
  const card = new Card();
  const sections = ["show_light", "show_scene", "show_characters", "show_alarms", "show_notes"]
    .map((key) => ({ dataset: { show: key }, hidden: false }));
  card.querySelectorAll = () => sections;
  card._config = { show_alarms: false, show_light: false, show_chat: true };
  card._applyVisibility();
  assert.deepEqual(sections.map((s) => s.hidden), [true, false, false, true, false]);
  card._config = {};
  card._applyVisibility();
  assert.equal(sections.every((s) => s.hidden === false), true, "on unless explicitly false");
  assert.equal(card._show("show_outfit"), true);
  card._config = { show_outfit: false };
  assert.equal(card._show("show_outfit"), false);
});

test("CODE27 AI language selector lists the select's options and sets the choice", () => {
  const Card = globalThis.customElements.get("code27-card");
  const card = new Card();
  card._hass = { states: { "select.ki_sprache": { state: "Deutsch", attributes: { options: ["Deutsch", "English"] } } } };
  card._eid = (domain, suffix) => `${domain}.${suffix}`;
  card._config = {};
  const elements = {
    '[data-el="languagesection"]': { hidden: true },
    '[data-el="language"]': { dataset: {}, innerHTML: "", value: "" },
  };
  card.querySelector = (selector) => elements[selector];
  card._updateLanguage();
  assert.equal(elements['[data-el="languagesection"]'].hidden, false);
  assert.match(elements['[data-el="language"]'].innerHTML, /<option value="English">English<\/option>/);
  assert.equal(elements['[data-el="language"]'].value, "Deutsch");

  card._config = { show_language: false };
  card._updateLanguage();
  assert.equal(elements['[data-el="languagesection"]'].hidden, true);

  card._config = {};
  card._hass.states["select.ki_sprache"].state = "unavailable";
  card._updateLanguage();
  assert.equal(elements['[data-el="languagesection"]'].hidden, true, "hidden without firmware support");
});

test("CODE27 card editor offers a switch for every section the card can hide", () => {
  const source = readFileSync(new URL("../custom_components/hase/cards/code27-card.js", import.meta.url), "utf8");
  const editorBlock = source.slice(source.indexOf("const EDITOR_SHOW_FLAGS"), source.indexOf("class Code27CardEditor"));
  const flagsInMarkup = new Set([...source.matchAll(/data-show="(show_[a-z_]+)"/g)].map((m) => m[1]));
  // Sections managed in code rather than through data-show:
  // show_scene decides whether the scenes appear in the one light dropdown.
  for (const key of ["show_music", "show_outfit", "show_language", "show_chips", "show_chat", "show_feed", "show_scene"]) flagsInMarkup.add(key);
  assert.ok(flagsInMarkup.size >= 11);
  for (const key of flagsInMarkup) assert.ok(editorBlock.includes(`"${key}"`), `${key} missing in the editor`);
});

test("CODE27 card effect dropdown uses the light's effect keys and understands the old German names", () => {
  const source = readFileSync(new URL("../custom_components/hase/cards/code27-card.js", import.meta.url), "utf8");
  const constSource = readFileSync(new URL("../custom_components/hase/providers/code27/const.py", import.meta.url), "utf8");
  const backend = [...constSource.matchAll(/^LIGHT_EFFECT_[A-Z]+ = "([a-z]+)"$/gm)].map((m) => m[1]).sort();
  const card = JSON.parse(source.match(/const EFFECT_VALUES = (\[[^\]]*\])/)[1]).sort();
  assert.deepEqual(card, backend, "card and light entity must offer the same effect keys");
  const aliases = [...source.matchAll(/(\w+): "([a-z]+)"/g)].filter((m) => /^[A-Z]/.test(m[1])).map((m) => [m[1], m[2]]);
  for (const [german, key] of [["Statisch", "static"], ["Lauflicht", "chase"]]) {
    assert.ok(aliases.some(([g, k]) => g === german && k === key), `${german} -> ${key}`);
  }
  for (const labels of source.matchAll(/effect_labels: \{([^}]*)\}/g)) {
    for (const key of backend) assert.match(labels[1], new RegExp(`\\b${key}:`), `label for ${key}`);
  }
});

test("CODE27 card versions image URLs with the entity's last-changed state so switched pictures are not served from cache", () => {
  const Card = globalThis.customElements.get("code27-card");
  const card = new Card();
  const st = (state) => ({ state, attributes: { entity_picture: "/api/image_proxy/image.x?token=t" } });
  assert.equal(card._imageUrl(st("2026-10-19T19:42:01+00:00")), "/api/image_proxy/image.x?token=t&v=2026-10-19T19%3A42%3A01%2B00%3A00");
  assert.equal(card._imageUrl(st("unavailable")), "/api/image_proxy/image.x?token=t");
  assert.equal(card._imageUrl({ state: "x", attributes: { entity_picture: "/p" } }), "/p?v=x");
  assert.equal(card._imageUrl({ state: "x", attributes: {} }), null);
  assert.equal(card._imageUrl(undefined), null);
  // The same entity with a new state is a new URL (a new picture).
  assert.notEqual(card._imageUrl(st("a")), card._imageUrl(st("b")));
});

test("CODE27 card has one light dropdown for the ambient effects and the scenes", () => {
  const source = readFileSync(new URL("../custom_components/hase/cards/code27-card.js", import.meta.url), "utf8");
  // The separate scene picker is gone; its scenes live in the effect dropdown.
  assert.doesNotMatch(source, /data-el="scene"/);
  assert.match(source, /<optgroup label=/);
  // Choosing a scene applies it through the scene select; choosing an effect
  // writes the ambient light - never the other way round.
  assert.match(source, /value\.startsWith\("scene:"\)[\s\S]{0,200}select_option/);
  assert.match(source, /value\.startsWith\("fx:"\)[\s\S]{0,80}_setLight\(\{ effect:/);
  // A real scene state means "a scene is lit"; unknown/empty falls back to the effect.
  assert.match(source, /\["unknown", "unavailable", "none"\]\.includes\(scene\.state\)/);
});
