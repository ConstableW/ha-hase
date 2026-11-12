🇩🇪 [Deutsch](../../de/spellbooks/concepts.md) | 🇬🇧 English

# Spellbook concepts

A Spellbook is a YAML file containing stable command IDs and multilingual human phrasing. A Spell describes meaning once; frontends decide only how a request enters and how the response is delivered.

Each enabled command has one primary `trigger`, optional `triggers` aliases, and exactly one execution form: `operation`, `sources`, `steps`, or text-only `output`. `output` may also accompany a typed command as display metadata, but execution failure never falls back to it.

Captures such as `{area}` are filled by natural-language matching or MCP arguments. `target` determines how a capture is resolved; `arguments` converts other captures into typed values. Area and entity resolution happens at execution time against current Home Assistant registries and policy.

Book IDs select runtime units. User books in `<config>/hase_spellbooks/` override bundled books with the same ID. They are complete replacements, not partial language patches.

Language fallback is deterministic: requested language, English, declared default, the only available language, then a technical fallback. Technical identifiers are never translated.

Continue with [schema](schema.md), [actions](actions.md), [queries](queries.md), and [Combined Spells](combined-spells.md).
