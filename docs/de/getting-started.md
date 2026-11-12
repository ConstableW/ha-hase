🇩🇪 Deutsch | 🇬🇧 [English](../en/getting-started.md)

# Erste Schritte

Dieser Weg benötigt nur Home Assistant und HASE. CODE27-Hardware und MCP-Client sind optional.

1. Kopiere `custom_components/hase` nach `<config>/custom_components/hase` und starte Home Assistant vollständig neu.
2. Wähle **Einstellungen → Geräte & Dienste → Integration hinzufügen → HASE → Spellbook-Sprachassistent erstellen**.
3. Wähle das mitgelieferte Spellbook `demo`. Erlaube mindestens einen Bereich für Aktionen und Abfragen oder aktiviere bewusst die jeweilige uneingeschränkte Option.
4. Wähle die neue `conversation.*`-Entität in einer Assist-Pipeline.
5. Probiere beispielsweise `licht wohnzimmer an` mit dem exakten Home-Assistant-Bereichsnamen.

Die Aktion gelingt nur, wenn das Ziel für Assist freigegeben und durch die Action AreaPolicy erlaubt ist.

Öffne danach Spellbook Studio unter `/hase`, erstelle oder kopiere ein Buch und wähle im Aktionskatalog **Licht einschalten** (`light.turn_on`). Beim Speichern eines mitgelieferten Buchs entsteht ein Benutzer-Override; das Original bleibt unverändert. Weise das neue Buch in den Optionen des HASE-Agenten zu.

Weiter: [Assist](assist.md), [Schema](spellbooks/schema.md), [Studio](studio.md), [MCP](mcp/overview.md) oder [CODE27](providers/code27.md).
