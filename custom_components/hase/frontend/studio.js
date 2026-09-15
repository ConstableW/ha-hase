/* Shipped with HASE. Uses only the authenticated Home Assistant connection. */
import {escapeHtml as esc, icon, bookCover, bookTheme, filterBooks, actionIcons} from "./studio-visuals.js?v=0.3.16-access1";
import {resolveBookValue, setBookValue, bookFallbacks} from "./studio-book-languages.js?v=0.3.16-access1";
const cssUrl = new URL("./studio.css?v=0.3.16-access1", import.meta.url).href;
const clone = value => JSON.parse(JSON.stringify(value));
import {studioLanguage, translate, translateMessage, contentLanguage} from "./studio-i18n.js?v=0.3.16-access1";
const reasons = {operation_disabled:"Diese Aktion ist für den ausgewählten Client ausgeschaltet.",vacuum_mapping_pending:"Bereich erlaubt; die Sauger-Zuordnung ist ohne Geräteaufruf noch nicht bestätigt.",dry_run:"Prüfung erfolgreich – keine Geräte bedient.",ok:"Home Assistant hat die Aktion angenommen.",no_match:"Kein passender Spell gefunden.",ambiguous:"Die Phrase ist mehrdeutig.",no_user_variant:"Keine User-Variante vorhanden.",expert_disabled:"Expert-Ausführung ist für dieses Buch ausgeschaltet.",target_not_found:"Das Ziel fehlt oder ist nicht freigegeben.",no_matching_targets:"Keine erlaubten Ziele gefunden.",area_not_permitted:"Der gewählte Client darf diesen Bereich nicht bedienen.",output_only:"Dieser Spell liefert Text an den Client. Keine typisierte HASE-Aktion zum Testen.",no_source_area:"Bitte den Herkunftsbereich für diesen Test auswählen.",template_error:"Das Template konnte nicht ausgewertet werden.",invalid_template_result:"Das Template muss eine Entity-ID oder eine Liste von Entity-IDs liefern.",bad_arguments:"Die Parameter passen nicht zur Aktion.",unknown_operation:"Diese Aktion wird von HASE nicht unterstützt.",allowed:"erlaubt",not_found:"nicht vorhanden",wrong_domain:"falscher Gerätetyp",unavailable:"nicht verfügbar",not_exposed_or_ineligible:"nicht freigegeben oder ungeeignet",ambiguous_source:"Mehrere passende Geräte gefunden – bitte genauer eingrenzen (z. B. Geräteklasse).",missing_attribute:"Dieses Attribut ist am Gerät nicht vorhanden.",empty_aggregation:"Keine passenden Geräte für diese Auswertung gefunden.",partial:"Antwort erzeugt – einzelne Datenquellen konnten nicht vollständig aufgelöst werden.",capability_not_supported:"Diese Funktion wird vom gewählten Gerät nicht unterstützt."};
// Output mode (item 8) is a small, fixed, domain-independent enum from
// core/models.py's OUTPUT_MODES - the valid set of ids still comes from
// the backend (this.info.output_modes), only the human label text lives
// here, same "UI-specific display logic" allowance as the `reasons` map.
const outputModeLabels = {AUTO:"Automatisch (empfohlen)",TEXT:"Nur Text",SPEECH:"Nur Sprache",TEXT_AND_SPEECH:"Text und Sprache",NONE:"Keine Ausgabe"};
// Same allowance as outputModeLabels/reasons above: the domain LIST itself
// is entirely backend-derived (OperationSpec.domain_filter via
// user_actions()), only this display-name lookup for the Wizard's
// "Gerätetyp" filter lives here - a HA domain id ("light", "media_player",
// ...) is stable, closed vocabulary, not something HASE could ever invent
// a new value for on its own.
const domainLabels = {light:"Licht",switch:"Schalter",input_boolean:"Schalt-Helfer",cover:"Abdeckung",climate:"Klima",fan:"Ventilator",lock:"Schloss",media_player:"Mediaplayer",vacuum:"Staubsauger",scene:"Szene",script:"Skript",automation:"Automatisierung",button:"Taster",number:"Zahl",input_number:"Zahl-Helfer",select:"Auswahl",input_select:"Auswahl-Helfer",timer:"Timer"};

class HaseSpellbookStudio extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode:"open"});
    this.expert = false;
    this.authorized = true;
    this.userManagementOpen = false;
    this.access = null;
    this.accessDraft = null;
    this.filters = {search:this.loadStoredSpellSearch()};
    this.wizardEntityFilter = "";
    this.selected = new Set();
    this.modalReturnFocus = null;
    this.registry = {areas:[], entities:[]};
    this.entityDomainsKey = null;

    // Spell editor dirty-state
    this.editorDirty = false;
    this.editorBaseline = null;
    this.editorMode = "manual";
    this.wizardStep = 0;
    this.wizardMaxStep = 0;
    this.wizardActionDomain = "";
    this.wizardHelp = new Set();
    this.wizardValidation = null;
    this.wizardLanguageExplicit = null;
    this.newSpellChoice = false;
    this.newSpellStep = 0;
    this.newSpellLanguageChoice = null;
    // Combined Spells (steps) - which step card is currently expanded
    // for editing, or null when the list is fully collapsed.
    this.editingStepIndex = null;

    // Warn on real browser/page leave.
    this._beforeUnload = e => {
      if (!this.edit || !this.editorDirty) return;
      e.preventDefault();
      e.returnValue = "";
    };

    this.shadowRoot.addEventListener("click", e => {
      const b = e.target.closest("[data-do]");
      if (b) {
        this.act(b.dataset.do, b.dataset.id).catch(err => this.fail(err));
        return;
      }

      const backdrop = e.target.closest(".modal-backdrop");
      if (backdrop && e.target === backdrop && !this.pendingConfirm) {
        this.closeModal();
      }
    });

    this.shadowRoot.addEventListener("keydown", e => {
      if (e.key === "Escape" && (this.result || this.message || this.pendingConfirm)) {
        e.preventDefault();
        if (this.pendingConfirm) {
          this.act("confirm-no").catch(err => this.fail(err));
        } else {
          this.closeModal();
        }
      }
    });

    this.shadowRoot.addEventListener("input", e => {
      if (this.isEditorField(e.target)) { this.setEditorDirty(true); if (!this.isResponseTextField(e.target)) this.wizardValidation=null; }
      if (e.target.id === "triggers") this.refreshResponseChips();
      if (e.target.id === "filter-search") this.updateSpellSearch(e.target);
      if (e.target.id === "wizard-entity-filter") this.updateWizardEntityFilter(e.target.value);
    });

    this.shadowRoot.addEventListener("change", e => {
      if (this.isEditorField(e.target)) { this.setEditorDirty(true); if (!this.isResponseTextField(e.target)) this.wizardValidation=null; }
      if (e.target.id === "wizard-language") { this.changeWizardLanguage(e.target); return; }
      this.change(e).catch(err => this.fail(err));
    });

    this.shadowRoot.addEventListener("dragover", e => {
      if (this.importing) e.preventDefault();
    });

    this.shadowRoot.addEventListener("drop", async e => {
      if (this.importing) {
        e.preventDefault();
        const file = e.dataTransfer.files[0];
        if (file) await this.readFile(file);
      }
    });
  }

  connectedCallback() {
    window.addEventListener("beforeunload", this._beforeUnload);
  }

  disconnectedCallback() {
    window.removeEventListener("beforeunload", this._beforeUnload);
  }
  set hass(value) { const language=studioLanguage(value), changed=this.language!==language, inherited=this.current&&!this.storedBookLanguage(); this._hass=value; if(this.loaded&&changed&&inherited&&this.edit&&!this.wizardLanguageExplicit)this.captureLanguageDraft(); this.language=language; if(this.loaded&&changed&&inherited&&!this.wizardLanguageExplicit)this.bookLanguage=this.effectiveBookLanguage(); if(!this.loaded && !this.loading){this.loading=true; this.init().catch(e=>this.fail(e));} else if(this.loaded && changed){this.render(true);} }
  t(key,values){return translate(this.language || "en",key,values);}
  validLanguage(value){const code=String(value||"").trim().toLowerCase().replaceAll("_","-");return /^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$/.test(code)?code:null;}
  storedBookLanguage(){return this.validLanguage(this.current?.book?.default_language||this.current?.book?.language);}
  effectiveBookLanguage(explicit=null){return this.validLanguage(explicit)||this.storedBookLanguage()||this.validLanguage(this.language)||"en";}
  languageName(code){try{return new Intl.DisplayNames([this.language||"en"],{type:"language"}).of(code)||code.toUpperCase();}catch(_err){return code.toUpperCase();}}
  messageText(text){return translateMessage(this.language || "en",text);}
  localized(value,technical=""){return resolveBookValue(value,this.bookLanguage || this.language,this.current?.default_language,technical).value;}
  bookName(book){return resolveBookValue(book.name,this.current?.book.id===book.id?this.bookLanguage:this.language,book.default_language || book.language,book.id).value;}
  editText(value){if(value && typeof value==="object" && !Array.isArray(value))return value[this.bookLanguage] || "";return value || "";}
  putText(previous,value){return setBookValue(previous,this.bookLanguage,value,this.storedBookLanguage()||this.effectiveBookLanguage());}
  captureLanguageDraft(){if(!this.edit)return;const u=this.collectUser();this.sharedForm=[...this.shadowRoot.querySelectorAll("input,textarea,select")].filter(e=>(e.id || e.name) && !["spell-name","spell-description","spell-examples","spell-output","spell-catchphrase","expert-catchphrase","triggers","phrase"].includes(e.id)).map(e=>({id:e.id,name:e.name,value:e.value,checked:e.checked}));for(const [key,value] of Object.entries({name:u.name,description:u.description,trigger:u.triggers[0],triggers:u.triggers.slice(1),examples:u.examples,output:u.output,catchphrase:u.catchphrase})){if(value!==undefined)this.edit[key]=this.putText(this.edit[key],value);}if(this.expert && this.edit.expert)this.edit.expert.catchphrase=this.putText(this.edit.expert.catchphrase,this.value("expert-catchphrase"));this.renderedEditId=null;}

  get editorActions(){return this.edit && !Array.isArray(this.edit.steps) && !this.info.actions.some(a=>a.operation===this.edit.operation && JSON.stringify(a.arguments)===JSON.stringify(this.edit.arguments||{}))?[{id:"__preserve__",label:"Keep existing technical definition",operation:this.edit.operation||"text.response",arguments:this.edit.arguments||{},targets:[this.edit.target?.kind||"none"],target_capture:this.edit.target?.capture},...this.info.actions]:this.info.actions;}
  get actionChoices(){return this.editorActions.map(action=>({...action,label:this.t(action.label)}));}
  // Item 13 (capability-driven UI): the Wizard's Action list is narrowed by
  // domain and, once a concrete target entity is already selected, by that
  // entity's live capabilities - entirely from backend metadata
  // (action.domain_filter/capability_hint from user_actions(), entity
  // capabilities from the entities WS op). Never a hardcoded domain/action
  // table here - a light seeing "Öffnen/Schließen" was exactly this
  // filtering being absent, not a case needing a special-cased fix.
  availableActionDomains(){return [...new Set(this.info.actions.map(a=>a.domain_filter).filter(Boolean))].sort((a,b)=>a.localeCompare(b));}
  // Two aligned columns ("Licht          | light") inside a native <select>
  // option can only line up visually with a monospace font (options render
  // as plain text, no CSS layout inside them) - see the #wizard-action-domain
  // font-family rule in studio.css. Width adapts to the longest friendly
  // name actually offered, not a fixed guess.
  domainOptionLabel(id){
    // Non-breaking spaces, not plain ones: HTML collapses runs of regular
    // whitespace even inside <option> text, which would silently undo the
    // padding regardless of the monospace font below.
    const friendly=this.t(domainLabels[id]||id),width=Math.max(...this.availableActionDomains().map(d=>this.t(domainLabels[d]||d).length));
    return `${friendly.padEnd(width," ")} | ${id}`;
  }
  currentTargetCapabilities(){const ids=[...this.shadowRoot.querySelectorAll('input[name="entity"]:checked')].map(e=>e.value);return ids.map(id=>this.registry.entities.find(e=>e.id===id)?.capabilities).filter(Boolean);}
  visibleActionChoices(keepId=null){
    const domain=this.wizardActionDomain,caps=this.currentTargetCapabilities();
    const filtered=this.actionChoices.filter(action=>{
      if(domain && action.domain_filter && action.domain_filter!==domain)return false;
      if(action.capability_hint && caps.length && !caps.some(c=>{
        const flag=c?.[action.capability_hint];
        return Array.isArray(flag)?flag.length>0:Boolean(flag);
      }))return false;
      return true;
    });
    if(keepId && !filtered.some(a=>a.id===keepId)){
      const keep=this.actionChoices.find(a=>a.id===keepId);
      if(keep)filtered.push(keep);
    }
    return filtered;
  }
  targetCapture(action,existing=null){const candidate=action?.target_capture || existing || "area";return /^[a-zA-Z_][a-zA-Z0-9_]*$/.test(candidate)?candidate:"area";}
  operationTargetCapture(operation,existing=null){const action=this.info.actions.find(a=>a.operation===operation && a.target_capture);return this.targetCapture(action,existing);}
  get hass(){return this._hass;}
  loadStoredSpellSearch(){try{return globalThis.localStorage?.getItem("hase-studio-spell-search")||"";}catch(_err){return "";}}
  storeSpellSearch(value){try{globalThis.localStorage?.setItem("hase-studio-spell-search",value);}catch(_err){/* Storage can be unavailable in private/restricted contexts. */}}
  updateSpellSearch(target){const value=target.value,caret=target.selectionStart;this.filters.search=value;this.storeSpellSearch(value);this.render(true);queueMicrotask(()=>{const input=this.shadowRoot.getElementById("filter-search");input?.focus?.();if(Number.isInteger(caret))input?.setSelectionRange?.(caret,caret);});}
  updateWizardEntityFilter(value){this.wizardEntityFilter=value;const needle=value.trim().toLocaleLowerCase();for(const row of this.shadowRoot.querySelectorAll("[data-entity-search]"))row.hidden=Boolean(needle&&!row.dataset.entitySearch.includes(needle));}
  async api(operation,data={}) {return this.hass.callWS({type:"hase/studio",operation,data:{book_id:this.current?.book.id,revision:this.current?.revision,profile_id:this.profile,source_area_id:this.source,language:this.bookLanguage || this.language || "en",book_language:this.bookLanguage,...data}});}
  async init(){
    try {
      this.info = await this.api("list");
    } catch (err) {
      // A denied user still reaches this code (the panel route itself is
      // reachable by any authenticated user - see studio_api.py's
      // require_admin=False), but the WebSocket layer is the real gate.
      if (err?.code === "unauthorized") {
        this.authorized = false; this.loaded = true; this.loading = false; this.render();
        return;
      }
      throw err;
    }
    this.authorized = true; this.loaded = true; this.loading = false; this.render(); this.loadHomeCovers();
  }
  async loadHomeCovers(){for(const book of this.info?.books || []){if(!book.has_cover || book.cover)continue;try{const result=await this.api("cover",{book_id:book.id});book.cover=result.cover || null;this.render();}catch(_err){book.cover=null;}}}
  actionDomains(action){if(!action?.operation || action.operation==="entity.get_state")return [];const domain=action.operation.split(".",1)[0];return [domain];}
  async ensureAreas(){if(this.registry.areas.length)return;const result=await this.api("areas");this.registry.areas=result.areas || [];if(this.loaded)this.render(true);}
  async ensureEntitiesForAction(action,includeIds=[]){const domains=this.actionDomains(action);const key=domains.join(",") || "*";const wanted=[...new Set((includeIds||[]).filter(Boolean))];if(this.entityDomainsKey===key && wanted.every(id=>this.registry.entities.some(e=>e.id===id)))return;const result=await this.api("entities",{domains,include_ids:wanted});this.registry.entities=result.entities || [];this.entityDomainsKey=key;if(this.loaded)this.render(true);}
  async ensureEditorTargets(){if(!this.edit || Array.isArray(this.edit.steps))return;const a=this.editorActions.find(a=>a.operation===this.edit.operation && JSON.stringify(a.arguments)===JSON.stringify(this.edit.arguments||{})) || this.editorActions[0];const kind=this.edit.target?.kind;if(kind==="area_id")await this.ensureAreas();else if(["entity_id","entity_ids"].includes(kind)){const values=Array.isArray(this.edit.target?.value)?this.edit.target.value:[this.edit.target?.value];await this.ensureEntitiesForAction(a,values);}}
  fail(err){this.loading=false; this.rememberModalFocus(); this.message=err.message || this.t("Die Anfrage ist fehlgeschlagen."); this.render();}
  value(id){return this.shadowRoot.getElementById(id)?.value || "";}
  isEditorField(target) {
    if (!this.edit || !target?.closest?.("#spell-editor")) return false;
    if (["phrase", "variant", "wizard-language", "wizard-entity-filter", "wizard-action-domain", "step-action-domain"].includes(target.id)) return false;
    return target.matches?.("input, textarea, select") === true;
  }
  isResponseTextField(target) {
    return ["spell-output", "spell-catchphrase", "expert-catchphrase"].includes(target?.id);
  }
  setEditorDirty(value = true) { this.editorDirty = value; this.updateDirtyUi(); }
  updateDirtyUi() {
    const abort = this.shadowRoot.querySelector('[data-do="abort-editor"]');
    if (abort) {
      abort.disabled = !this.editorDirty;
      abort.setAttribute("aria-disabled", this.editorDirty ? "false" : "true");
    }
  }
  beginEditorSession() {
    this.editorBaseline = clone(this.edit);
    this.editorDirty = false;
    this.renderedEditId = null;
    this.sharedForm = null;
  }
  clearEditorSession() {
    this.editorDirty = false;
    this.editorBaseline = null;
    this.renderedEditId = null;
    this.sharedForm = null;
    this.editorMode = "manual";
    this.wizardStep = 0;
    this.wizardMaxStep = 0;
    this.wizardActionDomain = "";
    this.wizardHelp.clear();
    this.wizardValidation = null;
    this.wizardLanguageExplicit = null;
    this.newSpellChoice = false;
    this.newSpellStep = 0;
    this.newSpellLanguageChoice = null;
    this.editingStepIndex = null;
  }
  async confirmDiscardEditor() {
    if (!this.edit || !this.editorDirty) return true;
    return await this.askConfirmation(this.t("Du hast ungespeicherte Änderungen. Wenn du den Editor jetzt verlässt, gehen sie verloren. Änderungen verwerfen?"));
  }
  async restoreEditorBaseline() {
    if (!this.editorBaseline) return;
    this.edit = clone(this.editorBaseline);
    this.editorDirty = false;
    this.result = null;
    this.registry.entities = [];
    this.entityDomainsKey = null;
    this.renderedEditId = null;
    this.sharedForm = null;
    this.render();
    await this.ensureEditorTargets();
  }
  async open(id){const previousId=this.current?.book?.id,previousLanguage=this.bookLanguage;this.current=await this.api("read",{book_id:id});const available=new Set([...(this.current.languages||[]),this.effectiveBookLanguage()]);this.bookLanguage=previousId===id&&available.has(previousLanguage)?previousLanguage:this.effectiveBookLanguage();this.profile=this.testProfiles()[0]?.id || ""; this.report=null; this.metadataOpen=false;this.metadataDraft=null; this.edit=null; this.editLanguage=undefined; this.wizardLanguageExplicit=null; this.importing=false; this.selected.clear(); this.filters={search:this.loadStoredSpellSearch()}; this.result=null; this.registry.areas=[];this.registry.entities=[];this.entityDomainsKey=null;this.render(); this.scrollTop=0; this.ensureAreas().catch(e=>this.fail(e));}
  async refresh(){const id=this.current?.book.id; this.info=await this.api("list"); if(id) await this.open(id); else {this.render();this.loadHomeCovers();}}
  get brandUrl(){return this.panel?.config?.brand_url || "/hase-studio-brand/icon.png";}
  button(label,action,id="",kind=""){return `<button class="${kind}" data-do="${action}" data-id="${esc(id)}">${actionIcons[action]?icon(actionIcons[action]):""}${label}</button>`;}
  options(rows,value){return rows.map(r=>`<option value="${esc(r.id)}" ${r.id===value?"selected":""}>${esc(r.name || r.label)}</option>`).join("");}
  outputModeOptions(value){const modes=this.info.output_modes||["AUTO","TEXT","SPEECH","TEXT_AND_SPEECH","NONE"];return this.options(modes.map(id=>({id,name:this.t(outputModeLabels[id]||id)})),value||this.info.default_output_mode||"AUTO");}
  // light.set_color's visual color picker (item: "provide a visual color
  // picker... show the currently configured color") - a plain HTML color
  // input alongside the curated presets, plus a free HA color_name field
  // (arbitrary HA-supported names, not limited to the curated word list).
  // Changing either directly overwrites the edited operation/arguments
  // (see the "action-color-picker"/"action-color-name" change() handlers)
  // - the existing editorActions "__preserve__" row (already built for
  // exactly this "current draft doesn't match any catalog preset" case)
  // then keeps the custom color through save/reload with zero backend
  // changes, same mechanism the Expert editor's own custom drafts use.
  rgbToHex(rgb){return "#"+(Array.isArray(rgb)&&rgb.length===3?rgb:[255,255,255]).map(c=>Math.max(0,Math.min(255,Number(c)||0)).toString(16).padStart(2,"0")).join("");}
  hexToRgb(hex){const m=/^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex||"");return m?[1,2,3].map(i=>Number.parseInt(m[i],16)):[255,255,255];}
  lightColorPickerView(operation,arguments_){
    if(operation!=="light.set_color")return "";
    const rgb=Array.isArray(arguments_?.rgb_color)?arguments_.rgb_color:null;
    const colorName=typeof arguments_?.color_name==="string"?arguments_.color_name:"";
    return `<div class="light-color-picker"><label><span>${esc(this.t("Farbe wählen"))}</span><input type="color" id="action-color-picker" value="${esc(this.rgbToHex(rgb))}"></label><label><span>${esc(this.t("Oder HA-Farbname eingeben (optional)"))}</span><small>${esc(this.t("Beliebiger von Home Assistant unterstützter Farbname, z. B. chartreuse."))}</small><input id="action-color-name" value="${esc(colorName)}" placeholder="chartreuse"></label></div>`;
  }
  expertArgumentDefaults(operation){
    // Derived entirely from this.info.actions (backend user_actions()) -
    // never a per-operation JS list. A user_choice's arguments are either
    // a fixed literal value (used as-is) or a capture spec (sampled: mid-
    // range for int/float, the first true_value for a boolean-word
    // mapping) - this covers every operation the catalog can ever grow to
    // without a JS change here.
    const row=this.info.actions.find(a=>a.operation===operation);
    if(!row)return {};
    const sample={};
    for(const [key,value] of Object.entries(row.arguments||{})){
      if(value && typeof value==="object" && !Array.isArray(value) && "capture" in value){
        if(value.type==="int" || value.type==="float"){
          const min=value.min ?? 0,max=value.max ?? 100;
          const mid=(Number(min)+Number(max))/2;
          sample[key]=value.type==="int"?Math.round(mid):mid;
        } else {
          sample[key]=value.true_values?.[0]!==undefined?true:(value.false_values?.[0]!==undefined?false:"");
        }
      } else {
        sample[key]=value;
      }
    }
    return sample;
  }
  rememberModalFocus(){const active=this.shadowRoot.activeElement;if(active)this.modalReturnFocus=active;}
  closeModal(){
    this.result=null; this.message=null;
    const focus=this.modalReturnFocus; this.modalReturnFocus=null;
    this.render();
    queueMicrotask(()=>{if(focus?.isConnected && typeof focus.focus==="function")focus.focus();});
  }
  modalView(){
    if(this.pendingConfirm){
      return `<div class="modal-backdrop modal-confirm-backdrop"><section class="studio-modal modal-confirm" role="alertdialog" aria-modal="true" aria-labelledby="modal-title"><div class="modal-head"><h2 id="modal-title">${esc(this.t("Bitte bestätigen"))}</h2></div><div class="modal-body"><p>${esc(this.messageText(this.pendingConfirm.text))}</p></div><div class="modal-actions">${this.button(this.t("Abbrechen"),"confirm-no")}${this.button(this.t("Bestätigen"),"confirm-yes","","primary")}</div></section></div>`;
    }
    if(this.message){
      return `<div class="modal-backdrop"><section class="studio-modal modal-error" role="alertdialog" aria-modal="true" aria-labelledby="modal-title"><div class="modal-head"><h2 id="modal-title">${esc(this.t("Fehler"))}</h2>${this.button("×","dismiss","","modal-close")}</div><div class="modal-body"><p class="modal-message">${esc(this.messageText(this.message))}</p></div><div class="modal-actions">${this.button(this.t("Schließen"),"dismiss","","primary")}</div></section></div>`;
    }
    if(this.result){
      return `<div class="modal-backdrop"><section class="studio-modal modal-result" role="dialog" aria-modal="true" aria-labelledby="modal-title">${this.resultView(true)}</section></div>`;
    }
    return "";
  }
  testProfiles(){
    // Backend studio.py exposes the complete, unfiltered Studio client list
    // explicitly as `studio_profiles`. Prefer it over runtime-assigned profiles.
    // Runtime routing/policy remains unchanged.
    if(Array.isArray(this.current?.studio_profiles))return this.current.studio_profiles;

    // Compatibility fallback for older backends only.
    const sources=[
      this.registry?.profiles,
      this.registry?.clients,
      this.info?.profiles,
      this.info?.clients,
      this.current?.all_profiles,
      this.current?.all_clients,
      this.current?.profiles
    ];
    const merged=new Map();
    for(const rows of sources){
      if(!Array.isArray(rows))continue;
      for(const row of rows){
        if(row?.id && !merged.has(row.id))merged.set(row.id,row);
      }
    }
    return [...merged.values()];
  }
  topbarToolsView(){
    return `${this.button(this.t("Studio-Einstellungen"),"settings")}${this.hass?.user?.is_admin?this.button(this.t("Nutzerverwaltung"),"user-management"):""}${this.button(this.expert?"Expert Mode ✓":"Expert Mode","expert")}`;
  }
  accessDeniedView(){
    return `<header class="studio-topbar"><ha-menu-button></ha-menu-button><div class="topbar-brand"><img src="${esc(this.brandUrl)}" alt=""><strong>HASE</strong><span>Spellbook Studio</span></div></header><main class="shell"><section class="empty-state">${icon("shield")}<h2>${esc(this.t("Kein Zugriff auf HASE Studio"))}</h2><p>${esc(this.t("Du bist als {name} angemeldet, hast aber keinen Zugriff auf HASE Studio. Bitte eine Administratorin oder einen Administrator kontaktieren.",{name:this.hass?.user?.name||""}))}</p></section></main>`;
  }
  // Nutzerverwaltung/User Management - admin-only in the frontend (real
  // enforcement is server-side: access_get/access_set are gated by
  // connection.user.is_admin in studio_api.py, never trusting this UI).
  userManagementView(){
    if(!this.access)return `<section class="studio-settings"><h2>${esc(this.t("Nutzerverwaltung"))}</h2><p>${esc(this.t("HASE Spellbook Studio wird geladen …"))}</p></section>`;
    const draft=this.accessDraft;
    const modes=[
      {id:"all", label:"Alle Benutzer", help:"Jeder angemeldete Home-Assistant-Benutzer darf HASE öffnen."},
      {id:"admin", label:"Nur Administratoren", help:"Nur Home-Assistant-Administratoren dürfen HASE öffnen."},
      {id:"custom", label:"Benutzer & Gruppen individuell", help:"Zugriff über ausgewählte Benutzer oder Gruppen."},
    ];
    const reasonLabel={admin:this.t("Administrator"),all:this.t("Alle Benutzer"),user:this.t("Explizit erlaubt"),group:this.t("Über Gruppe erlaubt"),denied:this.t("Kein Zugriff")};
    const modeRows=modes.map(m=>`<label class="setting-row"><span>${esc(this.t(m.label))}<small>${esc(this.t(m.help))}</small></span><input type="radio" name="access-mode" value="${m.id}" ${draft.mode===m.id?"checked":""}></label>`).join("");
    const groupsRows=this.access.groups.length?this.access.groups.map(g=>
      `<label class="setting-row"><span>${esc(g.name)}<small>${esc(this.t("{n} Mitglieder",{n:g.member_count}))}</small></span><input type="checkbox" data-access-group="${esc(g.id)}" ${draft.allowed_group_ids.has(g.id)?"checked":""}></label>`
    ).join(""):`<p>${esc(this.t("Keine Home-Assistant-Gruppen gefunden."))}</p>`;
    const usersRows=this.access.users.length?this.access.users.map(u=>{
      const role=u.is_admin?this.t("Administrator"):this.t("Standard");
      const groupNames=u.group_ids.map(id=>this.access.groups.find(g=>g.id===id)?.name).filter(Boolean).join(", ");
      const meta=`<strong>${esc(u.name)}</strong> — ${esc(role)}${groupNames?" · "+esc(groupNames):""}<small>${esc(reasonLabel[u.access])}</small>`;
      return u.is_admin
        ? `<div class="setting-row"><span>${meta}</span></div>`
        : `<label class="setting-row"><span>${meta}</span><input type="checkbox" data-access-user="${esc(u.id)}" ${draft.allowed_user_ids.has(u.id)?"checked":""}></label>`;
    }).join(""):`<p>${esc(this.t("Keine Home-Assistant-Benutzer gefunden."))}</p>`;
    return `<section class="studio-settings"><h2>${esc(this.t("Nutzerverwaltung"))}</h2><p>${esc(this.t("Wer darf HASE Studio öffnen?"))}</p>
      <fieldset><legend>${esc(this.t("Zugriffsmodus"))}</legend>${modeRows}</fieldset>
      ${draft.mode==="custom"?`<h3>${esc(this.t("Gruppen"))}</h3><div class="checklist">${groupsRows}</div><h3>${esc(this.t("Benutzer"))}</h3><div class="checklist">${usersRows}</div>`:""}
      <div class="toolbar">${this.button(this.t("Zugriffseinstellungen speichern"),"access-save")}${this.button(this.t("Abbrechen"),"access-cancel")}</div>
      </section>`;
  }
  render(preserveForm=false){
    if(!this.loaded){this.shadowRoot.innerHTML=`<p>${esc(this.t("HASE Spellbook Studio wird geladen …"))}</p>`; return;}
    if(this.authorized===false){
      this.shadowRoot.innerHTML=`<link rel="stylesheet" href="${esc(cssUrl)}">${this.accessDeniedView()}`;
      const menu=this.shadowRoot.querySelector("ha-menu-button"); if(menu)menu.hass=this.hass;
      return;
    }
    const retained = preserveForm || this.edit && this.renderedEditId===this.edit.id ? [...this.shadowRoot.querySelectorAll("input,textarea,select")].filter(e=>e.id || e.name).map(e=>({id:e.id,name:e.name,value:e.value,checked:e.checked})) : [];
    this.lang=this.language || "en";
    const book=this.current?.book;
    this.shadowRoot.innerHTML=`<link rel="stylesheet" href="${esc(cssUrl)}"><style>
      .modal-backdrop{position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.58);display:flex;align-items:center;justify-content:center;padding:24px;box-sizing:border-box;backdrop-filter:blur(2px)}
      .studio-modal{width:min(900px,96vw);max-height:85vh;display:flex;flex-direction:column;background:var(--card-background-color,#111f2a);color:var(--primary-text-color,#fff);border:1px solid rgba(75,215,240,.42);border-radius:14px;box-shadow:0 22px 70px rgba(0,0,0,.48);overflow:hidden}
      .modal-result{width:min(1050px,96vw)} .modal-confirm{width:min(620px,94vw)} .modal-error{border-color:rgba(255,183,77,.65)}
      .modal-head{display:flex;align-items:center;gap:16px;justify-content:space-between;padding:18px 20px;border-bottom:1px solid rgba(255,255,255,.11);flex:0 0 auto}
      .modal-head h2{margin:0}.modal-body{padding:18px 20px;overflow:auto;overscroll-behavior:contain;min-height:0}.modal-actions{display:flex;justify-content:flex-end;gap:10px;padding:14px 20px;border-top:1px solid rgba(255,255,255,.11);flex:0 0 auto}
      .modal-close{min-width:38px;padding-inline:10px;font-size:20px;line-height:1}.abort-editor:disabled{opacity:.45;cursor:not-allowed;filter:saturate(.6)}.studio-modal .result{margin:0;border:0;border-radius:0;max-height:none;overflow:auto;padding:0}.studio-modal pre{max-width:100%;overflow:auto}
      @media (max-width:700px){.modal-backdrop{padding:10px;align-items:flex-end}.studio-modal{width:100%;max-height:90vh;border-radius:14px 14px 0 0}}
    </style><header class="studio-topbar"><ha-menu-button></ha-menu-button><div class="topbar-brand"><img src="${esc(this.brandUrl)}" alt=""><strong>HASE</strong><span>Spellbook Studio</span></div><div class="topbar-tools">${this.topbarToolsView()}</div></header><main class="shell">
    ${this.settingsOpen?`<section class="studio-settings"><h2>${esc(this.t("Studio-Einstellungen"))}</h2><label class="setting-row"><span>${esc(this.t("Show Spellbook Studio in sidebar"))}<small>${esc(this.t("/hase bleibt auch ohne Seitenleisteneintrag erreichbar."))}</small></span><input id="show-sidebar" type="checkbox" ${this.info.show_in_sidebar?"checked":""}></label></section>`:""}
    ${this.userManagementOpen && this.hass?.user?.is_admin?this.userManagementView():""}
    ${book?this.bookView():this.homeView()}</main>${this.modalView()}`
    const menu=this.shadowRoot.querySelector("ha-menu-button"); if(menu)menu.hass=this.hass;
    for(const saved of retained){const elements=saved.id?[this.shadowRoot.getElementById(saved.id)]:[...this.shadowRoot.querySelectorAll('input[name="entity"]')].filter(e=>e.value===saved.value);for(const el of elements){if(!el)continue;if(el.type==="checkbox")el.checked=saved.checked;else if(el.tagName!=="SELECT" || [...el.options].some(o=>o.value===saved.value))el.value=saved.value;}}
    this.renderedEditId=this.edit?.id; this.updateTargetControls();if(this.edit)this.refreshResponseChips(); this.updateDirtyUi();
    const modal=this.shadowRoot.querySelector(".studio-modal");
    if(modal)queueMicrotask(()=>{const target=modal.querySelector(".primary,button,select,input,textarea,[tabindex]");target?.focus?.();});
  }
  homeView(){return `<div class="studio-hero"><img class="hero-logo" src="${esc(this.brandUrl)}" alt="${esc(this.t("HASE – Hase mit Zauberbuch"))}"><div class="hero-copy"><h1>HASE</h1><div class="hero-title">Spellbook Studio</div><p class="hero-tagline">${esc(this.t("Deine Worte. Deine Magie."))}</p><p class="muted">${esc(this.t("Eigenständige Bücher, gemeinsam verwaltet. Jedes Buch behält seine Spells und Freigaben."))}</p></div><div class="hero-charm" aria-hidden="true">✧</div></div><div class="overview-toolbar"><div class="toolbar">${this.button(this.t("Spellbook erstellen"),"new-book","","primary")}${this.button(this.t("Spellbook importieren"),"import-new")}${this.button(this.t("Alle Bücher prüfen"),"check-all")}${this.button(this.t("Aktualisieren"),"refresh")}</div><label class="book-search">${icon("search")}<input id="book-search" aria-label="${esc(this.t("Spellbooks suchen"))}" placeholder="${esc(this.t("Spellbooks suchen …"))}" value="${esc(this.bookSearch)}"></label></div><div class="grid">${filterBooks(this.info.books,this.bookSearch).map(b=>`<article class="card">${bookCover(b)}<div class="card-body"><div class="eyebrow">${b.count} Spells</div><h2>${esc(this.bookName(b))}</h2><span class="badge">${esc(b.capability)}</span><p class="card-meta">${icon("clients")}${b.active_clients ?? 0} ${esc(this.t("zugeordnete Clients"))}</p><p class="card-meta">${icon("book")}${esc(this.t("Expert-Ausführung:"))} <strong>${b.expert_allowed?this.t("AN"):this.t("AUS")}</strong></p>${this.button(this.t("Buch öffnen →"),"open",b.id,"primary open-book")}</div></article>`).join("")}<button class="card card-create" data-do="new-book"><span class="plus-ring">${icon("plus")}</span><strong>${esc(this.t("Neues Spellbook"))}</strong><span>${esc(this.t("Erstelle ein neues Buch"))}<br>${esc(this.t("und beginne zu zaubern."))}</span></button></div>${this.importNew?this.newImportView():""}${this.allReport?`<section><h2>${esc(this.t("Prüfung aller Bücher"))}</h2>${this.allReport.books.map(b=>`<p><strong>${esc(this.bookName(b))}</strong> · ${b.report.errors} ${esc(this.t("Schemafehler"))} · ${b.report.user_executable} ${esc(this.t("User ausführbar"))} · ${esc(b.profile || this.t("Kein Client konfiguriert"))}</p>`).join("")}</section>`:""}${this.creating?`<section><h2>${esc(this.t("Neues Spellbook"))}</h2><label>${esc(this.t("Name"))}<input id="book-name" placeholder="${esc(this.t("Mein Spellbook"))}"></label><label>${esc(this.t("Kurze Kennung"))}<input id="book-id" placeholder="mein_spellbook"></label>${this.button(this.t("Erstellen"),"create-book","","primary")}</section>`:""}`;}
  bookView(){const b=this.current.book; return `<div class="back-link">${this.button(this.t("Alle Spellbooks"),"home")}</div><div class="book-hero">${bookCover({...b,cover:this.current.cover},{large:true})}<div class="book-identity"><div class="eyebrow">${esc(this.t("Eigenständiges Spellbook"))}</div><h1>${esc(this.bookName(b))}</h1><span class="badge">${esc(this.current.capability)}</span><span class="badge">${b.commands.length} Spells</span><p class="muted">${esc(this.localized(b.description) || this.t("Spells gestalten, prüfen und mit anderen teilen."))}</p><div class="book-context"><label>${esc(this.t("Testen mit Client"))} <select id="profile"><option value="">${esc(this.t("Kein Client ausgewählt"))}</option>${this.options(this.testProfiles(),this.profile)}</select></label><label>${esc(this.t("Herkunftsbereich"))} <select id="source"><option value="">${esc(this.t("Nicht angegeben"))}</option>${this.options(this.registry.areas,this.source)}</select></label><div class="expert-status ${this.current.expert_allowed?"is-on":""}"><div>${esc(this.t("Expert-Ausführung:"))} <strong>${this.current.expert_allowed?this.t("AN"):this.t("AUS")}</strong><small>${esc(this.t("Freigabe für dieses Buch"))}</small>${this.expert?this.button(this.t("Freigabe ändern"),"permission"):""}</div><span class="status-light" aria-hidden="true"></span></div></div></div></div>${this.languageView()}${this.metadataOpen?this.metadataView():this.edit?(this.editorMode==="wizard"?this.wizardView():this.editorMode==="steps"?this.stepsWizardView():this.editorView()):this.newSpellChoice?this.newSpellChoiceView():this.importing?this.importView():this.listView()}`;}
  languageView(){const stored=this.storedBookLanguage(),effective=this.effectiveBookLanguage(),inherited=!stored,codes=[...new Set([...(this.current.languages||[]),effective])],fallbacks=bookFallbacks(this.current.book,this.bookLanguage,stored||effective);return `<section class="book-languages"><div class="toolbar"><strong>${esc(this.t("Book language"))}</strong>${codes.map(code=>`<button data-do="book-language" data-id="${esc(code)}" aria-pressed="${code===this.bookLanguage}">${esc(`${this.languageName(code)} (${code.toUpperCase()})${inherited&&code===effective?"*":""}`)}</button>`).join("")}${inherited?this.wizardHelpButton("inherited-language"):""}${this.button(this.t("+ Language"),"languages")}</div>${inherited&&this.wizardHelp.has("inherited-language")?`<div class="context-help" role="note">${esc(this.t("* System language — this Spellbook has no saved default language."))}</div>`:""}${this.languagesOpen?`<label>${esc(this.t("New language code"))}<input id="language-code" placeholder="fr"></label><label>${esc(this.t("Copy from (optional)"))}<select id="language-source"><option value="">${esc(this.t("Empty translation"))}</option>${this.options(codes.map(id=>({id,name:`${this.languageName(id)} (${id.toUpperCase()})`})),"")}</select></label><div class="toolbar">${this.button(this.t("Add language"),"add-language")}${this.button(this.t("Use as default"),"default-language")}${this.button(this.t("Remove selected language"),"remove-language","","danger")}</div>`:""}${fallbacks.length?`<details><summary>${esc(this.t("Translation fallbacks"))}: ${fallbacks.length}</summary>${fallbacks.map(row=>`<p><small>${esc(row.id)} · ${esc(row.field)}: ${esc(this.bookLanguage?.toUpperCase())} → ${esc(row.used?.toUpperCase() || this.t("No executable trigger / technical fallback"))} (${esc(this.t(`fallback_${row.reason}`))})</small></p>`).join("")}</details>`:""}<div class="toolbar"><span class="badge">${esc(this.t(this.current.origin || "builtin"))}</span>${this.current.can_reset?this.button(this.t("Reset to original"),"reset-book","","danger"):this.current.can_delete?this.button(this.t("Delete spellbook"),"delete-book","","danger"):""}</div></section>`;}
  listView(){const tags=[...new Set(this.current.book.commands.flatMap(c=>c.tags || []))]; const rows=this.current.book.commands.filter(c=>{
    const cap=this.current.spell_capabilities[c.id];
    const report=this.report?.spells.find(r=>r.id===c.id);
    return (!this.filters.search || JSON.stringify([c.name,c.trigger,c.triggers]).toLowerCase().includes(this.filters.search.toLowerCase())) && (!this.filters.tag || (c.tags||[]).includes(this.filters.tag)) && (!this.filters.action || this.info.actions.some(a=>a.id===this.filters.action && c.operation===a.operation && JSON.stringify(c.arguments||{})===JSON.stringify(a.arguments))) && (!this.filters.cap || cap===this.filters.cap) && (!this.filters.enabled || String(c.enabled!==false)===this.filters.enabled) && (!this.filters.valid || report && String(report.valid)===this.filters.valid);
    });return `<div class="toolbar">${this.button(this.t("Neuer Spell"),"new-spell","","primary")}${this.button(this.t("Buchdetails"),"metadata")}${this.button(this.t("Spellbook prüfen"),"check")}${this.button(this.t("Importieren"),"import")}${this.button(this.t("Buch exportieren"),"export")}${this.button(this.t("Auswahl exportieren"),"export-selected")}${this.filters.tag?this.button(this.t("Sammlung exportieren"),"export-tag"):""}</div>
    ${this.report?`<section><h2>${esc(this.t("Prüfergebnis"))}</h2><div class="stats"><div><strong>${this.report.user_executable ?? "–"}</strong>${esc(this.t("User ausführbar"))}</div><div><strong>${this.report.expert_executable ?? "–"}</strong>${esc(this.t("Expert / Fallback ausführbar"))}</div><div><strong>${this.report.errors}</strong>${esc(this.t("Schemafehler"))}</div></div><small>${esc(this.t("Momentaufnahme für den gewählten Client. Dynamische Trigger benötigen eine passende Testphrase."))}</small></section>`:""}
    <div class="filters"><input id="filter-search" aria-label="${esc(this.t("Spells suchen"))}" placeholder="${esc(this.t("Spells suchen …"))}" value="${esc(this.filters.search)}" style="max-width:260px"><select id="filter-tag" aria-label="${esc(this.t("Sammlung filtern"))}"><option value="">${esc(this.t("Alle Sammlungen"))}</option>${this.options(tags.map(t=>({id:t,name:t})),this.filters.tag)}</select><select id="filter-action" aria-label="${esc(this.t("Aktion filtern"))}"><option value="">${esc(this.t("Alle Aktionen"))}</option>${this.options(this.actionChoices,this.filters.action)}</select><select id="filter-cap" aria-label="${esc(this.t("Capability filtern"))}"><option value="">${esc(this.t("User und Expert"))}</option>${this.options(["USER","USER + EXPERT","EXPERT ONLY"].map(v=>({id:v,name:v})),this.filters.cap)}</select><select id="filter-enabled" aria-label="${esc(this.t("Aktivierung filtern"))}"><option value="">${esc(this.t("Aktiv und inaktiv"))}</option>${this.options([{id:"true",name:this.t("Aktiv")},{id:"false",name:this.t("Inaktiv")}],this.filters.enabled)}</select><select id="filter-valid" aria-label="${esc(this.t("Prüfzustand filtern"))}"><option value="">${esc(this.t("Alle Prüfzustände"))}</option>${this.options([{id:"true",name:this.t("Schema gültig")},{id:"false",name:this.t("Schema ungültig")}],this.filters.valid)}</select></div><section class="spell-list">${rows.length?rows.map(c=>{const r=this.report?.spells.find(r=>r.id===c.id);const a=this.info.actions.find(a=>a.operation===c.operation && JSON.stringify(a.arguments)===JSON.stringify(c.arguments||{}));return `<article class="row"><input type="checkbox" data-select="${esc(c.id)}" aria-label="${esc(this.localized(c.name,c.id))} ${esc(this.t("auswählen"))}" ${this.selected.has(c.id)?"checked":""}><div><h3>${esc(this.localized(c.name)||this.localized(c.trigger,c.id))}</h3><p class="muted">„${esc(this.localized(c.trigger))}“</p><small>${esc((c.tags||[]).join(" · "))}</small></div><div><span class="badge">${esc(r?.capability || this.current.spell_capabilities[c.id])}</span><p>${esc(this.t(a?.label) || (c.operation?this.t("Erweiterte Aktion"):this.t("Textausgabe")))}</p><small>${c.enabled===false?this.t("Deaktiviert"):r?(r.valid?this.t("Schema gültig"):this.t("Fehler vorhanden")):this.t("Noch nicht geprüft")}</small>${r?this.diagnostics(r):""}</div><div class="toolbar">${this.button(this.t("Öffnen"),"edit",c.id)}${this.button(c.enabled===false?this.t("Aktivieren"):this.t("Deaktivieren"),"toggle",c.id)}</div></article>`;}).join(""):this.t("Keine passenden Spells.")}</section>`;}
  newImportView(){return `<section><h2>${esc(this.t("Eigenständiges Spellbook importieren"))}</h2><p>${esc(this.t("ID, Metadaten und Spells bleiben erhalten. Existierende Bücher werden nicht ersetzt."))}</p><input id="file" type="file" accept=".yaml,.yml,.txt"><label>YAML / Text<textarea id="import-text" class="code">${esc(this.importText||"")}</textarea></label>${this.button(this.t("Prüfen"),"analyze-new")}${this.importPreview?`<p>${esc(this.bookName(this.importPreview.incoming))} · ${this.importPreview.incoming.commands.length} Spells · ${esc(this.importPreview.report.capability)}</p><p>${esc(this.t("Expert-Ausführung:"))} ${this.importPreview.expert_allowed?this.t("AN (lokale Freigabe vorhanden)"):this.t("AUS")}</p>${this.button(this.t("Als eigenes Buch übernehmen"),"apply-new","","primary")}`:""}</section>`;}
  metadataView(){const b=this.metadataDraft || this.current.book;return `<section><h2>${esc(this.t("Buchdetails"))}</h2><p class="muted">${esc(this.t("Die Buch-ID bleibt unverändert:"))} ${esc(b.id)}</p>${["name","description","author","version","hase_min_version"].map(k=>`<label>${esc(({name:this.t("Name"),description:this.t("Beschreibung"),author:this.t("Autor"),version:this.t("Version"),language:this.t("Sprache"),hase_min_version:this.t("HASE-Mindestversion")})[k])}<input id="meta-${k}" value="${esc(["name","description"].includes(k)?this.editText(b[k]):b[k])}"></label>`).join("")}<label>${esc(this.t("Cover-Stil"))}<select id="meta-theme">${this.options(["sapphire","emerald","ember","amethyst"].map(id=>({id,name:({sapphire:this.t("Saphir"),emerald:this.t("Smaragd"),ember:this.t("Glut"),amethyst:this.t("Amethyst")})[id]})),bookTheme(b))}</select></label><div class="toolbar">${this.button(this.t("Speichern"),"save-metadata","","primary")}${this.button(this.t("Abbrechen"),"close-metadata")}</div></section>`;}
  diagnostics(r){return [...r.errors,...r.warnings].map(t=>`<p><small>${esc(this.messageText(t))}</small></p>`).join("") + ["user","expert"].map(v=>r[v]?`<p><small>${v==="user"?"User":"Expert"}: ${esc(this.messageText(reasons[r[v].reason_code]||r[v].reason_code))}</small></p>`:"").join("");}
  spellLanguageChoices(){return [...new Set([...(this.current?.languages||[]),this.effectiveBookLanguage(),this.validLanguage(this.language)||"en"])];}
  languageOptions(value,automatic=true){return `${automatic?`<option value="" ${value?"":"selected"}>${esc(this.t("Automatic — effective Spellbook language"))}</option>`:""}${this.spellLanguageChoices().map(code=>`<option value="${esc(code)}" ${code===value?"selected":""}>${esc(`${this.languageName(code)} (${code.toUpperCase()})`)}</option>`).join("")}`;}
  newSpellChoiceView(){
    if(this.newSpellStep===1)return this.newSpellWizardTypeView();
    return `<section class="new-spell-choice"><div class="toolbar">${this.button(this.t("← Back to Spellbook"),"cancel-new-spell")}</div><h2>${esc(this.t("How would you like to create your spell?"))}</h2><p class="muted">${esc(this.t("Both options use the same spell format. You can switch to the Manual Editor from the Wizard at any time."))}</p><label>${esc(this.t("Spell language"))}<small>${esc(this.t("Automatic uses the effective Spellbook language. You can explicitly choose another available language."))}</small><select id="new-spell-language">${this.languageOptions(null)}</select></label><div class="creation-choices"><button data-do="new-spell-wizard-menu" class="creation-choice primary"><strong>${esc(this.t("Wizard"))}</strong><span>${esc(this.t("Guided creation with explanations and validation. Create single actions and queries, or complex multi-step flows."))}</span></button><button data-do="new-spell-manual" class="creation-choice"><strong>${esc(this.t("Manual / Expert Mode"))}</strong><span>${esc(this.t("Create and edit the spell directly in the full editor."))}</span></button></div></section>`;
  }
  newSpellWizardTypeView(){
    return `<section class="new-spell-choice"><div class="toolbar">${this.button(this.t("← Back"),"new-spell-back")}</div><h2>${esc(this.t("What should your spell do?"))}</h2><div class="creation-choices"><button data-do="new-spell-wizard" class="creation-choice primary"><strong>${esc(this.t("Single action or query"))}</strong><span>${esc(this.t("Perform one action, or query information from Home Assistant."))}</span></button><button data-do="new-spell-steps" class="creation-choice"><strong>${esc(this.t("Combined Actions & Queries"))}</strong><span>${esc(this.t("Combine several actions and queries, reuse their results, and produce one combined response."))}</span></button></div></section>`;
  }
  targetChoices(action){return [{id:"none",name:this.t("No target")},{id:"area_name",name:this.t("Bereich aus dem gesprochenen Satz")},{id:"area_id",name:this.t("Einen Bereich auswählen")},{id:"entity_ids",name:this.t("Bestimmte Geräte auswählen")},{id:"entity_id",name:this.t("Ein Gerät auswählen")},{id:"all_allowed",name:this.t("Alle erlaubten Geräte dieses Typs")},{id:"source_area",name:this.t("Bereich des sprechenden Geräts")}].filter(v=>action.targets.includes(v.id));}
  wizardHelpButton(id){return `<button type="button" class="help-button" data-do="wizard-help" data-id="${esc(id)}" aria-expanded="${this.wizardHelp.has(id)}" aria-label="${esc(this.t("Show help"))}">?</button>`;}
  wizardHelpText(id,kind,operation=null){const query=operation==="entity.get_state",messages={name:this.t("Choose a short name that helps you recognize this spell in the Spellbook. Example: Aquamenti."),trigger:this.t("This is the phrase HASE listens for. Text in braces is captured from what you say. Example: aquamenti {target}."),action:this.t("The action describes what HASE should do. The available choices come from HASE's supported actions."),"action-domain":this.t("Narrows the action list to one device type. Devices that don't support a shown action (e.g. color on a light without color support) are hidden automatically once you've picked a device below."),target:kind==="all_allowed"?this.t("All permitted devices of this type may be affected. The selected client's permissions still apply."):kind==="area_name"?this.t("HASE takes the area from the spoken phrase. The trigger must contain the matching placeholder."):kind==="source_area"?this.t("HASE uses the Home Assistant area assigned to the device or client from which the command was spoken. No area name is needed in the phrase."):this.t("The target tells HASE where or on which device the action should happen."),response:query?this.t("Build the answer with the available return values below. HASE replaces them with live Home Assistant data when the query runs."):this.t("HASE can return text or speak a short confirmation. Trigger placeholders can be reused here."),"output-mode":this.t("Decide how the response is delivered. Automatic lets the calling client (Assist text chat, voice satellite, CODE27, …) decide what fits it best - choose Text/Speech/Both only if this spell needs one specific behavior, or None to answer silently."),test:this.t("Use a concrete phrase with real names instead of placeholders. The dry run checks the current draft without controlling devices.")};return this.wizardHelp.has(id)?`<div class="context-help" role="note">${esc(messages[id]||"")}</div>`:"";}
  queryResponseFieldNames(action){if(action?.operation!=="entity.get_state")return [];const selected=[...this.shadowRoot.querySelectorAll('input[name="entity"]:checked')].map(e=>e.value)[0]||this.edit?.target?.value;const entityId=Array.isArray(selected)?selected[0]:selected,domain=typeof entityId==="string"&&entityId.includes(".")?entityId.split(".",1)[0]:null,fields=action.response_fields||{};return [...new Set([...(fields["*"]||[]),...(domain?fields[domain]||[]:Object.values(fields).flat())])];}
  queryResponseLabel(name){return this.t(({state:"State",unit:"Unit",entity_name:"Device name",entity_id:"Entity ID",area:"Area",brightness:"Brightness (0–255)",brightness_percent:"Brightness (%)",color_temp_kelvin:"Color temperature (Kelvin)",rgb_color:"RGB color",effect:"Light effect",current_position:"Position (%)",current_temperature:"Current temperature",temperature:"Target temperature",hvac_action:"Current climate action",percentage:"Percentage",preset_mode:"Preset mode",volume_level:"Volume (0–1)",volume_percent:"Volume (%)",media_title:"Media title",media_artist:"Media artist"})[name]||name);}
  queryResponseChips(field,action){const names=this.queryResponseFieldNames(action);return names.length?`<div class="placeholder-chips query-placeholder-chips"><small>${esc(this.t("Return values:"))}</small>${names.map(name=>`<button type="button" data-do="insert-placeholder" data-id="${esc(`${field}:${name}`)}" title="${esc(this.queryResponseLabel(name))}">{${esc(name)}} · ${esc(this.queryResponseLabel(name))}</button>`).join("")}</div>`:"";}
  wizardStateResponseHelp(action){if(action?.operation!=="entity.get_state")return "";const names=this.queryResponseFieldNames(action);return `<div class="capture-explanation query-response-help" role="note"><strong>${esc(this.t("Configurable state response"))}</strong><p>${esc(this.t("Combine normal text with the return values offered below. Example: In {area} it is currently {state} {unit}."))}</p>${names.length?`<p>${esc(this.t("Available for the selected device:"))} ${names.map(name=>`{${esc(name)}}`).join(", ")}</p>`:""}</div>`;}
  triggerPlaceholderNames(){const text=this.value("triggers") || [this.editText(this.edit?.trigger),...(this.editText(this.edit?.triggers)||[])].filter(Boolean).join("\n");return [...new Set([...text.matchAll(/\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g)].map(match=>match[1]))];}
  wizardLocalIssues(){const issues=[],triggers=this.value("triggers").split("\n").map(v=>v.trim()).filter(Boolean),action=this.editorActions.find(a=>a.id===this.value("action")),kind=this.value("target-kind");if(!this.value("spell-name").trim())issues.push({field:"basic",level:"warning",message:this.t("Add a recognizable name so you can find this spell later.")});if(!triggers.length)issues.push({field:"trigger",level:"error",message:this.t("Add at least one phrase that should activate the spell.")});if(!action)issues.push({field:"action",level:"error",message:this.t("Choose what HASE should do.")});if(kind==="area_id"&&!this.value("target-area"))issues.push({field:"target",level:"error",message:this.t("Select the area where the action should happen.")});const entities=[...this.shadowRoot.querySelectorAll('input[name="entity"]:checked')];if(["entity_id","entity_ids"].includes(kind)&&!entities.length)issues.push({field:"target",level:"error",message:this.t("Select at least one device.")});if(action?.capability_hint&&["entity_id","entity_ids"].includes(kind)&&entities.length&&!entities.some(el=>{const flag=this.registry.entities.find(e=>e.id===el.value)?.capabilities?.[action.capability_hint];return Array.isArray(flag)?flag.length>0:Boolean(flag);}))issues.push({field:"target",level:"warning",message:this.t("The selected device may not support this function.")});const required=new Set();if(kind==="area_name"&&action)required.add(this.targetCapture(action,this.edit?.target?.capture));for(const spec of Object.values(action?.arguments||{}))if(spec?.capture)required.add(spec.capture);for(const name of required)if(triggers.some(line=>!line.includes(`{${name}}`)))issues.push({field:"target",level:"warning",message:this.t("Add {placeholder} to every trigger phrase so HASE knows which value you mean.",{placeholder:`{${name}}`})});const response=`${this.value("spell-output")} ${this.value("spell-catchphrase")}`;if(action?.operation==="entity.get_state"&&response.trim()&&!this.queryResponseFieldNames(action).some(name=>response.includes(`{${name}}`)))issues.push({field:"response",level:"warning",message:this.t("Add at least one return value such as {state}; otherwise HASE uses its automatic state answer.")});for(const name of this.triggerPlaceholderNames())if(!required.has(name)&&!response.includes(`{${name}}`))issues.push({field:"target",level:"warning",message:this.t("{placeholder} is captured by the trigger but is not used by the selected target, action, or response.",{placeholder:`{${name}}`})});return issues;}
  wizardBackendIssues(){const spell=this.wizardValidation?.spells?.[0];if(!spell)return [];return [...(spell.errors||[]).map(message=>({level:"error",message:this.messageText(message)})),...(spell.warnings||[]).map(message=>({level:"warning",message:this.messageText(message)}))].map(issue=>({...issue,field:/trigger|phrase|capture|Formulierung/i.test(issue.message)?"trigger":/target|ziel|bereich|gerät/i.test(issue.message)?"target":/action|aktion|parameter/i.test(issue.message)?"action":"review"}));}
  wizardIssues(field){return [...this.wizardLocalIssues(),...this.wizardBackendIssues()].filter(issue=>issue.field===field).map(issue=>`<div class="field-message ${issue.level}" role="${issue.level==="error"?"alert":"status"}">${esc(issue.message)}</div>`).join("");}
  wizardApplyDraft(){const u=this.collectUser(),action=this.editorActions.find(a=>a.id===u.action);if(!action)return;const next={...this.edit,name:this.putText(this.edit.name,u.name),trigger:this.putText(this.edit.trigger,u.triggers[0]||""),triggers:this.putText(this.edit.triggers,u.triggers.slice(1)),tags:u.tags,operation:action.operation,arguments:clone(action.arguments||{}),output_mode:u.output_mode||this.edit.output_mode};for(const [key,value] of Object.entries({description:u.description,examples:u.examples,output:u.output,catchphrase:u.catchphrase})){if(value?.length || next[key]){const translated=this.putText(next[key],value);if(typeof translated!=="object"||Object.keys(translated).length)next[key]=translated;else delete next[key];}}next.target={kind:u.target_kind};if(u.target_kind==="area_name")next.target.capture=this.targetCapture(action,this.edit?.target?.capture);else if(!["all_allowed","source_area","none"].includes(u.target_kind))next.target.value=u.target_value;this.edit=next;}
  wizardTargetSummary(kind){if(kind==="area_id")return this.registry.areas.find(a=>a.id===this.value("target-area"))?.name||this.t("No area selected");if(["entity_id","entity_ids"].includes(kind)){const ids=[...this.shadowRoot.querySelectorAll('input[name="entity"]:checked')].map(e=>e.value);return ids.map(id=>this.registry.entities.find(e=>e.id===id)?.name||id).join(", ")||this.t("No device selected");}const action=this.editorActions.find(a=>a.id===this.value("action"));return action?this.targetChoices(action).find(v=>v.id===kind)?.name||kind:kind;}
  wizardValidationSummary(){const issues=[...this.wizardLocalIssues(),...this.wizardBackendIssues()],errors=issues.filter(i=>i.level==="error").length,warnings=issues.filter(i=>i.level==="warning").length;const state=(field,label)=>`<li class="${issues.some(i=>i.field===field&&i.level==="error")?"bad":"good"}">${issues.some(i=>i.field===field&&i.level==="error")?"✕":"✓"} ${esc(label)}</li>`;return `<div class="validation-summary"><ul>${state("trigger",this.t("Trigger"))}${state("action",this.t("Action"))}${state("target",this.t("Target"))}${state("response",this.t("Response"))}</ul><strong>${errors} ${esc(this.t(errors===1?"error":"errors"))} · ${warnings} ${esc(this.t(warnings===1?"warning":"warnings"))}</strong></div>`;}
  // --- Combined Spells (steps) - item 6/7: a step-list builder for
  // multiple actions and queries in one spell, reusing the SAME backend
  // data (actionChoices/targetChoices/entity-capability metadata) the
  // single-action Wizard already uses - never a second hardcoded rule
  // table. The legacy 6-step single-action wizard above stays untouched;
  // this is a parallel, gated (this.editorMode==="steps") path.
  newStepDraft(kind){return kind==="action"?{alias:"",kind:"action",action:"",target_kind:"",target_value:null}:{alias:"",kind:"query",sources:[this.newSourceDraft()]};}
  stepsFromRaw(steps){return (steps||[]).map(step=>step.sources?{alias:step.alias||"",kind:"query",sources:step.sources.map(s=>({kind:s.kind,target_kind:s.target?.kind||"all_allowed",target_value:s.target?.value??null,domain_filter:s.domain_filter||"",device_class:s.device_class||"",attribute:s.attribute||"",state_filter:s.state_filter||"",variable:s.variable||"",unit_variable:s.unit_variable||""}))}:{alias:step.alias||"",kind:"action",action:this.info.actions.find(a=>a.operation===step.operation && JSON.stringify(a.arguments||{})===JSON.stringify(step.arguments||{}))?.id||"",target_kind:step.target?.kind||"",target_value:step.target?.value??null});}
  newSourceDraft(){return {kind:"count",target_kind:"all_allowed",target_value:null,domain_filter:"",device_class:"",attribute:"",state_filter:"",variable:"",unit_variable:""};}
  stepTargetChoices(action){return this.targetChoices({targets:(action.targets||[]).filter(k=>["area_id","entity_id","entity_ids","all_allowed","source_area"].includes(k))});}
  sourceTargetChoices(sourceKind){const caps=this.info.query_source_capabilities||{kinds:[],aggregate_kinds:[],single_target_kinds:[],aggregate_target_kinds:[]};const raw=caps.aggregate_kinds.includes(sourceKind)?caps.aggregate_target_kinds:caps.single_target_kinds;return this.targetChoices({targets:raw.filter(k=>["area_id","entity_id","all_allowed","source_area"].includes(k))});}
  stepTargetFieldView(prefix,kind,value){
    if(kind==="area_id")return `<label>${esc(this.t("Bereich"))}<select id="${prefix}-area"><option value="">${esc(this.t("Bereich auswählen"))}</option>${this.options(this.registry.areas,value)}</select></label>`;
    if(kind==="entity_id")return `<label>${esc(this.t("Gerät"))}<select id="${prefix}-entity"><option value="">${esc(this.t("Gerät auswählen"))}</option>${this.options(this.registry.entities,value)}</select></label>`;
    if(kind==="entity_ids"){const ids=Array.isArray(value)?value:[];return `<div><label>${esc(this.t("Geräte"))}</label><div class="checklist">${this.registry.entities.map(e=>`<label><input type="checkbox" name="entity" value="${esc(e.id)}" ${ids.includes(e.id)?"checked":""}> ${esc(e.name)}${e.area_name?` <small>· ${esc(e.area_name)}</small>`:""}</label>`).join("")}</div></div>`;}
    return "";
  }
  stepActionLabel(step){const action=this.actionChoices.find(a=>a.id===step.action);return action?this.t(action.label):this.t("(keine Aktion gewählt)");}
  stepTargetSummaryFor(kind,value){
    if(kind==="area_id")return this.registry.areas.find(a=>a.id===value)?.name||this.t("Kein Bereich gewählt");
    if(kind==="entity_id")return this.registry.entities.find(e=>e.id===value)?.name||this.t("Kein Gerät gewählt");
    if(kind==="entity_ids"){const ids=Array.isArray(value)?value:[];return ids.map(id=>this.registry.entities.find(e=>e.id===id)?.name||id).join(", ")||this.t("Kein Gerät gewählt");}
    return {all_allowed:this.t("Alle erlaubten Geräte dieses Typs"),source_area:this.t("Bereich des sprechenden Geräts")}[kind]||this.t("Kein Ziel gewählt");
  }
  sourceKindLabel(kind){return this.t({state:"Zustand",attribute:"Attribut",count:"Anzahl",min:"Minimum",max:"Maximum",avg:"Durchschnitt",sum:"Summe",names:"Gerätenamen",states:"Zustände"}[kind]||kind);}
  stepFieldLabel(field){return this.t({success:"Erfolgreich",state:"Zustand",entity_name:"Gerätename",entity_id:"Entity-ID",area:"Bereich",target_count:"Anzahl Ziele",success_count:"Erfolgreich",failed_count:"Fehlgeschlagen",failed_names:"Fehlgeschlagene Geräte",count:"Anzahl",names:"Gerätenamen",states:"Zustände",unit:"Einheit"}[field]||field);}
  stepSummary(step){if(step.kind==="query")return `${this.t("Abfrage")} · ${step.sources.length} ${this.t(step.sources.length===1?"Datenquelle":"Datenquellen")}`;return `${this.stepActionLabel(step)} · ${this.stepTargetSummaryFor(step.target_kind,step.target_value)}`;}
  stepListView(){const steps=this.edit.steps||[];return `<div class="steps-list">${steps.map((step,i)=>this.stepCardView(step,i)).join("")||`<p class="muted">${esc(this.t("Noch keine Aktionen oder Abfragen."))}</p>`}</div><div class="toolbar">${this.button(this.t("+ Aktion"),"add-step","action","primary")}${this.button(this.t("+ Abfrage"),"add-step","query")}</div>`;}
  stepCardView(step,index){
    if(index===this.editingStepIndex)return this.stepEditorView(step,index);
    return `<article class="step-card"><div><strong>${esc(step.alias||this.t("(ohne Namen)"))}</strong><p class="muted">${esc(this.stepSummary(step))}</p></div><div class="toolbar">${index>0?this.button("↑","move-step-up",index):""}${index<this.edit.steps.length-1?this.button("↓","move-step-down",index):""}${this.button(this.t("Bearbeiten"),"edit-step",index)}${this.button(this.t("Entfernen"),"remove-step",index,"danger")}</div></article>`;
  }
  stepEditorView(step,index){
    const isAction=step.kind==="action";
    const action=isAction?this.actionChoices.find(a=>a.id===step.action):null;
    const targetKind=step.target_kind||(action?this.stepTargetChoices(action)[0]?.id:"")||"";
    return `<article class="step-card step-card-open"><label><span>${esc(this.t("Name (optional)"))}</span><small>${esc(this.t("Namensraum für Antwort-Variablen, z. B. {name.feld}."))}</small><input id="step-alias" value="${esc(step.alias||"")}" placeholder="shutters"></label>
    ${isAction?this.stepActionSubForm(step,action,targetKind):this.stepQuerySubForm(step)}
    <div class="toolbar">${this.button(this.t("Übernehmen"),"close-step","","primary")}${this.button(this.t("Entfernen"),"remove-step",index,"danger")}</div></article>`;
  }
  stepActionSubForm(step,action,targetKind){
    const choices=this.visibleActionChoices(action?.id);
    return `${this.availableActionDomains().length?`<label>${esc(this.t("Gerätetyp"))}<select id="step-action-domain"><option value="">${esc(this.t("Alle"))}</option>${this.options(this.availableActionDomains().map(id=>({id,name:this.domainOptionLabel(id)})),this.wizardActionDomain)}</select></label>`:""}
    <label>${esc(this.t("Aktion"))}<select id="step-action">${this.options(choices,action?.id)}</select></label>
    <label>${esc(this.t("Wo?"))}<select id="step-target-kind">${this.options(this.stepTargetChoices(action||{targets:[]}),targetKind)}</select></label>
    ${this.stepTargetFieldView("step-target",targetKind,step.target_value)}`;
  }
  stepQuerySubForm(step){
    return `<div class="query-sources">${step.sources.map((source,j)=>this.sourceRowView(source,j,step.sources.length)).join("")}</div><div class="toolbar">${this.button(this.t("+ Datenquelle"),"add-source")}</div>`;
  }
  sourceRowView(source,j,total){
    const caps=this.info.query_source_capabilities||{kinds:[],aggregate_kinds:[]};
    const isAggregate=caps.aggregate_kinds.includes(source.kind);
    return `<fieldset class="source-row"><legend>${esc(this.t("Datenquelle {n}",{n:j+1}))}</legend>
    <label>${esc(this.t("Art"))}<select id="source-${j}-kind">${this.options(caps.kinds.map(id=>({id,name:this.sourceKindLabel(id)})),source.kind)}</select></label>
    <label>${esc(this.t("Wo?"))}<select id="source-${j}-target-kind">${this.options(this.sourceTargetChoices(source.kind),source.target_kind)}</select></label>
    ${this.stepTargetFieldView(`source-${j}-target`,source.target_kind,source.target_value)}
    <label>${esc(this.t("Gerätetyp (Domain, optional)"))}<input id="source-${j}-domain-filter" value="${esc(source.domain_filter||"")}" placeholder="binary_sensor"></label>
    <label>${esc(this.t("Geräteklasse (optional)"))}<input id="source-${j}-device-class" value="${esc(source.device_class||"")}" placeholder="opening"></label>
    ${source.kind==="attribute"?`<label>${esc(this.t("Attribut"))}<input id="source-${j}-attribute" value="${esc(source.attribute||"")}" placeholder="rgb_color"></label>`:""}
    ${isAggregate?`<label>${esc(this.t("Zustandsfilter (optional)"))}<input id="source-${j}-state-filter" value="${esc(source.state_filter||"")}" placeholder="on"></label>`:""}
    <label>${esc(this.t("Variablenname"))}<input id="source-${j}-variable" value="${esc(source.variable||"")}" placeholder="count"></label>
    ${source.kind==="state"?`<label>${esc(this.t("Einheit-Variablenname (optional)"))}<input id="source-${j}-unit-variable" value="${esc(source.unit_variable||"")}"></label>`:""}
    ${total>1?this.button(this.t("Datenquelle entfernen"),"remove-source",j,"danger"):""}</fieldset>`;
  }
  stepsResponseChips(field){
    const rows=(this.wizardValidation?.spell_variables||[]).filter(r=>r.source==="step_field");
    if(!rows.length)return "";
    const byAlias=new Map();
    for(const row of rows){if(!byAlias.has(row.alias))byAlias.set(row.alias,[]);byAlias.get(row.alias).push(row);}
    return `<div class="placeholder-chips step-response-chips">${[...byAlias.entries()].map(([alias,fields])=>`<div class="chip-group"><small>${esc(alias)}</small>${fields.map(row=>`<button type="button" data-do="insert-placeholder" data-id="${esc(`${field}:${row.name}`)}" title="${esc(this.stepFieldLabel(row.field))}">{${esc(row.name)}} · ${esc(this.stepFieldLabel(row.field))}</button>`).join("")}</div>`).join("")}</div>`;
  }
  stepsValidationSummary(){const issues=this.wizardBackendIssues();const errors=issues.filter(i=>i.level==="error").length,warnings=issues.filter(i=>i.level==="warning").length;return `<div class="validation-summary"><strong>${errors} ${esc(this.t(errors===1?"error":"errors"))} · ${warnings} ${esc(this.t(warnings===1?"warning":"warnings"))}</strong>${issues.map(i=>`<div class="field-message ${i.level}" role="${i.level==="error"?"alert":"status"}">${esc(i.message)}</div>`).join("")}</div>`;}
  stepsApplyDraft(){if(this.editingStepIndex!=null && this.edit.steps[this.editingStepIndex])this.collectStepDraft(this.editingStepIndex);}
  collectStepDraft(index){
    const step=this.edit.steps[index];
    if(!step)return;
    const alias=this.value("step-alias").trim();
    if(step.kind==="action"){
      const targetKind=this.value("step-target-kind");
      const targetValue=targetKind==="area_id"?this.value("step-target-area"):targetKind==="entity_id"?this.value("step-target-entity"):targetKind==="entity_ids"?[...this.shadowRoot.querySelectorAll('input[name="entity"]:checked')].map(e=>e.value):null;
      this.edit.steps[index]={...step,alias,action:this.value("step-action")||step.action,target_kind:targetKind||step.target_kind,target_value:targetValue};
    } else {
      const sources=step.sources.map((source,j)=>{
        const targetKind=this.value(`source-${j}-target-kind`)||source.target_kind;
        return {
          kind:this.value(`source-${j}-kind`)||source.kind,
          target_kind:targetKind,
          target_value:targetKind==="area_id"?this.value(`source-${j}-target-area`):targetKind==="entity_id"?this.value(`source-${j}-target-entity`):null,
          domain_filter:this.value(`source-${j}-domain-filter`).trim(),
          device_class:this.value(`source-${j}-device-class`).trim(),
          attribute:this.value(`source-${j}-attribute`).trim(),
          state_filter:this.value(`source-${j}-state-filter`).trim(),
          variable:this.value(`source-${j}-variable`).trim(),
          unit_variable:this.value(`source-${j}-unit-variable`).trim(),
        };
      });
      this.edit.steps[index]={...step,alias,sources};
    }
  }
  collectSteps(){
    this.stepsApplyDraft();
    const c=this.edit;
    const language=this.validLanguage(this.bookLanguage)||this.effectiveBookLanguage();
    const steps=(c.steps||[]).map(step=>step.kind==="action"
      ?{alias:step.alias||null,kind:"action",action:step.action,target_kind:step.target_kind,target_value:step.target_value}
      :{alias:step.alias||null,kind:"query",sources:step.sources.map(s=>({
          kind:s.kind,target_kind:s.target_kind,target_value:s.target_value,
          domain_filter:s.domain_filter||undefined,device_class:s.device_class||undefined,
          attribute:s.attribute||undefined,state_filter:s.state_filter||undefined,
          variable:s.variable,unit_variable:s.unit_variable||undefined,
        }))});
    return {
      id:c.id,
      translations:Object.fromEntries(["name","description","trigger","triggers","examples","output","catchphrase"].filter(key=>c[key]!==undefined).map(key=>[key,clone(c[key])])),
      trigger_language:language, default_language:this.storedBookLanguage()||this.effectiveBookLanguage(),
      name:this.value("spell-name"), description:this.value("spell-description"),
      examples:this.value("spell-examples").split("\n").filter(Boolean),
      triggers:this.value("triggers").split("\n").map(t=>t.trim()).filter(Boolean),
      tags:this.value("tags").split(",").map(t=>t.trim()).filter(Boolean),
      output:this.value("spell-output"), catchphrase:this.value("spell-catchphrase"),
      output_mode:this.value("output-mode")||undefined,
      steps,
    };
  }
  stepsWizardView(){
    const c=this.edit;
    const steps=[this.t("Basic information"),this.t("Trigger"),this.t("Actions & Queries"),this.t("Response"),this.t("Review & Test")];
    const step=this.wizardStep;
    const maxStep=this.wizardMaxStep??0;
    const explanation=[
      this.t("Give the spell a clear identity. HASE uses this information to help you recognize and organize it."),
      this.t("Define what you will say. HASE compares spoken words with these phrases and extracts values written in braces."),
      this.t("Add one or more actions and queries. Give each an optional name to reuse its result in the response below."),
      this.t("Combine free text with the variables your actions and queries produce."),
      this.t("Review the spell in plain language, validate it, and try a dry run without operating devices."),
    ][step];
    return `<section id="spell-editor" class="wizard"><div class="toolbar">${this.button(this.t("← Back to Spellbook"),"close-editor")}</div><h2>${esc(this.t("Create a Combined Spell"))}</h2><ol class="wizard-steps">${steps.map((label,index)=>{const cls=index===step?"current":index<step?"done":"",inner=`<span>${index+1}</span>${esc(label)}`;return `<li class="${cls}">${index<=maxStep?`<button type="button" class="step-button" data-do="wizard-goto" data-id="${index}" aria-current="${index===step?"step":"false"}">${inner}</button>`:`<div class="step-button" aria-disabled="true">${inner}</div>`}</li>`;}).join("")}</ol><div class="step-intro"><h3>${esc(steps[step])}</h3><p>${esc(explanation)}</p></div>
    <div class="wizard-step" ${step===0?"":"hidden"}><label><span>${esc(this.t("Name"))}</span><input id="spell-name" value="${esc(this.editText(c.name)||this.editText(c.trigger))}" placeholder="Gute Nacht"></label><label>${esc(this.t("Beschreibung"))}<textarea id="spell-description">${esc(this.editText(c.description))}</textarea></label><label>${esc(this.t("Sammlungen"))}<small>${esc(this.t("Mit Kommas trennen."))}</small><input id="tags" value="${esc((c.tags||[]).join(", "))}"></label></div>
    <div class="wizard-step" ${step===1?"":"hidden"}><label><span>${esc(this.t("Wenn ich sage …"))}</span><small>${esc(this.t("Eine Formulierung pro Zeile."))}</small><textarea id="triggers" placeholder="gute nacht">${esc([this.editText(c.trigger),...(this.editText(c.triggers)||[])].filter(Boolean).join("\n"))}</textarea></label>${this.placeholderChips()}</div>
    <div class="wizard-step" ${step===2?"":"hidden"}>${this.stepListView()}</div>
    <div class="wizard-step" ${step===3?"":"hidden"}><div><label><span>${esc(this.t("Text response"))}</span><textarea id="spell-output">${esc(this.editText(c.output))}</textarea></label>${this.placeholderChips("spell-output")}${this.stepsResponseChips("spell-output")}</div><div><label>${esc(this.t("Spoken response"))}<textarea id="spell-catchphrase">${esc(this.editText(c.catchphrase))}</textarea></label>${this.placeholderChips("spell-catchphrase")}${this.stepsResponseChips("spell-catchphrase")}</div><label>${esc(this.t("Output"))}<select id="output-mode">${this.outputModeOptions(c.output_mode)}</select></label>${this.button(this.t("Variablen aktualisieren"),"wizard-validate")}</div>
    <div class="wizard-step" ${step===4?"":"hidden"}><div class="spell-review"><h3>${esc(this.t("When I say:"))}</h3><blockquote>${esc(this.value("triggers").split("\n").find(Boolean)||this.editText(c.trigger)||this.t("No trigger yet"))}</blockquote><h3>${esc(this.t("Steps:"))}</h3><ul>${(c.steps||[]).map(s=>`<li>${esc(s.alias||"–")}: ${esc(this.stepSummary(s))}</li>`).join("")||`<li>${esc(this.t("Noch keine Aktionen oder Abfragen."))}</li>`}</ul></div>${this.stepsValidationSummary()}<div class="test-panel"><div class="book-context wizard-test-context"><label>${esc(this.t("Testen mit Client"))} <select id="wizard-profile"><option value="">${esc(this.t("Kein Client ausgewählt"))}</option>${this.options(this.testProfiles(),this.profile)}</select></label><label>${esc(this.t("Herkunftsbereich"))} <select id="wizard-source"><option value="">${esc(this.t("Nicht angegeben"))}</option>${this.options(this.registry.areas,this.source)}</select></label></div><label><span>${esc(this.t("Testphrase"))}</span><small>${esc(this.t("Platzhalter durch echte Werte ersetzen."))}</small><input id="phrase" value="${esc(this.localized(c.trigger))}" placeholder="gute nacht"></label><div class="toolbar">${this.button(this.t("Validate spell"),"wizard-validate")}${this.button(this.t("Testen – ohne Geräteaufruf"),"test-edit","","primary")}${this.editExisting?this.button(this.t("Jetzt wirklich ausführen …"),"execute"):""}</div></div></div>
    <div class="wizard-nav">${step?this.button(this.t("Back"),"wizard-back"):"<span></span>"}<span>${esc(this.t("Step {current} of {total}",{current:step+1,total:steps.length}))}</span>${step<steps.length-1?this.button(this.t("Next"),"wizard-next","","primary"):this.button(this.t("Speichern"),"save","","primary")}</div></section>`;
  }
  wizardView(){const c=this.edit,a=this.editorActions.find(row=>row.operation===c.operation&&JSON.stringify(row.arguments)===JSON.stringify(c.arguments||{}))||this.editorActions[0],kind=this.value("target-kind")||c.target?.kind||a.targets[0],steps=[this.t("Basic information"),this.t("Trigger"),this.t("Action"),this.t("Target / resolver"),this.t("Response"),this.t("Review & Test")],step=this.wizardStep,maxStep=this.wizardMaxStep??0,explanation=[this.t("Give the spell a clear identity. HASE uses this information to help you recognize and organize it."),this.t("Define what you will say. HASE compares spoken words with these phrases and extracts values written in braces."),this.t("Choose what should happen after the phrase matches. These are the same safe actions used by the Manual Editor."),this.t("Choose where the action happens. HASE needs this to resolve permitted areas or devices."),this.t("Decide what HASE should return after the spell. Responses are optional and may reuse captured words."),this.t("Review the spell in plain language, validate it, and try a dry run without operating devices.")][step];return `<section id="spell-editor" class="wizard"><div class="toolbar">${this.button(this.t("← Back to Spellbook"),"close-editor")}${this.button(this.t("Open in Manual Editor"),"open-manual")}</div><h2>${esc(this.t("Create a new spell"))}</h2><ol class="wizard-steps">${steps.map((label,index)=>{const cls=index===step?"current":index<step?"done":"",inner=`<span>${index+1}</span>${esc(label)}`;return `<li class="${cls}">${index<=maxStep?`<button type="button" class="step-button" data-do="wizard-goto" data-id="${index}" aria-current="${index===step?"step":"false"}">${inner}</button>`:`<div class="step-button" aria-disabled="true">${inner}</div>`}</li>`;}).join("")}</ol><div class="step-intro"><h3>${esc(steps[step])}</h3><p>${esc(explanation)}</p></div>
    <div class="wizard-step" ${step===0?"":"hidden"}><label>${esc(this.t("Spell language"))}<small>${esc(this.t("The language is initialized before translated spell fields are stored."))}</small><select id="wizard-language">${this.languageOptions(this.wizardLanguageExplicit)}</select></label><label><span>${esc(this.t("Name"))}${this.wizardHelpButton("name")}</span><input id="spell-name" value="${esc(this.editText(c.name)||this.editText(c.trigger))}" placeholder="Aquamenti"></label>${this.wizardHelpText("name",kind)}${this.wizardIssues("basic")}<label>${esc(this.t("Beschreibung"))}<textarea id="spell-description" placeholder="${esc(this.t("A short note about what this spell does."))}">${esc(this.editText(c.description))}</textarea></label><label>${esc(this.t("Examples"))}<small>${esc(this.t("One example per line."))}</small><textarea id="spell-examples">${esc((this.editText(c.examples)||[]).join("\n"))}</textarea></label><label>${esc(this.t("Sammlungen"))}<small>${esc(this.t("Mit Kommas trennen. Sammlungen gehören zu diesem Buch."))}</small><input id="tags" value="${esc((c.tags||[]).join(", "))}"></label></div>
    <div class="wizard-step" ${step===1?"":"hidden"}><label><span>${esc(this.t("Wenn ich sage …"))}${this.wizardHelpButton("trigger")}</span><small>${esc(this.t("Eine Formulierung pro Zeile."))}</small><textarea id="triggers" placeholder="aquamenti {target}">${esc([this.editText(c.trigger),...(this.editText(c.triggers)||[])].filter(Boolean).join("\n"))}</textarea></label>${this.wizardHelpText("trigger",kind)}${this.placeholderChips()}${this.triggerPlaceholderNames().length?`<div class="capture-explanation"><strong>${esc(this.t("Detected placeholders:"))}</strong> ${this.triggerPlaceholderNames().map(name=>`<code>{${esc(name)}}</code>`).join(" ")}<p>${esc(this.t("HASE takes these words from the spoken phrase. Each one needs a compatible target or action capture."))}</p></div>`:""}${this.wizardIssues("trigger")}</div>
    <div class="wizard-step" ${step===2?"":"hidden"}>${this.availableActionDomains().length?`<label><span>${esc(this.t("Gerätetyp"))}${this.wizardHelpButton("action-domain")}</span><select id="wizard-action-domain"><option value="">${esc(this.t("Alle"))}</option>${this.options(this.availableActionDomains().map(id=>({id,name:this.domainOptionLabel(id)})),this.wizardActionDomain)}</select></label>${this.wizardHelpText("action-domain",kind)}`:""}<label><span>${esc(this.t("Aktion"))}${this.wizardHelpButton("action")}</span><select id="action">${this.options(this.visibleActionChoices(a.id),a.id)}</select></label>${this.wizardHelpText("action",kind)}${this.lightColorPickerView(a.operation,c.arguments)}${this.wizardIssues("action")}</div>
    <div class="wizard-step" ${step===3?"":"hidden"}><label><span>${esc(this.t("Wo?"))}${this.wizardHelpButton("target")}</span><select id="target-kind">${this.options(this.targetChoices(a),kind)}</select></label>${this.wizardHelpText("target",kind)}<label id="area-field">${esc(this.t("Bereich"))}<select id="target-area"><option value="">${esc(this.t("Bereich auswählen"))}</option>${this.options(this.registry.areas,c.target?.value)}</select></label><div id="entities-field"><label>${esc(this.t("Geräte"))}</label><label class="entity-filter">${esc(this.t("Geräte filtern"))}<input id="wizard-entity-filter" type="search" autocomplete="off" placeholder="${esc(this.t("Geräte filtern …"))}" value="${esc(this.wizardEntityFilter)}"></label><div class="checklist">${this.registry.entities.map(e=>{const search=`${e.name||""} ${e.id||""} ${e.area_name||""}`.toLocaleLowerCase();const hidden=this.wizardEntityFilter.trim()&&!search.includes(this.wizardEntityFilter.trim().toLocaleLowerCase());return `<label data-entity-search="${esc(search)}" ${hidden?"hidden":""}><input type="checkbox" name="entity" value="${esc(e.id)}" ${(Array.isArray(c.target?.value)?c.target.value:[c.target?.value]).includes(e.id)?"checked":""}> ${esc(e.name)}${e.area_name?` <small>· ${esc(e.area_name)}</small>`:""}${e.selectable===false?` <small>(${esc(this.t("nicht verfügbar"))})</small>`:""}</label>`;}).join("")}</div></div>${this.wizardIssues("target")}</div>
    <div class="wizard-step" ${step===4?"":"hidden"}>${this.wizardStateResponseHelp(a)}<div><label><span>${esc(this.t("Text response"))}${this.wizardHelpButton("response")}</span><textarea id="spell-output">${esc(this.editText(c.output))}</textarea></label>${this.wizardHelpText("response",kind,a.operation)}<div id="chips-spell-output">${this.placeholderChips("spell-output")}${this.queryResponseChips("spell-output",a)}</div></div><div><label>${esc(this.t("Spoken response"))}<textarea id="spell-catchphrase" placeholder="Aquamenti.">${esc(this.editText(c.catchphrase))}</textarea></label><div id="chips-spell-catchphrase">${this.placeholderChips("spell-catchphrase")}${this.queryResponseChips("spell-catchphrase",a)}</div></div><label><span>${esc(this.t("Output"))}${this.wizardHelpButton("output-mode")}</span><select id="output-mode">${this.outputModeOptions(c.output_mode)}</select></label>${this.wizardHelpText("output-mode",kind)}${this.wizardIssues("response")}</div>
    <div class="wizard-step" ${step===5?"":"hidden"}><div class="spell-review"><h3>${esc(this.t("When I say:"))}</h3><blockquote>${esc(this.value("triggers").split("\n").find(Boolean)||this.editText(c.trigger)||this.t("No trigger yet"))}</blockquote><h3>${esc(this.t("HASE will:"))}</h3><p>${esc(this.t(a.label))}</p><h3>${esc(this.t("Target:"))}</h3><p>${esc(this.wizardTargetSummary(kind))}</p><h3>${esc(this.t("Response:"))}</h3><p>${esc(this.value("spell-catchphrase")||this.editText(c.catchphrase)||this.value("spell-output")||this.editText(c.output)||this.t("No response configured"))}</p></div>${this.wizardValidationSummary()}${this.wizardIssues("review")}<div class="test-panel"><div class="book-context wizard-test-context"><label>${esc(this.t("Testen mit Client"))} <select id="wizard-profile"><option value="">${esc(this.t("Kein Client ausgewählt"))}</option>${this.options(this.testProfiles(),this.profile)}</select></label><label>${esc(this.t("Herkunftsbereich"))} <select id="wizard-source"><option value="">${esc(this.t("Nicht angegeben"))}</option>${this.options(this.registry.areas,this.source)}</select></label></div><label><span>${esc(this.t("Testphrase"))}${this.wizardHelpButton("test")}</span><small>${esc(this.t("Platzhalter durch echte Werte ersetzen, z. B. lumos Gondor."))}</small><input id="phrase" value="${esc(this.localized(c.trigger))}" placeholder="${esc(this.t("Lumos Wohnzimmer"))}"></label>${this.wizardHelpText("test",kind)}<div class="toolbar">${this.button(this.t("Validate spell"),"wizard-validate")}${this.button(this.t("Testen – ohne Geräteaufruf"),"test-edit","","primary")}</div></div></div>
    <div class="wizard-nav">${step?this.button(this.t("Back"),"wizard-back"):"<span></span>"}<span>${esc(this.t("Step {current} of {total}",{current:step+1,total:steps.length}))}</span>${step<steps.length-1?this.button(this.t("Next"),"wizard-next","","primary"):this.button(this.t("Speichern"),"save","","primary")}</div></section>`;}
  editorView(){const c=this.edit; const a=this.editorActions.find(a=>a.operation===c.operation && JSON.stringify(a.arguments)===JSON.stringify(c.arguments||{})) || this.editorActions[0];
    const kind=c.target?.kind || "area_id";const technical=c.expert || {operation:c.operation||a.operation,target:{kind:"template",value:"{{ [] }}"},arguments:c.arguments||a.arguments};
    return `<section id="spell-editor"><div class="toolbar">${this.button(this.t("← Back to Spellbook"),"close-editor")}${this.editExisting?this.button(this.t("Duplizieren"),"duplicate",c.id):""}${this.editExisting?this.button(this.t("Löschen"),"delete",c.id,"danger"):""}${this.editExisting?this.button(this.t("Exportieren"),"export-one",c.id):""}</div><h2>${this.editExisting?this.t("Spell bearbeiten"):this.t("Neuer Spell")}</h2><label>${esc(this.t("Name"))}<input id="spell-name" value="${esc(this.editText(c.name)||this.editText(c.trigger))}"></label><label>${esc(this.t("Wenn ich sage …"))}<small>${esc(this.t("Eine Formulierung pro Zeile."))}</small><textarea id="triggers">${esc([this.editText(c.trigger),...(this.editText(c.triggers)||[])].filter(Boolean).join("\n"))}</textarea></label>${this.placeholderChips()}<label>${esc(this.t("Sammlungen"))}<small>${esc(this.t("Mit Kommas trennen. Sammlungen gehören zu diesem Buch."))}</small><input id="tags" value="${esc((c.tags||[]).join(", "))}"></label>
    <div class="split"><label>${esc(this.t("Beschreibung"))}<textarea id="spell-description">${esc(this.editText(c.description))}</textarea></label><label>${esc(this.t("Examples"))}<textarea id="spell-examples">${esc((this.editText(c.examples)||[]).join("\n"))}</textarea></label><div><label>${esc(this.t("Text response"))}<textarea id="spell-output">${esc(this.editText(c.output))}</textarea></label><div id="chips-spell-output">${this.placeholderChips("spell-output")}</div></div><div><label>${esc(this.t("Spoken response"))}<textarea id="spell-catchphrase">${esc(this.editText(c.catchphrase))}</textarea></label><div id="chips-spell-catchphrase">${this.placeholderChips("spell-catchphrase")}</div></div></div><label>${esc(this.t("Output"))}<select id="output-mode">${this.outputModeOptions(c.output_mode)}</select></label><div class="split"><div><h3>${esc(this.t("Dann …"))}</h3><label>${esc(this.t("Aktion"))}<select id="action">${this.options(this.actionChoices,a.id)}</select></label><label>${esc(this.t("Wo?"))}<select id="target-kind">${this.options(this.targetChoices(a),kind)}</select></label><label id="area-field">${esc(this.t("Bereich"))}<select id="target-area"><option value="">${esc(this.t("Bereich auswählen"))}</option>${this.options(this.registry.areas,c.target?.value)}</select></label><div id="entities-field"><label>${esc(this.t("Geräte"))}</label><div class="checklist">${this.registry.entities.map(e=>`<label><input type="checkbox" name="entity" value="${esc(e.id)}" ${(Array.isArray(c.target?.value)?c.target.value:[c.target?.value]).includes(e.id)?"checked":""}> ${esc(e.name)}${e.area_name?` <small>· ${esc(e.area_name)}</small>`:""}${this.expert?` <small>${esc(e.id)}</small>`:""}${e.selectable===false?` <small>(${esc(this.t("nicht verfügbar"))})</small>`:""}</label>`).join("")}</div></div></div><div><h3>${esc(this.t("Ohne Ausführung testen"))}</h3><label>${esc(this.t("Testphrase"))}<small>${esc(this.t("Platzhalter durch echte Werte ersetzen, z. B. lumos Gondor."))}</small><input id="phrase" value="${esc(this.localized(c.trigger))}" placeholder="${esc(this.t("Lumos Wohnzimmer"))}"></label><p class="muted">${esc(this.t("Die Prüfung verwendet die Freigaben des ausgewählten Clients."))}</p>${this.button(this.t("Testen – ohne Geräteaufruf"),"test-edit","","primary")}${this.expert?`<label>${esc(this.t("Variante"))}<select id="variant"><option value="runtime">${esc(this.t("Aktuelle Runtime-Freigabe"))}</option><option value="user">${esc(this.t("User-Variante"))}</option><option value="expert">${esc(this.t("Expert-Vorschau ohne Freigabeänderung"))}</option></select></label>`:""}</div></div>
    ${this.expert?`<details open><summary>${esc(this.t("Expert-Erweiterung"))}</summary><p class="muted">${esc(this.t("Diese Bearbeitung aktiviert keine Expert-Ausführung. Eine vorhandene User-Version bleibt standardmäßig erhalten."))}</p><label><input id="expert-enable" type="checkbox" ${c.expert?"checked":""}> ${esc(this.t("Expert-Variante speichern"))}</label><label>${esc(this.t("Technische Aktion"))}<select id="expert-operation">${this.options(this.info.operations.map(v=>({id:v,name:v})),technical.operation)}</select></label><label>${esc(this.t("Target-Art"))}<select id="expert-kind">${this.options(["template","entity_id","entity_ids","area_id","area_name","all_allowed","source_area"].map(v=>({id:v,name:v})),technical.target?.kind)}</select></label><label>${esc(this.t("Dynamische Zielauswahl / feste IDs"))}<small>${esc(this.t("Bei entity_ids eine ID pro Zeile."))}</small><textarea class="code" id="template">${esc(Array.isArray(technical.target?.value)?technical.target.value.join("\n"):technical.target?.value)}</textarea></label><label>${esc(this.t("Argumente (JSON)"))}<textarea class="code" id="arguments">${esc(JSON.stringify(technical.arguments||{},null,2))}</textarea></label><div><label>${esc(this.t("Expert spoken response"))}<textarea id="expert-catchphrase">${esc(this.editText(c.expert?.catchphrase))}</textarea></label><div id="chips-expert-catchphrase">${this.placeholderChips("expert-catchphrase")}</div></div><label>${esc(this.t("User-Fallback"))}<select id="fallback"><option value="keep">${esc(this.t("Bestehende User-Version behalten"))}</option><option value="update">${esc(this.t("User-Version mit obigem Formular aktualisieren"))}</option><option value="remove">${esc(this.t("Nur Expert-Version behalten"))}</option></select></label>${this.button(this.t("Template / Targets prüfen"),"test-template")}${this.editExisting?this.button(this.t("Gespeicherte YAML-Vorschau"),"yaml",c.id):""}</details>`:""}
    <div class="toolbar">${this.button(this.t("Speichern"),"save","","primary")}${this.button(this.t("Zurücksetzen"),"abort-editor","","abort-editor")}${this.button(this.t("Abbrechen"),"close-editor")}${this.editExisting?this.button(this.t("Jetzt wirklich ausführen …"),"execute"):""}</div></section>`;
  }
  resultView(inModal=false){const r=this.result,queryAnswer=r.data?.response_text??(r.status==="QUERY_ANSWER"&&r.data?.state!=null?`${r.data.state}${r.data.unit||""}`:null);return `<section class="result" role="status">${inModal?`<div class="modal-head"><h2 id="modal-title">${esc(this.messageText(reasons[r.reason_code] || r.title || this.t("Prüfergebnis")))}</h2>${this.button("×","close-result","","modal-close")}</div><div class="modal-body">`:`<h2>${esc(this.messageText(reasons[r.reason_code] || r.title || this.t("Prüfergebnis")))}</h2>`}${queryAnswer!=null?`<div class="capture-explanation"><strong>${esc(this.t("Returned answer"))}</strong><p>${esc(queryAnswer)}</p></div>`:""}${r.data?.user_fallback?`<p>${esc(this.t("User-Fallback wurde verwendet."))}</p>`:""}${r.data?.entity_ids?`<p><strong>${r.data.entity_ids.length}</strong> ${esc(this.t("effektive Ziele"))}</p><ul>${r.data.entity_ids.map(id=>`<li>${esc(this.registry.entities.find(e=>e.id===id)?.name|| (this.expert?id:this.t("Unbekanntes Gerät")))}</li>`).join("")}</ul>`:""}${(r.data?.warnings||[]).map(w=>`<p>${esc(this.messageText(w))}</p>`).join("")}${this.expert?`<details open><summary>${esc(this.t("Resolver / Policy / Rohes und effektives Ergebnis"))}</summary><h3>${esc(this.t("Rohes Ergebnis:"))} ${Array.isArray(r.data?.raw)?r.data.raw.length:"–"} ${esc(this.t("Ziele"))}</h3><h3>${esc(this.t("Effektives HASE-Ergebnis:"))} ${r.data?.entity_ids?.length ?? 0} ${esc(this.t("Ziele"))}</h3><ul>${(r.data?.targets||[]).map(t=>`<li>${esc(this.registry.entities.find(e=>e.id===t.entity_id)?.name || t.entity_id || "Template")} · ${esc(this.registry.areas.find(a=>a.id===t.area_id)?.name || this.t("Ohne Bereich"))} · ${esc(this.messageText(reasons[t.reason]||t.reason))}</li>`).join("")}</ul><pre>${esc(JSON.stringify(r,null,2))}</pre></details>`:""}${r.yaml?`<pre>${esc(r.yaml)}</pre>`:""}${r.portability?`<p>${r.portability.length} ${esc(this.t("instanzabhängige Ziele im Export."))}</p>`:""}${inModal?`</div><div class="modal-actions">${this.button(this.t("Schließen"),"close-result","","primary")}</div>`:this.button(this.t("Schließen"),"close-result")}</section>`;}
  importView(){const preview=this.importPreview;return `<section><div class="toolbar">${this.button(this.t("← Zur Liste"),"close-editor")}</div><h2>${esc(this.t("In {name} importieren",{name:this.bookName(this.current.book)}))}</h2><p>${esc(this.t("Eine Datei hier ablegen, auswählen oder YAML einfügen. Das gewählte Buch bleibt eigenständig."))}</p><input id="file" type="file" accept=".yaml,.yml,.txt"><label>YAML / Text<textarea id="import-text" class="code" style="min-height:220px">${esc(this.importText||"")}</textarea></label>${this.button(this.t("Import analysieren"),"analyze","","primary")}
    ${preview?`<h3>${preview.incoming.commands.length} ${esc(this.t("Spells erkannt"))}</h3><p>${esc(this.t("Expert-Ausführung bleibt {state}.",{state:this.t(preview.expert_allowed?this.t("AN"):this.t("AUS"))}))}</p><label>${esc(this.t("Für alle Konflikte"))}<select id="batch-conflict">${this.options([{id:"keep",name:this.t("Vorhandene behalten")},{id:"replace",name:this.t("Importierte ersetzen")},{id:"copy",name:this.t("Als Kopien hinzufügen")},{id:"skip",name:this.t("Überspringen")}],"keep")}</select></label>${preview.conflicts.map(id=>`<label>${esc(id)}<select data-conflict="${esc(id)}">${this.options([{id:"keep",name:this.t("Vorhandenen behalten")},{id:"replace",name:this.t("Importierten ersetzen")},{id:"copy",name:this.t("Als Kopie hinzufügen")},{id:"skip",name:this.t("Überspringen")}],"keep")}</select></label>`).join("")}
    ${preview.suggestions.length?`<h3>${esc(this.t("Lokale Zuordnungen prüfen"))}</h3>${preview.suggestions.map((s,i)=>`<label>${esc(s.value)} → <select data-correction="${i}"><option value="">${esc(this.t("Unverändert lassen"))}</option>${this.options((s.options||[]),null)}</select><small>${esc(this.t("Vorschläge:"))} ${esc(s.suggestions.join(", ")||this.t("Keine eindeutige Zuordnung"))}</small></label>`).join("")}${this.button(this.t("Zuordnungen übernehmen und neu prüfen"),"correct")}`:""}
    <details><summary>${esc(this.t("Prüfung pro Spell"))}</summary>${preview.report.spells.map(r=>`<h4>${esc(r.id)} · ${esc(r.capability)}</h4>${this.diagnostics(r)}`).join("")}</details><div class="toolbar">${this.button(this.t("Vorschau übernehmen"),"apply-import","","primary")}</div>`:""}</section>`;}
  placeholderNames(){const names=new Set(["area","entity","value","target"]);for(const variant of [this.edit,this.edit?.expert]){if(variant?.target?.capture)names.add(variant.target.capture);for(const spec of Object.values(variant?.arguments || {}))if(spec?.capture)names.add(spec.capture);}return [...names].filter(name=>/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(name));}
  responsePlaceholderNames(){const field=this.shadowRoot.getElementById?.("triggers");const text=field?field.value:[this.editText(this.edit?.trigger),...(this.editText(this.edit?.triggers)||[])].filter(Boolean).join("\n");const lines=text.split("\n").filter(line=>line.trim());const captures=lines.map(line=>new Set([...line.matchAll(/\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g)].map(match=>match[1])));return captures.length?[...captures[0]].filter(name=>captures.every(names=>names.has(name))):[];}
  refreshResponseChips(){for(const field of ["spell-output","spell-catchphrase","expert-catchphrase"]){const container=this.shadowRoot.getElementById(`chips-${field}`);if(container)container.innerHTML=this.placeholderChips(field);}}
  placeholderChips(field="triggers"){const names=field==="triggers"?this.placeholderNames():this.responsePlaceholderNames();return `<div class="placeholder-chips" role="group" aria-label="${esc(this.t("Platzhalter einfügen"))}"><small>${esc(this.t("Platzhalter einfügen"))}</small>${names.map(name=>`<button type="button" data-do="insert-placeholder" data-id="${esc(field==='triggers'?name:field+':'+name)}" aria-label="${esc(this.t("{placeholder} einfügen",{placeholder:`{${name}}`}))}">{${esc(name)}}</button>`).join("")}</div>`;}
  insertPlaceholder(id){const [target,name]=id.includes(":")?id.split(":"):["triggers",id];if(!["triggers","spell-output","spell-catchphrase","expert-catchphrase"].includes(target))return;const field=this.shadowRoot.getElementById(target);const stepFieldNames=(this.wizardValidation?.spell_variables||[]).filter(r=>r.source==="step_field").map(r=>r.name);if(!field || !(target==="triggers"?this.placeholderNames():[...this.responsePlaceholderNames(),...stepFieldNames]).includes(name))return;const start=field.selectionStart ?? field.value.length,end=field.selectionEnd ?? start;const before=field.value.slice(0,start),after=field.value.slice(end);const token=(!before || /\s$/.test(before)?"":" ")+`{${name}}`+(!after || /^[\s.,!?;:]/.test(after)?"":" ");field.focus();field.setRangeText(token,start,end,"end");field.dispatchEvent(new Event("input",{bubbles:true,composed:true}));}
  testPhrase(){const phrase=this.value("phrase").trim();if(!phrase || /[{}\[\]]/.test(phrase))throw Error(this.t("Bitte eine konkrete Testphrase ohne Platzhalter oder Klammern eingeben, z. B. „lumos Gondor“ statt „lumos {area}“."));return phrase;}
  collectUser(){const entities=[...this.shadowRoot.querySelectorAll('input[name="entity"]:checked')].map(e=>e.value);const kind=this.value("target-kind"),language=this.validLanguage(this.bookLanguage)||this.effectiveBookLanguage();return {id:this.edit.id,translations:Object.fromEntries(["name","description","trigger","triggers","examples","output","catchphrase"].filter(key=>this.edit[key]!==undefined).map(key=>[key,clone(this.edit[key])])),trigger_language:language,default_language:this.storedBookLanguage()||this.effectiveBookLanguage(),description:this.value("spell-description"),examples:this.value("spell-examples").split("\n").filter(Boolean),output:this.value("spell-output"),catchphrase:this.value("spell-catchphrase"),name:this.value("spell-name"),triggers:this.value("triggers").split("\n").map(t=>t.trim()).filter(Boolean),tags:this.value("tags").split(",").map(t=>t.trim()).filter(Boolean),action:this.value("action"),target_kind:kind,target_value:kind==="area_id"?this.value("target-area"):kind==="entity_ids"?entities:entities[0],output_mode:this.value("output-mode")||undefined};}
  collectSpell(runtimePreview=false){if(Array.isArray(this.edit.steps))return {mode:"steps",spell:this.collectSteps()};if(!this.expert)return {mode:"user",spell:this.collectUser()};const raw=clone(this.edit);raw.trigger=this.putText(raw.trigger,this.value("triggers").split("\n").map(v=>v.trim()).filter(Boolean)[0]);raw.triggers=this.putText(raw.triggers,this.value("triggers").split("\n").map(v=>v.trim()).filter(Boolean).slice(1)); raw.name=this.putText(raw.name,this.value("spell-name"));for(const [key,value] of Object.entries({description:this.value("spell-description"),examples:this.value("spell-examples").split("\n").filter(Boolean),output:this.value("spell-output"),catchphrase:this.value("spell-catchphrase")})){if(value || raw[key]){const translated=this.putText(raw[key],value);if(Object.keys(translated).length)raw[key]=translated;else delete raw[key];}}raw.tags=this.value("tags").split(",").map(t=>t.trim()).filter(Boolean);raw.output_mode=this.value("output-mode")||raw.output_mode||"AUTO";
    const fallback=this.value("fallback");
    // Saving with "keep" preserves the stored User fallback. Studio preview/live
    // execution must instead use the User form currently visible in the editor,
    // otherwise an old stored action (e.g. legacy light.set_power) is executed.
    if(runtimePreview || fallback==="update" || !this.editExisting){const u=this.collectUser(),a=this.editorActions.find(a=>a.id===u.action);raw.trigger=this.putText(this.edit.trigger,u.triggers[0]);raw.triggers=this.putText(raw.triggers,u.triggers.slice(1));if(a.id!=="__preserve__"){raw.operation=a.operation;raw.arguments=a.arguments;raw.target={kind:u.target_kind};if(u.target_kind==="area_name")raw.target.capture=this.targetCapture(a,this.edit?.target?.capture);else if(!["all_allowed","source_area"].includes(u.target_kind))raw.target.value=u.target_value;}}
    // Always build the expert draft currently visible in the editor. Whether it
    // is persisted is a separate decision (`persist_expert`) used only by save.
    const kind=this.value("expert-kind");const argsText=this.value("arguments").trim();raw.expert={...raw.expert,operation:this.value("expert-operation"),arguments:argsText?JSON.parse(argsText):{},target:{kind}};
    const expertReply=this.putText(raw.expert.catchphrase,this.value("expert-catchphrase"));if(Object.keys(expertReply).length)raw.expert.catchphrase=expertReply;else delete raw.expert.catchphrase;
    if(kind==="area_name")raw.expert.target.capture=this.operationTargetCapture(this.value("expert-operation"),this.edit?.expert?.target?.capture);
    else if(kind==="entity_ids")raw.expert.target.value=this.value("template").split("\n").map(v=>v.trim()).filter(Boolean);
    else if(!["all_allowed","source_area"].includes(kind))raw.expert.target.value=this.value("template");
    if(fallback==="remove"){delete raw.operation;delete raw.target;delete raw.arguments;delete raw.output;}
    // For an ephemeral runtime preview, "keep" means: use the User variant
    // currently visible in this editor as the fallback. The backend's normal
    // fallback_mode="keep" deliberately restores the STORED User layer, which
    // is correct for Save but wrong for an unsaved live test.
    const effectiveFallback=runtimePreview && fallback==="keep"?"update":fallback;
    return {mode:"expert",spell:raw,fallback_mode:effectiveFallback,remove_user:fallback==="remove",persist_expert:this.shadowRoot.getElementById("expert-enable")?.checked===true};}
  updateTargetControls(){const kind=this.value("target-kind");const area=this.shadowRoot.getElementById("area-field"),entities=this.shadowRoot.getElementById("entities-field");if(area)area.hidden=kind!=="area_id";if(entities)entities.hidden=!["entity_id","entity_ids"].includes(kind);}
  changeWizardLanguage(target){this.captureLanguageDraft();this.wizardLanguageExplicit=this.validLanguage(target.value);this.bookLanguage=this.effectiveBookLanguage(this.wizardLanguageExplicit);this.editLanguage=this.bookLanguage;this.render();}
  async readFile(file){if(file.size>1000000)throw Error(this.t("Datei zu groß (maximal 1 MB)."));this.importText=await file.text();this.importPreview=null;this.render();}
  async change(e){const t=e.target;if(t.id==="show-sidebar"){const result=await this.api("ui_settings",{show_in_sidebar:t.checked});this.info.show_in_sidebar=result.show_in_sidebar;return;}
    if(t.name==="access-mode"){this.accessDraft.mode=t.value;this.render(true);return;}
    if(t.dataset.accessGroup){t.checked?this.accessDraft.allowed_group_ids.add(t.dataset.accessGroup):this.accessDraft.allowed_group_ids.delete(t.dataset.accessGroup);return;}
    if(t.dataset.accessUser){t.checked?this.accessDraft.allowed_user_ids.add(t.dataset.accessUser):this.accessDraft.allowed_user_ids.delete(t.dataset.accessUser);return;}
    if(t.id==="book-search"){this.bookSearch=t.value;this.render();return;}if(t.id.startsWith("filter-")){this.filters[t.id.slice(7)]=t.value;this.render();}else if(t.dataset.select){t.checked?this.selected.add(t.dataset.select):this.selected.delete(t.dataset.select);}else if(["profile","wizard-profile"].includes(t.id)){this.profile=t.value;}else if(["source","wizard-source"].includes(t.id)){this.source=t.value;}else if(t.id==="target-kind"){this.updateTargetControls();const a=this.editorActions.find(a=>a.id===this.value("action"));if(t.value==="area_id")await this.ensureAreas();else if(["entity_id","entity_ids"].includes(t.value))await this.ensureEntitiesForAction(a,Array.isArray(this.edit?.target?.value)?this.edit.target.value:[this.edit?.target?.value]);}else if(t.id==="wizard-action-domain"){this.wizardActionDomain=t.value;this.render(true);}else if(t.id==="file" && t.files[0]){await this.readFile(t.files[0]);}else if(t.id==="batch-conflict"){this.shadowRoot.querySelectorAll("[data-conflict]").forEach(s=>s.value=t.value);}else if(t.id==="import-text"){this.importText=t.value;this.importPreview=null;}else if(t.id==="action"){const u=this.collectUser(),a=this.editorActions.find(a=>a.id===u.action);if(a.id==="__preserve__")return;const targetKind=a.targets.includes(u.target_kind)?u.target_kind:a.targets.find(t=>t!=="entity_name");const nextTarget={kind:targetKind};if(targetKind==="area_name")nextTarget.capture=this.targetCapture(a,this.edit?.target?.capture);else if(!["all_allowed","source_area"].includes(targetKind))nextTarget.value=u.target_value;this.edit={...this.edit,name:this.putText(this.edit.name,u.name),trigger:this.putText(this.edit.trigger,u.triggers[0]),triggers:this.putText(this.edit.triggers,u.triggers.slice(1)),tags:u.tags,operation:a.operation,arguments:a.arguments,target:nextTarget};this.registry.entities=[];this.entityDomainsKey=null;this.render();if(targetKind==="area_id")await this.ensureAreas();else if(["entity_id","entity_ids"].includes(targetKind))await this.ensureEntitiesForAction(a,Array.isArray(u.target_value)?u.target_value:[u.target_value]);}else if(t.id==="expert-operation"){const args=this.shadowRoot.getElementById("arguments");if(args)args.value=JSON.stringify(this.expertArgumentDefaults(t.value),null,2);}
    else if(t.id==="step-action-domain"){this.wizardActionDomain=t.value;this.render(true);}
    else if(t.id==="step-action"){const step=this.edit.steps[this.editingStepIndex];if(!step)return;const actionRow=this.actionChoices.find(a=>a.id===t.value);if(!actionRow)return;const choices=this.stepTargetChoices(actionRow);const targetKind=choices.some(v=>v.id===step.target_kind)?step.target_kind:choices[0]?.id||"";step.action=t.value;step.target_kind=targetKind;step.target_value=null;this.render();if(targetKind==="area_id")await this.ensureAreas();else if(["entity_id","entity_ids"].includes(targetKind))await this.ensureEntitiesForAction(actionRow,[]);}
    else if(t.id==="step-target-kind"){const step=this.edit.steps[this.editingStepIndex];if(!step)return;step.target_kind=t.value;step.target_value=null;this.render();if(t.value==="area_id")await this.ensureAreas();else if(["entity_id","entity_ids"].includes(t.value))await this.ensureEntitiesForAction(this.actionChoices.find(a=>a.id===step.action)||{operation:null},[]);}
    else if(/^source-\d+-target-kind$/.test(t.id)){const j=Number(t.id.match(/\d+/)[0]);const step=this.edit.steps[this.editingStepIndex];const source=step?.sources[j];if(!source)return;source.target_kind=t.value;source.target_value=null;this.render();if(t.value==="area_id")await this.ensureAreas();else if(t.value==="entity_id")await this.ensureEntitiesForAction({operation:null},[]);}
    else if(/^source-\d+-kind$/.test(t.id)){const j=Number(t.id.match(/\d+/)[0]);const step=this.edit.steps[this.editingStepIndex];const source=step?.sources[j];if(source){source.kind=t.value;this.render();}}
    else if(t.id==="action-color-picker"){this.edit={...this.edit,operation:"light.set_color",arguments:{rgb_color:this.hexToRgb(t.value)}};this.render(true);}
    else if(t.id==="action-color-name"){const name=t.value.trim();if(!name)return;this.edit={...this.edit,operation:"light.set_color",arguments:{color_name:name}};this.render(true);}
  }
  download(text,name){const url=URL.createObjectURL(new Blob([text],{type:"application/yaml"}));const a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  startNewSpell(mode,explicitLanguage=null){this.editExisting=false;this.wizardLanguageExplicit=this.validLanguage(explicitLanguage);this.bookLanguage=this.effectiveBookLanguage(this.wizardLanguageExplicit);this.editLanguage=this.bookLanguage;const id=globalThis.crypto?.randomUUID?.()??`spell-${Date.now()}-${Math.random().toString(16).slice(2)}`;this.edit=mode==="steps"?{id,trigger:"",steps:[]}:{id,trigger:"",target:{kind:"area_id"},operation:"light.turn_on",arguments:{}};this.editorMode=mode;this.wizardStep=0;this.wizardMaxStep=0;this.wizardHelp.clear();this.wizardValidation=null;this.wizardEntityFilter="";this.newSpellChoice=false;this.newSpellStep=0;this.result=null;this.registry.entities=[];this.entityDomainsKey=null;this.editingStepIndex=null;this.beginEditorSession();this.render();this.ensureEditorTargets().catch(e=>this.fail(e));}
  askConfirmation(text){this.rememberModalFocus();return new Promise(resolve=>{this.pendingConfirm={text,resolve};this.render();});}
  async act(action,id){
    if(action==="confirm-yes" || action==="confirm-no"){const pending=this.pendingConfirm;this.pendingConfirm=null;const focus=this.modalReturnFocus;this.modalReturnFocus=null;this.render();queueMicrotask(()=>{if(focus?.isConnected)focus.focus?.();});pending?.resolve(action==="confirm-yes");return;}
    if(this.pendingConfirm)return;
    if(action==="insert-placeholder"){this.insertPlaceholder(id);return;}
    if(action==="wizard-help"){this.wizardHelp.has(id)?this.wizardHelp.delete(id):this.wizardHelp.add(id);this.render(true);return;}
    this.message=null;
    if(action==="book-language"){if(this.metadataOpen){for(const field of ["name","description"])this.metadataDraft[field]=this.putText(this.metadataDraft[field],this.value(`meta-${field}`));for(const field of ["author","version","hase_min_version"])this.metadataDraft[field]=this.value(`meta-${field}`);this.metadataDraft.metadata={...this.metadataDraft.metadata,visual:{theme:this.value("meta-theme")}};}this.captureLanguageDraft();this.bookLanguage=id;this.render();for(const saved of this.sharedForm || []){const elements=saved.id?[this.shadowRoot.getElementById(saved.id)]:[...this.shadowRoot.querySelectorAll('input[name="entity"]')].filter(el=>el.value===saved.value);for(const el of elements)if(el){el.value=saved.value;el.checked=saved.checked;}}this.sharedForm=null;this.updateTargetControls();return;}
    if(action==="languages"){this.languagesOpen=!this.languagesOpen;this.render();return;}
    if(["add-language","remove-language","default-language"].includes(action)){const code=action==="add-language"?this.value("language-code").trim().toLowerCase():this.bookLanguage,source=this.value("language-source")||null;if((this.edit||this.metadataOpen)&&!await this.askConfirmation(this.t("Unsaved changes will be discarded. Continue?")))return;if(action==="remove-language"&&!await this.askConfirmation(this.t("Remove this language and its translations?")))return;await this.api("manage_language",{action:action.split("-")[0]==="default"?"default":action.split("-")[0],code,source,confirmed:true});this.bookLanguage=action==="remove-language"?null:code;return this.refresh();}
    if(["delete-book","reset-book"].includes(action)){if(!await this.askConfirmation(this.t(action==="reset-book"?"Remove the custom book and PNG and restore the built-in original?":"Delete this custom book and its PNG? Assigned clients will need another book.")))return;const result=await this.api(action==="reset-book"?"reset_book":"delete_book",{confirmed:true});this.current=null;this.edit=null;await this.refresh();if(result.restored)await this.open(result.book_id);return;}
    if(action==="settings"){this.settingsOpen=!this.settingsOpen;if(this.settingsOpen)this.userManagementOpen=false;this.render();return;}
    if(action==="user-management"){
      this.userManagementOpen=!this.userManagementOpen;
      if(this.userManagementOpen){
        this.settingsOpen=false;
        if(!this.access){
          this.access=await this.api("access_get");
          this.accessDraft={mode:this.access.config.mode,
                            allowed_user_ids:new Set(this.access.config.allowed_user_ids),
                            allowed_group_ids:new Set(this.access.config.allowed_group_ids)};
        }
      }
      this.render();
      return;
    }
    if(action==="access-save"){
      if(!await this.askConfirmation(this.t("Zugriffseinstellungen wirklich speichern? Der Zugriffsmodus gilt sofort für alle Benutzer.")))return;
      this.access=await this.api("access_set",{mode:this.accessDraft.mode,
                                                allowed_user_ids:[...this.accessDraft.allowed_user_ids],
                                                allowed_group_ids:[...this.accessDraft.allowed_group_ids]});
      this.accessDraft={mode:this.access.config.mode,
                        allowed_user_ids:new Set(this.access.config.allowed_user_ids),
                        allowed_group_ids:new Set(this.access.config.allowed_group_ids)};
      this.render();
      return;
    }
    if(action==="access-cancel"){
      this.accessDraft={mode:this.access.config.mode,
                        allowed_user_ids:new Set(this.access.config.allowed_user_ids),
                        allowed_group_ids:new Set(this.access.config.allowed_group_ids)};
      this.render();
      return;
    }
    if(action==="dismiss"){this.closeModal();return;}
    if(action==="expert"){if(!this.expert && !await this.askConfirmation(this.t("Expert Mode öffnet technische Bearbeitung. Die Ausführungsfreigabe pro Buch bleibt unverändert.")))return;this.expert=!this.expert;this.render();return;}
    if(action==="open"){if(!await this.confirmDiscardEditor())return;return this.open(id);}
    if(action==="refresh")return this.refresh();
    if(action==="home"){if(!await this.confirmDiscardEditor())return;this.current=null;this.edit=null;this.editLanguage=undefined;this.result=null;this.clearEditorSession();return this.refresh();}
    if(action==="new-book"){this.creating=true;this.render();return;}
    if(action==="create-book"){await this.api("create_book",{book_id:this.value("book-id"),name:this.value("book-name"),language:this.language});this.creating=false;return this.refresh();}
    if(action==="new-spell"){if(this.edit&&this.editorDirty&&!await this.confirmDiscardEditor())return;this.newSpellChoice=true;this.newSpellStep=0;this.newSpellLanguageChoice=null;this.render();return;}
    if(action==="cancel-new-spell"){this.newSpellChoice=false;this.newSpellStep=0;this.render();return;}
    if(action==="new-spell-wizard-menu"){this.newSpellLanguageChoice=this.value("new-spell-language");this.newSpellStep=1;this.render();return;}
    if(action==="new-spell-back"){this.newSpellStep=0;this.render();return;}
    if(["new-spell-wizard","new-spell-steps","new-spell-manual"].includes(action)){this.startNewSpell(action==="new-spell-wizard"?"wizard":action==="new-spell-steps"?"steps":"manual",this.value("new-spell-language")||this.newSpellLanguageChoice);return;}
    if(action==="edit"){if(this.edit&&this.editorDirty&&id!==this.edit.id&&!await this.confirmDiscardEditor())return;this.editExisting=true;this.edit=clone(this.current.book.commands.find(c=>c.id===id));this.editLanguage=contentLanguage(this.edit.trigger,this.language);this.editorMode=Array.isArray(this.edit.steps)?"steps":"manual";if(this.editorMode==="steps")this.edit.steps=this.stepsFromRaw(this.edit.steps);this.wizardStep=0;this.wizardMaxStep=this.editorMode==="steps"?4:0;this.newSpellChoice=false;this.result=null;this.registry.entities=[];this.entityDomainsKey=null;this.editingStepIndex=null;this.beginEditorSession();this.render();this.ensureEditorTargets().catch(e=>this.fail(e));return;}
    if(action==="open-manual"){this.wizardApplyDraft();this.editorMode="manual";this.render();return;}
    if(action==="wizard-back"||action==="wizard-next"){const isSteps=Array.isArray(this.edit.steps);if(isSteps)this.stepsApplyDraft();else this.wizardApplyDraft();const maxStep=isSteps?4:5;this.wizardStep=Math.max(0,Math.min(maxStep,this.wizardStep+(action==="wizard-next"?1:-1)));this.wizardMaxStep=Math.max(this.wizardMaxStep??0,this.wizardStep);this.render();await this.ensureEditorTargets();return;}
    if(action==="wizard-goto"){const target=Number.parseInt(id,10);if(!Number.isInteger(target)||target<0||target>(this.wizardMaxStep??0))return;if(Array.isArray(this.edit.steps))this.stepsApplyDraft();else this.wizardApplyDraft();this.wizardStep=target;this.render();await this.ensureEditorTargets();return;}
    if(action==="wizard-validate"){if(Array.isArray(this.edit.steps))this.stepsApplyDraft();else this.wizardApplyDraft();this.wizardValidation=await this.api("validate",this.collectSpell(true));this.render();return;}
    if(["add-step","edit-step","close-step","remove-step","move-step-up","move-step-down","add-source","remove-source"].includes(action)){
      this.stepsApplyDraft();
      if(action==="add-step"){this.edit.steps=[...(this.edit.steps||[]),this.newStepDraft(id)];this.editingStepIndex=this.edit.steps.length-1;this.wizardActionDomain="";this.render();if(id==="action")await this.ensureAreas();else await this.ensureEntitiesForAction({operation:null},[]);return;}
      if(action==="edit-step"){this.editingStepIndex=Number.parseInt(id,10);this.wizardActionDomain="";this.render();await this.ensureAreas();const step=this.edit.steps[this.editingStepIndex];if(step?.kind==="action"){const actionRow=this.actionChoices.find(a=>a.id===step.action);await this.ensureEntitiesForAction(actionRow||{operation:null},Array.isArray(step.target_value)?step.target_value:[step.target_value].filter(Boolean));}else await this.ensureEntitiesForAction({operation:null},[]);return;}
      if(action==="close-step"){this.editingStepIndex=null;this.render();return;}
      if(action==="remove-step"){const index=Number.parseInt(id,10);this.edit.steps=this.edit.steps.filter((_,i)=>i!==index);if(this.editingStepIndex===index)this.editingStepIndex=null;else if(this.editingStepIndex>index)this.editingStepIndex--;this.render();return;}
      if(action==="move-step-up"||action==="move-step-down"){const index=Number.parseInt(id,10),swap=action==="move-step-up"?index-1:index+1;if(swap<0||swap>=this.edit.steps.length)return;const steps=[...this.edit.steps];[steps[index],steps[swap]]=[steps[swap],steps[index]];this.edit.steps=steps;if(this.editingStepIndex===index)this.editingStepIndex=swap;else if(this.editingStepIndex===swap)this.editingStepIndex=index;this.render();return;}
      if(action==="add-source"){const step=this.edit.steps[this.editingStepIndex];if(step)step.sources=[...step.sources,this.newSourceDraft()];this.render();return;}
      if(action==="remove-source"){const step=this.edit.steps[this.editingStepIndex];if(step&&step.sources.length>1)step.sources=step.sources.filter((_,i)=>i!==Number.parseInt(id,10));this.render();return;}
    }
    if(action==="close-editor"){if(!await this.confirmDiscardEditor())return;this.edit=null;this.editLanguage=undefined;this.importing=false;this.result=null;this.clearEditorSession();this.render();return;}
    if(action==="close-result"){this.closeModal();return;}
    if(action==="permission"){const allowed=!this.current.expert_allowed;if(!await this.askConfirmation(this.t("Expert-Ausführung für „{name}“ {state}?",{name:this.bookName(this.current.book),state:this.t(allowed?this.t("einschalten"):this.t("ausschalten"))})))return;await this.api("expert_permission",{allowed});this.current.expert_allowed=allowed;this.render();return;}
    if(action==="metadata"){this.metadataDraft=clone(this.current.book);this.metadataOpen=true;this.render();return;}
    if(action==="close-metadata"){this.metadataDraft=null;this.metadataOpen=false;this.render();return;}
    if(action==="save-metadata"){const metadata={};for(const key of ["name","description","author","version","hase_min_version"])metadata[key]=this.value(`meta-${key}`);for(const field of ["name","description"])metadata[field]=this.putText((this.metadataDraft || this.current.book)[field],metadata[field]);metadata.metadata={...this.current.book.metadata,visual:{theme:this.value("meta-theme")}};await this.api("save_metadata",{metadata});this.metadataOpen=false;return this.refresh();}
    if(action==="check-all"){this.allReport=await this.api("validate_all");this.render();return;}
    if(action==="check"){this.report=await this.api("validate");this.render();return;}
    if(["export","export-selected","export-tag","export-one","yaml"].includes(action)){if(action==="export-selected"&&!this.selected.size)throw Error(this.t("Bitte zuerst Spells auswählen."));const r=await this.api("export",{ids:action==="export-selected"?[...this.selected]:["export-one","yaml"].includes(action)?[id]:undefined,collection:action==="export-tag"?this.filters.tag:undefined});if(action!=="yaml")this.download(r.yaml,`${this.current.book.id}.yaml`);this.result={...r,title:action==="yaml"?this.t("YAML-Vorschau"):this.t("Export erstellt")};this.render();return;}
    if(action==="abort-editor"){if(!this.editorDirty)return;if(!await this.askConfirmation(this.t("Alle ungespeicherten Änderungen an diesem Spell verwerfen und den zuletzt gespeicherten Stand wiederherstellen?")))return;await this.restoreEditorBaseline();return;}
    if(action==="save"){const payload=this.collectSpell();if(payload.remove_user&&!await this.askConfirmation(this.t("Die User-Version dieses Spells wirklich entfernen?")))return;const saved=await this.api("save_spell",{...payload,spell_id:this.editExisting?this.edit.id:null});this.clearEditorSession();await this.refresh();this.report=saved.report;this.render();return;}
    if(["test-edit","test-template"].includes(action)){const phrase=this.testPhrase(),payload=this.collectSpell(true);this.rememberModalFocus();this.result=await this.api("dry_run",{...payload,phrase,variant:action==="test-template"?"expert":this.value("variant")||"runtime"});this.render();return;}
    if(action==="execute"){const phrase=this.testPhrase();const payload=this.collectSpell(true);this.rememberModalFocus();const expertAllowed=this.current?.expert_allowed===true||this.current?.expert_enabled===true||this.info?.expert_allowed===true;const spell=payload?.spell||{};const expert=spell.expert;const notice=payload.mode==="steps"?`\n\n${esc(this.t("Es werden {n} Schritte ausgeführt.",{n:(spell.steps||[]).length}))}`:this.expert&&expert?(expertAllowed?`\n\n${esc(this.t("Expert-Ausführung ist aktiviert."))}\n${esc(this.t("Es wird die Expert-Version ausgeführt:"))} ${expert.operation||this.t("Unbekannte Aktion")}`:`\n\n${esc(this.t("Expert-Ausführung ist deaktiviert."))}\n${esc(this.t("Es wird die User-Version (Fallback) ausgeführt:"))} ${spell.operation||this.t("Unbekannte Aktion")}`):`\n\n${esc(this.t("Es wird die User-Version ausgeführt:"))} ${spell.operation||this.t("Unbekannte Aktion")}`;if(!await this.askConfirmation(this.t("Aktuellen Editorstand mit „{phrase}“ jetzt wirklich ausführen? Geräte können ihren Zustand ändern. Nicht gespeicherte Änderungen gelten nur für diesen Test.",{phrase})+notice))return;this.result=await this.api("execute",{...payload,phrase,confirmed:true,variant:"runtime"});this.render();return;}
    if(["toggle","duplicate","delete"].includes(action)){if(this.edit&&this.editorDirty&&["duplicate","delete"].includes(action)){if(!await this.confirmDiscardEditor())return;}if(action==="delete"&&!await this.askConfirmation(this.t("Diesen Spell wirklich löschen?")))return;await this.api(`${action==="toggle"?"toggle":action==="duplicate"?"duplicate":"delete"}_spell`,{spell_id:id});this.clearEditorSession();return this.refresh();}
    if(action==="import-new"){this.importNew=true;this.importing=true;this.importPreview=null;this.importText="";this.render();return;}
    if(action==="analyze-new"){this.importText=this.value("import-text");this.importPreview=await this.api("import_book_preview",{text:this.importText});this.render();return;}
    if(action==="apply-new"){const saved=await this.api("import_book_apply",{text:this.importText});this.importNew=false;this.importing=false;await this.open(saved.book_id);this.report=saved.report;this.render();return;}
    if(action==="import"){this.importing=true;this.importPreview=null;this.importText="";this.render();return;}
    if(action==="analyze"){this.importText=this.value("import-text");this.importPreview=await this.api("import_preview",{text:this.importText});this.render();return;}
    if(action==="correct"){const raw=clone(this.importPreview.incoming);this.shadowRoot.querySelectorAll("[data-correction]").forEach(s=>{if(!s.value)return;const correction=this.importPreview.suggestions[Number(s.dataset.correction)];const c=raw.commands.find(c=>c.id===correction.id);(correction.variant==="expert"?c.expert:c).target.value=s.value;});this.importText=JSON.stringify(raw,null,2);this.importPreview=await this.api("import_preview",{text:this.importText});this.render();return;}
    if(action==="apply-import"){const decisions={};this.shadowRoot.querySelectorAll("[data-conflict]").forEach(s=>decisions[s.dataset.conflict]=s.value);const saved=await this.api("import_apply",{text:this.importText,decisions});await this.refresh();this.report=saved.report;this.render();return;}
  }
}
if(!customElements.get("hase-spellbook-studio"))customElements.define("hase-spellbook-studio",HaseSpellbookStudio);
