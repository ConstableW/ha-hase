🇩🇪 [Deutsch](../../de/mcp/bridges.md) | 🇬🇧 English

# MCP Server and Bridges

The singleton **MCP Server** owns listener infrastructure only: bind address, port (default `8815`) and `/mcp` Streamable HTTP endpoint. It has no Spellbook, language, area or execution policy.

An **MCP Bridge** is one external client/device context. It owns a bearer credential, assigned Spellbook, optional speaking-device area, language override, Action AreaPolicy and Query AreaPolicy. Create Bridges as subentries of the Server.

The credential is revealed once. Later settings show only its suffix. Regeneration atomically publishes a new verifier and Bridge generation. The previous token becomes invalid without restarting the listener or other Bridges.

A Bridge with no area is an area-less or master Bridge; Spells that require an area expose it as a required argument. A Bridge with an area can use it as an implicit default where policy permits. If that configured area is deleted, HASE fails closed and raises a Repairs issue.

`BridgeRuntimeSupervisor` isolates each Bridge's configuration, credential and lifecycle. A change increments only that Bridge's generation and replaces its immutable snapshot; peer Bridges and the running listener remain untouched. In-flight requests belong to the generation they acquired. See [runtime](runtime.md) for the dispatch boundary, stale responses and the separate listener restart lifecycle.
