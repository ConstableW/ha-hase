🇩🇪 Deutsch | 🇬🇧 [English](../en/security.md)

# Sicherheitsmodell

HASE trennt Authoring, strukturelle Projektion, Metadaten und Laufzeit-Autorisierung.

Spellbook Studio ist eine **Authoring- und Verwaltungsoberfläche**, keine Autorisierungs-Engine für Assist oder MCP. Laufzeitgrenzen sind:

- Ein MCP-Bearer-Token wird serverseitig genau einer Bridge zugeordnet; der Client liefert keine vertrauenswürdige Bridge-ID.
- Action und Query `AreaPolicy` werden unabhängig nach realer Zielauflösung durch `ActionExecutor` geprüft.
- Assist Exposure begrenzt auflösbare Entitäten.
- Expert-Operationen und Template-Ziele benötigen die lokale Expert-Freigabe des Buchs.
- MCP-`assist_redirect` benötigt uneingeschränkte Action- und Query-Policy, weil `conversation.async_converse` keine enge Bereichsfreigabe pro Aufruf annimmt.

MCP-Projektion ist keine zweite Autorisierungs-Engine. Schema, Beschreibung und `readOnlyHint` sind beschreibende Metadaten und erteilen keine Rechte. `tools/call` leitet die Projektion erneut aus serverseitiger Konfiguration ab und durchläuft den gemeinsamen Ausführungspfad.

Bridge-Tokens sind hochentropisch und werden einmal vollständig angezeigt. Persistiert werden nur HMAC-SHA256-Verifier und ein kurzer Suffix; Token-Regeneration macht das alte Token ungültig.

Der MCP-Listener bindet alle Interfaces und liefert weder TLS noch OAuth. LAN und Token sind Sicherheitsgrenzen. Wird ein konfigurierter Bridge-Bereich gelöscht, verweigert HASE sicher die Ausführung und erstellt einen Repairs-Hinweis, statt zur bereichslosen Master-Bridge zu werden.
