🇩🇪 Deutsch | 🇬🇧 [English](../../en/mcp/area-policy.md)

# MCP-Bereichs-Policy und Schema-Projektion

Die Projektion liest dieselbe `ScopedAreaPolicy`, die der Executor später erzwingt, aber nur zur Formung von Bereichsargumenten. Sie erteilt keine Berechtigung.

- **Fall A – raumgebunden und auf diesen Raum begrenzt:** Capture wird aus dem Schema entfernt und intern mit dem Bridge-Bereich gefüllt.
- **Fall B – bereichslose/Master-Bridge:** kein Default; Bereich ist Pflichtargument.
- **Fall C – raumgebunden mit breiterer Policy:** Bereich ist optional; Weglassen nutzt den Bridge-Bereich, expliziter Wert kann einen anderen erlaubten Bereich wählen.

Aktionen nutzen Action AreaPolicy, Abfragen Query AreaPolicy; Combined Spells können beide berühren. `area_id` und `area_name` bleiben getrennt: Policy-Mitgliedschaft nutzt IDs, eine `area_name`-Capture den Anzeigenamen.

Jedes explizite oder injizierte Ziel wird gegen aktuelle Registries geprüft und bei Ausführung erneut autorisiert. Ein zulässiger Schemawert verspricht weder Existenz noch Exposure oder Berechtigung.
