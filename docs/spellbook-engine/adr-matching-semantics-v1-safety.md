# ADR: Matching Semantics v1, Safety Ambiguity und Matching-Diagnose

Status: angenommen. P5 und P6 implementiert und getestet (Stand dieses Dokuments
gegen den Code geprüft). Offene Entscheidung: Abschnitt 6. Combined Spells: siehe `docs/de/spellbooks/combined-spells.md`.

Maßgeblicher Code: `custom_components/hase/core/spellbook_engine.py`
(Kommentarblock „Matching Semantics v1“, `_find_command_impl`, `_opposite`,
`_drafts_are_opposite`), `core/matching.py`, `core/action_semantics.py`,
`core/collision_analyzer.py`, `studio.py` (Validierung/Bestätigung).
Bei Widerspruch zwischen diesem Dokument und Code/Tests gilt der Code; der
Widerspruch ist dann ein Fehler dieses Dokuments.

## 1. Matching Semantics v1 (eingefroren)

v1 dokumentiert bestehendes Verhalten, kein neues Ranking-Modell. Jede
beabsichtigte Änderung der folgenden Regeln ist eine Semantik-Änderung und darf
nicht als Nebeneffekt eines Refactors entstehen.

1. **Specificity/Tiering.** Specificity = Anzahl literaler Wörter des kompilierten
   Triggers (`literal_word_count`); Platzhalter zählen nicht. Aliase sind eigene
   Trigger mit eigener Specificity. Tiers werden absteigend ausgewertet; sobald
   eine Tier mindestens einen zulässigen Kandidaten hat, werden niedrigere Tiers
   nicht betrachtet.
2. **Full-Input-Consumption.** Ein Trigger muss die gesamte Äußerung abdecken.
   Unverbrauchte führende, mittlere oder nachgestellte Tokens verwerfen den
   Trigger-Match (`match_segments`). Platzhalter müssen mindestens ein Token
   fangen; leere Captures sind nur per `fallback_source_area` erlaubt.
3. **Fuzzy-Ranking.** Literale werden wortweise fuzzy verglichen; jedes Wort muss
   seine eigene längenabhängige Schwelle erreichen (≤ 6 Zeichen: 0,82, sonst
   0,75). Score = Mittelwert der Wort-Ratios der Literal-Segmente. Innerhalb der
   gewählten Tier wird absteigend nach Score sortiert. Es gibt keine neue
   Fuzzy-Semantik.
4. **Zulässigkeit.** Ein Trigger-Match allein genügt nicht: Action-Draft,
   Query-Plan, Step-Plan bzw. Text-Antwort müssen mit den Captures baubar sein,
   sonst wird der Kandidat verworfen. (Diese Verwerfung ist Matcher-Verhalten, ihre
   Ursache ist Draft-/Engine-Wissen – siehe Abschnitt 5.)
5. **Top-2-Ambiguity, epsilon = 0,05.** Nur die zwei bestplatzierten zulässigen
   Kandidaten der gewählten Tier nehmen teil. Ist die Score-Differenz
   `< _AMBIGUITY_SCORE_EPSILON` (0,05) *und* sind die Outcomes gegensätzlich
   (`_opposite`), ist das Ergebnis AMBIGUOUS. Ein dritter Kandidat verändert die
   Runtime-Ambiguity nicht, auch wenn er innerhalb epsilon liegt.
   Queries und Combined Spells nehmen an der Gegensätzlichkeitsregel nicht teil.
6. **Stable Order.** Greift Regel 5 nicht, gewinnt der bestplatzierte Kandidat.
   Gleich bewertete, nicht gegensätzliche Kandidaten entscheidet die stabile
   Befehls-/Alias-Reihenfolge – deterministisch, aber keine semantische Aussage.
7. **fallback_source_area.** Ein Kandidat, dessen Area-Capture nur durch
   `fallback_source_area` leer bleiben durfte, ist schwächer als jeder echte
   Capture: in derselben Tier gewinnen echte Captures; eine Tier, die nur
   Fallback-Kandidaten liefert, wird zurückgestellt und nur genutzt, wenn keine
   niedrigere Tier einen echten Capture liefert.
8. **Validierte Zerlegung (Tie-Break, Erweiterung von 3/6).** Unter den
   Kandidaten der gewählten Tier, deren Score innerhalb epsilon des besten liegt,
   gewinnt der mit mehr *validierten* Placeholdern (numerisch `type: int/float`
   oder geschlossenes Vokabular `value_map`/`true_values`/`false_values`),
   die tatsächlich gefüllt wurden. Ein allgemeiner Trigger gewinnt also nicht
   dadurch, dass sein freier Placeholder Wörter verschluckt, die ein
   spezifischerer Trigger in gültige typisierte Werte zerlegt
   (`lumos {area}` vs. `lumos {area} {color}` für „Lumos Küche rot“). Kandidaten
   mit unterschiedlicher Anzahl validierter Placeholder unterliegen keiner
   Gegensatz-Ambiguity untereinander. Ein Trigger, dessen validierte Placeholder
   nicht erfüllt werden können, matcht gar nicht (`match_segments`) und
   profitiert nie von dieser Regel. Generisch: kein Spell, Wort oder Typ ist
   fest verdrahtet. Der Collision-Analyzer meldet solche Paare als
   `resolved_overlap` (info, `validated_placeholders_select_specific_trigger`).
9. **Residual-Fallback (Recovery, kein Matching).** Regel 2 gilt unverändert:
   bei einem normalen erfolgreichen Match verschwindet nie ein Token. Ist der
   Ziel-Capture (`area_name`/`entity_name`) der *letzte* Placeholder des
   Triggers und mehrwortig, hinterlegt die Engine kürzere führende Teile als
   `ActionDraft.residual_alternatives` (längste zuerst, höchstens 3 verworfene
   Wörter). Sie nehmen nie an Matching, Ranking oder Ambiguity teil. Nur
   `ActionExecutor.execute` (der gemeinsame Einstieg für Router, Code27-Bridge
   und Studio) darf sie verwenden, und nur wenn das *vollständige* Ziel mit
   `target_not_found` scheiterte – nie nach einem aufgelösten Ziel, nie bei
   einem anderen Fehler. Jede Alternative wird nebenwirkungsfrei geprüft; genau
   eine muss eindeutig auflösen **und** die Policy erfüllen, sonst bleibt der
   ursprüngliche Fehler. Der Rest wird still verworfen (keine Rückfrage, kein
   gesprochener Hinweis); sichtbar ist er in
   `ExecutionResult.data["residual_fallback"]` (`original_capture`,
   `shortened_capture`, `discarded_residual`) und vorab im Explain
   (`selected.debug.residual_alternatives`). Die Catchphrase wird mit dem
   gekürzten Ziel gesprochen. MCP-Tool-Aufrufe sind strukturiert (kein
   gesprochener Text) und nutzen den Fallback nicht. Queries und Combined
   Spells sind noch nicht abgedeckt.

## 2. Safety-Vertrag (P5)

**ActionOutcome** (`action_semantics.ActionOutcome`): `ON`, `OFF`. Nur statisch
beweisbare Outcomes: `light.turn_on` → ON, `light.turn_off` → OFF,
`light.set_power(on=True|False)` → ON|OFF. Alles andere: kein Outcome (kein Beweis).

**TargetRelation** (`action_semantics.TargetRelation`): `SAME`, `DIFFERENT`,
`UNKNOWN`. Verschiedene Target-Kinds → DIFFERENT. Statisch: gleicher Capture-Name
→ SAME; `{area}` ↔ `{room}` → **UNKNOWN, nicht SAME**; Literal ↔ Capture → UNKNOWN;
gleiche Literale → SAME. `static_target()` kanonisiert YAML-Listen
(`entity_ids`) in dieselbe Tupel-Form wie die Runtime (`_resolve_target`).
UNKNOWN wird nie zu SAME hochgestuft.

**Ausführbare Varianten.** Ein Kandidat kann mehrere ausführbare Aktionen
haben. Die gemeinsame Projektion lebt in `action_semantics`:
`draft_action_variants(draft)` (Runtime: Draft + jeder `user_fallback`) und
`command_action_variants(command)` (statisch: User-Variante bzw. einzige Variante
eines Expert-only-Spells + Expert-Variante; Queries/Combined/Text-only: keine).
Es gibt keine Berechtigungs-Simulation: jede laut Definition ausführbare Variante
zählt.

**Safety Ambiguity** =
gleiches effektives Target + gegensätzliche kanonische Outcomes + konkurrierende
Kandidaten. Beweis: `safety_opposite_variants(variants(A), variants(B))`, d. h.
irgendein Paar aus `variants(A) × variants(B)` erfüllt
`same_target_opposite_outcomes` (= `target_relation` SAME und
`action_outcomes_are_opposite`).

**Runtime** (`_drafts_are_opposite`): erst der gemeinsame Variantenbeweis, danach
unverändert der Semantics-v1-Fallback auf den beiden Haupt-Drafts (gleiche
Operation, gleiches Target, gekipptes Bool-Argument). Gemischte typed/Text-Paare
nutzen weiter `outputs_are_opposite`. Das erweitert nur den Safety-Beweis für die
bereits bestimmten Top-2-Kandidaten – Ranking, Tiering, Fuzzy, epsilon und
Top-2 bleiben unverändert. Bei Safety Ambiguity wird **keine Aktion** ausgeführt
(Router: Rückfrage, kein Service-Call).

**Collision Analyzer** (`analyze_collisions`, advisory): nur identische
normalisierte Trigger-Formen. Verschiedene Specificity → `resolved_overlap`.
Gleiche Tier + Variantenbeweis → `safety_ambiguity`. Gleiche Tier, gleiche Form,
kein Beweis → `order_dependent`. Er nutzt exakt dieselbe Projektion und denselben
Beweis wie die Runtime.

**Invariante Analyzer ↔ Runtime:** Für jedes konkurrierende Spell-*Paar* mit
statisch beweisbarem Action-Safety-Fall gilt: Analyzer `safety_ambiguity` ⇔
Runtime AMBIGUOUS (`tests/test_safety_action_variants.py`, Contract-Test über
User-/Expert-Varianten und Target-Arten). Bei drei oder mehr gleichrangigen
Kandidaten gilt zur Runtime weiterhin Regel 5 (nur Top-2), siehe Abschnitt 6. Die Runtime kennt
aufgelöste Capture-Werte und kann darüber hinaus Fälle beweisen, die statisch
UNKNOWN sind (z. B. `{area}` ↔ `{room}` mit gleichem gefangenem Wert, Literal
„Gondor“ ↔ Capture). Der statische Beweis ist dort bewusst eine konservative
Teilmenge – die Runtime ist nie die weniger sichere Seite.

**Schweregrade:** `order_dependent` = Warnung. `safety_ambiguity` = Authoring-Error
mit Bestätigung und zugleich Runtime-Failsafe.

**Studio:** Beim Speichern eines Spells, der eine Safety Ambiguity neu erzeugt,
antwortet Studio mit `requires_confirmation` / `confirmation_code:
safety_ambiguity` („Trotzdem speichern?“ – Abbrechen / Bestätigen). Nach
expliziter Bestätigung (`allow_safety_ambiguity: true`) wird gespeichert;
`enabled` wird dabei **nicht** automatisch verändert. Eigentümer des Fehlers ist
der bearbeitete Spell; bestehende Partner werden nur referenziert.

## 3. Historische migration-inventory

`docs/spellbook-engine/migration-inventory.json` ist ein **historischer Snapshot**
der Etappe-1/8-Migration (`test_core_engine`, Legacy-Migrationstest), **nicht**
Sollzustand der aktuellen canonical Spellbooks. Aktuelle Bücher werden als
aktuelle Daten validiert (`test_unified_spellbooks.InventoryTests`). Hinweis:
`etappe-8-unified-spellbooks.md` beschreibt noch einen Vergleich „mit den neuen
YAMLs“; das ist durch diese Trennung überholt.

## 4. Diagnose-Architekturregel (P6)

> **Explain may observe matching, but must never influence matching.**

- Es gibt genau eine Matching-Implementierung (`_find_command_impl`).
  `find_command()` ruft sie ohne Trace, `explain()` mit `collect_trace=True` auf.
- Trace-Sammlung darf Ranking, Scores, Tiering, Ambiguity und Kandidatenauswahl
  nicht beeinflussen; Diagnosebedingungen stehen nur hinter `collect_trace`.
- Explain besitzt keine zweite Matching-Implementierung; Frontends rechnen keine
  Matching- oder Konfliktregeln nach.

## 5. P6 – Matching-Diagnose und Studio Explain (implementiert)

**Instrumentierung (nur Beobachtung).** `_find_command_impl(..., collect_trace)`
ist die einzige Matching-Implementierung. Mit `collect_trace=True` werden
zusätzlich erfasst: Ablehnungsgrund aus `match_segments(diagnostics=…)`,
Engine-Ursache aus `build_draft(diagnostics=…)`, Dispositionen nach der
Entscheidung, der Gegensätzlichkeitsbeweis (`_opposite_proof` → `OppositeProof`:
`action_outcomes` mit Variantenpaar, `v1_boolean_flip`, `output_text`).
`_opposite()` und `_drafts_are_opposite()` sind dünne Wrapper derselben Beweise.
Ohne Trace sind alle `diagnostics`-Parameter `None`; der Entscheidungspfad ist
identisch (Test: Ergebnis, Score, `is_exact`, Captures, Tier mit und ohne Trace
über einen Korpus aus allen canonical Spellbooks).

**Vertrag `hase.match_explain/1`** (`core/match_explain.py`, reine Projektion,
keine Matching-Regeln – per Test abgesichert):

- `decision`: `MATCH` | `AMBIGUOUS` | `NO_MATCH`
- `reason`: MATCH → `single_candidate`, `clear_winner`,
  `within_epsilon_not_opposite`, `stable_order`; AMBIGUOUS → `safety_ambiguity`;
  NO_MATCH → `no_candidate`, `no_full_match`, `no_resolvable_candidate`
- `selected`, `ambiguity` (`basis`, `spell_ids`, beweisendes Variantenpaar mit
  Outcome/Target), `via_source_area_fallback`
- `candidates[]`: `spell_id`, `trigger`, `is_alias`, `specificity`, `score`,
  `is_exact`, `captures`, `actions` (Rolle, Operation, Outcome, Target),
  `disposition` (`selected`, `ambiguous`, `outranked`, `rejected`,
  `not_evaluated`, `excluded`) und `reason`:
  outranked → `lower_score`, `stable_order`, `not_in_top_two`,
  `superseded_by_real_capture`, `fallback_deferred`;
  rejected → `no_trigger_match`, `input_not_fully_consumed`,
  `required_capture_missing`, `trigger_unavailable`, `candidate_unresolvable`;
  not_evaluated → `lower_specificity_tier`; excluded → `spell_disabled`,
  `typed_only_excluded`.
- `engine` (nur bei `candidate_unresolvable`): `layer: engine`, `stage`
  (`action_draft`, `query_plan`, `step_plan`, `response`), `reason`
  (`target_unresolved`, `argument_invalid`, `no_operation`, `draft_build_failed`,
  `query_plan_build_failed`, `step_plan_build_failed`, `response_format_error`).
  Bei `stage: step_plan` nennt `steps` den betroffenen Schritt
  (`step`, `alias`, `kind`, `reason`); `reason` ist dann `target_unresolved` oder
  `argument_invalid` (Engine-Diagnose über `build_step_plan(diagnostics=…)`,
  reine Beobachtung).

Reason-Codes sind API. `debug` (Trace-Codes, Token-/Segment-Index,
Reihenfolge-Index) und exakte Score-Werte sind **keine** API.

**Layer.** Die Verwerfung nicht baubarer Kandidaten ist Matcher-Verhalten
(Regel 4) und heißt dort nur `candidate_unresolvable`; die Ursache ist
Engine-Wissen und steht ausschließlich im getrennten `engine`-Block.
HA-Target-Auflösung, Policy und Ausführung liegen nach dem Match und sind nicht
Teil von MatchExplain.

**Studio.** WebSocket-Operation `explain` (ganzes Buch, ein ungespeicherter
Editor-Entwurf wird eingesetzt; keine HA-Aufrufe, kein Schreiben). `dry_run` /
`execute` liefern zusätzlich `match_explain` (weiterhin auch `matching_trace`).
Das Frontend rendert den Vertrag (Buchansicht „Matching erklären“, Editor-Button
„Matching erklären“, Ergebnis-Dialog) und berechnet keine Matching-Regeln.

**Konflikt-Badges.** Der Analyzer vergibt `severity` (`safety_ambiguity` →
error, `order_dependent`/`hard_collision` → warning, `resolved_overlap` → info)
und bei Safety `evidence` (beweisendes Variantenpaar). `collision_report.spells`
fasst pro Spell zusammen (`ok`, `warning`, `error`, `not_analyzed` für
deaktivierte Spells). Studio zeigt ✓ OK / ⚠ n Warnungen / ⛔ Sicherheitskonflikt
und im Editor die Konflikt-Details (Partner, Trigger, Typ, gemeinsames Ziel,
beide Aktionen/Outcomes, Grund). Speichern mit Bestätigung bleibt unverändert;
`enabled` wird nicht verändert.

## 6. Offene Entscheidung: Safety außerhalb der Top-2

Regel 5 (eingefroren) lässt nur die zwei bestplatzierten Kandidaten an der
Ambiguity teilnehmen. Beispiel: drei Spells mit identischem Trigger und Score,
in Buchreihenfolge `set_power(on)`, `turn_on`, `turn_off` auf dasselbe Target.
Runtime: Top-2 = on/on → kein Gegensatz → MATCH, das Licht wird eingeschaltet;
`turn_off` liegt innerhalb epsilon, nimmt aber nicht teil. Der paarweise Analyzer
meldet on ↔ off als `safety_ambiguity` (Authoring-Error mit Bestätigung).
Explain zeigt den Fall wahrheitsgemäß (`not_in_top_two`). Eine Änderung wäre eine
Semantics-v1-Änderung und ist bewusst nicht vorgenommen worden.
