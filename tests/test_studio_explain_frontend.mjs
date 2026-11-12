// P6 Studio Explain + Conflict Badges: the frontend renders backend
// diagnostics (match_explain contract, analyzer severities) and never
// re-implements matching or conflict rules.
import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

let Studio;
globalThis.HTMLElement = class {
  attachShadow() {this.shadowRoot = {addEventListener() {},getElementById() {return null;},querySelectorAll() {return [];}};}
};
globalThis.customElements = {get() {}, define(name, ctor) {Studio = ctor;}};
await import("../custom_components/hase/frontend/studio.js");

const on = {id:"beacons_on", name:{de:"Leuchtfeuer einschalten"}, trigger:{de:"entzündet die leuchtfeuer von {area}"}, operation:"light.set_power", arguments:{on:true}, target:{kind:"area_name",capture:"area"}, tags:[]};
const alt = {id:"beacons_alt", name:{de:"Leuchtfeuer Alternative"}, trigger:{de:"entzündet die leuchtfeuer von {area}"}, operation:"light.turn_on", target:{kind:"area_name",capture:"area"}, tags:[]};
const off = {id:"beacons_off", name:{de:"Leuchtfeuer ausschalten"}, trigger:{de:"entzündet die leuchtfeuer von {area}"}, operation:"light.turn_off", target:{kind:"area_name",capture:"area"}, tags:[]};

function studio() {
  const ui = new Studio();
  ui.language="de"; ui.bookLanguage="de";
  ui.info = {books:[], actions:[], operations:[]};
  ui.current = {default_language:"de", languages:["de"], book:{id:"me", name:"ME", default_language:"de", languages:["de"], commands:[on, alt, off]}, profiles:[], spell_capabilities:{}};
  ui.registry = {areas:[], entities:[]};
  ui.render = () => {};
  return ui;
}

const variant = (role, operation, outcome) => ({role, operation, outcome, target:{kind:"area_name", value:"gondor"}});
const AMBIGUOUS = {
  contract:"hase.match_explain/1", semantics:1, text:"entzünde die leuchtfeuer von gondor", language:"de", typed_only:false,
  decision:"AMBIGUOUS", reason:"safety_ambiguity", selected:null, via_source_area_fallback:false,
  ambiguity:{reason:"safety_ambiguity", basis:"action_outcomes", spell_ids:["beacons_on","beacons_off"], variants:[variant("user","light.set_power","on"), variant("user","light.turn_off","off")], epsilon:0.05},
  candidates:[
    {spell_id:"beacons_on", trigger:"entzündet die leuchtfeuer von {area}", is_alias:false, specificity:4, score:0.971, is_exact:false, captures:{area:"gondor"}, disposition:"ambiguous", reason:"safety_ambiguity", actions:[variant("user","light.set_power","on")], engine:null, debug:{trace_reason:"eligible", order:0}},
    {spell_id:"beacons_off", trigger:"entzündet die leuchtfeuer von {area}", is_alias:false, specificity:4, score:0.971, is_exact:false, captures:{area:"gondor"}, disposition:"ambiguous", reason:"safety_ambiguity", actions:[variant("user","light.turn_off","off")], engine:null, debug:{}},
    {spell_id:"beacons_alt", trigger:"entzündet die leuchtfeuer von {area}", is_alias:false, specificity:4, score:0.971, is_exact:false, captures:{area:"gondor"}, disposition:"outranked", reason:"not_in_top_two", actions:[], engine:null, debug:{}},
  ],
  debug:{trace_reason:"top_two_opposite_within_epsilon"},
};

test("explain view renders AMBIGUOUS with reason, captures, outcomes and spells from backend data", () => {
  const html = studio().explainView(AMBIGUOUS);
  for (const text of ["AMBIGUOUS", "SAFETY_AMBIGUITY", "Leuchtfeuer einschalten", "Leuchtfeuer ausschalten",
                      "entzündet die leuchtfeuer von {area}", "area = <strong>gondor</strong>", "light.set_power → EIN",
                      "light.turn_off → AUS", "0.971", "NOT_IN_TOP_TWO"]) assert.ok(html.includes(text), text);
});

test("explain view renders NO_MATCH with rejected candidate reason and separate engine cause", () => {
  const e = {...AMBIGUOUS, decision:"NO_MATCH", reason:"no_resolvable_candidate", ambiguity:null, candidates:[
    {spell_id:"beacons_on", trigger:"x {area}", is_alias:false, specificity:1, score:1, is_exact:true, captures:{area:"k"}, disposition:"rejected", reason:"candidate_unresolvable", actions:[], engine:{layer:"engine", stage:"action_draft", reason:"argument_invalid"}, debug:{}},
    {spell_id:"beacons_off", trigger:"y", is_alias:false, specificity:1, score:null, is_exact:null, captures:{}, disposition:"rejected", reason:"input_not_fully_consumed", actions:[], engine:null, debug:{}},
  ]};
  const html = studio().explainView(e);
  for (const text of ["NO_MATCH", "NO_RESOLVABLE_CANDIDATE", "CANDIDATE_UNRESOLVABLE", "ARGUMENT_INVALID", "action_draft", "INPUT_NOT_FULLY_CONSUMED"]) assert.ok(html.includes(text), text);
});

test("debug details are only shown in expert presentation", () => {
  const ui = studio();
  assert.ok(!ui.explainView(AMBIGUOUS).includes("Debug-Details"));
  ui.expert = true;
  assert.ok(ui.explainView(AMBIGUOUS).includes("Debug-Details"));
});

test("book-level explain calls the backend explain operation and renders its answer", async () => {
  const ui = studio();
  const calls = [];
  ui.shadowRoot.getElementById = id => id === "explain-phrase" ? {value:"entzünde die leuchtfeuer von gondor"} : null;
  ui.api = async (operation, data) => {calls.push([operation, data]); return {match_explain:AMBIGUOUS};};
  await ui.act("explain-book");
  assert.deepEqual(calls, [["explain", {phrase:"entzünde die leuchtfeuer von gondor"}]]);
  assert.equal(ui.result.match_explain, AMBIGUOUS);
  assert.ok(ui.resultView(true).includes("SAFETY_AMBIGUITY"));
});

test("conflict badges render analyzer status only", () => {
  const ui = studio();
  ui.current.collision_report = {findings:[], count:0, spells:{
    beacons_on:{status:"ok", errors:0, warnings:0, infos:0, findings:[]},
    beacons_alt:{status:"warning", errors:0, warnings:1, infos:0, findings:[]},
    beacons_off:{status:"error", errors:1, warnings:0, infos:0, findings:[]},
  }};
  assert.ok(ui.collisionBadge(on).includes("✓ OK"));
  assert.ok(ui.collisionBadge(alt).includes("⚠ 1 Warnung"));
  assert.ok(ui.collisionBadge(off).includes("⛔ Sicherheitskonflikt"));
  // No findings array at all, yet the badge follows the backend status:
  // the UI does not derive conflicts from spell definitions itself.
  assert.ok(ui.collisionBadge(off).includes('data-conflict-status="error"'));
  assert.equal(ui.collisionBadge({id:"unknown"}), "");
});

test("conflict details show peer, triggers, type, shared target and both outcomes from analyzer evidence", () => {
  const ui = studio();
  ui.current.collision_report = {count:1, spells:{}, findings:[{
    kind:"safety_ambiguity", severity:"error", spell_ids:["beacons_on","beacons_off"],
    triggers:["entzündet die leuchtfeuer von {area}","entzündet die leuchtfeuer von {area}"], specificity:[4,4],
    reason:"same_tier_structurally_opposite",
    evidence:[{role:"user",operation:"light.set_power",outcome:"on",target:{kind:"area_name",value:null,capture:"area"}},
              {role:"user",operation:"light.turn_off",outcome:"off",target:{kind:"area_name",value:null,capture:"area"}}],
  }]};
  const html = ui.conflictDetailsView("beacons_off");
  for (const text of ["Leuchtfeuer einschalten", "safety_ambiguity", "Sicherheits-Mehrdeutigkeit", "area_name {area}",
                      "light.turn_off → AUS", "light.set_power → EIN", "Gleiche Trigger-Struktur, gleiches Ziel"]) assert.ok(html.includes(text), text);
  assert.ok(html.indexOf("light.turn_off → AUS") < html.indexOf("light.set_power → EIN"), "own action first");
  assert.ok(ui.conflictDetailsView("beacons_alt").includes("✓ OK"));
});

test("studio frontend contains no matching or conflict rules", async () => {
  const source = await readFile(new URL("../custom_components/hase/frontend/studio.js", import.meta.url), "utf8");
  for (const forbidden of [/0\.05\b/, /epsilon\s*[=<>]/i, /fuzzy_threshold|SequenceMatcher|levenshtein/i,
                           /outputs_are_opposite|same_target_opposite|target_relation\(/,
                           /\.sort\([^)]*score/]) assert.ok(!forbidden.test(source), String(forbidden));
});

test("explain and conflict strings have English catalogue entries", async () => {
  const {en} = await import("../custom_components/hase/frontend/studio-i18n.js");
  const source = await readFile(new URL("../custom_components/hase/frontend/studio.js", import.meta.url), "utf8");
  const missing = [...source.matchAll(/this\.t\("([^"\n]+)"/g)].map(m => m[1]).filter(k => !Object.hasOwn(en, k));
  assert.deepEqual([...new Set(missing)], []);
});
