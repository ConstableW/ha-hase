🇩🇪 Deutsch | 🇬🇧 [English](../../en/spellbooks/concepts.md)

# Spellbook-Grundlagen

Ein Spellbook ist eine YAML-Datei mit stabilen Befehls-IDs und mehrsprachigen Formulierungen. Ein Spell definiert die Bedeutung einmal; Frontends bestimmen nur Ein- und Ausgabe.

Jeder aktive Befehl besitzt einen primären `trigger`, optionale `triggers`-Aliase und genau eine Ausführungsform: `operation`, `sources`, `steps` oder reines `output`. `output` kann bei typisierten Befehlen zusätzliche Anzeigeinformation sein, ist aber nie Fallback nach einem Ausführungsfehler.

Captures wie `{area}` stammen aus Sprach-Matching oder MCP-Argumenten. `target` bestimmt die Zielauflösung, `arguments` wandelt weitere Captures in typisierte Werte. Registry und Policy werden erst bei der Ausführung geprüft.

Benutzerbücher in `<config>/hase_spellbooks/` ersetzen Built-ins mit derselben ID vollständig. Sprach-Fallback: angefordert → Englisch → Default → einzige Sprache → technischer Fallback. Technische Identifikatoren werden nie übersetzt.

Weiter: [Schema](schema.md), [Aktionen](actions.md), [Abfragen](queries.md), [Combined Spells](combined-spells.md).
