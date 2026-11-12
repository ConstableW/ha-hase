🇩🇪 [Deutsch](../de/cards.md) | 🇬🇧 English

# HASEAMP (dashboard card)

HASEAMP is HASE's Winamp-inspired music card: `custom_components/hase/cards/haseamp-card.js`, served by the integration at `/hase-cards/haseamp-card.js` and loaded automatically. You only need to add as a card of type `custom:haseamp-card`. The name belongs to this user interface only - it is not a backend module, not a service and not a music platform.

For device controls and character chat, use the separate `custom:code27-card`. Like HASEAMP, it ships with the integration and is loaded automatically (HASE: `/hase-cards/code27-card.js`, standalone integration: `/code27-cards/code27-card.js`); no manual Lovelace resource is needed. The card resolves the selected device's owner. HASEAMP is the additional music card.

Configuration keys: `target` (the `config_entry_id` of a real CODE27 device entry under `hase` or `code27`; a HASE External Hub binding is not a target), `name`, `provider` (which frontend provider to talk to; defaults to the registered one) and the visibility flags `show_browser`, `show_queue`, `show_playlists`, `show_import`, `show_animation`, `show_diagnostics` and `follow_light` (default on; the card's colours follow the device's ambient light).

The card is provider-neutral. It holds no queue and no playlist state of its own (only which row the user clicked last), it never calls a device API itself, and it offers only what the backend reports as supported: transport, seek, volume, source, queue modes and the media browser are gated by the snapshot's capability flags (`features.*`). A backend without a feature therefore gets an honestly unavailable control instead of a pretend one, and text that depends on the device - for example that a stop is emulated because the device has no STOP action - is derived from those flags rather than hard-wired into the card.

The card is one window with four tabs (Queue, Library, Playlists, Import). It shows "MusicMode is off!" while the device's music screen is closed, can import audio files and M3U playlists onto the device's card, delete files and folders, edit and export stored playlists, and takes its colours from the device's ambient light. Those functions and their limits are described in [CODE27 Music and the HASEAMP card](providers/code27.md#layout-music-mode-and-colours); like everything else, they are offered only when the provider's backend implements them.

## The frontend provider boundary

Everything provider-specific lives in one marked table in the card's own source (`PROVIDER_TRANSPORTS`) and nowhere else:

| Entry | Meaning |
|---|---|
| `command` | the WebSocket command that carries the operations |
| `subscribe` | the subscription that pushes snapshots |
| `noDeviceReason` | the reason code meaning "no device of this provider is loaded" |
| `deviceLabel` | how this provider's device is named in user-facing hints |

Outside that table the card stays free of device vocabulary: no device units, status names, paths, track-id prefixes, error codes or namespaces. Two contract tests enforce that on the comment-stripped source (`test_card_code_stays_provider_neutral_outside_the_boundary` and `test_card_declares_its_provider_boundary_exactly_once` in `tests/test_code27_music.py`, plus the namespace check in `tests/test_haseamp_frontend.mjs`), so device logic cannot creep back into the UI.

This is a frontend boundary, not a HASE contract: there is no core module, no Python interface and no shared music model behind it. Each frontend provider defines and implements its own music interface, with its own namespace, operations, payload shape and device quirks. A provider without music features registers nothing and therefore feeds no HASEAMP card. HASE core never gains a music platform, and no provider has to imitate another one.

The first implemented frontend provider is CODE27 - see the [CODE27 provider](providers/code27.md) for its contract, its module layout and its device specifics.
