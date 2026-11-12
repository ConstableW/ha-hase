🇩🇪 Deutsch | 🇬🇧 [English](../../en/mcp/protocol.md)

# MCP-Protokoll

Endpoint: `http://<home-assistant-host>:<port>/mcp` mit `Authorization: Bearer <Bridge token>`. Der Listener ist vom Home-Assistant-Webport getrennt und bindet `0.0.0.0`.

HASE nutzt MCP Streamable HTTP mit `stateless_http=True`, Protokollversion `2025-06-18`. Clients müssen `application/json` und `text/event-stream` akzeptieren, sonst folgt `406 Not Acceptable`.

```bash
curl -N -X POST "http://<host>:8815/mcp" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}'
```

`tools/list` liefert Bridge-spezifische Schemas. `tools/call` liefert Text. Policy-/Ausführungsfehler erscheinen als `DENIED: <reason>` oder `FAILED: <reason>`, unbekannte/nicht projektierbare Tools als `error: ...`. Transport/Auth-Fehler entstehen vorher.

`readOnlyHint` ist korrekte Empfehlung, keine Autorisierung. Resources, Prompts, stdio und OAuth fehlen; `tools.listChanged` ist false. Clients holen Änderungen mit einem neuen `tools/list`.
