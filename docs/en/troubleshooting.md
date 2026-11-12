🇩🇪 [Deutsch](../de/troubleshooting.md) | 🇬🇧 English

# Troubleshooting

| Symptom | Check |
|---|---|
| HASE is missing after copying files | Perform a full Home Assistant restart and confirm `<config>/custom_components/hase/manifest.json` exists. |
| A phrase does not match | Check the selected book, language fallback, enabled flag, exact area/entity name and aliases. Use Studio validation. |
| `DENIED` | This is a real policy, exposure or Expert-tier refusal. Inspect the `reason_code`; do not try to fix it by changing MCP metadata. |
| `FAILED` | Dispatch started but did not complete. Check Home Assistant logs and the target integration. |
| Query returns `?` | One source was missing, ambiguous or unavailable. Multi-source queries preserve the rest of the answer and report source diagnostics. |
| MCP connection refused | Use the MCP Server port (default `8815`), not Home Assistant's UI port, and `/mcp`. |
| MCP `401` | Supply the correct `Authorization: Bearer <token>` header or regenerate the Bridge token. |
| MCP `406` | Send an `Accept` header containing both `application/json` and `text/event-stream`. |
| Tool is absent | Run Studio's MCP check; inspect structural skip reasons and duplicate IDs. |
| Bridge area was deleted | Reconfigure the Bridge and clear the corresponding Home Assistant Repairs issue. HASE intentionally fails closed. |

The historical live SSE abort remains not root-caused. A restart was followed by recovery once, but that does not establish the cause or a general fix. Capture Home Assistant logs before restarting if it recurs.
