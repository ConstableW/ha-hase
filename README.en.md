<p align="center"><img width="600" alt="HASE – Home Assistant Spellbook Engine" src="docs/branding/hase-title.png" /></p>

🇩🇪 [Deutsch](README.md) | 🇬🇧 English

# HASE — Home Assistant Spellbook Engine

HASE is the semantic layer between what a human or agent means and what Home Assistant does.

## One semantic layer. Multiple interfaces.

```text
                         Spellbooks
                 multilingual intent + meaning
                              |
               matching, planning, policy, execution
                              |
          +-------------------+-------------------+
          |                   |                   |
   Assist / Conversation     MCP       CODE27 / other frontends
                              |
                     Spellbook Studio
                     authoring and management
```

HASE defines commands once as multilingual **Spells**. Assist, MCP and CODE27 adapt their own input and output to that model; Spellbook Studio manages it. A new command is authored as a Spellbook entry instead of implementing separate frontend logic.

MCP is therefore not the product. It is one channel: a conventional MCP server defines tools for a backend, while HASE projects a controlled subset of its existing semantic model as tools and executes calls through the same security path.

## CODE27 ownership

HASE can set up CODE27 itself: the resulting config entry, device and entities belong to `domain=hase`. Alternatively, [ha-code27](https://github.com/ConstableW/ha-code27) can own a standalone device under `domain=code27`. HASE can optionally bind that loaded entry through **Bind existing standalone CODE27** and add bridge and satellite features without duplicating the CODE27 client, device or entities. Different devices can have different owners.

**Use CODE27 with HASE:** For an embedded device, open **Settings → Devices & services → Add integration → HASE → Connect a CODE27 device**, enter its local host/IP and confirm the pairing code shown on the device. For a hub already configured under **CODE27**, choose **HASE → Bind existing standalone CODE27** and select its entry instead. Then open **Configure** on the HASE hub, enable **Assist Bridge** and select a Spellbook. Spellbook triggers do not require a codeword. See the [CODE27 provider guide](docs/en/providers/code27.md) for the full steps and [ha-code27](https://github.com/ConstableW/ha-code27) for standalone pairing.

## What is included

- A standalone Home Assistant Conversation agent, with no CODE27 hardware required.
- Actions, status queries, multi-source queries, Combined Spells and text-only responses.
- Independent action and query area policies, Assist Exposure checks and Expert-tier gates.
- A Streamable HTTP MCP frontend with per-client Bridges.
- Spellbook Studio for authoring, validation, import/export and dry runs.
- An optional CODE27 companion provider for local device integration, character chat and Satellites.

## Installation

**Via HACS (recommended):** Search for “HASE” in HACS, download it and restart Home Assistant. Then go to *Settings → Devices & services → Add integration* and choose “HASE”. Until the integration is listed in the HACS catalog, use it as a custom repository.

**Fallback – custom repository:** HACS → ⋮ → *Custom repositories* → add `https://github.com/ConstableW/ha-hase` with category *Integration*, then download as above.

**Manual:** Copy the `custom_components/hase` folder into your Home Assistant `custom_components` directory and fully restart Home Assistant.

## Start here

Follow [Getting started](docs/en/getting-started.md) to install HASE and run one Spell through Assist. Then continue with:

- [Spellbook concepts](docs/en/spellbooks/concepts.md)
- [Architecture](docs/en/architecture.md)
- [Security model](docs/en/security.md)
- [MCP overview](docs/en/mcp/overview.md)
- [Spellbook Studio](docs/en/studio.md)
- [HASEAMP card](docs/en/cards.md)
- [CODE27 provider](docs/en/providers/code27.md)
- [Troubleshooting](docs/en/troubleshooting.md)

## Project status

HASE has automated test coverage and its available features have been tested on a real Home Assistant installation with a physical CODE27 device. This includes the Spellbooker, the MCP interface with hasechan, and CODE27 connected through HASE and as an External Hub. The real-system test confirms the exercised features; the documented technical limits still apply.

See [Testing and verification](docs/en/development/testing.md) and the per-feature limitations before relying on HASE for consequential automations.

## Development

- [Contributing](docs/en/development/contributing.md)
- [Testing](docs/en/development/testing.md)
- [Architecture rules](docs/en/development/architecture-rules.md)


## License and authorship

Open source under the [Apache License 2.0](LICENSE). **Original author: ConstableW.** The [NOTICE file](NOTICE) carries the attribution. Distributors of derivative works must include the license, mark modified files, and retain applicable `NOTICE` attributions in readable form.

**AI assistance:** ChatGPT/Codex, Claude, DeepSeek, Gemini, and Ollama with the `qwen3-coder:30b` model were used as tools for analysis, design, implementation, and documentation. Parts of the project were created with AI assistance; project responsibility and decisions remain with ConstableW.

## Support & Issues

For support, feature requests, or bug reports, please visit the [HASE GitHub issues](https://github.com/ConstableW/ha-hase/issues). We welcome community contributions and feedback!

Buy Me a Coffee – Maintainer:

[![Buy Me a Coffee – Maintainer](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/constablew)
