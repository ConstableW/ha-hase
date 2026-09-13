# Etappe 8 – Ein gemeinsamer HASE-Spellbook-Bestand

## Architektur

Kanonische Bücher liegen ausschließlich unter `custom_components/hase/spellbooks/`:
`middle_earth.yaml`, `potter_verse.yaml`, `demo.yaml`. Benutzerdateien unter
`<config>/hase_spellbooks/` überschreiben dieselbe Buch-ID. Alle Frontends verwenden
`spellbook_loader.py` und `core/spellbook_engine.py`; Dateien werden bei der nächsten
Nachricht erneut eingelesen. Es gibt keine frontendabhängige Dateiauswahl und keine
Frontend-Marker im YAML.

```text
HASE spellbooks → gemeinsames Schema/Loader/Matcher
                              ├─ CODE27 → ActionDraft → ActionExecutor
                              │          oder reines output → Assist
                              └─ Satellite → nur ActionDraft → Policy → ActionExecutor
```

`trigger` und Platzhalter beschreiben die Erkennung; `catchphrase` die optionale
Antwort. `operation`, `target` und `arguments` beschreiben typisierte Semantik.
`output` ist optionaler lokalisierter Freitext. Mindestens Operation oder Output
muss vorhanden sein. Einträge ohne beides werden verworfen. Templates erlauben
nur einfache benannte Platzhalter, keine Attributzugriffe oder Format-Ausdrücke.

```yaml
id: example
name: Example
commands:
  - id: lumos
    trigger: lumos
    operation: light.set_power
    target: {kind: all_allowed}
    arguments: {"on": true}
    catchphrase: {de: Licht., en: Light.}
  - id: evening
    trigger: {de: abendstimmung, en: evening mood}
    output: {de: Aktiviere die Abendstimmung, en: Activate evening mood}
```

## Matching und Ausführung

`find_command()` liefert `CommandMatch` oder `CommandAmbiguity`. Beide enthalten
Befehle und Captures aus derselben Erkennung. Spezifität, vollständiger Verbrauch
der Äußerung, wortweise Fuzzy-Prüfung und Gegenaktions-Ambiguität bleiben erhalten.
`match()` verwendet diese Funktion mit `typed_only=True`: reine Output-Einträge
werden bereits vor der Rangfolge ausgeschlossen. Sie können keine ActionDraft
produzieren und keine typisierten Kandidaten verdrängen.

CODE27 verwendet den neutralen Match. Eine vorhandene Operation gewinnt immer.
Auch UNSUPPORTED, INVALID, DENIED oder FAILED führen niemals zur Assist-Weitergabe
des Outputs. Es gibt keinen Runtime-Fallback von Operation auf Output. Ein Frontend,
das eine Operation nicht unterstützt, meldet diese fehlende Fähigkeit selbst.

`Code27FrontendAdapter.execute(draft)` ist die gemeinsame Schnittstelle zum
ActionExecutor. Action- und Query-Policy sind im bisherigen Owner-Chat beide
unbeschränkt; Exposure, Gruppenprüfung und die anderen Resolver-Prüfungen gelten
trotzdem. Der Vacuum-Schalter bleibt eine CODE27-Einstellung, keine Eigenschaft
des Buchs. Satellite verwendet unverändert seine getrennten Bereichspolicies.

Die bestehenden Delivery-, History-, Continuation- und Catchphrase-Pfade bleiben
in der Bridge. Entity-Correction-Helfer liegen jetzt in `entity_correction.py`.
Typisierte Namenskorrekturen behalten die ActionDraft und ändern ausschließlich
das Ziel; eine bestätigte Korrektur wird erneut geprüft und typisiert ausgeführt.
Spell-Choice speichert entweder eine ActionDraft oder Output-Text. Vorhandener
Output darf als Vergleichstext zum Erkennen der Auswahl dienen, wird bei einer
typisierten Auswahl aber niemals ausgeführt. Slot-/Sprachbindung, Ablaufzeit und
Abbruch durch einen neuen expliziten Befehl bleiben bestehen.

## Inventar und bewusste Konfliktauflösung

`migration-inventory.json` enthält die aus den tatsächlichen Ausgangsdateien
extrahierten Einträge, einschließlich Lokalisierung, Catchphrases, Outputs und
typisierter Daten. Der deterministische Test vergleicht sie mit den neuen YAMLs.

| Thema | bisherige Output-/Shortcut-Einträge | bisherige typisierte Einträge | vereinigt |
|---|---:|---:|---:|
| Middle-earth | 41 | 10 | 42 |
| Potterverse | 45 | 6 | 45 |
| Demo | 0 | 10 | 10 |
| Gesamt | 86 | 26 | 97 |

15 überlappende Einträge sind zusammengeführt. Der zusätzliche Middle-earth-Befehl
ist der vorhandene `source_area`-Befehl ohne gesprochenen Bereich. Kein Trigger
oder Sprachvariant geht verloren. Synonyme mit unterschiedlichen Triggern bleiben
separate Einträge, weil das Schema pro Sprache genau einen Trigger beschreibt.
Die fünf historischen `action`-Shortcuts sind jetzt echte Operationen.

Ein echter Catchphrase-Konflikt besteht bei `light_beacons_named_area`: CODE27
sprach „Rohan wird antworten.“, das typisierte Buch „Ein Licht an dunklen Orten.“.
Der vereinheitlichte Eintrag verwendet die etablierte thematische CODE27-Antwort;
beide Ausgangswerte bleiben im Inventar dokumentiert. Eine unterschiedliche
Antwort je Frontend wäre mit einem agnostischen Buch nicht vereinbar.

Derselbe Befehl hatte als Output 100 % Helligkeit, aber als Operation nur
`light.set_power(on=true)`. Die bestehende typisierte Semantik bleibt erhalten.
Output und Übersetzungen bleiben gespeichert; die typisierte Ausführung setzt
keine Helligkeit. Dies ist eine bewusste Folge der vorgegebenen Typed-wins-Regel,
keine stillschweigende Gleichsetzung von Helligkeit und Einschalten. Eine spätere
Helligkeitsoperation benötigt eine eigene Katalog-/Executor-Erweiterung.

## all_allowed: Befund und Regression

Im vorhandenen CODE27-Adapter lagen zwei konkret nachweisbare Fehler:

1. Er übergab `AreaPolicy`, obwohl `ActionExecutor.execute()` inzwischen
   `ScopedAreaPolicy` erwartet und auf `.actions`/`.queries` zugreift. Der Fehler
   tritt vor dem Registry-Walk auf, auch bei einem vollständig korrekten Registry.
2. Der Licht-Template-Workaround benutzte `self._hass`, das der Konstruktor nicht
   setzte. Dieser Sonderpfad ist vollständig entfernt.

Der generische Adapter übergibt jetzt die korrekte ScopedAreaPolicy. Regressionen
verwenden den echten Adapter, HaTargetResolver, HaServiceCaller und ActionExecutor
mit einer kontrollierten HA-Registry: mehrere Lichter, direkt und über Geräte
zugeordnete Bereiche, an/aus und beschränkte Satellite-Ausführung. Gruppen werden
weiter ausgeschlossen, damit ihre Mitglieder keine Bereichspolicy umgehen.
Der Resolver schließt für Lichtaktionen zusätzlich versteckte und unavailable/unknown-Entities aus,
wie es der entfernte Licht-Workaround tat. Zustandsabfragen dürfen weiterhin
unavailable/unknown melden. Bei leerem `all_allowed` protokolliert
er auf Debug-Level Domain, Registry-Größe und die relevanten Filter.

Die historische Aussage „Registry liefert live null Lichter“ konnte ohne laufendes
HA nicht unabhängig reproduziert werden. Die nachgewiesenen Adapterfehler sind
behoben; eine HA-2026.9-spezifische Registry-Ursache wird nicht behauptet.
Unregistrierte Entities bleiben ausgeschlossen. Bereichslose registrierte Entities
folgen weiterhin der bestehenden Policy: bei eingeschränkten Frontends verboten,
bei explizit unbeschränkter Policy erlaubt. Die Bereichslosigkeit allein darf die
Bedeutung der vorhandenen Policy nicht umdefinieren.

## Entfernung und Benutzerdateien

Der alte CODE27-Matcher und beide getrennten Buchverzeichnisse sind entfernt.
Keine Laufzeitimporte verwenden die entfernten Typen oder Pfade. Historische
Etappenberichte und das Inventar benennen den Ausgangsstand ausdrücklich als
historisch. `legacy_migration.py` bleibt eine Offline-Konvertierungs-/Audit-Hilfe,
kein Runtime-Loader; konvertierte typisierte Einträge erhalten optionalen Output.

Eigene Bücher müssen in das gemeinsame Benutzerverzeichnis übernommen werden.
Historische `action`-Kürzel sind durch die entsprechenden Operationen zu ersetzen.
Es gibt keinen automatischen Laufzeit-Import des alten Schemas und keine getrennten
benutzerdefinierten Buchbestände je Frontend.

## Validierung

- Gemeinsame Schema-/Matching-Tests einschließlich Output-only, Typed+Output,
  unbekannter/fehlgeschlagener Operation ohne Assist-Fallback.
- CODE27 Delivery, Continuation, History-Sicherheit, Vacuum-Schalter, typisierte
  Namenskorrektur und Ambiguitätsauswahl.
- Satellite-/Spellbooker-Policy und äquivalente ActionDraft aus demselben Buch.
- Realer Adapter-/Resolver-/Executor-Pfad für all_allowed an/aus mit mehreren
  Lichtern; unerlaubtes benanntes Ziel führt zu DENIED ohne Serviceaufruf.
- Vollständiger Inventarvergleich und DE/EN-Rendering.
- Vollständige Testsuite und Ruff für geänderte Python-Dateien.
- Import-Smoke-Test mit echten Integrationsmodulen und adaptierten HA-Schnittstellen.

Live-UAT mit HA/CODE27 ist in dieser Umgebung nicht verfügbar. Vor Produktion sind
Licht an/aus, Sauger, Output-only, verbotene Satellite-Aktion und mehrstufige
Ambiguitätskorrektur am Gerät zu prüfen. Automatisierte Serviceaufruf-Akzeptanz ist
kein Nachweis physischer Gerätewirkung.


### Ergebnis im lokalen Checkout

Vollständige Suite: **304 passed, 30 subtests passed**. Ruff: **24 geänderte
Python-Dateien ohne Befund**. Fokussiert: 141 Schema-/Matching-/Migrationsprüfungen,
88 Bridge-Prüfungen (8 Subtests), 41 Satellite-/HA-Adapter-Prüfungen. Die fünf
Quell-Snapshots wurden vor Abschluss zusätzlich gegen Git HEAD verglichen;
ihre SHA-256-Werte stehen im Inventar. Die entfernten Pfade/Typen haben keine
Laufzeit- oder Testimporte mehr. Alte Namen verbleiben ausschließlich in
historischen Berichten, dem Offline-Konverter und seinen Regressionen.
