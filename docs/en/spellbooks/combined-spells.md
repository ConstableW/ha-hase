🇩🇪 [Deutsch](../../de/spellbooks/combined-spells.md) | 🇬🇧 English

# Combined Spells

`steps` composes actions and queries into one Spell. Steps run sequentially in the given order through the same executor as single actions and queries – there is no second execution path.

```yaml
- id: night_mode
  trigger: good night in {area}
  steps:
    - alias: lights
      operation: light.turn_off
      target: {kind: area_name, capture: area}
    - alias: contacts
      sources:
        - kind: count
          target: {kind: all_allowed}
          domain_filter: binary_sensor
          state_filter: "on"
          variable: open
        - kind: names
          target: {kind: all_allowed}
          domain_filter: binary_sensor
          state_filter: "on"
          variable: which
  catchphrase: "{area}: {lights.success_count} of {lights.target_count} lights off; open: {contacts.open} ({contacts.which})."
```

## Schema

- `steps` is a list of 1–12 entries and excludes `operation` and `sources` on the same Spell.
- Each step is **exactly one** of:
  - **Action:** `operation`, `target`, optional `arguments` – the same rules as a single action Spell.
  - **Query:** `sources` – the same rules and kinds as the flat `sources:` field (`state`, `attribute`, `count`, `min`, `max`, `avg`, `sum`, `names`, `states`).
- `alias` is optional, must be a valid identifier, unique within the Spell, and must not shadow a trigger placeholder.
- Combined Spells are USER-tier. An `expert` variant together with `steps` is rejected.

## Variables

| Source | usable in | syntax |
|---|---|---|
| Trigger captures | every step's target and arguments, response | `capture: area` / `{area}` |
| Step results (alias) | **only** the response (`output`/`catchphrase`) | `{alias.field}` |

An alias cannot be used as a later step's target or argument; such a reference is an unknown capture and the Spell is not loaded. The response is rendered after all steps, so there is no ordering constraint there. Unknown aliases, unknown fields, bare `{alias}` and nested dots are rejected at load time.

Available fields:

- **Action with a single target** (`entity_id`, `entity_name`): `success`, `state`, `entity_name`, `entity_id`, `area` plus domain fields (e.g. `brightness` for lights).
- **Action with a collection target** (all other kinds): `target_count`, `success_count`, `failed_count`, `failed_names`. Real per-entity confirmation (re-reading state) only exists for operations with a fixed end state (e.g. `light.turn_off`, `cover.close_cover`, `lock.lock`) and at most 40 entities; otherwise the honest batch outcome of the service call applies.
- **Query:** each source's `variable` / `unit_variable`.

Missing values render as `?`; lists (`names`, `states`, `failed_names`) are joined with commas, an empty list renders as `?`.

## Failure behaviour

- A **policy denial (DENIED)** of a step aborts all remaining steps. Steps already executed are **not** rolled back.
- **Any other failure** (target not found, unknown operation, rejected service call, unavailable query source) affects only that step. Later steps still run, the step's fields reflect the outcome (e.g. `target_count: 0`), and the overall result is `partial`.
- If a step cannot be built from the recognised captures at all (invalid target or argument, e.g. a percentage outside `min`/`max`), the Spell is not executable for that input (matching rule "eligibility"); nothing runs. Explain matching then shows `CANDIDATE_UNRESOLVABLE` with the engine cause and the affected step.
- There are no transactions, rollback, conditions, loops or parallel steps.

## Dry run and Studio

- "Test – without calling devices" executes no action; queries only read state. The result lists every step with action, target, arguments, devices, status, reason and its alias values.
- The wizard builds steps with fixed targets (area, devices, all allowed, source area) and catalogue actions. Any step can additionally be switched to "Advanced (Expert)" individually, to enter a free operation, a free target (including Jinja/template, a spoken area/device placeholder) and free arguments - without forcing the rest of the spell into Expert. A step with Expert content stays normally editable, movable and removable; it only prevents converting the whole spell back to plain Wizard mode.
- The check names the affected step (e.g. "Schritt 2 (…): Aktion, Ziel oder Parameter ungültig").

## Safety

Like queries, Combined Spells do not take part in safety ambiguity (opposite actions). This holds for the runtime, the Collision Analyzer and Explain matching alike; equally ranked Combined Spells are decided by order (`order_dependent`). Every action step still goes through target resolution, Assist exposure, AreaPolicy and catalogue validation.

## Todo lists

`todo.list_items` behaves differently from all other action steps: it calls `todo.get_items` with `return_response=True` and returns status `QUERY_ANSWER`. The executor treats `QUERY_ANSWER` as success, so the step does not abort subsequent steps.

The step exposes two response fields under the step's alias:

| Field | Content |
|---|---|
| `{alias.items}` | All item summaries joined with commas. Empty list renders as `?`. |
| `{alias.count}` | Total number of items as an integer. |

Both fields are also accessible as `{items}` and `{count}` in a flat (non-combined) spell with `output`.

The optional `status` argument filters the result before the fields are populated:

```yaml
arguments:
  status: "needs_action"   # open items only
  # status: "completed"    # done items only
  # omit for all items
```

**Typical pattern — read list, report contents:**

```yaml
- id: show_inbox
  trigger: what is in my inbox
  steps:
    - alias: tasks
      operation: todo.list_items
      target: {kind: entity_id, value: todo.inbox}
      arguments: {}
  output: "Your inbox ({tasks.count} items): {tasks.items}"
```

**Combined with an action — check list then act:**

```yaml
- id: morning_brief
  trigger: good morning
  steps:
    - alias: lights
      operation: light.turn_on
      target: {kind: area_name, value: Bedroom}
      arguments: {}
    - alias: tasks
      operation: todo.list_items
      target: {kind: entity_id, value: todo.inbox}
      arguments:
        status: "needs_action"
  catchphrase: "Good morning! {lights.success_count} light(s) on. Open tasks: {tasks.items} ({tasks.count} total)."
```

**Entity exposure:** each todo entity must be exposed to the conversation domain individually. The wizard warns at the final step (Check & test) if the targeted entity is not exposed; the book validator flags it with a corrective action.

**Error handling:** if `todo.list_items` fails (entity unavailable, service error), `{alias.items}` and `{alias.count}` render as `?` and the overall result is `partial`. Later steps still run.
