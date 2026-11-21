# Capability/Query/Response/Output-Erweiterung (15. September 2026)

Dieser Durchlauf erweitert das bestehende Spell-Schema additiv um mehr
Aktionen, mehrquellige Datenabfragen mit Aggregation, benannte
Response-Variablen und getrennte Ausgabemodi. Bestehende Spellbooks und die
gerade zuvor implementierte Einzelziel-Zustandsabfrage (`entity.get_state`)
bleiben unverändert lauffähig; nichts wurde migriert oder umbenannt.

## Aktionskatalog

`core/operations.py` (neu, aus `core/executor.py` herausgezogen) ist jetzt
die alleinige deklarative Quelle: Operation-ID, HA-Domain/Service,
USER/EXPERT-Stufe, erlaubte Target-Arten, Domain-Filter, Argumenttypen und
-wertebereiche, Wizard-Presets (`user_choices`) sowie ein optionaler
`capability_hint` (z. B. `rgb`, `tilt`, `hvac_modes`) für die
capability-gesteuerte Oberfläche. `core/executor.py` bleibt reine
Ausführung/Validierung und liest diesen einen Katalog, ebenso
`core/studio.py`/das Wizard-Frontend – keine zweite Registry.

Rund 50 Operationen über switch, input_boolean, cover (inkl. Position/Tilt),
climate, fan, media_player, vacuum, scene, script, automation, button,
number/input_number, select/input_select und timer. Die meisten sind
USER-Stufe (im Wizard ohne lokale Expert-Freigabe bau- und testbar) –
inklusive der vorher Expert-gebundenen `light.set_color`/
`set_color_temperature`, da das Aufgabenziel ausdrücklich Farbe/Kelvin/
Effekt als normale Fähigkeiten nennt. `lock.lock`/`unlock` bleiben bewusst
Expert-gebunden (physische Sicherheit, nicht Teil der genannten
Beispiel-Fähigkeiten). Effektnamen, Fan-Presets, Medienquellen und
Select-Optionen sind geräte-/installationsspezifisch ohne HA-weiten
Standard – Katalogeintrag vorhanden (Manual/Expert-Editor nutzbar), aber
noch kein Live-Picker im einfachen Wizard-Aktionsmenü (siehe „Offen“).

## Mehrquellige Datenabfragen

Neues, zum bestehenden `operation`-Feld alternatives Spell-Feld `sources`
(Liste). Jede Quelle: `kind` (`state`, `attribute`, `count`, `min`, `max`,
`avg`, `sum`), `target` (dasselbe `ActionTarget`-Schema wie Aktionen),
optional `domain_filter`, `device_class` (unterscheidet z. B.
Temperatur- von Feuchtigkeitssensoren im selben Bereich), `attribute`
(nur bei `kind: attribute`), `state_filter` (nur Aggregationen, z. B.
„Zustand = on“ zum Zählen eingeschalteter Lichter), `variable` und optional
`unit_variable`. `state`/`attribute` lösen auf genau eine Entity auf
(mehrdeutig oder keine Entity = kontrollierter Fehler, kein Raten);
Aggregationen lösen auf eine Menge auf.

Beispiel (das Klima-Beispiel aus der Aufgabenstellung):

```yaml
commands:
  - id: climate_report
    trigger: wie ist das klima in {area}
    sources:
      - kind: state
        target: {kind: area_name, capture: area}
        domain_filter: sensor
        device_class: temperature
        variable: temperature
        unit_variable: temperature_unit
      - kind: state
        target: {kind: area_name, capture: area}
        domain_filter: sensor
        device_class: humidity
        variable: humidity
        unit_variable: humidity_unit
      - kind: count
        target: {kind: area_name, capture: area}
        domain_filter: light
        state_filter: "on"
        variable: lights_on
    catchphrase: >
      Im {area} hat es {temperature} {temperature_unit} bei einer RLF von
      {humidity} {humidity_unit} und es sind {lights_on} Lichter an.
```

Ausführung in `core/executor.py`s `execute_query_plan`/
`_resolve_query_source`: dieselbe Zielauflösung und `AreaPolicy` wie bei
Aktionen. Ein benannter Ziel-Bereich mit unerlaubter Entity lehnt die
GESAMTE Abfrage ab (`DENIED`, wie bei Aktionen); ein fehlendes/mehrdeutiges/
leeres Einzelergebnis degradiert NUR diese eine Variable zu einem
neutralen Platzhalter (`?`) und wird als Diagnose zurückgegeben – die
Antwort wird trotzdem erzeugt, kein kaputter Satz, kein Absturz.

## Variablen und Response

`core/studio.py`s neue `spell_variables(raw)` ist der EINE Katalog aller
in Antwort/Ansage verwendbaren Variablennamen (Trigger-Captures,
`entity.get_state`s feste Feldliste, deklarierte `sources`-Variablen) –
ersetzt langfristig die im Frontend noch getrennten drei Chip-Listen
(siehe „Offen“). Variablennamen sind gegen die eigenen
Trigger-Capture-Namen reserviert (nicht gegen `entity.get_state`s
Vokabular, da beide Pfade sich gegenseitig ausschließen – „temperature“
als Quellenname ist damit ausdrücklich erlaubt).

## Ausgabemodus

Neues Spell-Feld `output_mode` (`AUTO` | `TEXT` | `SPEECH` |
`TEXT_AND_SPEECH` | `NONE`, Default `AUTO`), auf `ActionDraft`/`QueryPlan`/
`ConversationResult`. Gegen den tatsächlichen HA-Quellcode geprüft:
`intent.IntentResponse` kennt keine native Text-vs-Sprache-Unterscheidung,
nur ob überhaupt `async_set_speech` aufgerufen wurde – AUTO/TEXT/SPEECH/
TEXT_AND_SPEECH verhalten sich in `conversation.py` deshalb identisch
(heutiges Verhalten), NONE setzt eine leere Sprachantwort. CODE27
(`providers/code27/bridge.py`) erreicht HASE ausschließlich über die
generische `conversation.async_converse()`-API und sieht ebenfalls nur
diese leere-vs-echte Sprachantwort – bestätigt in `_forward_to_assist`:
eine leere Antwort bricht dort bereits vor `_output_speech()` ab, NONE
unterdrückt also TTS und Chat-Relay korrekt, ohne dass `bridge.py`
geändert werden musste. TEXT/SPEECH/TEXT_AND_SPEECH als eigene Signale
erreichen CODE27 (und jeden anderen Standard-Conversation-Client)
grundsätzlich nicht – dieselbe Grenze wie bei HAs eigenem Assist-Frontend.

## Capability-gesteuerte Aktionsliste im Wizard

Live-Test gegen eine Test-Instanz deckte auf: der Wizard bot in Schritt „Aktion“
(vor der Zielauswahl) uneingeschränkt alle Aktionen an, unabhängig vom
später gewählten Gerät – ein Licht sah z. B. „Öffnen“/„Schließen“ (Cover).
Behoben, vollständig aus Backend-Metadaten abgeleitet, kein hartkodiertes
Domain-/Aktions-Mapping in `studio.js`:

- `user_actions()` liefert jetzt zusätzlich `domain_filter` je Aktion
  (aus `OperationSpec.domain_filter`, `core/operations.py`).
- Neues Wizard-Feld „Gerätetyp“ (Schritt „Aktion“) – die Auswahlliste
  selbst kommt aus `[...neue Menge der domain_filter-Werte]`, nicht aus
  einer festen JS-Liste. Domain-lose Aktionen (`entity.get_state`, aktuell
  einzige) bleiben unabhängig vom gewählten Gerätetyp sichtbar.
- Sobald im Ziel-Schritt bereits ein konkretes Gerät ausgewählt ist (auch
  bei erneutem Zurückblättern zu „Aktion“ – alle sechs Schritte bleiben im
  DOM erhalten), werden zusätzlich `capability_hint`-gebundene Aktionen
  ausgeblendet, die dieses Gerät laut `entity_capability_flags()` nicht
  unterstützt (z. B. Farbe bei einer Lampe ohne RGB). Die aktuell gewählte
  Aktion wird dabei nie stillschweigend aus der Liste entfernt.
- Vor Geräteauswahl (kein Gerät markiert) bleiben capability-gebundene
  Aktionen sichtbar, da noch keine Aussage möglich ist.
- Regressionstests: `tests/test_studio_frontend.mjs` („Wizard action list is
  filtered by device type…“, „…hides capability-gated actions once a
  non-supporting device is already selected“).

## Bewusst nicht umgesetzt

**Multi-Step-Aktionen (Punkt 11) und Conditions (Punkt 12) – historisch, überholt.**
Multi-Step ist inzwischen vollständig gebaut, allerdings mit einem anderen
Design als unten spekuliert: `steps:` (Combined Spells, siehe
`core/models.py`s `ActionStep`/`QueryStep`/`StepPlan`, `core/executor.py`s
`execute_steps`) mit Alias-Namensraum pro Schritt statt einer flachen
`ActionSequence`-Liste; Conditions/Verzweigungen wurden bewusst nicht gebaut
(siehe Nicht-Ziel-Hinweis unten – weiterhin gültig). Der folgende
Schema-Vorschlag ist nur als historischer Kontext erhalten, nicht mehr
umzusetzen:

Architektonisch
sauber möglich (der Router führt bei SATELLITE-Backend-Vorschlägen bereits
eine Liste von `ActionDraft`s seriell mit Stop-bei-Fehlschlag aus –
dieselbe Form würde Multi-Step brauchen), aber in diesem Durchlauf aus
Zeitgründen nicht gebaut. Konkreter Schema-Vorschlag für einen späteren
Durchlauf:

- `EngineCommand`/Spell-Feld `steps: list[{operation, target, arguments}]`
  – dieselbe Form wie das heutige flache `operation`/`target`/`arguments`,
  ein Einzelschritt-Spell entspricht der heutigen flachen Form 1:1, keine
  Formatänderung für bestehende Spells nötig.
- `core/models.py`: `ActionSequence(steps: tuple[ActionDraft, ...],
  catchphrase, output_mode)`.
- `core/executor.py`: `execute_sequence()` – die serielle
  Stop-bei-Fehlschlag-Schleife aus `router.py`s `_consult_backend`
  herausgezogen und wiederverwendet (Duplikat vermeiden statt neu bauen).
- Conditions minimal: ein optionales `condition: {source: <wie
  QuerySourceSpec, kind: state|attribute>, op: eq|ne|gt|lt|available|
  unavailable, value}` plus `then`/optional `else` als je eine `steps`-Liste,
  ausgewertet über dieselbe Einzelquellen-Auflösung, die `sources` bereits
  mitbringt – kein neuer Auflösungscode nötig.
- Falls sich beim Bauen zeigt, dass mehr als dieses Minimum nötig wird
  (verschachtelte Bedingungen, Schleifen, Variablen zwischen Schritten
  weiterreichen), dort stoppen und HASE nicht zur zweiten
  Automation-Engine ausbauen – Nicht-Ziel der Aufgabenstellung.

**Live-Picker für geräte-/installationsspezifische Werte** (Lichteffekt,
Fan-Preset, Medienquelle, Select-/Input-Select-Option): Katalogeintrag und
Manual-Editor-Zugriff funktionieren bereits; ein Wizard-Picker, der die
tatsächlich am gewählten Gerät verfügbaren Werte live anbietet
(`entity_capability_flags()` liefert die Rohdaten dafür bereits: `effect`,
`preset_modes`, `source_list`, `options`), ist noch nicht gebaut.

**Vereinheitlichte Response-Chip-Oberfläche.** `spell_variables()` existiert
serverseitig und liefert bereits den vollständigen, einen Variablenkatalog;
das Frontend nutzt für Antwort-Textfelder weiterhin die drei historisch
getrennten Chip-Funktionen (`placeholderChips`/`queryResponseChips`/
`responsePlaceholderNames`, plus die neueren `stepsResponseChips`/
`singleQueryResponseChips` der Wizard-Vereinheitlichung) statt dieser einen
Quelle. Funktioniert unverändert; eine Konsolidierung auf `spell_variables()`
als einzige Quelle bleibt offen.

**Query-Builder-Oberfläche im Wizard – historisch, überholt.** Der Wizard
hat inzwischen einen geführten Query-Step-Builder (Hinzufügen/Benennen von
Quellen als Teil des vereinheitlichten Wizard, siehe
`custom_components/hase/frontend/studio.js`s `stepQuerySubForm`/
`sourceRowView`); ein 1-Schritt-Wizard-Query wird zudem beim Speichern in
die flache `sources:`-Form normalisiert (Spellbook-Studio-Vereinheitlichung,
2026). Dieser Punkt ist damit erledigt, nicht mehr offen.

## Geprüft

Vollständige Python-Regression (`.venv/Scripts/python.exe -m pytest -q`)
und Frontend-Suite (`node --experimental-default-type=module --test
tests/test_studio_frontend.mjs`) bleiben grün; neue, dedizierte Tests für
Aktionskatalog, Query-Ausführung/-Parsing, Output-Modus und die
Studio-Schema-Integration wurden ergänzt (siehe jeweilige `tests/`-Dateien).
