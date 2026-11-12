# Nächster Spellbook-Studio-Pass

Implementierter Pass vom 14. September 2026. Studio und Runtime verwenden dieselbe
Buch-Priorisierung und dieselbe Sprach-Fallback-Reihenfolge. Die Oberfläche trennt
HA-Benutzersprache und ausgewählte Buchsprache.

## Dateien, Cover und Herkunft

- Bücher bleiben eigenständige Runtime-Einheiten mit stabiler ID.
- Cover liegen neben dem jeweiligen Spellbook und haben denselben Basename:
  `potterverse.yaml` und `potterverse.png`. Kein manuelles Cover-Pathing im Studio.
- PNG vorhanden: anzeigen. PNG fehlt: automatischer Placeholder.
- Ein mitgeliefertes Buch kann geöffnet und bearbeitet werden. Sein ausgeliefertes
  Original bleibt unverändert. Beim ersten Speichern entsteht eine vollständige
  Custom-Kopie im bestehenden `hase_spellbooks`-Ordner, mit derselben Buch-ID.
- Die Auflösung erfolgt nach **Custom > Built-in**, anhand der identischen ID.
  Es gibt weder zusammengeführte Buchbestände noch partielle Sprach-Patches.

## Löschen und Zurücksetzen

- Eigene Custom-Bücher sind im Studio nach Bestätigung löschbar; YAML und
  zugehöriges PNG werden gemeinsam entfernt.
- Für Overrides mit vorhandenem Built-in bietet das Studio ausdrücklich
  **Auf Original zurücksetzen** an. Dabei werden ausschließlich Custom-YAML und
  Custom-Cover entfernt; anschließend wird das Built-in wieder aufgelöst.
- Löschen eines Overrides entspricht diesem Rückfall auf das Original.
- Kein zusätzliches dauerhaftes `disabled=true` zur Herkunftssteuerung.

## Mehrsprachige Buchinhalte

Ein Buch kann DE, EN, FR oder weitere Sprachen enthalten. Ein ausschließlich
deutsches Custom-Buch ist ebenso gültig; Mehrsprachigkeit ist optional.

Sprachabhängig sind Name/Beschreibung, Trigger/Phrasen, Beispiele und gegebenenfalls
Antworten. Action, Target, Captures und weitere technische Definitionen werden
einmal gespeichert und nicht je Sprache dupliziert.

Verbindliche Reihenfolge für fehlende Inhalte:

1. Gewünschte Sprache.
2. Englisch.
3. Im Buch definierte Default-Sprache.
4. Die einzige vorhandene Sprache, falls genau eine verfügbar ist.
5. Technischer letzter Fallback.

Bei mehreren vorhandenen Sprachen darf keine zufällige erste Sprache den
definierten Fallback ersetzen. Der technische letzte Fallback ist die stabile ID für Anzeigenamen und leerer
Inhalt für optionale Texte. Ohne auflösbaren Trigger gibt es keinen Match und
keine Geräteaktion. Technische Definitionen werden durch Sprachwechsel nicht
verändert. Bei alten einsprachigen Dateien ordnet die deklarierte Standardsprache
die einfachen Textfelder zu; ohne Sprachangabe gilt EN.

## Sprachverwaltung im Studio

- Sichtbarer Sprachumschalter wie **DE | EN | FR | + Sprache**.
- Sprachen hinzufügen und entfernen; optional eine vorhandene Sprache als
  Vorlage kopieren.
- Default-Sprache sichtbar machen und einstellen lassen.
- Validator meldet fehlende Übersetzungen/Trigger und nennt den tatsächlich
  verwendeten Fallback, etwa: „Kein deutscher Trigger vorhanden – EN-Fallback
  wird verwendet.“
- UI-Sprache aus dem HA-Benutzerprofil und bearbeitete Buchsprache sind getrennte
  Zustände. Der UI-Fallback auf EN ersetzt nicht die oben definierte Buchlogik.
- Bearbeiten eines Built-ins kopiert stets das gesamte Buch. Beispielsweise kann
  eine vollständige DE/EN-Potterverse-Kopie anschließend um FR erweitert werden.

## Abnahme für die Umsetzung

Prüfen: unveränderte Originaldateien, stabile IDs, identische Auflösung nach
Neustart, Löschung nur der zugehörigen Custom-Dateien, automatische Cover-Erkennung
und Placeholder, Bestätigungen für Löschen/Reset, Import-/Export-Roundtrips des
gesamten Buchs sowie jede Stufe der Sprach-Fallback-Kette. Client-Buchauswahl und
Expert-Freigaben bleiben pro Buch erhalten.

## YAML-Format und Bedienung

```yaml
id: example
name:
  de: Mein Zauberbuch
  en: My spellbook
default_language: de
languages: [de, en]
commands:
  - id: illuminate
    name:
      de: Lichtzauber
      en: Light spell
    trigger:
      de: Licht an
      en: Lights on
    triggers:
      de: [Erhelle den Raum]
      en: [Illuminate the room]
    examples:
      de: [Licht an]
      en: [Lights on]
    catchphrase:
      de: Licht ist an
      en: Lights are on
    operation: light.turn_on
    target:
      kind: source_area
    arguments: {}
```

Auch einfache Strings/Listen bleiben gültig; ein DE-only-Buch kann
`default_language: de` verwenden. Beim Ergänzen einer Sprache werden solche
Felder der bisherigen Standardsprache zugeordnet. Eine leere neue Sprache
verwendet Fallbacks, bis Übersetzungen eingetragen sind. Die Standardsprache ist
mit ★ markiert. Zum Entfernen der Standardsprache zuerst eine andere auswählen
und als Standard setzen. Die letzte Sprache oder der letzte erforderliche
Trigger lässt sich nicht entfernen.

Im Spell-Editor bleiben Entwürfe beim Wechsel der Buchsprache erhalten. Die
Sprachverwaltung speichert strukturelle Änderungen am ganzen Buch; bei offenen
Editorentwürfen erfolgt vorher ein Hinweis auf das Verwerfen dieser Entwürfe.
Der Validator und die aufklappbare Fallback-Anzeige nennen die tatsächlich
verwendete Sprache. Import/Export enthält das gesamte YAML einschließlich aller
Sprachen; PNG bleibt eine separate gleichnamige Datei und muss beim Transfer
neben dem YAML abgelegt werden.

Beim ersten Speichern eines Originals entsteht eine vollständige Custom-Kopie,
inklusive vorhandener PNG. Der ursprüngliche Dateibasename und die stabile ID
bleiben erhalten. Zurücksetzen entfernt diese Custom-Dateien und erhält die
buchbezogene Expert-Freigabe. Löschen eines reinen Custom-Buchs entfernt dessen
Freigabe; zugewiesene Clients benötigen anschließend ein anderes Buch.

## Prüfung

Gezielte Tests prüfen Kopie/Reset/Löschen einschließlich Originalschutz,
Nachbardateien, Bestätigung und veralteter Revision; außerdem alle Sprach-Fallbacks,
mehrsprachige Aliase/Antworten, DE-only, vollständige YAML-Roundtrips und
Sprachwechsel ohne Verlust von Entwürfen. Die lokale Browser-Fixture prüft die
neue Sprachleiste, Vorlagenkopie, UI-Wechsel DE/EN und das schmale mobile Layout.
Die Fixture führt keine echten Geräteaktionen aus.
