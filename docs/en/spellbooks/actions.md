🇩🇪 [Deutsch](../../de/spellbooks/actions.md) | 🇬🇧 English

# Actions

The implemented operation catalog in `core/operations.py` is authoritative. Spellbooks reference operation IDs; they cannot define arbitrary Home Assistant services.

Covered domains include lights, locks, covers, climate, fans, media players, vacuums, scenes, scripts, automations, buttons, numbers, selects, timers, switches and input booleans. Important bespoke operations are `light.set_power`, `vacuum.clean_area` and `media_player.skip_track`; `entity.get_state` is a query.

The current catalog, derived from `OPERATION_CATALOG`, is:

- Light: `light.set_power`, `light.turn_on`, `light.turn_off`, `light.toggle`, `light.set_color_temperature`, `light.set_color`, `light.set_effect`
- Lock: `lock.lock`, `lock.unlock`
- Cover: `cover.open_cover`, `cover.close_cover`, `cover.stop_cover`, `cover.toggle`, `cover.set_cover_position`, `cover.set_cover_tilt_position`, `cover.open_cover_tilt`, `cover.close_cover_tilt`, `cover.stop_cover_tilt`
- Climate/fan: `climate.set_temperature`, `climate.set_hvac_mode`, `climate.set_preset_mode`, `climate.set_fan_mode`, `fan.turn_on`, `fan.turn_off`, `fan.toggle`, `fan.set_percentage`, `fan.set_preset_mode`, `fan.oscillate`
- Media/vacuum: `media_player.skip_track`, `media_player.volume_up`, `media_player.volume_down`, `media_player.volume_mute`, `media_player.stop`, `media_player.pause`, `media_player.media_play`, `media_player.media_play_pause`, `media_player.media_next_track`, `media_player.media_previous_track`, `media_player.volume_set`, `media_player.select_source`, `vacuum.clean_area`, `vacuum.start`, `vacuum.pause`, `vacuum.stop`, `vacuum.return_to_base`
- Helpers and automation: `scene.turn_on`, `script.turn_on`, `automation.trigger`, `automation.turn_on`, `automation.turn_off`, `button.press`, `number.set_value`, `input_number.set_value`, `select.select_option`, `input_select.select_option`, `timer.start`, `timer.pause`, `timer.cancel`, `timer.finish`, `switch.turn_on`, `switch.turn_off`, `switch.toggle`, `input_boolean.turn_on`, `input_boolean.turn_off`, `input_boolean.toggle`
- Todo: `todo.list_items`, `todo.add_item`, `todo.update_item`, `todo.remove_item`
- Query: `entity.get_state`

```yaml
- id: dim_room
  trigger: set {area} lights to {value} percent
  operation: light.set_power
  target: {kind: area_name, capture: area}
  arguments:
    percentage: {capture: value, type: int, min: 0, max: 100}
```

## Todo lists

Todo entities (`todo.*`) are always targeted by entity — no area semantics apply ("all todos in the living room" has no natural meaning). Each entity must be individually exposed to the conversation domain in **Settings → Voice Assistants → Expose entities**. The wizard and spell validator warn if a targeted entity is not exposed.

### Operations

| Operation | Kind | Description |
|---|---|---|
| `todo.list_items` | query | Read all items on a list. Returns `items` (comma-joined summaries) and `count` (integer). |
| `todo.add_item` | action | Add a new item to the list. Required: `item` (text). |
| `todo.update_item` | action | Mark an item done or open, or rename it. Required: `item` (must exactly match the item's name). Optional: `status` (`completed` / `needs_action`), `rename` (new name). |
| `todo.remove_item` | action | Permanently delete an item by name. Required: `item`. |

`todo.list_items` uses Home Assistant's `return_response` mechanism and returns status `QUERY_ANSWER` (not `SUCCESS`). The optional `status` argument filters the result: `"needs_action"` returns only open items, `"completed"` only completed ones.

`todo.update_item` and `todo.remove_item` identify items by their display name. If the item does not exist, Home Assistant raises a service error; HASE surfaces this as `FAILED` — no pre-check by HASE is needed or performed.

### Examples

```yaml
# Read list — response uses {items} and {count}
- id: shopping_read
  trigger: what is on the shopping list
  operation: todo.list_items
  target: {kind: entity_id, value: todo.shopping}
  arguments: {}
  output: "On the list: {items} ({count} items)"

# Add item captured from the trigger phrase
- id: shopping_add
  trigger: add {item} to the shopping list
  operation: todo.add_item
  target: {kind: entity_id, value: todo.shopping}
  arguments:
    item: {capture: item, type: str}

# Mark item done — HA fails if item name does not exist
- id: inbox_done
  trigger: "complete task {item}"
  operation: todo.update_item
  target: {kind: entity_id, value: todo.inbox}
  arguments:
    item: {capture: item, type: str}
    status: completed
  output: "Task '{item}' completed."

# Remove item
- id: shopping_remove
  trigger: remove {item} from the list
  operation: todo.remove_item
  target: {kind: entity_id, value: todo.shopping}
  arguments:
    item: {capture: item, type: str}
```

For reading a list and using its contents in a multi-step response, see [Combined Spells](combined-spells.md#todo-lists).

The catalog defines allowed targets, argument types/ranges, service mapping, capability hints and Expert status. `lock.lock` and `lock.unlock` are Expert-only. Capability hints control what Studio offers; Home Assistant remains authoritative about a specific entity's actual support.

Actions resolve targets first, enforce Assist Exposure and action `AreaPolicy`, validate arguments, then dispatch. `all_allowed` means all resolved targets that remain within policy, not global unrestricted access. `vacuum.clean_area` uses native Home Assistant area cleaning when available, otherwise an optional provider profile; ambiguity fails safely.
