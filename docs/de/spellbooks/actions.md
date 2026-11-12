🇩🇪 Deutsch | 🇬🇧 [English](../../en/spellbooks/actions.md)

# Aktionen

Der implementierte Katalog in `core/operations.py` ist verbindlich. Spellbooks referenzieren Operation-IDs und können keine beliebigen Home-Assistant-Services definieren.

Abgedeckt sind Licht, Schloss, Cover, Klima, Fan, Media Player, Vacuum, Scene, Script, Automation, Button, Number, Select, Timer, Switch und Input Boolean. Besondere Operationen sind `light.set_power`, `vacuum.clean_area` und `media_player.skip_track`; `entity.get_state` ist eine Abfrage.

Der aktuelle, aus `OPERATION_CATALOG` abgeleitete Katalog:

- Light: `light.set_power`, `light.turn_on`, `light.turn_off`, `light.toggle`, `light.set_color_temperature`, `light.set_color`, `light.set_effect`
- Lock: `lock.lock`, `lock.unlock`
- Cover: `cover.open_cover`, `cover.close_cover`, `cover.stop_cover`, `cover.toggle`, `cover.set_cover_position`, `cover.set_cover_tilt_position`, `cover.open_cover_tilt`, `cover.close_cover_tilt`, `cover.stop_cover_tilt`
- Climate/Fan: `climate.set_temperature`, `climate.set_hvac_mode`, `climate.set_preset_mode`, `climate.set_fan_mode`, `fan.turn_on`, `fan.turn_off`, `fan.toggle`, `fan.set_percentage`, `fan.set_preset_mode`, `fan.oscillate`
- Media/Vacuum: `media_player.skip_track`, `media_player.volume_up`, `media_player.volume_down`, `media_player.volume_mute`, `media_player.stop`, `media_player.pause`, `media_player.media_play`, `media_player.media_play_pause`, `media_player.media_next_track`, `media_player.media_previous_track`, `media_player.volume_set`, `media_player.select_source`, `vacuum.clean_area`, `vacuum.start`, `vacuum.pause`, `vacuum.stop`, `vacuum.return_to_base`
- Helper/Automation: `scene.turn_on`, `script.turn_on`, `automation.trigger`, `automation.turn_on`, `automation.turn_off`, `button.press`, `number.set_value`, `input_number.set_value`, `select.select_option`, `input_select.select_option`, `timer.start`, `timer.pause`, `timer.cancel`, `timer.finish`, `switch.turn_on`, `switch.turn_off`, `switch.toggle`, `input_boolean.turn_on`, `input_boolean.turn_off`, `input_boolean.toggle`
- Todo: `todo.list_items`, `todo.add_item`, `todo.update_item`, `todo.remove_item`
- Query: `entity.get_state`

```yaml
- id: dim_room
  trigger: licht in {area} auf {value} prozent
  operation: light.set_power
  target: {kind: area_name, capture: area}
  arguments:
    percentage: {capture: value, type: int, min: 0, max: 100}
```

## Todo-Listen

Todo-Entitäten (`todo.*`) werden immer per Entität adressiert – Bereichsziele sind nicht sinnvoll ("alle Todos im Wohnzimmer" hat keine natürliche Bedeutung). Jede Entität muss einzeln in **Einstellungen → Sprachassistenten → Entitäten freigeben** für die Konversations-Domäne freigegeben sein. Wizard und Spell-Validator warnen, wenn eine gezielte Entität nicht freigegeben ist.

### Operationen

| Operation | Art | Beschreibung |
|---|---|---|
| `todo.list_items` | Abfrage | Alle Einträge einer Liste lesen. Gibt `items` (kommagetrennte Zusammenfassungen) und `count` (Ganzzahl) zurück. |
| `todo.add_item` | Aktion | Neuen Eintrag anlegen. Pflicht: `item` (Text). |
| `todo.update_item` | Aktion | Eintrag abhaken, wiedereröffnen oder umbenennen. Pflicht: `item` (muss exakt mit dem Namen übereinstimmen). Optional: `status` (`completed` / `needs_action`), `rename` (neuer Name). |
| `todo.remove_item` | Aktion | Eintrag dauerhaft löschen. Pflicht: `item`. |

`todo.list_items` nutzt den `return_response`-Mechanismus von Home Assistant und gibt den Status `QUERY_ANSWER` zurück (nicht `SUCCESS`). Das optionale Argument `status` filtert: `"needs_action"` liefert nur offene Einträge, `"completed"` nur erledigte.

`todo.update_item` und `todo.remove_item` identifizieren Einträge über den Anzeigenamen. Existiert der Eintrag nicht, wirft Home Assistant einen Service-Fehler; HASE liefert daraufhin `FAILED` zurück – eine eigene Vorabprüfung durch HASE findet nicht statt und ist nicht nötig.

### Beispiele

```yaml
# Liste lesen — Antwort nutzt {items} und {count}
- id: einkauf_lesen
  trigger: was steht auf der Einkaufsliste
  operation: todo.list_items
  target: {kind: entity_id, value: todo.einkaufen}
  arguments: {}
  output: "Auf der Liste: {items} ({count} Eintraege)"

# Eintrag aus dem Trigger-Platzhalter hinzufuegen
- id: einkauf_hinzufuegen
  trigger: "{item} auf die Einkaufsliste"
  operation: todo.add_item
  target: {kind: entity_id, value: todo.einkaufen}
  arguments:
    item: {capture: item, type: str}

# Eintrag abhaken — HA schlaegt fehl, wenn der Name nicht existiert
- id: inbox_erledigt
  trigger: "ToDo {item} abschliessen"
  operation: todo.update_item
  target: {kind: entity_id, value: todo.inbox}
  arguments:
    item: {capture: item, type: str}
    status: completed
  output: "Aufgabe '{item}' abgeschlossen."

# Eintrag entfernen
- id: einkauf_entfernen
  trigger: "{item} von der Liste streichen"
  operation: todo.remove_item
  target: {kind: entity_id, value: todo.einkaufen}
  arguments:
    item: {capture: item, type: str}
```

Wie man eine Liste liest und den Inhalt in einer mehrstufigen Antwort verwendet, beschreibt [Combined Spells](combined-spells.md#todo-listen).

Der Katalog definiert Ziele, Argumenttypen/-grenzen, Service-Mapping, Capability-Hinweise und Expert-Status. `lock.lock` und `lock.unlock` sind Expert-only. Hinweise steuern Studio-Angebote; die konkrete Entitätsunterstützung entscheidet Home Assistant.

Die Runtime löst Ziele auf, prüft Assist Exposure und Action AreaPolicy, validiert Argumente und führt dann aus. `all_allowed` bedeutet alle aufgelösten und durch Policy erlaubten Ziele, nicht globale Freigabe.
