import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import vm from "node:vm";
import {studioLanguage, translate, translateMessage, localizedContent, de, en} from "../custom_components/hase/frontend/studio-i18n.js";
import {bookCover, bookTheme, safeCoverPath, filterBooks} from "../custom_components/hase/frontend/studio-visuals.js";

let Studio;
globalThis.HTMLElement = class {
  attachShadow() {this.shadowRoot = {addEventListener() {},getElementById() {return null;},querySelectorAll() {return [];},querySelector() {return null;}};}
};
globalThis.customElements = {get() {}, define(name, ctor) {Studio = ctor;}};
await import("../custom_components/hase/frontend/studio.js");
const spell = {id:"lumos", name:"Lumos", trigger:"Lumos", operation:"light.set_power", arguments:{on:true}, target:{kind:"source_area"}, tags:[]};
function studio() {
  const ui = new Studio();
  ui.language="de";ui.bookLanguage="de";
  ui.info = {books:[{id:"testbook",name:"Test Book",count:1,active_clients:2,capability:"USER + EXPERT",expert_allowed:false}],actions:[{id:"light-on",label:"Licht einschalten",operation:"light.set_power",arguments:{on:true},targets:["source_area"]}]};
  ui.current = {default_language:"de",languages:["de","en"],book:{id:"testbook",name:"Test Book",default_language:"de",languages:["de","en"],commands:[spell]}, profiles:[{id:"client",name:"Client"}],capability:"USER + EXPERT",spell_capabilities:{lumos:"USER"}};
  ui.registry = {areas:[],entities:[]};
  ui.profile = "client";
  ui.render = () => {};
  return ui;
}

test("covers reject external, script and malformed URLs", () => {
  for (const path of ["javascript:alert(1)","https://external.test/a.png","//external.test/a.png","/\\external.test/a.png","/\n/evil"]) assert.equal(safeCoverPath(path),null);
  assert.equal(safeCoverPath("/local/cover.png"),"/local/cover.png");
  assert.ok(!bookCover({id:"x",name:'"><script>',metadata:{visual:{cover:"//external.test"}}}).includes("<script>"));
});
test("books retain distinct optional visual identities", () => {
  assert.equal(bookTheme({id:"middle_earth"}),"emerald");
  assert.equal(bookTheme({id:"testbook",metadata:{visual:{theme:"sapphire"}}}),"sapphire");
  assert.equal(filterBooks([{id:"hdr",name:"Middle Earth"},{id:"testbook",name:"Test Book"}],"EARTH").length,1);
});
test("overview keeps count, clients, permissions and original book action", () => {
  const html=studio().homeView();
  for (const text of ["1 Spells","2 zugeordnete Clients","USER + EXPERT","AUS",'data-id="testbook"','data-do="new-book"','data-do="import-new"','data-do="check-all"','data-do="refresh"']) assert.ok(html.includes(text),text);
});
test("disabled books are hidden by default and can be shown and reactivated", async () => {
  const ui=studio();
  ui.info.books.push({id:"hidden",name:"Hidden Book",count:1,disabled:true,capability:"USER"});
  assert.ok(!ui.homeView().includes("Hidden Book"));
  ui.showDisabledBooks=true;
  assert.ok(ui.homeView().includes("Hidden Book"));
  assert.ok(ui.homeView().includes("Deaktiviert"));
  ui.current.book.disabled=true;
  assert.ok(ui.bookHeroView().includes('data-do="toggle-book"'));
  let request;
  ui.api=async(operation,data)=>{request={operation,data};};
  ui.refresh=async()=>{};
  await ui.act("toggle-book");
  assert.deepEqual(request,{operation:"save_metadata",data:{metadata:{disabled:false}}});
});
test("execution diagnostics show only the bounded backend summary", async () => {
  const ui=studio();
  ui.api=async operation=>{assert.equal(operation,"diagnostics");return {executions:[{
    timestamp:"2026-10-07T12:00:00Z",book_id:"testbook",spell_id:"lumos",
    channel:"studio_dry_run",status:"SUCCESS",reason_code:"dry_run",duration_ms:8,
  }]};};
  await ui.act("diagnostics");
  assert.equal(ui.diagnosticsOpen,true);
  const html=ui.executionDiagnosticsView();
  assert.ok(html.includes("lumos"));
  assert.ok(html.includes("SUCCESS"));
  assert.ok(!html.includes("arguments"));
});
test("invalid dropped import is surfaced without leaving an unhandled rejection", async () => {
  const original=HTMLElement.prototype.attachShadow;
  const listeners={};
  HTMLElement.prototype.attachShadow=function(){this.shadowRoot={
    addEventListener:(type,callback)=>{listeners[type]=callback;},
    getElementById:()=>null,querySelectorAll:()=>[],querySelector:()=>null,
  };};
  try {
    const ui=new Studio();ui.importing=true;
    const failure=new Error("too large");
    ui.readFile=async()=>{throw failure;};
    let shown;
    ui.fail=err=>{shown=err;};
    await listeners.drop({preventDefault(){},dataTransfer:{files:[{name:"large.yaml"}]}});
    assert.equal(shown,failure);
  } finally {HTMLElement.prototype.attachShadow=original;}
});
test("detail keeps selectors, all filters and editor/import/export actions", () => {
  const html=studio().bookView();
  for(const id of ["profile","source","filter-search","filter-tag","filter-action","filter-cap","filter-enabled","filter-valid"]) assert.ok(html.includes(`id="${id}"`),id);
  for(const action of ["new-spell","metadata","check","import","export","export-selected","edit","toggle"]) assert.ok(html.includes(`data-do="${action}"`),action);
});
test("expert presentation mode never grants book execution", async () => {
  const ui=studio();
  ui.askConfirmation=async()=>true;
  ui.api=()=>assert.fail("Opening Expert Mode must not write permissions");
  await ui.act("expert");
  assert.equal(ui.expert,true);
  assert.equal(ui.current.expert_allowed,undefined);
  assert.ok(ui.bookView().includes('data-do="permission"'));
});
test("requests preserve selected book, revision, client and source", async () => {
  const ui=studio();ui.current.revision="r1";ui.source="living";
  let request;
  ui._hass={callWS:async data=>{request=data;}};
  await ui.api("validate");
  assert.deepEqual(request.data,{book_id:"testbook",revision:"r1",profile_id:"client",source_area_id:"living",language:"de",book_language:"de"});
});
test("sidebar changes use a separate UI operation", async () => {
  const ui=studio();let request;
  ui.api=async(operation,data)=>{request={operation,data};return data;};
  await ui.change({target:{id:"show-sidebar",checked:true}});
  assert.deepEqual(request,{operation:"ui_settings",data:{show_in_sidebar:true}});
  assert.equal(ui.info.show_in_sidebar,true);
});
test("bundled HASE logo is identical to official branding source", async () => {
  const hash=async path=>createHash("sha256").update(await readFile(new URL(path,import.meta.url))).digest("hex");
  assert.equal(await hash("../docs/branding/hase-icon.png"),await hash("../custom_components/hase/brand/icon.png"));
});

test("Integration toolbar has one Studio link even without a Codie", async () => {
  const queued=[];
  const rows=["hase","other"].map(domain=>{
    const row={localName:"ha-config-integration-page",isConnected:true,domain,configEntries:[]};
    const root={host:row,links:[],querySelectorAll:()=>[],querySelector(selector){
      if(selector===".actions")return {append:link=>this.links.push(link)};
      return this.links[0];
    }};
    row.shadowRoot=root;
    return row;
  });
  const document={querySelectorAll:()=>rows,createTextNode:text=>text,createElement:tag=>({tag,dataset:{},style:{},setAttribute(){},append(...items){this.children=items;}})};
  const scope={document,window:{location:{pathname:"/config/integrations/integration/hase"},addEventListener(){}},MutationObserver:class{observe(){} disconnect(){}},queueMicrotask:fn=>queued.push(fn),customElements:{whenDefined:()=>new Promise(()=>{})}};
  vm.runInNewContext(await readFile(new URL("../custom_components/hase/frontend/assist-entry.js",import.meta.url),"utf8"),scope);
  queued.shift()();
  assert.equal(rows[0].shadowRoot.links.length,1);
  assert.equal(rows[1].shadowRoot.links.length,0);
  assert.equal(rows[0].shadowRoot.links[0].href,"/hase");
  assert.equal(rows[0].shadowRoot.links[0].children[1],"Spellbook Studio");
});

test("HA user locale wins; regional and unavailable languages fall back correctly", () => {
  assert.equal(studioLanguage({locale:{language:"en"},language:"de"}),"en");
  assert.equal(studioLanguage({locale:{language:"de-CH"}}),"de");
  assert.equal(studioLanguage({language:"en_GB"}),"en");
  assert.equal(studioLanguage({locale:{language:"fr"}}),"en");
  assert.equal(studioLanguage(),"en");
  const saved=de["Buchdetails"];
  delete de["Buchdetails"];
  try {assert.equal(translate("de","Buchdetails"),"Book details");}
  finally {de["Buchdetails"]=saved;}
});

test("language changes rerender without reloading data or losing form state", () => {
  const ui=studio();ui.loaded=true;const renders=[];
  ui.render=preserve=>renders.push(preserve);
  ui.api=()=>assert.fail("Changing language must not reload or save a book");
  ui.hass={locale:{language:"en"}};
  assert.equal(ui.language,"en");
  assert.deepEqual(renders,[true]);
  ui.hass={locale:{language:"en"}};
  assert.deepEqual(renders,[true]);
  ui.hass={locale:{language:"de"}};
  assert.deepEqual(renders,[true,true]);
});

test("English covers overview, details, editor, import, diagnostics and confirmations", () => {
  const ui=studio();ui.language="en";ui.expert=true;ui.edit={...spell,steps:ui.normalizeSteps(spell)};ui.editorMode="wizard";ui.editExisting=true;ui.info.operations=["light.set_power"];
  ui.wizardStep=4;ui.wizardMaxStep=4;
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  const html=[ui.homeView(),ui.bookView(),ui.listView(),ui.stepsWizardView(),ui.metadataView(),ui.importView(),ui.newImportView()].join("\n");
  for(const label of ["Create spellbook","assigned clients","Test with client","Save","When I say","Import into Test Book","Turn on lights"]) assert.ok(html.includes(label),label);
  for(const label of ["Spellbook erstellen","Expert-Ausführung:","Nicht angegeben","Licht einschalten","Speichern","Wenn ich sage"]) assert.ok(!html.includes(label),label);
  assert.equal(translateMessage("en","Doppelter Trigger mit nox."),"Duplicate trigger with nox.");
  assert.equal(translateMessage("en","Ein Buchname ist erforderlich."),"A book name is required.");
  assert.equal(translateMessage("en","Die Aktion „Licht dimmen“ benötigt {value} in jeder Trigger-Formulierung."),"The action “Dim lights” requires {value} in every trigger phrase.");
  assert.equal(translate("en","Expert-Ausführung für „{name}“ {state}?",{name:"Potter",state:"Enable"}),"Enable Expert execution for “Potter”?");
  assert.equal(localizedContent({de:"Licht",en:"Light"},"en"),"Light");
  assert.equal(localizedContent({de:"Licht",en:"Light"},"fr"),"Light");
});

test("editing an English trigger sends the German trigger untouched for the backend to merge", async () => {
  const ui=studio();ui.editExisting=true;ui.editLanguage="en";ui.bookLanguage="en";
  ui.edit={...spell,trigger:{de:"Licht an",en:"Lights on"},steps:ui.normalizeSteps(spell)};
  const fields={"spell-name":"Lumos",tags:"",action:"light-on",triggers:"Illuminate","step-target-kind":"source_area"};
  ui.value=id=>fields[id] || "";
  ui.shadowRoot.querySelectorAll=()=>[];ui.shadowRoot.getElementById=()=>null;
  const payload=(await ui.collectSpell()).spell;
  // The merge itself (translations.trigger + fresh triggers/trigger_language
  // -> the final per-language trigger dict) is now wizard_spell()'s job -
  // see test_studio.py's own edit_language coverage.
  assert.deepEqual(payload.translations.trigger,{de:"Licht an",en:"Lights on"});
  assert.deepEqual(payload.triggers,["Illuminate"]);
  assert.equal(payload.trigger_language,"en");
});

test("every interface message has an English catalogue entry", async () => {
  const source=await readFile(new URL("../custom_components/hase/frontend/studio.js",import.meta.url),"utf8");
  for(const match of source.matchAll(/this\.t\("([^"\n]+)"/g)) assert.ok(Object.hasOwn(en,match[1]),match[1]);
});


test("book fallback order is deterministic and independent of UI locale", async () => {
  const {resolveBookValue:resolve,setBookValue,bookFallbacks}=await import("../custom_components/hase/frontend/studio-book-languages.js");
  assert.deepEqual(resolve({de:"DE",en:"EN"},"de-CH","fr"),{value:"DE",used:"de",reason:"requested"});
  assert.equal(resolve({de:"DE",en:"EN"},"fr","de").used,"en");
  assert.equal(resolve({de:"DE",fr:"FR"},"it","fr").used,"fr");
  assert.equal(resolve({de:"DE"},"it","en").reason,"only");
  assert.deepEqual(resolve({de:"DE",fr:"FR"},"it","en","id"),{value:"id",used:null,reason:"technical"});
  assert.deepEqual(setBookValue("DE","fr","FR","de"),{de:"DE",fr:"FR"});
  assert.deepEqual(setBookValue({de:["eins"],en:["one"]},"fr",["un"],"de"),{de:["eins"],en:["one"],fr:["un"]});
  assert.equal(bookFallbacks({id:"x",commands:[{id:"s",trigger:{en:"Light"}}]},"de","en")[0].used,"en");
});
test("automatic PNG cover replaces placeholder; manual cover metadata is ignored", () => {
  assert.ok(bookCover({id:"x",cover:"data:image/png;base64,iVBORw0KGgo="}).includes('<img src="data:image/png;base64,'));
  assert.ok(!bookCover({id:"x",metadata:{visual:{cover:"/local/old.png"}}}).includes("<img"));
});
test("switching book language preserves drafts and shared controls", async () => {
  const ui=studio();ui.edit=structuredClone(spell);ui.edit.trigger={de:"Alt",en:"Old"};ui.edit.steps=ui.normalizeSteps(spell);
  const fields={"spell-name":"Name DE",triggers:"Neu\nAlias","spell-description":"Beschreibung",tags:"tag"};
  ui.value=id=>fields[id]||"";
  ui.shadowRoot.querySelectorAll=()=>[];ui.shadowRoot.getElementById=()=>null;
  await ui.act("book-language","en");
  assert.deepEqual(ui.edit.trigger,{de:"Neu",en:"Old"});
  assert.deepEqual(ui.edit.triggers,{de:["Alias"]});
  fields.triggers="New";fields["spell-name"]="Name EN";
  const payload=await ui.collectSpell();
  assert.equal(payload.spell.trigger_language,"en");
  assert.equal(payload.spell.translations.trigger.de,"Neu");
  assert.equal(payload.spell.translations.description.de,"Beschreibung");
  assert.equal(ui.language,"de");
});
test("delete/reset is confirmation-gated and scoped to the selected book", async () => {
  const ui=studio();let calls=[];ui.api=async(op,data)=>{calls.push([op,data]);return {restored:false};};ui.refresh=async()=>{};
  ui.askConfirmation=async()=>false;await ui.act("delete-book");assert.equal(calls.length,0);
  ui.askConfirmation=async()=>true;await ui.act("delete-book");assert.deepEqual(calls,[["delete_book",{confirmed:true}]]);
});
test("book origin selects reset, delete or no destructive action", () => {
  const ui=studio();ui.current.origin="builtin";
  assert.ok(!ui.languageView().includes('data-do="delete-book"'));
  ui.current.can_delete=true;assert.ok(ui.languageView().includes('data-do="delete-book"'));
  ui.current.can_reset=true;assert.ok(ui.languageView().includes('data-do="reset-book"'));
  assert.ok(!ui.languageView().includes('data-do="delete-book"'));
});


test("placeholder phrases are rejected before preview, execution confirmation or requests", async () => {
  const ui=studio();ui.api=()=>assert.fail("No request for an invalid phrase");
  ui.askConfirmation=()=>assert.fail("No execution confirmation for an invalid phrase");
  ui.collectSpell=()=>assert.fail("Validate the phrase before building the execution draft");
  for(const phrase of ["lumos {area}","lumos {area]","lumos area}","lumos [area]",""]) {
    ui.value=()=>phrase;
    for(const action of ["test-edit","execute"])await assert.rejects(ui.act(action),/konkrete Testphrase/);
  }
  ui.value=()=>"lumos Gondor";assert.equal(ui.testPhrase(),"lumos Gondor");
});
test("test-edit dry-runs the current unified draft with a concrete phrase", async () => {
  const ui=studio();ui.editExisting=true;ui.edit={...spell,trigger:"lumos {area}",steps:ui.normalizeSteps(spell)};
  const fields={"spell-name":"Lumos",triggers:"lumos {area}",phrase:"lumos Gondor","step-target-kind":"source_area"};
  ui.value=id=>fields[id]||"";ui.shadowRoot.querySelectorAll=()=>[];ui.shadowRoot.getElementById=()=>null;
  let sent;ui.api=async(operation,data)=>{sent={operation,data};return {};};
  await ui.act("test-edit");
  assert.equal(sent.operation,"dry_run");
  assert.equal(sent.data.mode,"wizard");
  assert.equal(sent.data.phrase,"lumos Gondor");
});


test("placeholder chips appear in both new and existing spell editors", () => {
  const ui=studio();ui.edit={...structuredClone(spell),steps:ui.normalizeSteps(spell)};ui.editorMode="wizard";ui.wizardStep=2;ui.wizardMaxStep=2;
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  for(const existing of [false,true]){ui.editExisting=existing;const html=ui.stepsWizardView();assert.ok(html.includes('data-do="insert-placeholder"'));assert.ok(html.includes('data-id="area"'));assert.ok(html.includes('data-id="value"'));assert.ok(html.includes('data-id="entity"'));}
  ui.edit.expert={target:{capture:"entity"},arguments:{level:{capture:"level"}}};
  assert.deepEqual(ui.placeholderNames(),["area","entity","value","target","level"]);
});
test("chips insert at the caret, replace selections and preserve other editor fields", async () => {
  const ui=studio();ui.edit=structuredClone(spell);ui.render=()=>assert.fail("Insertion must not rebuild the form");
  let focused=false,events=0;
  const field={value:"",selectionStart:0,selectionEnd:0,focus(){focused=true;},dispatchEvent(){events++;},setRangeText(text,start,end){this.value=this.value.slice(0,start)+text+this.value.slice(end);this.selectionStart=this.selectionEnd=start+text.length;}};
  ui.shadowRoot.getElementById=id=>id==="triggers"?field:null;
  for(const [value,start,end,expected] of [["lumos",5,5,"lumos {area}"],["lumos Gondor",6,12,"lumos {area}"],["lumos jetzt",6,6,"lumos {area} jetzt"],["lumos ",6,6,"lumos {area}"],["nox\nlumos",9,9,"nox\nlumos {area}"],["",0,0,"{area}"]]){
    Object.assign(field,{value,selectionStart:start,selectionEnd:end});await ui.act("insert-placeholder","area");assert.equal(field.value,expected);assert.equal(field.selectionStart,field.selectionEnd);
  }
  assert.equal(focused,true);assert.equal(events,6);assert.equal(ui.edit.trigger,"Lumos");
});


test("response chips only offer captures shared by current trigger alternatives", () => {
  const ui=studio();ui.edit={...spell,trigger:"lumos {area}",triggers:["licht {area}"]};
  assert.deepEqual(ui.responsePlaceholderNames(),["area"]);
  for(const field of ["spell-output","spell-catchphrase","expert-catchphrase"]){const html=ui.placeholderChips(field);assert.ok(html.includes(`data-id="${field}:area"`));assert.ok(!html.includes(`data-id="${field}:value"`));}
  ui.shadowRoot.getElementById=id=>id==="triggers"?{value:"dimme {area} auf {value}\nhelligkeit {value}"}:null;
  assert.deepEqual(ui.responsePlaceholderNames(),["value"]);
});
test("response chip replaces selected text in its own field and returns the caret", async () => {
  const ui=studio();ui.edit=structuredClone(spell);let focused=false;
  const trigger={value:"lumos {area}"};
  const reply={value:"Licht in Gondor an",selectionStart:9,selectionEnd:15,focus(){focused=true;},dispatchEvent(){},setRangeText(text,start,end){this.value=this.value.slice(0,start)+text+this.value.slice(end);this.selectionStart=this.selectionEnd=start+text.length;}};
  ui.shadowRoot.getElementById=id=>id==="triggers"?trigger:id==="spell-catchphrase"?reply:null;
  await ui.act("insert-placeholder","spell-catchphrase:area");
  assert.equal(reply.value,"Licht in {area} an");assert.equal(reply.selectionStart,15);assert.equal(focused,true);assert.equal(trigger.value,"lumos {area}");
});
test("spell search filters live and survives a Studio reload", async () => {
  const previous=globalThis.localStorage;
  const storage=new Map([["hase-studio-spell-search","Lumos"]]);
  globalThis.localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)};
  try {
    const restored=studio();
    assert.equal(restored.filters.search,"Lumos");
    restored.updateSpellSearch({value:"Nox",selectionStart:3});
    await new Promise(resolve=>queueMicrotask(resolve));
    assert.equal(restored.filters.search,"Nox");
    assert.equal(storage.get("hase-studio-spell-search"),"Nox");
    assert.equal(studio().filters.search,"Nox");
  } finally {
    if(previous===undefined)delete globalThis.localStorage;else globalThis.localStorage=previous;
  }
});

test("New spell top-level choice is Wizard vs Expert, with no second sub-choice screen (item 2)", async () => {
  const ui=studio();ui.ensureEditorTargets=async()=>{};
  await ui.act("new-spell");
  assert.equal(ui.edit,undefined);assert.equal(ui.newSpellChoice,true);assert.equal(ui.newSpellStep,0);
  const choice=ui.newSpellChoiceView();
  assert.ok(choice.includes('data-do="new-spell-wizard"'));
  assert.ok(choice.includes('data-do="new-spell-expert"'));
  assert.ok(!choice.includes('data-do="new-spell-wizard-menu"'),"the old single/combined sub-choice screen no longer exists");
  assert.ok(!choice.includes('data-do="new-spell-steps"'));
});

test("New spell Wizard starts with exactly one step; New spell Expert starts in the same editor with Expert affordances", async () => {
  const ui=studio();ui.ensureEditorTargets=async()=>{};
  await ui.act("new-spell");
  await ui.act("new-spell-wizard");
  assert.equal(ui.editorMode,"wizard");
  assert.equal(ui.edit.steps.length,1);
  assert.equal(ui.edit.steps[0].kind,"action");
  assert.equal(ui.editorDirty,false);

  await ui.act("new-spell");
  await ui.act("new-spell-expert");
  assert.equal(ui.editorMode,"expert");
  assert.equal(ui.edit.steps.length,1);
});

test("New spell: the language chosen on the top-level screen is used to initialize the new spell", async () => {
  const ui=studio();ui.ensureEditorTargets=async()=>{};
  await ui.act("new-spell");
  ui.value=id=>id==="new-spell-language"?"en":"";
  await ui.act("new-spell-wizard");
  assert.equal(ui.wizardLanguageExplicit,"en");
});

test("Combined Spells: adding an action step opens it for editing and collectSteps() forwards it", async () => {
  const ui=studio();
  ui.edit={id:"night",trigger:"",steps:[]};ui.editorMode="steps";
  ui.ensureAreas=async()=>{};ui.ensureEntitiesForAction=async()=>{};
  ui.info.actions=[{id:"cover.close_cover:0",label:"Schließen",operation:"cover.close_cover",arguments:{},targets:["all_allowed","area_id","entity_id"]}];
  await ui.act("add-step","action");
  assert.equal(ui.editingStepIndex,0);
  assert.equal(ui.edit.steps.length,1);
  assert.equal(ui.edit.steps[0].kind,"action");
  const fields={"step-alias":"shutters","step-action":"cover.close_cover:0","step-target-kind":"all_allowed","triggers":"gute nacht","spell-name":"Gute Nacht"};
  ui.value=id=>fields[id]||"";
  ui.shadowRoot.querySelectorAll=()=>[];
  const payload=await ui.collectSteps();
  assert.equal(payload.steps.length,1);
  assert.deepEqual(payload.steps[0],{alias:"shutters",kind:"action",action:"cover.close_cover:0",target_kind:"all_allowed",target_value:null});
  assert.equal(payload.triggers[0],"gute nacht");
});

test("Combined Spells: adding a query step's data source and collectSteps() forwards it", async () => {
  const ui=studio();
  ui.edit={id:"contacts",trigger:"",steps:[]};ui.editorMode="steps";
  ui.ensureAreas=async()=>{};ui.ensureEntitiesForAction=async()=>{};
  ui.info.query_source_capabilities={kinds:["count","names","states","state","attribute","min","max","avg","sum"],aggregate_kinds:["count","names","states","min","max","avg","sum"],single_target_kinds:["area_id","entity_id","source_area"],aggregate_target_kinds:["area_id","all_allowed","source_area"]};
  await ui.act("add-step","query");
  assert.equal(ui.edit.steps[0].kind,"query");
  assert.equal(ui.edit.steps[0].sources.length,1);
  const fields={"step-alias":"open_contacts","source-0-kind":"count","source-0-target-kind":"all_allowed","source-0-domain-filter":"binary_sensor","source-0-device-class":"","source-0-state-filter":"on","source-0-variable":"count","source-0-unit-variable":"","triggers":"wie viele fenster sind offen"};
  ui.value=id=>fields[id]||"";
  ui.shadowRoot.querySelectorAll=()=>[];
  const payload=await ui.collectSteps();
  assert.equal(payload.steps[0].alias,"open_contacts");
  assert.deepEqual(payload.steps[0].sources[0],{kind:"count",expert:false,target_kind:"all_allowed",target_value:null,capture:undefined,domain_filter:"binary_sensor",device_class:undefined,attribute:undefined,state_filter:"on",variable:"count",unit_variable:undefined});
});

test("Combined Spells: hand-written steps the guided builder cannot represent become editable Expert steps, not read-only", async () => {
  const ui=studio();
  ui.ensureAreas=async()=>{};ui.ensureEntitiesForAction=async()=>{};
  ui.info.actions=[{id:"cover.close_cover:0",label:"Schließen",operation:"cover.close_cover",arguments:{},targets:["all_allowed"]}];
  const raw={steps:[
    {alias:"dim",operation:"light.turn_on",target:{kind:"area_name",capture:"area"},arguments:{brightness_pct:{capture:"pct",type:"int"}}},
    {alias:"shutters",operation:"cover.close_cover",target:{kind:"all_allowed"},arguments:{}},
    {alias:"count",sources:[{kind:"count",target:{kind:"area_name",capture:"area"},variable:"n"}]},
  ]};
  const steps=ui.normalizeSteps(raw);
  assert.deepEqual(steps.map(s=>s.kind==="query"?"query":s.expert?"expert":"guided"),["expert","guided","query"]);
  assert.ok(steps[2].sources[0].expert,"a query step's own source can independently need Expert (area_name capture)");
  ui.editorMode="expert";ui.edit={id:"evening",trigger:"",steps};ui.editingStepIndex=null;
  const card=ui.stepCardView(steps[0],0);
  assert.ok(card.includes("Expert"),"an Expert-only step must be visibly badged");
  // a guided step whose entity is not in the lazily loaded registry shows its id, not "no device"
  assert.ok(ui.stepSummary({kind:"action",action:"cover.close_cover:0",target_kind:"entity_id",target_value:"cover.x"}).includes("cover.x"));
  assert.ok(card.includes('data-do="edit-step"'),"Expert steps are editable, not a dead end - see item 5/9");
  await ui.act("edit-step","0");
  assert.equal(ui.editingStepIndex,0);
  ui.editingStepIndex=null; // closed without touching the (blank, unmocked) form fields
  await ui.act("move-step-down","0");
  ui.value=id=>({triggers:"abend {area} auf {pct}"})[id]||"";
  ui.shadowRoot.querySelectorAll=()=>[];
  // Untouched Expert steps round-trip byte for byte through collectSteps(),
  // reordering included - no "preserve by index" indirection needed anymore.
  assert.deepEqual((await ui.collectSteps()).steps,[
    {alias:"shutters",kind:"action",action:"cover.close_cover:0",target_kind:"all_allowed",target_value:null},
    {alias:"dim",kind:"action",expert:true,operation:"light.turn_on",target:{kind:"area_name",capture:"area"},arguments:{brightness_pct:{capture:"pct",type:"int"}}},
    {alias:"count",kind:"query",sources:[{kind:"count",expert:true,target_kind:"area_name",target_value:null,capture:"area",domain_filter:undefined,device_class:undefined,attribute:undefined,state_filter:undefined,variable:"n",unit_variable:undefined}]},
  ]);
});

test("Combined Spells: dry-run result lists every step with operation, target, status and alias values", () => {
  const ui=studio();
  ui.result={status:"QUERY_ANSWER",reason_code:"partial",preview_only:true,data:{response_text:"1/1",steps:[
    {index:0,alias:"before",kind:"query",status:"ok",source_count:1,values:{on:1}},
    {index:1,alias:"broken",kind:"action",status:"failed",reason_code:"unknown_operation",operation:"no.such_operation",target:{kind:"area_name",value:"Küche"},arguments:{},entity_ids:[],values:{target_count:0}},
    {index:2,alias:"dim",kind:"action",status:"ok",reason_code:"dry_run",operation:"light.turn_on",target:{kind:"area_name",value:"Küche"},arguments:{brightness_pct:40},entity_ids:["light.k"],values:{success_count:1,failed_names:[]}},
  ]}};
  const html=ui.resultView(true);
  for(const text of ["Schritte (3)","ohne Geräteaufruf","before.on = <strong>1</strong>","no.such_operation","unknown_operation","Fehlgeschlagen","light.k","{&quot;brightness_pct&quot;:40}","dim.failed_names = <strong>–</strong>","1 Datenquelle<"]) assert.ok(html.includes(text),text);
});

test("Combined Spells: response chips are grouped by alias with dotted variable names", () => {
  const ui=studio();
  ui.edit={id:"night",trigger:"",steps:[]};
  ui.wizardValidation={spell_variables:[
    {name:"shutters.target_count",source:"step_field",kind:"action",alias:"shutters",field:"target_count",operation:"cover.close_cover"},
    {name:"shutters.success_count",source:"step_field",kind:"action",alias:"shutters",field:"success_count",operation:"cover.close_cover"},
    {name:"open_contacts.count",source:"step_field",kind:"query",alias:"open_contacts",field:"count"},
  ]};
  const html=ui.stepsResponseChips("spell-output");
  assert.ok(html.includes("shutters"));
  assert.ok(html.includes("open_contacts"));
  assert.ok(html.includes('data-id="spell-output:shutters.target_count"'));
  assert.ok(html.includes('data-id="spell-output:open_contacts.count"'));
});

test("Combined Spells: insertPlaceholder accepts a dotted step-field variable", () => {
  const ui=studio();
  ui.edit={id:"night",trigger:"",steps:[]};
  ui.wizardValidation={spell_variables:[{name:"shutters.target_count",source:"step_field",kind:"action",alias:"shutters",field:"target_count"}]};
  let dispatched=false;
  const field={value:"Fertig: ",selectionStart:8,selectionEnd:8,focus(){},setRangeText(token){this.value+=token;},dispatchEvent(){dispatched=true;}};
  ui.shadowRoot.getElementById=id=>id==="spell-output"?field:null;
  ui.insertPlaceholder("spell-output:shutters.target_count");
  assert.ok(dispatched);
  assert.ok(field.value.includes("{shutters.target_count}"));
});

test("Combined Spells: opening an existing steps spell routes to the steps wizard", async () => {
  const ui=studio();
  const stepsSpell={id:"night",name:"Gute Nacht",trigger:"gute nacht",steps:[{alias:"a",operation:"light.turn_off",target:{kind:"all_allowed"},arguments:{}}]};
  ui.current.book.commands.push(stepsSpell);
  ui.current.wizard_blockers={lumos:[],night:[]};
  ui.ensureEditorTargets=async()=>{};
  await ui.act("edit","night");
  assert.equal(ui.editorMode,"wizard","no blockers reported for this spell -> it opens in the Wizard, per item 14");
  assert.equal(ui.edit.steps.length,1);
});

test("item 14: editor routing on open is decided by the backend's wizard_blockers verdict, never Array.isArray(steps)", async () => {
  const ui=studio();
  const blocked={id:"jinja",name:"Jinja",trigger:"mach das",steps:[{alias:"a",operation:"light.turn_on",target:{kind:"template",value:"{{ [] }}"},arguments:{}}]};
  ui.current.book.commands.push(blocked);
  ui.current.wizard_blockers={lumos:[],jinja:["Schritt 1: Ziel-Art „template“ (Jinja) wird vom Wizard nicht unterstützt."]};
  ui.ensureEditorTargets=async()=>{};
  await ui.act("edit","jinja");
  assert.equal(ui.editorMode,"expert","a reported blocker forces Expert regardless of the spell's own steps/flat shape");
});

test("Combined Spells: reopening a saved spell converts raw stored steps into the editor's internal shape", async () => {
  const ui=studio();
  const stepsSpell={id:"night",name:"Gute Nacht",trigger:"gute nacht",steps:[
    {alias:"a",operation:"light.set_power",target:{kind:"area_id",value:"wohnzimmer"},arguments:{on:true}},
    {alias:"b",sources:[{kind:"count",target:{kind:"area_id",value:"wohnzimmer"},variable:"count",domain_filter:"light",state_filter:"on"}]},
  ]};
  ui.current.book.commands.push(stepsSpell);
  ui.current.wizard_blockers={lumos:[],night:[]};
  ui.ensureEditorTargets=async()=>{};
  await ui.act("edit","night");
  assert.equal(ui.editorMode,"wizard");
  assert.deepEqual(ui.edit.steps[0],{alias:"a",kind:"action",expert:false,action:"light-on",target_kind:"area_id",target_value:"wohnzimmer",operation:"light.set_power",target:{kind:"area_id",value:"wohnzimmer"},arguments:{on:true}});
  assert.deepEqual(ui.edit.steps[1],{alias:"b",kind:"query",sources:[{kind:"count",expert:false,target_kind:"area_id",target_value:"wohnzimmer",capture:"",domain_filter:"light",device_class:"",attribute:"",state_filter:"on",variable:"count",unit_variable:""}]});
  const html=ui.stepListView();
  assert.ok(html.includes("Licht einschalten"),"reopened action step must show its real action label, not a blank one");
  assert.equal(ui.wizardMaxStep,4,"reopening an existing spell must unlock direct navigation to every step, not just Next");
});

test("Combined Spells: the review step offers real device execution, but only for an already-saved spell", () => {
  const ui=studio();
  ui.edit={id:"night",trigger:"gute nacht",steps:[]};
  ui.editorMode="steps";ui.wizardStep=4;
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  ui.editExisting=false;
  assert.ok(!ui.stepsWizardView().includes('data-do="execute"'),"a not-yet-saved spell must not offer real execution");
  ui.editExisting=true;
  const html=ui.stepsWizardView();
  assert.ok(html.includes('data-do="execute"'),"an already-saved Combined Spell must offer real execution, not just the dry run");
  assert.ok(html.includes('data-do="test-edit"'),"the dry-run test must stay available alongside real execution");
});

test("Combined Wizard step order: step 1 is Actions & Queries, step 2 is Trigger", () => {
  const ui=studio();
  ui.edit={id:"x",trigger:"",steps:[]};
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  // The step-intro h3 shows the active step label (German UI)
  ui.wizardStep=1;
  const step1=ui.stepsWizardView();
  // h3 in step-intro reflects the current step label
  assert.ok(step1.includes("Aktionen") || step1.includes("Actions"),"step 1 label must be Aktionen/Actions");
  // The active panel must contain the step list
  assert.ok(step1.includes('data-do="add-step"'),"step 1 must include the action/query list");
  // Step 2 trigger panel
  ui.wizardStep=2;ui.wizardMaxStep=2;
  const step2=ui.stepsWizardView();
  assert.ok(step2.includes("Trigger") || step2.includes("Wenn ich sage"),"step 2 label must be Trigger");
  assert.ok(step2.includes('id="triggers"'),"step 2 must include the trigger textarea");
});

test("Combined Wizard trigger step shows capture hints when an action uses a capture argument", () => {
  const ui=studio();
  ui.info.actions=[todoAddAction];
  ui.edit={id:"x",trigger:"",steps:[{kind:"action",action:"todo-add:0",target_kind:"entity_ids",target_value:[],alias:"bring"}]};
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  ui.wizardStep=2;ui.wizardMaxStep=2;
  const html=ui.stepsWizardView();
  assert.ok(html.includes("{item}"),"trigger step must show {item} capture hint");
  assert.ok(html.includes("Benötigte Platzhalter") || html.includes("Required placeholder"),"must show the hint label");
});

test("Combined Wizard step editor shows capture-info banner for action with capture arguments", () => {
  const ui=studio();
  ui.info.actions=[todoAddAction];
  ui.registry.entities=[{id:"todo.shopping",name:"Einkaufsliste",area_name:null}];
  ui.edit={id:"x",trigger:"",steps:[{kind:"action",action:"todo-add:0",target_kind:"entity_ids",target_value:[],alias:""}]};
  ui.editingStepIndex=0;
  const html=ui.stepEditorView(ui.edit.steps[0],0);
  assert.ok(html.includes("{item}"),"step editor must show {item} in capture-info banner");
  assert.ok(html.includes("Auslöser-Platzhalter") || html.includes("Trigger placeholder"),"must show the capture-info label");
});

test("placeholderNames() includes captures from Combined Wizard steps", () => {
  const ui=studio();
  ui.info.actions=[todoAddAction];
  ui.edit={id:"x",trigger:"",steps:[{kind:"action",action:"todo-add:0",target_kind:"entity_ids",target_value:[],alias:""}]};
  const names=ui.placeholderNames();
  assert.ok(names.includes("item"),`item must appear in placeholderNames from steps, got: ${names}`);
});

test("light.set_color: hex/RGB conversion round-trips", () => {
  const ui=studio();
  assert.deepEqual(ui.hexToRgb(ui.rgbToHex([127,255,0])),[127,255,0]);
  assert.equal(ui.rgbToHex(null),"#ffffff");
  assert.deepEqual(ui.hexToRgb("not-a-color"),[255,255,255]);
});

test("light.set_color: visual picker only renders for that operation, and reflects the currently configured color", () => {
  const ui=studio();
  assert.equal(ui.lightColorPickerView("light.turn_on",{}),"");
  const html=ui.lightColorPickerView("light.set_color",{rgb_color:[127,255,0]});
  assert.ok(html.includes('id="action-color-picker"'));
  assert.ok(html.includes('value="#7fff00"'));
  const named=ui.lightColorPickerView("light.set_color",{color_name:"chartreuse"});
  assert.ok(named.includes('value="chartreuse"'));
});

test("light.set_color: picking a color visually overrides an Expert step's own arguments", async () => {
  const ui=studio();
  ui.edit={id:"lumos",trigger:"Lumos",steps:[{kind:"action",expert:true,operation:"light.set_color",arguments:{rgb_color:[255,0,0]},target:{kind:"all_allowed"}}]};
  ui.editingStepIndex=0;
  ui.api=async()=>({ok:true,issues:[]});
  await ui.change({target:{id:"action-color-picker",value:"#7fff00",dataset:{}}});
  assert.deepEqual(ui.edit.steps[0].arguments,{rgb_color:[255,0,0]},"form changes stay isolated until Apply");
  assert.equal(await ui.stepsApplyDraft(),true);
  const step=ui.edit.steps[0];
  assert.equal(step.operation,"light.set_color");
  assert.deepEqual(step.arguments,{rgb_color:[127,255,0]});
  // core/studio.py's _is_freeform_light_color() still recognizes exactly
  // this shape as Wizard-representable on reopen, even though entering a
  // custom color uses the step's Expert fields.
});

test("light.set_color: typing an HA color name overrides rgb_color with color_name", async () => {
  const ui=studio();
  ui.edit={id:"lumos",trigger:"Lumos",steps:[{kind:"action",expert:true,operation:"light.set_color",arguments:{rgb_color:[255,0,0]},target:{kind:"all_allowed"}}]};
  ui.editingStepIndex=0;
  ui.api=async()=>({ok:true,issues:[]});
  await ui.change({target:{id:"action-color-name",value:"chartreuse",dataset:{}}});
  assert.deepEqual(ui.edit.steps[0].arguments,{rgb_color:[255,0,0]},"form changes stay isolated until Apply");
  assert.equal(await ui.stepsApplyDraft(),true);
  assert.deepEqual(ui.edit.steps[0].arguments,{color_name:"chartreuse"});
});

test("convert-to-expert always switches immediately (Wizard -> Expert never needs validation)", async () => {
  const ui=studio();ui.edit={...structuredClone(spell),steps:ui.normalizeSteps(spell)};ui.editorMode="wizard";
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  await ui.act("convert-to-expert");
  assert.equal(ui.editorMode,"expert");
  assert.equal(ui.edit.id,"lumos","the step data itself is untouched - only the editor's own affordances change");
});

test("convert-to-wizard is gated by the backend's validate response and reports concrete blockers on failure", async () => {
  const ui=studio();ui.edit={id:"jinja",trigger:"mach das",steps:[{kind:"action",expert:true,operation:"light.turn_on",target:{kind:"template",value:"{{ [] }}"},arguments:{}}]};
  ui.editorMode="expert";
  ui.value=()=>"mach das";ui.shadowRoot.querySelectorAll=()=>[];
  ui.api=async()=>({wizard_blockers:["Schritt 1: Ziel-Art „template“ (Jinja) wird vom Wizard nicht unterstützt."]});
  await ui.act("convert-to-wizard");
  assert.equal(ui.editorMode,"expert","a rejected conversion must leave the spell in Expert, unchanged");
  assert.deepEqual(ui.conversionBlockers,["Schritt 1: Ziel-Art „template“ (Jinja) wird vom Wizard nicht unterstützt."]);
  assert.ok(ui.stepsWizardView().includes("Ziel-Art „template“"),"the concrete blocker must be shown to the user");

  ui.api=async()=>({wizard_blockers:[]});
  await ui.act("convert-to-wizard");
  assert.equal(ui.editorMode,"wizard");
  assert.equal(ui.conversionBlockers,null);
});

test("Gerätetyp options show a localized friendly name and the raw domain as two aligned, non-collapsing columns", () => {
  const ui=studio();
  ui.info.actions=[
    {id:"light-on",operation:"light.turn_on",arguments:{},targets:["entity_id"],domain_filter:"light"},
    {id:"cover-open",operation:"cover.open_cover",arguments:{},targets:["entity_id"],domain_filter:"cover"},
  ];
  const label=ui.domainOptionLabel("light");
  // Non-breaking spaces, not plain ones - HTML would otherwise collapse
  // plain-space padding and silently undo the column alignment.
  assert.ok(label.includes(" "),"padding must use non-breaking spaces");
  assert.ok(label.startsWith("Licht"),"left column is the localized friendly name");
  assert.ok(label.trimEnd().endsWith("| light"),"right column is the raw HA domain, pipe-separated");
  // Both options pad to the same width so the "|" separators line up.
  const lightPipeIndex=ui.domainOptionLabel("light").indexOf("|");
  const coverPipeIndex=ui.domainOptionLabel("cover").indexOf("|");
  assert.equal(lightPipeIndex,coverPipeIndex,"separators must align across options");
});

test("Gerätetyp friendly names are localized (DE default, EN translation) and unknown domains fall back to the raw id", () => {
  const ui=studio();
  ui.info.actions=[{id:"a",operation:"select.select_option",arguments:{},targets:["entity_id"],domain_filter:"select"}];
  ui.language="de";
  assert.ok(ui.domainOptionLabel("select").startsWith("Auswahl"),"German label");
  ui.language="en";
  assert.ok(ui.domainOptionLabel("select").startsWith("Select"),"English translation");
  // A domain the label map has never heard of (e.g. a future catalogue
  // addition) must still render usably - the raw domain id itself, not a
  // crash or an empty label.
  assert.ok(ui.domainOptionLabel("humidifier").startsWith("humidifier"));
});

test("Wizard action list is filtered by device type from backend domain_filter metadata, not a JS domain table", () => {
  const ui=studio();ui.edit={...structuredClone(spell),operation:"light.turn_on",arguments:{}};
  ui.info.actions=[
    {id:"light-on",label:"Licht einschalten",operation:"light.turn_on",arguments:{},targets:["entity_id"],domain_filter:"light"},
    {id:"cover-open",label:"Öffnen",operation:"cover.open_cover",arguments:{},targets:["entity_id"],domain_filter:"cover"},
    {id:"query",label:"Zustand abfragen",operation:"entity.get_state",arguments:{},targets:["entity_id"],domain_filter:null},
  ];
  ui.shadowRoot.querySelectorAll=()=>[];
  // No filter chosen yet: every action (including the domain-agnostic query) is visible.
  assert.deepEqual(ui.visibleActionChoices().map(a=>a.id).sort(),["cover-open","light-on","query"]);
  // Domain list itself comes from the actions' own domain_filter values, not a hardcoded set.
  assert.deepEqual(ui.availableActionDomains(),["cover","light"]);
  // Choosing "light" hides the cover action but keeps the domain-agnostic query.
  ui.wizardActionDomain="light";
  assert.deepEqual(ui.visibleActionChoices().map(a=>a.id).sort(),["light-on","query"]);
  // The currently-selected action is never silently dropped by a filter change,
  // even if it no longer matches - it stays selectable until explicitly changed.
  assert.deepEqual(ui.visibleActionChoices("cover-open").map(a=>a.id).sort(),["cover-open","light-on","query"]);
});

test("Wizard action list hides capability-gated actions once a non-supporting device is already selected", () => {
  const ui=studio();ui.edit={...structuredClone(spell),operation:"light.turn_on",arguments:{}};
  ui.info.actions=[
    {id:"set-color",label:"Rot",operation:"light.set_color",arguments:{rgb_color:[255,0,0]},targets:["entity_id"],domain_filter:"light",capability_hint:"rgb"},
    {id:"light-on",label:"Licht einschalten",operation:"light.turn_on",arguments:{},targets:["entity_id"],domain_filter:"light",capability_hint:null},
  ];
  // No device selected yet: capability-gated action stays visible (can't judge yet).
  ui.shadowRoot.querySelectorAll=()=>[];
  assert.deepEqual(ui.visibleActionChoices().map(a=>a.id).sort(),["light-on","set-color"]);
  // A plain on/off light is selected: the RGB-only action is hidden.
  ui.registry.entities=[{id:"light.plain",name:"Plain Light",capabilities:{rgb:false}}];
  ui.shadowRoot.querySelectorAll=sel=>sel==='input[name="entity"]:checked'?[{value:"light.plain"}]:[];
  assert.deepEqual(ui.visibleActionChoices().map(a=>a.id),["light-on"]);
  // An RGB-capable light is selected instead: both actions are offered again.
  ui.registry.entities=[{id:"light.rgb",name:"RGB Light",capabilities:{rgb:true}}];
  ui.shadowRoot.querySelectorAll=sel=>sel==='input[name="entity"]:checked'?[{value:"light.rgb"}]:[];
  assert.deepEqual(ui.visibleActionChoices().map(a=>a.id).sort(),["light-on","set-color"]);
});

test("output mode selector reads the valid set and default from backend metadata, never a JS-hardcoded list", () => {
  const ui=studio();ui.edit=structuredClone(spell);
  ui.info.output_modes=["AUTO","TEXT","SPEECH","TEXT_AND_SPEECH","NONE"];ui.info.default_output_mode="AUTO";
  const html=ui.outputModeOptions("TEXT");
  for(const mode of ui.info.output_modes)assert.ok(html.includes(`value="${mode}"`),mode);
  assert.ok(html.includes('value="TEXT" selected'));
  // Falls back to the same five modes even when the backend hasn't supplied
  // output_modes yet (an older server) - still never invents domain data,
  // just a stable default for this fixed, non-domain-specific enum.
  ui.info.output_modes=undefined;
  assert.ok(ui.outputModeOptions().includes('value="AUTO" selected'));
});

test("context help is UI-only and Wizard validation calls the existing validator", async () => {
  const ui=studio();ui.edit=structuredClone(spell);ui.editorMode="wizard";ui.editorDirty=false;
  await ui.act("wizard-help","target");
  assert.equal(ui.wizardHelp.has("target"),true);assert.equal(ui.editorDirty,false);
  ui.wizardApplyDraft=()=>{};ui.collectSpell=()=>({mode:"user",spell:{id:"lumos"}});
  let call;ui.api=async(operation,data)=>{call={operation,data};return {spells:[{id:"lumos",errors:[],warnings:[]}]};};
  await ui.act("wizard-validate");
  assert.equal(call.operation,"validate");assert.equal(ui.wizardValidation.spells[0].id,"lumos");
});

test("light.set_color: reopening a saved spell shows the configured color pre-filled in the Wizard's Action step", () => {
  const ui=studio();
  ui.edit={id:"lumos",trigger:"Lumos",steps:[{kind:"action",expert:true,operation:"light.set_color",arguments:{rgb_color:[127,255,0]},target:{kind:"all_allowed"}}]};
  ui.editorMode="expert";ui.wizardStep=1;ui.wizardMaxStep=1;ui.editingStepIndex=0;
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  const html=ui.stepsWizardView();
  assert.ok(html.includes('id="action-color-picker"'));
  assert.ok(html.includes('value="#7fff00"'));
});

test("Wizard/Expert renders the five unified step-editor sections over the same field IDs", () => {
  const ui=studio();ui.edit={...structuredClone(spell),steps:ui.normalizeSteps(spell)};ui.editorMode="wizard";ui.wizardStep=0;
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  const html=ui.stepsWizardView();
  for(const text of ["Grundinformationen","Aktionen &amp; Abfragen","Auslöser","Antwort","Prüfen &amp; Testen"])assert.ok(html.includes(text),text);
  for(const id of ["spell-name","spell-description","spell-examples","tags"])assert.ok(html.includes(`id="${id}"`),id);
  assert.ok(html.includes('data-do="convert-to-expert"'),"Wizard offers switching to Expert instead of the old open-manual jump");
  assert.ok(html.includes('data-do="wizard-next"'));
});

test("Wizard stepper: current, already-visited, and previously-reached steps are clickable, never-reached steps are not", () => {
  const ui=studio();ui.edit={...structuredClone(spell),steps:ui.normalizeSteps(spell)};ui.editorMode="wizard";ui.wizardStep=3;ui.wizardMaxStep=3;
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  const html=ui.stepsWizardView();
  for(const index of [0,1,2,3])assert.ok(html.includes(`data-do="wizard-goto" data-id="${index}"`),`step ${index} clickable`);
  assert.ok(!html.includes('data-do="wizard-goto" data-id="4"'),"step 4 not clickable");
  // The whole circle+label area is one clickable control, not just the number.
  assert.match(html,/<button type="button" class="step-button" data-do="wizard-goto" data-id="0"[^>]*><span>1<\/span>/);
  assert.match(html,/<div class="step-button" aria-disabled="true"><span>5<\/span>/);
});

test("wizard-goto navigates freely within already-validated steps, forward and backward, but refuses to skip past the furthest validated step", async () => {
  const ui=studio();ui.edit=structuredClone(spell);ui.editorMode="wizard";ui.wizardStep=3;ui.wizardMaxStep=3;
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  const calls=[];ui.api=async(op,data)=>{calls.push(op);return {ok:true,issues:[]};};
  await ui.act("wizard-goto","1");
  assert.equal(ui.wizardStep,1);
  assert.equal(ui.wizardMaxStep,3,"jumping backward must not shrink the furthest validated step");
  assert.deepEqual(calls,[],"backward navigation never re-validates");
  await ui.act("wizard-goto","4");
  assert.equal(ui.wizardStep,1,"a step never yet validated must be refused");
  await ui.act("wizard-goto","3");
  assert.equal(ui.wizardStep,3,"jumping forward again to an already-validated step must work directly, not only via Next");
  // Jumping from Step 1 to Step 3 re-validates BOTH skipped steps (1
  // and 2), not just the one being left - see the dedicated multi-step
  // breadcrumb-jump regression test below for why.
  assert.deepEqual(calls,["validate_step","validate_step"],"the forward jump re-validated every step it passed through");
  await ui.act("wizard-goto","1");
  assert.equal(ui.wizardStep,1,"the current step itself stays reachable");
});

test("wizard-next advances the furthest validated step so it stays unlocked after navigating back", async () => {
  const ui=studio();ui.edit=structuredClone(spell);ui.editorMode="wizard";ui.wizardStep=0;ui.wizardMaxStep=0;
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  ui.api=async()=>({ok:true,issues:[]});
  await ui.act("wizard-next");
  await ui.act("wizard-next");
  assert.equal(ui.wizardStep,2);
  assert.equal(ui.wizardMaxStep,2);
  await ui.act("wizard-goto","0");
  assert.equal(ui.wizardMaxStep,2,"going back must not forget steps already validated");
  await ui.act("wizard-goto","2");
  assert.equal(ui.wizardStep,2,"the previously validated step must be directly reachable again");
});

test("wizard-next is blocked by a failed validate_step and shows the structured error without advancing", async () => {
  const ui=studio();ui.edit=structuredClone(spell);ui.editorMode="wizard";ui.wizardStep=0;ui.wizardMaxStep=0;
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  ui.api=async()=>({ok:false,issues:[{code:"missing_id",severity:"error",path:"id",message:"Jeder Spell benötigt eine eindeutige ID."}]});
  await ui.act("wizard-next");
  assert.equal(ui.wizardStep,0,"Next must not advance past a failed step validation");
  assert.equal(ui.wizardMaxStep,0);
  assert.deepEqual(ui.stepValidation,{step:0,issues:[{code:"missing_id",severity:"error",path:"id",message:"Jeder Spell benötigt eine eindeutige ID."}]});
  assert.ok(ui.stepsWizardView().includes("Jeder Spell benötigt eine eindeutige ID."),"the field-level error is rendered on the current step");
});

test("wizard-back never re-validates and always succeeds even when the current step is invalid", async () => {
  const ui=studio();ui.edit=structuredClone(spell);ui.editorMode="wizard";ui.wizardStep=2;ui.wizardMaxStep=2;
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  ui.api=async()=>assert.fail("wizard-back must never call the backend");
  await ui.act("wizard-back");
  assert.equal(ui.wizardStep,1);
});

// Regression: reproduced on a live instance (a themed spellbook, spell
// "lumos_percent", a stale trigger saved before this validation existed
// with two adjacent placeholders). Opening an EXISTING spell sets
// wizardMaxStep=4 immediately (every breadcrumb clickable, "already
// saved = trusted valid"). Jumping straight from Step 1 to Step 5 via
// the breadcrumb used to validate ONLY the step being left (Step 1),
// silently skipping Steps 2-4's own checks - the broken trigger was
// never caught until the final "validate"/"dry_run" call, surfacing as
// the old generic "Spell X: Trigger, Captures oder Aktionsstruktur sind
// ungültig." error far away from where it actually lives.
test("a multi-step breadcrumb jump validates every skipped step, not just the one being left, and lands on the first failing one", async () => {
  const ui=studio();ui.edit=structuredClone(spell);ui.editorMode="wizard";ui.wizardStep=0;ui.wizardMaxStep=4;
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  const checkedSteps=[];
  ui.api=async(operation,data)=>{
    if(operation!=="validate_step")return {};
    checkedSteps.push(data.step);
    // Steps 0 and 1 are fine; Step 2 (Trigger) is the one with the
    // stale, structurally invalid trigger - exactly like lumos_percent.
    if(data.step>=2)return {ok:false,issues:[{code:"adjacent_placeholders",severity:"error",path:"trigger",
      message:"spellbook engine: command lumos_percent's trigger 'Lumos {area} {value} Prozent' has two adjacent placeholders, skipping"}]};
    return {ok:true,issues:[]};
  };
  await ui.act("wizard-goto","4");
  assert.deepEqual(checkedSteps,[0,1,2],"Steps 0 and 1 must be checked before Step 2 is ever reached - not skipped by the jump");
  assert.equal(ui.wizardStep,2,"must land on the first step that actually fails, not stay on Step 0 or reach Step 4");
  assert.equal(ui.wizardMaxStep,4,"wizardMaxStep is untouched by a failed jump - the existing spell is still fully reachable for further edits");
  assert.equal(ui.stepValidation.step,2);
  assert.ok(ui.stepsWizardView().includes("adjacent placeholders")||ui.stepsWizardView().includes("Prozent"),
    "the structured error is rendered on Step 2 (Trigger), where the problem actually lives");
});

test("Wizard device filter reacts live without changing the spell draft", () => {
  const ui=studio();ui.edit=structuredClone(spell);ui.editorDirty=false;
  const rows=[
    {dataset:{entitySearch:"petkit puramax badezimmer"},hidden:false},
    {dataset:{entitySearch:"licht küche light.kitchen"},hidden:false},
  ];
  ui.shadowRoot.querySelectorAll=selector=>selector==="[data-entity-search]"?rows:[];
  ui.updateWizardEntityFilter("küche");
  assert.equal(rows[0].hidden,true);
  assert.equal(rows[1].hidden,false);
  assert.equal(ui.wizardEntityFilter,"küche");
  assert.equal(ui.editorDirty,false);
});

test("Wizard explains automatic output for state queries (single-step query action)", () => {
  const ui=studio();
  ui.info.actions=[{id:"get-state",label:"Zustand abfragen",operation:"entity.get_state",arguments:{},targets:["entity_id"],response_fields:{"*":["state","entity_name","entity_id","area"],sensor:["unit"],light:["brightness","brightness_percent"]}}];
  ui.registry.entities=[{id:"sensor.room",name:"Raumtemperatur",area_name:"Wohnzimmer"}];
  ui.edit={id:"temp",trigger:"temperatur",steps:[{kind:"action",expert:false,action:"get-state",target_kind:"entity_id",target_value:"sensor.room"}]};
  ui.editorMode="wizard";ui.wizardStep=3;ui.wizardMaxStep=3;ui.wizardHelp.add("response");
  const fields={"spell-name":"Temperatur","triggers":"temperatur","spell-output":"Die Temperatur ist fest.","spell-catchphrase":""};
  ui.value=id=>fields[id]||"";
  ui.shadowRoot.querySelectorAll=selector=>selector==='input[name="entity"]:checked'?[{value:"sensor.room"}]:[];

  const html=ui.stepsWizardView();
  assert.ok(html.includes("Konfigurierbare Zustandsantwort"));
  assert.ok(html.includes("{state}"));
  assert.ok(html.includes("{unit}"));
  assert.ok(html.includes("{entity_name}"));
  assert.ok(html.includes("{area}"));
  assert.ok(!html.includes("{brightness}"));
});

test("Wizard review uses the shared client and source-area dry-run context", async () => {
  const ui=studio();ui.edit={...structuredClone(spell),steps:ui.normalizeSteps(spell)};ui.editorMode="wizard";ui.wizardStep=4;
  ui.current.profiles=[{id:"client",name:"CODE27"},{id:"client-2",name:"Flur-Client"}];
  ui.registry.areas=[{id:"living",name:"Wohnzimmer"},{id:"hall",name:"Flur"}];
  ui.source="living";ui.value=id=>({triggers:"Lumos","spell-name":"Lumos"}[id]||"");
  ui.shadowRoot.querySelectorAll=()=>[];
  const html=ui.stepsWizardView();
  for(const id of ["wizard-profile","wizard-source"])assert.ok(html.includes(`id="${id}"`),id);
  for(const text of ["Testen mit Client","Herkunftsbereich","Flur-Client","Wohnzimmer"])assert.ok(html.includes(text),text);

  await ui.change({target:{id:"wizard-profile",value:"client-2",dataset:{}}});
  await ui.change({target:{id:"wizard-source",value:"hall",dataset:{}}});
  let request;ui._hass={callWS:async data=>{request=data;}};
  await ui.api("dry_run",{phrase:"Lumos"});
  assert.equal(request.data.profile_id,"client-2");
  assert.equal(request.data.source_area_id,"hall");
});

test("Wizard target kinds come directly from action metadata including source_area", () => {
  const ui=studio();
  const action={id:"area-action",label:"Area action",operation:"custom.area_action",arguments:{},targets:["area_name","area_id","source_area"]};
  assert.deepEqual(ui.targetChoices(action).map(row=>row.id),["area_name","area_id","source_area"]);
  assert.ok(!ui.targetChoices(action).some(row=>row.id==="all_allowed"));
});

test("new Wizard spells initialize a valid effective language before translated fields", async () => {
  const ui=studio();delete ui.current.book.default_language;delete ui.current.book.language;ui.current.languages=["en"];
  ui.language="de";ui.ensureEditorTargets=async()=>{};
  ui.startNewSpell("wizard");
  assert.equal(ui.bookLanguage,"de");assert.equal(ui.editLanguage,"de");assert.ok(ui.validLanguage(ui.editLanguage));
  const fields={"step-target-kind":"source_area",triggers:"lumos","spell-name":"Lumos"};ui.value=id=>fields[id]||"";
  ui.shadowRoot.querySelectorAll=()=>[];
  const automatic=await ui.collectSteps();assert.equal(automatic.trigger_language,"de");assert.equal(automatic.default_language,"de");
  ui.startNewSpell("wizard","en");
  assert.equal(ui.bookLanguage,"en");assert.equal(ui.editLanguage,"en");assert.equal(ui.wizardLanguageExplicit,"en");
});

test("inherited Spellbook language is marked without changing the stored language", () => {
  const ui=studio();delete ui.current.book.default_language;delete ui.current.book.language;ui.current.languages=["en"];ui.bookLanguage="de";
  const html=ui.languageView();
  assert.ok(html.includes("Deutsch (DE)*"));
  assert.ok(html.includes('data-id="de"'));
  assert.ok(!html.includes('data-id="de*"'));
  assert.equal(ui.storedBookLanguage(),null);
  ui.current.book.default_language="de";
  const stored=ui.languageView();
  assert.ok(stored.includes("Deutsch (DE)"));assert.ok(!stored.includes("Deutsch (DE)*"));
});

test("opening and system-language changes keep inherited and stored defaults distinct", async () => {
  const ui=studio();ui.language="de";ui.current=null;ui.ensureAreas=async()=>{};
  const inherited={default_language:"en",languages:["en"],book:{id:"legacy",name:"Legacy",commands:[]},profiles:[],spell_capabilities:{}};
  const calls=[];ui.api=async(operation,data)=>{calls.push([operation,data]);return structuredClone(inherited);};
  await ui.open("legacy");
  assert.equal(ui.bookLanguage,"de");assert.deepEqual(calls.map(call=>call[0]),["read"]);assert.equal(ui.current.book.default_language,undefined);
  ui.loaded=true;ui.render=()=>{};ui.hass={locale:{language:"en"}};
  assert.equal(ui.bookLanguage,"en");assert.equal(ui.current.book.default_language,undefined);
  ui.startNewSpell("wizard");assert.equal(ui.editLanguage,"en");ui.edit=null;ui.wizardLanguageExplicit=null;
  ui.current.book.default_language="de";ui.bookLanguage="de";ui.hass={locale:{language:"de"}};ui.hass={locale:{language:"en"}};
  assert.equal(ui.bookLanguage,"de");
});

test("User Management button is admin-only, real enforcement stays server-side", () => {
  const ui=studio();
  ui._hass={user:{is_admin:false}};
  assert.ok(!ui.topbarToolsView().includes('data-do="user-management"'));
  ui._hass={user:{is_admin:true}};
  assert.ok(ui.topbarToolsView().includes('data-do="user-management"'));
});

test("access-denied screen names the signed-in user in both languages and never shows the Studio", () => {
  const ui=studio();
  ui._hass={user:{is_admin:false,name:"Nils"}};
  const de=ui.accessDeniedView();
  assert.ok(de.includes("Nils"));
  assert.ok(de.includes("Kein Zugriff auf HASE Studio"));
  ui.language="en";
  const en=ui.accessDeniedView();
  assert.ok(en.includes("Nils"));
  assert.ok(en.includes("No access to HASE Studio"));
});

test("init() shows the access-denied state only for an unauthorized WebSocket rejection", async () => {
  const ui=new Studio();
  ui.language="de";
  ui.render=()=>{};
  ui._hass={callWS:async()=>{throw {code:"unauthorized",message:"Kein Zugriff auf HASE Studio."};}};
  await ui.init();
  assert.equal(ui.authorized,false);
  assert.equal(ui.loaded,true);
});

test("init() surfaces a non-authorization WebSocket error normally instead of hiding it", async () => {
  const ui=new Studio();
  ui.language="de";
  ui.render=()=>{};
  ui._hass={callWS:async()=>{throw new Error("boom");}};
  await assert.rejects(() => ui.init());
});

test("User Management loads lazily on open and shows a loading state before access_get resolves", async () => {
  const ui=studio();
  ui._hass={user:{is_admin:true}};
  assert.ok(ui.userManagementView().includes("Nutzerverwaltung"));
  let requested=null;
  ui.api=async operation=>{requested=operation; return {config:{mode:"admin",allowed_user_ids:[],allowed_group_ids:[]},groups:[],users:[]};};
  await ui.act("user-management");
  assert.equal(requested,"access_get");
  assert.equal(ui.userManagementOpen,true);
  assert.deepEqual(ui.accessDraft,{mode:"admin",allowed_user_ids:new Set(),allowed_group_ids:new Set()});
});

test("User Management: CUSTOM mode renders groups/users with an understandable effective-access reason", async () => {
  const ui=studio();
  ui._hass={user:{is_admin:true,name:"Admin"}};
  const snapshot={
    config:{mode:"custom",allowed_user_ids:["u-anna"],allowed_group_ids:["g-family"]},
    groups:[{id:"g-family",name:"Family",member_count:2,allowed:true}],
    users:[
      {id:"admin-1",name:"Admin",is_admin:true,group_ids:[],access:"admin",allowed:false},
      {id:"u-anna",name:"Anna",is_admin:false,group_ids:[],access:"user",allowed:true},
      {id:"u-anika",name:"Anika",is_admin:false,group_ids:["g-family"],access:"group",allowed:false},
      {id:"u-nils",name:"Nils",is_admin:false,group_ids:[],access:"denied",allowed:false},
    ],
  };
  ui.api=async operation=>{assert.equal(operation,"access_get"); return snapshot;};
  await ui.act("user-management");
  const html=ui.userManagementView();
  assert.ok(html.includes("checklist"));
  assert.ok(html.includes("Family"));
  assert.ok(html.includes("2 Mitglieder"));
  assert.ok(html.includes("Anna") && html.includes("Explizit erlaubt"));
  assert.ok(html.includes("Anika") && html.includes("Über Gruppe erlaubt"));
  assert.ok(html.includes("Nils") && html.includes("Kein Zugriff"));
  assert.ok(html.includes("Admin") && html.includes("Administrator"));

  await ui.change({target:{name:undefined, dataset:{accessGroup:"g-family"}, checked:false}});
  assert.deepEqual(ui.accessDraft.allowed_group_ids, new Set());
  await ui.change({target:{name:undefined, dataset:{accessUser:"u-nils"}, checked:true}});
  assert.deepEqual(ui.accessDraft.allowed_user_ids, new Set(["u-anna","u-nils"]));
});

test("only CUSTOM mode renders the groups/users pickers", () => {
  const ui=studio();
  ui.access={config:{mode:"all",allowed_user_ids:[],allowed_group_ids:[]},groups:[{id:"g",name:"G",member_count:1,allowed:false}],users:[]};
  ui.accessDraft={mode:"all",allowed_user_ids:new Set(),allowed_group_ids:new Set()};
  assert.ok(!ui.userManagementView().includes("checklist"));
  ui.accessDraft.mode="custom";
  assert.ok(ui.userManagementView().includes("checklist"));
});

test("changing the access-mode radio updates the draft, not the saved config, until Save", async () => {
  const ui=studio();
  ui.access={config:{mode:"admin",allowed_user_ids:[],allowed_group_ids:[]},groups:[],users:[]};
  ui.accessDraft={mode:"admin",allowed_user_ids:new Set(),allowed_group_ids:new Set()};
  await ui.change({target:{name:"access-mode",value:"all",dataset:{}}});
  assert.equal(ui.accessDraft.mode,"all");
  assert.equal(ui.access.config.mode,"admin");
});

test("access-save confirms, then persists the draft through access_set", async () => {
  const ui=studio();
  ui.access={config:{mode:"admin",allowed_user_ids:[],allowed_group_ids:[]},groups:[],users:[]};
  ui.accessDraft={mode:"all",allowed_user_ids:new Set(),allowed_group_ids:new Set()};
  let confirmed=false;
  ui.askConfirmation=async()=>{confirmed=true; return true;};
  let savedPayload=null;
  ui.api=async(operation,data)=>{
    assert.equal(operation,"access_set");
    savedPayload=data;
    return {config:{mode:"all",allowed_user_ids:[],allowed_group_ids:[]},groups:[],users:[]};
  };
  await ui.act("access-save");
  assert.ok(confirmed);
  assert.deepEqual(savedPayload,{mode:"all",allowed_user_ids:[],allowed_group_ids:[]});
  assert.equal(ui.access.config.mode,"all");
});

test("declining the access-save confirmation leaves the stored config untouched", async () => {
  const ui=studio();
  ui.access={config:{mode:"admin",allowed_user_ids:[],allowed_group_ids:[]},groups:[],users:[]};
  ui.accessDraft={mode:"all",allowed_user_ids:new Set(),allowed_group_ids:new Set()};
  ui.askConfirmation=async()=>false;
  ui.api=()=>assert.fail("must not save without explicit confirmation");
  await ui.act("access-save");
  assert.equal(ui.access.config.mode,"admin");
});

test("access-cancel discards draft edits back to the last saved config", async () => {
  const ui=studio();
  ui.access={config:{mode:"admin",allowed_user_ids:["a"],allowed_group_ids:[]},groups:[],users:[]};
  ui.accessDraft={mode:"custom",allowed_user_ids:new Set(),allowed_group_ids:new Set(["x"])};
  await ui.act("access-cancel");
  assert.deepEqual(ui.accessDraft,{mode:"admin",allowed_user_ids:new Set(["a"]),allowed_group_ids:new Set()});
});

test("opening Nutzerverwaltung closes Studio-Einstellungen and vice versa", async () => {
  const ui=studio();
  ui._hass={user:{is_admin:true}};
  ui.api=async()=>({config:{mode:"admin",allowed_user_ids:[],allowed_group_ids:[]},groups:[],users:[]});
  await ui.act("user-management");
  assert.equal(ui.userManagementOpen,true);
  await ui.act("settings");
  assert.equal(ui.settingsOpen,true);
  assert.equal(ui.userManagementOpen,false);
});

test("User Management i18n has both German and English text for every new label", () => {
  for (const [de_key, english] of [
    ["Nutzerverwaltung","User Management"],
    ["Alle Benutzer","All users"],
    ["Nur Administratoren","Administrators only"],
    ["Benutzer & Gruppen individuell","Custom users & groups"],
    ["Explizit erlaubt","Explicitly allowed"],
    ["Über Gruppe erlaubt","Allowed through group"],
    ["Kein Zugriff","No access"],
    ["Administrator","Administrator"],
    ["Kein Zugriff auf HASE Studio","No access to HASE Studio"],
  ]) {
    assert.equal(translate("de",de_key),de_key);
    assert.equal(translate("en",de_key),english);
  }
});

test("MCP-check button and not-yet-checked badge render in the spell list", () => {
  const html = studio().bookView();
  assert.ok(html.includes('data-do="check-mcp"'));
  assert.ok(html.includes("MCP: noch nicht geprüft"));
});

test("check-mcp action calls check_mcp and stores the report", async () => {
  const ui = studio();
  let calledWith = null;
  ui.api = async (op) => {
    calledWith = op;
    return {spells: [{id: "lumos", execution_kind: "action", projectable: true, skip_reason: null,
                       read_only: false, requires_unrestricted_bridge: false, grant_reason: null}],
            projectable: 1, not_projectable: 0, requires_unrestricted: 0};
  };
  await ui.act("check-mcp");
  assert.equal(calledWith, "check_mcp");
  assert.equal(ui.mcpReport.projectable, 1);
});

test("mcpReport is cleared when a different book is opened, never shown stale", async () => {
  const ui = studio();
  ui.mcpReport = {spells: [], projectable: 0, not_projectable: 0, requires_unrestricted: 0};
  ui.api = async () => ui.current;
  ui.testProfiles = () => [];
  ui.loadStoredSpellSearch = () => "";
  ui.ensureAreas = async () => {};
  await ui.open("testbook");
  assert.equal(ui.mcpReport, null);
});

test("mcpBadge shows structural capability and actual grant separately for a projectable action", () => {
  const ui = studio();
  ui.mcpReport = {spells: [{id: "lumos", execution_kind: "action", projectable: true, skip_reason: null,
                             read_only: false, requires_unrestricted_bridge: false, grant_reason: null}],
                   projectable: 1, not_projectable: 0, requires_unrestricted: 0};
  const html = ui.bookView();
  assert.ok(html.includes("Aktion"));
  assert.ok(html.includes("keine zusätzliche Bridge-Voraussetzung"));
  assert.ok(!html.includes("MCP: nicht bereitstellbar"));
});

test("mcpBadge surfaces the assist_redirect grant requirement with the shared reason code", () => {
  const ui = studio();
  ui.mcpReport = {spells: [{id: "lumos", execution_kind: "assist_redirect", projectable: true, skip_reason: null,
                             read_only: false, requires_unrestricted_bridge: true,
                             grant_reason: "assist_redirect_requires_unrestricted_policy"}],
                   projectable: 1, not_projectable: 0, requires_unrestricted: 1};
  const html = ui.bookView();
  assert.ok(html.includes("Assist-Weiterleitung"));
  assert.ok(html.includes("benötigt uneingeschränkte Bridge"));
});

test("mcpBadge reports a not-projectable spell with its concrete reason, never a silent gap", () => {
  const ui = studio();
  ui.mcpReport = {spells: [{id: "lumos", execution_kind: "action", projectable: false,
                             skip_reason: "unknown_operation:light.bogus", read_only: null,
                             requires_unrestricted_bridge: false, grant_reason: null}],
                   projectable: 0, not_projectable: 1, requires_unrestricted: 0};
  const html = ui.bookView();
  assert.ok(html.includes("MCP: nicht bereitstellbar"));
  assert.ok(html.includes("light.bogus"));
});

test("MCP capability i18n has both German and English text for every new label", () => {
  for (const [de_key, english] of [
    ["MCP prüfen", "Check MCP"],
    ["MCP-Prüfergebnis", "MCP check result"],
    ["MCP: noch nicht geprüft", "MCP: not checked yet"],
    ["MCP: nicht bereitstellbar", "MCP: not exposable"],
    ["nur lesend", "read-only"],
    ["Assist-Weiterleitung", "Assist redirect"],
    ["Ablauf", "Combined"],
    ["Textantwort", "Text answer"],
    ["(noch nicht gespeichert)", "(not yet saved)"],
  ]) {
    assert.equal(translate("de",de_key),de_key);
    assert.equal(translate("en",de_key),english);
  }
});

test("opening a book populates mcpReport from the read response's saved status", async () => {
  const ui = studio();
  const savedReport = {spells: [{id: "lumos", execution_kind: "action", projectable: true, skip_reason: null,
                                  read_only: false, requires_unrestricted_bridge: false, grant_reason: null,
                                  stale: false}],
                        projectable: 1, not_projectable: 0, requires_unrestricted: 0, stale_count: 0};
  ui.api = async () => ({...ui.current, mcp_report: savedReport});
  ui.testProfiles = () => [];
  ui.loadStoredSpellSearch = () => "";
  ui.ensureAreas = async () => {};
  await ui.open("testbook");
  assert.deepEqual(ui.mcpReport, savedReport);
});

test("check-mcp updates the local revision from the persisted response, never leaving it stale", async () => {
  const ui = studio();
  ui.current.revision = "old-rev";
  ui.api = async () => ({spells: [], projectable: 0, not_projectable: 0, requires_unrestricted: 0,
                          revision: "new-rev-after-save"});
  await ui.act("check-mcp");
  assert.equal(ui.current.revision, "new-rev-after-save");
});

test("mcpBadge flags a stale (not-yet-saved) spell without hiding its real, current values", () => {
  const ui = studio();
  ui.mcpReport = {spells: [{id: "lumos", execution_kind: "action", projectable: true, skip_reason: null,
                             read_only: false, requires_unrestricted_bridge: false, grant_reason: null,
                             stale: true}],
                   projectable: 1, not_projectable: 0, requires_unrestricted: 0, stale_count: 1};
  // P6.5: the MCP summary lives in the "MCP" book tab; the spell badge stays in the list.
  ui.bookTab = "mcp";
  const html = ui.bookView();
  assert.ok(html.includes("Aktion"));
  assert.ok(html.includes("(noch nicht gespeichert)"));
  assert.ok(html.includes("1 Spells zeigen einen noch nicht gespeicherten Stand"));
});

// ── Todo Wizard integration (P8 follow-up) ──────────────────────────────────
// Tests 89-100: verify that todo operations fit cleanly into the existing
// Studio Wizard architecture without any domain-specific special-casing.

const todoListAction = {id:"todo-list:0",label:"Todo-Liste lesen",operation:"todo.list_items",arguments:{},
  targets:["all_allowed","entity_id","entity_ids","entity_name"],response_fields:{"*":["items","count"]},
  domain_filter:"todo",capability_hint:null,target_capture:null};
const todoAddAction = {id:"todo-add:0",label:"Aufgabe hinzufügen",operation:"todo.add_item",
  arguments:{"item":{"capture":"item","type":"str"}},
  targets:["all_allowed","entity_id","entity_ids","entity_name"],response_fields:{},
  domain_filter:"todo",capability_hint:null,target_capture:null};
const todoRemoveAction = {id:"todo-remove:0",label:"Aufgabe entfernen",operation:"todo.remove_item",
  arguments:{"item":{"capture":"item","type":"str"}},
  targets:["all_allowed","entity_id","entity_ids","entity_name"],response_fields:{},
  domain_filter:"todo",capability_hint:null,target_capture:null};
const todoUpdateAction = {id:"todo-update:0",label:"Aufgabe abhaken",operation:"todo.update_item",
  arguments:{"item":{"capture":"item","type":"str"},"status":"completed"},
  targets:["all_allowed","entity_id","entity_ids","entity_name"],response_fields:{},
  domain_filter:"todo",capability_hint:null,target_capture:null};

test("Todo domain label shows 'Todo' not 'todo | todo' in the Gerätetyp picker", () => {
  const ui = studio();
  ui.info.actions = [todoListAction, {id:"light-on",operation:"light.turn_on",arguments:{},targets:["entity_id"],domain_filter:"light"}];
  const label = ui.domainOptionLabel("todo");
  assert.ok(label.startsWith("Todo"), `expected 'Todo…' but got '${label}'`);
  assert.ok(label.includes("| todo"), "right column must be the raw domain id 'todo'");
  assert.ok(!label.startsWith("todo | todo"), "must not show raw id twice");
});

test("Todo entity target choices use 'Todo-Liste' / 'Todo-Listen' labels", () => {
  const ui = studio();
  const choices = ui.targetChoices(todoListAction);
  const entityId = choices.find(c => c.id === "entity_id");
  const entityIds = choices.find(c => c.id === "entity_ids");
  assert.ok(entityId, "entity_id choice must exist");
  assert.ok(entityIds, "entity_ids choice must exist");
  assert.ok(entityId.name.toLowerCase().includes("todo"), `entity_id label must mention todo, got: ${entityId.name}`);
  assert.ok(entityIds.name.toLowerCase().includes("todo"), `entity_ids label must mention todo, got: ${entityIds.name}`);
  // Must NOT say 'Gerät' for todo
  assert.ok(!entityId.name.toLowerCase().includes("gerät"), "must not say Gerät for todo");
  assert.ok(!entityIds.name.toLowerCase().includes("gerät"), "must not say Geräte for todo");
});

test("Non-todo entity target choices retain 'Gerät' / 'Geräte' labels unchanged", () => {
  const ui = studio();
  const lightAction = {id:"light-on",operation:"light.turn_on",arguments:{},targets:["entity_id","entity_ids"],domain_filter:"light"};
  const choices = ui.targetChoices(lightAction);
  const entityId = choices.find(c => c.id === "entity_id");
  const entityIds = choices.find(c => c.id === "entity_ids");
  assert.ok(entityId.name.toLowerCase().includes("gerät"), `light entity_id label must say Gerät, got: ${entityId.name}`);
  assert.ok(entityIds.name.toLowerCase().includes("gerät"), `light entity_ids label must say Gerät, got: ${entityIds.name}`);
});

test("Todo entity picker renders 'Todo-Liste' heading in the unified step editor", () => {
  const ui = studio();
  ui.info.actions = [todoListAction];
  ui.registry.entities = [{id:"todo.shopping",name:"Einkaufsliste",area_name:null}];
  ui.edit = {id:"todo-spell",trigger:"offene todos",steps:[{kind:"action",expert:false,action:"todo-list:0",target_kind:"entity_id",target_value:"todo.shopping"}],tags:[]};
  ui.editorMode = "wizard"; ui.wizardStep = 1; ui.editingStepIndex=0;
  ui.value = id => ({"step-target-kind":"entity_id","step-alias":"","spell-name":"Todos","triggers":"offene todos","spell-output":"","spell-catchphrase":"","output-mode":"","tags":""})[id]||"";
  ui.shadowRoot.querySelectorAll = sel => sel==='input[name="entity"]:checked'?[{value:"todo.shopping"}]:[];
  const html = ui.stepsWizardView();
  assert.ok(html.includes("Todo-Liste") || html.includes("Todo-Listen"), "entity field label must mention Todo-Liste");
  assert.ok(html.includes("todo.shopping") || html.includes("Einkaufsliste"), "todo entity must appear in picker");
});

test("Todo entity picker renders 'Todo-Liste' heading in Combined Wizard step editor", () => {
  const ui = studio();
  ui.info.actions = [todoListAction];
  ui.registry.entities = [{id:"todo.shopping",name:"Einkaufsliste",area_name:null}];
  const html = ui.stepTargetFieldView("step-target","entity_id","todo.shopping",todoListAction);
  assert.ok(html.toLowerCase().includes("todo-liste"), `expected 'Todo-Liste' label in step target field, got: ${html.slice(0,200)}`);
  assert.ok(!html.toLowerCase().includes("gerät auswählen"), "must not say 'Gerät auswählen' for todo entity_id");
  assert.ok(html.includes("todo.shopping"), "todo entity must appear in picker");
});

test("stepTargetChoices uses 'Todo-Liste' labels for todo action (Combined Wizard 'Wo?' dropdown)", () => {
  const ui=studio();
  const choices=ui.stepTargetChoices(todoListAction);
  const entityId=choices.find(c=>c.id==="entity_id");
  assert.ok(entityId,"entity_id choice must exist");
  assert.ok(entityId.name.toLowerCase().includes("todo"),`stepTargetChoices entity_id label must say Todo, got: ${entityId.name}`);
  assert.ok(!entityId.name.toLowerCase().includes("gerät"),`stepTargetChoices must NOT say Gerät for todo, got: ${entityId.name}`);
});

test("Non-todo Combined Wizard step target keeps 'Gerät' label", () => {
  const ui = studio();
  const lightAction = {id:"light-on",operation:"light.turn_on",arguments:{},targets:["entity_id"],domain_filter:"light"};
  ui.registry.entities = [{id:"light.kitchen",name:"Küche",area_name:"Küche"}];
  const html = ui.stepTargetFieldView("step-target","entity_id","light.kitchen",lightAction);
  assert.ok(html.toLowerCase().includes("gerät"), `non-todo field must still say Gerät, got: ${html.slice(0,200)}`);
});

test("todo.add_item is accessible in the Wizard action picker via user_choices capture", () => {
  const ui = studio();
  ui.info.actions = [todoAddAction, todoListAction];
  // The action must appear in the general choices (no domain filter selected)
  const choices = ui.actionChoices;
  assert.ok(choices.some(a => a.operation === "todo.add_item"), "todo.add_item must be in action choices");
  assert.ok(choices.some(a => a.operation === "todo.list_items"), "todo.list_items must be in action choices");
  // When 'todo' domain is selected, both todo actions appear
  ui.wizardActionDomain = "todo";
  const visible = ui.visibleActionChoices();
  assert.ok(visible.some(a => a.operation === "todo.add_item"), "todo.add_item visible when todo domain selected");
  // Non-todo action is hidden when todo domain is selected
  ui.info.actions = [...ui.info.actions, {id:"light-on",operation:"light.turn_on",arguments:{},targets:["entity_id"],domain_filter:"light"}];
  const filteredVisible = ui.visibleActionChoices();
  assert.ok(!filteredVisible.some(a => a.operation === "light.turn_on"), "light action hidden when todo domain selected");
});

test("{item} capture in trigger is recognized for a Todo add operation", () => {
  const ui = studio();
  ui.info.actions = [todoAddAction];
  ui.edit = {id:"aquamenti",trigger:"aquamenti {item}",tags:[],
    steps:[{kind:"action",expert:false,action:"todo-add:0",target_kind:"entity_id",target_value:"todo.shopping"}]};
  // placeholderNames() must include 'item' because it is in the guided step's action.arguments
  const names = ui.placeholderNames();
  assert.ok(names.includes("item"), `item must be in placeholderNames, got: ${names}`);
});

// Note: the old single-action Wizard's instant, client-side field warnings
// (wizardLocalIssues() - missing name/trigger/target, unused/missing
// capture placeholders, capability mismatches) have no equivalent in the
// unified step editor yet - see the redesign's final report. The backend
// `validate` round-trip (stepsValidationSummary()) still catches a missing
// {item} placeholder; it just no longer happens instantly on every
// keystroke. Re-adding instant local validation for the new step model is
// tracked as a known follow-up, not reimplemented here.

test("todo.list_items response fields are exposed via generic queryResponseFieldNames", () => {
  const ui = studio();
  ui.info.actions = [todoListAction];
  const names = ui.queryResponseFieldNames(todoListAction);
  assert.ok(names.includes("items"), `response fields must include 'items', got: ${names}`);
  assert.ok(names.includes("count"), `response fields must include 'count', got: ${names}`);
});

test("todo.list_items response help chips appear in the Wizard response step", () => {
  const ui = studio();
  ui.info.actions = [todoListAction];
  ui.registry.entities = [{id:"todo.shopping",name:"Einkaufsliste",area_name:null}];
  ui.edit = {id:"todo-query",trigger:"offene todos",operation:"todo.list_items",arguments:{},
    target:{kind:"entity_id",value:"todo.shopping"},tags:[]};
  ui.shadowRoot.querySelectorAll = sel => sel==='input[name="entity"]:checked'?[{value:"todo.shopping"}]:[];
  const help = ui.wizardStateResponseHelp(todoListAction);
  assert.ok(help.includes("{items}"), `response help must show {items}, got: ${help.slice(0,300)}`);
  assert.ok(help.includes("{count}"), `response help must show {count}, got: ${help.slice(0,300)}`);
});


test("audit: structured blockers each navigate to their own step and field", async()=>{
  const ui=studio();ui.editorMode="expert";ui.edit={id:"audit",steps:Array.from({length:12},()=>({kind:"action",expert:true,target:{kind:"template"},arguments:{}}))};
  ui.stepsApplyDraft=()=>true;
  ui.conversionBlockers=[{code:"template_target",step_index:1,path:"target.value"},{code:"operation_arguments",step_index:10,path:"arguments"}];
  let focused;
  ui.showBlockerHighlight=()=>{focused=ui.activeBlocker.path;};
  assert.equal((ui.conversionBlockersView().match(/data-do="goto-blocker"/g)||[]).length,2);
  await ui.act("goto-blocker","0");assert.equal(ui.editingStepIndex,1);assert.equal(ui.wizardStep,1);assert.equal(focused,"target.value");
  await ui.act("goto-blocker","1");assert.equal(ui.editingStepIndex,10);assert.equal(focused,"arguments");
});

test("audit: blocker focus, accessible inline reason and edit clearing",()=>{
  const ui=studio();let focused=false,scrolled=false,note,described;
  const classes=new Set();
  const field={classList:{add:x=>classes.add(x),remove:x=>classes.delete(x)},setAttribute:(k,v)=>{described=v;},removeAttribute:()=>{described=null;},closest:()=>null,focus:()=>{focused=true;},scrollIntoView:()=>{scrolled=true;},insertAdjacentElement:(_,n)=>{note=n;}};
  const previous=globalThis.document;
  globalThis.document={createElement:()=>({setAttribute(){},remove(){note=null;}})};
  ui.shadowRoot.getElementById=id=>id==="step-expert-target-value"?field:id==="blocker-inline-reason"?note:null;
  ui.shadowRoot.querySelectorAll=()=>classes.size?[field]:[];
  ui.activeBlocker={code:"template_target",step_index:1,path:"target.value"};
  try{ui.showBlockerHighlight();assert.ok(focused&&scrolled);assert.equal(described,"blocker-inline-reason");assert.ok(note.textContent.includes("Template-Ziel"));assert.ok(classes.has("blocker-highlight"));ui.clearBlockerHighlight();assert.equal(note,null);assert.equal(described,null);assert.equal(classes.size,0);}finally{globalThis.document=previous;}
});

test("audit: repeated conversion uses fresh blockers then canonical guided drafts",async()=>{
  const ui=studio();ui.edit={id:"audit",steps:[]};ui.collectSpell=()=>({});
  const first={code:"template_target",step_index:1,path:"target.value"},second={code:"operation_arguments",step_index:2,path:"arguments"};
  const replies=[{wizard_blocker_details:[first,second]},{wizard_blocker_details:[second]},{wizard_blocker_details:[],wizard_step_flags:[false],editor_spell:{operation:"light.set_power",arguments:{on:true},target:{kind:"source_area"}}}];
  ui.api=async()=>replies.shift();ui.editorMode="expert";
  await ui.act("convert-to-wizard");assert.equal(ui.conversionBlockers.length,2);
  await ui.act("convert-to-wizard");assert.deepEqual(ui.conversionBlockers,[second]);
  await ui.act("convert-to-wizard");assert.equal(ui.editorMode,"wizard");assert.equal(ui.edit.steps[0].expert,false);assert.equal(ui.edit.steps[0].action,"light-on");
});

test("audit: all twelve Expert steps stay collapsed except the selected card",()=>{
  const ui=studio();ui.editorMode="expert";ui.info.operations=["light.turn_on"];
  ui.edit={steps:Array.from({length:12},(_,i)=>({alias:`step${i+1}`,kind:"action",expert:true,operation:"light.turn_on",target:{kind:"template",value:"{{ ['light.x'] }}"},arguments:{}}))};
  ui.editingStepIndex=10;
  const html=ui.stepListView();assert.equal((html.match(/step-card-open/g)||[]).length,1);assert.equal((html.match(/id="step-expert-arguments"/g)||[]).length,1);assert.ok(html.includes("Maximal 12"));assert.ok(html.includes('id="step-card-10"'));assert.ok(!html.includes('data-do="add-step"'));
});

test("audit: normal steps in mixed Expert spells keep guided controls",()=>{
  const ui=studio();ui.editorMode="expert";ui.edit={steps:[{kind:"action",expert:false,action:"light-on",target_kind:"source_area"}]};
  const html=ui.stepEditorView(ui.edit.steps[0],0);assert.ok(html.includes('id="step-action"'));assert.ok(!html.includes('id="step-expert-arguments"'));
});

test("audit: free RGB opens guided, shares color widget and survives Wizard to Expert",async()=>{
  const ui=studio();ui.info.actions=[{id:"color",label:"Rot",operation:"light.set_color",arguments:{rgb_color:[255,0,0]},targets:["all_allowed"]}];
  ui.edit={id:"audit",steps:ui.normalizeSteps({operation:"light.set_color",target:{kind:"all_allowed"},arguments:{rgb_color:[17,123,245]}},[false])};
  ui.editorMode="wizard";ui.value=id=>id==="triggers"?"audit":"";
  assert.equal(ui.edit.steps[0].expert,false);assert.ok(ui.stepEditorView(ui.edit.steps[0],0).includes('value="#117bf5"'));
  assert.deepEqual((await ui.collectSteps()).steps[0].color_arguments,{rgb_color:[17,123,245]});
  await ui.act("convert-to-expert");assert.deepEqual((await ui.collectSteps()).steps[0].color_arguments,{rgb_color:[17,123,245]});
});

test("audit: closing technical fields refuses lossy preset substitution and offers repair",async()=>{
  const ui=studio();ui.editorMode="expert";ui.edit={steps:[{kind:"action",expert:true,operation:"light.set_power",target:{kind:"all_allowed"},arguments:{on:false}}]};ui.editingStepIndex=0;
  await ui.change({target:{id:"step-expert",checked:false,dataset:{}}});assert.equal(ui.edit.steps[0].expert,true);assert.deepEqual(ui.edit.steps[0].arguments,{on:false});assert.ok(ui.stepConversionIssue);assert.match(ui.stepEditorView(ui.editingStepDraft,0),/repair-step-preset/);
});

test("audit: empty Expert arguments become an empty object while malformed or non-object JSON still blocks",()=>{
  const ui=studio();const previous={rgb_color:[1,2,3]};
  for(const value of ['','   ','{}']){ui.shadowRoot.getElementById=()=>({value,setCustomValidity(){},focus(){},reportValidity(){}});assert.deepEqual(ui.jsonObject("step-expert-arguments",previous),{});}
  for(const value of ['{bad','[]','null']){let message;ui.shadowRoot.getElementById=()=>({value,setCustomValidity:v=>{message=v;},focus(){},reportValidity(){}});assert.throws(()=>ui.jsonObject("step-expert-arguments",previous),/JSON/);assert.ok(message);assert.deepEqual(previous,{rgb_color:[1,2,3]});}
});

test("step names survive Wizard/Expert roundtrips and reordering without changing alias references",async()=>{
  const ui=studio();ui.edit={id:"night",steps:[
    {alias:"shutters",kind:"action",expert:false,action:"light-on",target_kind:"source_area",target_value:null},
    {alias:"lights",kind:"action",expert:true,operation:"light.set_power",target:{kind:"all_allowed"},arguments:{on:true}},
    {alias:"state",kind:"query",sources:[{kind:"count",expert:false,target_kind:"all_allowed",target_value:null,variable:"n"}]},
  ],output:"{shutters.success} {state.n}"};ui.editorMode="wizard";ui.editingStepIndex=null;ui.value=id=>id==="triggers"?"night":"";ui.render=()=>{};
  await ui.act("convert-to-expert");assert.deepEqual(ui.edit.steps.map(s=>s.alias),["shutters","lights","state"]);
  ui.api=async()=>({wizard_blocker_details:[],wizard_step_flags:[false,false,false],editor_spell:{steps:[
    {alias:"shutters",operation:"light.set_power",target:{kind:"source_area"},arguments:{on:true}},
    {alias:"lights",operation:"light.set_power",target:{kind:"all_allowed"},arguments:{on:true}},
    {alias:"state",sources:[{kind:"count",target:{kind:"all_allowed"},variable:"n"}]},
  ]}});
  await ui.act("convert-to-wizard");assert.deepEqual(ui.edit.steps.map(s=>s.alias),["shutters","lights","state"]);
  await ui.act("move-step-down","0");assert.deepEqual(ui.edit.steps.map(s=>s.alias),["lights","shutters","state"]);assert.equal(ui.edit.output,"{shutters.success} {state.n}");
  await ui.act("move-step-up","1");assert.deepEqual(ui.edit.steps.map(s=>s.alias),["shutters","lights","state"]);
});

test("non-string step names remain exact and block mode changes instead of being coerced or dropped",async()=>{
  const ui=studio();ui.edit={steps:[{alias:42,kind:"action",expert:false,action:"light-on",target_kind:"source_area"}]};ui.editorMode="wizard";ui.editingStepIndex=null;ui.render=()=>{};
  assert.equal(ui.normalizeSteps({steps:[{alias:42,operation:"light.set_power",target:{kind:"source_area"},arguments:{on:true}}]})[0].alias,42);
  await ui.act("convert-to-expert");assert.equal(ui.editorMode,"wizard");assert.equal(ui.edit.steps[0].alias,42);assert.equal(ui.conversionBlockers[0].code,"invalid_step_alias");
});

test("collapsed cards summarize Guided, Expert and Jinja steps and expose workflow buttons",()=>{
  const ui=studio();ui.edit={steps:[]};
  const guided={alias:"lights",kind:"action",expert:false,action:"light-on",target_kind:"source_area",target_value:null};
  const expert={alias:"shutters",kind:"action",expert:true,operation:"cover.close_cover",target:{kind:"all_allowed"},arguments:{}};
  const jinja={alias:"ceiling",kind:"action",expert:true,operation:"light.set_power",target:{kind:"template",value:"{{ [] }}"},arguments:{on:true}};
  ui.edit.steps=[guided,expert,jinja];ui.stepBlockers=[{step_index:1,code:"operation_arguments"},{step_index:2,code:"template_target"}];
  const html=ui.stepListView();assert.match(html,/Schritt 1/);assert.match(html,/lights/);assert.match(html,/cover\.close_cover/);assert.match(html,/all_allowed/);assert.match(html,/Expert-only/);assert.match(html,/Jinja/);assert.match(html,/data-do="edit-step"/);assert.match(html,/data-do="move-step-up"/);assert.match(html,/data-do="move-step-down"/);assert.match(html,/data-do="remove-step"/);
});

test("MCP counters open concrete spell findings and jump into the shared spell editor",async()=>{
  const ui=studio();ui.mcpReport={projectable:1,not_projectable:1,requires_unrestricted:1,spells:[{id:"lumos",projectable:false,skip_reason:"unknown_operation:x"},{id:"other",projectable:true,execution_kind:"assist_redirect",requires_unrestricted_bridge:true,grant_reason:"assist_redirect_requires_unrestricted_policy"}]};
  assert.match(ui.mcpPanelView(),/data-do="mcp-detail"/);await ui.act("mcp-detail","not_projectable");assert.equal(ui.mcpDetail,"not_projectable");assert.match(ui.mcpDetailView("not_projectable"),/lumos/);assert.match(ui.mcpDetailView("not_projectable"),/data-do="mcp-open"/);
});

test("spell editing uses a compact book context while keeping details reachable",()=>{
  const ui=studio();ui.edit={id:"lumos",name:"Lumos",steps:[]};ui.editExisting=true;ui.stepsWizardView=()=>'<section id="spell-editor"></section>';ui.bookDetailsExpanded=false;
  const compact=ui.bookView();assert.match(compact,/book-edit-context/);assert.doesNotMatch(compact,/book-hero/);assert.match(compact,/toggle-book-details/);
  ui.bookDetailsExpanded=true;assert.match(ui.bookView(),/book-hero/);
});

test("studio assets share the release cache-bust version",async()=>{
  const js=await readFile(new URL("../custom_components/hase/frontend/studio.js",import.meta.url),"utf8");const api=await readFile(new URL("../custom_components/hase/studio_api.py",import.meta.url),"utf8");
  assert.equal([...js.matchAll(/\?v=(0\.3\.27)/g)].length,4);assert.equal([...api.matchAll(/studio\.js\?v=0\.3\.27/g)].length,2);assert.doesNotMatch(js,/audit\d|steps\d|access\d/);
});

test("audit: source capture retains target kind through reopening and collection",async()=>{
  const ui=studio();ui.edit={steps:ui.normalizeSteps({sources:[{kind:"count",target:{kind:"area_name",capture:"area"},variable:"n"}]},[false])};
  ui.value=id=>id==="triggers"?"audit {area}":"";
  const source=(await ui.collectSteps()).steps[0].sources[0];assert.equal(source.target_kind,"area_name");assert.equal(source.capture,"area");assert.ok(source.expert);
});

test("audit: existing Expert variant is editable and explicitly removed as null",async()=>{
  const ui=studio();ui.editorMode="expert";ui.edit={id:"audit",steps:[],expert:{operation:"light.turn_on",arguments:{},target:{kind:"template",value:"{{ [] }}"}}};
  assert.ok(ui.expertVariantView().includes('id="expert-variant-json"'));
  ui.value=()=>"";assert.deepEqual((await ui.collectSteps()).expert_variant,ui.edit.expert);
  ui.edit.expert=null;assert.equal((await ui.collectSteps()).expert_variant,null);
});

test("audit: local hints are immediate advice and use backend capability metadata",()=>{
  const ui=studio();ui.info.actions[0].capability_hint="rgb";ui.registry.entities=[{id:"light.x",capabilities:{rgb:false}}];
  ui.edit={steps:[{kind:"action",action:"light-on",target_kind:"entity_id",target_value:"light.x"}]};
  ui.value=id=>id==="triggers"?"audit {unused}":"";
  assert.ok(ui.localHintMessages().some(m=>m.includes("{unused}")));assert.ok(ui.localHintMessages().some(m=>m.includes("Unterstützung")));assert.ok(ui.localHintMessages().some(m=>m.includes("Ohne Namen")));
  ui.value=id=>id==="spell-output"?"{unused}":id==="triggers"?"audit {unused}":"Audit";
  assert.ok(!ui.localHintMessages().some(m=>m.includes("{unused}")));
});

test("audit: English blocker UI uses codes, never German-message parsing",()=>{
  const ui=studio();ui.language="en";ui.conversionBlockers=[{code:"template_target",step_index:10,path:"target.value",message:"Arbitrary backend text"}];
  const html=ui.conversionBlockersView();for(const text of ["Step 11","Go to issue","Template targets","Check again"])assert.ok(html.includes(text));assert.ok(!html.includes("Arbitrary backend text"));
});


test("audit: render never restores another card's shared field IDs",()=>{
  const ui=studio();ui.loaded=true;ui.edit={id:"audit",steps:[]};ui.renderedEditId="audit";ui.bookView=()=>"";ui.topbarToolsView=()=>"";ui.modalView=()=>"";
  const fields=[{id:"step-alias",value:"previous-step",closest:()=>({})},{id:"spell-name",value:"draft name",closest:()=>null}];
  const writes=[];ui.shadowRoot.querySelectorAll=selector=>selector==="input,textarea,select"?fields:[];
  ui.shadowRoot.getElementById=id=>["step-alias","spell-name"].includes(id)?{set value(value){writes.push([id,value]);}}:null;
  Studio.prototype.render.call(ui);
  assert.deepEqual(writes,[["spell-name","draft name"]]);
});

test("audit: structural edits are dirty even without typing",async()=>{
  const ui=studio();ui.ensureAreas=async()=>{};ui.edit={steps:[]};
  await ui.act("add-step","action");assert.equal(ui.editorDirty,true);
});

test("audit: extended target options and source fields survive collection",async()=>{
  const ui=studio();ui.edit={steps:ui.normalizeSteps({steps:[
    {operation:"light.set_power",arguments:{on:true},target:{kind:"source_area",extra:{nested:true}}},
    {sources:[{kind:"count",target:{kind:"area_name",capture:"area",fallback_source_area:true},variable:"n",extension:{nested:[1,2]}}]}
  ]},[false,false])};ui.value=id=>id==="triggers"?"audit {area}":"";
  const steps=(await ui.collectSteps()).steps;assert.deepEqual(steps[0].target_extras,{extra:{nested:true}});assert.deepEqual(steps[1].sources[0].target_extras,{fallback_source_area:true});assert.deepEqual(steps[1].sources[0].source_extras,{extension:{nested:[1,2]}});
});

test("audit: selected targets absent from a lazy registry remain selectable",()=>{
  const ui=studio();assert.ok(ui.stepTargetFieldView("step-target","entity_id","light.missing").includes('value="light.missing" selected'));
  assert.ok(ui.stepTargetFieldView("step-target","entity_ids",["light.missing"]).includes('value="light.missing" checked'));
});


test("audit: unknown stored operations are retained in the technical select",()=>{
  const ui=studio();ui.editorMode="expert";ui.info.operations=["light.turn_on"];ui.edit={steps:[{kind:"action",expert:true,operation:"custom.unknown",target:{kind:"all_allowed"},arguments:{extension:true}}]};
  assert.ok(ui.stepEditorView(ui.edit.steps[0],0).includes('value="custom.unknown" selected'));
});


test("audit: lossless Expert conversion recognizes reordered JSON object keys",()=>{
  const ui=studio();ui.info.actions=[{id:"dim",operation:"light.turn_on",arguments:{brightness_pct:{capture:"value",type:"int",min:0,max:100}},targets:["all_allowed"]}];
  const steps=ui.normalizeSteps({operation:"light.turn_on",arguments:{brightness_pct:{max:100,min:0,type:"int",capture:"value"}},target:{kind:"all_allowed"}},[false]);
  assert.equal(steps[0].expert,false);assert.equal(steps[0].action,"dim");
});

function installStepFields(ui,values){
  ui.shadowRoot.getElementById=id=>Object.hasOwn(values,id)?{value:values[id],setCustomValidity(){},focus(){},reportValidity(){}}:null;
  ui.shadowRoot.querySelectorAll=()=>[];
}

test("state model: Guided free fields roundtrip is lossless and commits only on Apply",async()=>{
  const ui=studio();ui.editorMode="expert";ui.info.operations=["light.set_power"];
  ui.edit={steps:[{alias:"lights",kind:"action",expert:false,action:"light-on",operation:"light.set_power",arguments:{on:true},target_kind:"source_area",target_value:null}]};ui.openStepEditor(0);
  await ui.change({target:{id:"step-expert",checked:true,dataset:{}}});
  assert.equal(ui.edit.steps[0].expert,false);assert.equal(ui.editingStepDraft.expert,true);
  await ui.change({target:{id:"step-expert",checked:false,dataset:{}}});
  assert.equal(ui.editingStepDraft.expert,false);assert.equal(ui.editingStepDraft.action,"light-on");assert.equal(ui.edit.steps[0].expert,false);
  assert.equal(await ui.stepsApplyDraft(),true);assert.equal(ui.edit.steps[0].alias,"lights");assert.equal(ui.edit.steps[0].target_kind,"source_area");
});

test("state model: an exact Expert preset returns to Guided without backend mutation",async()=>{
  const ui=studio();ui.editorMode="expert";ui.info.operations=["light.set_power"];
  ui.edit={steps:[{alias:"lights",kind:"action",expert:true,operation:"light.set_power",arguments:{on:true},target:{kind:"source_area"}}]};ui.openStepEditor(0);
  await ui.change({target:{id:"step-expert",checked:false,dataset:{}}});
  assert.equal(ui.edit.steps[0].expert,true,"canonical state is unchanged before Apply");assert.equal(ui.editingStepDraft.expert,false);assert.equal(ui.editingStepDraft.action,"light-on");
});

test("state model: preset repair previews, confirms and preserves alias plus compatible capture",async()=>{
  const ui=studio();ui.editorMode="expert";ui.info.actions.push({id:"light-off",label:"Licht ausschalten",operation:"light.turn_off",arguments:{},targets:["area_name","source_area"],target_capture:"area"});ui.info.operations=["light.set_power","light.turn_off"];
  const original={alias:"lights",kind:"action",expert:true,operation:"light.set_power",arguments:{on:false},target:{kind:"area_name",capture:"area"}};ui.edit={steps:[structuredClone(original)]};ui.openStepEditor(0);
  installStepFields(ui,{"step-alias":"lights","step-expert-operation":"light.set_power","step-expert-target-kind":"area_name","step-expert-target-capture":"area","step-expert-arguments":"{\"on\":false}","triggers":"licht aus {area}","step-repair-action":"light-off"});
  let confirmation="";ui.askConfirmation=async text=>(confirmation=text,true);
  await ui.act("repair-step-preset");
  assert.match(confirmation,/light\.set_power/);assert.match(confirmation,/light\.turn_off/);assert.equal(ui.edit.steps[0].expert,false);assert.equal(ui.edit.steps[0].action,"light-off");assert.equal(ui.edit.steps[0].alias,"lights");assert.equal(ui.edit.steps[0].target_kind,"area_name");assert.equal(ui.edit.steps[0].target_capture,"area");assert.deepEqual(ui.edit.steps[0].arguments,{});
});

test("state model: cancelling a lossy preset repair leaves the canonical step byte-for-byte unchanged",async()=>{
  const ui=studio();ui.editorMode="expert";ui.info.actions.push({id:"light-off",label:"Licht ausschalten",operation:"light.turn_off",arguments:{},targets:["source_area"]});ui.info.operations=["light.set_power","light.turn_off"];
  const original={alias:"lights",kind:"action",expert:true,operation:"light.set_power",arguments:{on:false},target:{kind:"source_area"}};ui.edit={steps:[structuredClone(original)]};ui.openStepEditor(0);
  installStepFields(ui,{"step-alias":"lights","step-expert-operation":"light.set_power","step-expert-target-kind":"source_area","step-expert-arguments":"{\"on\":false}","triggers":"licht aus","step-repair-action":"light-off"});ui.askConfirmation=async()=>false;
  await ui.act("repair-step-preset");assert.deepEqual(ui.edit.steps[0],original);
});

test("state model: cleared Expert arguments replace stale data through canonical payload and reload",async()=>{
  const ui=studio();ui.editorMode="expert";ui.info.operations=["light.set_power"];ui.edit={id:"x",steps:[{alias:"lights",kind:"action",expert:true,operation:"light.set_power",arguments:{on:true},target:{kind:"source_area"}}]};ui.openStepEditor(0);
  ui.api=async()=>({ok:true,issues:[]});
  installStepFields(ui,{"step-alias":"lights","step-expert-operation":"light.set_power","step-expert-target-kind":"source_area","step-expert-arguments":"   ","triggers":"licht","spell-name":"Licht","spell-description":"","spell-examples":"","tags":"","spell-output":"","spell-catchphrase":"","output-mode":"AUTO"});
  assert.equal(await ui.stepsApplyDraft(),true);assert.deepEqual(ui.edit.steps[0].arguments,{});assert.equal(ui.exactGuidedStep(ui.edit.steps[0]),null,"classification uses the new canonical state");
  ui.editingStepIndex=null;ui.editingStepDraft=null;const payload=await ui.collectSteps();assert.deepEqual(payload.steps[0].arguments,{});assert.deepEqual(ui.normalizeSteps({operation:payload.steps[0].operation,target:payload.steps[0].target,arguments:payload.steps[0].arguments})[0].arguments,{});
});

test("state model: invalid JSON stays local (never reaches the backend) and never partially commits",async()=>{
  const ui=studio();ui.editorMode="expert";ui.info.operations=["light.set_power"];ui.info.operation_targets={"light.set_power":["source_area"]};
  ui.api=async()=>assert.fail("malformed JSON must be rejected locally, before any backend round trip");
  const original={alias:"lights",kind:"action",expert:true,operation:"light.set_power",arguments:{on:true},target:{kind:"source_area"}};ui.edit={steps:[structuredClone(original)]};ui.openStepEditor(0);
  installStepFields(ui,{"step-alias":"lights","step-expert-operation":"light.set_power","step-expert-target-kind":"source_area","step-expert-arguments":"{bad","triggers":"licht"});assert.equal(await ui.stepsApplyDraft(),false);assert.deepEqual(ui.edit.steps[0],original);assert.equal(ui.editingStepIndex,0);assert.equal(ui.stepEditorError.field,"step-expert-arguments");
});

test("state model: a target kind the backend rejects for this operation stays local and never partially commits",async()=>{
  const ui=studio();ui.editorMode="expert";ui.info.operations=["light.set_power"];ui.info.operation_targets={"light.set_power":["source_area"]};
  // The business rule itself (is "template" an allowed target kind for
  // light.set_power?) now lives only in ActionExecutor.describe_shape,
  // reached here through "validate_step" (scope: action_draft) - not
  // re-implemented in validateStepCandidate.
  ui.api=async(op,data)=>op==="validate_step"&&data.scope==="action_draft"&&data.draft?.target?.kind==="template"
    ?{ok:false,issues:[{code:"target_kind_not_allowed",severity:"error",path:"target.kind",message:"Zieltyp „template“ ist für „light.set_power“ nicht zulässig."}]}
    :{ok:true,issues:[]};
  const original={alias:"lights",kind:"action",expert:true,operation:"light.set_power",arguments:{on:true},target:{kind:"source_area"}};ui.edit={steps:[structuredClone(original)]};ui.openStepEditor(0);
  installStepFields(ui,{"step-alias":"lights","step-expert-operation":"light.set_power","step-expert-target-kind":"template","step-expert-target-value":"{{ [] }}","step-expert-arguments":"{\"on\":true}","triggers":"licht"});
  assert.equal(await ui.stepsApplyDraft(),false);assert.deepEqual(ui.edit.steps[0],original);assert.equal(ui.stepEditorError.field,"step-expert-target-kind");
});

test("state model: a capture not yet declared in any trigger is no longer a local Step 2 block - Step 3 owns capture<->trigger consistency",async()=>{
  const ui=studio();ui.editorMode="expert";ui.info.operations=["light.set_power"];
  ui.api=async()=>({ok:true,issues:[]});
  const original={alias:"lights",kind:"action",expert:true,operation:"light.set_power",arguments:{on:true},target:{kind:"source_area"}};ui.edit={steps:[structuredClone(original)]};ui.openStepEditor(0);
  installStepFields(ui,{"step-alias":"lights","step-expert-operation":"light.set_power","step-expert-target-kind":"area_name","step-expert-target-capture":"room","step-expert-arguments":"{\"on\":true}","triggers":"licht aus"});
  assert.equal(await ui.stepsApplyDraft(),true,"Step 2's Apply no longer duplicates the trigger<->capture consistency rule");
  assert.equal(ui.edit.steps[0].target.capture,"room");
});

test("editor navigation: Cancel and top Back share one dirty guard",async()=>{
  const clean=studio();clean.edit={id:"x",steps:[]};clean.editorDirty=false;let cleanAsked=0;clean.askConfirmation=async()=>{cleanAsked++;return true;};await clean.act("close-editor");assert.equal(cleanAsked,0);assert.equal(clean.edit,null);
  const discard=studio();discard.edit={id:"x",steps:[]};discard.editorDirty=true;discard.askConfirmation=async()=>true;await discard.act("close-editor");assert.equal(discard.edit,null);
  const stay=studio();stay.edit={id:"x",steps:[]};stay.editorDirty=true;stay.askConfirmation=async()=>false;await stay.act("close-editor");assert.ok(stay.edit);assert.equal(stay.editorDirty,true);
  stay.wizardStep=4;stay.wizardMaxStep=4;const html=stay.stepsWizardView();assert.match(html,/data-do="wizard-back"/);assert.match(html,/data-do="close-editor"/);assert.match(html,/data-do="save"/);assert.equal((stay.compactBookContextView().match(/data-do="close-editor"/g)||[]).length,1);
});

// ── Guided Wizard: Gerätetyp hydration + domain-correct target filtering ──
const lightAct={id:"light-on",label:"Licht an",operation:"light.turn_on",arguments:{},targets:["entity_id","entity_ids"],domain_filter:"light"};
const coverAct={id:"cover-open",label:"Öffnen",operation:"cover.open_cover",arguments:{},targets:["entity_id","entity_ids"],domain_filter:"cover"};
const genericAct={id:"get-state",label:"Zustand",operation:"entity.get_state",arguments:{},targets:["entity_id"],domain_filter:null};
const mixedEntities=[
  {id:"todo.inbox",name:"Inbox"},{id:"todo.einkaufen",name:"Einkaufen"},
  {id:"light.kueche",name:"Küche"},{id:"light.diffuser",name:"Diffuser"},{id:"cover.rollo",name:"Rollo"},
];
function guidedStudio(steps){
  const ui=studio();
  ui.info.actions=[todoListAction,lightAct,coverAct,genericAct];
  ui.registry.entities=mixedEntities;
  ui.edit={id:"s",trigger:"t",tags:[],steps};
  ui.editorMode="wizard";ui.wizardStep=1;
  return ui;
}
const step=(action,target_kind="entity_id",target_value=null)=>({kind:"action",expert:false,action,target_kind,target_value});
const selectedDomain=html=>{const m=html.match(/<select id="step-action-domain">([\s\S]*?)<\/select>/);return m?(m[1].match(/<option value="([^"]*)" selected/)||[])[1]:undefined;};
const entityIdsIn=html=>[...html.matchAll(/(?:todo|light|cover)\.[a-z_]+/g)].map(m=>m[0]);

test("editing an existing step reconstructs its saved Gerätetyp instead of 'Alle'", () => {
  const ui=guidedStudio([step("light-on","entity_id","light.kueche")]);
  ui.wizardActionDomain="stale";           // leftover from a previously edited step
  ui.openStepEditor(0);
  assert.equal(selectedDomain(ui.stepActionSubForm(ui.editingStepDraft,lightAct,"entity_id")),"light");
});
test("combined spell: every step loads its own Gerätetyp", () => {
  const ui=guidedStudio([step(todoListAction.id,"entity_id","todo.inbox"),step("cover-open","entity_id","cover.rollo"),step("get-state","entity_id","light.kueche")]);
  const expected=["todo","cover",undefined];
  [todoListAction,coverAct,genericAct].forEach((action,i)=>{
    ui.openStepEditor(i);
    const dom=selectedDomain(ui.stepActionSubForm(ui.editingStepDraft,action,"entity_id"));
    // genuinely generic step -> "Alle" ("" option selected or none marked)
    assert.equal(dom||undefined,expected[i]||undefined,`step ${i}`);
  });
});
test("edit -> save without change leaves the step untouched", () => {
  const ui=guidedStudio([step("light-on","entity_id","light.kueche")]);
  const before=JSON.stringify(ui.edit.steps);
  ui.openStepEditor(0);
  ui.stepActionSubForm(ui.editingStepDraft,lightAct,"entity_id");
  assert.equal(JSON.stringify(ui.edit.steps),before);
  assert.equal(JSON.stringify(ui.editingStepDraft),before.slice(1,-1));
});
test("todo / light / cover actions offer only their own domain's targets", () => {
  const ui=guidedStudio([]);
  for(const [action,prefix] of [[todoListAction,"todo."],[lightAct,"light."],[coverAct,"cover."]]){
    for(const kind of ["entity_id","entity_ids"]){
      const ids=entityIdsIn(ui.stepTargetFieldView("step-target",kind,null,action));
      assert.ok(ids.length>0,`${prefix} ${kind} has targets`);
      assert.ok(ids.every(id=>id.startsWith(prefix)),`${prefix} ${kind}: ${ids}`);
    }
  }
});
test("domain is derived from the operation when the stored Gerätetyp is missing", () => {
  const ui=guidedStudio([]);
  const noFilter={...todoListAction,domain_filter:null};
  const ids=entityIdsIn(ui.stepTargetFieldView("step-target","entity_id",null,noFilter));
  assert.deepEqual(ids.sort(),["todo.einkaufen","todo.inbox"]);
});
test("switching action re-filters targets immediately (Todoist inbox / Bring einkaufen stay, lights vanish)", () => {
  const ui=guidedStudio([]);
  assert.ok(ui.entitiesForAction(todoListAction).every(e=>["todo.inbox","todo.einkaufen"].includes(e.id)));
  assert.ok(ui.entitiesForAction(lightAct).every(e=>e.id.startsWith("light.")));
});
test("targets of a no-longer-valid domain are dropped, not silently kept", () => {
  const ui=guidedStudio([]);
  const single=ui.stepTargetFieldView("step-target","entity_id","light.kueche",todoListAction);
  assert.ok(!single.includes("light.kueche"));
  const multi=ui.stepTargetFieldView("step-target","entity_ids",["todo.inbox","light.kueche"],todoListAction);
  assert.ok(!multi.includes("light.kueche"));
  assert.ok(/value="todo\.inbox"[^>]*checked/.test(multi));
});
test("changing Gerätetyp switches action, clears invalid target and refilters the list", async () => {
  const ui=guidedStudio([step("light-on","entity_id","light.kueche")]);
  ui.openStepEditor(0);
  ui.value=id=>({"step-target-kind":"entity_id","step-alias":"","step-action":"light-on","step-target-entity":"light.kueche"})[id]||"";
  ui.ensureEntitiesForAction=async()=>{};
  await ui.changeStepDomain("todo");
  assert.equal(ui.editingStepDraft.operation,"todo.list_items");
  assert.equal(ui.editingStepDraft.target_value,null);
  const html=ui.stepTargetFieldView("step-target","entity_id",ui.editingStepDraft.target_value,todoListAction);
  assert.ok(!html.includes("light."));
});

test("edit -> save of an existing guided todo spell keeps its status filter", () => {
  const open={id:"todo-list:0",label:"Todo-Liste lesen",operation:"todo.list_items",arguments:{status:"needs_action"},targets:["entity_id"],domain_filter:"todo"};
  const all={id:"todo-list:1",label:"Todo-Liste lesen (alle Einträge)",operation:"todo.list_items",arguments:{},targets:["entity_id"],domain_filter:"todo"};
  const ui=studio();ui.info.actions=[open,all];
  ui.edit={steps:[]};
  const raw=args=>ui.normalizeStepFromAction({kind:"action",operation:"todo.list_items",target:{kind:"entity_id",value:"todo.inbox"},arguments:args},false,false);
  const filtered=raw({status:"needs_action"}),unfiltered=raw({});
  assert.equal(filtered.expert,false);assert.equal(filtered.action,"todo-list:0");assert.deepEqual(filtered.arguments,{status:"needs_action"});
  assert.equal(unfiltered.expert,false);assert.equal(unfiltered.action,"todo-list:1");assert.deepEqual(unfiltered.arguments,{});
});
