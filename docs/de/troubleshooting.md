🇩🇪 Deutsch | 🇬🇧 [English](../en/troubleshooting.md)

# Fehlerbehebung

| Symptom | Prüfung |
|---|---|
| HASE fehlt nach dem Kopieren | Home Assistant vollständig neu starten; `custom_components/hase/manifest.json` prüfen. |
| Satz matched nicht | Buch, Sprache/Fallback, `enabled`, exakte Bereichs-/Entitätsnamen und Aliase prüfen; Studio-Validator nutzen. |
| `DENIED` | Echte Policy-, Exposure- oder Expert-Verweigerung; `reason_code` prüfen. MCP-Metadaten ändern keine Rechte. |
| `FAILED` | Dispatch begann, wurde aber nicht erfolgreich beendet; HA-Logs und Zielintegration prüfen. |
| Abfrage liefert `?` | Eine Quelle fehlt, ist mehrdeutig oder nicht verfügbar; Quelldiagnose prüfen. |
| MCP-Verbindung abgelehnt | MCP-Server-Port (Standard `8815`) und `/mcp` statt HA-Webport verwenden. |
| MCP `401` | `Authorization: Bearer <token>` korrigieren oder Token regenerieren. |
| MCP `406` | `Accept` muss `application/json` und `text/event-stream` enthalten. |
| Tool fehlt | MCP-Prüfung im Studio: strukturelle Skip-Gründe und doppelte IDs ansehen. |
| Bridge-Bereich gelöscht | Bridge neu konfigurieren und Repairs-Hinweis bearbeiten; HASE scheitert absichtlich geschlossen. |

Der historische SSE-Abbruch ist nicht ursächlich geklärt. Ein Neustart ging einmal einer Erholung voraus, beweist aber keine allgemeine Lösung. Bei Wiederholung vor dem Neustart HA-Logs sichern.
