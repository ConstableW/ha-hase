🇩🇪 Deutsch | 🇬🇧 [English](../../en/mcp/overview.md)

# MCP-Überblick

Ein herkömmlicher MCP-Server definiert Tools für ein Backend. HASE projiziert einen kontrollierten Teil seines vorhandenen semantischen Modells als MCP-Tools.

`tools/list` projiziert jeden aktiven, strukturell projektierbaren Spell des zugewiesenen Buchs. `tools/call` nennt eine exakte `spell_id`; es gibt kein Fuzzy Matching oder Conversation-Clarification. Argumente werden zu denselben Captures wie im normalen Planer, danach folgt der gemeinsame Executor/Textpfad.

Strukturelle Projektierbarkeit ist getrennt von Runtime-Policy. `duplicate_spell_id`, `conflicting_capture_definition:*`, `unknown_operation:*` und `unresolvable_text` bedeuten, dass kein kohärentes Tool gebaut werden kann; sie sind keine Berechtigungsverweigerung. Bridge-Schema, Beschreibung und `readOnlyHint` sind Metadaten. Autorisierung erfolgt bei jedem Aufruf erneut.

Die MCP-Schnittstelle wurde im Home-Assistant-Realtest mit hasechan und einem physischen CODE27 genutzt. Sie unterstützt Streamable HTTP, `tools/list` und `tools/call`; Resources, Prompts, stdio, OAuth, dauerhafte MCP-Conversation-Sessions und Push für `tools/list_changed` gehören derzeit nicht zum Funktionsumfang.

Weiter: [Bridges](bridges.md), [Bereichs-Policy](area-policy.md), [Protokoll](protocol.md), [Runtime](runtime.md), [Sicherheit](../security.md).
