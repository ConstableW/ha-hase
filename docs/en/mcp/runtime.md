🇩🇪 [Deutsch](../../de/mcp/runtime.md) | 🇬🇧 English

# MCP runtime lifecycle

HASE uses MCP Streamable HTTP with `stateless_http=True`. There is no persistent server-side client or conversation session. Runtime ownership therefore supervises long-lived Bridges rather than client connections: every Bridge has an immutable snapshot and a monotonically increasing generation.

`BridgeRuntimeSupervisor` publishes Bridge snapshots and credential bindings together through immutable copy-and-swap. Authentication therefore obtains Bridge identity and generation from one consistent publication. Under the Bridge-local lock, a request acquires a `RequestLease` for that exact snapshot; it then executes without holding the lock and calls `may_publish()` before returning to ensure its generation is still current. Concurrent requests to the same or different Bridges are not serialized.

A configuration change or credential rotation replaces only that Bridge's snapshot and increments its generation. Other Bridges and the Uvicorn listener remain in place. Removing a Bridge unpublishes both its snapshot and credential binding. The retained `CredentialIndex` in `hass.data` is only a compatibility/diagnostic view; authentication uses the supervisor publication.

## Side-effect boundary

A Bridge change cancels in-flight work only while no Home Assistant service or Assist call has begun. `HaServiceCaller` and `HaAssistForwarder` mark that transition with `commit_side_effect()`. Afterward the real side effect is allowed to finish, but the stale response is discarded. `tools/call` reports `error: bridge_generation_changed`, while a stale `tools/list` result is not published.

## Listener lifecycle

Listener ownership is separate from Bridge updates. Only a listener-port change restarts Uvicorn. A dedicated lock serializes restarts, which follow `cancel → await termination → start` so the old port is released before rebinding. A full unload stops the listener and removes the supervisor.

Open risks remain: the historical SSE abort is not root-caused; a client-only reconnect without a Bridge change is not state the stateless supervisor can reclaim; and `manifest.json` permits `mcp>=2.2.0` although the semantics were verified with 2.2.0.
