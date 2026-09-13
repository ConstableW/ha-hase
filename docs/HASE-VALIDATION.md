# Local HASE validation — 2026-09-13

Baseline before edits: **235 tests passed, 30 subtests passed**.
After refactor: **241 tests passed, 30 subtests passed**.

Checks performed:

- Existing behavioral suite after relocating test loaders.
- New real-module import smoke test with only the external Home Assistant surface stubbed.
- Provider setup/unload, registration and invocation of all seven `hase` services.
- Standalone setup without starting a device client.
- Satellite backend's function-local coordinator import, new assistant event, shared device lock and slot mismatch refusal.
- Actual engine/bridge/vacuum YAML discovery, plus engine user-book override.
- Single integration domain, manifest, translation key parity and service selectors.
- Core import boundaries and absence of old runtime imports/user directories.
- Ruff undefined-name/import checks and JavaScript syntax check.
- Installation archive integrity check.

These are local checks on Windows, not a running Home Assistant instance. Real HA installation, UI behavior, physical CODE27 interactions and the testzwerg fresh setup are still pending. Use [the cutover checklist](HASE-CUTOVER.md).

No Git commit or GitHub repository rename was performed: the supplied workspace has no Git metadata. Historical input artifacts were retained. The source backup is `pre-hase-source.zip`; it is excluded from the release.
