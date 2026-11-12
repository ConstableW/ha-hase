🇩🇪 [Deutsch](../de/architecture.md) | 🇬🇧 English

# Architecture

HASE has one semantic core and several adapters.

```text
Assist       MCP       CODE27
   \          |          /
    frontend input/output adapters
                |
 spellbook_engine → plans → ActionExecutor
                |             |
        matching/languages   target resolution + HA calls
                |
          Spellbook files
```

`custom_components/hase/core/` owns Spell meaning: multilingual resolution, matching, action/query/Combined planning, policies, execution status and response construction. It intentionally has no Home Assistant or provider imports. `spellbooker_ha.py` implements Home Assistant target resolution, Assist Exposure checks, service calls and Assist forwarding. `conversation.py`, `providers/mcp/` and `providers/code27/` adapt their respective channels.

A frontend may select a Spell differently—natural-language matching in Assist and CODE27, exact `spell_id` in MCP—but it must not redefine `operation`, `sources`, `steps`, `output`, `assist_redirect`, targets or policies.

Spellbooks are loaded from the bundled `custom_components/hase/spellbooks/` directory and user `<config>/hase_spellbooks/`; the user file with the same stable book ID wins. Generic vacuum profiles follow the same bundled/user pattern.

The current intentional provider boundary is that Home Assistant platform modules at the integration root delegate CODE27 entities to `providers/code27/`. The core remains provider-neutral.

Dashboard cards are frontend surfaces too. HASEAMP is the provider-neutral music card: it renders what a frontend provider reports and offers only what that provider's capability flags allow, and it knows no device of its own. Everything device-facing - namespace, operations, payloads, units, quirks - belongs to the frontend provider that owns it, inside its own provider directory; the first one is CODE27's music runtime. HASE core gains no music logic, and there is no central music contract that providers implement. See [HASEAMP (dashboard card)](cards.md).

See [Architecture rules](development/architecture-rules.md) for dependency constraints and the internal references (`../internal/INDEX.md`, maintainer documents that are not part of the public repository) for implementation history.
