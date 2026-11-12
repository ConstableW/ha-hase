🇩🇪 Deutsch | 🇬🇧 [English](../../en/mcp/runtime.md)

# MCP-Runtime-Lebenszyklus

HASE verwendet MCP Streamable HTTP mit `stateless_http=True`. Es gibt keine dauerhafte serverseitige Client- oder Conversation-Session. Die Runtime überwacht deshalb langlebige Bridges statt Client-Verbindungen: Jede Bridge besitzt einen unveränderlichen Snapshot und eine monoton steigende Generation.

`BridgeRuntimeSupervisor` veröffentlicht Bridge-Snapshots und Credential-Bindings gemeinsam per immutable Copy-and-swap. Eine Authentifizierung erhält dadurch Bridge-Identität und Generation aus demselben Stand. Die Anfrage nimmt unter dem Bridge-lokalen Lock eine `RequestLease` auf genau diesen Snapshot, führt danach ohne gehaltenen Lock aus und prüft vor der Antwort mit `may_publish()`, ob ihre Generation noch aktuell ist. Gleichzeitige Anfragen derselben oder verschiedener Bridges werden nicht serialisiert.

Eine Konfigurationsänderung oder Token-Regeneration ersetzt nur den Snapshot dieser Bridge und erhöht ihre Generation. Andere Bridges und der Uvicorn-Listener bleiben bestehen. Eine gelöschte Bridge wird samt Credential-Binding aus der Veröffentlichung entfernt. Der weiterhin in `hass.data` vorhandene `CredentialIndex` ist nur eine Kompatibilitäts-/Diagnoseansicht; die Authentifizierung verwendet die Supervisor-Veröffentlichung.

## Side-effect Boundary

Eine Bridge-Änderung storniert In-flight-Arbeit nur, solange noch kein Home-Assistant-Service oder Assist-Aufruf begonnen hat. `HaServiceCaller` und `HaAssistForwarder` markieren den Übergang mit `commit_side_effect()`. Danach darf der reale Side Effect fertiglaufen; die alte Antwort wird jedoch verworfen. `tools/call` meldet in diesem Fall `error: bridge_generation_changed`, und eine veraltete `tools/list`-Antwort wird nicht publiziert.

## Listener-Lebenszyklus

Der Listener-Lebenszyklus ist von Bridge-Updates getrennt. Nur eine Änderung des Listener-Ports startet Uvicorn neu. Der Neustart ist durch einen eigenen Lock serialisiert und folgt `cancel → Beendigung abwarten → start`, damit der alte Port vor dem erneuten Binden freigegeben ist. Ein vollständiges Unload stoppt den Listener und entfernt den Supervisor.

Offene Risiken bleiben: Der historische SSE-Abbruch ist nicht ursächlich geklärt; ein reiner Client-Reconnect ohne Bridge-Änderung ist kein Zustand, den der stateless Supervisor zurücksetzen kann; und `manifest.json` erlaubt `mcp>=2.2.0`, obwohl die Semantik mit 2.2.0 verifiziert wurde.
