# Spellbook Engine – Etappe 7: vereinheitlichtes Buch-Schema

> Historischer Stand. Die Zusammenführung ist in [Etappe 8](etappe-8-unified-spellbooks.md) dokumentiert: ein kanonischer Bestand, keine getrennten Laufzeitformate.

Stand: 13. September 2026. Entwurf + erste Implementierungsstufe für "ein Buch statt zwei" (Nutzerentscheidung, siehe Session-Notizen). Diese Etappe liefert das gemeinsame Entry-Modell und den erweiterten Loader in `core/spellbook_engine.py`, **nicht** die Umstellung von `bridge.py` auf den neuen Matcher und **nicht** das physische Zusammenführen der bestehenden Buch-Dateien. Bestehender Gerätebetrieb bleibt bis zur kontrollierten Umstellung (nächste Etappe) unverändert – exakt dieselbe Zurückhaltung, die Etappe 1 für die Spellbook-Migration insgesamt festgelegt hat.

## Ausgangslage

Zwei unabhängige Ausführungsmodelle, zwei Dateibestände:

- `providers/code27/spellbook.py` (`trigger → output`-Schema, eigener Matcher): matcht Text, erzeugt am Ende **freien Text**, der an Assist weitergereicht wird. Kann alles, was Assist kann (Helligkeit, Farbe, Rollos, Schlösser, Klima …), weil Assists eigene NLU die Entity-Auflösung übernimmt.
- `core/spellbook_engine.py` (`operation/target/arguments`-Schema, eigener Matcher `core/matching.py`): matcht Text, erzeugt direkt ein typisiertes `ActionDraft`, ausgeführt über `ActionExecutor` – **ohne** Assist. Kann nur, was im `OPERATION_CATALOG` steht (`light.set_power` an/aus, `vacuum.clean_area`, `media_player.skip_track`, `entity.get_state`).

Der Grund für die Trennung ist kein Versehen: Spellbooker kann pro Instanz auf `allowed_areas` beschränkt werden (siehe Etappe 1, "free text was ruled out as a backdoor"). Freitext an Assist hätte keine Ahnung von dieser Beschränkung. Deshalb führt Spellbooker alles selbst aus, typisiert, mit hart durchgesetzter `AreaPolicy`. Diese Sicherheitsgarantie bleibt in Etappe 7 unangetastet.

Die Datei-Duplikation ist die Konsequenz: `spellbooker_books/*.yaml` ist schon heute nur eine **Teilmenge** von `providers/code27/spellbooks/*.yaml` (alles, was sich nicht in den typisierten Katalog übersetzen lässt, fehlt dort bewusst – siehe die Datei-Kommentare selbst). Beide Bestände müssen bislang manuell synchron gehalten werden.

## Entscheidung

Ein Buch pro Thema (`middle_earth.yaml`, `potter_verse.yaml`, `demo.yaml`, …), gelesen von beiden Engines. Jeder Befehlseintrag trägt optional eine typisierte Form (`operation`/`target`/`arguments`) und/oder eine Freitext-Form (`output`) – nie ineinander gewrappt, nie eines aus dem anderen simuliert.

### Schema (pro `commands`-Eintrag)

```yaml
- id: optional_stable_id          # sonst "<book_id>#<index>"
  trigger:
    de: "..."
    en: "..."                     # str oder {sprache: text}, wie bisher
  catchphrase: "..."              # optional, str oder {sprache: text}
  output:                         # optional – Freitext an Assist (nur CODE27 nutzt das)
    de: "..."
    en: "..."
  operation: light.set_power      # optional – typisiert (beide Engines könnten es nutzen)
  target: {kind: area_name, capture: area}
  arguments: {"on": true}
```

Regel: **mindestens eines von `operation` oder `output` muss vorhanden sein** – ein Eintrag mit keinem von beiden kann in keinem System je etwas bewirken und wird beim Laden verworfen (mit Warnung, wie jeder andere Ladefehler). Beides gleichzeitig ist erlaubt (z. B. eine typisierte Aktion mit einer menschenlesbaren Freitext-Alternative dokumentiert) und wird nicht abgelehnt.

### Implementiert (dieser Etappe)

In [`core/spellbook_engine.py`](../../../custom_components/hase/core/spellbook_engine.py):

- `EngineCommand.operation` ist jetzt `str | None` (vorher `str`, Pflichtfeld).
- `EngineCommand.output: str | dict[str, str] | None = None` neu.
- `_load_command`: `operation` ODER `output` reicht; beide fehlend → Ladefehler wie jedes andere ungültige Feld. Bei `output`-only-Einträgen entfällt die Target-/Argument-Validierung (es gibt kein `target_spec` zu prüfen) – das Command lädt trotzdem vollständig (Trigger kompiliert, `sort_key` gesetzt), nur ohne typisierte Form.
- `_build_draft`: gibt für `operation is None` sofort `None` zurück – ein `output`-only-Command ist für `match()` unsichtbar, kann also nie ein `EngineMatch`/`ActionDraft` erzeugen. Das ist die technische Umsetzung von "Spellbooker: ausschließlich typed, Rest still skippen" (Schritt 6 der Nutzer-Entscheidung) – ohne `match()` selbst anzufassen, ohne den Sicherheitsvertrag aus Etappe 1 zu berühren.
- `load_spellbook_from_dict`s "alle Commands ungültig → Ladefehler"-Regel greift unverändert korrekt: ein Buch aus ausschließlich `output`-only-Einträgen lädt normal (jeder Eintrag ist ja gültig geladen, nur ohne typisierte Form), wird also nicht fälschlich als kaputt behandelt.
- Sechs neue Tests in `tests/test_core_engine.py` (`OutputOnlyCommandTests`): Laden, Nicht-Matchen, Skip bei fehlenden Feldern, Buch aus nur `output`-Einträgen, ungültiges `output`-Feld, Koexistenz von `operation` und `output`.

`core/legacy_migration.py` bleibt bewusst unverändert – sein bestehender Test `test_free_text_output_is_reported_not_silently_wrapped` prüft explizit, dass Freitext-Einträge bei der Legacy-Migration nicht in ein `ActionDraft` gewrappt werden. Das ist weiterhin richtig und unabhängig von Etappe 7: dieses Modul bleibt das einmalige Konvertierungswerkzeug "alte `action:`-Kürzel → typisiert" für den bestehenden Bestand, nicht der Loader für das neue vereinheitlichte Schema. Sobald die Dateien physisch zusammengeführt sind, laden beide Engines direkt über `load_spellbook_from_path`/`load_spellbook_from_text` – `legacy_migration.py` wird dann nicht mehr gebraucht.

### Bewusst nicht in dieser Etappe

1. **`bridge.py` auf den neuen Matcher umstellen.** Heute ruft `bridge.py` `providers/code27/spellbook.py`s eigenen Matcher (`match_spellbook`, `SpellAmbiguity`, `async_get_spellbook`). Für "ein Loader" müsste das auf `core/spellbook_engine.py`s `match`/`EngineAmbiguous`/`load_spellbook_from_path` umgestellt werden – inklusive Neuschreiben der Ambiguity-Korrekturschleife, Catchphrase-Ersetzung und Vacuum-/Light-Direktausführung, die heute an die Legacy-Typen gekoppelt sind. Das ist der riskante Teil: jeder produktiv laufende Spellbook-Befehl hängt an genau dieser Logik.
2. **Physisches Zusammenführen der Dateien** unter `hase/spellbooks/` als kanonischem Ort. Migration der 86 verbleibenden Trigger-Einträge (siehe `docs/spellbook-engine/stage-1/inventory.md`) aus zwei Beständen in einen, inklusive Umwandlung der drei `action:`-Kürzel (`vacuum_clean`, `light_all_on`, `light_all_off`) in explizites `operation`/`target`/`arguments` – dafür kann `core/legacy_migration.py`s bestehende Konvertierungslogik als Vorlage dienen, müsste aber um das Durchreichen von `output` statt Verwerfen als "unsupported" erweitert werden (dann speziell für diesen einmaligen Migrationslauf, nicht als dauerhaftes Laufzeitverhalten).
3. **Entfernen der alten Pfade** (`providers/code27/spellbook.py`s Matcher, `spellbooker_books/`-Verzeichnis) – erst nach 1 und 2, mit eigener Live-Verifikation gegen ein echtes Gerät.

## Übergabe an die nächste Etappe

Reihenfolge, wie in der Nutzer-Entscheidung festgelegt: (1) `hase/spellbooks/` als kanonische Quelle einrichten, (2) *[erledigt]* gemeinsames Entry-Modell + Loader, (3) Legacy-Bücher und `spellbooker_books` darin zusammenführen, (4) `action:`-Kürzel migrieren, (5) CODE27 auf typed bei vorhandener Operation, sonst `output` (kein Fehler-Fallback) umstellen, (6) *[erledigt]* Spellbooker bleibt ausschließlich typed, Rest wird still übersprungen, (7) alte Loader/Pfade entfernen.

Schritte 1, 3, 4, 5, 7 offen – als eigene, fokussierte Session, nicht am Ende einer bereits langen.
