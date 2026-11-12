// P6.5 book view usability: tabs with backend-derived status, work area
// right after the tabs, bulk delete via one confirmation and one backend call.
import test from "node:test";
import assert from "node:assert/strict";

let Studio;
const store = new Map();
globalThis.localStorage = {getItem:k=>store.has(k)?store.get(k):null, setItem:(k,v)=>store.set(k,String(v)), removeItem:k=>store.delete(k)};
globalThis.HTMLElement = class {
  attachShadow() {this.shadowRoot = {addEventListener() {},getElementById() {return null;},querySelectorAll() {return [];},querySelector() {return null;}};}
};
globalThis.customElements = {get() {}, define(name, ctor) {Studio = ctor;}};
await import("../custom_components/hase/frontend/studio.js");

const spell = (id, extra={}) => ({id, name:{de:id}, trigger:{de:`trigger ${id}`}, operation:"light.turn_on", target:{kind:"area_name",capture:"area"}, tags:[], ...extra});
const ok = {state:"ok", errors:0, warnings:0, infos:0};
const warn = n => ({state:"warning", errors:0, warnings:n, infos:0});
const err = n => ({state:"error", errors:n, warnings:0, infos:0});

function studio(commands=[spell("a"), spell("b", {enabled:false}), spell("c")]) {
  const ui = new Studio();
  ui.language="de"; ui.bookLanguage="de";
  ui.info = {books:[], actions:[], operations:[]};
  ui.current = {default_language:"de", languages:["de"], book:{id:"book1", name:"B", default_language:"de", languages:["de"], commands}, profiles:[], spell_capabilities:{}, collision_report:{findings:[], count:0, spells:{}, status:ok}};
  ui.registry = {areas:[], entities:[]};
  ui.render = () => {};
  return ui;
}
const tabStatus = (html, id) => (html.match(new RegExp(`<button role="tab" id="book-tab-${id}"[^>]*>.*?</button>`, "s"))||[""])[0];

test("1 default tab is Buch & Sprache and the language panel is shown", () => {
  store.clear();
  const ui = studio();
  ui.bookTab = ui.loadBookTab("book1");
  const html = ui.bookView();
  assert.equal(ui.bookTab, "book");
  assert.match(tabStatus(html, "book"), /aria-selected="true"/);
  assert.ok(html.includes('class="book-languages"'));
  for (const id of ["book", "check", "mcp", "matching"]) assert.ok(html.includes(`data-do="book-tab" data-id="${id}"`), id);
});

test("2 tab switch renders the chosen panel and 10 remembers it per book", async () => {
  store.clear();
  const ui = studio();
  await ui.act("book-tab", "matching");
  assert.equal(ui.bookTab, "matching");
  const html = ui.bookView();
  assert.match(tabStatus(html, "matching"), /aria-selected="true"/);
  assert.ok(html.includes("Matching erklären") && html.includes('id="explain-phrase"'));
  assert.ok(!html.includes('class="book-languages"'));
  assert.equal(ui.loadBookTab("book1"), "matching");
  assert.equal(ui.loadBookTab("other-book"), "book");
  await ui.act("book-tab", "bogus");
  assert.equal(ui.bookTab, "matching");
});

test("3 check tab shows not-checked, ok, warning and error state from report.status", () => {
  const ui = studio();
  assert.match(tabStatus(ui.bookView(), "check"), /data-tab-status="unknown"/);
  ui.report = {spells:[], status:ok, errors:0, schema_errors:0, safety_errors:0, warnings:0};
  assert.match(tabStatus(ui.bookView(), "check"), /tab-ok.*✓/s);
  ui.report = {spells:[], status:warn(2), errors:0, warnings:2};
  assert.match(tabStatus(ui.bookView(), "check"), /tab-warning.*⚠ 2/s);
  ui.report = {spells:[], status:err(3), errors:3, schema_errors:1, safety_errors:2, warnings:0};
  const html = ui.bookView();
  assert.match(tabStatus(html, "check"), /tab-error.*⛔ 3/s);
  ui.bookTab = "check";
  const panel = ui.bookView();
  assert.ok(panel.includes("Schemafehler") && panel.includes("Sicherheitskonflikte"));
});

test("4 MCP tab status comes from mcpReport.status", () => {
  const ui = studio();
  ui.mcpReport = {spells:[], projectable:10, not_projectable:0, requires_unrestricted:32, status:warn(32)};
  assert.match(tabStatus(ui.bookView(), "mcp"), /tab-warning.*⚠ 32/s);
  ui.mcpReport = {...ui.mcpReport, requires_unrestricted:0, status:ok};
  assert.match(tabStatus(ui.bookView(), "mcp"), /tab-ok.*✓/s);
});

test("5-7 matching tab: ✓, ⚠ n for warnings, ⛔ n for safety ambiguity", () => {
  const ui = studio();
  assert.match(tabStatus(ui.bookView(), "matching"), /tab-ok.*✓/s);
  ui.current.collision_report.status = warn(2);
  assert.match(tabStatus(ui.bookView(), "matching"), /tab-warning.*⚠ 2/s);
  ui.current.collision_report.status = {state:"error", errors:1, warnings:4, infos:0};
  const html = tabStatus(ui.bookView(), "matching");
  assert.match(html, /tab-error/);
  assert.match(html, /⛔ 1/);
});

test("tab status is not computed from findings in the frontend", () => {
  const ui = studio();
  // findings claim a safety error, backend status says ok: the tab follows the backend.
  ui.current.collision_report = {findings:[{kind:"safety_ambiguity", severity:"error", spell_ids:["a","c"]}], count:1, spells:{}, status:ok};
  assert.match(tabStatus(ui.bookView(), "matching"), /tab-ok/);
});

test("8-9 matching explain is reachable from the tab and the detail dialog stays complete", async () => {
  const ui = studio();
  ui.bookTab = "matching";
  assert.ok(ui.bookView().includes('data-do="explain-book"'));
  ui.shadowRoot.getElementById = id => id === "explain-phrase" ? {value:"trigger a Gondor"} : null;
  const explain = {decision:"MATCH", reason:"single_candidate", selected:null, ambiguity:null, via_source_area_fallback:false,
    candidates:[{spell_id:"a", trigger:"trigger {area}", is_alias:false, specificity:1, score:1, is_exact:true, captures:{area:"Gondor"}, disposition:"selected", reason:"single_candidate", actions:[{role:"user",operation:"light.turn_on",outcome:"on",target:{kind:"area_name",value:"Gondor"}}], engine:null, debug:{}}]};
  ui.api = async (op, data) => {assert.equal(op, "explain"); return {match_explain:explain};};
  await ui.act("explain-book");
  const html = ui.resultView(true);
  for (const text of ["MATCH", "SINGLE_CANDIDATE", "Spezifität", "Score", "Exakt", "area = <strong>Gondor</strong>", "light.turn_on → EIN"]) assert.ok(html.includes(text), text);
});

test("work area (toolbar, filters, list) follows the tab container directly", () => {
  const ui = studio();
  const html = ui.bookView();
  const tabsEnd = html.indexOf('class="tab-panel"');
  const toolbar = html.indexOf('data-do="new-spell"');
  const filters = html.indexOf('id="filter-search"');
  const list = html.indexOf('class="spell-list"');
  assert.ok(tabsEnd > 0 && tabsEnd < toolbar && toolbar < filters && filters < list);
  // diagnostic sections are no longer between toolbar and list
  const between = html.slice(toolbar, list);
  for (const heading of ["Prüfergebnis", "MCP-Prüfergebnis", "Matching-Diagnose</h2>"]) assert.ok(!between.includes(heading), heading);
});

test("11-13 bulk delete disabled without selection, shows count with selection", () => {
  const ui = studio();
  assert.match(ui.bookView(), /data-do="delete-selected" disabled/);
  ui.selected.add("a"); ui.selected.add("c");
  const html = ui.bookView();
  assert.ok(html.includes("Auswahl löschen (2)"));
  assert.doesNotMatch(html, /data-do="delete-selected" disabled/);
});

test("14 cancel changes nothing and keeps the selection", async () => {
  const ui = studio();
  ui.selected.add("a"); ui.selected.add("c");
  let prompt;
  ui.askConfirmation = async text => {prompt = text; return false;};
  ui.api = async () => assert.fail("no backend call on cancel");
  await ui.act("delete-selected");
  assert.equal(prompt, "2 ausgewählte Spells wirklich löschen?");
  assert.deepEqual([...ui.selected], ["a", "c"]);
  assert.equal(ui.current.book.commands.length, 3);
});

test("15-17,19 confirm deletes exactly the selection in one call, clears it, refreshes and keeps the new report", async () => {
  const ui = studio();
  ui.selected.add("c"); ui.selected.add("a");
  ui.askConfirmation = async () => true;
  const calls = [];
  const report = {spells:[], status:ok, errors:0, warnings:0};
  ui.api = async (op, data) => {calls.push([op, data]); return {revision:"r2", report, deleted:data.spell_ids};};
  let refreshed = 0;
  ui.refresh = async () => {refreshed++; ui.current.book.commands = ui.current.book.commands.filter(c=>!["a","c"].includes(c.id)); ui.report = null;};
  await ui.act("delete-selected");
  assert.deepEqual(calls, [["delete_spells", {spell_ids:["a","c"], confirmed:true}]]);
  assert.equal(ui.selected.size, 0);
  assert.equal(refreshed, 1);
  assert.equal(ui.report, report);
  assert.deepEqual(ui.current.book.commands.map(c=>c.id), ["b"]);
});

test("18 backend failure: reload real state, keep selection, surface the error", async () => {
  const ui = studio();
  ui.selected.add("a");
  ui.askConfirmation = async () => true;
  ui.api = async () => {throw new Error("Mindestens ein ausgewählter Spell existiert nicht mehr. Es wurde nichts gelöscht.");};
  let refreshed = 0;
  ui.refresh = async () => {refreshed++;};
  await assert.rejects(ui.act("delete-selected"), /nichts gelöscht/);
  assert.equal(refreshed, 1);
  assert.deepEqual([...ui.selected], ["a"]);
  assert.equal(ui.current.book.commands.length, 3);
});

test("selection of ids no longer in the book is not sent", async () => {
  const ui = studio();
  ui.selected.add("ghost");
  ui.askConfirmation = async () => assert.fail("nothing to confirm");
  ui.api = async () => assert.fail("nothing to delete");
  await ui.act("delete-selected");
});

test("check result arriving after a book switch is dropped (no wrong tab/report)", async () => {
  store.clear();
  const ui = studio();
  let release;
  ui.api = () => new Promise(r => {release = r;});
  const pending = ui.act("check");
  ui.current = {...ui.current, book:{...ui.current.book, id:"book2"}};
  release({spells:[], status:ok});
  await pending;
  assert.equal(ui.report, undefined);
  assert.equal(ui.loadBookTab("book1"), "book");
  assert.equal(ui.loadBookTab("book2"), "book");
});

test("check result for the same book switches to the check tab and stores it", async () => {
  store.clear();
  const ui = studio();
  ui.api = async () => ({spells:[], status:ok});
  await ui.act("check");
  assert.equal(ui.bookTab, "check");
  assert.equal(ui.loadBookTab("book1"), "check");
});

const REPORT = {status:warn(3), errors:1, schema_errors:1, safety_errors:1, warnings:3, user_executable:1, expert_executable:2,
  executable_ids:{user:["a"], expert:["a","c"]},
  spells:[{id:"a", errors:[], warnings:["Dynamischer Trigger: für vollständige Prüfung eigene Testphrase eingeben."]},
          {id:"b", errors:["user: Aktion, Ziel oder Parameter ungültig."], warnings:[]},
          {id:"c", errors:[], warnings:["Doppelter Trigger mit a.", "Reine Textausgabe: keine typisierte HASE-Aktion; Ausführung hängt vom Client ab."]}],
  validation_issues:[{code:"safety_ambiguity", severity:"error", spell_id:"c", related_spell_ids:["a"], message:"Dieser Spell ist statisch sicherheitsmehrdeutig mit „a“."}]};

test("check tiles are buttons that open a detail dialog listing backend data", async () => {
  const ui = studio();
  ui.report = REPORT; ui.bookTab = "check";
  const html = ui.bookView();
  for (const id of ["user", "expert", "schema", "safety", "warnings"]) assert.ok(html.includes(`data-do="report-detail" data-id="${id}"`), id);
  await ui.act("report-detail", "warnings");
  let modal = ui.modalView();
  assert.ok(modal.includes("Hinweise (3)"));
  assert.ok(modal.includes("Doppelter Trigger mit a.") && modal.includes("Dynamischer Trigger"));
  assert.ok(!modal.includes("sicherheitsmehrdeutig"));
  ui.closeModal();
  assert.equal(ui.reportDetail, null);
  await ui.act("report-detail", "expert");
  modal = ui.modalView();
  assert.ok(modal.includes("Expert / Fallback ausführbar (2)"));
  assert.ok(modal.includes('data-do="report-open" data-id="c"') && !modal.includes('data-do="report-open" data-id="b"'));
  await ui.act("report-detail", "safety");
  assert.ok(ui.modalView().includes("sicherheitsmehrdeutig"));
  await ui.act("report-detail", "schema");
  assert.ok(ui.modalView().includes("Aktion, Ziel oder Parameter ungültig"));
  await ui.act("report-detail", "bogus");
  assert.equal(ui.reportDetail, "schema");
});

test("opening a spell from the detail dialog closes it and opens the editor", async () => {
  const ui = studio();
  ui.report = REPORT;
  await ui.act("report-detail", "warnings");
  const opened = [];
  const original = ui.act.bind(ui);
  ui.act = async (action, id) => { if (action === "edit") { opened.push(id); return; } return original(action, id); };
  await original("report-open", "c");
  assert.equal(ui.reportDetail, null);
  assert.deepEqual(opened, ["c"]);
});
