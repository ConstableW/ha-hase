# HASE: testzwerg cutover

This is a fresh setup, not a domain migration. Do not install an old `code27` compatibility integration or create service/event aliases.

## Before deployment

- Record the actual Home Assistant version and compare local code with the deployed baseline.
- Back up Home Assistant and the old integration files together.
- Record device settings, bridge options, standalone agents, satellite titles/slots, books, action/query area policies and Assist pipeline selections.
- Record affected automations, scripts, dashboards and external callers. Existing JSON dumps in this project are historical and incomplete.
- Run local tests and verify the release in a disposable real Home Assistant instance.

## Cutover

1. Temporarily disable affected automations.
2. Remove old `code27` entries through Home Assistant while the old integration remains installed.
3. Stop Home Assistant.
4. Replace `custom_components/code27` with `custom_components/hase` from the complete release. Do not merge them.
5. Copy selected user books/profiles into the new `hase_*` directories. Preserve their respective formats.
6. Copy `www/hase/`, update the dashboard resource to `/local/hase/code27-card.js`, and remove the old resource registration to avoid defining the same custom element twice.
7. Start Home Assistant and add HASE. Pair CODE27 and recreate standalone agents and satellite subentries.
8. Restore the intended options/policies and select the new conversation agents in Assist pipelines.
9. Update service domains, event names and entity/device/config-entry references in callers. Re-select the device in the CODE27 card editor.
10. Complete verification, restart HA again and re-enable automations.

## Live acceptance — mostly complete (2026-09-13)

- [x] Exactly one new integration domain, `hase`; no remaining active `code27` entries.
- [x] Device setup, options, reauth entry point, reload and unload work.
- [x] Sensors, music, light, character selection, alarms and memo to-do list work.
- [x] Chat, bridge, speech, notifications and acknowledgement work once per request.
- [x] All seven `hase.*` services work with the selected device entry.
- [x] Both new chat events and the card's activity feed work.
- [x] Standalone Spellbook-only agents work without CODE27 hardware.
- [x] Satellite create/reconfigure/remove works under the correct parent; slot mismatch is rejected.
- [ ] Empty satellite book reaches the backend; a configured broken book fails closed.
- [ ] Policies, source areas, clarification and concurrent satellite requests retain existing behavior.
- [ ] Custom books/profiles override the bundled resources correctly.
- [x] A second restart creates no duplicate entities, listeners or device connections.

## Rollback

Restore the complete pre-cutover Home Assistant backup and matching old integration package. Merely restoring the old Python folder does not restore entries removed during fresh setup.

The local `pre-hase-source.zip` is only a source rollback aid, not an HA backup. It must not be included in release packages.

## Repository publication

The intended repository is `ConstableW/ha-hase`, as referenced by the manifest. The supplied workspace has no Git metadata; the GitHub repository rename and publication are separate pending operations. Do not claim the links are live until the repository has been renamed or created.
