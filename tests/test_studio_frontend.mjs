import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import vm from "node:vm";
import {studioLanguage, translate, translateMessage, localizedContent, de, en} from "../custom_components/hase/frontend/studio-i18n.js";
import {bookCover, bookTheme, safeCoverPath, filterBooks} from "../custom_components/hase/frontend/studio-visuals.js";

let Studio;
globalThis.HTMLElement = class {
  attachShadow() {this.shadowRoot = {addEventListener() {},getElementById() {return null;},querySelectorAll() {return [];}};}
};
globalThis.customElements = {get() {}, define(name, ctor) {Studio = ctor;}};
await import("../custom_components/hase/frontend/studio.js");
const spell = {id:"lumos", name:"Lumos", trigger:"Lumos", operation:"light.set_power", arguments:{on:true}, target:{kind:"source_area"}, tags:[]};
function studio() {
  const ui = new Studio();
  ui.language="de";ui.bookLanguage="de";
  ui.info = {books:[{id:"potter",name:"Potterverse",count:1,active_clients:2,capability:"USER + EXPERT",expert_allowed:false}],actions:[{id:"light-on",label:"Licht einschalten",operation:"light.set_power",arguments:{on:true},targets:["source_area"]}]};
  ui.current = {default_language:"de",languages:["de","en"],book:{id:"potter",name:"Potterverse",default_language:"de",languages:["de","en"],commands:[spell]}, profiles:[{id:"client",name:"Client"}],capability:"USER + EXPERT",spell_capabilities:{lumos:"USER"}};
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
  assert.equal(bookTheme({id:"potter",metadata:{visual:{theme:"sapphire"}}}),"sapphire");
  assert.equal(filterBooks([{id:"hdr",name:"Middle Earth"},{id:"potter",name:"Potterverse"}],"EARTH").length,1);
});
test("overview keeps count, clients, permissions and original book action", () => {
  const html=studio().homeView();
  for (const text of ["1 Spells","2 zugeordnete Clients","USER + EXPERT","AUS",'data-id="potter"','data-do="new-book"','data-do="import-new"','data-do="check-all"','data-do="refresh"']) assert.ok(html.includes(text),text);
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
  assert.deepEqual(request.data,{book_id:"potter",revision:"r1",profile_id:"client",source_area_id:"living",language:"de",book_language:"de"});
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
  const ui=studio();ui.language="en";ui.expert=true;ui.edit={...spell};ui.editExisting=true;ui.info.operations=["light.set_power"];
  const html=[ui.homeView(),ui.bookView(),ui.listView(),ui.editorView(),ui.metadataView(),ui.importView(),ui.newImportView()].join("\n");
  for(const label of ["Create spellbook","assigned clients","Test with client","Save","Check template / targets","When I say","Import into Potterverse","Turn on lights"]) assert.ok(html.includes(label),label);
  for(const label of ["Spellbook erstellen","Expert-Ausführung:","Nicht angegeben","Licht einschalten","Speichern","Wenn ich sage"]) assert.ok(!html.includes(label),label);
  assert.equal(translateMessage("en","Doppelter Trigger mit nox."),"Duplicate trigger with nox.");
  assert.equal(translateMessage("en","Ein Buchname ist erforderlich."),"A book name is required.");
  assert.equal(translateMessage("en","Die Aktion „Licht dimmen“ benötigt {value} in jeder Trigger-Formulierung."),"The action “Dim lights” requires {value} in every trigger phrase.");
  assert.equal(translate("en","Expert-Ausführung für „{name}“ {state}?",{name:"Potter",state:"Enable"}),"Enable Expert execution for “Potter”?");
  assert.equal(localizedContent({de:"Licht",en:"Light"},"en"),"Light");
  assert.equal(localizedContent({de:"Licht",en:"Light"},"fr"),"Light");
});

test("editing an English trigger preserves the German trigger in Expert preview", () => {
  const ui=studio();ui.expert=true;ui.editExisting=true;ui.editLanguage="en";ui.bookLanguage="en";
  ui.edit={...spell,trigger:{de:"Licht an",en:"Lights on"}};
  const fields={"spell-name":"Lumos",tags:"",fallback:"keep",action:"light-on",triggers:"Illuminate","target-kind":"source_area","expert-kind":"template",template:"{{ [] }}","expert-operation":"light.set_power",arguments:'{"on":true}'};
  ui.value=id=>fields[id] || "";
  ui.shadowRoot.querySelectorAll=()=>[];ui.shadowRoot.getElementById=()=>({checked:true});
  assert.deepEqual(ui.collectSpell(true).spell.trigger,{de:"Licht an",en:"Illuminate"});
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
  const ui=studio();ui.edit=structuredClone(spell);ui.edit.trigger={de:"Alt",en:"Old"};
  const fields={"spell-name":"Name DE",triggers:"Neu\nAlias","spell-description":"Beschreibung",tags:"tag","target-kind":"source_area",action:"light-on"};
  ui.value=id=>fields[id]||"";
  ui.shadowRoot.querySelectorAll=()=>[];ui.shadowRoot.getElementById=()=>null;
  await ui.act("book-language","en");
  assert.deepEqual(ui.edit.trigger,{de:"Neu",en:"Old"});
  assert.deepEqual(ui.edit.triggers,{de:["Alias"]});
  fields.triggers="New";fields["spell-name"]="Name EN";
  const payload=ui.collectSpell();
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
    for(const action of ["test-edit","test-template","execute"])await assert.rejects(ui.act(action),/konkrete Testphrase/);
  }
  ui.value=()=>"lumos Gondor";assert.equal(ui.testPhrase(),"lumos Gondor");
});
test("concrete test phrase submits the current toggle draft even when Expert persistence is off", async () => {
  const ui=studio();ui.expert=true;ui.editExisting=true;ui.edit={...spell,trigger:"lumos {area}"};
  const fields={"spell-name":"Lumos",fallback:"keep",action:"light-on",triggers:"lumos {area}",phrase:"lumos Gondor","target-kind":"source_area","expert-kind":"area_name","expert-operation":"light.toggle",arguments:"{}"};
  ui.value=id=>fields[id]||"";ui.shadowRoot.querySelectorAll=()=>[];ui.shadowRoot.getElementById=()=>({checked:false});
  let sent;ui.api=async(operation,data)=>{sent={operation,data};return {};};
  await ui.act("test-template");
  assert.equal(sent.data.spell.expert.operation,"light.toggle");
  assert.deepEqual(sent.data.spell.expert.target,{kind:"area_name",capture:"area"});
  assert.equal(sent.data.phrase,"lumos Gondor");assert.equal(sent.data.variant,"expert");
  assert.equal(sent.data.persist_expert,false);
});


test("placeholder chips appear in both new and existing spell editors", () => {
  const ui=studio();ui.edit=structuredClone(spell);
  for(const existing of [false,true]){ui.editExisting=existing;const html=ui.editorView();assert.ok(html.includes('data-do="insert-placeholder"'));assert.ok(html.includes('data-id="area"'));assert.ok(html.includes('data-id="value"'));assert.ok(html.includes('data-id="entity"'));}
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

test("New spell top-level choice is Wizard vs Manual; Manual still creates the unchanged single-action draft directly", async () => {
  const ui=studio();ui.ensureEditorTargets=async()=>{};
  await ui.act("new-spell");
  assert.equal(ui.edit,undefined);assert.equal(ui.newSpellChoice,true);assert.equal(ui.newSpellStep,0);
  const choice=ui.newSpellChoiceView();
  assert.ok(choice.includes('data-do="new-spell-wizard-menu"'));
  assert.ok(choice.includes('data-do="new-spell-manual"'));
  assert.ok(!choice.includes('data-do="new-spell-wizard"'),"the single-action and combined choices only appear after picking Wizard");
  assert.ok(!choice.includes('data-do="new-spell-steps"'));
  await ui.act("new-spell-manual");
  assert.equal(ui.editorMode,"manual");
  assert.deepEqual({trigger:ui.edit.trigger,target:ui.edit.target,operation:ui.edit.operation,arguments:ui.edit.arguments},{trigger:"",target:{kind:"area_id"},operation:"light.turn_on",arguments:{}});
  assert.equal(ui.editorDirty,false);
});

test("New spell Wizard choice opens a second screen offering a single action/query or Actions & Queries, with a working back button", async () => {
  const ui=studio();ui.ensureEditorTargets=async()=>{};
  await ui.act("new-spell");
  ui.value=id=>id==="new-spell-language"?"en":"";
  await ui.act("new-spell-wizard-menu");
  assert.equal(ui.newSpellStep,1);
  let choice=ui.newSpellChoiceView();
  assert.ok(choice.includes('data-do="new-spell-wizard"'));
  assert.ok(choice.includes('data-do="new-spell-steps"'));
  assert.ok(choice.includes('data-do="new-spell-back"'));
  await ui.act("new-spell-back");
  assert.equal(ui.newSpellStep,0);
  choice=ui.newSpellChoiceView();
  assert.ok(choice.includes('data-do="new-spell-wizard-menu"'),"back must return to the top-level Wizard/Manual choice");
});

test("Combined Spells: New spell Wizard choice offers Actions & Queries (via the sub-menu) and initializes an empty steps list", async () => {
  const ui=studio();ui.ensureEditorTargets=async()=>{};
  await ui.act("new-spell");
  await ui.act("new-spell-wizard-menu");
  const choice=ui.newSpellChoiceView();
  assert.ok(choice.includes('data-do="new-spell-steps"'));
  await ui.act("new-spell-steps");
  assert.equal(ui.editorMode,"steps");
  assert.deepEqual(ui.edit.steps,[]);
});

test("New spell: the language chosen on the top-level screen survives into the Wizard sub-menu", async () => {
  const ui=studio();ui.ensureEditorTargets=async()=>{};
  await ui.act("new-spell");
  ui.value=id=>id==="new-spell-language"?"en":"";
  await ui.act("new-spell-wizard-menu");
  ui.value=()=>"";
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
  const payload=ui.collectSteps();
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
  const payload=ui.collectSteps();
  assert.equal(payload.steps[0].alias,"open_contacts");
  assert.deepEqual(payload.steps[0].sources[0],{kind:"count",target_kind:"all_allowed",target_value:null,domain_filter:"binary_sensor",device_class:undefined,attribute:undefined,state_filter:"on",variable:"count",unit_variable:undefined});
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
  ui.ensureEditorTargets=async()=>{};
  await ui.act("edit","night");
  assert.equal(ui.editorMode,"steps");
  assert.equal(ui.edit.steps.length,1);
});

test("Combined Spells: reopening a saved spell converts raw stored steps into the editor's internal shape", async () => {
  const ui=studio();
  const stepsSpell={id:"night",name:"Gute Nacht",trigger:"gute nacht",steps:[
    {alias:"a",operation:"light.set_power",target:{kind:"area_id",value:"wohnzimmer"},arguments:{on:true}},
    {alias:"b",sources:[{kind:"count",target:{kind:"area_id",value:"wohnzimmer"},variable:"count",domain_filter:"light",state_filter:"on"}]},
  ]};
  ui.current.book.commands.push(stepsSpell);
  ui.ensureEditorTargets=async()=>{};
  await ui.act("edit","night");
  assert.equal(ui.editorMode,"steps");
  assert.deepEqual(ui.edit.steps[0],{alias:"a",kind:"action",action:"light-on",target_kind:"area_id",target_value:"wohnzimmer"});
  assert.deepEqual(ui.edit.steps[1],{alias:"b",kind:"query",sources:[{kind:"count",target_kind:"area_id",target_value:"wohnzimmer",domain_filter:"light",device_class:"",attribute:"",state_filter:"on",variable:"count",unit_variable:""}]});
  const html=ui.stepListView();
  assert.ok(html.includes("Licht einschalten"),"reopened action step must show its real action label, not a blank one");
  assert.equal(ui.wizardMaxStep,4,"reopening an existing steps spell must unlock direct navigation to every step, not just Next");
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

test("light.set_color: picking a color visually overrides the preset arguments via the existing __preserve__ mechanism", async () => {
  const ui=studio();
  ui.edit={...spell,operation:"light.set_color",arguments:{rgb_color:[255,0,0]},target:{kind:"all_allowed"}};
  await ui.change({target:{id:"action-color-picker",value:"#7fff00",dataset:{}}});
  assert.equal(ui.edit.operation,"light.set_color");
  assert.deepEqual(ui.edit.arguments,{rgb_color:[127,255,0]});
  // The custom RGB no longer matches any curated preset, so the existing
  // editorActions "__preserve__" row (built for exactly this case) picks
  // it up automatically - no backend change needed to round-trip it.
  const preserved=ui.editorActions.find(a=>a.id==="__preserve__");
  assert.ok(preserved);
  assert.deepEqual(preserved.arguments,{rgb_color:[127,255,0]});
});

test("light.set_color: typing an HA color name overrides rgb_color with color_name", async () => {
  const ui=studio();
  ui.edit={...spell,operation:"light.set_color",arguments:{rgb_color:[255,0,0]},target:{kind:"all_allowed"}};
  await ui.change({target:{id:"action-color-name",value:"chartreuse",dataset:{}}});
  assert.deepEqual(ui.edit.arguments,{color_name:"chartreuse"});
});

test("Wizard uses the shared draft, action catalogue and Manual switch", async () => {
  const ui=studio();ui.edit=structuredClone(spell);ui.editorMode="wizard";
  ui.info.actions=[{id:"dim",label:"Licht dimmen",operation:"light.set_power",arguments:{percentage:{capture:"level"}},targets:["area_name"],target_capture:"room"}];
  const fields={"spell-name":"Aquamenti",triggers:"aquamenti {room} auf {level}","spell-description":"Wasserlicht", "spell-examples":"aquamenti Küche auf 40",tags:"water",action:"dim","target-kind":"area_name","spell-output":"", "spell-catchphrase":"Aquamenti in {room}."};
  ui.value=id=>fields[id]||"";ui.shadowRoot.querySelectorAll=()=>[];
  await ui.act("open-manual");
  assert.equal(ui.editorMode,"manual");
  assert.equal(ui.edit.id,"lumos");
  assert.equal(ui.edit.operation,"light.set_power");
  assert.deepEqual(ui.edit.arguments,{percentage:{capture:"level"}});
  assert.deepEqual(ui.edit.target,{kind:"area_name",capture:"room"});
  assert.equal(ui.edit.trigger.de,"aquamenti {room} auf {level}");
  assert.equal(ui.edit.catchphrase.de,"Aquamenti in {room}.");
});

test("Wizard detects arbitrary captures and flags target placeholder mismatches", () => {
  const ui=studio();ui.edit=structuredClone(spell);
  ui.info.actions=[{id:"dim",label:"Licht dimmen",operation:"light.set_power",arguments:{percentage:{capture:"level"}},targets:["area_name"],target_capture:"room"}];
  const fields={"spell-name":"Dimmer",triggers:"dimme {room}",action:"dim","target-kind":"area_name","target-area":""};
  ui.value=id=>fields[id]||"";ui.shadowRoot.querySelectorAll=()=>[];
  assert.deepEqual(ui.triggerPlaceholderNames(),["room"]);
  assert.ok(ui.wizardLocalIssues().some(issue=>issue.field==="target"&&issue.level==="warning"&&issue.message.includes("{level}")));
  fields.triggers="dimme {room} auf {level}";
  assert.ok(!ui.wizardLocalIssues().some(issue=>issue.message.includes("{level}")));
  fields.triggers="dimme {unused}";
  assert.ok(ui.wizardLocalIssues().some(issue=>issue.message.includes("{unused}")));
});

test("capability-driven filtering warns when the selected device lacks the required capability", () => {
  const ui=studio();ui.edit=structuredClone(spell);
  ui.info.actions=[{id:"set-color",label:"Rot",operation:"light.set_color",arguments:{rgb_color:[255,0,0]},targets:["entity_id"],capability_hint:"rgb"}];
  const fields={"spell-name":"Farbe",triggers:"rot an","action":"set-color","target-kind":"entity_id"};
  ui.value=id=>fields[id]||"";
  ui.registry.entities=[{id:"light.plain",name:"Plain Light",capabilities:{rgb:false}}];
  ui.shadowRoot.querySelectorAll=sel=>sel==='input[name="entity"]:checked'?[{value:"light.plain"}]:[];
  assert.ok(ui.wizardLocalIssues().some(issue=>issue.field==="target"&&issue.level==="warning"&&issue.message.includes("may not support")));
  ui.registry.entities=[{id:"light.rgb",name:"RGB Light",capabilities:{rgb:true}}];
  ui.shadowRoot.querySelectorAll=sel=>sel==='input[name="entity"]:checked'?[{value:"light.rgb"}]:[];
  assert.ok(!ui.wizardLocalIssues().some(issue=>issue.message.includes("may not support")));
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

test("expert argument defaults are derived from backend action metadata, not a per-operation JS table", () => {
  const ui=studio();
  ui.info.actions=[
    {id:"tilt",label:"Lamellen einstellen",operation:"cover.set_cover_tilt_position",arguments:{tilt_position:{capture:"value",type:"int",min:0,max:100}},targets:["entity_id"]},
    {id:"preset",label:"Eco",operation:"climate.set_preset_mode",arguments:{preset_mode:"eco"},targets:["entity_id"]},
  ];
  assert.deepEqual(ui.expertArgumentDefaults("cover.set_cover_tilt_position"),{tilt_position:50});
  assert.deepEqual(ui.expertArgumentDefaults("climate.set_preset_mode"),{preset_mode:"eco"});
  assert.deepEqual(ui.expertArgumentDefaults("nonexistent.operation"),{});
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
  ui.edit={...spell,operation:"light.set_color",arguments:{rgb_color:[127,255,0]},target:{kind:"all_allowed"}};
  ui.editorMode="wizard";ui.wizardStep=2;ui.wizardMaxStep=2;
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  const html=ui.wizardView();
  assert.ok(html.includes('id="action-color-picker"'));
  assert.ok(html.includes('value="#7fff00"'));
});

test("Wizard renders six guided steps over the Manual Editor field IDs", () => {
  const ui=studio();ui.edit=structuredClone(spell);ui.editorMode="wizard";ui.wizardStep=0;
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  const html=ui.wizardView();
  for(const text of ["Grundinformationen","Auslöser","Aktion","Ziel / Auflösung","Antwort","Prüfen &amp; Testen"])assert.ok(html.includes(text),text);
  for(const id of ["spell-name","spell-description","spell-examples","tags","triggers","action","target-kind","target-area","wizard-entity-filter","spell-output","spell-catchphrase","phrase"])assert.ok(html.includes(`id="${id}"`),id);
  assert.ok(html.includes('data-do="open-manual"'));
  assert.ok(html.includes('data-do="wizard-next"'));
});

test("Wizard stepper: current, already-visited, and previously-reached steps are clickable, never-reached steps are not", () => {
  const ui=studio();ui.edit=structuredClone(spell);ui.editorMode="wizard";ui.wizardStep=3;ui.wizardMaxStep=3;
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  const html=ui.wizardView();
  for(const index of [0,1,2,3])assert.ok(html.includes(`data-do="wizard-goto" data-id="${index}"`),`step ${index} clickable`);
  for(const index of [4,5])assert.ok(!html.includes(`data-do="wizard-goto" data-id="${index}"`),`step ${index} not clickable`);
  // The whole circle+label area is one clickable control, not just the number.
  assert.match(html,/<button type="button" class="step-button" data-do="wizard-goto" data-id="0"[^>]*><span>1<\/span>/);
  assert.match(html,/<div class="step-button" aria-disabled="true"><span>5<\/span>/);
});

test("wizard-goto navigates freely within already-reached steps, forward and backward, but refuses to skip past the furthest reached step", async () => {
  const ui=studio();ui.edit=structuredClone(spell);ui.editorMode="wizard";ui.wizardStep=3;ui.wizardMaxStep=3;
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  await ui.act("wizard-goto","1");
  assert.equal(ui.wizardStep,1);
  assert.equal(ui.wizardMaxStep,3,"jumping backward must not shrink the furthest reached step");
  await ui.act("wizard-goto","4");
  assert.equal(ui.wizardStep,1,"a step never yet reached must be refused");
  await ui.act("wizard-goto","3");
  assert.equal(ui.wizardStep,3,"jumping forward again to an already-reached step must work directly, not only via Next");
  await ui.act("wizard-goto","1");
  assert.equal(ui.wizardStep,1,"the current step itself stays reachable");
});

test("wizard-next advances the furthest reached step so it stays unlocked after navigating back", async () => {
  const ui=studio();ui.edit=structuredClone(spell);ui.editorMode="wizard";ui.wizardStep=0;ui.wizardMaxStep=0;
  ui.value=()=>"";ui.shadowRoot.querySelectorAll=()=>[];
  await ui.act("wizard-next");
  await ui.act("wizard-next");
  assert.equal(ui.wizardStep,2);
  assert.equal(ui.wizardMaxStep,2);
  await ui.act("wizard-goto","0");
  assert.equal(ui.wizardMaxStep,2,"going back must not forget steps already reached");
  await ui.act("wizard-goto","2");
  assert.equal(ui.wizardStep,2,"the previously reached step must be directly reachable again");
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

test("Wizard explains automatic output for state queries", () => {
  const ui=studio();
  ui.info.actions=[{id:"get-state",label:"Zustand abfragen",operation:"entity.get_state",arguments:{},targets:["entity_id"],response_fields:{"*":["state","entity_name","entity_id","area"],sensor:["unit"],light:["brightness","brightness_percent"]}}];
  ui.registry.entities=[{id:"sensor.room",name:"Raumtemperatur",area_name:"Wohnzimmer"}];
  ui.edit={...structuredClone(spell),operation:"entity.get_state",arguments:{},target:{kind:"entity_id",value:"sensor.room"}};
  ui.editorMode="wizard";ui.wizardStep=4;ui.wizardHelp.add("response");
  const fields={"spell-name":"Temperatur","triggers":"temperatur","action":"get-state","target-kind":"entity_id","spell-output":"Die Temperatur ist fest.","spell-catchphrase":""};
  ui.value=id=>fields[id]||"";
  ui.shadowRoot.querySelectorAll=selector=>selector==='input[name="entity"]:checked'?[{value:"sensor.room"}]:[];

  const html=ui.wizardView();
  assert.ok(html.includes("Konfigurierbare Zustandsantwort"));
  assert.ok(html.includes("{state}"));
  assert.ok(html.includes("{unit}"));
  assert.ok(html.includes("{entity_name}"));
  assert.ok(html.includes("{area}"));
  assert.ok(!html.includes("{brightness}"));
  assert.ok(ui.wizardLocalIssues().some(issue=>issue.field==="response"&&issue.level==="warning"));
  fields["spell-output"]="In {area} sind es {state} {unit}.";
  assert.ok(!ui.wizardLocalIssues().some(issue=>issue.field==="response"));
});

test("Wizard review uses the shared client and source-area dry-run context", async () => {
  const ui=studio();ui.edit=structuredClone(spell);ui.editorMode="wizard";ui.wizardStep=5;
  ui.current.profiles=[{id:"client",name:"CODE27"},{id:"client-2",name:"Flur-Client"}];
  ui.registry.areas=[{id:"living",name:"Wohnzimmer"},{id:"hall",name:"Flur"}];
  ui.source="living";ui.value=id=>({"target-kind":"source_area",action:"light-on",triggers:"Lumos","spell-name":"Lumos"}[id]||"");
  ui.shadowRoot.querySelectorAll=()=>[];
  const html=ui.wizardView();
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
  ui.wizardHelp.add("target");
  assert.match(ui.wizardHelpText("target","source_area"),/Home.?Assistant/);
});

test("new Wizard spells initialize a valid effective language before translated fields", () => {
  const ui=studio();delete ui.current.book.default_language;delete ui.current.book.language;ui.current.languages=["en"];
  ui.language="de";ui.ensureEditorTargets=async()=>{};
  ui.startNewSpell("wizard");
  assert.equal(ui.bookLanguage,"de");assert.equal(ui.editLanguage,"de");assert.ok(ui.validLanguage(ui.editLanguage));
  const fields={action:"light-on","target-kind":"source_area",triggers:"lumos","spell-name":"Lumos"};ui.value=id=>fields[id]||"";
  const automatic=ui.collectUser();assert.equal(automatic.trigger_language,"de");assert.equal(automatic.default_language,"de");
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
    config:{mode:"custom",allowed_user_ids:["u-ben"],allowed_group_ids:["g-family"]},
    groups:[{id:"g-family",name:"Family",member_count:2,allowed:true}],
    users:[
      {id:"admin-1",name:"Admin",is_admin:true,group_ids:[],access:"admin",allowed:false},
      {id:"u-ben",name:"Benjamin",is_admin:false,group_ids:[],access:"user",allowed:true},
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
  assert.ok(html.includes("Benjamin") && html.includes("Explizit erlaubt"));
  assert.ok(html.includes("Anika") && html.includes("Über Gruppe erlaubt"));
  assert.ok(html.includes("Nils") && html.includes("Kein Zugriff"));
  assert.ok(html.includes("Admin") && html.includes("Administrator"));

  await ui.change({target:{name:undefined, dataset:{accessGroup:"g-family"}, checked:false}});
  assert.deepEqual(ui.accessDraft.allowed_group_ids, new Set());
  await ui.change({target:{name:undefined, dataset:{accessUser:"u-nils"}, checked:true}});
  assert.deepEqual(ui.accessDraft.allowed_user_ids, new Set(["u-ben","u-nils"]));
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
  await ui.open("potter");
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
  await ui.open("potter");
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
  const html = ui.bookView();
  assert.ok(html.includes("Aktion"));
  assert.ok(html.includes("(noch nicht gespeichert)"));
  assert.ok(html.includes("1 Spells zeigen einen noch nicht gespeicherten Stand"));
});
