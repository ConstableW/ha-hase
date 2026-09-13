> Historischer Stand vor dem HASE-Rebrand. Aktuelle Pfade und Verantwortlichkeiten: [HASE-Architektur](../../HASE-ARCHITECTURE.md).

# Spellbook Engine – Etappe 1

Stand: 10. September 2026. Implementierungsvertrag v1 für die nächste Etappe; keine Änderung am Laufzeitcode. Grundlage sind der lokale Code und die im Gespräch beschlossenen Architekturkorrekturen. Das extern bearbeitete HTML wurde nicht erneut eingelesen.

## Gesicherter Ausgangspunkt

- HEAD: `c2483ad44154e34f8c8ef22c6614c29148001cb7` (Middle-Earth Spellbook).
- Arbeitsbaum war vor dieser Etappe sauber; die früheren Änderungen sind inzwischen eingecheckt.
- `baseline-c2483ad.zip` enthält die getrackten Arbeitsdateien einschließlich Tests, ohne Git-Metadaten, bytegenau geprüft (einschließlich lokaler Zeilenenden und ausgecheckter Binärdateien). Zur Wiederherstellung separat entpacken; bestehende Arbeit nicht überschreiben. SHA-256 steht in `baseline-sha256.txt`.
- `baseline-tests.txt`: Python 3.12, `python -B -m unittest discover -s tests -v`: 60 Tests, 59 erfolgreich, 1 Fehler.
- Bestehender Fehler: `test_bridge_delivery.py:184` erzeugt ein Match mit sechs Feldern; `bridge.py:582` entpackt sieben. Reale Engine liefert sieben (`spellbook.py:557`). Testfixture vor dem ersten Implementierungsvergleich um das bestehende `action`-Feld ergänzen. In Etappe 1 bewusst unverändert.
- Tests ersetzen externe HA-Imports durch Adapter. Sie belegen keine echte HA-Plattformregistrierung und kein Geräteverhalten.

## Zuständigkeiten und Ablauf

Router orchestriert SessionResolver, InteractionStateStore, Engine, Backend und Executor. Der Store ruft keine Engine auf. Engine erhält ein geladenes, validiertes Spellbook und Text/Sprache; kein HA-Import, Dateizugriff oder Gerätestatus.

EngineResult ist eine geschlossene Variante: `MATCH(action_draft, spell_id, captures, catchphrase)`, `AMBIGUOUS(candidates)` oder `NO_MATCH`. Konfigurationsfehler sind kein NO_MATCH. Kandidaten enthalten strukturierte Entwürfe, keine ausführbaren Assist-Texte.

Bei offener Rückfrage verarbeitet der Router Abbruch, eindeutigen neuen Befehl oder Antwort. Danach lokales Matching. Ein erkannter, aber abgelehnter/fehlgeschlagener/mehrdeutiger Befehl wird niemals als Backend-Fallback behandelt. Nur NO_MATCH im SATELLITE-Modus erreicht das gebundene Backend. SPELLBOOK_ONLY benötigt kein Backend und liefert einen festen, lokalisierbaren no_match_response.

## ConversationRequest v1 – vertrauenswürdige Eingangshülle

| Feld | Typ / Regel |
|---|---|
| schema_version | exakt 1 |
| request_id | pro Turn einmal erzeugte opaque ID; bei Transportwiederholung wiederverwenden |
| agent_entry_id | stabiler zuständiger spellbooker-Entry; vom Adapter ermittelt |
| text, language | nichtleerer Text und Sprachkennung; Sprache nicht auf zwei Zeichen im Contract kürzen |
| source_namespace | Adapterart plus stabile Client-/Transportinstanz; kein Anzeigename |
| source_id | stabile Quelle innerhalb des Namespace, optional; keine Nachrichten-ID, keine Berechtigung |
| source_message_id | optionale Transport-Nachrichten-ID für Deduplizierung |
| conversation_token | effektive Gesprächs-ID im Namespace; vor Store-Zugriff normalisiert |
| conversation_scope | vom Resolver aus Konfiguration erzeugt, siehe unten |
| interaction_session | strukturierter Schlüssel aus Entry, Scope, Namespace, Conversation-Token |
| policy_revision | zum Eingang erfasste Konfigurationsgeneration; Executor prüft aktuelle Gültigkeit |
| origin_context_ref | opaque Referenz auf adapterseitig gehaltenen HA Context; kein hass-Objekt im Core |
| origin_area_id | optionaler normalisierter Hinweis; erteilt keine Berechtigung |
| reply_route_ref | opaque unveränderliche Rückroute; CODE27-Slot bleibt adapterseitig |

Backend-Ausgaben dürfen keines dieser vertrauenswürdigen Felder überschreiben. HA-Adapter bewahrt den ursprünglichen Context für Service-Aufrufe. Ein direkter CODE27-Eingang hat ohne nachgewiesene Zuordnung keine HA-Benutzeridentität; niemals eine erfinden.

## ActionRequest v1

Backend und Engine liefern lediglich ActionDrafts. Router validiert Form und versieht sie mit vertrauenswürdiger Herkunft; Executor validiert Operation, Parameter, Ziel und Policy erneut.

| Feld | Typ / Regel |
|---|---|
| schema_version | exakt 1 |
| action_id | Router-ID innerhalb request_id; stabil bei Wiederholung desselben Vorschlags |
| request_id, interaction_session, agent_entry_id | aus vertrauenswürdiger Eingangshülle |
| policy_revision, origin_context_ref | intern gesetzt, nie vom Backend akzeptiert |
| operation | geschlossene semantische Operation aus registriertem Katalog |
| target | getaggte Variante `area_name`, `area_id`, `entity_name`, `entity_id`, `all_allowed` oder `none`; Zulässigkeit pro Operation |
| arguments | operationsspezifisches Schema; keine beliebigen Service-Daten |
| provenance | intern: spellbook + spell_id oder backend_instance_id + proposal_id |

Unbekannte Felder/Operationen werden abgelehnt. Keine freien Service-Namen, Templates, ausführbaren Skripte oder `resolved_command` als Hintertür. Boolean zählt nicht als numerischer Wert. Zahlen müssen endlich sein. Leere Namen und unpassende Target-Varianten sind ungültig. Action/Query wird aus dem Operationskatalog abgeleitet, nicht aus Backend-Angaben.

Executor löst Ziele zu einem internen ResolvedTarget auf, prüft die gesamte Zielmenge vor dem ersten Schreibzugriff und benutzt explizite IDs. Eine vom Backend gelieferte ID ist kein bereits autorisiertes Ziel. Aliase sind keine Identität. Mehrdeutige Namen führen zu Rückfragen; es wird kein ähnlichster Kandidat ungeprüft ausgeführt.

### Verbindlicher kleiner Katalog für Etappe 2

| Operation | Art | Targets | Argumente / Semantik |
|---|---|---|---|
| light.set_power | action | area_name/id, entity_name/id, all_allowed | `on`: bool; nur light-Entities; Bereiche meinen alle erlaubten Lichter darin |
| entity.get_state | query | entity_name/id | keine Argumente; genau eine erlaubte Entity; Ausgabe Zustand, Einheit falls vorhanden, keine beliebigen Attribute |

Noch kein universelles `homeassistant.turn_on`, kein Skript-/Szenenaufruf. Weitere Operationen aus inventory.md werden erst mit eigenem Schema und Executor-Unterstützung freigeschaltet. Ein fehlender Handler ist UNSUPPORTED, niemals Assist-Fallback.

## BackendRequest / BackendResult v1

BackendRequest enthält request_id, stabile backend_instance_id/profile_id, conversation_scope, interaction_session, Text/Sprache sowie ausschließlich freigegebenen Kontext. Gemeinsame Persona-Historie ist keine Berechtigungsquelle. Kein ungefilterter HA-ChatLog oder Registry-Abzug für Agents mit verschiedenen Scopes.

BackendResult enthält schema_version=1, request_id, optionalen Antworttext, `proposals` (0..n ActionDrafts mit innerhalb der Antwort eindeutiger proposal_id), optionales `clarification` und einen Status `reply|needs_input|error`. Freie Antwort ohne Aktionen ist gültig. `needs_input` darf in v1 keine gleichzeitig auszuführenden Vorschläge enthalten. Backend-Text bestätigt niemals eigenmächtig eine erfolgreiche Aktion.

Bei Aktionsvorschlägen erfolgen Validierung und Ausführung zuerst. Der Router kann anschließend Ergebnisse mit derselben request_id an das Backend zur Formulierung zurückgeben. Diese Abschlussantwort ist in v1 text-only: keine rekursive neue Aktionsrunde. Bei Backend-Ausfall formuliert der Router das bestätigte Ergebnis deterministisch.

V1 begrenzt Antworten auf maximal 8 Vorschläge; Schema der gesamten Liste vor Ausführung prüfen. Ausführung seriell in Listenreihenfolge, beim ersten nicht erfolgreichen Ergebnis stoppen und verbleibende Aktionen als NOT_EXECUTED markieren. Keine Transaktions-/Rollback-Zusage, Teil-Erfolge bleiben sichtbar. Bereits gestartete Aktionen bei Timeout nicht automatisch wiederholen.

## ExecutionResult / ConversationResult v1

ExecutionResult referenziert request_id/action_id und liefert `SUCCESS|QUERY_ANSWER|NEEDS_INPUT|DENIED|INVALID|UNSUPPORTED|FAILED|UNKNOWN|NOT_EXECUTED`, maschinenlesbaren reason_code, nur erlaubte Zielinformationen und typisierte Ergebnisdaten. UNKNOWN bedeutet: Ausführung könnte stattgefunden haben; keine automatische Wiederholung. SUCCESS bestätigt nur die vom Handler nachweisbare Ebene (z. B. Service angenommen, nicht physisches Licht garantiert eingeschaltet).

ConversationResult enthält request_id, effektives conversation_token, Antworttext, `completed|needs_input|rejected|error`, Liste der ExecutionResults und bei needs_input eine clarification_id. HA-Adapter übersetzt nach IntentResponse und setzt continue_conversation nur für eine echte erwartete Fortsetzung. Catchphrases ersetzen ausschließlich bestätigte Aktionserfolge, nie Query-Antworten, Teilfehler oder UNKNOWN.

## Session- und Dialogvertrag

conversation_scope ist ein strukturiertes Tupel: `(backend_kind, backend_instance_id, profile_id)`. Ohne Backend: `('local', agent_entry_id, 'default')`. Kein Anzeigename, keine aktuelle Slotnummer als globale Identität. CODE27-Profilzuordnung ist explizit `(device_entry_id, slot)` und bleibt während der Anfrage fix.

interaction_session ist `(agent_entry_id, conversation_scope, source_namespace, conversation_token)`. Als strukturierte Werte speichern oder eindeutig kodieren, nicht mit beliebigen Doppelpunkten zusammenkleben. Gleiche Persona darf bewusst Historie teilen, niemals offene Rückfragen oder Policies verschiedener Entries. Änderungen an Profil/Backend beginnen neue Sessions.

HA: effektive Session-/ChatLog-ID verwenden und zurückgeben. Bei fehlender ID neue ID normalisieren, nicht zunächst auf device_id ausweichen. Client muss die zurückgegebene ID wiederverwenden. Quellenwechsel innerhalb einer bestehenden Session nur bei expliziter vertrauenswürdiger Fortsetzungsregel; keine heuristische Zusammenführung.

CODE27: chat_id ist Nachrichten-ID, kein conversation_token. Adapter hält ein eigenes Session-Token pro stabiler Quelle/Profil; für Rückfragen bis Abschluss/Abbruch/Ablauf. Die vorhandenen Daten unterscheiden App und physisches Gerät nicht zuverlässig. Solange keine separate Transportidentität nachgewiesen ist, werden beide als derselbe slotbezogene Kanal behandelt; parallele Gespräche darin sind nicht als getrennt garantiert. Kein Geräte-Sonderfall im Core.

Store: pro Session ein serialisierter Zustandswechsel und maximal eine offene clarification_id; enthält strukturierten Entwurf, erlaubte Kandidaten-IDs, policy_revision, Sprache, unveränderliche Rückroute, created_at/last_activity (monotone Zeit). Idle-TTL 120 Sekunden wie bisher; nur konsumierte Antworten erneuern sie. Limits v1: maximal 256 offene Dialoge pro Runtime; bei Erreichen neue Rückfrage mit kontrolliertem Fehler ablehnen, keine aktive fremde Rückfrage verdrängen.

Eindeutiger neuer Befehl oder Abbruch verwirft alte Rückfrage. Antworten werden nur im passenden Session-Zustand konsumiert. Explizit mitgelieferte clarification_id muss passen; bei reinen Sprachantworten ohne ID verhindert der Session-Lock Rennen, kann aber die menschliche Absicht bei verspätetem „ja“ nicht beweisen. Deshalb Kandidatenwechsel nicht stillschweigend vornehmen. Vor Ausführung erneut Policy und Zielzustand prüfen. Relevante Optionsänderung, Unload und Profilwechsel löschen Dialoge; late Results werden über Generation/request_id verworfen. Keine Persistenz offener Aktionen über Neustarts.

Deduplizierung gehört zusätzlich zum Transport und zur Ausführung: gleicher Request/Action-Schlüssel darf innerhalb laufender Runtime nicht erneut schreiben. Keine Exactly-once-Zusage über Neustarts oder mehrdeutige Transportfehler. Backend-slotbezogene Serialisierung ist getrennt vom Session-Lock.

HA-Fortsetzungsbindung ist separater HA-Zustand. Offene Store-Rückfrage allein überspringt keine Satztrigger. Reguläre Pipeline-Turns können vor Spellbooker verarbeitet werden; continue_conversation samt gültiger ID muss vom Client übernommen werden. Live-Abnahme der installierten HA-Version bleibt erforderlich; der Dev-Quellcodebefund aus dem Audit ist keine pauschale Gerätesicherheitsgarantie.

## Policy und Entry-Lifecycle

Beide Modi gehen durch denselben Executor. Je Action-/Query-Policy: allowed_areas fehlt = Konfiguration ungültig; [] = keine Bereiche; uneingeschränkter Zugriff nur als explizites `unrestricted=true`, nicht implizit durch fehlende Werte. Bereichslose Entities werden bei eingeschränkter Policy abgelehnt. Bereichsfreie Operationen wie Uhrzeit benötigen später eine ausdrücklich erlaubte Operation. V1 respektiert zusätzlich HA-Conversation-Exposure; Policy erweitert Exposure nicht.

**Umgesetzt (Fund #11):** `core.models.ScopedAreaPolicy` trägt zwei unabhängige `AreaPolicy`-Objekte (`actions`, `queries`); `core.executor.ActionExecutor.execute` wählt anhand der Operation-Katalog-Klassifikation (`OperationSpec.kind`), welche der beiden geprüft wird - nie eine geteilte Policy für beides. Config-/Options-Flow bieten beide Bereichspaare getrennt an (`spellbooker_allowed_areas`/`spellbooker_unrestricted` für Aktionen, `spellbooker_queries_allowed_areas`/`spellbooker_queries_unrestricted` für Abfragen). Migrationssicher: fehlen die Queries-Felder komplett (Entry/Subentry vor #11), spiegelt `conversation.py` die Actions-Policy 1:1 in die Queries-Policy - nie automatisch uneingeschränkt, nie ein Crash. Die Options-/Reconfigure-Formulare füllen bei fehlenden Queries-Feldern ebenfalls mit den Actions-Werten vor, damit ein unverändertes erneutes Speichern das bisherige Verhalten exakt reproduziert statt es stillschweigend zu verengen.

`all_allowed` bedeutet alle von Operation und Policy erlaubten Ziele, nie ungeprüft entity_id=all. Explizit angeforderte Zielmengen mit verbotenen Mitgliedern werden vollständig abgelehnt, nicht stillschweigend teilweise ausgeführt. Vorschlagsnamen und Query-Kontext bereits vor Ausgabe filtern. Gruppen mit nicht überprüfbaren Mitgliedern und Skripte/Szenen mit unbekannten Folgewirkungen bleiben unsupported. Bei Saugern Reinigungbereich prüfen, nicht nur Entity-Standort; Kartenwechsel ist Teil der kontrollierten Operation.

Eigener Diskriminator `entry_kind=code27_device|spellbooker`, kein HA-internes Entry-Feld umdeuten. Fehlender Diskriminator bestehender Entries bedeutet code27_device. Agent-Unique-ID aus Entry-ID, nicht Titel. Ein spellbooker-Setup benötigt weder Host noch eigenen Coordinator-Refresh; nur die Conversation-Plattform laden. Geräte-Services akzeptieren ausschließlich Geräte-Runtimes. Unload entfernt Registrierungen, Tasks und Store-Zustand des betroffenen Entries. Backend-Reload darf lokalen Pfad nicht lahmlegen; entfernte Backend-Bindung wird als Fehler gemeldet und nie automatisch ersetzt.

**SATELLITE-Hierarchie (präzisiert 2026-09-11, Fund #18):** `spellbooker_satellite` war ursprünglich ein dritter, eigenständiger Top-Level-`entry_kind` - das erfüllte zwar "eigene benannte Conversation-Entity", zeigte aber auf `/config/integrations` unvermeidbar als eigener Hub, unabhängig von `via_device`/`via_device_id` (diese Felder wirken nur auf die Geräte-Registry-Hierarchie, nicht auf die Gruppierung dieser Seite nach Config-Entry). Ersetzt durch HA's natives `ConfigSubentry`-Mechanismus: ein SATELLITE-Agent ist jetzt ein `ConfigSubentry` vom Typ `SUBENTRY_TYPE_SATELLITE`, der auf dem Entry des jeweiligen CODE27-Geräts liegt (`config_flow.py`s `SatelliteSubentryFlow`, hinzugefügt/bearbeitet über den "+ Satellit hinzufügen"-Knopf auf genau diesem Geräte-Entry, nie über "Integration hinzufügen"). Damit entfällt auch das vormals separat gespeicherte `spellbooker_backend_entry_id`-Feld - das Backend ist immer schlicht "der Entry, auf dem dieser Subentry liegt". `conversation.py`s `async_setup_entry` erzeugt eine Entity pro SATELLITE-Subentry und registriert sie mit `config_subentry_id`, was sie unter demselben Hub wie das Gerät selbst einhängt - live gegen testzwerg mit zwei gleichzeitigen Satelliten (samt Erstellen und Rekonfigurieren über die REST-Subentry-Flow-API) nachgewiesen. `__init__.py`s Update-Listener erkennt geänderte SATELLITE-Subentries (Hinzufügen, Bearbeiten, Entfernen) an einem Snapshot und reloadet gezielt nur dafür - alle anderen Optionsänderungen (Bridge-Einstellungen etc.) bleiben reload-frei. Ein Leftover-Entry der alten `spellbooker_satellite`-Art wird beim Setup mit einer klaren `ConfigEntryError` abgelehnt statt stillschweigend zu laufen oder mit einem rohen `KeyError` zu crashen.

**Reload-Semantik (präzisiert 2026-09-10, zweiter Audit Fund #7 - Widerrufsforderung bewusst nicht umgesetzt):**

- Jeder Request übernimmt bei seiner Annahme (Eingang bei `_async_handle_message`/`SpellbookRouter.handle`) einen konsistenten Konfigurationssnapshot.
- Ein bereits angenommener Request darf unter diesem Snapshot abschließen - kein Abbruch mitten in der Ausführung, kein Nachziehen auf eine währenddessen geänderte Konfiguration.
- Ein erst NACH einem abgeschlossenen Reload angenommener Request verwendet ausschließlich die neue Konfiguration.
- Eine offene Rückfrage (`InteractionStateStore`) gilt dabei NICHT als bereits angenommene Folgeaktion - ihre Antwort ist ein eigener, neuer Request und übernimmt ihrerseits einen frischen Snapshot bei ihrer eigenen Annahme, nicht den der ursprünglichen Anfrage. (In der Implementierung bereits so: `_consume_reply` erhält die `AgentConfig` desselben `handle()`-Aufrufs, der die Antwort verarbeitet, nie eine beim `begin()` der Rückfrage eingefrorene Kopie - siehe `test_policy_is_rechecked_at_reply_time_not_cached`.)

Für einen Voice Assistant ist das die einzig nachvollziehbare Semantik: „Licht an" wurde gesagt, bevor die Rechte geändert wurden, also gehört der Befehl noch zur alten Erlaubnis - ein Nutzer würde das genauso erwarten. Damit ist dies **bewusst akzeptierte Lifecycle-Semantik, kein offener Bug** - der ursprünglich geforderte sofortige Widerrufsmechanismus wird absichtlich nicht gebaut.

Zwei Abgrenzungen bleiben davon unberührt und sind NICHT durch diese Regel entschieden:
- **Reload vs. Entfernen/Shutdown:** Dass ein Request einen Reload überleben darf, legt nicht fest, was beim Entfernen des Entries passieren soll - das ist eine eigene, noch offene Frage.
- **Physische Ausführung:** Eine bereits an HA bzw. ein Gerät übergebene Aktion lässt sich durch einen Policy-Wechsel ohnehin nicht mehr zuverlässig zurückholen, unabhängig von dieser Regel.

Die Annahmegrenze selbst ist architektonisch bereits konsistent abgebildet: jede `SpellbookerConversationEntity`-Instanz (und damit ihr `SpellbookRouter`, `InteractionStateStore`, `SessionLockRegistry`) wird bei einem Reload komplett neu aufgebaut, nie mutiert - ein noch laufender Request der alten Instanz teilt sich daher nie denselben Zustand mit der neuen. Einzige bewusste Ausnahme: `backends/code27.py`s modul-globale `_device_locks`-Registry, die geräteweite Serialisierung über Reloads hinweg garantiert - das ist ein Nebenläufigkeits-, kein Policy-Snapshot-Mechanismus und bleibt davon unberührt.

## Übergabe an Etappe 2

Inventar: 91 Trigger-Einträge in drei mitgelieferten Spellbooks, davon 7 Direktaktionen und 84 Assist-Freitexte. Vollständige Zuordnung mit Zeilenangaben in inventory.md. Benutzer-Spellbooks außerhalb dieses Repositorys sind nicht erfasst.

1. Bestehende Testfixture korrigieren und grüne Referenz herstellen.
2. Contracts/Core und typisierten kleinen Operationskatalog implementieren.
3. Entry-Verzweigung und Conversation-Plattformeinstieg hinzufügen.
4. Eine Lichtaktion und eine Statusabfrage durchgängig samt Policy nachweisen.
5. Tests: unbekannte Felder, verbotene Ziele, fehlendes Backend, fehlendes Spellbook, Fehler statt Fallback, Setup/Unload. Echte HA-Abnahme getrennt von Adaptertests.

Etappe 1 abgeschlossen als Vorbereitung. CODE27-Backend bleibt eine explizite Fähigkeitsprüfung (backend-readiness.md), kein stillschweigend zugesagtes Feature. Vollständige Spellbook-Migration folgt Etappe 4, vorhandener Gerätebetrieb bleibt bis zur kontrollierten Umstellung erhalten.
