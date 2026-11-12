🇩🇪 [Deutsch](../../de/mcp/overview.md) | 🇬🇧 English

# MCP overview

A conventional MCP server defines tools for a backend. HASE projects a controlled subset of its existing semantic model as MCP tools.

`tools/list` projects each enabled, structurally projectable Spell in the Bridge's assigned book. `tools/call` names an exact `spell_id`; it does not run fuzzy matching or conversation clarification. Arguments become the same captures used by the normal planner, and execution continues through the shared `ActionExecutor` or shared text-only path.

Structural projectability is separate from runtime policy. `duplicate_spell_id`, `conflicting_capture_definition:*`, `unknown_operation:*` and `unresolvable_text` mean no coherent tool can be produced. They are not permission denials. Bridge-specific JSON schema, descriptions and `readOnlyHint` are metadata. Authorization occurs again from server-held configuration at call time.

The MCP interface was used with hasechan and a physical CODE27 device during the Home Assistant real-system test. It supports Streamable HTTP, `tools/list` and `tools/call`. Resources, prompts, stdio, OAuth, persistent MCP conversation state and `tools/list_changed` push are currently outside its feature scope.

Continue with [Bridges](bridges.md), [area policy](area-policy.md), [protocol](protocol.md), [runtime](runtime.md), and the shared [security model](../security.md).
