🇩🇪 [Deutsch](../../de/development/testing.md) | 🇬🇧 English

# Testing and verification

Run the Python suite from the repository environment:

```powershell
python -B -m pytest -q -p no:cacheprovider
```

Run the Studio browser-unit suite when frontend files or Studio behavior changes:

```powershell
node --experimental-default-type=module --test tests/test_studio_frontend.mjs
```

Automated MCP coverage includes projection/execution/context tests and a local Streamable HTTP round trip using the official SDK and a simulated Home Assistant surface. In addition, the MCP interface was used with hasechan and a physical CODE27 device during the Home Assistant real-system test. That test also covered the available HASE features, including the Spellbooker and CODE27 integration.

Keep four evidence levels distinct in reports: automated test coverage, verification on a real Home Assistant host, physical-device verification, and unverified/historical observations. Never promote a server-reported success to a confirmed physical outcome without independent observation.

For documentation-only changes, also check relative links, language counterparts, terminology and the final diff for accidental runtime changes.
