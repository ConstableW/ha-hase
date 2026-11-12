🇩🇪 [Deutsch](../../de/development/architecture-rules.md) | 🇬🇧 English

# Architecture rules

1. `core/` owns Spell semantics, models, matching, planning, policy and execution contracts. It must not import Home Assistant or provider modules.
2. Frontends own only input, transport, identity/context wiring and output adaptation. They must not redefine a Spell's meaning.
3. `core/operations.py` is the sole operation catalog. Arbitrary services are not accepted from YAML.
4. All real actions and queries pass through `ActionExecutor` and its target, Assist Exposure, policy, argument and Expert checks.
5. Action and Query AreaPolicy remain independent.
6. Text-only `assist_redirect` is explicit and separate from `output_mode`.
7. MCP structural projection, Bridge-specific schema, metadata, authorization and execution-time enforcement are separate layers.
8. Provider-specific behavior stays out of generic architecture unless it defines an intentional adapter contract.
9. Planned ADRs and historical reports never become claims of shipped behavior without implementation and executable tests.
10. A frontend provider owns the interface to its own device, music included: its namespace, operations, payload shape, units and device quirks live in its own provider directory. HASE core never gains a device or music platform, and no provider implements a central music contract on the core's behalf.
11. UI names are UI-only. HASEAMP names the dashboard card; backend modules, APIs and classes are named after what they do, and a card contains no device logic. The card's provider boundary is documented in [HASEAMP (dashboard card)](../cards.md) and enforced by contract tests.

New frontend adapters should follow the current reference contract in `docs/HASE-NEW-BRIDGES.md` and shared adapter tests, while treating that retained document as an internal implementation reference. Cards follow the frontend provider boundary in [HASEAMP (dashboard card)](../cards.md) instead.
