# HASE — Home Assistant Spellbook Engine

Repository target: `ha-hase`. HA integration domain: `hase`.
Exactly one integration lives under `custom_components/hase`.

## Boundaries

- `core/`: matching, typed operations, executor interfaces, router, policies, sessions and clarifications. No Home Assistant or provider imports.
- `spellbooker_ha.py`: Home Assistant service caller, target resolver, source-area resolution and engine-book discovery.
- `vacuum_provider.py`, `vacuum_providers/`: generic Home Assistant vacuum adapters, shared by engine actions and the CODE27 frontend.
- `conversation.py`: HA ConversationEntity for standalone agents and satellite subentries. Composes the existing router with the CODE27 backend when a satellite is used.
- `config_flow.py`: HASE registration, setup menu, standalone agent configuration/options, provider flow composition.
- `providers/code27/`: pairing, reauth, device options, satellite subentry flow, REST/WebSocket protocol, coordinator, bridge, frontend adapter, conversation backend, services and device entities.
- Root HA platform modules delegate device entity setup to `providers/code27/`; HA still loads the root platform paths.

There is one provider runtime per CODE27 config entry. Satellites use their owning entry's coordinator and the existing shared per-device backend lock. Standalone agents create no CODE27 device or coordinator.

## Spellbooks and resources

| Format | Bundled directory | User directory under HA config |
|---|---|---|
| Engine books | `custom_components/hase/spellbooker_books/` | `hase_spellbooker_books/` |
| CODE27 bridge books | `custom_components/hase/providers/code27/spellbooks/` | `hase_spellbooks/` |
| Generic vacuum profiles | `custom_components/hase/vacuum_providers/` | `hase_vacuum_providers/` |

The two book formats remain distinct. Identical book IDs do not imply interchangeable contents. User files override bundled files according to the existing loaders. There is no fallback to old `code27_*` directories.

## Public API

Services: `hase.create_alarm`, `hase.update_alarm`, `hase.delete_alarm`, `hase.send_chat_message`, `hase.notify`, `hase.notify_clear`, `hase.speak`. All retain their existing parameter schemas and accept the new device config entry ID.

Chat events: `hase_user_message`, `hase_assistant_message`; payload fields unchanged.
Bridge activity uses HA's `logbook_entry` with domain `hase` and provider name `CODE27 Assist-Bridge`.

The CODE27-specific dashboard keeps `custom:code27-card`, served from `/local/hase/code27-card.js`. Its device and config-entry selection must be recreated after cutover.

## Identity and lifecycle

Device identifiers use the `hase` namespace. Device entity unique-ID formulas retain their existing machine/entry identity and suffixes. Standalone conversation unique IDs use the new config entry ID; satellites use their subentry ID. No old registry identity is imported.

Existing option keys and policy semantics are unchanged. Missing query-policy keys still mirror action policy. Sessions, clarification state, deduplication and notification preview state remain in memory, with their existing reload/restart behavior.

CODE27 manufacturer names, authentication, firmware endpoints, local transport and backend limitations are unchanged. This rebrand adds no cloud fallback, structured tool protocol or new companion capability.

## Verification

Run `python -m pip install -r requirements-test.txt`, then `python -B -m pytest -q -p no:cacheprovider`.
The legacy behavioral suite is supplemented by package/resource boundary checks and an import smoke test that loads real HASE modules with only the external HA surface stubbed. This does not replace a real HA setup or the testzwerg checklist.

Historical stage-1 reports, audit bundles, screenshots and baseline archives describe the pre-HASE implementation. They are evidence, not current installation instructions.
