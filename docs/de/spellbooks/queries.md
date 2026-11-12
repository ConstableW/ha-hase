🇩🇪 Deutsch | 🇬🇧 [English](../../en/spellbooks/queries.md)

# Abfragen

`entity.get_state` fragt eine Entität ab; `sources` baut eine Antwort aus mehreren Quellen. Es gilt die unabhängige Query AreaPolicy.

```yaml
- id: room_climate
  trigger: klima in {area}
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
  catchphrase: "{temperature} {temperature_unit}; {lights_on} Lichter an."
```

Quellenarten: `state`, `attribute`, `count`, `min`, `max`, `avg`, `sum`, `names`, `states`. `state`/`attribute` verlangen genau eine Entität; Aggregationen arbeiten auf Mengen. `state_filter` schränkt ein, `device_class` unterscheidet Sensorarten.

Policy-Verweigerung stoppt die gesamte Abfrage. Fehlende, mehrdeutige oder nicht verfügbare Quellen werden als `?` plus Diagnose dargestellt, damit andere Quellen weiterhin eine Antwort bilden können.
