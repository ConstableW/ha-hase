Klar – hier ist eine generelle Korrektur- und Pflegeanleitung für Spellbooks, die du als `docs/SPELLBOOK_AUTHORING.md` ablegen kannst.

Sie ist so geschrieben, dass sie:
- **Für Menschen** lesbar ist (du, wenn du ein Spellbook pflegst)
- **Für Agenten** als Prompt funktioniert (wenn du sie einem LLM gibst, damit es ein Spellbook nachzieht)
- **Für HASE** konsistent ist (Format, Felder, Semantik)
- **Für HASE-CHAN** kompatibel bleibt (LLM-freundliche Descriptions, keine Semantik-Duplikate)

```markdown
# Spellbook Authoring Guide

This document describes how to write, correct and maintain HASE spellbooks.
It is authoritative for human authors and for any LLM/agent asked to produce
or fix a spellbook.

It is intentionally independent of any specific frontend (CODE27, Assist,
Spellbook Studio, MCP). The same spellbook must work across all of them.

---

## 1. Purpose of a spellbook

A spellbook is a set of **semantic commands** that a user can speak or type.
Each command is resolved by the HASE Spellbook Engine, not by the frontend
that received it.

A spellbook is NOT:

- a keyword list for a specific device
- a prompt for an LLM
- a copy of Home Assistant automations
- a frontend-specific configuration

A spellbook IS:

- frontend-independent
- language-aware
- semantic (it describes intent, not device calls)
- safe by default (respects AreaPolicy and exposure)

---

## 2. File structure

A spellbook file is YAML and MUST contain at least:

```yaml
id: <stable_machine_id>
name: <human_readable_name>
commands: [ ... ]
```

Optional top-level fields:

```yaml
default_language: de   # or en
language: de           # legacy alias; prefer default_language
```

- `id` is a stable machine identifier. Never change it once a spell is
  in use; other systems may reference it.
- `name` is a human-readable display name.
- `default_language` selects the fallback language for missing translations.

---

## 3. Command structure

Every command MUST have:

- `id` – stable machine identifier, unique within the spellbook
- `trigger` – the phrase(s) that activate the command
- at least one of:
  - `operation` (a typed action against a target)
  - `output` (a text response)
  - `steps` (a sequence of the above)

Recommended fields:

- `name` – human-readable command name (per language)
- `catchphrase` – the spoken confirmation on success
- `description` – short, LLM-friendly description of what the command does
- `examples` – 1–2 concrete example utterances
- `tags` – semantic categories (Licht, Musik, Sicherheit, Status, …)
- `enabled` – `true` unless explicitly disabled
- `output_mode` – usually `AUTO`

Do NOT invent fields that the loader does not validate. If a field is not
documented in the HASE architecture, it will be ignored or rejected.

---

## 4. Trigger rules

- A trigger is a phrase template. `{placeholders}` mark captured variables.
- A command MAY have multiple triggers (per language, or as aliases).
- Prefer **multi-language** form:

```yaml
trigger:
  de: licht {area} an
  en: turn on the light in {area}
```

- Do NOT use regex, globbing or keyword lists inside a trigger.
- Do NOT rely on case. Matching is case-insensitive where the engine allows.
- Do NOT add a trigger that duplicates another command’s semantic action.
  If two commands do the same thing, one of them is wrong.

---

## 5. Placeholders

Placeholders are typed by the command’s `target` and `arguments`:

- `{area}` – captured from `target.kind: area_name`
- `{entity}` – captured from `target.kind: entity_name`
- `{target}` – generic target, resolved by HASE
- `{value}` – numeric or enum argument
- `{color}` – color argument

Placeholder names are part of the command’s public interface. Renaming a
placeholder is a breaking change; other systems may rely on it.

---

## 6. Operation and target

`operation` is a typed action (e.g. `light.set_power`,
`media_player.skip_track`, `vacuum.clean_area`). It is resolved by HASE,
never by a frontend.

`target.kind` describes how to resolve the affected entity:

- `area_name` – an area named by a capture
- `entity_name` – an entity named by a capture
- `all_allowed` – every target the Bridge is allowed to affect
- `source_area` – the Bridge’s own area (requires the Bridge to have one)
- `none` – no target (text-only commands)

Do NOT invent `target.kind` values. Do NOT bypass AreaPolicy by using a
free-text target.

---

## 7. Catchphrase, output, description

These three are often confused. They serve different purposes:

- **`output`** – the text HASE uses when the command succeeds. May be
  templated with placeholders. Used for the returned answer.
- **`catchphrase`** – the short spoken confirmation. Preferred over `output`
  when the user should hear a fixed line (e.g. “Lumos.”).
- **`description`** – what an LLM sees when deciding whether to call this
  command. Not spoken. Not returned to the user.

A command may have all three, or only some, depending on its role.

Rules:

- `catchphrase` SHOULD be short (1–5 words).
- `output` SHOULD describe the action in plain language.
- `description` MUST be semantically precise and distinguishable from
  sibling commands (see §9).

---

## 8. Language rules

Every user-visible field SHOULD be provided in both supported languages:

```yaml
name:
  de: ...
  en: ...
trigger:
  de: ...
  en: ...
catchphrase:
  de: ...
  en: ...
output:
  de: ...
  en: ...
description:
  de: ...
  en: ...
examples:
  de: [...]
  en: [...]
```

- German is the primary authoring language in this project.
- English MUST be provided where the user may speak English.
- Missing English falls back to German and vice versa, but this fallback
  is a safety net, not a goal. Provide both.

Do NOT mix languages inside a single string.

---

## 9. Description quality — the single most important rule

Descriptions are how an LLM decides which command to call. If two commands
have identical or vague descriptions, the LLM cannot choose correctly.

Rules for `description`:

- MUST be specific to this command’s actual effect.
- MUST distinguish this command from its closest sibling.
- MUST NOT be a copy of the command name.
- MUST NOT be the same string as another command’s description.
- SHOULD mention the action (on/off, raise/lower, next/previous, etc.).
- SHOULD be short (one sentence).

Bad:

```yaml
description:
  de: lichti
```

Bad (two commands with identical description):

```yaml
- id: lumos
  description: {de: Schaltet das Licht}
- id: nox
  description: {de: Schaltet das Licht}
```

Good:

```yaml
- id: lumos
  description:
    de: Schaltet das Licht in einem gesprochenen Bereich ein
    en: Turns on the light in the given area
- id: nox
  description:
    de: Schaltet das Licht in einem gesprochenen Bereich aus
    en: Turns off the light in the given area
```

If a command has no clear description, it is not ready.

---

## 10. Examples

`examples` give the LLM concrete utterances. Rules:

- 1–2 examples per language.
- MUST be valid for the command’s trigger.
- SHOULD use realistic area/entity names.
- MUST NOT use dangerous or non-existent targets.
- MUST NOT be the same as another command’s example.

Examples are guidance, not routing. The engine will not match on them
directly.

---

## 11. Tags

`tags` are semantic categories. Use them sparingly and consistently.

Suggested categories:

- `Licht`
- `Musik`
- `Klima`
- `Sicherheit`
- `Reinigung`
- `Szene`
- `Status`
- `Zeit`
- `Tür`
- `Gerät`

Tags are not used for routing. They are used for grouping, filtering and
future UI. Do not invent a tag per command.

---

## 12. Operations that need an area

A command whose target is `source_area` requires the Bridge to have a
configured area. Without one, the command will fail with
`no_source_area`.

This is intentional. Do not work around it by inventing a target argument
in the command. Fix the Bridge configuration instead.

---

## 13. Commands that only produce text

A command with no `operation` and only `output` is a **text-only** command.
It is valid and will be projected as an MCP tool, but it performs no
device action.

Text-only commands MUST:

- have a precise `description`
- have a `catchphrase` only if a spoken acknowledgement is desired
- not be used to fake a device action

---

## 14. Forbidden patterns

Do NOT:

- hardcode device entity IDs
- duplicate semantic actions across commands
- copy a spellbook from another user without reviewing it
- put frontend-specific behaviour in a spellbook
- use regex, keyword lists or fuzzy matching in triggers
- invent `operation` or `target.kind` values
- rely on case-sensitive matching
- bypass AreaPolicy via free-text targets
- add a command that only changes wording of another command
- add a `description` that is not distinguishable from a sibling
- use placeholders that are not captured by `target`/`arguments`
- ship a command whose `examples` don’t match its `trigger`

---

## 15. Authoring checklist

Before publishing a spellbook, verify:

- [ ] `id`, `name`, `commands` present
- [ ] `default_language` set
- [ ] every command has a unique `id`
- [ ] every command has at least one `trigger`
- [ ] triggers are multi-language where the user may speak both
- [ ] every command has a `description` (both languages)
- [ ] every command has 1–2 `examples` (both languages)
- [ ] every command has appropriate `tags`
- [ ] every command has `enabled: true` unless intentionally disabled
- [ ] no two commands have identical or overlapping `description`
- [ ] no two commands have the same semantic action without reason
- [ ] `catchphrase` is short and language-appropriate
- [ ] `output` is templated correctly
- [ ] `target.kind` is a supported value
- [ ] `source_area` commands have a configured Bridge area
- [ ] no device IDs, secrets or environment values are embedded
- [ ] the file is valid YAML and round-trips through the loader

---

## 16. Correction prompt for an LLM/agent

When asked to correct or complete a spellbook, use the following rules:

> You are given one or more HASE spellbook YAML files. Your task is to
> bring them into the canonical form described in this document.
>
> Rules:
>
> - Preserve `id`, `operation`, `target`, `arguments`, `trigger` semantics.
> - Do not change the meaning of any command.
> - Do not invent new commands unless explicitly asked.
> - Do not delete commands unless they violate a hard rule.
> - Add missing `name`, `description`, `examples`, `tags`, `enabled`,
>   `output_mode` fields.
> - Provide both `de` and `en` for every user-visible field.
> - Make `description` semantically precise and distinguishable from
>   siblings. This is the single most important rule.
> - Keep `catchphrase` short.
> - Ensure `examples` match the command’s `trigger`.
> - Do not use regex, keyword lists or fuzzy matching.
> - Do not embed secrets, tokens, entity IDs or environment values.
> - Output the corrected YAML in full, ready to commit.
>
> If a command cannot be corrected without changing its meaning, leave it
> unchanged and add a note at the end of the file explaining why.

---

## 17. Definition of Done for a corrected spellbook

A spellbook is considered ready when:

- every command passes the checklist in §15
- every command has a distinguishable `description`
- every command has valid `examples`
- the YAML round-trips through the loader without warnings
- the spellbook does not rely on any frontend-specific behaviour
- no command duplicates another command’s semantic action
- no command relies on `source_area` without a configured Bridge area
- secrets, tokens and environment values are absent

If any of these is not satisfied, the spellbook is not ready.
```

