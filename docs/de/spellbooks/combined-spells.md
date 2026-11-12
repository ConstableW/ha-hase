🇩🇪 Deutsch | 🇬🇧 [English](../../en/spellbooks/combined-spells.md)

# Combined Spells

`steps` kombiniert Aktionen und Abfragen zu einem Spell. Die Schritte laufen seriell in der angegebenen Reihenfolge durch denselben Executor wie einzelne Aktionen und Abfragen – es gibt keine zweite Ausführungslogik.

```yaml
- id: night_mode
  trigger: gute nacht in {area}
  steps:
    - alias: lights
      operation: light.turn_off
      target: {kind: area_name, capture: area}
    - alias: contacts
      sources:
        - kind: count
          target: {kind: all_allowed}
          domain_filter: binary_sensor
          state_filter: "on"
          variable: open
        - kind: names
          target: {kind: all_allowed}
          domain_filter: binary_sensor
          state_filter: "on"
          variable: which
  catchphrase: "{area}: {lights.success_count} von {lights.target_count} Lichtern aus; offen: {contacts.open} ({contacts.which})."
```

## Schema

- `steps` ist eine Liste mit 1–12 Einträgen und schließt `operation` und `sources` auf derselben Spell-Ebene aus.
- Jeder Schritt ist **genau eines** von beiden:
  - **Aktion:** `operation`, `target`, optional `arguments` – dieselben Regeln wie bei einem einzelnen Aktions-Spell.
  - **Abfrage:** `sources` – dieselben Regeln und Arten wie das flache `sources:`-Feld (`state`, `attribute`, `count`, `min`, `max`, `avg`, `sum`, `names`, `states`).
- `alias` ist optional, muss ein gültiger Bezeichner sein, ist innerhalb des Spells eindeutig und darf keinen Trigger-Platzhalter überdecken.
- Combined Spells sind USER-tier. Eine `expert`-Variante zusammen mit `steps` wird abgelehnt.

## Variablen

| Quelle | wo verwendbar | Syntax |
|---|---|---|
| Trigger-Captures | Ziel und Argumente jedes Schritts, Antwort | `capture: area` bzw. `{area}` |
| Schritt-Ergebnisse (Alias) | **nur** in der Antwort (`output`/`catchphrase`) | `{alias.feld}` |

Ein Alias ist in einem späteren Schritt **nicht** als Ziel oder Argument verwendbar; ein solcher Verweis gilt als unbekannte Capture und der Spell wird nicht geladen. Die Antwort wird erst nach allen Schritten gerendert, deshalb gibt es dort keine Vorwärts-/Rückwärtsreihenfolge. Unbekannte Aliase, unbekannte Felder, `{alias}` ohne Feld und verschachtelte Punkte werden beim Laden abgelehnt.

Verfügbare Felder:

- **Aktion mit Einzelziel** (`entity_id`, `entity_name`): `success`, `state`, `entity_name`, `entity_id`, `area` sowie domänenspezifische Felder (z. B. bei Licht `brightness`).
- **Aktion mit Sammelziel** (alle anderen Zielarten): `target_count`, `success_count`, `failed_count`, `failed_names`. Echte Einzelbestätigung (erneutes Lesen des Zustands) gibt es nur für eindeutig bestimmte Endzustände (z. B. `light.turn_off`, `cover.close_cover`, `lock.lock`) und höchstens 40 Geräte; sonst gilt ehrlich die Batch-Aussage des Service-Aufrufs.
- **Abfrage:** jede `variable` bzw. `unit_variable` ihrer Quellen.

Fehlende Werte erscheinen als `?`; Listen (`names`, `states`, `failed_names`) werden mit Komma verbunden, eine leere Liste ergibt `?`.

## Fehlerverhalten

- **Policy-Verweigerung (DENIED)** eines Schritts bricht alle folgenden Schritte ab. Bereits ausgeführte Schritte werden **nicht** zurückgerollt.
- **Jeder andere Fehler** (Ziel nicht gefunden, unbekannte Operation, abgelehnter Service-Aufruf, Abfragequelle nicht verfügbar) betrifft nur diesen Schritt. Die folgenden Schritte laufen weiter, die Felder des Schritts spiegeln das Ergebnis (z. B. `target_count: 0`), das Gesamtergebnis ist `partial`.
- Kann ein Schritt mit den erkannten Captures gar nicht gebildet werden (Ziel oder Argument ungültig, z. B. Prozentwert außerhalb von `min`/`max`), gilt der Spell für diese Eingabe als nicht ausführbar (Matching-Regel „Zulässigkeit“); es wird nichts ausgeführt. Matching erklären zeigt dann `CANDIDATE_UNRESOLVABLE` mit Engine-Ursache und betroffenem Schritt.
- Transaktionen, Rollback, Bedingungen, Schleifen und parallele Schritte gibt es nicht.

## Dry-Run und Studio

- „Testen – ohne Geräteaufruf“ führt keine Aktion aus; Abfragen lesen nur Zustände. Das Ergebnis listet jeden Schritt mit Aktion, Ziel, Parametern, Geräten, Status, Grund und seinen Alias-Werten.
- Der Wizard erstellt Schritte mit festen Zielen (Bereich, Geräte, alle erlaubten, Herkunftsbereich) und Aktionen aus dem Katalog. Jeder Schritt kann zusätzlich einzeln auf „Erweitert (Expert)“ umgeschaltet werden, um eine freie Operation, ein freies Ziel (inklusive Jinja/Template, gesprochenem Bereichs-/Geräte-Platzhalter) und freie Argumente einzugeben – ohne den restlichen Spell in den Expert-Modus zu zwingen. Ein Schritt mit Expert-Inhalt bleibt normal bearbeitbar, verschiebbar und entfernbar; er verhindert lediglich die Umwandlung des gesamten Spells zurück in den reinen Wizard-Modus.
- Die Prüfung nennt den betroffenen Schritt (z. B. „Schritt 2 (…): Aktion, Ziel oder Parameter ungültig“).

## Sicherheit

Combined Spells nehmen – wie Abfragen – nicht an der Sicherheits-Mehrdeutigkeit (gegensätzliche Aktionen) teil. Das gilt für Runtime, Collision Analyzer und Matching erklären gleichermaßen; gleichrangige Combined Spells entscheidet die Reihenfolge (`order_dependent`). Jede Aktion eines Schritts durchläuft trotzdem Zielauflösung, Assist-Exposure, AreaPolicy und Katalogvalidierung.

## Todo-Listen

`todo.list_items` verhält sich anders als alle anderen Aktionsschritte: es ruft `todo.get_items` mit `return_response=True` auf und gibt den Status `QUERY_ANSWER` zurück. Der Executor wertet `QUERY_ANSWER` als Erfolg, sodass nachfolgende Schritte nicht abgebrochen werden.

Der Schritt stellt zwei Antwortfelder unter dem Alias des Schritts bereit:

| Feld | Inhalt |
|---|---|
| `{alias.items}` | Alle Eintragsbezeichnungen kommagetrennt. Eine leere Liste ergibt `?`. |
| `{alias.count}` | Gesamtanzahl der Eintraege als Ganzzahl. |

In einem flachen Spell (ohne `steps`) sind dieselben Felder als `{items}` und `{count}` im `output`-Template verfuegbar.

Das optionale Argument `status` filtert das Ergebnis, bevor die Felder befuellt werden:

```yaml
arguments:
  status: "needs_action"   # nur offene Eintraege
  # status: "completed"    # nur erledigte
  # weglassen fuer alle Eintraege
```

**Typisches Muster — Liste lesen und Inhalt ausgeben:**

```yaml
- id: inbox_anzeigen
  trigger: was steht in meiner Inbox
  steps:
    - alias: aufgaben
      operation: todo.list_items
      target: {kind: entity_id, value: todo.inbox}
      arguments: {}
  output: "Deine Inbox ({aufgaben.count} Aufgaben): {aufgaben.items}"
```

**Kombiniert mit einer Aktion — Licht an und Aufgaben vorlesen:**

```yaml
- id: guten_morgen
  trigger: guten morgen
  steps:
    - alias: licht
      operation: light.turn_on
      target: {kind: area_name, value: Schlafzimmer}
      arguments: {}
    - alias: aufgaben
      operation: todo.list_items
      target: {kind: entity_id, value: todo.inbox}
      arguments:
        status: "needs_action"
  catchphrase: "Guten Morgen! {licht.success_count} Licht(er) an. Offene Aufgaben: {aufgaben.items} ({aufgaben.count} gesamt)."
```

**Entitaets-Freigabe:** Jede Todo-Entitaet muss einzeln fuer die Konversations-Domaene freigegeben sein. Der Wizard warnt im letzten Schritt (Pruefen & Testen), wenn die Ziel-Entitaet nicht freigegeben ist; die Buchpruefung zeigt das ebenfalls als Hinweis mit Korrekturempfehlung.

**Fehlerverhalten:** Schlaegt `todo.list_items` fehl (Entitaet nicht verfuegbar, Service-Fehler), erscheinen `{alias.items}` und `{alias.count}` als `?` und das Gesamtergebnis ist `partial`. Spaetere Schritte laufen weiter.
