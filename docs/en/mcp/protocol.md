🇩🇪 [Deutsch](../../de/mcp/protocol.md) | 🇬🇧 English

# MCP protocol

Endpoint: `http://<home-assistant-host>:<port>/mcp`, with `Authorization: Bearer <Bridge token>`. The listener is separate from Home Assistant's web port and binds `0.0.0.0`.

The server uses MCP Streamable HTTP with `stateless_http=True` and protocol version `2025-06-18`. Clients must accept both `application/json` and `text/event-stream`; otherwise the SDK returns `406 Not Acceptable`.

```bash
curl -N -X POST "http://<host>:8815/mcp" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}'
```

`tools/list` returns Bridge-specific tool schemas. `tools/call` returns text content. Policy or execution failures are tool errors such as `DENIED: <reason>` or `FAILED: <reason>`; unknown/unprojectable tools use `error: ...`. Transport/authentication failures occur before Spell execution.

`readOnlyHint` is accurate advisory metadata, not authorization. There are no resources, prompts, stdio or OAuth flows, and `tools.listChanged` is false. Clients discover changes with a new `tools/list` request.
