🇩🇪 [Deutsch](../../de/spellbooks/schema.md) | 🇬🇧 English

# Spellbook schema

```yaml
id: example
name: {en: Example, de: Beispiel}
default_language: en
languages: [en, de]
commands:
  - id: lights_on
    trigger:
      en: turn on the light in {area}
      de: licht in {area} an
    triggers:
      en: [lights on in {area}]
      de: [licht an in {area}]
    operation: light.turn_on
    target: {kind: area_name, capture: area}
    arguments: {}
    catchphrase: {en: Done., de: Erledigt.}
    output_mode: AUTO
    enabled: true
```

Technical fields include `id`, `operation`, `target`, `arguments`, `sources`, `steps`, `output_mode`, `assist_redirect`, `enabled` and Expert variants. Translatable fields include `name`, `description`, `trigger`, `triggers`, `examples`, `output` and `catchphrase`.

Target kinds currently include `area_name`, `area_id`, `entity_name`, `entity_id`, `entity_ids`, `source_area`, `all_allowed` and `template`; each operation restricts which are valid. Template targets are Expert-tier.

Captured arguments may declare `capture`, `type`, bounds, boolean word sets, or `value_map`. Every alias of one command must provide the captures its shared target/argument definition needs; use separate commands for genuinely different argument shapes.

`output_mode` is `AUTO`, `TEXT`, `SPEECH`, `TEXT_AND_SPEECH` or `NONE`. Home Assistant Conversation currently exposes speech-or-empty rather than distinct text/speech channels, so the first four behave alike there; `NONE` suppresses the response. Other frontends may map modes to their capabilities.

For text-only commands, set `assist_redirect` explicitly. The current compatibility default is `true`, so new books should not rely on omission: `false` returns `output`; `true` forwards it to Assist and may perform an action.
