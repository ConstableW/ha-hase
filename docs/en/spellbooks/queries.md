🇩🇪 [Deutsch](../../de/spellbooks/queries.md) | 🇬🇧 English

# Queries

Use `entity.get_state` for one resolved entity, or `sources` for a composed answer. Queries use the independent query `AreaPolicy`.

```yaml
- id: room_climate
  trigger: climate in {area}
  sources:
    - kind: state
      target: {kind: area_name, capture: area}
      domain_filter: sensor
      device_class: temperature
      variable: temperature
      unit_variable: temperature_unit
    - kind: count
      target: {kind: area_name, capture: area}
      domain_filter: light
      state_filter: "on"
      variable: lights_on
  catchphrase: "{temperature} {temperature_unit}; {lights_on} lights on."
```

Implemented source kinds are `state`, `attribute`, `count`, `min`, `max`, `avg`, `sum`, `names` and `states`. `state` and `attribute` require exactly one entity. Aggregations operate on a set; `state_filter` narrows it. `device_class` distinguishes sensors of the same domain.

A policy denial rejects the whole query. A missing, ambiguous or unavailable source becomes `?` with diagnostics so other sources can still produce a useful response. Use unique identifier-style `variable` and `unit_variable` names referenced by `catchphrase`.
