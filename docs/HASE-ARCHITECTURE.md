# HASE — Home Assistant Spellbook Engine

Repository target: `ha-hase`. HA integration domain: `hase`.
Exactly one integration lives under `custom_components/hase`.

## Boundaries

- `core/`: matching, typed operations, executor interfaces, router, policies, sessions and clarifications. No Home Assistant or provider imports.
- `core/text_only_execution.py`: the fifth execution kind (a text-only Spell, `assist_redirect` true/false — see `core/models.py::DEFAULT_ASSIST_REDIRECT`) — neutral and shared. `core/router.py::SpellbookRouter` (the native HASE-Agent) and `core/mcp_execution.py` (MCP `tools/call`) both depend on it directly; neither depends on the other's own adapter module. No Home Assistant or provider imports.
- `spellbooker_ha.py`: Home Assistant service caller, target resolver, source-area resolution, and `HaAssistForwarder` (the real `AssistForwarder` implementation both `conversation.py` and `providers/mcp/transport.py` inject into the shared `core/text_only_execution.py` function above).
- `spellbook_loader.py`: shared discovery/loading of `spellbooks/`, for every frontend.
- `vacuum_provider.py`, `vacuum_providers/`: generic Home Assistant vacuum adapters, shared by engine actions and the CODE27 frontend.
- `conversation.py`: HA ConversationEntity for standalone agents and satellite subentries. Composes the existing router with the CODE27 backend when a satellite is used.
- `config_flow.py`: HASE registration, setup menu, standalone agent configuration/options, provider flow composition.
- `providers/code27/`: pairing, reauth, device options, satellite subentry flow, REST/WebSocket protocol, coordinator, bridge, frontend adapter, conversation backend, services and device entities.
- Root HA platform modules delegate device entity setup to `providers/code27/`; HA still loads the root platform paths.
- `core/mcp_projection.py`, `core/mcp_bridge_context.py`, `core/mcp_execution.py`, `core/mcp_capability.py`: the MCP frontend's pure Spell↔tool projection, execution adapter and capability-check logic (Studio's "MCP prüfen"). No Home Assistant or provider imports.
- `providers/mcp/`: MCP Server/Bridge config flow, credentials, Streamable HTTP transport, Repairs integration — the HA-touching half. See [HASE-MCP.md](HASE-MCP.md).

Adding a *different* frontend adapter (a new Bridge/channel onto the same
shared `core/` machinery)? See [HASE-NEW-BRIDGES.md](HASE-NEW-BRIDGES.md)
for the contract every adapter must satisfy and the mandatory shared
tests that enforce it.

There is one provider runtime per CODE27 config entry. Satellites use their owning entry's coordinator and the existing shared per-device backend lock. Standalone agents create no CODE27 device or coordinator.

## Spellbooks and resources

| Format | Bundled directory | User directory under HA config |
|---|---|---|
| HASE spellbooks | `custom_components/hase/spellbooks/` | `hase_spellbooks/` |
| Generic vacuum profiles | `custom_components/hase/vacuum_providers/` | `hase_vacuum_providers/` |

One schema and one matcher serve every frontend, including the native HASE-Agent (the HA `ConversationEntity` a Home Assistant Voice Assist pipeline is configured to use, both the standalone SPELLBOOK_ONLY agent and a Satellite subentry — `core/router.py::SpellbookRouter`) — `spellbook_engine.match()` ranks every execution kind (action, query, Combined, text-only) in one pass, never a filtered subset. CODE27, MCP and SpellbookRouter all consume typed commands through ActionDraft/ActionExecutor; a text-only command's own `assist_redirect` field (never its wording) decides whether it is a fixed answer rendered directly or a command phrase forwarded to Home Assistant's own Assist agent for real execution — see [HASE-MCP.md](HASE-MCP.md)'s own "Text-only Spells and `assist_redirect`" section for the full rule. CODE27's own `_forward_to_assist` is a separate, historical implementation of the same behavioral contract; MCP's `core/mcp_execution.py` and the HASE-Agent's `SpellbookRouter._execute_text_only_and_respond` both call the SAME neutral `core/text_only_execution.py::execute_text_only_command` directly — neither depends on the other, no fourth implementation. Typed execution errors never fall back to output. YAML contains no frontend markers. User files override bundled IDs through the shared loader.

See [Etappe 8](spellbook-engine/etappe-8-unified-spellbooks.md) for inventory, policy and migration details.

## Public API

Services: `hase.create_alarm`, `hase.update_alarm`, `hase.delete_alarm`, `hase.send_chat_message`, `hase.notify`, `hase.notify_clear`, `hase.speak`. All retain their existing parameter schemas and accept the new device config entry ID.

Chat events: `hase_user_message`, `hase_assistant_message`; payload fields unchanged.

The CODE27-specific dashboard keeps `custom:code27-card`, served from `/hase-cards/code27-card.js`. Its device and config-entry selection must be recreated after cutover.

A fourth frontend, MCP, lets external MCP clients call an already-known Spell by id over Streamable HTTP — see [HASE-MCP.md](HASE-MCP.md).

## Identity and lifecycle

Device identifiers use the `hase` namespace. Device entity unique-ID formulas retain their existing machine/entry identity and suffixes. Standalone conversation unique IDs use the new config entry ID; satellites use their subentry ID. No old registry identity is imported.

Existing option keys and policy semantics are unchanged. Missing query-policy keys still mirror action policy. Sessions, clarification state, deduplication and notification preview state remain in memory, with their existing reload/restart behavior.

CODE27 manufacturer names, authentication, firmware endpoints, local transport and backend limitations are unchanged. This rebrand adds no cloud fallback, structured tool protocol or new companion capability.

## Verification

Run `python -m pip install -r requirements-test.txt`, then `python -B -m pytest -q -p no:cacheprovider`.
The legacy behavioral suite is supplemented by package/resource boundary checks and an import smoke test that loads real HASE modules with only the external HA surface stubbed. This does not replace a real HA setup or a live-instance checklist.

Historical stage-1 reports, audit bundles, screenshots and baseline archives describe the pre-HASE implementation. They are evidence, not current installation instructions.
