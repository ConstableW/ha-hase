# Spellbook Studio – Audit und Architektur

## Lokaler Ausgangsstand (13.09.2026)

Die 97 Befehle in `custom_components/hase/spellbooks/{demo,middle_earth,potter_verse}.yaml`
verwenden bereits ein gemeinsames Schema und denselben Matcher. Der bestehende
Loader entdeckt Bücher nach ID mit Benutzerdatei-Priorität. Diese gewünschte
Multi-Book-Architektur bleibt erhalten. Studio verwaltet jedes Buch separat;
keine Zusammenführung, keine Umdeutung von Book-IDs zu Collections.

`core/spellbook_engine.py` enthält Parser, Capture-Auflösung und Ranking.
`core/executor.py` enthält vier Operationen (light.set_power, entity.get_state,
vacuum.clean_area, media_player.skip_track), Argumentprüfung und getrennte
Action-/Query-AreaPolicies. `spellbooker_ha.py` prüft Registry, Exposure, Gruppen,
Domains und Bereiche. Vacuum verwendet einen eigenen bestehenden Discovery-Pfad.
Conversation und CODE27 verwenden diese gemeinsamen Komponenten, haben aber
unterschiedliche lokale Bereichsfreigaben. Diese bleiben erhalten.

Config-/Options-Flows wählen bisher Bücher. Es gibt keine Studio-API, kein
integriertes Editor-Panel, keinen Import/Merge/Export und keine Expert-Freigabe.
Das bestehende www-Verzeichnis gehört nicht zur Studio-Auslieferung. Der
Offline-Legacy-Konverter ist keine Runtime und wird nicht entfernt.
Ausgangsprüfung: 304 Tests und 30 Subtests erfolgreich.

## Implementierungsplan

1. Metadaten, mehrere Trigger, User-/Expert-Varianten und Klassifikation im
   bestehenden Schema ergänzen. User-Fallbacks beim Bearbeiten schützen.
2. Expert-Ausführung lokal PRO BOOK-ID speichern und im Executor erzwingen. Templates und
   Entity-Listen über denselben HA-Resolver prüfen. Dry Run durch denselben
   Executor ohne Serviceaufruf, einschließlich Policy-Ergebnis.
3. Bestehenden Loader mit separaten Dateien weiterverwenden. Studio schreibt je
   Buch atomar in das vorhandene Benutzerverzeichnis `hase_spellbooks`;
   mitgelieferte Originaldateien bleiben erhalten. Revisionskontrolle verhindert
   verlorene Änderungen. Import/Merge, Export und Klassifikation gelten pro Buch.
   `.storage/hase.studio_permissions` enthält ausschließlich lokale Freigaben je
   Book-ID. Diese Berechtigungen sind niemals Bestandteil eines Imports/Exports.
4. Admin-geschützte HA-WebSocket-API und mitgeliefertes Web-Component-Panel.
   Freundliche Auswahl im User Mode, technische Bearbeitung bewusst im Expert Mode.
5. Regressionen, Berechtigungs-, Import-/Export- und Dry-Run-Tests; vollständige Suite.

Die Buchauswahl pro Client sowie die getrennten Runtime-Bücher bleiben bestehen.
Gemeinsam sind Studio, Checker, Tester und die vorhandene Engine-Infrastruktur.

## Verifizierte Home-Assistant-Schnittstellen

- https://developers.home-assistant.io/blog/2024/06/18/async_register_static_paths/
- https://developers.home-assistant.io/docs/frontend/extending/websocket-api/
- https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/panel_custom/__init__.py

Assets werden mit `async_register_static_paths` ausgeliefert, das Panel mit
`panel_custom.async_register_panel` registriert. HA authentifiziert die vorhandene
WebSocket-Verbindung; im Browser werden keine Tokens gespeichert.

## HASE-Oberfläche und Einstieg

Das Studio verwendet `frontend/studio.css` und `studio-visuals.js` für die gemeinsame
Gestaltung von Übersicht, Buchansicht und Editor. Die Oberfläche bleibt ein
Web Component mit derselben WebSocket-API. Die offiziellen Branding-Dateien in
`docs/branding` sind die Quelle; das bereits vorhandene, bytegleiche
`custom_components/hase/brand/icon.png` wird über `/hase-studio-brand/icon.png`
ausgeliefert. Es gibt keine neue oder nachgezeichnete HASE-Logo-Datei.

Cover werden automatisch aus der PNG-Datei neben dem Spellbook geladen:
`potterverse.yaml` → `potterverse.png` (auch für `.yml`). Manuelle Cover-Pfade
entfallen; alte `metadata.visual.cover`-Angaben werden ignoriert. Ohne PNG bleibt
das gestaltete Buchmotiv sichtbar. Der optionale Stil `metadata.visual.theme`
(sapphire, emerald, ember, amethyst) bleibt einstellbar. Die PNG-Datei wird über
die authentifizierte Studio-Verbindung geladen; es wird kein öffentlicher
Dateizugriff auf den Custom-Ordner eingerichtet.

Unter **Studio-Einstellungen → Show Spellbook Studio in sidebar** lässt sich
der Seitenleisteneintrag einschalten. Standard: **aus**. Die Einstellung wird
global in `.storage/hase.studio_ui` gespeichert, getrennt von den Expert-Freigaben.
Solange HASE geladen ist, bleibt `/hase` auch bei ausgeschaltetem Eintrag als
admin-geschützte Route registriert. Änderungen aktualisieren das bestehende Panel.

`assist-entry.js` ergänzt bei HASE-Konfigurationen in der zentralen
Aktionsleiste nach „Hub hinzufügen“ und „Satellite hinzufügen“ einen Link **Spellbook Studio** mit dem offiziellen Logo.
Die Konfigurations- und Satellite-Aktionen bleiben erhalten. Die Erweiterung
wird unabhängig von einem vorherigen Studio-Besuch geladen und beobachtet nur
die Konfigurationsseiten für Integrationen und Sprachassistenten. Andere
Integrationen werden nicht ergänzt.

Für diesen zusätzlichen nativen Link gibt es keinen öffentlichen HA-Slot.
Die kleine Ergänzung richtet sich daher nach `ha-config-integration-page` und dessen
zentralem `.actions`-Container im geprüften HA-Frontend. Ändert HA diese DOM-Struktur,
kann der Link fehlen; `/hase` und die Studio-Option bleiben davon unabhängig.
Die Referenz ist
https://github.com/home-assistant/frontend/blob/dev/src/panels/config/integrations/ha-config-integration-page.ts.
Eine Prüfung in einer laufenden HA-Installation steht noch aus.

## Lokale Prüfung

Python-Regressionen: `.venv/Scripts/python.exe -m pytest -q`.
Frontend-Tests (ohne zusätzliche Pakete):
`node --experimental-default-type=module --test tests/test_studio_frontend.mjs`.
Die Tests prüfen unter anderem Buch-/Client-Kontext, sichtbare Aktionen,
Expert-Mode ohne Freigabeänderung, sichere Cover-Pfade, Logo-Identität,
Sidebar-Registrierung und den zusätzlichen Assist-Link.

`tests/studio-preview.html` ist eine lokale UI-Testseite mit Beispieldaten,
keine alternative Runtime. Desktop und mobile Breiten werden damit visuell
geprüft; echte HA-Geräteaktionen und Safari/iOS sind dadurch nicht getestet.


## UI-Sprache (Korrektur vom 14. September 2026)

Das Studio folgt `hass.locale.language` aus dem HA-Benutzerprofil, alternativ
`hass.language`. DE und EN werden unterstützt; regionale Kennungen werden auf
ihre Basissprache zurückgeführt. Fehlende Sprachen und einzelne fehlende
Übersetzungen verwenden Englisch. Sprachkataloge liegen in
`frontend/studio-i18n.js`; neue Sprachen können dort ergänzt werden.

Ein Sprachwechsel aktualisiert das geöffnete Studio ohne neue Buchabfrage.
Ungespeicherte Formularwerte bleiben erhalten. Eigene Buchnamen, Inhalte sowie
Geräte- und Bereichsnamen werden nicht als UI-Texte übersetzt. Bei vorhandenen
mehrsprachigen Triggern bleibt die bearbeitete Sprachfassung beim Speichern
explizit erhalten; andere Fassungen werden nicht überschrieben.

Der Studio-Einstieg wird einmal pro HASE-Integrationsseite in der oberen
Aktionsleiste ergänzt, auch wenn kein CODE27-Hub konfiguriert ist. Er erscheint
nicht mehr pro Hub. Die lokalen Browser-Fixtures in `tests/serve_studio_preview.py`
prüfen diesen Fall und die Wiederherstellung nach einem erneuten Rendern.
Nach Installation dieses Updates HA neu starten
und die Browserseite neu laden; die aktualisierte Asset-Kennung verhindert die
Weiterverwendung des alten Studio-Einstiegs.

Die umgesetzten Regeln für Cover, Custom-Overrides und Buch-Sprachverwaltung
sowie das mehrsprachige YAML-Format stehen in [SPELLBOOK-STUDIO-NEXT-PASS.md](SPELLBOOK-STUDIO-NEXT-PASS.md).
Der erweiterte Aktionskatalog, mehrquellige Datenabfragen mit Aggregation,
Response-Variablen und Ausgabemodi (AUTO/TEXT/SPEECH/TEXT_AND_SPEECH/NONE)
stehen in [SPELLBOOK-STUDIO-CAPABILITY-EXPANSION.md](SPELLBOOK-STUDIO-CAPABILITY-EXPANSION.md).

Bei Editor-Tests mit aktivierter Expert-Freigabe des Buchs wird der aktuelle
Expert-Entwurf verwendet, unabhängig vom Speicher-Haken. Ein Fehler dieser
Expert-Version wird direkt angezeigt; der Studio-Editor ersetzt sie in diesem
Fall nicht durch die User-Aktion. Die explizite User-Vorschau bleibt verfügbar.
„Testen“ bleibt ohne Geräteaufruf; „Jetzt wirklich ausführen“ benötigt Bestätigung.
Die normale Conversation-Runtime behält ihre bestehende Fallback-Regel.
