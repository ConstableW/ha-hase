🇩🇪 Deutsch | 🇬🇧 [English](../../en/mcp/bridges.md)

# MCP Server und Bridges

Der singleton **MCP Server** besitzt nur Listener-Infrastruktur: Bind-Adresse, Port (Standard `8815`) und `/mcp`. Er besitzt weder Spellbook, Sprache, Bereich noch Ausführungs-Policy.

Eine **MCP Bridge** repräsentiert einen externen Client/Gerätekontext. Sie besitzt Bearer-Token, Spellbook, optionalen Sprechgeräte-Bereich, Sprache sowie Action und Query AreaPolicy.

Das Token wird einmal gezeigt; später bleibt nur der Suffix sichtbar. Regeneration veröffentlicht atomar einen neuen Verifier und eine neue Bridge-Generation. Das alte Token wird ungültig, ohne den Listener oder andere Bridges neu zu starten.

Ohne Bereich ist eine Bridge bereichslos/master; benötigte Bereiche werden Pflichtargumente. Mit Bereich kann dieser als Default dienen. Ein gelöschter Bereich führt zum sicheren Fehler und Repairs-Hinweis.

Konfiguration, Credential und Lebenszyklus jeder Bridge werden durch `BridgeRuntimeSupervisor` isoliert. Eine Änderung erhöht nur die Generation dieser Bridge und ersetzt ihren unveränderlichen Snapshot; Peer-Bridges und laufender Listener bleiben unberührt. Bereits laufende Requests gehören ihrer aufgenommenen Generation. Details zu Abbruchgrenze, veralteten Antworten und separatem Listener-Neustart stehen unter [Runtime](runtime.md).
