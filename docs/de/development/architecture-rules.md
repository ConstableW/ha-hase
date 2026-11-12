🇩🇪 Deutsch | 🇬🇧 [English](../../en/development/architecture-rules.md)

# Architekturregeln

1. `core/` besitzt Spell-Semantik, Modelle, Matching, Planung, Policy und Execution-Verträge; keine Home-Assistant-/Provider-Imports.
2. Frontends besitzen nur Eingabe, Transport, Identity/Context-Wiring und Ausgabe; sie definieren Spell-Bedeutung nicht neu.
3. `core/operations.py` ist der einzige Operationskatalog; YAML akzeptiert keine beliebigen Services.
4. Reale Aktionen/Abfragen durchlaufen `ActionExecutor` mit Ziel-, Exposure-, Policy-, Argument- und Expert-Prüfungen.
5. Action und Query AreaPolicy bleiben unabhängig.
6. Text-`assist_redirect` ist explizit und getrennt von `output_mode`.
7. MCP-Projektion, Bridge-Schema, Metadaten, Autorisierung und Laufzeitdurchsetzung sind getrennte Schichten.
8. Provider-spezifisches Verhalten bleibt aus generischer Architektur heraus, außer es definiert bewusst den Adaptervertrag.
9. Geplante ADRs/historische Berichte werden ohne Implementierung und Tests nie als ausgeliefert beschrieben.
10. Ein Frontend-Provider besitzt die Schnittstelle zu seinem eigenen Gerät, Musik eingeschlossen: Namespace, Operationen, Payload-Form, Einheiten und Gerätebesonderheiten liegen in seinem Provider-Ordner. HASE Core bekommt nie eine Geräte- oder Musikplattform, und kein Provider implementiert stellvertretend einen zentralen Musikvertrag.
11. UI-Namen sind UI-Namen. HASEAMP bezeichnet die Dashboard-Card; Backend-Module, APIs und Klassen werden nach ihrer Aufgabe benannt, und eine Card enthält keine Gerätelogik. Die Provider-Grenze der Card ist in [HASEAMP (Dashboard-Card)](../cards.md) dokumentiert und durch Contract-Tests abgesichert.

Neue Adapter folgen `docs/HASE-NEW-BRIDGES.md` und den gemeinsamen Contract-Tests; das Dokument bleibt interne Implementierungsreferenz. Cards folgen stattdessen der Frontend-Provider-Grenze in [HASEAMP (Dashboard-Card)](../cards.md).
