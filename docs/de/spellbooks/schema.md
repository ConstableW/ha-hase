🇩🇪 Deutsch | 🇬🇧 [English](../../en/spellbooks/schema.md)

# Spellbook-Schema

```yaml
id: example
name: {en: Example, de: Beispiel}
default_language: de
languages: [en, de]
commands:
  - id: lights_on
    trigger: {en: turn on the light in {area}, de: licht in {area} an}
    operation: light.turn_on
    target: {kind: area_name, capture: area}
    arguments: {}
    catchphrase: {en: Done., de: Erledigt.}
    output_mode: AUTO
```

Technische Felder sind unter anderem `id`, `operation`, `target`, `arguments`, `sources`, `steps`, `output_mode`, `assist_redirect` und `enabled`. Übersetzbar sind `name`, `description`, `trigger`, `triggers`, `examples`, `output` und `catchphrase`.

Target-Kinds: `area_name`, `area_id`, `entity_name`, `entity_id`, `entity_ids`, `source_area`, `all_allowed`, `template`; jede Operation beschränkt die zulässigen Arten. Template-Ziele sind Expert-tier.

Capture-Argumente können `capture`, `type`, Grenzen, boolesche Wortmengen oder `value_map` verwenden. Jeder Alias eines Befehls muss die Captures der gemeinsamen Definition liefern.

`output_mode`: `AUTO`, `TEXT`, `SPEECH`, `TEXT_AND_SPEECH`, `NONE`. In HA Conversation verhalten sich die ersten vier derzeit gleich; `NONE` unterdrückt die Antwort.

Setze `assist_redirect` bei Text-Spells explizit. Der aktuelle Kompatibilitätsdefault ist `true`: `false` liefert `output`, `true` leitet es an Assist weiter und kann eine Aktion auslösen.
