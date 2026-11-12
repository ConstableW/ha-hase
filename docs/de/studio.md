🇩🇪 Deutsch | 🇬🇧 [English](../en/studio.md)

# Spellbook Studio

Spellbook Studio ist die Authoring- und Verwaltungsoberfläche unter `/hase`, keine Autorisierungsgrenze für MCP oder Assist.

Studio bearbeitet ein stabiles Buch, validiert mit demselben Schema wie die Runtime und unterstützt mehrsprachige Felder, Import/Export, Dry Runs, optional bestätigte Live-Ausführung und lokale Expert-Freigabe je Buch. Das Bearbeiten eines Built-ins erzeugt eine vollständige Benutzerkopie in `<config>/hase_spellbooks/`; Reset entfernt nur diesen Override.

Die UI folgt der Home-Assistant-Benutzersprache. Die Buchsprache ist unabhängig. Fallback: gewünschte Sprache → Englisch → Buch-Default → einzige vorhandene Sprache → technischer Fallback.

Es gibt nur noch **einen** Wizard: ein Spell hat 0–12 Schritte (0 = reine Textausgabe, 1 = einzelne Aktion/Abfrage, 2–12 = mehrere Aktionen und Abfragen kombiniert). Die frühere Unterscheidung zwischen „Einzel-Wizard" und „Combined Wizard" ist rein historisch – beide sind heute derselbe Editor, nur mit unterschiedlicher Schrittzahl. Der Wizard deckt die häufigen, katalogisierten User-Aktionen ab; jeder einzelne Schritt kann zusätzlich auf „Erweitert (Expert)" umgeschaltet werden, um eine freie Operation, ein freies Ziel (inklusive Jinja/Template) und freie Argumente einzugeben – ohne den restlichen Spell in den Expert-Modus zu zwingen.

**Editor-Wahl beim Öffnen:** Welcher Editor (Wizard oder Expert) beim Öffnen eines Spells erscheint, ist keine gespeicherte Eigenschaft des Spells, sondern wird bei jedem Öffnen aus dem tatsächlichen Inhalt abgeleitet: Enthält der Spell mindestens eine Expert-only-Struktur (z. B. ein Jinja-Ziel, eine nicht katalogisierte Operation/Argumente oder einen separaten `expert:`-Block), öffnet er in Expert; sonst im Wizard. Über „Zu Expert wechseln" (immer möglich) bzw. „Zu Wizard wechseln" (nur möglich, wenn der gesamte Spell verlustfrei im Wizard darstellbar ist – sonst erscheint eine konkrete, schrittbezogene Fehlermeldung) kann bewusst gewechselt werden.

**Jeder Schritt validiert sich selbst:** Ein Klick auf „Weiter" validiert den verlassenen Schritt – soweit an dieser Stelle fachlich möglich – und blockiert bei Fehlern mit konkreten, feldbezogenen Meldungen; „Zurück" ist immer frei möglich. Das gilt gleichermaßen im Wizard- und im Expert-Modus (Expert bedeutet frei editierbar, nicht unvalidiert) sowie für „Übernehmen" bei einer einzelnen Aktion/Abfrage. Spätere Schritte sehen mehr: Capture-↔-Trigger-Konsistenz lässt sich erst prüfen, sobald der Trigger-Schritt existiert, Antwort-Variablen erst, sobald Aktionen/Abfragen und Trigger feststehen – ein Fehler wird also immer im frühestmöglichen Schritt erkannt, nie erst am Ende zurückgehalten.

**Letzter Schritt – Prüfen & Testen:** Der letzte Wizard-Schritt führt weiterhin eine vollständige Gesamtprüfung (inklusive buchweiter Sicherheitsprüfung) als abschließendes Sicherheitsnetz aus – was dort gefunden wird, sollte zu diesem Zeitpunkt bereits aus einem früheren Schritt bekannt sein. Beim Erreichen dieses Schritts (über „Weiter" oder per Breadcrumb-Sprung) wird „Spell prüfen" automatisch ausgeführt; das Ergebnis erscheint sofort in der Validierungszusammenfassung („X Fehler · Y Warnungen").

**Freigabe-Warnung beim Speichern:** Wenn ein Spell eine Entität anspricht, die nicht für Assist (Konversations-Domäne) freigegeben ist, erscheint beim Speichern ein Bestätigungsdialog mit den betroffenen Entitäten. Es kann trotzdem gespeichert und die Freigabe später nachgeholt werden – oder der Dialog wird abgebrochen, um die Entität zuerst freizugeben.

Die MCP-Prüfung trennt strukturelle Projektierbarkeit von Bridge-Anforderungen. `duplicate_spell_id`, `conflicting_capture_definition:*`, `unknown_operation:*` und `unresolvable_text` sind Authoring-/Projektionsprobleme, keine Berechtigungsverweigerung. Reale Aufrufe werden erneut autorisiert.

## Wizard: Todo-Spells

Der Entitätspicker des Wizards zeigt **alle** Todo-Entitäten, auch solche, die noch nicht für Assist freigegeben sind. Nicht freigegebene Entitäten werden mit einem inline-Hinweis markiert (⚠ Nicht für Assist freigegeben · Jetzt freigeben). Ein Klick auf „Jetzt freigeben" öffnet die Home-Assistant-Einstellungsseite. Nicht freigegebene Entitäten aus dem Picker herauszufiltern würde den häufigsten Einrichtungsfehler still verbergen – deshalb werden immer alle Listen angezeigt.

**`{item}`-Capture:** Für Operationen, die auf einem einzelnen Eintrag arbeiten (`todo.add_item`, `todo.update_item`, `todo.remove_item`), sollte der Trigger-Satz `{item}` als Platzhalter enthalten. Was der Nutzer an dieser Stelle sagt, wird wörtlich als `item`-Argument übergeben. Bei `todo.update_item` und `todo.remove_item` muss der Name exakt übereinstimmen – einschließlich Groß-/Kleinschreibung und Satzzeichen. Beispiel-Trigger: `ToDo {item} abschließen`.

**Freigabe-Prüfung im letzten Schritt:** Wenn die automatische Validierung im letzten Wizard-Schritt läuft, prüft das Backend, ob die Ziel-Entität freigegeben ist. Falls nicht, erscheint eine Warnung in der Validierungszusammenfassung und am Ziel-Feld: „Das Ziel `todo.inbox` ist nicht für Assist freigegeben und kann nicht aufgelöst werden." Der Wizard erlaubt weiterhin das Speichern; der Speicherdialog fragt dann, ob trotzdem fortgefahren werden soll.

Vollständige Operationsdetails, `status`-Filterung und mehrstufige Muster: [Aktionen → Todo-Listen](spellbooks/actions.md#todo-listen) und [Combined Spells → Todo-Listen](spellbooks/combined-spells.md#todo-listen).
