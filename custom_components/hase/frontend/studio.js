/* Shipped with HASE. Uses only the authenticated Home Assistant connection. */
import {escapeHtml as esc, icon, bookCover, bookTheme, filterBooks, actionIcons} from "./studio-visuals.js?v=0.3.27";
import {resolveBookValue, setBookValue, bookFallbacks} from "./studio-book-languages.js?v=0.3.27";
const BOOK_TABS = ["book","check","mcp","matching"];
const cssUrl = new URL("./studio.css?v=0.3.27", import.meta.url).href;
const clone = value => JSON.parse(JSON.stringify(value));
const extras = (value,known) => Object.fromEntries(Object.entries(value||{}).filter(([key])=>!known.includes(key)));
const sameData = (a,b) => a===b || !!(a&&b&&typeof a==="object"&&typeof b==="object"&&Array.isArray(a)===Array.isArray(b)&&Object.keys(a).length===Object.keys(b).length&&Object.keys(a).every(key=>Object.hasOwn(b,key)&&sameData(a[key],b[key])));
import {studioLanguage, translate, translateMessage, contentLanguage} from "./studio-i18n.js?v=0.3.27";
const reasons = {operation_disabled:"Diese Aktion ist für den ausgewählten Client ausgeschaltet.",vacuum_mapping_pending:"Bereich erlaubt; die Sauger-Zuordnung ist ohne Geräteaufruf noch nicht bestätigt.",dry_run:"Prüfung erfolgreich – keine Geräte bedient.",ok:"Home Assistant hat die Aktion angenommen.",no_match:"Kein passender Spell gefunden.",ambiguous:"Die Phrase ist mehrdeutig.",no_user_variant:"Keine User-Variante vorhanden.",expert_disabled:"Expert-Ausführung ist für dieses Buch ausgeschaltet.",target_not_found:"Das Ziel fehlt oder ist nicht freigegeben.",no_matching_targets:"Keine erlaubten Ziele gefunden.",area_not_permitted:"Der gewählte Client darf diesen Bereich nicht bedienen.",output_only:"Dieser Spell liefert Text an den Client. Keine typisierte HASE-Aktion zum Testen.",no_source_area:"Bitte den Herkunftsbereich für diesen Test auswählen.",template_error:"Das Template konnte nicht ausgewertet werden.",invalid_template_result:"Das Template muss eine Entity-ID oder eine Liste von Entity-IDs liefern.",bad_arguments:"Die Parameter passen nicht zur Aktion.",unknown_operation:"Diese Aktion wird von HASE nicht unterstützt.",allowed:"erlaubt",not_found:"nicht vorhanden",wrong_domain:"falscher Gerätetyp",unavailable:"nicht verfügbar",not_exposed_or_ineligible:"nicht freigegeben oder ungeeignet",ambiguous_source:"Mehrere passende Geräte gefunden – bitte genauer eingrenzen (z. B. Geräteklasse).",missing_attribute:"Dieses Attribut ist am Gerät nicht vorhanden.",empty_aggregation:"Keine passenden Geräte für diese Auswertung gefunden.",partial:"Antwort erzeugt – einzelne Datenquellen konnten nicht vollständig aufgelöst werden.",capability_not_supported:"Diese Funktion wird vom gewählten Gerät nicht unterstützt.",unresolvable_text:"Der Text lässt sich in keiner Sprache auflösen.",duplicate_spell_id:"Mehrere Spells verwenden dieselbe ID – mehrdeutig, wird nicht als Werkzeug bereitgestellt.",assist_redirect_requires_unrestricted_policy:"Benötigt eine uneingeschränkte Bridge (Aktionen UND Abfragen uneingeschränkt) – sonst wird der Aufruf verweigert, bevor Assist kontaktiert wird."};
const mcpExecutionKindLabels = {action:"Aktion",query:"Abfrage",combined:"Ablauf",text_answer:"Textantwort",assist_redirect:"Assist-Weiterleitung"};
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
const domainLabels = {light:"Licht",switch:"Schalter",input_boolean:"Schalt-Helfer",cover:"Abdeckung",climate:"Klima",fan:"Ventilator",lock:"Schloss",media_player:"Mediaplayer",vacuum:"Staubsauger",scene:"Szene",script:"Skript",automation:"Automatisierung",button:"Taster",number:"Zahl",input_number:"Zahl-Helfer",select:"Auswahl",input_select:"Auswahl-Helfer",timer:"Timer",todo:"Todo"};

class HaseSpellbookStudio extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode:"open"});
    this.expert = false;
    this.showDisabledBooks = false;
    this.diagnosticsOpen = false;
    this.executionDiagnostics = [];
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
    this.wizardActionDomain = null;
    this.wizardHelp = new Set();
    this.wizardValidation = null;
    // {step, issues} from the last "validate_step" call for the wizard
    // page being left - null once cleared (successful Next/goto, or the
    // user edits a field). wizardMaxStep now means "furthest step
    // reached via a SUCCESSFUL validate_step", not just "furthest
    // visited" - see wizard-next/wizard-goto in act().
    this.stepValidation = null;
    this.wizardLanguageExplicit = null;
    this.newSpellChoice = false;
    this.newSpellStep = 0;
    this.newSpellLanguageChoice = null;
    // Combined Spells (steps) - which step card is currently expanded
    // for editing, or null when the list is fully collapsed.
    this.editingStepIndex = null;
    // The open card owns an isolated form draft. `this.edit.steps` remains
    // the last valid canonical spell draft until the user presses Apply.
    this.editingStepDraft = null;
    this.stepEditorError = null;
    this.stepConversionIssue = null;

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
      const dialog = this.shadowRoot.querySelector(".studio-modal");
      if (dialog && e.key === "Tab") {
        const focusable = [...dialog.querySelectorAll("button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex='-1'])")];
        if (focusable.length) {
          const first = focusable[0], last = focusable.at(-1);
          if (e.shiftKey && (this.shadowRoot.activeElement === first || !dialog.contains(this.shadowRoot.activeElement))) {e.preventDefault();last.focus();return;}
          if (!e.shiftKey && (this.shadowRoot.activeElement === last || !dialog.contains(this.shadowRoot.activeElement))) {e.preventDefault();first.focus();return;}
        }
      }
      if (e.key === "Escape" && (this.result || this.message || this.pendingConfirm || this.reportDetail || this.mcpDetail)) {
        e.preventDefault();
        if (this.pendingConfirm) {
          this.act("confirm-no").catch(err => this.fail(err));
        } else {
          this.closeModal();
        }
        return;
      }
      const tab = e.target?.closest?.('[role="tab"][data-do="book-tab"]');
      if (tab && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
        e.preventDefault();
        const index = BOOK_TABS.indexOf(tab.dataset.id);
        const next = e.key === "Home" ? 0 : e.key === "End" ? BOOK_TABS.length - 1
          : (index + (e.key === "ArrowRight" ? 1 : -1) + BOOK_TABS.length) % BOOK_TABS.length;
        this.act("book-tab", BOOK_TABS[next]).catch(err => this.fail(err));
      }
    });

    this.shadowRoot.addEventListener("input", e => {
      this.clearBlockerHighlight();
      if(e.target.setCustomValidity)e.target.setCustomValidity("");
      if (this.isEditorField(e.target)) { this.setEditorDirty(true); if (!this.isResponseTextField(e.target)) { this.wizardValidation=null; this.stepValidation=null; } }
      if (e.target.id === "triggers") this.refreshResponseChips();
      if (e.target.id === "filter-search") this.updateSpellSearch(e.target);
      if (e.target.id === "wizard-entity-filter") this.updateWizardEntityFilter(e.target.value);
      if(e.target.closest?.(".step-card-open")){
        if(this.editingStepDraft&&e.target.id==="step-expert-arguments")this.editingStepDraft.argumentsText=e.target.value;
        this.stepEditorError=null;
      }
      if(e.target.closest?.(".step-card-open"))this.stepBlockers=(this.stepBlockers||[]).filter(b=>b.step_index!==this.editingStepIndex);
      this.refreshLocalHints();
    });

    this.shadowRoot.addEventListener("change", e => {
      if (this.isEditorField(e.target)) { this.setEditorDirty(true); if (!this.isResponseTextField(e.target)) { this.wizardValidation=null; this.stepValidation=null; } }
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
        if (file) try { await this.readFile(file); } catch (err) { this.fail(err); }
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
  captureLanguageDraft(){if(!this.edit)return;const triggers=this.value("triggers").split("\n").map(t=>t.trim()).filter(Boolean);this.sharedForm=[...this.shadowRoot.querySelectorAll("input,textarea,select")].filter(e=>(e.id || e.name) && !["spell-name","spell-description","spell-examples","spell-output","spell-catchphrase","spell-failure-text","triggers","phrase"].includes(e.id)).map(e=>({id:e.id,name:e.name,value:e.value,checked:e.checked}));for(const [key,value] of Object.entries({name:this.value("spell-name"),description:this.value("spell-description"),trigger:triggers[0],triggers:triggers.slice(1),examples:this.value("spell-examples").split("\n").filter(Boolean),output:this.value("spell-output"),catchphrase:this.value("spell-catchphrase"),failure_text:this.value("spell-failure-text")})){if(value!==undefined)this.edit[key]=this.putText(this.edit[key],value);}this.renderedEditId=null;}

  get editorActions(){return this.info.actions;}
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
  entityLabel(action,plural=false){if(action?.domain_filter==="todo")return this.t(plural?"Todo-Listen":"Todo-Liste");return this.t(plural?"Geräte":"Gerät");}
  entityExposureWarning(e){if(e.exposed!==false)return "";return ` <small class="entity-not-exposed">⚠ ${esc(this.t("Nicht für Assist freigegeben"))} · <button type="button" class="inline-link" data-do="expose-entity" data-id="${esc(e.id)}">${esc(this.t("Jetzt freigeben"))}</button></small>`;}
  currentTargetCapabilities(){const ids=[...this.shadowRoot.querySelectorAll('input[name="entity"]:checked')].map(e=>e.value);return ids.map(id=>this.registry.entities.find(e=>e.id===id)?.capabilities).filter(Boolean);}
  // The Gerätetyp shown for a step: an explicit user pick in this open step
  // editor, otherwise reconstructed from the step's saved action (its
  // domain_filter). "" ("Alle") only for a genuinely generic action.
  effectiveActionDomain(action){return this.wizardActionDomain!=null?this.wizardActionDomain:(action?.domain_filter||"");}
  // Targets are always narrowed by the ACTION's own domain (operation prefix),
  // independent of the stored/selected Gerätetyp, so a todo.* operation never
  // offers lights. Ids of another domain are dropped, never silently kept.
  entitiesForAction(action,keepIds=[]){
    const [domain]=this.actionDomains(action);
    if(!domain)return keepIds.filter(Boolean).reduce((acc,id)=>this.optionsWithCurrent(acc,id),this.registry.entities);
    const fits=id=>String(id).startsWith(domain+".");
    const rows=this.registry.entities.filter(e=>fits(e.id));
    return keepIds.filter(fits).reduce((acc,id)=>this.optionsWithCurrent(acc,id),rows);
  }
  // Switching Gerätetyp re-filters the Aktion list and the target list at once.
  // If the step's current action belongs to another domain, it moves to the
  // first action of the new domain and the (now invalid) target is cleared.
  async changeStepDomain(domain){
    this.wizardActionDomain=domain;
    let step=null;try{step=this.collectStepDraft(this.editingStepIndex);}catch(_err){step=null;}
    const current=step&&this.actionChoices.find(a=>a.id===step.action);
    if(step&&domain&&current&&current.domain_filter!==domain){
      const next=this.visibleActionChoices(null).find(a=>a.domain_filter===domain);
      if(next){
        const choices=(this.edit.steps.length===1?this.targetChoices(next):this.stepTargetChoices(next));
        const targetKind=choices.some(v=>v.id===step.target_kind)?step.target_kind:choices[0]?.id||"";
        this.editingStepDraft={...step,action:next.id,operation:next.operation,arguments:clone(next.arguments||{}),target_kind:targetKind,target_value:null};
        delete this.editingStepDraft.color_arguments;
        this.render(true);
        if(targetKind==="area_id")await this.ensureAreas();else if(["entity_id","entity_ids"].includes(targetKind))await this.ensureEntitiesForAction(next,[]);
        return;
      }
    }
    if(step)this.editingStepDraft=step;
    this.render(true);
  }
  visibleActionChoices(keepId=null){
    const domain=this.effectiveActionDomain(this.actionChoices.find(a=>a.id===keepId)),caps=this.currentTargetCapabilities();
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
  checked(id){return !!this.shadowRoot.getElementById(id)?.checked;}
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
    this.expertVariantText=null;
    this.editorBaseline = clone(this.edit);
    this.editorDirty = false;
    this.renderedEditId = null;
    this.sharedForm = null;
    this.editingStepIndex = null;
    this.editingStepDraft = null;
    this.stepEditorError = null;
    this.stepConversionIssue = null;
  }
  clearEditorSession() {
    this.editorDirty = false;
    this.editorBaseline = null;
    this.renderedEditId = null;
    this.sharedForm = null;
    this.editorMode = "manual";
    this.wizardStep = 0;
    this.wizardMaxStep = 0;
    this.wizardActionDomain = null;
    this.wizardHelp.clear();
    this.wizardValidation = null;
    this.stepValidation = null;
    this.wizardLanguageExplicit = null;
    this.newSpellChoice = false;
    this.newSpellStep = 0;
    this.newSpellLanguageChoice = null;
    this.editingStepIndex = null;
    this.editingStepDraft = null;
    this.stepEditorError = null;
    this.stepConversionIssue = null;
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
  async open(id){const previousId=this.current?.book?.id,previousLanguage=this.bookLanguage;this.current=await this.api("read",{book_id:id});const available=new Set([...(this.current.languages||[]),this.effectiveBookLanguage()]);this.bookLanguage=previousId===id&&available.has(previousLanguage)?previousLanguage:this.effectiveBookLanguage();this.profile=this.testProfiles()[0]?.id || ""; this.report=null; this.mcpReport=this.current.mcp_report||null; this.bookTab=this.loadBookTab(id); this.metadataOpen=false;this.metadataDraft=null; this.edit=null; this.editLanguage=undefined; this.wizardLanguageExplicit=null; this.importing=false; this.selected.clear(); this.filters={search:this.loadStoredSpellSearch()}; this.result=null; this.registry.areas=[];this.registry.entities=[];this.entityDomainsKey=null;this.render(); this.scrollTop=0; this.ensureAreas().catch(e=>this.fail(e));}
  async refresh(){const id=this.current?.book.id; this.info=await this.api("list"); if(id) await this.open(id); else {this.render();this.loadHomeCovers();}}
  get brandUrl(){return this.panel?.config?.brand_url || "/hase-studio-brand/icon.png";}
  button(label,action,id="",kind=""){return `<button class="${kind}" data-do="${action}" data-id="${esc(id)}">${actionIcons[action]?icon(actionIcons[action]):""}${label}</button>`;}
  options(rows,value){return rows.map(r=>`<option value="${esc(r.id)}" ${r.id===value?"selected":""}>${esc(r.name || r.label)}</option>`).join("");}
  outputModeOptions(value){const modes=this.info.output_modes||["AUTO","TEXT","SPEECH","TEXT_AND_SPEECH","NONE"];return this.options(modes.map(id=>({id,name:this.t(outputModeLabels[id]||id)})),value||this.info.default_output_mode||"AUTO");}
  // One color widget is shared by Guided and technical action fields.
  // Custom RGB/name arguments travel through the same step builder.
  rgbToHex(rgb){return "#"+(Array.isArray(rgb)&&rgb.length===3?rgb:[255,255,255]).map(c=>Math.max(0,Math.min(255,Number(c)||0)).toString(16).padStart(2,"0")).join("");}
  hexToRgb(hex){const m=/^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex||"");return m?[1,2,3].map(i=>Number.parseInt(m[i],16)):[255,255,255];}
  lightColorPickerView(operation,arguments_){
    if(operation!=="light.set_color")return "";
    const rgb=Array.isArray(arguments_?.rgb_color)?arguments_.rgb_color:null;
    const colorName=typeof arguments_?.color_name==="string"?arguments_.color_name:"";
    return `<div class="light-color-picker"><label><span>${esc(this.t("Farbe wählen"))}</span><input type="color" id="action-color-picker" value="${esc(this.rgbToHex(rgb))}"></label><label><span>${esc(this.t("Oder HA-Farbname eingeben (optional)"))}</span><small>${esc(this.t("Beliebiger von Home Assistant unterstützter Farbname, z. B. chartreuse."))}</small><input id="action-color-name" value="${esc(colorName)}" placeholder="chartreuse"></label></div>`;
  }
  rememberModalFocus(){const active=this.shadowRoot.activeElement;if(active)this.modalReturnFocus=active;}
  closeModal(){
    this.result=null; this.message=null; this.reportDetail=null; this.mcpDetail=null;
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
    if(this.reportDetail){
      return `<div class="modal-backdrop"><section class="studio-modal modal-result" role="dialog" aria-modal="true" aria-labelledby="modal-title">${this.reportDetailView(this.reportDetail)}</section></div>`;
    }
    if(this.mcpDetail){
      return `<div class="modal-backdrop"><section class="studio-modal modal-result" role="dialog" aria-modal="true" aria-labelledby="modal-title">${this.mcpDetailView(this.mcpDetail)}</section></div>`;
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
    return `${this.button(this.t("Studio-Einstellungen"),"settings")}${this.button(this.t("Ausführungsdiagnose"),"diagnostics")}${this.hass?.user?.is_admin?this.button(this.t("Nutzerverwaltung"),"user-management"):""}${this.button(this.t("Technische Werkzeuge")+(this.expert?" ✓":""),"expert")}`;
  }
  executionDiagnosticsView(){return `<section class="studio-settings"><h2>${esc(this.t("Ausführungsdiagnose"))}</h2><p class="muted">${esc(this.t("Die letzten 100 Ausführungen dieser HA-Sitzung; ohne Inhalte, Argumente und Zugangsdaten."))}</p>${this.button(this.t("Aktualisieren"),"diagnostics-refresh")}${this.button(this.t("Schließen"),"diagnostics-close")}${this.executionDiagnostics.length?`<div class="diagnostics-list">${this.executionDiagnostics.map(row=>`<article class="row"><div><strong>${esc(row.book_id||"–")} · ${esc(row.spell_id||"–")}</strong><p>${esc(row.timestamp||"")} · ${esc(row.channel||"")}</p></div><div><span class="badge">${esc(row.status||"–")}</span><p>${esc(row.reason_code||"")} · ${esc(row.duration_ms??0)} ms</p><small>${esc(row.error_type||"")}</small></div></article>`).join("")}</div>`:`<p>${esc(this.t("Noch keine Ausführungen erfasst."))}</p>`}</section>`;}
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
    // Step controls share IDs. Their values belong to the draft, never to
    // whichever different card happens to reuse that ID after a render.
    const variantField=this.shadowRoot.getElementById("expert-variant-json");
    if(variantField&&this.edit?.expert&&this.renderedEditId===this.edit.id)this.expertVariantText=variantField.value;
    const retained = preserveForm || this.edit && this.renderedEditId===this.edit.id ? [...this.shadowRoot.querySelectorAll("input,textarea,select")].filter(e=>(e.id || e.name)&&(!e.closest?.(".step-card-open")||this.message||this.pendingConfirm)).map(e=>({id:e.id,name:e.name,value:e.value,checked:e.checked})) : [];
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
    ${this.diagnosticsOpen?this.executionDiagnosticsView():""}${book?this.bookView():this.homeView()}</main>${this.modalView()}`
    const menu=this.shadowRoot.querySelector("ha-menu-button"); if(menu)menu.hass=this.hass;
    for(const saved of retained){const elements=saved.id?[this.shadowRoot.getElementById(saved.id)]:[...this.shadowRoot.querySelectorAll('input[name="entity"]')].filter(e=>e.value===saved.value);for(const el of elements){if(!el)continue;if(el.type==="checkbox")el.checked=saved.checked;else if(el.tagName!=="SELECT" || [...el.options].some(o=>o.value===saved.value))el.value=saved.value;}}
    this.renderedEditId=this.edit?.id; if(this.edit)this.refreshResponseChips(); this.updateDirtyUi();
    this.showBlockerHighlight(false);
    this.refreshLocalHints();
    const modal=this.shadowRoot.querySelector(".studio-modal");
    const background=this.shadowRoot.querySelector("main"), topbar=this.shadowRoot.querySelector("header");
    if(background)background.inert=Boolean(modal);
    if(topbar)topbar.inert=Boolean(modal);
    if(modal)queueMicrotask(()=>{const target=modal.querySelector(".primary,button,select,input,textarea,[tabindex]");target?.focus?.();});
  }
  homeView(){return `<div class="studio-hero"><img class="hero-logo" src="${esc(this.brandUrl)}" alt="${esc(this.t("HASE – Hase mit Zauberbuch"))}"><div class="hero-copy"><h1>HASE</h1><div class="hero-title">Spellbook Studio</div><p class="hero-tagline">${esc(this.t("Deine Worte. Deine Magie."))}</p><p class="muted">${esc(this.t("Eigenständige Bücher, gemeinsam verwaltet. Jedes Buch behält seine Spells und Freigaben."))}</p></div><div class="hero-charm" aria-hidden="true">✧</div></div><div class="overview-toolbar"><div class="toolbar">${this.button(this.t("Spellbook erstellen"),"new-book","","primary")}${this.button(this.t("Spellbook importieren"),"import-new")}${this.button(this.t("Alle Bücher prüfen"),"check-all")}${this.button(this.t("Aktualisieren"),"refresh")}</div><label class="setting-row"><input id="show-disabled-books" type="checkbox" ${this.showDisabledBooks?"checked":""}>${esc(this.t("Ausgeblendete anzeigen"))}</label><label class="book-search">${icon("search")}<input id="book-search" aria-label="${esc(this.t("Spellbooks suchen"))}" placeholder="${esc(this.t("Spellbooks suchen …"))}" value="${esc(this.bookSearch)}"></label></div><div class="grid">${filterBooks(this.info.books.filter(b=>this.showDisabledBooks||!b.disabled),this.bookSearch).map(b=>`<article class="card ${b.disabled?"book-disabled":""}">${bookCover(b)}<div class="card-body"><div class="eyebrow">${b.count} Spells</div><h2>${esc(this.bookName(b))}</h2>${b.disabled?`<span class="badge disabled-badge">${esc(this.t("Deaktiviert"))}</span>`:""}<span class="badge">${esc(b.capability)}</span><p class="card-meta">${icon("clients")}${b.active_clients ?? 0} ${esc(this.t("zugeordnete Clients"))}</p><p class="card-meta">${icon("book")}${esc(this.t("Expert-Ausführung:"))} <strong>${b.expert_allowed?this.t("AN"):this.t("AUS")}</strong></p>${this.button(this.t("Buch öffnen →"),"open",b.id,"primary open-book")}</div></article>`).join("")}<button class="card card-create" data-do="new-book"><span class="plus-ring">${icon("plus")}</span><strong>${esc(this.t("Neues Spellbook"))}</strong><span>${esc(this.t("Erstelle ein neues Buch"))}<br>${esc(this.t("und beginne zu zaubern."))}</span></button></div>${this.importNew?this.newImportView():""}${this.allReport?`<section><h2>${esc(this.t("Prüfung aller Bücher"))}</h2>${this.allReport.books.map(b=>`<p><strong>${esc(this.bookName(b))}</strong> · ${b.report.errors} ${esc(this.t("Schemafehler"))} · ${b.report.user_executable} ${esc(this.t("User ausführbar"))} · ${esc(b.profile || this.t("Kein Client konfiguriert"))}</p>`).join("")}</section>`:""}${this.creating?`<section><h2>${esc(this.t("Neues Spellbook"))}</h2><label>${esc(this.t("Name"))}<input id="book-name" placeholder="${esc(this.t("Mein Spellbook"))}"></label><label>${esc(this.t("Kurze Kennung"))}<input id="book-id" placeholder="mein_spellbook"></label>${this.button(this.t("Erstellen"),"create-book","","primary")}</section>`:""}`;}
  bookHeroView(){const b=this.current.book;return `<div class="back-link">${this.button(this.t("Alle Spellbooks"),"home")}</div><div class="book-hero">${bookCover({...b,cover:this.current.cover},{large:true})}<div class="book-identity"><div class="eyebrow">${esc(this.t("Eigenständiges Spellbook"))}</div><h1>${esc(this.bookName(b))}</h1><span class="badge">${esc(this.current.capability)}</span>${b.disabled?`<span class="badge disabled-badge">${esc(this.t("Deaktiviert"))}</span>`:""}<span class="badge">${b.commands.length} Spells</span>${this.button(this.t(b.disabled?"Spellbook reaktivieren":"Spellbook deaktivieren"),"toggle-book")}<p class="muted">${esc(this.localized(b.description) || this.t("Spells gestalten, prüfen und mit anderen teilen."))}</p><div class="book-context"><label>${esc(this.t("Testen mit Client"))} <select id="profile"><option value="">${esc(this.t("Kein Client ausgewählt"))}</option>${this.options(this.testProfiles(),this.profile)}</select></label><label>${esc(this.t("Herkunftsbereich"))} <select id="source"><option value="">${esc(this.t("Nicht angegeben"))}</option>${this.options(this.registry.areas,this.source)}</select></label><div class="expert-status ${this.current.expert_allowed?"is-on":""}"><div>${esc(this.t("Expert-Ausführung:"))} <strong>${this.current.expert_allowed?this.t("AN"):this.t("AUS")}</strong><small>${esc(this.t("Freigabe für dieses Buch"))}</small>${this.expert?this.button(this.t("Freigabe ändern"),"permission"):""}</div><span class="status-light" aria-hidden="true"></span></div></div></div></div>`;}
  compactBookContextView(){const spell=this.editText(this.edit?.name)||this.editText(this.edit?.trigger)||this.edit?.id||"";return `<section class="book-edit-context"><div><small>${esc(this.bookName(this.current.book))} › ${esc(this.t(this.editExisting?"Spell bearbeiten":"Spell erstellen"))}</small><strong>${esc(spell)}</strong></div><div class="toolbar">${this.button(this.t("← Back to Spellbook"),"close-editor")}${this.button(this.t(this.bookDetailsExpanded?"Buchdetails ausblenden":"Buchdetails anzeigen"),"toggle-book-details")}</div></section>`;}
  bookView(){if(this.edit)return `${this.compactBookContextView()}${this.bookDetailsExpanded?`${this.bookHeroView()}${this.bookTabsView()}`:""}${this.stepsWizardView()}`;return `${this.bookHeroView()}${this.bookTabsView()}${this.metadataOpen?this.metadataView():this.newSpellChoice?this.newSpellChoiceView():this.importing?this.importView():this.listView()}`;}
  languageView(){const stored=this.storedBookLanguage(),effective=this.effectiveBookLanguage(),inherited=!stored,codes=[...new Set([...(this.current.languages||[]),effective])],fallbacks=bookFallbacks(this.current.book,this.bookLanguage,stored||effective);return `<section class="book-languages"><div class="toolbar"><strong>${esc(this.t("Book language"))}</strong>${codes.map(code=>`<button data-do="book-language" data-id="${esc(code)}" aria-pressed="${code===this.bookLanguage}">${esc(`${this.languageName(code)} (${code.toUpperCase()})${inherited&&code===effective?"*":""}`)}</button>`).join("")}${inherited?this.wizardHelpButton("inherited-language"):""}${this.button(this.t("+ Language"),"languages")}</div>${inherited&&this.wizardHelp.has("inherited-language")?`<div class="context-help" role="note">${esc(this.t("* System language — this Spellbook has no saved default language."))}</div>`:""}${this.languagesOpen?`<label>${esc(this.t("New language code"))}<input id="language-code" placeholder="fr"></label><label>${esc(this.t("Copy from (optional)"))}<select id="language-source"><option value="">${esc(this.t("Empty translation"))}</option>${this.options(codes.map(id=>({id,name:`${this.languageName(id)} (${id.toUpperCase()})`})),"")}</select></label><div class="toolbar">${this.button(this.t("Add language"),"add-language")}${this.button(this.t("Use as default"),"default-language")}${this.button(this.t("Remove selected language"),"remove-language","","danger")}</div>`:""}${fallbacks.length?`<details><summary>${esc(this.t("Translation fallbacks"))}: ${fallbacks.length}</summary>${fallbacks.map(row=>`<p><small>${esc(row.id)} · ${esc(row.field)}: ${esc(this.bookLanguage?.toUpperCase())} → ${esc(row.used?.toUpperCase() || this.t("No executable trigger / technical fallback"))} (${esc(this.t(`fallback_${row.reason}`))})</small></p>`).join("")}</details>`:""}<div class="toolbar"><span class="badge">${esc(this.t(this.current.origin || "builtin"))}</span>${this.current.can_reset?this.button(this.t("Reset to original"),"reset-book","","danger"):this.current.can_delete?this.button(this.t("Delete spellbook"),"delete-book","","danger"):""}</div></section>`;}
  listView(){const tags=[...new Set(this.current.book.commands.flatMap(c=>c.tags || []))]; const rows=this.current.book.commands.filter(c=>{
    const cap=this.current.spell_capabilities[c.id];
    const report=this.report?.spells.find(r=>r.id===c.id);
    return (!this.filters.search || JSON.stringify([c.name,c.trigger,c.triggers]).toLowerCase().includes(this.filters.search.toLowerCase())) && (!this.filters.tag || (c.tags||[]).includes(this.filters.tag)) && (!this.filters.action || this.info.actions.some(a=>a.id===this.filters.action && c.operation===a.operation && JSON.stringify(c.arguments||{})===JSON.stringify(a.arguments))) && (!this.filters.cap || cap===this.filters.cap) && (!this.filters.enabled || String(c.enabled!==false)===this.filters.enabled) && (!this.filters.valid || report && String(report.valid)===this.filters.valid);
    });return `<div class="toolbar">${this.button(this.t("Neuer Spell"),"new-spell","","primary")}${this.button(this.t("Buchdetails"),"metadata")}${this.button(this.t("Spellbook prüfen"),"check")}${this.button(this.t("MCP prüfen"),"check-mcp")}${this.button(this.t("Importieren"),"import")}${this.button(this.t("Buch exportieren"),"export")}${this.button(this.t("Auswahl exportieren"),"export-selected")}${this.bulkDeleteButton()}${this.filters.tag?this.button(this.t("Sammlung exportieren"),"export-tag"):""}</div>
    <div class="filters"><input id="filter-search" aria-label="${esc(this.t("Spells suchen"))}" placeholder="${esc(this.t("Spells suchen …"))}" value="${esc(this.filters.search)}" style="max-width:260px"><select id="filter-tag" aria-label="${esc(this.t("Sammlung filtern"))}"><option value="">${esc(this.t("Alle Sammlungen"))}</option>${this.options(tags.map(t=>({id:t,name:t})),this.filters.tag)}</select><select id="filter-action" aria-label="${esc(this.t("Aktion filtern"))}"><option value="">${esc(this.t("Alle Aktionen"))}</option>${this.options(this.actionChoices,this.filters.action)}</select><select id="filter-cap" aria-label="${esc(this.t("Capability filtern"))}"><option value="">${esc(this.t("User und Expert"))}</option>${this.options(["USER","USER + EXPERT","EXPERT ONLY"].map(v=>({id:v,name:v})),this.filters.cap)}</select><select id="filter-enabled" aria-label="${esc(this.t("Aktivierung filtern"))}"><option value="">${esc(this.t("Aktiv und inaktiv"))}</option>${this.options([{id:"true",name:this.t("Aktiv")},{id:"false",name:this.t("Inaktiv")}],this.filters.enabled)}</select><select id="filter-valid" aria-label="${esc(this.t("Prüfzustand filtern"))}"><option value="">${esc(this.t("Alle Prüfzustände"))}</option>${this.options([{id:"true",name:this.t("Schema gültig")},{id:"false",name:this.t("Schema ungültig")}],this.filters.valid)}</select></div><section class="spell-list">${rows.length?rows.map(c=>{const r=this.report?.spells.find(r=>r.id===c.id);const a=this.info.actions.find(a=>a.operation===c.operation && JSON.stringify(a.arguments)===JSON.stringify(c.arguments||{}));return `<article class="row"><input type="checkbox" data-select="${esc(c.id)}" aria-label="${esc(this.localized(c.name,c.id))} ${esc(this.t("auswählen"))}" ${this.selected.has(c.id)?"checked":""}><div><h3>${esc(this.localized(c.name)||this.localized(c.trigger,c.id))}</h3><p class="muted">„${esc(this.localized(c.trigger))}“</p><small>${esc((c.tags||[]).join(" · "))}</small></div><div><span class="badge">${esc(r?.capability || this.current.spell_capabilities[c.id])}</span><p>${esc(this.t(a?.label) || (c.steps?.length?this.t("{n} Schritte",{n:c.steps.length}):c.sources?.length?this.t("Abfrage"):c.operation?this.t("Erweiterte Aktion"):this.t("Textausgabe")))}</p><small>${c.enabled===false?this.t("Deaktiviert"):r?(r.valid?this.t("Schema gültig"):this.t("Fehler vorhanden")):this.t("Noch nicht geprüft")}</small>${r?this.diagnostics(r):""}${this.mcpBadge(c)}${this.collisionBadge(c)}</div><div class="toolbar">${this.button(this.t("Öffnen"),"edit",c.id)}${this.button(c.enabled===false?this.t("Aktivieren"):this.t("Deaktivieren"),"toggle",c.id)}</div></article>`;}).join(""):this.t("Keine passenden Spells.")}</section>`;}
  newImportView(){return `<section><h2>${esc(this.t("Eigenständiges Spellbook importieren"))}</h2><p>${esc(this.t("ID, Metadaten und Spells bleiben erhalten. Existierende Bücher werden nicht ersetzt."))}</p><input id="file" type="file" accept=".yaml,.yml,.txt"><label>YAML / Text<textarea id="import-text" class="code">${esc(this.importText||"")}</textarea></label>${this.button(this.t("Prüfen"),"analyze-new")}${this.importPreview?`<p>${esc(this.bookName(this.importPreview.incoming))} · ${this.importPreview.incoming.commands.length} Spells · ${esc(this.importPreview.report.capability)}</p><p>${esc(this.t("Expert-Ausführung:"))} ${this.importPreview.expert_allowed?this.t("AN (lokale Freigabe vorhanden)"):this.t("AUS")}</p>${this.button(this.t("Als eigenes Buch übernehmen"),"apply-new","","primary")}`:""}</section>`;}
  metadataView(){const b=this.metadataDraft || this.current.book;return `<section><h2>${esc(this.t("Buchdetails"))}</h2><p class="muted">${esc(this.t("Die Buch-ID bleibt unverändert:"))} ${esc(b.id)}</p>${["name","description","author","version","hase_min_version"].map(k=>`<label>${esc(({name:this.t("Name"),description:this.t("Beschreibung"),author:this.t("Autor"),version:this.t("Version"),language:this.t("Sprache"),hase_min_version:this.t("HASE-Mindestversion")})[k])}<input id="meta-${k}" value="${esc(["name","description"].includes(k)?this.editText(b[k]):b[k])}"></label>`).join("")}<label>${esc(this.t("Cover-Stil"))}<select id="meta-theme">${this.options(["sapphire","emerald","ember","amethyst"].map(id=>({id,name:({sapphire:this.t("Saphir"),emerald:this.t("Smaragd"),ember:this.t("Glut"),amethyst:this.t("Amethyst")})[id]})),bookTheme(b))}</select></label><div class="toolbar">${this.button(this.t("Speichern"),"save-metadata","","primary")}${this.button(this.t("Abbrechen"),"close-metadata")}</div></section>`;}
  loadBookTab(bookId){try{const v=JSON.parse(globalThis.localStorage?.getItem("hase-studio-book-tabs")||"{}")[bookId];return BOOK_TABS.includes(v)?v:"book";}catch{return "book";}}
  storeBookTab(bookId,tab){try{const all=JSON.parse(globalThis.localStorage?.getItem("hase-studio-book-tabs")||"{}");all[bookId]=tab;globalThis.localStorage?.setItem("hase-studio-book-tabs",JSON.stringify(all));}catch{}}
  tabStatus(status){if(!status)return {state:"unknown",symbol:"·",count:""};const n=status.state==="error"?status.errors:status.state==="warning"?status.warnings:0;return {state:status.state,symbol:status.state==="error"?"⛔":status.state==="warning"?"⚠":"✓",count:n?String(n):""};}
  bookTabDefs(){return [{id:"book",label:this.t("Buch & Sprache"),status:null},{id:"check",label:this.t("Prüfung"),status:this.report?.status||null},{id:"mcp",label:this.t("MCP"),status:this.mcpReport?.status||null},{id:"matching",label:this.t("Matching-Diagnose"),status:this.collisionReport().status||null}];}
  bookTabsView(){const active=BOOK_TABS.includes(this.bookTab)?this.bookTab:"book",tabs=this.bookTabDefs();return `<section class="book-tabs"><div class="tab-bar" role="tablist" aria-label="${esc(this.t("Buchweite Informationen"))}">${tabs.map(t=>{const s=t.id==="book"?null:this.tabStatus(t.status);return `<button role="tab" id="book-tab-${t.id}" class="book-tab${t.id===active?" is-active":""}${s?` tab-${s.state}`:""}" data-do="book-tab" data-id="${t.id}" aria-selected="${t.id===active}" aria-controls="book-tabpanel" tabindex="${t.id===active?"0":"-1"}"><span>${esc(t.label)}</span>${s?`<span class="tab-status" data-tab-status="${esc(s.state)}" aria-label="${esc(this.tabStatusLabel(s))}">${esc(s.symbol)}${s.count?` ${esc(s.count)}`:""}</span>`:""}</button>`;}).join("")}</div><div class="tab-panel" id="book-tabpanel" role="tabpanel" aria-labelledby="book-tab-${active}">${this.bookTabPanel(active)}</div></section>`;}
  tabStatusLabel(s){return s.state==="error"?this.t("{n} Fehler",{n:s.count||"1"}):s.state==="warning"?this.t("{n} Warnungen",{n:s.count}):s.state==="ok"?this.t("OK"):this.t("Noch nicht geprüft");}
  bookTabPanel(tab){if(tab==="check")return this.checkPanelView();if(tab==="mcp")return this.mcpPanelView();if(tab==="matching")return `${this.collisionSummaryView()}${this.explainPanelView()}`;return this.languageView();}
  checkPanelView(){if(!this.report)return `<section><h2>${esc(this.t("Prüfergebnis"))}</h2><p class="muted">${esc(this.t("Noch nicht geprüft. „Spellbook prüfen“ testet alle Spells ohne Geräteaufruf."))}</p>${this.button(this.t("Spellbook prüfen"),"check")}</section>`;return `<section><h2>${esc(this.t("Prüfergebnis"))}</h2><div class="stats">${[["user",this.report.user_executable ?? "–",this.t("User ausführbar")],["expert",this.report.expert_executable ?? "–",this.t("Expert / Fallback ausführbar")],["schema",this.report.schema_errors ?? this.report.errors,this.t("Schemafehler")],["safety",this.report.safety_errors ?? 0,this.t("Sicherheitskonflikte")],["warnings",this.report.warnings ?? 0,this.t("Hinweise")]].map(([id,n,label])=>`<button type="button" class="stat-tile" data-do="report-detail" data-id="${id}" aria-haspopup="dialog"><strong>${esc(n)}</strong>${esc(label)}</button>`).join("")}</div><small>${esc(this.t("Auf eine Zahl klicken, um die betroffenen Spells anzuzeigen."))}</small><small>${esc(this.t("Momentaufnahme für den gewählten Client. Dynamische Trigger benötigen eine passende Testphrase."))}</small></section>`;}
  mcpPanelView(){if(!this.mcpReport)return `<section><h2>${esc(this.t("MCP-Prüfergebnis"))}</h2><p class="muted">${esc(this.t("Noch kein MCP-Stand vorhanden."))}</p>${this.button(this.t("MCP prüfen"),"check-mcp")}</section>`;const tiles=[["projectable",this.mcpReport.projectable,this.t("als MCP-Werkzeug bereitstellbar")],["not_projectable",this.mcpReport.not_projectable,this.t("nicht bereitstellbar")],["requires_unrestricted",this.mcpReport.requires_unrestricted,this.t("benötigen uneingeschränkte Bridge")]];return `<section><h2>${esc(this.t("MCP-Prüfergebnis"))}</h2><div class="stats">${tiles.map(([id,n,label])=>`<button type="button" class="stat-tile" data-do="mcp-detail" data-id="${id}" aria-haspopup="dialog"><strong>${esc(n)}</strong>${esc(label)}</button>`).join("")}</div><small>${esc(this.t("Auf eine Zahl klicken, um die betroffenen Spells anzuzeigen."))}</small><small>${esc(this.t("Strukturelle Bereitstellbarkeit (bereichs-/Bridge-unabhängig) und tatsächliche Freigabe (abhängig von der Bridge-Berechtigung) sind getrennt ausgewiesen. Der Stand wird im Buch gespeichert und bei jedem Speichern automatisch aktualisiert; „MCP prüfen“ aktualisiert und speichert ihn sofort."))}</small>${this.mcpReport.stale_count?`<p><small class="mcp-stale">${esc(this.t("{n} Spells zeigen einen noch nicht gespeicherten Stand – wird beim nächsten Speichern oder „MCP prüfen“ aktualisiert.").replace("{n}",this.mcpReport.stale_count))}</small></p>`:""}</section>`;}
  mcpDetailView(kind){const rows=(this.mcpReport?.spells||[]).filter(row=>kind==="projectable"?row.projectable:kind==="not_projectable"?!row.projectable:row.requires_unrestricted_bridge);const title={projectable:this.t("als MCP-Werkzeug bereitstellbar"),not_projectable:this.t("nicht bereitstellbar"),requires_unrestricted:this.t("benötigen uneingeschränkte Bridge")}[kind]||"MCP";return `<div class="modal-head"><h2 id="modal-title">${esc(title)} (${rows.length})</h2>${this.button("×","close-result","","modal-close")}</div><div class="modal-body report-detail">${rows.length?rows.map(row=>`<article class="report-detail-item"><h3>${esc(this.spellName(row.id))} <small class="muted">${esc(row.id)}</small></h3><p>${esc(this.mcpReasonText(row.skip_reason||row.grant_reason)||(row.projectable?this.t(mcpExecutionKindLabels[row.execution_kind]||row.execution_kind):""))}</p><div class="toolbar">${this.button(this.t("Öffnen"),"mcp-open",row.id)}</div></article>`).join(""):`<p class="muted">${esc(this.t("Keine Einträge."))}</p>`}</div>`;}
  bulkDeleteButton(){const n=this.selected.size;return `<button class="danger bulk-delete" data-do="delete-selected" ${n?"":"disabled aria-disabled=\"true\""}>${esc(n?this.t("Auswahl löschen ({n})",{n}):this.t("Auswahl löschen"))}</button>`;}
  updateBulkDeleteUi(){const b=this.shadowRoot.querySelector?.('[data-do="delete-selected"]');if(!b)return;const n=this.selected.size;b.disabled=!n;b.setAttribute("aria-disabled",n?"false":"true");b.textContent=n?this.t("Auswahl löschen ({n})",{n}):this.t("Auswahl löschen");}
  async deleteSelected(){const ids=this.current.book.commands.map(c=>c.id).filter(id=>this.selected.has(id));if(!ids.length)return;const n=ids.length;if(!await this.askConfirmation(n===1?this.t("1 ausgewählten Spell wirklich löschen?"):this.t("{n} ausgewählte Spells wirklich löschen?",{n})))return;let saved;try{saved=await this.api("delete_spells",{spell_ids:ids,confirmed:true});}catch(err){try{await this.refresh();}catch{}throw err;}this.selected.clear();await this.refresh();this.report=saved.report||null;this.render();}
  reportDetailView(kind){const r=this.report;if(!r)return "";const name=id=>this.spellName(id);const rows=r.spells||[];let title,items;
    if(kind==="user"||kind==="expert"){const ids=r.executable_ids?.[kind]||[];title=kind==="user"?this.t("User ausführbar"):this.t("Expert / Fallback ausführbar");items=ids.map(id=>({id,lines:[]}));}
    else if(kind==="schema"){title=this.t("Schemafehler");items=rows.filter(x=>(x.errors||[]).length).map(x=>({id:x.id,lines:x.errors}));}
    else if(kind==="safety"){title=this.t("Sicherheitskonflikte");items=(r.validation_issues||[]).filter(i=>i.severity==="error").map(i=>({id:i.spell_id,lines:[i.message]}));}
    else {title=this.t("Hinweise");items=rows.filter(x=>(x.warnings||[]).length).map(x=>({id:x.id,lines:x.warnings}));}
    const total=items.reduce((n,i)=>n+Math.max(1,i.lines.length),0);
    return `<div class="modal-head"><h2 id="modal-title">${esc(title)} (${kind==="user"||kind==="expert"?items.length:total})</h2>${this.button("×","close-result","","modal-close")}</div><div class="modal-body report-detail">${items.length?items.map(i=>`<article class="report-detail-item"><h3>${esc(name(i.id))} <small class="muted">${esc(i.id)}</small></h3>${i.lines.length?`<ul>${i.lines.map(l=>`<li>${esc(this.messageText(l))}</li>`).join("")}</ul>`:""}<div class="toolbar">${this.button(this.t("Öffnen"),"report-open",i.id)}</div></article>`).join(""):`<p class="muted">${esc(this.t("Keine Einträge."))}</p>`}</div>`;}
  stepStatusLabel(status){return ({ok:this.t("OK"),failed:this.t("Fehlgeschlagen"),partial:this.t("Teilweise"),denied:this.t("Verweigert")})[status]||status;}
  stepsResultView(steps,preview){if(!Array.isArray(steps)||!steps.length)return "";const fmt=v=>Array.isArray(v)?(v.length?v.join(", "):"–"):v==null?"–":String(v);return `<section class="steps-result"><h3>${esc(this.t("Schritte"))} (${steps.length})${preview?` <small class="muted">${esc(this.t("ohne Geräteaufruf"))}</small>`:""}</h3>${steps.map(st=>{const t=st.target||{},values=Object.entries(st.values||{});return `<article class="step-result step-${esc(st.status)}"><h4>${esc(String((st.index??0)+1))}. ${esc(st.alias||this.t("(ohne Namen)"))} <span class="badge">${esc(this.stepStatusLabel(st.status))}</span></h4><dl class="kv">${st.kind==="action"?`<dt>${esc(this.t("Aktion"))}</dt><dd><code>${esc(st.operation)}</code></dd><dt>${esc(this.t("Ziel"))}</dt><dd>${esc(t.kind||"")}${t.value!=null?` · ${esc(fmt(t.value))}`:""}</dd>${Object.keys(st.arguments||{}).length?`<dt>${esc(this.t("Parameter"))}</dt><dd><code>${esc(JSON.stringify(st.arguments))}</code></dd>`:""}<dt>${esc(this.t("Geräte"))}</dt><dd>${esc(fmt(st.entity_ids))}</dd>`:`<dt>${esc(this.t("Abfrage"))}</dt><dd>${esc(String(st.source_count??(st.sources||[]).length))} ${esc(this.t((st.source_count??(st.sources||[]).length)===1?"Datenquelle":"Datenquellen"))}</dd>`}<dt>${esc(this.t("Grund"))}</dt><dd><code>${esc(st.reason_code||st.status||"")}</code></dd>${values.length?`<dt>${esc(this.t("Ergebnis"))}</dt><dd>${values.map(([k,v])=>`${esc(st.alias?`${st.alias}.${k}`:k)} = <strong>${esc(fmt(v))}</strong>`).join("<br>")}</dd>`:""}</dl></article>`;}).join("")}</section>`;}
  collisionReport(){return this.report?.collisions || this.current?.collision_report || {findings:[],count:0};}
  collisionLabel(kind){return ({order_dependent:this.t("Reihenfolge entscheidet"),safety_ambiguity:this.t("Sicherheits-Mehrdeutigkeit"),resolved_overlap:this.t("Durch Spezifität aufgelöst"),hard_collision:this.t("Trigger-Kollision")})[kind]||kind;}
  spellCollisions(id){return (this.collisionReport().findings||[]).filter(f=>(f.spell_ids||[]).includes(id));}
  collisionSummaryView(){const c=this.collisionReport(),findings=c.findings||[];if(!findings.length)return `<section><h2>${esc(this.t("Matching-Diagnose"))}</h2><p><strong>${esc(this.t("Keine statisch sicheren Trigger-Kollisionen gefunden."))}</strong></p><small>${esc(this.t("Semantics v1 · konservative Prüfung identischer normalisierter Trigger-Strukturen. Fuzzy-Überschneidungen werden absichtlich nicht geraten."))}</small></section>`;return `<section><h2>${esc(this.t("Matching-Diagnose"))}</h2><div class="stats"><div><strong>${findings.length}</strong>${esc(this.t("statische Kollisionen"))}</div><div><strong>${findings.filter(f=>f.kind==="safety_ambiguity").length}</strong>${esc(this.t("Sicherheits-Mehrdeutigkeiten"))}</div><div><strong>${findings.filter(f=>f.kind==="order_dependent").length}</strong>${esc(this.t("reihenfolgeabhängig"))}</div></div><small>${esc(this.t("Nur diagnostisch: Speichern, Laden und Runtime-Matching werden dadurch nicht verändert. Die v1-Analyse meldet nur statisch sichere Überschneidungen identischer Trigger-Strukturen."))}</small><details><summary>${esc(this.t("Kollisionen anzeigen"))}</summary>${findings.map(f=>`<p><span class="badge">${esc(this.collisionLabel(f.kind))}</span> <strong>${esc((f.spell_ids||[]).join(" ↔ "))}</strong><br><small>„${esc((f.triggers||[])[0]||"")}“ ↔ „${esc((f.triggers||[])[1]||"")}“ · ${esc(this.t("Spezifität"))}: ${(f.specificity||[]).map(v=>esc(v)).join(" / ")}</small></p>`).join("")}</details></section>`;}
  collisionStatus(id){return this.collisionReport().spells?.[id]||null;}
  collisionBadge(c){const s=this.collisionStatus(c.id);if(!s)return "";const warn=s.warnings===1?this.t("1 Warnung"):this.t("{n} Warnungen",{n:s.warnings});const text=s.status==="error"?`⛔ ${this.t("Sicherheitskonflikt")}${s.warnings?` · ${warn}`:""}`:s.status==="warning"?`⚠ ${warn}`:s.status==="ok"?`✓ ${this.t("OK")}`:this.t("Nicht geprüft (deaktiviert)");return `<div class="conflict-status"><span class="badge conflict-badge conflict-${esc(s.status)}" data-conflict-status="${esc(s.status)}">${esc(text)}</span></div>`;}
  collisionReasonText(code){return ({validated_placeholders_select_specific_trigger:this.t("Der Trigger mit zusätzlichen geprüften Werten (Zahl/Vokabular) gewinnt, wenn diese gültig sind; sonst gilt der allgemeinere."),specificity_v1_selects_higher_tier:this.t("Der spezifischere Trigger (mehr feste Wörter) gewinnt immer."),same_tier_structurally_opposite:this.t("Gleiche Trigger-Struktur, gleiches Ziel, gegensätzliche Aktion: Bei dieser Eingabe führt HASE nichts aus und fragt nach."),same_tier_same_trigger_shape_stable_order_decides_equal_scores:this.t("Gleiche Trigger-Struktur ohne bewiesenen Gegensatz: Bei gleichem Score entscheidet die Reihenfolge im Buch."),same_tier_overlapping_trigger_language_without_static_resolution:this.t("Überschneidende Trigger ohne statische Auflösung.")})[code]||code;}
  outcomeLabel(value){return value==="on"?this.t("EIN"):value==="off"?this.t("AUS"):"–";}
  variantText(v){if(!v)return "–";const t=v.target||{};const target=t.capture?`{${t.capture}}`:Array.isArray(t.value)?t.value.join(", "):(t.value??"");return `${v.role==="expert"?"Expert":"User"} · ${v.operation} → ${this.outcomeLabel(v.outcome)} · ${t.kind||""}${target?` ${target}`:""}`;}
  spellName(id){const c=this.current?.book?.commands?.find(c=>c.id===id);return c?(this.localized(c.name)||this.localized(c.trigger,id)):id;}
  conflictDetailsView(id){const findings=this.spellCollisions(id);if(!findings.length)return `<p class="conflict-none"><span class="badge conflict-badge conflict-ok">✓ ${esc(this.t("OK"))}</span> ${esc(this.t("Keine statisch erkannten Konflikte."))}</p>`;return `<details open class="conflict-details"><summary>${esc(this.t("Konflikte"))} (${findings.length})</summary>${findings.map(f=>{const ids=f.spell_ids||[],self=ids.indexOf(id),other=self===0?1:0,ev=f.evidence||null;return `<div class="conflict-item conflict-${esc(f.severity||"info")}"><p><span class="badge conflict-badge conflict-${esc(f.severity||"info")}">${esc(f.severity==="error"?"⛔ ":f.severity==="warning"?"⚠ ":"")}${esc(this.collisionLabel(f.kind))}</span> <strong>${esc(this.spellName(ids[other]))}</strong> <small>(${esc(ids[other]||"")})</small></p><dl class="kv"><dt>${esc(this.t("Trigger"))}</dt><dd>„${esc((f.triggers||[])[self]||"")}“ ↔ „${esc((f.triggers||[])[other]||"")}“</dd><dt>${esc(this.t("Konflikt-Typ"))}</dt><dd><code>${esc(f.kind)}</code></dd>${ev?`<dt>${esc(this.t("Gemeinsames Ziel"))}</dt><dd>${esc(this.variantText(ev[self]).split(" · ").slice(2).join(" · "))}</dd><dt>${esc(this.t("Aktion dieses Spells"))}</dt><dd>${esc(this.variantText(ev[self]))}</dd><dt>${esc(this.t("Aktion des anderen Spells"))}</dt><dd>${esc(this.variantText(ev[other]))}</dd>`:""}<dt>${esc(this.t("Grund"))}</dt><dd>${esc(this.collisionReasonText(f.reason))}</dd></dl></div>`;}).join("")}</details>`;}
  explainDecisionLabel(d){return ({MATCH:this.t("Treffer"),AMBIGUOUS:this.t("Mehrdeutig – keine Aktion"),NO_MATCH:this.t("Kein Treffer")})[d]||d;}
  explainReasonText(code){return ({single_candidate:this.t("Nur ein passender Kandidat."),clear_winner:this.t("Höchster Score mit deutlichem Abstand."),within_epsilon_not_opposite:this.t("Knapper Score-Abstand, aber keine gegensätzlichen Aktionen – der höhere Score gewinnt."),stable_order:this.t("Gleicher Score ohne Gegensatz – die Reihenfolge im Buch entscheidet."),safety_ambiguity:this.t("Sicherheits-Mehrdeutigkeit: gleiches Ziel, gegensätzliche Aktionen. HASE führt nichts aus und fragt nach."),no_candidate:this.t("Kein aktiver Spell steht zur Auswahl."),no_full_match:this.t("Kein Trigger deckt die gesamte Eingabe ab."),no_resolvable_candidate:this.t("Ein Trigger passt, aber die Aktion ließ sich mit den erkannten Werten nicht bilden."),lower_score:this.t("Niedrigerer Score."),not_in_top_two:this.t("Nicht unter den zwei besten Kandidaten – nimmt an der Mehrdeutigkeitsprüfung nicht teil."),superseded_by_real_capture:this.t("Ein Trigger mit echtem erkanntem Wert hat Vorrang vor dem Quellbereich-Fallback."),fallback_deferred:this.t("Nur Quellbereich-Fallback – zurückgestellt."),no_trigger_match:this.t("Trigger passt nicht zur Eingabe."),input_not_fully_consumed:this.t("Die Eingabe enthält Wörter, die der Trigger nicht abdeckt."),required_capture_missing:this.t("Ein Platzhalter hat keinen Wert erhalten."),trigger_unavailable:this.t("Trigger ist nicht auswertbar."),candidate_unresolvable:this.t("Trigger passt, aber der Spell ist mit diesen Werten nicht ausführbar."),lower_specificity_tier:this.t("Nicht ausgewertet: ein spezifischerer Trigger hat bereits gepasst."),spell_disabled:this.t("Spell ist deaktiviert."),typed_only_excluded:this.t("Reine Textausgabe ist in diesem Kontext ausgeschlossen.")})[code]||code;}
  engineReasonText(code){return ({target_unresolved:this.t("Ziel konnte nicht gebildet werden."),argument_invalid:this.t("Ein Argument ist ungültig (Wert nicht erkannt oder außerhalb des erlaubten Bereichs)."),no_operation:this.t("Variante ohne Aktion."),draft_build_failed:this.t("Aktion konnte nicht gebildet werden."),query_plan_build_failed:this.t("Abfrage konnte nicht gebildet werden."),step_plan_build_failed:this.t("Ablauf konnte nicht gebildet werden."),response_format_error:this.t("Antworttext passt nicht zu den Platzhaltern.")})[code]||code;}
  oppositeBasisText(code){return ({action_outcomes:this.t("Gleiches Ziel und gegensätzliche Aktionen (bewiesen)."),v1_boolean_flip:this.t("Gleiche Aktion und gleiches Ziel mit umgekehrtem Schalter (Semantics v1)."),output_text:this.t("Gegensätzliche Textausgaben (Semantics-v1-Wortliste).")})[code]||code||"–";}
  dispositionLabel(d){return ({selected:this.t("Gewählt"),ambiguous:this.t("Mehrdeutig"),outranked:this.t("Unterlegen"),rejected:this.t("Abgelehnt"),not_evaluated:this.t("Nicht ausgewertet"),excluded:this.t("Ausgeschlossen")})[d]||d;}
  explainCandidateView(c){const captures=Object.entries(c.captures||{});return `<article class="explain-candidate disp-${esc(c.disposition)}"><h4>${esc(this.spellName(c.spell_id))} <span class="badge disp-badge disp-${esc(c.disposition)}">${esc(this.dispositionLabel(c.disposition))}</span></h4><dl class="kv"><dt>${esc(this.t("Spell"))}</dt><dd><code>${esc(c.spell_id)}</code></dd><dt>${esc(this.t("Trigger"))}</dt><dd>„${esc(c.trigger)}“${c.is_alias?` <small>(${esc(this.t("Alias"))})</small>`:""}</dd><dt>${esc(this.t("Spezifität"))}</dt><dd>${esc(c.specificity)}</dd><dt>${esc(this.t("Score"))}</dt><dd>${c.score==null?"–":esc(Number(c.score).toFixed(3))}</dd><dt>${esc(this.t("Exakt"))}</dt><dd>${c.is_exact==null?"–":esc(c.is_exact?this.t("ja"):this.t("nein"))}</dd>${captures.length?`<dt>${esc(this.t("Captures"))}</dt><dd>${captures.map(([k,v])=>`${esc(k)} = <strong>${esc(v)}</strong>`).join("<br>")}</dd>`:""}${(c.actions||[]).length?`<dt>${esc(this.t("Aktionen"))}</dt><dd>${c.actions.map(a=>esc(this.variantText(a))).join("<br>")}</dd>`:""}<dt>${esc(this.t("Grund"))}</dt><dd><code>${esc(String(c.reason).toUpperCase())}</code> ${esc(this.explainReasonText(c.reason))}</dd>${c.engine?`<dt>${esc(this.t("Engine-Ursache"))}</dt><dd><code>${esc(String(c.engine.reason).toUpperCase())}</code> ${esc(this.engineReasonText(c.engine.reason))} <small>(${esc(c.engine.stage)})</small></dd>`:""}</dl>${this.expert&&c.debug?`<details class="explain-debug"><summary>${esc(this.t("Debug-Details"))}</summary><pre>${esc(JSON.stringify(c.debug,null,2))}</pre></details>`:""}</article>`;}
  explainView(e){if(!e)return "";const main=(e.candidates||[]).filter(c=>["selected","ambiguous","outranked"].includes(c.disposition)||(c.disposition==="rejected"&&c.reason!=="no_trigger_match")),rest=(e.candidates||[]).filter(c=>!main.includes(c));const amb=e.ambiguity;return `<section class="match-explain" data-decision="${esc(e.decision)}"><p class="explain-decision"><span class="badge decision-badge decision-${esc(e.decision)}">${esc(e.decision)}</span> <strong>${esc(this.explainDecisionLabel(e.decision))}</strong></p><p><strong>${esc(this.t("Grund"))}:</strong> <code>${esc(String(e.reason).toUpperCase())}</code> ${esc(this.explainReasonText(e.reason))}</p>${e.via_source_area_fallback?`<p class="muted">${esc(this.t("Ausgewählt über den Quellbereich-Fallback (kein Bereich genannt)."))}</p>`:""}${amb?`<div class="notice"><p><strong>${esc(this.t("Sicherheitsprüfung"))}:</strong> ${esc(this.oppositeBasisText(amb.basis))}</p>${(amb.variants||[]).length===2?`<p>${(amb.spell_ids||[]).map((id,i)=>`${esc(this.spellName(id))}: ${esc(this.variantText(amb.variants[i]))}`).join("<br>")}</p>`:""}</div>`:""}<h3>${esc(this.t("Kandidaten"))}</h3>${main.length?main.map(c=>this.explainCandidateView(c)).join(""):`<p class="muted">${esc(this.t("Kein Trigger kam in die engere Auswahl."))}</p>`}${rest.length?`<details><summary>${esc(this.t("Weitere Kandidaten"))} (${rest.length})</summary>${rest.map(c=>this.explainCandidateView(c)).join("")}</details>`:""}${this.expert?`<details class="explain-debug"><summary>${esc(this.t("Debug-Details"))}</summary><pre>${esc(JSON.stringify({contract:e.contract,semantics:e.semantics,language:e.language,text:e.text,debug:e.debug},null,2))}</pre></details>`:""}</section>`;}
  explainPanelView(){return `<section class="explain-panel"><h2>${esc(this.t("Matching erklären"))}</h2><p class="muted">${esc(this.t("Zeigt, wie HASE eine Eingabe im ganzen Buch zuordnen würde – ohne Geräteaufruf."))}</p><label>${esc(this.t("Eingabe"))}<input id="explain-phrase" value="${esc(this.explainPhrase||"")}" placeholder="${esc(this.t("z. B. lumos Wohnzimmer"))}"></label>${this.button(this.t("Erklären"),"explain-book","","primary")}</section>`;}
  matchingTraceView(trace){if(!trace)return "";const decision=trace.decision==="match"?this.t("Match"):trace.decision==="ambiguous"?this.t("Mehrdeutig"):this.t("Kein Match");return `<details open class="matching-trace"><summary>${esc(this.t("Matching Semantics v1"))} · ${esc(decision)}</summary><p><small>${esc(this.t("Entscheidung"))}: ${esc(trace.reason||"–")}${trace.selected_spell_id?` · ${esc(this.t("gewählt"))}: ${esc(trace.selected_spell_id)}`:""}${trace.ambiguity_spell_ids?.length?` · ${esc(this.t("Kandidaten"))}: ${esc(trace.ambiguity_spell_ids.join(" ↔ "))}`:""}</small></p><div class="table-wrap"><table><thead><tr><th>${esc(this.t("Spell"))}</th><th>${esc(this.t("Trigger"))}</th><th>${esc(this.t("Spezifität"))}</th><th>${esc(this.t("Score"))}</th><th>${esc(this.t("Captures"))}</th><th>${esc(this.t("Status"))}</th></tr></thead><tbody>${(trace.candidates||[]).map(c=>`<tr><td>${esc(c.spell_id)}</td><td>${esc(c.trigger_text)}</td><td>${esc(c.specificity)}</td><td>${c.score==null?"–":esc(Number(c.score).toFixed(3))}</td><td>${esc(c.captures?JSON.stringify(c.captures):"–")}</td><td>${esc(c.evaluated===false?this.t("nicht ausgewertet · niedrigere Stufe"):c.eligible?this.t("eligible"):c.reason||"–")}</td></tr>`).join("")}</tbody></table></div></details>`;}
  diagnostics(r){return [...r.errors,...r.warnings].map(t=>`<p><small>${esc(this.messageText(t))}</small></p>`).join("") + ["user","expert"].map(v=>r[v]?`<p><small>${v==="user"?"User":"Expert"}: ${esc(this.messageText(reasons[r[v].reason_code]||r[v].reason_code))}</small></p>`:"").join("");}
  mcpReasonText(code){if(!code)return "";if(reasons[code])return this.messageText(reasons[code]);if(code.startsWith("unknown_operation:"))return this.messageText(`${reasons.unknown_operation} (${code.slice("unknown_operation:".length)})`);if(code.startsWith("conflicting_capture_definition:"))return this.messageText(`Platzhalter mit widersprüchlicher Rolle: ${code.slice("conflicting_capture_definition:".length)}`);return this.messageText(code);}
  mcpBadge(c){const m=this.mcpReport?.spells?.find(m=>m.id===c.id);if(!m)return `<small>${esc(this.t("MCP: noch nicht geprüft"))}</small>`;const stale=m.stale?`<br><span class="mcp-stale">${esc(this.t("(noch nicht gespeichert)"))}</span>`:"";if(!m.projectable)return `<small class="mcp-not-projectable">${esc(this.t("MCP: nicht bereitstellbar"))} – ${esc(this.mcpReasonText(m.skip_reason))}${stale}</small>`;const kind=esc(this.t(mcpExecutionKindLabels[m.execution_kind]||m.execution_kind));const grant=m.requires_unrestricted_bridge?`<br>${esc(this.t("Freigabe: benötigt uneingeschränkte Bridge"))} – ${esc(this.mcpReasonText(m.grant_reason))}`:`<br>${esc(this.t("Freigabe: keine zusätzliche Bridge-Voraussetzung"))}`;return `<small>${esc(this.t("MCP:"))} ${kind}${m.read_only?` · ${esc(this.t("nur lesend"))}`:""}${grant}${stale}</small>`;}
  spellLanguageChoices(){return [...new Set([...(this.current?.languages||[]),this.effectiveBookLanguage(),this.validLanguage(this.language)||"en"])];}
  languageOptions(value,automatic=true){return `${automatic?`<option value="" ${value?"":"selected"}>${esc(this.t("Automatic — effective Spellbook language"))}</option>`:""}${this.spellLanguageChoices().map(code=>`<option value="${esc(code)}" ${code===value?"selected":""}>${esc(`${this.languageName(code)} (${code.toUpperCase()})`)}</option>`).join("")}`;}
  newSpellChoiceView(){
    return `<section class="new-spell-choice"><div class="toolbar">${this.button(this.t("← Back to Spellbook"),"cancel-new-spell")}</div><h2>${esc(this.t("How would you like to create your spell?"))}</h2><p class="muted">${esc(this.t("Both options use the same spell format and the same step editor. You can switch between Wizard and Expert at any time."))}</p><label>${esc(this.t("Spell language"))}<small>${esc(this.t("Automatic uses the effective Spellbook language. You can explicitly choose another available language."))}</small><select id="new-spell-language">${this.languageOptions(null)}</select></label><div class="creation-choices"><button data-do="new-spell-wizard" class="creation-choice primary"><strong>${esc(this.t("Wizard"))}</strong><span>${esc(this.t("Guided creation with explanations and validation. Starts with one step - add more actions and queries as needed."))}</span></button><button data-do="new-spell-expert" class="creation-choice"><strong>${esc(this.t("Expert"))}</strong><span>${esc(this.t("Create and edit the spell directly, including Jinja/templates and free arguments."))}</span></button></div></section>`;
  }
  targetChoices(action){const isTodo=action?.domain_filter==="todo";return [{id:"none",name:this.t("No target")},{id:"area_name",name:this.t("Bereich aus dem gesprochenen Satz")},{id:"area_id",name:this.t("Einen Bereich auswählen")},{id:"entity_ids",name:isTodo?this.t("Todo-Listen auswählen"):this.t("Bestimmte Geräte auswählen")},{id:"entity_id",name:isTodo?this.t("Eine Todo-Liste auswählen"):this.t("Ein Gerät auswählen")},{id:"all_allowed",name:this.t("Alle erlaubten Geräte dieses Typs")},{id:"source_area",name:this.t("Bereich des sprechenden Geräts")}].filter(v=>action.targets.includes(v.id));}
  wizardHelpButton(id){return `<button type="button" class="help-button" data-do="wizard-help" data-id="${esc(id)}" aria-expanded="${this.wizardHelp.has(id)}" aria-label="${esc(this.t("Show help"))}">?</button>`;}
  queryResponseFieldNames(action){const fields=action?.response_fields||{};if(!Object.keys(fields).length)return [];if(action.operation==="entity.get_state"){const selected=[...this.shadowRoot.querySelectorAll('input[name="entity"]:checked')].map(e=>e.value)[0]||this.edit?.target?.value;const entityId=Array.isArray(selected)?selected[0]:selected,domain=typeof entityId==="string"&&entityId.includes(".")?entityId.split(".",1)[0]:null;return [...new Set([...(fields["*"]||[]),...(domain?fields[domain]||[]:Object.values(fields).flat())])];}return [...new Set(Object.values(fields).flat())];}
  queryResponseLabel(name){return this.t(({state:"State",unit:"Unit",entity_name:"Device name",entity_id:"Entity ID",area:"Area",brightness:"Brightness (0–255)",brightness_percent:"Brightness (%)",color_temp_kelvin:"Color temperature (Kelvin)",rgb_color:"RGB color",effect:"Light effect",current_position:"Position (%)",current_temperature:"Current temperature",temperature:"Target temperature",hvac_action:"Current climate action",percentage:"Percentage",preset_mode:"Preset mode",volume_level:"Volume (0–1)",volume_percent:"Volume (%)",media_title:"Media title",media_artist:"Media artist"})[name]||name);}
  queryResponseChips(field,action){const names=this.queryResponseFieldNames(action);return names.length?`<div class="placeholder-chips query-placeholder-chips"><small>${esc(this.t("Return values:"))}</small>${names.map(name=>`<button type="button" data-do="insert-placeholder" data-id="${esc(`${field}:${name}`)}" title="${esc(this.queryResponseLabel(name))}">{${esc(name)}} · ${esc(this.queryResponseLabel(name))}</button>`).join("")}</div>`:"";}
  wizardStateResponseHelp(action){const names=this.queryResponseFieldNames(action);if(!names.length)return "";return `<div class="capture-explanation query-response-help" role="note"><strong>${esc(this.t("Configurable state response"))}</strong><p>${esc(this.t("Combine normal text with the return values offered below. Example: In {area} it is currently {state} {unit}."))}</p><p>${esc(this.t("Available for the selected device:"))} ${names.map(name=>`{${esc(name)}}`).join(", ")}</p></div>`;}
  triggerPlaceholderNames(){const text=this.value("triggers") || [this.editText(this.edit?.trigger),...(this.editText(this.edit?.triggers)||[])].filter(Boolean).join("\n");return [...new Set([...text.matchAll(/\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g)].map(match=>match[1]))];}
  wizardBackendIssues(){const spell=this.wizardValidation?.spells?.[0];if(!spell)return [];return [...(spell.errors||[]).map(message=>({level:"error",message:this.messageText(message)})),...(spell.warnings||[]).map(message=>({level:"warning",message:this.messageText(message)}))].map(issue=>({...issue,field:/trigger|phrase|capture|Formulierung/i.test(issue.message)?"trigger":/target|ziel|bereich|gerät/i.test(issue.message)?"target":/action|aktion|parameter/i.test(issue.message)?"action":"review"}));}
  // --- The Wizard/Expert step editor (item 2/4/17) - ONE step-list builder
  // for 0-12 steps, shared by both editor modes and reusing the same
  // backend data (actionChoices/targetChoices/entity-capability metadata).
  // "Single" and "Combined" are no longer different editor/spell kinds -
  // only the resulting step count is (see wizard_incompatibilities()'s
  // module docstring in core/studio.py for the persisted-shape rule).
  newStepDraft(kind){return kind==="action"?{alias:"",kind:"action",expert:false,action:this.info.actions?.[0]?.id||"",target_kind:"",target_value:null,operation:this.info.actions?.[0]?.operation||"",target:{kind:"all_allowed"},arguments:{}}:{alias:"",kind:"query",sources:[this.newSourceDraft()]};}
  // Wizard and Expert share ONE step editor (item 4): every step/source
  // carries BOTH its guided fields (action id / target_kind / target_value)
  // and its raw/expert fields (operation / target / arguments), and
  // `expert` just picks which set of widgets - and which shape
  // collectStepDraft()/collectSteps() sends to wizard_spell() - is active
  // for THAT one step. Never a separate, read-only "preserved" step
  // anymore: every step is always fully editable in at least one of the
  // two modes (see wizard_spell()'s `_resolve_action_target`/`_resolve_
  // source`, which accept the full canonical shape when `expert: true`).
  normalizeStepFromAction(step,allowAreaName,forcedExpert){
    const guidedKinds=["none","area_id","entity_id","entity_ids","all_allowed","source_area"],target=step.target||{};
    const plainGuided=guidedKinds.includes(target.kind)&&!("capture" in target);
    const areaNameGuided=allowAreaName&&target.kind==="area_name"&&(typeof target.capture==="string"||typeof target.value==="string");
    const action=this.info.actions.find(a=>a.operation===step.operation&&sameData(a.arguments||{},step.arguments||{}))
      ||(forcedExpert===false&&step.operation==="light.set_color"?this.info.actions.find(a=>a.operation===step.operation):null);
    const guided=!forcedExpert&&!!action&&(plainGuided||areaNameGuided);
    return {alias:Object.hasOwn(step,"alias")?step.alias:"",kind:"action",expert:!guided,
      action:guided?action.id:(this.info.actions[0]?.id||""),
      target_kind:guided?target.kind:"all_allowed",
      target_value:guided?(target.value??null):null,
      ...(target.capture?{target_capture:target.capture}:{}),
      ...(Object.keys(extras(target,["kind","value","capture"])).length?{target_extras:extras(target,["kind","value","capture"])}:{}),
      operation:step.operation||this.info.actions[0]?.operation||"",
      target:target.kind?clone(target):{kind:"all_allowed"},
      arguments:clone(step.arguments||{})};
  }
  normalizeStepFromQuery(step){return {alias:Object.hasOwn(step,"alias")?step.alias:"",kind:"query",sources:(step.sources||[]).map(s=>this.normalizeSource(s))};}
  normalizeSource(s){
    const guidedKinds=["area_id","entity_id","all_allowed","source_area"],target=s.target||{};
    const guided=guidedKinds.includes(target.kind)&&!("capture" in target);
    return {kind:s.kind,expert:!guided,
      ...(Object.keys(extras(s,["kind","target","domain_filter","device_class","attribute","state_filter","variable","unit_variable"])).length?{source_extras:extras(s,["kind","target","domain_filter","device_class","attribute","state_filter","variable","unit_variable"])}:{}),
      ...(Object.keys(extras(target,["kind","value","capture"])).length?{target_extras:extras(target,["kind","value","capture"])}:{}),
      target_kind:target.kind||"all_allowed",target_value:target.value??null,
      capture:target.capture||"",
      domain_filter:s.domain_filter||"",device_class:s.device_class||"",attribute:s.attribute||"",
      state_filter:s.state_filter||"",variable:s.variable||"",unit_variable:s.unit_variable||""};
  }
  normalizeSteps(raw,flags){
    const logical=Array.isArray(raw.steps)?raw.steps.filter(s=>s&&typeof s==="object"):raw.operation
      ?[{alias:raw.alias,operation:raw.operation,target:raw.target,arguments:raw.arguments}]
      :Array.isArray(raw.sources)&&raw.sources.length?[{alias:raw.alias,sources:raw.sources}]:[];
    const allowAreaName=logical.length===1&&!Array.isArray(raw.steps);
    return logical.map((step,index)=>step.sources
      ?this.normalizeStepFromQuery(step)
      :this.normalizeStepFromAction(step,allowAreaName,flags?.[index]));
  }
  newSourceDraft(){return {kind:"count",target_kind:"all_allowed",target_value:null,domain_filter:"",device_class:"",attribute:"",state_filter:"",variable:"",unit_variable:""};}
  stepTargetChoices(action){return this.targetChoices(Object.assign({},action,{targets:(action.targets||[]).filter(k=>["none","area_id","entity_id","entity_ids","all_allowed","source_area"].includes(k))}));}
  sourceTargetChoices(sourceKind){const caps=this.info.query_source_capabilities||{kinds:[],aggregate_kinds:[],single_target_kinds:[],aggregate_target_kinds:[]};const raw=caps.aggregate_kinds.includes(sourceKind)?caps.aggregate_target_kinds:caps.single_target_kinds;return [...this.targetChoices({targets:raw}),...(raw.includes("entity_name")?[{id:"entity_name",name:this.t("Gerät aus dem gesprochenen Satz")}]:[])];}
  optionsWithCurrent(rows,value){return value&&!rows.some(r=>r.id===value)?[...rows,{id:value,name:value}]:rows;}
  stepTargetFieldView(prefix,kind,value,action=null){
    if(kind==="area_id")return `<label>${esc(this.t("Bereich"))}<select id="${prefix}-area"><option value="">${esc(this.t("Bereich auswählen"))}</option>${this.options(this.optionsWithCurrent(this.registry.areas,value),value)}</select></label>`;
    const isTodo=action?.domain_filter==="todo";
    const label=this.entityLabel(action);const labelPlural=this.entityLabel(action,true);
    if(kind==="entity_id")return `<label>${esc(label)}<select id="${prefix}-entity"><option value="">${esc(isTodo?this.t("Todo-Liste auswählen"):this.t("Gerät auswählen"))}</option>${this.options(this.entitiesForAction(action,[value]),this.entitiesForAction(action,[value]).some(e=>e.id===value)?value:"")}</select></label>`;
    if(kind==="entity_ids"){const ids=(Array.isArray(value)?value:[]).filter(id=>this.entitiesForAction(action,[id]).some(e=>e.id===id)),entities=this.entitiesForAction(action,ids);return `<div><label>${esc(labelPlural)}</label><div class="checklist">${entities.map(e=>`<label><input type="checkbox" name="entity" value="${esc(e.id)}" ${ids.includes(e.id)?"checked":""}> ${esc(e.name)}${e.area_name?` <small>· ${esc(e.area_name)}</small>`:""}${this.entityExposureWarning(e)}</label>`).join("")}</div></div>`;}
    return "";
  }
  stepActionLabel(step){const action=this.actionChoices.find(a=>a.id===step.action);if(action?.operation==="light.set_color"){const args=step.color_arguments||step.arguments||action.arguments;return this.t("Farbe")+": "+(args.color_name||this.rgbToHex(args.rgb_color));}return action?this.t(action.label):this.t("(keine Aktion gewählt)");}
  stepTargetSummaryFor(kind,value){
    // A set value that is not in the (lazily loaded) registry is shown by its id, never as "not selected".
    if(kind==="area_id")return this.registry.areas.find(a=>a.id===value)?.name||value||this.t("Kein Bereich gewählt");
    if(kind==="entity_id")return this.registry.entities.find(e=>e.id===value)?.name||value||this.t("Kein Gerät gewählt");
    if(kind==="entity_ids"){const ids=Array.isArray(value)?value:[];return ids.map(id=>this.registry.entities.find(e=>e.id===id)?.name||id).join(", ")||this.t("Kein Gerät gewählt");}
    return {all_allowed:this.t("Alle erlaubten Geräte dieses Typs"),source_area:this.t("Bereich des sprechenden Geräts")}[kind]||this.t("Kein Ziel gewählt");
  }
  sourceKindLabel(kind){return this.t({state:"Zustand",attribute:"Attribut",count:"Anzahl",min:"Minimum",max:"Maximum",avg:"Durchschnitt",sum:"Summe",names:"Gerätenamen",states:"Zustände"}[kind]||kind);}
  stepFieldLabel(field){return this.t({success:"Erfolgreich",state:"Zustand",entity_name:"Gerätename",entity_id:"Entity-ID",area:"Bereich",target_count:"Anzahl Ziele",success_count:"Erfolgreich",failed_count:"Fehlgeschlagen",failed_names:"Fehlgeschlagene Geräte",count:"Anzahl",names:"Gerätenamen",states:"Zustände",unit:"Einheit"}[field]||field);}
  stepSummary(step){
    if(step.kind==="query")return `${this.t("Abfrage")} · ${step.sources.map(source=>this.sourceKindLabel(source.kind)).join(" + ")||this.t("(keine Datenquelle)")}`;
    if(step.expert)return `${step.operation||this.t("(keine Operation)")} · ${step.target?.kind||"–"}`;
    return `${this.stepActionLabel(step)} · ${this.stepTargetSummaryFor(step.target_kind,step.target_value)}`;
  }
  stepIsExpert(step){return step.kind==="action"&&!!step.expert;}
  stepExpertBadge(step){
    const index=this.edit?.steps?.indexOf(step),blockers=(this.stepBlockers||[]).filter(b=>b.step_index===index);
    if(!blockers.length&&!this.stepIsExpert(step))return `<span class="badge">${esc(this.t("Guided"))}</span>`;
    const label=step.target?.kind==="template"?"Expert · Jinja":"Expert-only";
    return `<span class="badge editor-mode-badge mode-expert" title="${esc(blockers.map(b=>this.blockerMessage(b)).join(" ")||this.t("Freie Felder sind geöffnet. Die Konvertierung prüft den tatsächlichen Inhalt."))}">⚙ ${esc(this.t(label))}</span>`;
  }
  stepListView(){const steps=this.edit.steps||[],atLimit=steps.length>=12;return `<div class="steps-list">${steps.map((step,i)=>this.stepCardView(step,i)).join("")||`<p class="muted">${esc(this.t("Noch keine Aktionen oder Abfragen."))}</p>`}</div>${atLimit?`<p class="muted">${esc(this.t("Maximal 12 Schritte pro Spell."))}</p>`:`<div class="toolbar">${this.button(this.t("+ Aktion"),"add-step","action","primary")}${this.button(this.t("+ Abfrage"),"add-step","query")}</div>`}`;}
  stepCardView(step,index){
    if(index===this.editingStepIndex)return this.stepEditorView(this.editingStepDraft||step,index);
    const alias=typeof step.alias==="string"?step.alias:step.alias==null?"":JSON.stringify(step.alias);
    return `<article class="step-card"><div class="step-card-head"><strong>${esc(this.t("Schritt {n}",{n:index+1}))}</strong>${this.stepExpertBadge(step)}</div>${alias?`<div class="step-card-name">${esc(alias)}</div>`:""}<p class="muted">${esc(this.stepSummary(step))}</p><div class="toolbar">${this.button(this.t("Bearbeiten"),"edit-step",index)}${index>0?this.button("↑","move-step-up",index):""}${index<this.edit.steps.length-1?this.button("↓","move-step-down",index):""}${this.button(this.t("Entfernen"),"remove-step",index,"danger")}</div></article>`;
  }
  stepEditorView(step,index){
    const isAction=step.kind==="action";
    const action=isAction?this.actionChoices.find(a=>a.id===step.action):null;
    const targetKind=step.target_kind||(action?this.stepTargetChoices(action)[0]?.id:"")||"";
    return `<article id="step-card-${index}" class="step-card step-card-open"><h4>${esc(this.t("Schritt {n}",{n:index+1}))} ${this.stepExpertBadge(step)}</h4>${this.stepEditorError?`<p class="field-message error" role="alert">${esc(this.stepEditorError.message)}</p>`:""}<label><span>${esc(this.t("Name (optional)"))}</span><small>${esc(this.t("Namensraum für Antwort-Variablen, z. B. {name.feld}."))}</small><input id="step-alias" value="${esc(step.alias==null?"":String(step.alias))}" placeholder="shutters"></label>
    ${isAction?this.stepActionSubForm(step,action,targetKind):this.stepQuerySubForm(step)}
    <div class="toolbar">${this.button(this.t("Übernehmen"),"close-step","","primary")}${this.button(this.t("Entfernen"),"remove-step",index,"danger")}</div></article>`;
  }
  expertModeToggle(id,checked,label){return `<label class="expert-toggle"><input id="${id}" type="checkbox" ${checked?"checked":""}> ${esc(label)}</label>`;}
  expertTargetValueField(prefix,kind,value,capture){
    if(kind==="template")return `<label>${esc(this.t("Jinja-Ausdruck"))}<small>${esc(this.t("Wird zur Laufzeit als Home-Assistant-Template ausgewertet."))}</small><textarea class="code" id="${prefix}-value" placeholder="{{ ['light.ceiling'] }}">${esc(value||"")}</textarea></label>`;
    if(["area_name","entity_name"].includes(kind)&&value&&!capture)return `<label>${esc(this.t("Fester Zielname"))}<input id="${prefix}-value" value="${esc(value)}"></label>`;
    if(["area_name","entity_name"].includes(kind))return `<label>${esc(this.t("Platzhalter-Name"))}<small>${esc(this.t("Muss als {name} in jeder Trigger-Formulierung vorkommen."))}</small><input id="${prefix}-capture" value="${esc(capture||"")}" placeholder="area"></label>`;
    if(kind==="entity_ids")return `<label>${esc(this.t("Entity-IDs (eine pro Zeile)"))}<textarea id="${prefix}-value">${esc(Array.isArray(value)?value.join("\n"):value||"")}</textarea></label>`;
    if(["area_id","entity_id"].includes(kind))return `<label>${esc(this.t("Ziel-Wert (ID)"))}<input id="${prefix}-value" value="${esc(Array.isArray(value)?value.join(", "):value||"")}"></label>`;
    return "";
  }
  stepActionSubForm(step,action,targetKind){
    const expertAvailable=this.editorMode==="expert";
    if(expertAvailable&&step.expert){
      const target=step.target?.kind?step.target:{kind:"all_allowed"};
      return `${this.expertModeToggle("step-expert",true,this.t("Freie Felder bearbeiten (Operation, Ziel, Argumente)"))}
      <label>${esc(this.t("Technische Operation"))}<select id="step-expert-operation">${this.options(this.optionsWithCurrent((this.info.operations||[]).map(v=>({id:v,name:v})),step.operation),step.operation)}</select></label>
      <label>${esc(this.t("Ziel-Art"))}<select id="step-expert-target-kind">${this.options(["template","area_id","area_name","entity_id","entity_ids","entity_name","all_allowed","source_area","none"].map(v=>({id:v,name:v})),target.kind)}</select></label>
      ${this.expertTargetValueField("step-expert-target",target.kind,target.value,target.capture)}
      ${this.lightColorPickerView(step.operation,step.arguments)}
      <label>${esc(this.t("Argumente (JSON)"))}<textarea class="code" id="step-expert-arguments">${esc(step.argumentsText??JSON.stringify(step.arguments||{},null,2))}</textarea></label>
      ${this.stepConversionRepairView(step)}`;
    }
    const choices=this.visibleActionChoices(action?.id).map(choice=>choice.id===action?.id&&action.operation==="light.set_color"?{...choice,name:this.stepActionLabel(step)}:choice);
    const captureSpecs=Object.values(action?.arguments||{}).filter(s=>s?.capture);
    return `${expertAvailable?this.expertModeToggle("step-expert",false,this.t("Freie Felder bearbeiten (Operation, Ziel, Argumente)")):""}
    ${this.availableActionDomains().length?`<label>${esc(this.t("Gerätetyp"))}<select id="step-action-domain"><option value="">${esc(this.t("Alle"))}</option>${this.options(this.availableActionDomains().map(id=>({id,name:this.domainOptionLabel(id)})),this.effectiveActionDomain(action))}</select></label>`:""}
    <label>${esc(this.t("Aktion"))}<select id="step-action">${this.options(choices,action?.id)}</select></label>
    ${this.lightColorPickerView(action?.operation,step.color_arguments||(step.operation===action?.operation&&Object.keys(step.arguments||{}).length?step.arguments:action?.arguments))}
    ${captureSpecs.length?`<div class="capture-info"><strong>${esc(this.t("Auslöser-Platzhalter:"))}</strong> ${captureSpecs.map(s=>`<code>{${esc(s.capture)}}</code>`).join(" ")} – ${esc(this.t("dieser Wert wird aus deiner Spracheingabe gelesen."))}</div>`:""}
    <label>${esc(this.t("Wo?"))}<select id="step-target-kind">${this.options((this.edit.steps||[]).length===1?this.targetChoices(action||{targets:[]}):this.stepTargetChoices(action||{targets:[]}),targetKind)}</select></label>
    ${targetKind==="area_name"?this.expertTargetValueField("step-target",targetKind,step.target_value,step.target_capture||(step.target_value?"":action?.target_capture||"area")):this.stepTargetFieldView("step-target",targetKind,step.target_value,action)}`;
  }
  stepConversionRepairView(step){
    if(!step.expert)return "";
    const exact=this.exactGuidedStep(step);
    if(exact&&!this.stepConversionIssue)return "";
    const message=this.stepConversionIssue||this.t("Dieser technische Schritt entspricht keiner Wizard-Voreinstellung. Wähle selbst eine aktuelle Wizard-Aktion, um ihn kontrolliert zu ersetzen.");
    return `<section class="conversion-repair"><strong>${esc(this.t("In Guided-Bearbeitung übernehmen"))}</strong><p>${esc(message)}</p><label>${esc(this.t("Wizard-Aktion auswählen"))}<select id="step-repair-action"><option value="">${esc(this.t("Aktion auswählen …"))}</option>${this.options(this.actionChoices,"")}</select></label>${this.button(this.t("Änderungen prüfen …"),"repair-step-preset")}</section>`;
  }
  stepsCaptureHintView(){
    const caps=[];
    for(const step of this.edit.steps||[]){
      if(step.kind!=="action")continue;
      const action=this.actionChoices.find(a=>a.id===step.action);
      for(const spec of Object.values(action?.arguments||{}))
        if(spec?.capture&&!caps.includes(spec.capture))caps.push(spec.capture);
    }
    if(!caps.length)return "";
    return `<div class="capture-explanation"><strong>${esc(this.t("Benötigte Platzhalter:"))}</strong> ${caps.map(n=>`<code>{${esc(n)}}</code>`).join(" ")}<p>${esc(this.t("Deine Aktionen erwarten diese Platzhalter aus dem Auslöser."))}</p></div>`;
  }
  stepQuerySubForm(step){
    return `<div class="query-sources">${step.sources.map((source,j)=>this.sourceRowView(source,j,step.sources.length)).join("")}</div><div class="toolbar">${this.button(this.t("+ Datenquelle"),"add-source")}</div>`;
  }
  sourceRowView(source,j,total){
    const caps=this.info.query_source_capabilities||{kinds:[],aggregate_kinds:[]};
    const isAggregate=caps.aggregate_kinds.includes(source.kind);
    const targetKindChoices=this.sourceTargetChoices(source.kind);
    return `<fieldset class="source-row"><legend>${esc(this.t("Datenquelle {n}",{n:j+1}))}</legend>
    <label>${esc(this.t("Art"))}<select id="source-${j}-kind">${this.options(caps.kinds.map(id=>({id,name:this.sourceKindLabel(id)})),source.kind)}</select></label>
    <label>${esc(this.t("Wo?"))}<select id="source-${j}-target-kind">${this.options(targetKindChoices,source.target_kind)}</select></label>
    ${["area_name","entity_name"].includes(source.target_kind)?this.expertTargetValueField(`source-${j}-target`,source.target_kind,source.target_value,source.capture):this.stepTargetFieldView(`source-${j}-target`,source.target_kind,source.target_value)}
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
  // A single guided query action (e.g. entity.get_state) still gets its
  // per-domain response-field helper text/chips (queryResponseChips/
  // wizardStateResponseHelp), same as the old single-action Wizard - only
  // meaningful with exactly one step, since with 2+ steps a query's fields
  // are already offered, namespaced by alias, via stepsResponseChips().
  singleQueryAction(){const steps=this.edit?.steps||[];if(steps.length!==1||steps[0].kind!=="action"||steps[0].expert)return null;return this.actionChoices.find(a=>a.id===steps[0].action)||null;}
  singleQueryResponseHelp(){const action=this.singleQueryAction();return action?this.wizardStateResponseHelp(action):"";}
  singleQueryResponseChips(field){const action=this.singleQueryAction();return action?this.queryResponseChips(field,action):"";}
  // Renders the last blocked "validate_step" result for the CURRENTLY
  // open wizard page (cleared on any edit or on a successful forward
  // move - see stepValidation's own comment). Real backend `path` values
  // (e.g. "arguments.brightness_pct", "target.kind") replace the old
  // regex-based message guessing wizardBackendIssues() did for the
  // final-step-only summary.
  stepValidationView(){
    const validation=this.stepValidation;
    if(!validation||validation.step!==this.wizardStep||!validation.issues?.length)return "";
    return `<div class="validation-summary" role="alert">${validation.issues.map(i=>`<div class="field-message ${i.severity||"error"}">${esc(i.message)}</div>`).join("")}</div>`;
  }
  stepsValidationSummary(){const issues=this.wizardBackendIssues();const errors=issues.filter(i=>i.level==="error").length,warnings=issues.filter(i=>i.level==="warning").length;return `<div class="validation-summary"><strong>${errors} ${esc(this.t(errors===1?"error":"errors"))} · ${warnings} ${esc(this.t(warnings===1?"warning":"warnings"))}</strong>${issues.map(i=>`<div class="field-message ${i.level}" role="${i.level==="error"?"alert":"status"}">${esc(i.message)}</div>`).join("")}</div>`;}
  openStepEditor(index){
    this.editingStepIndex=index;
    // Gerätetyp is per step: null = derive from that step's own action on render.
    this.wizardActionDomain=null;
    this.editingStepDraft=this.edit?.steps?.[index]?clone(this.edit.steps[index]):null;
    this.stepEditorError=null;
    this.stepConversionIssue=null;
  }
  stepFieldError(field,message){return {field,message:this.t(message)};}
  // Maps a backend ValidationIssue's `path` (e.g. "operation",
  // "arguments.brightness_pct", "target.kind") to the step-card field id
  // showStepEditorError() should focus/report on - the one place this
  // wire-format -> DOM-id translation happens.
  actionDraftFieldFor(path){
    if(!path)return "step-expert-arguments";
    if(path==="operation")return "step-expert-operation";
    if(path.startsWith("arguments"))return "step-expert-arguments";
    if(path==="target.kind")return "step-expert-target-kind";
    if(path.startsWith("target"))return "step-expert-target-value";
    return "step-expert-arguments";
  }
  // Step 2's authoritative "is this one action/query structurally valid"
  // check - operation known, target kind allowed for it, arguments
  // required/typed/ranged. Delegates to the backend's ActionExecutor.
  // describe_shape via "validate_step" (scope: action_draft) instead of
  // re-implementing any of those rules in JS (there used to be a second,
  // drifting copy here - operationArgumentsError/most of
  // validateStepCandidate's expert branch - now deleted). Returns null
  // when valid, or a {field, message} pair for showStepEditorError.
  async describeActionDraft(step){
    const target={kind:step.target?.kind,...(step.target?.value!==undefined?{value:step.target.value}:{}),...(step.target?.capture?{capture:step.target.capture}:{})};
    const result=await this.api("validate_step",{step:1,scope:"action_draft",draft:{operation:step.operation,target,arguments:step.arguments||{}}});
    const issue=(result.issues||[])[0];
    if(!issue)return null;
    return {field:this.actionDraftFieldFor(issue.path),message:issue.message};
  }
  // Local, genuinely UI-only completeness checks (no business rule ever
  // duplicated here): a name was typed and is a valid identifier, a
  // target kind is actually selected, its value/capture field was
  // filled in, and (Wizard-guided steps only) the chosen target kind is
  // one this action's own UI offers. Everything about whether the
  // chosen operation/target/arguments combination is actually VALID is
  // the backend's job (describeActionDraft above, for an expert step) or
  // is correct by construction (a Wizard preset's operation/arguments
  // always come verbatim from user_actions()'s catalog-derived choices).
  validateStepCandidate(step){
    if(step.alias!==""&&step.alias!=null&&(!(typeof step.alias==="string")||!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(step.alias)))return this.stepFieldError("step-alias","Der Schrittname muss mit einem Buchstaben oder Unterstrich beginnen und darf nur Buchstaben, Zahlen und Unterstriche enthalten.");
    if(step.kind==="action"&&step.expert){
      if(typeof step.operation!=="string"||!step.operation.trim())return this.stepFieldError("step-expert-operation","Bitte eine technische Operation auswählen.");
      if(!step.arguments||typeof step.arguments!=="object"||Array.isArray(step.arguments))return this.stepFieldError("step-expert-arguments","Argumente müssen ein JSON-Objekt sein.");
      const target=step.target||{},kind=target.kind;
      if(!["template","area_id","area_name","entity_id","entity_ids","entity_name","all_allowed","source_area","none"].includes(kind))return this.stepFieldError("step-expert-target-kind","Bitte eine gültige Ziel-Art auswählen.");
      if(kind==="template"&&!(typeof target.value==="string"&&target.value.trim()))return this.stepFieldError("step-expert-target-value","Bitte einen Jinja-Ausdruck für das Ziel eingeben.");
      if(["area_id","entity_id"].includes(kind)&&!(typeof target.value==="string"&&target.value.trim()))return this.stepFieldError("step-expert-target-value","Bitte einen Zielwert eingeben.");
      if(kind==="entity_ids"&&(!Array.isArray(target.value)||!target.value.length))return this.stepFieldError("step-expert-target-value","Bitte mindestens eine Entity-ID eingeben.");
      if(["area_name","entity_name"].includes(kind)&&!(typeof target.value==="string"&&target.value.trim())){
        if(!(typeof target.capture==="string"&&/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(target.capture)))return this.stepFieldError("step-expert-target-capture","Bitte einen gültigen Platzhalter-Namen eingeben.");
      }
    }else if(step.kind==="action"){
      const action=this.actionChoices.find(row=>row.id===step.action);
      if(!action)return this.stepFieldError("step-action","Bitte eine Wizard-Aktion auswählen.");
      const targets=(this.edit?.steps||[]).length===1?this.targetChoices(action):this.stepTargetChoices(action);
      if(!targets.some(row=>row.id===step.target_kind))return this.stepFieldError("step-target-kind","Diese Ziel-Art ist mit der gewählten Wizard-Aktion nicht kompatibel.");
    }
    return null;
  }
  showStepEditorError(error){
    this.stepEditorError=error;this.render();
    queueMicrotask(()=>{const field=this.shadowRoot.getElementById(error.field);field?.setCustomValidity?.(error.message);field?.focus?.();field?.reportValidity?.();});
  }
  async stepsApplyDraft(){
    if(this.editingStepIndex==null||!this.edit?.steps?.[this.editingStepIndex])return true;
    let candidate;
    try{candidate=this.collectStepDraft(this.editingStepIndex);}catch(error){this.showStepEditorError({field:error.field||"step-expert-arguments",message:error.message||this.t("Ungültige Step-Eingabe.")});return false;}
    const invalid=this.validateStepCandidate(candidate);if(invalid){this.showStepEditorError(invalid);return false;}
    if(candidate.kind==="action"&&candidate.expert){
      // An invalid draft must never be accepted into the canonical step
      // state - the same local Step-2 structure check Next itself runs,
      // just scoped to this one draft and gating "Übernehmen" instead.
      const remoteError=await this.describeActionDraft(candidate);
      if(remoteError){this.showStepEditorError(remoteError);return false;}
    }
    delete candidate.argumentsText;
    const steps=[...this.edit.steps];steps[this.editingStepIndex]=candidate;this.edit.steps=steps;
    this.editingStepDraft=clone(candidate);this.stepEditorError=null;this.stepConversionIssue=null;
    this.conversionBlockers=(this.conversionBlockers||[]).filter(row=>row.step_index!==this.editingStepIndex);
    if(!this.conversionBlockers.length)this.conversionBlockers=null;
    return true;
  }
  collectExpertTargetValue(prefix,kind){
    if(kind==="template")return this.value(`${prefix}-value`);
    if(["area_name","entity_name"].includes(kind))return null;
    if(kind==="entity_ids")return this.value(`${prefix}-value`).split("\n").map(v=>v.trim()).filter(Boolean);
    if(["area_id","entity_id"].includes(kind))return this.value(`${prefix}-value`).trim();
    return null;
  }
  collectStepDraft(index){
    const step=clone(this.editingStepDraft||this.edit.steps[index]);
    if(!step)return null;
    const aliasField=this.shadowRoot.getElementById("step-alias");
    const aliasFallback=this.value("step-alias")||(typeof step.alias==="string"?step.alias:String(step.alias??""));
    const aliasText=(aliasField?aliasField.value:aliasFallback).trim();
    const alias=step.alias!=null&&typeof step.alias!=="string"&&aliasText===String(step.alias)?step.alias:aliasText;
    if(step.kind==="action"){
      if(step.expert){
        const targetKind=this.value("step-expert-target-kind")||step.target?.kind||"all_allowed";
        const target={...extras(step.target||{},["kind","value","capture"]),kind:targetKind};
        if(["area_name","entity_name"].includes(targetKind)){if(this.shadowRoot.getElementById("step-expert-target-value"))target.value=this.value("step-expert-target-value");else target.capture=this.value("step-expert-target-capture").trim();}
        else if(!["all_allowed","source_area","none"].includes(targetKind))target.value=this.collectExpertTargetValue("step-expert-target",targetKind);
        let args;try{args=this.jsonObject("step-expert-arguments",step.arguments||{});}catch(error){error.field="step-expert-arguments";throw error;}
        return {...step,alias,expert:true,operation:this.value("step-expert-operation")||step.operation,target,arguments:args};
      } else {
        const targetKind=this.value("step-target-kind");
        const targetValue=targetKind==="area_name"?this.value("step-target-value")||null:targetKind==="area_id"?this.value("step-target-area"):targetKind==="entity_id"?this.value("step-target-entity"):targetKind==="entity_ids"?[...this.shadowRoot.querySelectorAll('input[name="entity"]:checked')].map(e=>e.value):null;
        return {...step,alias,expert:false,...(targetKind==="area_name"?{target_capture:this.value("step-target-capture")||step.target_capture}:{}),action:this.value("step-action")||step.action,target_kind:targetKind||step.target_kind,target_value:targetValue};
      }
    } else {
      const sources=step.sources.map((source,j)=>{
        const expert=!!source.expert;
        const targetKind=this.value(`source-${j}-target-kind`)||source.target_kind;
        const isCapture=["area_name","entity_name"].includes(targetKind);
        return {
          ...source,
          kind:this.value(`source-${j}-kind`)||source.kind,
          expert,
          target_kind:targetKind,
          target_value:isCapture?(this.shadowRoot.getElementById(`source-${j}-target-value`)?this.value(`source-${j}-target-value`):null):targetKind==="area_id"?this.value(`source-${j}-target-area`):targetKind==="entity_id"?this.value(`source-${j}-target-entity`):null,
          capture:isCapture?this.value(`source-${j}-target-capture`).trim():"",
          domain_filter:this.value(`source-${j}-domain-filter`).trim(),
          device_class:this.value(`source-${j}-device-class`).trim(),
          attribute:this.value(`source-${j}-attribute`).trim(),
          state_filter:this.value(`source-${j}-state-filter`).trim(),
          variable:this.value(`source-${j}-variable`).trim(),
          unit_variable:this.value(`source-${j}-unit-variable`).trim(),
        };
      });
      return {...step,alias,sources};
    }
  }
  exactGuidedStep(step){
    if(step?.kind!=="action"||!step.expert)return step;
    const normalized=this.normalizeStepFromAction(step,(this.edit?.steps||[]).length===1,false);
    return normalized.expert?null:normalized;
  }
  expertDraftFromGuided(step){
    const action=this.actionChoices.find(row=>row.id===step.action);if(!action)return null;
    const target={...clone(step.target_extras||{}),kind:step.target_kind||this.targetChoices(action)[0]?.id||"all_allowed"};
    if(step.target_kind==="area_name"&&!step.target_value)target.capture=step.target_capture||action.target_capture||"area";else if(step.target_value!=null)target.value=clone(step.target_value);
    return {...step,expert:true,operation:action.operation,arguments:clone(step.color_arguments||(action.operation==="light.set_color"&&Object.keys(step.arguments||{}).length?step.arguments:action.arguments)||{}),target};
  }
  guidedDraftFromPreset(step,action){
    const allowed=((this.edit?.steps||[]).length===1?this.targetChoices(action):this.stepTargetChoices(action)).map(row=>row.id);
    const oldTarget=step.target||{};const targetKind=allowed.includes(oldTarget.kind)?oldTarget.kind:(allowed[0]||"");
    const targetValue=targetKind===oldTarget.kind?(oldTarget.value??null):null;
    return {...step,expert:false,action:action.id,operation:action.operation,arguments:clone(action.arguments||{}),target_kind:targetKind,target_value:clone(targetValue),...(targetKind==="area_name"&&oldTarget.capture?{target_capture:oldTarget.capture}:{}),target:clone(oldTarget)};
  }
  presetReplacementSummary(before,after){
    const rows=[];
    if(before.operation!==after.operation)rows.push(`${this.t("Operation")}: ${before.operation||"–"} → ${after.operation}`);
    const afterTarget={kind:after.target_kind,...(after.target_value!=null?{value:after.target_value}:{}),...(after.target_capture?{capture:after.target_capture}:{})};
    if(!sameData(before.target||{},afterTarget))rows.push(`${this.t("Ziel")}: ${JSON.stringify(before.target||{})} → ${JSON.stringify(afterTarget)}`);
    if(!sameData(before.arguments||{},after.arguments||{}))rows.push(`${this.t("Argumente")}: ${JSON.stringify(before.arguments||{})} → ${JSON.stringify(after.arguments||{})}`);
    return rows;
  }
  async collectSteps(){
    if(!await this.stepsApplyDraft())throw Error(this.stepEditorError?.message||this.t("Ungültige Step-Eingabe."));
    const c=this.edit;
    const language=this.validLanguage(this.bookLanguage)||this.effectiveBookLanguage();
    const steps=(c.steps||[]).map(step=>step.kind==="action"
      ?(step.expert
        ?{alias:step.alias===""?null:step.alias,kind:"action",expert:true,operation:step.operation,target:clone(step.target||{kind:"all_allowed"}),arguments:clone(step.arguments||{})}
        :{alias:step.alias===""?null:step.alias,kind:"action",action:step.action,target_kind:step.target_kind,target_value:step.target_value,...(step.target_capture?{target_capture:step.target_capture}:{}),...(step.target_extras?{target_extras:clone(step.target_extras)}:{}),...(step.color_arguments?{color_arguments:clone(step.color_arguments)}:step.operation==="light.set_color"&&Object.keys(step.arguments||{}).length?{color_arguments:clone(step.arguments)}:{})})
      :{alias:step.alias===""?null:step.alias,kind:"query",sources:step.sources.map(s=>({
          kind:s.kind,expert:!!s.expert,target_kind:s.target_kind,target_value:s.target_value,
          ...(s.source_extras?{source_extras:clone(s.source_extras)}:{}),...(s.target_extras?{target_extras:clone(s.target_extras)}:{}),
          capture:s.capture||undefined,
          domain_filter:s.domain_filter||undefined,device_class:s.device_class||undefined,
          attribute:s.attribute||undefined,state_filter:s.state_filter||undefined,
          variable:s.variable,unit_variable:s.unit_variable||undefined,
        }))});
    const payload={
      id:c.id,
      translations:Object.fromEntries(["name","description","trigger","triggers","examples","output","catchphrase","failure_text"].filter(key=>c[key]!==undefined).map(key=>[key,clone(c[key])])),
      trigger_language:language, default_language:this.storedBookLanguage()||this.effectiveBookLanguage(),
      name:this.value("spell-name"), description:this.value("spell-description"),
      examples:this.value("spell-examples").split("\n").filter(Boolean),
      triggers:this.value("triggers").split("\n").map(t=>t.trim()).filter(Boolean),
      tags:this.value("tags").split(",").map(t=>t.trim()).filter(Boolean),
      output:this.value("spell-output"), catchphrase:this.value("spell-catchphrase"),
      failure_text:this.value("spell-failure-text"),
      output_mode:this.value("output-mode")||undefined,
      steps,
    };
    if(steps.length===0)payload.assist_redirect=this.shadowRoot.getElementById("assist-redirect")?this.checked("assist-redirect"):c.assist_redirect;
    if("expert" in c)payload.expert_variant=this.shadowRoot.getElementById("expert-variant-json")?this.jsonObject("expert-variant-json",c.expert):c.expert;
    return payload;
  }
  stepsWizardView(){
    const c=this.edit;
    const steps=[this.t("Basic information"),this.t("Actions & Queries"),this.t("Trigger"),this.t("Response"),this.t("Review & Test")];
    const step=this.wizardStep;
    const maxStep=this.wizardMaxStep??0;
    const explanation=[
      this.t("Give the spell a clear identity. HASE uses this information to help you recognize and organize it."),
      this.t("Add one or more actions and queries. Give each an optional name to reuse its result in the response below."),
      this.t("Define what you will say. HASE compares spoken words with these phrases and extracts values written in braces."),
      this.t("Combine free text with the variables your actions and queries produce."),
      this.t("Review the spell in plain language, validate it, and try a dry run without operating devices."),
    ][step];
    const modeToggle=this.editorMode==="expert"
      ?this.button(this.t("Zu Wizard wechseln"),"convert-to-wizard")
      :this.button(this.t("Zu Expert wechseln"),"convert-to-expert");
    const blockersNotice=this.conversionBlockersView();
    return `<section id="spell-editor" class="wizard"><div class="toolbar">${modeToggle}${this.editExisting?this.button(this.t("Duplizieren"),"duplicate",c.id):""}${this.editExisting?this.button(this.t("Löschen"),"delete",c.id,"danger"):""}${this.editExisting?this.button(this.t("Exportieren"),"export-one",c.id):""}</div><h2>${esc(this.editExisting?this.t("Spell bearbeiten"):this.t("Spell erstellen"))}</h2>${blockersNotice}<ol class="wizard-steps">${steps.map((label,index)=>{const cls=index===step?"current":index<step?"done":"",inner=`<span>${index+1}</span>${esc(label)}`;return `<li class="${cls}">${index<=maxStep?`<button type="button" class="step-button" data-do="wizard-goto" data-id="${index}" aria-current="${index===step?"step":"false"}">${inner}</button>`:`<div class="step-button" aria-disabled="true">${inner}</div>`}</li>`;}).join("")}</ol><div class="step-intro"><h3>${esc(steps[step])}</h3><p>${esc(explanation)}</p></div>${this.stepValidationView()}
    <div class="wizard-step" ${step===0?"":"hidden"}><label><span>${esc(this.t("Name"))}</span><input id="spell-name" value="${esc(this.editText(c.name)||this.editText(c.trigger))}" placeholder="Gute Nacht"></label><label>${esc(this.t("Beschreibung"))}<textarea id="spell-description">${esc(this.editText(c.description))}</textarea></label><label>${esc(this.t("Examples"))}<small>${esc(this.t("One example per line."))}</small><textarea id="spell-examples">${esc((this.editText(c.examples)||[]).join("\n"))}</textarea></label><label>${esc(this.t("Sammlungen"))}<small>${esc(this.t("Mit Kommas trennen."))}</small><input id="tags" value="${esc((c.tags||[]).join(", "))}"></label></div>
    <div class="wizard-step" ${step===1?"":"hidden"}>${this.stepListView()}${this.expertVariantView()}</div>
    <div class="wizard-step" ${step===2?"":"hidden"}><label><span>${esc(this.t("Wenn ich sage …"))}</span><small>${esc(this.t("Eine Formulierung pro Zeile."))}</small><textarea id="triggers" placeholder="gute nacht">${esc([this.editText(c.trigger),...(this.editText(c.triggers)||[])].filter(Boolean).join("\n"))}</textarea></label>${this.placeholderChips()}${this.stepsCaptureHintView()}</div>
    <div class="wizard-step" ${step===3?"":"hidden"}>${this.singleQueryResponseHelp()}<div><label><span>${esc(this.t("Text response"))}</span><textarea id="spell-output">${esc(this.editText(c.output))}</textarea></label>${this.placeholderChips("spell-output")}${this.stepsResponseChips("spell-output")}${this.singleQueryResponseChips("spell-output")}</div><div><label>${esc(this.t("Spoken response"))}<textarea id="spell-catchphrase">${esc(this.editText(c.catchphrase))}</textarea></label>${this.placeholderChips("spell-catchphrase")}${this.stepsResponseChips("spell-catchphrase")}${this.singleQueryResponseChips("spell-catchphrase")}</div><div><label>${esc(this.t("Antwort bei Fehler (optional)"))}<textarea id="spell-failure-text">${esc(this.editText(c.failure_text))}</textarea></label><small>${esc(this.t("Wird gesagt, wenn die Aktion fehlschlägt, statt des Standardsatzes. Nur Platzhalter aus dem Trigger. Leer lassen für den Standardsatz."))}</small></div><label>${esc(this.t("Output"))}<select id="output-mode">${this.outputModeOptions(c.output_mode)}</select></label>${(c.steps||[]).length===0?`<label><input id="assist-redirect" type="checkbox" ${c.assist_redirect?"checked":""}> ${esc(this.t("Antwort an Home Assistants eigenen Conversation Agent weiterleiten, statt selbst zu antworten."))}</label>`:""}${this.button(this.t("Variablen aktualisieren"),"wizard-validate")}</div>
    <div class="wizard-step" ${step===4?"":"hidden"}><div class="spell-review"><h3>${esc(this.t("When I say:"))}</h3><blockquote>${esc(this.value("triggers").split("\n").find(Boolean)||this.editText(c.trigger)||this.t("No trigger yet"))}</blockquote>${(c.steps||[]).length?`<h3>${esc(this.t("Schritte:"))}</h3><ul>${(c.steps||[]).map(s=>`<li>${esc(s.alias||"–")}: ${esc(this.stepSummary(s))} ${this.stepExpertBadge(s)}</li>`).join("")}</ul>`:`<h3>${esc(this.t("Reine Textausgabe"))}</h3>`}</div>${this.stepsValidationSummary()}<div class="test-panel"><div class="book-context wizard-test-context"><label>${esc(this.t("Testen mit Client"))} <select id="wizard-profile"><option value="">${esc(this.t("Kein Client ausgewählt"))}</option>${this.options(this.testProfiles(),this.profile)}</select></label><label>${esc(this.t("Herkunftsbereich"))} <select id="wizard-source"><option value="">${esc(this.t("Nicht angegeben"))}</option>${this.options(this.registry.areas,this.source)}</select></label></div><label><span>${esc(this.t("Testphrase"))}</span><small>${esc(this.t("Platzhalter durch echte Werte ersetzen."))}</small><input id="phrase" value="${esc(this.localized(c.trigger))}" placeholder="gute nacht"></label><div class="toolbar">${this.button(this.t("Validate spell"),"wizard-validate")}${this.button(this.t("Testen – ohne Geräteaufruf"),"test-edit","","primary")}${this.button(this.t("Matching erklären"),"explain-edit")}${this.editExisting?this.button(this.t("Jetzt wirklich ausführen …"),"execute"):""}</div></div></div>
    <aside id="local-editor-hints" aria-live="polite"></aside><div class="wizard-nav"><div class="toolbar">${step?this.button(this.t("Back"),"wizard-back"):""}${this.button(this.t("Abbrechen"),"close-editor")}</div><span>${esc(this.t("Step {current} of {total}",{current:step+1,total:steps.length}))}</span>${step<steps.length-1?this.button(this.t("Next"),"wizard-next","","primary"):this.button(this.t("Speichern"),"save","","primary")}</div></section>`;
  }
  resultView(inModal=false){const r=this.result,queryAnswer=r.data?.response_text??(r.status==="QUERY_ANSWER"&&r.data?.state!=null?`${r.data.state}${r.data.unit||""}`:null);return `<section class="result" role="status">${inModal?`<div class="modal-head"><h2 id="modal-title">${esc(this.messageText(reasons[r.reason_code] || r.title || this.t("Prüfergebnis")))}</h2>${this.button("×","close-result","","modal-close")}</div><div class="modal-body">`:`<h2>${esc(this.messageText(reasons[r.reason_code] || r.title || this.t("Prüfergebnis")))}</h2>`}${queryAnswer!=null?`<div class="capture-explanation"><strong>${esc(this.t("Returned answer"))}</strong><p>${esc(queryAnswer)}</p></div>`:""}${r.data?.user_fallback?`<p>${esc(this.t("User-Fallback wurde verwendet."))}</p>`:""}${r.data?.entity_ids?`<p><strong>${r.data.entity_ids.length}</strong> ${esc(this.t("effektive Ziele"))}</p><ul>${r.data.entity_ids.map(id=>`<li>${esc(this.registry.entities.find(e=>e.id===id)?.name|| (this.expert?id:this.t("Unbekanntes Gerät")))}</li>`).join("")}</ul>`:""}${(r.data?.warnings||[]).map(w=>`<p>${esc(this.messageText(w))}</p>`).join("")}${this.stepsResultView(r.data?.steps,r.preview_only)}${r.match_explain?this.explainView(r.match_explain):this.matchingTraceView(r.matching_trace)}${this.expert?`<details open><summary>${esc(this.t("Resolver / Policy / Rohes und effektives Ergebnis"))}</summary><h3>${esc(this.t("Rohes Ergebnis:"))} ${Array.isArray(r.data?.raw)?r.data.raw.length:"–"} ${esc(this.t("Ziele"))}</h3><h3>${esc(this.t("Effektives HASE-Ergebnis:"))} ${r.data?.entity_ids?.length ?? 0} ${esc(this.t("Ziele"))}</h3><ul>${(r.data?.targets||[]).map(t=>`<li>${esc(this.registry.entities.find(e=>e.id===t.entity_id)?.name || t.entity_id || "Template")} · ${esc(this.registry.areas.find(a=>a.id===t.area_id)?.name || this.t("Ohne Bereich"))} · ${esc(this.messageText(reasons[t.reason]||t.reason))}</li>`).join("")}</ul><pre>${esc(JSON.stringify(r,null,2))}</pre></details>`:""}${r.yaml?`<pre>${esc(r.yaml)}</pre>`:""}${r.portability?`<p>${r.portability.length} ${esc(this.t("instanzabhängige Ziele im Export."))}</p>`:""}${inModal?`</div><div class="modal-actions">${this.button(this.t("Schließen"),"close-result","","primary")}</div>`:this.button(this.t("Schließen"),"close-result")}</section>`;}
  importView(){const preview=this.importPreview;return `<section><div class="toolbar">${this.button(this.t("← Zur Liste"),"close-editor")}</div><h2>${esc(this.t("In {name} importieren",{name:this.bookName(this.current.book)}))}</h2><p>${esc(this.t("Eine Datei hier ablegen, auswählen oder YAML einfügen. Das gewählte Buch bleibt eigenständig."))}</p><input id="file" type="file" accept=".yaml,.yml,.txt"><label>YAML / Text<textarea id="import-text" class="code" style="min-height:220px">${esc(this.importText||"")}</textarea></label>${this.button(this.t("Import analysieren"),"analyze","","primary")}
    ${preview?`<h3>${preview.incoming.commands.length} ${esc(this.t("Spells erkannt"))}</h3><p>${esc(this.t("Expert-Ausführung bleibt {state}.",{state:this.t(preview.expert_allowed?this.t("AN"):this.t("AUS"))}))}</p><label>${esc(this.t("Für alle Konflikte"))}<select id="batch-conflict">${this.options([{id:"keep",name:this.t("Vorhandene behalten")},{id:"replace",name:this.t("Importierte ersetzen")},{id:"copy",name:this.t("Als Kopien hinzufügen")},{id:"skip",name:this.t("Überspringen")}],"keep")}</select></label>${preview.conflicts.map(id=>`<label>${esc(id)}<select data-conflict="${esc(id)}">${this.options([{id:"keep",name:this.t("Vorhandenen behalten")},{id:"replace",name:this.t("Importierten ersetzen")},{id:"copy",name:this.t("Als Kopie hinzufügen")},{id:"skip",name:this.t("Überspringen")}],"keep")}</select></label>`).join("")}
    ${preview.suggestions.length?`<h3>${esc(this.t("Lokale Zuordnungen prüfen"))}</h3>${preview.suggestions.map((s,i)=>`<label>${esc(s.value)} → <select data-correction="${i}"><option value="">${esc(this.t("Unverändert lassen"))}</option>${this.options((s.options||[]),null)}</select><small>${esc(this.t("Vorschläge:"))} ${esc(s.suggestions.join(", ")||this.t("Keine eindeutige Zuordnung"))}</small></label>`).join("")}${this.button(this.t("Zuordnungen übernehmen und neu prüfen"),"correct")}`:""}
    <details><summary>${esc(this.t("Prüfung pro Spell"))}</summary>${preview.report.spells.map(r=>`<h4>${esc(r.id)} · ${esc(r.capability)}</h4>${this.diagnostics(r)}`).join("")}</details><div class="toolbar">${this.button(this.t("Vorschau übernehmen"),"apply-import","","primary")}</div>`:""}</section>`;}
  placeholderNames(){const names=new Set(["area","entity","value","target"]);if(this.edit?.expert?.target?.capture)names.add(this.edit.expert.target.capture);for(const spec of Object.values(this.edit?.expert?.arguments||{}))if(spec?.capture)names.add(spec.capture);for(const step of this.edit?.steps||[]){if(step.kind==="action"){if(step.expert){if(step.target?.capture)names.add(step.target.capture);}else{const action=this.actionChoices.find(a=>a.id===step.action);for(const spec of Object.values(action?.arguments||{}))if(spec?.capture)names.add(spec.capture);}}else for(const source of step.sources||[])if(source.expert&&source.capture)names.add(source.capture);}return [...names].filter(name=>/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(name));}
  responsePlaceholderNames(){const field=this.shadowRoot.getElementById?.("triggers");const text=field?field.value:[this.editText(this.edit?.trigger),...(this.editText(this.edit?.triggers)||[])].filter(Boolean).join("\n");const lines=text.split("\n").filter(line=>line.trim());const captures=lines.map(line=>new Set([...line.matchAll(/\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g)].map(match=>match[1])));return captures.length?[...captures[0]].filter(name=>captures.every(names=>names.has(name))):[];}
  refreshResponseChips(){for(const field of ["spell-output","spell-catchphrase","expert-catchphrase"]){const container=this.shadowRoot.getElementById(`chips-${field}`);if(container)container.innerHTML=this.placeholderChips(field);}}
  placeholderChips(field="triggers"){const names=field==="triggers"?this.placeholderNames():this.responsePlaceholderNames();return `<div class="placeholder-chips" role="group" aria-label="${esc(this.t("Platzhalter einfügen"))}"><small>${esc(this.t("Platzhalter einfügen"))}</small>${names.map(name=>`<button type="button" data-do="insert-placeholder" data-id="${esc(field==='triggers'?name:field+':'+name)}" aria-label="${esc(this.t("{placeholder} einfügen",{placeholder:`{${name}}`}))}">{${esc(name)}}</button>`).join("")}</div>`;}
  insertPlaceholder(id){const [target,name]=id.includes(":")?id.split(":"):["triggers",id];if(!["triggers","spell-output","spell-catchphrase","expert-catchphrase"].includes(target))return;const field=this.shadowRoot.getElementById(target);const stepFieldNames=(this.wizardValidation?.spell_variables||[]).filter(r=>r.source==="step_field").map(r=>r.name);if(!field || !(target==="triggers"?this.placeholderNames():[...this.responsePlaceholderNames(),...stepFieldNames]).includes(name))return;const start=field.selectionStart ?? field.value.length,end=field.selectionEnd ?? start;const before=field.value.slice(0,start),after=field.value.slice(end);const token=(!before || /\s$/.test(before)?"":" ")+`{${name}}`+(!after || /^[\s.,!?;:]/.test(after)?"":" ");field.focus();field.setRangeText(token,start,end,"end");field.dispatchEvent(new Event("input",{bubbles:true,composed:true}));}
  testPhrase(id="phrase"){const phrase=this.value(id).trim();if(!phrase || /[{}\[\]]/.test(phrase))throw Error(this.t("Bitte eine konkrete Testphrase ohne Platzhalter oder Klammern eingeben, z. B. „lumos Gondor“ statt „lumos {area}“."));return phrase;}
  // Wizard and Expert are the SAME editor (item 4/17): both always save
  // through collectSteps()/wizard_spell() - `this.editorMode` only picks
  // which widgets are shown (per-step Expert toggles), never a different
  // save payload shape. This also fixes the old global "Expert Mode"
  // viewing toggle (this.expert, an execution-permission-adjacent UI
  // setting, see the "expert" action) leaking into what gets SAVED - it is
  // never read here anymore (item 13).
  async collectSpell(){return {mode:"wizard",spell:await this.collectSteps()};}
  // Step N's own Next-gate (and the "Apply" single-draft gate,
  // describeActionDraft above) - one shared backend check
  // (core.studio_validation.validate_wizard_step), never a second JS
  // implementation of these rules. Returns {ok, issues}.
  async validateWizardStep(step){
    const payload=await this.collectSpell();
    return this.api("validate_step",{step,...payload});
  }
  // A step may only be left FORWARD if its own state is valid (going
  // back is always allowed - see wizard-back in act()). Re-runs on
  // every forward transition, so Back -> edit -> Next re-validates for
  // free. On success, extends wizardMaxStep (now "furthest step reached
  // via a successful validate_step", not just "furthest visited").
  //
  // Walks every step BETWEEN the one being left and the target, in
  // order - not just the single step currently open. An already-saved
  // spell opens with wizardMaxStep=4 (every breadcrumb immediately
  // clickable, since a saved spell is trusted to already be valid) -
  // without this loop, jumping straight from Step 1 to Step 5 would
  // validate only Step 1 and silently skip Steps 2-4's own checks
  // (e.g. a stale trigger saved before this validation existed, with
  // two adjacent placeholders, would never be caught until the final
  // "validate"/"dry_run" call - exactly the bug this loop closes).
  // Landing on the first step that actually fails (not just the
  // target) also matches what clicking "Weiter" through each step in
  // sequence would have done.
  async leaveStepForward(targetStep){
    for(let step=this.wizardStep; step<targetStep; step++){
      const result=await this.validateWizardStep(step);
      if(!result.ok){
        this.wizardStep=step;
        this.stepValidation={step,issues:result.issues||[]};
        this.render();
        return false;
      }
    }
    this.stepValidation=null;
    this.wizardMaxStep=Math.max(this.wizardMaxStep??0,targetStep);
    return true;
  }
  jsonObject(id,fallback={}){
    const field=this.shadowRoot.getElementById(id);
    const text=field?field.value:this.value(id);
    if(!text?.trim())return field?{}:fallback;
    try{const value=JSON.parse(text);if(value&&typeof value==="object"&&!Array.isArray(value))return value;}catch{}
    const message=this.t("Bitte ein gültiges JSON-Objekt eingeben. Die Eingabe wurde nicht übernommen.");
    field?.setCustomValidity?.(message);field?.focus?.();field?.reportValidity?.();
    throw Error(message);
  }
  localHintMessages(){
    const messages=[];
    if(!this.value("spell-name").trim())messages.push(this.t("Ohne Namen wird die erste Trigger-Formulierung als Name verwendet."));
    const triggers=this.value("triggers").trim();
    if(!triggers)messages.push(this.t("Mindestens eine Trigger-Formulierung fehlt noch."));
    const used=new Set();
    for(const step of this.edit?.steps||[]){
      const action=this.actionChoices.find(a=>a.id===step.action);
      const args=step.expert?step.arguments:action?.arguments;
      for(const spec of Object.values(args||{}))if(spec?.capture)used.add(spec.capture);
      if(step.target?.capture&&step.expert)used.add(step.target.capture);
      if(!step.expert&&step.target_kind==="area_name")used.add(step.target_capture||action?.target_capture||"area");
      for(const source of step.sources||[])if(source.capture)used.add(source.capture);
      if(action?.capability_hint&&step.target_kind==="entity_id"){
        const entity=this.registry.entities.find(e=>e.id===step.target_value);
        if(entity?.capabilities&&entity.capabilities[action.capability_hint]===false)messages.push(this.t("Das ausgewählte Gerät meldet keine Unterstützung für diese Funktion."));
      }
    }
    const response=this.value("spell-output")+this.value("spell-catchphrase");
    for(const capture of new Set([...triggers.matchAll(/\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g)].map(m=>m[1])))
      if(!used.has(capture)&&!response.includes(`{${capture}}`))messages.push(this.t("Platzhalter {capture} wird bisher weder in einem Ziel, Argument noch in der Antwort verwendet.",{capture:`{${capture}}`}));
    return [...new Set(messages)];
  }
  refreshLocalHints(){
    const container=this.shadowRoot.getElementById("local-editor-hints");if(!container)return;
    container.innerHTML=this.localHintMessages().map(message=>`<p class="field-message warning">${esc(message)}</p>`).join("");
  }
  blockerMessage(blocker){
    if(typeof blocker==="string")return this.messageText(blocker);
    const keys={template_target:"Template-Ziel wird vom Wizard nicht unterstützt.",entity_name_target:"Gesprochene Geräteziele werden für Aktionen im Wizard nicht unterstützt.",area_capture_target:"Gesprochene Bereichsziele werden für mehrere Aktionen im Wizard nicht unterstützt.",operation_arguments:"Operation/Argumente entsprechen keiner Wizard-Voreinstellung.",expert_variant:"Die separate Expert-Variante verhindert die Wizard-Konvertierung.",invalid_step:"Ungültige Schrittstruktur.",invalid_step_alias:"Der Schrittname muss ein String sein; der vorhandene Wert wurde nicht verändert."};
    return keys[blocker.code]?this.t(keys[blocker.code]):this.messageText(blocker.message);
  }
  conversionBlockersView(){
    if(!this.conversionBlockers?.length)return "";
    return `<div class="notice conversion-blockers" role="status"><strong>${esc(this.t("Konvertierung in Wizard nicht möglich"))}</strong><ul>${this.conversionBlockers.map((b,i)=>`<li><strong>${esc(Number.isInteger(b.step_index)?this.t("Schritt {n}",{n:b.step_index+1}):this.t("Spell"))}</strong><p>${esc(this.blockerMessage(b))}</p>${typeof b==="object"?this.button(this.t("Zum Problem"),"goto-blocker",i):""}</li>`).join("")}</ul><div class="toolbar">${this.button(this.t("Erneut prüfen"),"convert-to-wizard")}${this.button(this.t("Verstanden"),"dismiss-conversion-blockers")}</div></div>`;
  }
  clearBlockerHighlight(){
    this.activeBlocker=null;
    for(const el of this.shadowRoot.querySelectorAll(".blocker-highlight")){el.classList.remove("blocker-highlight");el.removeAttribute("aria-describedby");}
    this.shadowRoot.getElementById("blocker-inline-reason")?.remove();
  }
  showBlockerHighlight(focus=true){
    const b=this.activeBlocker;if(!b)return;
    const ids={"target.value":"step-expert-target-value","target.kind":"step-expert-target-kind",operation:"step-expert-operation",arguments:"step-expert-arguments",expert:"expert-variant-json"};
    const field=this.shadowRoot.getElementById(ids[b.path])||this.shadowRoot.getElementById(`step-card-${b.step_index}`);
    if(!field)return;
    const details=field.closest?.("details");if(details)details.open=true;
    field.classList.add("blocker-highlight");field.setAttribute("aria-describedby","blocker-inline-reason");
    if(!this.shadowRoot.getElementById("blocker-inline-reason")){
      const note=document.createElement("p");note.id="blocker-inline-reason";note.className="field-message warning";note.setAttribute("role","status");note.textContent=`⚠ ${this.blockerMessage(b)}`;
      field.insertAdjacentElement("afterend",note);
    }
    if(focus){field.scrollIntoView?.({block:"center",behavior:"auto"});field.focus?.();}
  }
  expertVariantView(){
    if(this.editorMode!=="expert"||!this.edit.expert)return "";
    return `<details class="expert-variant"><summary>${esc(this.t("Separate Expert-Variante / User-Fallback"))}</summary><p>${esc(this.t("Die Schritte bearbeiten die User-Variante. Dieser Block enthält die separate Expert-Variante; ohne Schritte gibt es keine User-Aktion."))}</p><label>${esc(this.t("Expert-Variante (JSON)"))}<textarea id="expert-variant-json" class="code">${esc(this.expertVariantText??JSON.stringify(this.edit.expert,null,2))}</textarea></label>${this.button(this.t("Separate Expert-Variante entfernen …"),"remove-expert-variant")}</details>`;
  }
  changeWizardLanguage(target){this.captureLanguageDraft();this.wizardLanguageExplicit=this.validLanguage(target.value);this.bookLanguage=this.effectiveBookLanguage(this.wizardLanguageExplicit);this.editLanguage=this.bookLanguage;this.render();}
  async readFile(file){if(file.size>1000000)throw Error(this.t("Datei zu groß (maximal 1 MB)."));this.importText=await file.text();this.importPreview=null;this.render();}
  async change(e){const t=e.target;this.clearBlockerHighlight();if(t.id==="show-sidebar"){const result=await this.api("ui_settings",{show_in_sidebar:t.checked});this.info.show_in_sidebar=result.show_in_sidebar;return;}
    if(t.name==="access-mode"){this.accessDraft.mode=t.value;this.render(true);return;}
    if(t.dataset.accessGroup){t.checked?this.accessDraft.allowed_group_ids.add(t.dataset.accessGroup):this.accessDraft.allowed_group_ids.delete(t.dataset.accessGroup);return;}
    if(t.dataset.accessUser){t.checked?this.accessDraft.allowed_user_ids.add(t.dataset.accessUser):this.accessDraft.allowed_user_ids.delete(t.dataset.accessUser);return;}
    if(t.id==="book-search"){this.bookSearch=t.value;this.render();return;}if(t.id==="show-disabled-books"){this.showDisabledBooks=t.checked;this.render();return;}if(t.id.startsWith("filter-")){this.filters[t.id.slice(7)]=t.value;this.render();}else if(t.dataset.select){t.checked?this.selected.add(t.dataset.select):this.selected.delete(t.dataset.select);this.updateBulkDeleteUi();}else if(["profile","wizard-profile"].includes(t.id)){this.profile=t.value;}else if(["source","wizard-source"].includes(t.id)){this.source=t.value;}else if(t.id==="wizard-action-domain"){this.wizardActionDomain=t.value;this.render(true);}else if(t.id==="file" && t.files[0]){await this.readFile(t.files[0]);}else if(t.id==="batch-conflict"){this.shadowRoot.querySelectorAll("[data-conflict]").forEach(s=>s.value=t.value);}else if(t.id==="import-text"){this.importText=t.value;this.importPreview=null;}
    else if(t.id==="step-action-domain"){await this.changeStepDomain(t.value);}
    else if(t.id==="step-action"){let step;try{step=this.collectStepDraft(this.editingStepIndex);}catch(error){this.showStepEditorError({field:error.field||"step-expert-arguments",message:error.message});return;}const actionRow=this.actionChoices.find(a=>a.id===t.value);if(!step||!actionRow)return;const choices=(this.edit.steps.length===1?this.targetChoices(actionRow):this.stepTargetChoices(actionRow));const targetKind=choices.some(v=>v.id===step.target_kind)?step.target_kind:choices[0]?.id||"";this.editingStepDraft={...step,action:t.value,operation:actionRow.operation,arguments:clone(actionRow.arguments||{}),target_kind:targetKind,target_value:null};delete this.editingStepDraft.color_arguments;this.render();if(targetKind==="area_id")await this.ensureAreas();else if(["entity_id","entity_ids"].includes(targetKind))await this.ensureEntitiesForAction(actionRow,[]);}
    else if(t.id==="step-target-kind"){let step;try{step=this.collectStepDraft(this.editingStepIndex);}catch(error){this.showStepEditorError({field:error.field||"step-expert-arguments",message:error.message});return;}if(!step)return;this.editingStepDraft={...step,target_kind:t.value,target_value:null};this.render();if(t.value==="area_id")await this.ensureAreas();else if(["entity_id","entity_ids"].includes(t.value))await this.ensureEntitiesForAction(this.actionChoices.find(a=>a.id===step.action)||{operation:null},[]);}
    else if(t.id==="step-expert"){
      let step;try{step=this.collectStepDraft(this.editingStepIndex);}catch(error){this.showStepEditorError({field:error.field||"step-expert-arguments",message:error.message});return;}if(!step)return;
      if(t.checked){
        this.editingStepDraft=this.expertDraftFromGuided(step);this.stepConversionIssue=null;this.render();
      }else{
        const invalid=this.validateStepCandidate(step);if(invalid){this.showStepEditorError(invalid);return;}
        const guided=this.exactGuidedStep(step);
        if(!guided){this.editingStepDraft=step;this.stepConversionIssue=this.t("Dieser technische Schritt kann nicht verlustfrei als Wizard-Aktion dargestellt werden. Wähle unten selbst eine Wizard-Aktion; vor dem Ersetzen siehst du alle Änderungen.");this.render();return;}
        this.editingStepDraft=guided;this.stepConversionIssue=null;this.render();
      }
    }
    else if(t.id==="step-expert-target-kind"){let step;try{step=this.collectStepDraft(this.editingStepIndex);}catch(error){this.showStepEditorError({field:error.field||"step-expert-arguments",message:error.message});return;}if(!step)return;this.editingStepDraft={...step,target:{kind:t.value}};this.render();}
    else if(t.id==="step-expert-operation"){try{this.editingStepDraft=this.collectStepDraft(this.editingStepIndex);}catch(error){this.showStepEditorError({field:error.field||"step-expert-arguments",message:error.message});return;}this.render();}
    else if(/^source-\d+-target-kind$/.test(t.id)){const j=Number(t.id.match(/\d+/)[0]);let step=this.collectStepDraft(this.editingStepIndex);const source=step?.sources[j];if(!source)return;step.sources[j]={...source,target_kind:t.value,expert:["area_name","entity_name"].includes(t.value),target_value:null,capture:""};this.editingStepDraft=step;this.render();if(t.value==="area_id")await this.ensureAreas();else if(t.value==="entity_id")await this.ensureEntitiesForAction({operation:null},[]);}
    else if(/^source-\d+-expert$/.test(t.id)){const j=Number(t.id.match(/\d+/)[0]),step=this.collectStepDraft(this.editingStepIndex),source=step?.sources[j];if(!source)return;step.sources[j]={...source,expert:t.checked};this.editingStepDraft=step;this.render();}
    else if(/^source-\d+-kind$/.test(t.id)){const j=Number(t.id.match(/\d+/)[0]),step=this.collectStepDraft(this.editingStepIndex),source=step?.sources[j];if(source){step.sources[j]={...source,kind:t.value};this.editingStepDraft=step;this.render();}}
    else if(t.id==="action-color-picker"){const step=this.collectStepDraft(this.editingStepIndex);if(!step)return;step.operation="light.set_color";step.arguments={rgb_color:this.hexToRgb(t.value)};delete step.argumentsText;if(!step.expert)step.color_arguments=clone(step.arguments);this.editingStepDraft=step;this.render();}
    else if(t.id==="action-color-name"){const step=this.collectStepDraft(this.editingStepIndex);if(!step)return;const name=t.value.trim();if(!name)return;step.operation="light.set_color";step.arguments={color_name:name};delete step.argumentsText;if(!step.expert)step.color_arguments=clone(step.arguments);this.editingStepDraft=step;this.render();}
  }
  download(text,name){const url=URL.createObjectURL(new Blob([text],{type:"application/yaml"}));const a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  startNewSpell(mode,explicitLanguage=null){this.editExisting=false;this.wizardLanguageExplicit=this.validLanguage(explicitLanguage);this.bookLanguage=this.effectiveBookLanguage(this.wizardLanguageExplicit);this.editLanguage=this.bookLanguage;const id=globalThis.crypto?.randomUUID?.()??`spell-${Date.now()}-${Math.random().toString(16).slice(2)}`;this.edit={id,trigger:"",steps:[this.newStepDraft("action")]};this.editorMode=mode;this.wizardStep=0;this.wizardMaxStep=0;this.wizardHelp.clear();this.wizardValidation=null;this.stepBlockers=[];this.wizardEntityFilter="";this.conversionBlockers=null;this.activeBlocker=null;this.newSpellChoice=false;this.newSpellStep=0;this.result=null;this.registry.entities=[];this.entityDomainsKey=null;this.editingStepIndex=null;this.beginEditorSession();this.render();this.ensureEditorTargets().catch(e=>this.fail(e));}
  askConfirmation(text){this.rememberModalFocus();return new Promise(resolve=>{this.pendingConfirm={text,resolve};this.render();});}
  async act(action,id){
    if(action==="confirm-yes" || action==="confirm-no"){const pending=this.pendingConfirm;this.pendingConfirm=null;const focus=this.modalReturnFocus;this.modalReturnFocus=null;this.render();queueMicrotask(()=>{if(focus?.isConnected)focus.focus?.();});pending?.resolve(action==="confirm-yes");return;}
    if(this.pendingConfirm)return;
    if(action==="insert-placeholder"){this.insertPlaceholder(id);return;}
    if(action==="expose-entity"){
      try{await this.api("expose_entity",{entity_id:id});}catch(err){this.fail(err);return;}
      this.entityDomainsKey=null;
      if(Array.isArray(this.edit?.steps)){
        const step=this.edit.steps[this.editingStepIndex];
        const actionRow=this.actionChoices.find(a=>a.id===step?.action)||{operation:null};
        await this.ensureEntitiesForAction(actionRow,[id]);
      }else{await this.ensureEditorTargets();}
      return;
    }
    if(action==="wizard-help"){this.wizardHelp.has(id)?this.wizardHelp.delete(id):this.wizardHelp.add(id);this.render(true);return;}
    this.message=null;
    if(action==="book-language"){if(this.metadataOpen){for(const field of ["name","description"])this.metadataDraft[field]=this.putText(this.metadataDraft[field],this.value(`meta-${field}`));for(const field of ["author","version","hase_min_version"])this.metadataDraft[field]=this.value(`meta-${field}`);this.metadataDraft.metadata={...this.metadataDraft.metadata,visual:{theme:this.value("meta-theme")}};}this.captureLanguageDraft();this.bookLanguage=id;this.render();for(const saved of this.sharedForm || []){const elements=saved.id?[this.shadowRoot.getElementById(saved.id)]:[...this.shadowRoot.querySelectorAll('input[name="entity"]')].filter(el=>el.value===saved.value);for(const el of elements)if(el){el.value=saved.value;el.checked=saved.checked;}}this.sharedForm=null;return;}
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
    if(action==="expert"){if(!this.expert && !await this.askConfirmation(this.t("Technische Werkzeuge zeigen Diagnose- und Freigabeoptionen. Der Editor-Modus des Spells bleibt unverändert.")))return;this.expert=!this.expert;this.render();return;}
    if(action==="open"){if(!await this.confirmDiscardEditor())return;return this.open(id);}
    if(action==="refresh")return this.refresh();
    if(action==="home"){if(!await this.confirmDiscardEditor())return;this.current=null;this.edit=null;this.editLanguage=undefined;this.result=null;this.clearEditorSession();return this.refresh();}
    if(action==="new-book"){this.creating=true;this.render();return;}
    if(action==="create-book"){await this.api("create_book",{book_id:this.value("book-id"),name:this.value("book-name"),language:this.language});this.creating=false;return this.refresh();}
    if(action==="new-spell"){if(this.edit&&this.editorDirty&&!await this.confirmDiscardEditor())return;this.newSpellChoice=true;this.newSpellStep=0;this.newSpellLanguageChoice=null;this.render();return;}
    if(action==="cancel-new-spell"){this.newSpellChoice=false;this.newSpellStep=0;this.render();return;}
    if(["new-spell-wizard","new-spell-expert"].includes(action)){this.startNewSpell(action==="new-spell-wizard"?"wizard":"expert",this.value("new-spell-language")||this.newSpellLanguageChoice);return;}
    if(action==="edit"){if(this.edit&&this.editorDirty&&id!==this.edit.id&&!await this.confirmDiscardEditor())return;this.editExisting=true;this.edit=clone(this.current.book.commands.find(c=>c.id===id));this.editLanguage=contentLanguage(this.edit.trigger,this.language);const blockers=this.current.wizard_blockers?.[id]||[];this.editorMode=blockers.length?"expert":"wizard";this.edit.steps=this.normalizeSteps(this.edit,this.current.wizard_step_flags?.[id]);this.stepBlockers=this.current.wizard_blocker_details?.[id]||[];this.wizardStep=0;this.wizardMaxStep=4;this.conversionBlockers=null;this.activeBlocker=null;this.newSpellChoice=false;this.result=null;this.registry.entities=[];this.entityDomainsKey=null;this.editingStepIndex=null;this.beginEditorSession();this.render();this.ensureEditorTargets().catch(e=>this.fail(e));return;}
    if(action==="repair-step-preset"){
      let before;try{before=this.collectStepDraft(this.editingStepIndex);}catch(error){this.showStepEditorError({field:error.field||"step-expert-arguments",message:error.message});return;}
      const invalid=this.validateStepCandidate(before);if(invalid){this.showStepEditorError(invalid);return;}
      const preset=this.actionChoices.find(row=>row.id===this.value("step-repair-action"));if(!preset){this.showStepEditorError(this.stepFieldError("step-repair-action","Bitte zuerst eine Wizard-Aktion auswählen."));return;}
      const after=this.guidedDraftFromPreset(before,preset),changes=this.presetReplacementSummary(before,after);
      const details=changes.length?`\n\n${changes.map(row=>`• ${row}`).join("\n")}`:"";
      if(!await this.askConfirmation(this.t("Diese Wizard-Aktion ersetzt die folgenden technischen Felder. Name und kompatible Zieldaten bleiben erhalten. Wirklich konvertieren?")+details))return;
      const index=this.editingStepIndex,steps=[...this.edit.steps];steps[index]=after;this.edit.steps=steps;this.editingStepDraft=clone(after);this.stepConversionIssue=null;this.stepEditorError=null;this.conversionBlockers=(this.conversionBlockers||[]).filter(row=>row.step_index!==index);if(!this.conversionBlockers.length)this.conversionBlockers=null;this.setEditorDirty(true);this.render();return;
    }
    if(action==="convert-to-expert"){if(!await this.stepsApplyDraft())return;const invalid=(this.edit.steps||[]).map((step,index)=>({step,index})).filter(({step})=>step.alias!=null&&typeof step.alias!=="string").map(({index})=>({code:"invalid_step_alias",step_index:index,path:"alias",message:`Schritt ${index+1}: Name muss ein String sein; der vorhandene Wert wurde nicht verändert.`}));if(invalid.length){this.conversionBlockers=invalid;this.render();return;}this.editorMode="expert";this.conversionBlockers=null;this.render();return;}
    if(action==="convert-to-wizard"){
      if(!await this.stepsApplyDraft())return;const validation=await this.api("validate",await this.collectSpell());
      this.clearBlockerHighlight();this.wizardValidation=validation;this.stepBlockers=validation.wizard_blocker_details||[];
      const blockers=validation?.wizard_blocker_details||validation?.wizard_blockers||[];
      if(blockers.length){this.conversionBlockers=blockers;this.render();return;}
      if(validation.editor_spell)this.edit.steps=this.normalizeSteps(validation.editor_spell,validation.wizard_step_flags);
      this.conversionBlockers=null;this.editorMode="wizard";this.render();return;
    }
    if(action==="goto-blocker"){
      const blocker=this.conversionBlockers?.[Number(id)];if(!blocker||typeof blocker!=="object")return;
      if(!await this.stepsApplyDraft())return;this.clearBlockerHighlight();this.wizardStep=1;this.wizardMaxStep=Math.max(this.wizardMaxStep||0,1);
      if(Number.isInteger(blocker.step_index)){
        const step=this.edit.steps[blocker.step_index];if(!step)return;
        this.openStepEditor(blocker.step_index);if(!this.editingStepDraft.expert)this.editingStepDraft=this.expertDraftFromGuided(this.editingStepDraft)||this.editingStepDraft;
        this.stepConversionIssue=this.blockerMessage(blocker);
      }
      this.activeBlocker=blocker;this.render();this.showBlockerHighlight();return;
    }
    if(action==="remove-expert-variant"){
      if(!await this.askConfirmation(this.t("Separate Expert-Variante entfernen? Der User-Fallback bleibt erhalten.")))return;
      this.edit.expert=null;this.setEditorDirty(true);this.conversionBlockers=null;this.clearBlockerHighlight();this.render();return;
    }
    if(action==="dismiss-conversion-blockers"){this.clearBlockerHighlight();this.conversionBlockers=null;this.stepBlockers=[];this.render();return;}
    if(action==="wizard-back"){
      if(!await this.stepsApplyDraft())return;
      this.editingStepIndex=null;this.editingStepDraft=null;this.stepValidation=null;
      this.wizardStep=Math.max(0,this.wizardStep-1);
      this.render();await this.ensureEditorTargets();return;
    }
    if(action==="wizard-next"){
      if(!await this.stepsApplyDraft())return;
      const target=Math.min(4,this.wizardStep+1);
      if(!await this.leaveStepForward(target))return;
      this.editingStepIndex=null;this.editingStepDraft=null;
      this.wizardStep=target;this.render();await this.ensureEditorTargets();
      if(target===4){this.wizardValidation=await this.api("validate",await this.collectSpell());this.render();}
      return;
    }
    if(action==="wizard-goto"){
      const target=Number.parseInt(id,10);
      if(!Number.isInteger(target)||target<0||target>(this.wizardMaxStep??0))return;
      if(!await this.stepsApplyDraft())return;
      // Backward (or staying put) is always free; only a forward jump
      // re-validates the step currently being left.
      if(target>this.wizardStep){if(!await this.leaveStepForward(target))return;}else this.stepValidation=null;
      this.editingStepIndex=null;this.editingStepDraft=null;this.wizardStep=target;this.render();await this.ensureEditorTargets();
      if(this.wizardStep===4){this.wizardValidation=await this.api("validate",await this.collectSpell());this.render();}
      return;
    }
    if(action==="wizard-validate"){if(!await this.stepsApplyDraft())return;this.wizardValidation=await this.api("validate",await this.collectSpell());this.render();return;}
    if(["add-step","edit-step","close-step","remove-step","move-step-up","move-step-down","add-source","remove-source"].includes(action)){
      if(!["edit-step","close-step"].includes(action))this.setEditorDirty(true);
      if(!await this.stepsApplyDraft())return;
      if(action==="add-step"){this.clearBlockerHighlight();this.conversionBlockers=null;this.stepBlockers=[];if((this.edit.steps||[]).length>=12)return;this.edit.steps=[...(this.edit.steps||[]),this.newStepDraft(id)];this.openStepEditor(this.edit.steps.length-1);this.render();if(id==="action")await this.ensureAreas();else await this.ensureEntitiesForAction({operation:null},[]);return;}
      if(action==="edit-step"){this.openStepEditor(Number.parseInt(id,10));this.render();await this.ensureAreas();const step=this.edit.steps[this.editingStepIndex];if(step?.kind==="action"){const actionRow=this.actionChoices.find(a=>a.id===step.action);await this.ensureEntitiesForAction(actionRow||{operation:null},Array.isArray(step.target_value)?step.target_value:[step.target_value].filter(Boolean));}else await this.ensureEntitiesForAction({operation:null},[]);return;}
      if(action==="close-step"){this.editingStepIndex=null;this.editingStepDraft=null;this.stepEditorError=null;this.render();return;}
      if(action==="remove-step"){this.clearBlockerHighlight();this.conversionBlockers=null;this.stepBlockers=[];const index=Number.parseInt(id,10);this.edit.steps=this.edit.steps.filter((_,i)=>i!==index);if(this.editingStepIndex===index){this.editingStepIndex=null;this.editingStepDraft=null;}else if(this.editingStepIndex>index)this.editingStepIndex--;this.render();return;}
      if(action==="move-step-up"||action==="move-step-down"){this.clearBlockerHighlight();this.conversionBlockers=null;this.stepBlockers=[];const index=Number.parseInt(id,10),swap=action==="move-step-up"?index-1:index+1;if(swap<0||swap>=this.edit.steps.length)return;const steps=[...this.edit.steps];[steps[index],steps[swap]]=[steps[swap],steps[index]];this.edit.steps=steps;if(this.editingStepIndex===index)this.editingStepIndex=swap;else if(this.editingStepIndex===swap)this.editingStepIndex=index;if(this.editingStepIndex!=null)this.editingStepDraft=clone(this.edit.steps[this.editingStepIndex]);this.render();return;}
      if(action==="add-source"){const step=this.editingStepDraft;if(step)step.sources=[...step.sources,this.newSourceDraft()];this.render();return;}
      if(action==="remove-source"){const step=this.editingStepDraft;if(step&&step.sources.length>1)step.sources=step.sources.filter((_,i)=>i!==Number.parseInt(id,10));this.render();return;}
    }
    if(action==="close-editor"){if(!await this.confirmDiscardEditor())return;this.edit=null;this.editLanguage=undefined;this.importing=false;this.result=null;this.clearEditorSession();this.render();return;}
    if(action==="toggle-book-details"){this.bookDetailsExpanded=!this.bookDetailsExpanded;this.render(true);return;}
    if(action==="close-result"){this.closeModal();return;}
    if(action==="permission"){const allowed=!this.current.expert_allowed;if(!await this.askConfirmation(this.t("Expert-Ausführung für „{name}“ {state}?",{name:this.bookName(this.current.book),state:this.t(allowed?this.t("einschalten"):this.t("ausschalten"))})))return;await this.api("expert_permission",{allowed});this.current.expert_allowed=allowed;this.render();return;}
    if(action==="metadata"){this.metadataDraft=clone(this.current.book);this.metadataOpen=true;this.render();return;}
    if(action==="close-metadata"){this.metadataDraft=null;this.metadataOpen=false;this.render();return;}
    if(action==="toggle-book"){await this.api("save_metadata",{metadata:{disabled:!this.current.book.disabled}});this.showDisabledBooks=true;await this.refresh();return;}
    if(action==="diagnostics"||action==="diagnostics-refresh"){const response=await this.api("diagnostics");this.executionDiagnostics=response.executions||[];this.diagnosticsOpen=true;this.render();return;}
    if(action==="diagnostics-close"){this.diagnosticsOpen=false;this.render();return;}
    if(action==="save-metadata"){const metadata={};for(const key of ["name","description","author","version","hase_min_version"])metadata[key]=this.value(`meta-${key}`);for(const field of ["name","description"])metadata[field]=this.putText((this.metadataDraft || this.current.book)[field],metadata[field]);metadata.metadata={...this.current.book.metadata,visual:{theme:this.value("meta-theme")}};await this.api("save_metadata",{metadata});this.metadataOpen=false;return this.refresh();}
    if(action==="check-all"){this.allReport=await this.api("validate_all");this.render();return;}
    if(action==="check"){const bookId=this.current?.book?.id;const report=await this.api("validate");if(this.current?.book?.id!==bookId)return;this.report=report;this.bookTab="check";this.storeBookTab(bookId,"check");this.render();return;}
    if(action==="check-mcp"){const bookId=this.current?.book?.id;const mcpReport=await this.api("check_mcp");if(this.current?.book?.id!==bookId)return;this.mcpReport=mcpReport;this.bookTab="mcp";this.storeBookTab(bookId,"mcp");if(this.current)this.current.revision=this.mcpReport.revision;this.render();return;}
    if(["export","export-selected","export-tag","export-one","yaml"].includes(action)){if(action==="export-selected"&&!this.selected.size)throw Error(this.t("Bitte zuerst Spells auswählen."));const r=await this.api("export",{ids:action==="export-selected"?[...this.selected]:["export-one","yaml"].includes(action)?[id]:undefined,collection:action==="export-tag"?this.filters.tag:undefined});if(action!=="yaml")this.download(r.yaml,`${this.current.book.id}.yaml`);this.result={...r,title:action==="yaml"?this.t("YAML-Vorschau"):this.t("Export erstellt")};this.render();return;}
    if(action==="abort-editor"){if(!this.editorDirty)return;if(!await this.askConfirmation(this.t("Alle ungespeicherten Änderungen an diesem Spell verwerfen und den zuletzt gespeicherten Stand wiederherstellen?")))return;await this.restoreEditorBaseline();return;}
    if(action==="save"){
      if(!await this.stepsApplyDraft())return;
      const payload=await this.collectSpell();
      const _eTargets=[];
      for(const _s of this.edit?.steps||[]){
        if(_s.kind==="action"){if(_s.expert){if(_s.target?.kind==="entity_id"&&_s.target.value)_eTargets.push(_s.target.value);}else if(_s.target_kind==="entity_id"&&_s.target_value)_eTargets.push(_s.target_value);}
        else for(const src of _s.sources||[]){if(src.target_kind==="entity_id"&&src.target_value)_eTargets.push(src.target_value);}
      }
      const _unexposed=_eTargets.filter(eid=>this.registry.entities.find(e=>e.id===eid)?.exposed===false);
      if(_unexposed.length&&!await this.askConfirmation(this.t("Folgendes Ziel ist nicht für Assist freigegeben: {list}. Trotzdem speichern?",{list:_unexposed.join(", ")})))return;
      const savePayload={...payload,spell_id:this.editExisting?this.edit.id:null};
      let saved=await this.api("save_spell",savePayload);
      if(saved?.requires_confirmation&&saved?.confirmation_code==="safety_ambiguity"){
        const names=saved.related_spell_names||saved.related_spell_ids||[];
        const peers=names.length?`„${names.join("“, „")}“`:this.t("einem bestehenden Spell");
        const message=this.t("Sicherheits-Mehrdeutigkeit: Dieser Spell widerspricht bei demselben Ziel {peers}. Bei einer tatsächlich mehrdeutigen Eingabe führt HASE aus Sicherheitsgründen keine Aktion aus. Trotzdem speichern?",{peers});
        if(!await this.askConfirmation(message))return;
        saved=await this.api("save_spell",{...savePayload,allow_safety_ambiguity:true});
      }
      this.clearEditorSession();
      await this.refresh();
      this.report=saved.report;
      this.render();
      return;
    }
    if(action==="book-tab"){if(!BOOK_TABS.includes(id))return;this.bookTab=id;if(this.current?.book?.id)this.storeBookTab(this.current.book.id,id);this.render(true);queueMicrotask(()=>this.shadowRoot.getElementById?.(`book-tab-${id}`)?.focus?.());return;}
    if(action==="delete-selected"){await this.deleteSelected();return;}
    if(action==="report-detail"){if(!["user","expert","schema","safety","warnings"].includes(id)||!this.report)return;this.rememberModalFocus();this.reportDetail=id;this.render();return;}
    if(action==="report-open"){this.reportDetail=null;this.modalReturnFocus=null;return this.act("edit",id);}
    if(action==="mcp-detail"){if(!["projectable","not_projectable","requires_unrestricted"].includes(id)||!this.mcpReport)return;this.rememberModalFocus();this.mcpDetail=id;this.render();return;}
    if(action==="mcp-open"){this.mcpDetail=null;this.modalReturnFocus=null;return this.act("edit",id);}
    if(action==="explain-book"){const phrase=this.testPhrase("explain-phrase");this.explainPhrase=phrase;this.rememberModalFocus();const r=await this.api("explain",{phrase});this.result={title:this.t("Matching-Erklärung"),match_explain:r.match_explain};this.render();return;}
    if(action==="explain-edit"){const phrase=this.testPhrase(),payload=await this.collectSpell();this.rememberModalFocus();const r=await this.api("explain",{...payload,phrase});this.result={title:this.t("Matching-Erklärung"),match_explain:r.match_explain};this.render();return;}
    if(action==="test-edit"){const phrase=this.testPhrase(),payload=await this.collectSpell();this.rememberModalFocus();this.result=await this.api("dry_run",{...payload,phrase});this.render();return;}
    if(action==="execute"){const phrase=this.testPhrase();const payload=await this.collectSpell();this.rememberModalFocus();const expertAllowed=this.current?.expert_allowed===true||this.current?.expert_enabled===true||this.info?.expert_allowed===true;const spell=payload?.spell||{};const expert=spell.expert;const notice=payload.mode==="steps"?`\n\n${esc(this.t("Es werden {n} Schritte ausgeführt.",{n:(spell.steps||[]).length}))}`:this.expert&&expert?(expertAllowed?`\n\n${esc(this.t("Expert-Ausführung ist aktiviert."))}\n${esc(this.t("Es wird die Expert-Version ausgeführt:"))} ${expert.operation||this.t("Unbekannte Aktion")}`:`\n\n${esc(this.t("Expert-Ausführung ist deaktiviert."))}\n${esc(this.t("Es wird die User-Version (Fallback) ausgeführt:"))} ${spell.operation||this.t("Unbekannte Aktion")}`):`\n\n${esc(this.t("Es wird die User-Version ausgeführt:"))} ${spell.operation||this.t("Unbekannte Aktion")}`;if(!await this.askConfirmation(this.t("Aktuellen Editorstand mit „{phrase}“ jetzt wirklich ausführen? Geräte können ihren Zustand ändern. Nicht gespeicherte Änderungen gelten nur für diesen Test.",{phrase})+notice))return;this.result=await this.api("execute",{...payload,phrase,confirmed:true,variant:"runtime"});this.render();return;}
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
