# HASE × CODE27 — Final Implementation Conformance Audit

**One-time audit report.**
**Audited:** the CODE27 provider of the current HASE working tree at `C:\USERDATA\ha-hase`
**Against:** the final CODE27 local API research at `C:\USERDATA\HA-Code27 API`
**Method:** static read-only inspection. No CODE27 device was contacted, no
endpoint was called, no port was scanned, no capability was bypassed, and no
source, test or configuration file was modified.
**Supersedes:** the previous revision of this same file (see *Superseded audit
revision* below — its findings could not be reproduced and it has been replaced).
**Companion documents (permanent, in the HASE repository):**

* `docs/code27/implemented-api.md` — human-readable implementation reference
* `docs/code27/implemented-api.yaml` — machine-readable ledger (127 operations + WebSocket + resources)

---

# Executive Summary

## Ground truth verification

| Question | Answer |
|---|---|
| How many operations does the authoritative inventory contain? | **127** (`_recon/inventory.json`: 127 entries, 127 unique `METHOD + PATH`) |
| Does the permanent ledger contain exactly 127 `METHOD + PATH` entries? | **Yes** — verified mechanically: no duplicates, no omissions, no additions |
| Is the "127" figure consistent with the API reference? | **Yes**, with one documented internal inconsistency: `CODE27-LOCAL-API.md` says "124" in an early paragraph and "127" in its coverage table, and `code27-runtime-architecture-findings.md` records the growth `124 → 127`. **127 is ground truth** per `inventory.json`. No forced number was used; the discrepancy is recorded here rather than silently merged. |

## Counts by implementation status

```
TOTAL CODE27 OPERATIONS:            127

IMPLEMENTED:                         51
PARTIAL:                              0
NOT_IMPLEMENTED:                     54
INTENTIONALLY_UNSUPPORTED:            1
CAPABILITY_GATED:                    21
NOT_RELEVANT_TO_HASE:                 0
REMOVED_UPSTREAM:                     0
```

## Counts by test coverage (of the 51 implemented)

```
IMPLEMENTED WITH STRONG TESTS:       14
IMPLEMENTED WITH PARTIAL TESTS:      24
IMPLEMENTED WITH NO TESTS:           13      ← TEST DEBT
```

## Counts by direction (of the 51 implemented)

```
READ OPERATIONS IMPLEMENTED:         24
WRITE OPERATIONS IMPLEMENTED:        27
```

No generic "percentage complete" is published. Not every CODE27 endpoint belongs
in a Home Assistant integration, so such a figure would mislead.

## Verification status of this audit

| Side | Status |
|---|---|
| CODE27 semantics | taken from the newest authoritative research; where newer findings supersede older ones this is recorded explicitly (see *Documentation Corrections*) |
| HASE behaviour | **VERIFIED_FROM_CODE** for every statement — traced through the actual runtime path, not through docstrings, comments or tests |
| Live testing | **NOT_LIVE_TESTED** — this audit performed none. Where HASE's own source asserts a previous live confirmation, that claim is attributed to the source and marked as such. |

## Headline assessment

The CODE27 provider is **architecturally sound and unusually well documented
internally**. It is confined to a single API client
(`providers/code27/api.py`) behind a clean coordinator, and it correctly handles
the four hardest parts of the protocol:

1. **Byte-exact request signing** — canonical string, payload modes, JSON body
   hashing with compact separators, and the `UNSIGNED-PAYLOAD` multipart
   sentinel all match the final contract exactly.
2. **The device's failure-in-a-success-envelope trap** — an HTTP 200 with
   `success: true` whose nested `status` is `FAILED` is detected and raised, so
   music control can never silently do nothing.
3. **The TTS asynchronous contract** — the POST acknowledgement is treated as
   *accepted only*; HASE polls to a terminal `ENDED`/`FAILED` state under a
   wall-clock deadline.
4. **The dead WebSocket chat topic** — `chat_history` is deliberately **not**
   subscribed; chat changes are detected by a dedicated 3-second REST poll. This
   was the single most important "did they get it right?" question in the brief,
   and the answer is yes.

Nine correctness issues were found. None is a data-loss risk. **Four are rated
Medium or Medium-High** and worth fixing; the remaining five are Low. Two of the
Medium findings share one root cause: HASE relies on premises about CODE27 that
the final research has since disproved.

The largest structural gap is an *absence* rather than a bug: 54 operations are
unimplemented, of which 21 are blocked by CODE27 capability gates that are
disabled on the known device, and 1 is provably not exposed by CODE27 at all.

## Consistency validation

All 18 required mechanical checks were executed (full log in the Appendix).
**Two failed; both are now corrected:**

1. **The inherited ledger was wrong.** The previously existing
   `docs/code27/implemented-api.yaml` did **not** contain all 127 authoritative
   operations: `C27-127` was assigned to `PUT /v1/ws/channel` — which is **not**
   one of the 127 REST operations and does not appear in `inventory.json` — while
   the real operation `PUT /data/user/slot` was **missing entirely**. The
   WebSocket channel is a separate realtime interface, not a REST operation.
   `C27-127` now names `PUT /data/user/slot`; ids `C27-001 … C27-126` were left
   unchanged so that no existing id was renumbered.
2. **The inherited audit report was unreliable.** See below.

All other checks pass: unique ids, unique `METHOD + PATH`, set equality with the
inventory, set equality between Markdown and YAML, every referenced source file
existing, every referenced symbol resolving in its declared files, every
referenced test file and case existing, and internal count consistency.

## Superseded audit revision (important)

This file previously contained an audit revision dated 2026-09-23 whose findings
**could not be reproduced**. It is not merely a difference of judgement; it
described an API and an implementation that do not exist. Concrete examples:

| Previous claim | Reality (verified against `inventory.json` and the current source) |
|---|---|
| `C27-127 — GET /data/system/capabilities`, marked `INTENTIONALLY_UNSUPPORTED` | **No such endpoint exists** in the 127-operation inventory (there is no `/data/system/*` family at all). The real `C27-127` is `PUT /data/user/slot`. |
| OTA check/install/status as `C27-011 … C27-013` | **No OTA endpoint exists** in the inventory. Those ids are `DELETE /data/user/memory/cards/{id}`, `DELETE /data/user/memos/{id}`, `DELETE /data/user/music/file`. |
| "Admin network config/scan/connect (C27-018 … C27-021)" | **No network-config endpoint exists.** Those ids are chat/music/character operations. |
| "`POST /data/user/character/{slot}/diary`", "`GET /data/user/chat/suggestions`" | **Neither exists.** |
| "`GET /data/user/alarms/next` (C27-096)", "`/data/user/alarms/{id}/enable`" | **Neither exists**; alarms are `/data/user/alarm/...` (singular) and there is no `next` route. |
| "`PUT /data/user/music/audio_route` (C27-075) — route selection" | **No write endpoint for the audio route exists** in the inventory; only the `GET`. |
| "Bluetooth device management `C27-083 … C27-086`", "Playlist management `C27-076 … C27-081`" | **No Bluetooth scan/connect/disconnect and no playlist endpoints exist.** |
| "`POST /data/user/proactive/trigger` (C27-123)" | **Does not exist**; the proactive family is only `GET`/`PUT /data/user/proactive/config`. |
| "HASE … checks them before calling gated endpoints" | **False.** No HASE feature is gated on a capability flag. `Code27CapabilitiesSensor` states this explicitly, and no conditional exists anywhere in the provider. |
| "Nonce freshness: each request uses `uuid.uuid4().hex`" | **False.** `api._nonce()` uses `random.choice` over `[0-9a-z]`, 16 characters. |
| Appendix A "range → domain" table (Authentication `001–007`, Chat `022–045`, Music `066–089`, …) | **Contradicts both the real inventory and that revision's own ledger**, whose ids 001–013 are all `DELETE` operations. |
| Summary table: 54 implemented / 1 partial / 18 gated / 53 not implemented | Not reproducible. This audit finds **51 / 0 / 21 / 54**. The difference is concentrated in the fabricated categories "Sounds/Audio (5 gated)" and "Vision (3 gated)", which do not correspond to any gated operation. |

Only one artefact of that revision survived scrutiny: its `implemented-api.yaml`
mapped ids `C27-001 … C27-126` to real inventory operations in alphabetical
order, and those assignments were therefore **preserved** for id stability (see
the ID-stability notes in the ledger header). Its `C27-127` was the single
error, corrected here.

**Consequence for the reader:** treat any statement from the superseded revision
as unverified. Nothing from it was carried into the permanent ledger or into this
report without independent verification against the current source and the
authoritative inventory.

---

# Critical Correctness Findings

Only actual current defects are listed. Missing optional features are **not**
listed here (see the backlog). No code was modified.

---

## CCF-1 — `machine_id` is used as durable device identity (MEDIUM-HIGH)

| | |
|---|---|
| **Severity** | Medium-High |
| **Affected feature** | Config-entry identity, every entity's `unique_id`, reauth device matching, remote IP recovery, `PUT /data/user/character` |
| **Current behaviour** | `Code27FlowMixin._async_finish_pairing` reads `machine_id` from `GET /data/user/system`, calls `async_set_unique_id(machine_id)`, persists it as `CONF_MACHINE_ID`, and every entity derives its `unique_id` from `entry.unique_id`. Reauth **aborts** with `wrong_device` when the reported `machine_id` differs from the stored one. Remote IP recovery trusts a relay answer only if `machine_id` matches. |
| **Expected behaviour** | The final research states plainly: *"**`machine_id` is not a stable device identity.** In the companion app the persistent identity is the `app_id`; `machine_id` is a **transient pairing token** … So do not key long-lived integration state on `machine_id`."* A durable identifier must not be built on it. |
| **Code evidence** | `config_flow.py` — `_async_finish_pairing` (`async_set_unique_id(machine_id)`, `CONF_MACHINE_ID`, reauth comparison) and `async_step_reauth`; `api.py` — `Code27Client.__init__` (`machine_id`), `_call_remote_system_info` (`X-DEVICE-ID`), `attempt_remote_recovery` (`remote_info.get("machine_id") != self.machine_id`, `local_info.get("machine_id") != self.machine_id`); every entity module's `f"{machine_id}_…"` unique ids (`sensor.py`, `switch.py`, `light.py`, `select.py`, `media_player.py`, `todo.py`, `image.py`, `button.py`, `binary_sensor.py`) |
| **CODE27 evidence** | `CODE27-LOCAL-API.md` §4.11 note on `GET /data/user/system`; `CODE27-HASE-CAPABILITY-MATRIX.md` §5 "Identity rules" (*"machine_id is a transient pairing token — do not key state on it"*) |
| **Impact** | If the device re-pairs (factory reset, re-pair, firmware change), `machine_id` may change. HASE would then see a different identity for the same physical device: reauth aborts as `wrong_device`, and `async_set_unique_id` would create a duplicate config entry instead of reattaching to the existing one. Entity history and statistics would fork. |
| **Test impact** | No test asserts identity stability. `FlowTests` always feed a stable `machine_id`, so the suite passes regardless of this defect. |
| **Not fixed** | Per audit scope. See backlog **P0-1**. |
| **Important nuance** | `machine_id` **is** the correct value for the `device_id` field of `PUT /data/user/character`, so its use *there* is contract-required. The defect is specifically its use as the config-entry/entity identity and reauth key. |

---

## CCF-2 — Media browser treats absolute device paths as relative (MEDIUM)

| | |
|---|---|
| **Severity** | Medium |
| **Affected feature** | Music media browser tree (`media_player.async_browse_media`) |
| **Current behaviour** | `_group_music_files` groups by `path.split("/", 1)[0]`. The live-confirmed value of `music_files[].path` is **absolute** — e.g. `/storage/142E2DF92E2DD4900/code27/music/Rockkapella/03 - Stand By Me.mp3`. Splitting that on the first `/` yields the empty string, so the browser produces **a single folder with an empty title** containing every file, instead of one folder per actual sub-directory. |
| **Expected behaviour** | Group by the path relative to the music root (strip the absolute prefix, or split on the segment following `code27/music/`), producing `Rockkapella` etc. |
| **Code evidence** | `media_player.py` — `_group_music_files`, `_files_in_folder` (prefix `f"{folder}/"` becomes `"/"`, matching every file), `_track_browse_item`; the adjacent source comment already admits the assumption was never verified: *"not verified against `music_storage.root_path` possibly being an absolute prefix instead"* |
| **CODE27 evidence** | `_recon/responses/_data_user_music_storage.json`: `root_path: "/storage/142E2DF92E2DD4900"` and every `music_files[].path` starting with `/storage/…`; `CODE27-LOCAL-API.md` music/storage section shows the same absolute example |
| **Impact** | User-visible: the media browser shows one oddly-named (empty) folder rather than the real folder structure. Playback still works, because the `track_id` is built from the full absolute path — so this is a presentation/organisation defect, not a functional break. |
| **Test impact** | **The test suite actively hides this defect.** `MediaPlayerTests.test_group_music_files_splits_root_and_subfolder`, `test_files_in_folder_filters_by_prefix_not_substring`, `test_browse_media_root_lists_folders_then_root_files` and `test_browse_media_folder_lists_only_its_own_files` all use **relative** paths (`"Rockkapella/a.mp3"`) — exactly the assumption contradicted by the live payload. |
| **Not fixed** | See backlog **P0-2**. |

---

## CCF-3 — `music_storage` has no REST fallback, based on a false premise (MEDIUM)

| | |
|---|---|
| **Severity** | Medium |
| **Affected feature** | Music media browser, `Code27MusicStorageSensor` (TF-card usage) |
| **Current behaviour** | `Code27Data.music_storage` is populated **only** by `_handle_ws_music_storage`. Each full poll carries the previous value forward (`music_storage = self.data.music_storage if self.data else {}`). The source justifies this with *"No confirmed REST endpoint exists for this yet (only the WebSocket push topic is live-verified)"* (`coordinator.py`) and *"no REST fallback exists for this yet"* (`sensor.py`). |
| **Expected behaviour** | A REST fallback is available and confirmed live, so storage state should not depend on the WebSocket being connected. |
| **Code evidence** | `coordinator.py` — `Code27Data.music_storage` field comment, `_async_update_data`, `_handle_ws_music_storage`; `sensor.py` — `Code27MusicStorageSensor` docstring |
| **CODE27 evidence** | `GET /data/user/music/storage` is listed **CONFIRMED LIVE** in `CODE27-LOCAL-API.md` (with optional `?refresh=1`), and `_recon/responses/_data_user_music_storage.json` is a real captured 31-file response containing `is_loaded`, `root_path`, `music_files[]`, `capacity_total_bytes`, `capacity_used_bytes`. It is operation **C27-061**. |
| **Impact** | If the WebSocket never connects or is permanently lost, the media browser and the storage sensor stay empty indefinitely even though a poll would work. This is the only pollable resource in HASE with **no** polling fallback. |
| **Test impact** | The push path is covered generically by `test_stage_three.py::CoordinatorTests` (stale/duplicate push rejection); there is no test for the absent fallback. |
| **Not fixed** | See backlog **P1-1**. |

---

## CCF-4 — One failing polled endpoint fails the entire poll (MEDIUM)

| | |
|---|---|
| **Severity** | Medium |
| **Affected feature** | All polled entities (system, music, light, alarms, memos, characters, slot, character overview) |
| **Current behaviour** | `Code27Coordinator._poll_once` uses `asyncio.gather(...)` over **14** client calls with no `return_exceptions=True` and no per-endpoint isolation. Any single `Code27Error` propagates and `_async_update_data` raises `UpdateFailed`, so **every** entity goes stale/unavailable. |
| **Expected behaviour** | Either per-endpoint tolerance (keep serving the endpoints that answered) or explicit capability-aware skipping. |
| **Code evidence** | `coordinator.py` — `_poll_once`, `_async_update_data` |
| **CODE27 evidence** | The device's `capabilities` object (captured live in `_data_user_system.json`) demonstrably contains **false** flags on the known device (`admin.*`, `lab.ai_provider`, `character.model_controls`, `chat.audio_debug`, `voice.clone_record_debug`, `lab.system`). CODE27 therefore *can* return a gated feature as unavailable, which would take the whole poll down. |
| **Impact** | On a device with a feature disabled (or during partial API availability), the integration becomes effectively dead rather than degraded. |
| **Acknowledged tradeoff** | `sensor.py::Code27CapabilitiesSensor` states the decision explicitly: *"No feature in this integration is currently gated on these flags … surfaced as diagnostic data so a device with fewer capabilities is at least observable, rather than building gating logic that can't be verified without such a device."* The risk is known and deliberate — but that reasoning covers *gating*, not *poll isolation*, which is the actual defect. |
| **Test impact** | `test_stage_three.py` covers poll/attention semantics but not partial-availability resilience. |
| **Status** | ✅ **Umgesetzt 2026-09-24** via S2 und Abschlusskorrektur — Gather, globale Auth-Prüfung und Zusammenführung pro Endpunkt. Siehe P1-2. |

---

## CCF-5 — Diary "read-only" claim rests on a factually wrong premise (LOW-MEDIUM)

| | |
|---|---|
| **Severity** | Low-Medium |
| **Affected feature** | Diary write support, and the credibility of adjacent in-code decisions |
| **Current behaviour** | `api.py` states: *"Confirmed live: no favorite/reaction/delete endpoint works on the device's local API (all HTTP 404) - only list/get are implemented here, deliberately, so no write action can be offered that would silently do nothing."* |
| **Expected behaviour** | The claim should distinguish the one route that genuinely does not exist from the two that do. |
| **Evidence** | **CODE27:** `POST /data/user/diary/entries/{id}/feedback` returns a **router-level plain-text 404** — genuinely not registered. But `PUT /data/user/diary/entries/{id}` answers a **normal JSON validation error** (`empty_update`) with a valid signature, which proves the route *is* registered; and `DELETE /data/user/diary/entries/{id}` answered **401**, i.e. it exists behind auth. HASE's blanket "all HTTP 404" is true for exactly one of the three routes. |
| **Code evidence** | `api.py` — the `-- diary (read-only on this firmware) --` comment block above `list_diary_entries` |
| **Impact** | No runtime breakage (nothing calls the routes). The consequence is that `C27-008` and `C27-122` are recorded in the permanent ledger as **NOT_IMPLEMENTED** rather than as a justified design decision, because the recorded justification does not hold. A future maintainer reading the comment would wrongly believe diary writing is impossible on this firmware. |
| **Not fixed** | See backlog **P3-1**. |

---

## CCF-6 — Voice-group listing skipped on a premise the research disproves (LOW-MEDIUM)

| | |
|---|---|
| **Severity** | Low-Medium |
| **Affected feature** | `hase.set_character_voice` usability; absence of a voice picker |
| **Current behaviour** | `api.py` deliberately omits the dynamic voice-group listing: *"the audit only confirms the *config key name* and its live behavior via a real device test, never the literal resolved REST path it points to … so there is nothing here to call without guessing a path outright."* |
| **Expected behaviour** | The literal path **is** confirmed, so the endpoint can be called without guessing. |
| **CODE27 evidence** | `GET /data/user/ability/group-keys?ability_type=tts&language=<code>` is **CONFIRMED LIVE**: `inventory.json` lists `GET /data/user/ability/group-keys`; `_recon/responses/_data_user_ability_group_keys_ability_type_tts.json` is a real 200 response with two voice groups; `code27-endpoints.md` documents the query parameters; `CODE27-HASE-CAPABILITY-MATRIX.md` documents the exact call site. |
| **Impact** | HASE cannot offer a voice picker, so a user must source `voice_id`/`group_key` elsewhere before calling `hase.set_character_voice`. The same wrong premise is repeated in HASE's own `docs/CODE27-FEATURE-STATUS.md` as a "known limitation". |
| **Not fixed** | See backlog **P1-3**. |

---

## CCF-7 — Session-scoped credentials are unsupported (LOW)

| | |
|---|---|
| **Severity** | Low (conditional) |
| **Affected feature** | Authentication robustness |
| **Current behaviour** | `pair_confirm` sends only `{pairing_code, challenge_id}` — it never sends or reads `auth_scope`, and the strings `auth_scope`, `session_id` and `local_session_id` appear **nowhere** in the repository. |
| **Expected behaviour** | The contract says pairing returns `auth_scope: permanent \| session` (the latter accompanied by `local_session_id`), and a session-scoped secret **must** pass `session_id` on the handshake (and on requests) or the server returns 401. |
| **Code evidence** | `api.py` — `Code27Client.pair_confirm`, `_sign`, `build_ws_url`; `config_flow.py` — `async_step_pair` |
| **CODE27 evidence** | `CODE27-LOCAL-API.md` §auth (`auth_scope`, `local_session_id`), WebSocket query-parameter table (`[&session_id=…]`); `CODE27-HASE-CAPABILITY-MATRIX.md` §WebSocket implementation rules (*"A session-scoped secret must pass `session_id` or the handshake returns 401"*) |
| **Impact** | HASE does not request session scope, so it should receive a `permanent` credential and work correctly. But if a device or firmware ever returned a session-scoped credential, every signed request **and** the WebSocket handshake would fail with 401, with no recovery path other than re-pairing — and no diagnostic distinguishing it from a generic auth failure. |
| **Verification** | Not live-tested either way; the `permanent`-by-default assumption is unverified. |
| **Not fixed** | See backlog **P2-1**. |

---

## CCF-8 — Dead wrapper with a wrong return shape (LOW, currently unreachable)

| | |
|---|---|
| **Severity** | Low (latent; no runtime path) |
| **Affected feature** | `GET /resources/system/original-portraits/manifest` (C27-068) |
| **Current behaviour** | `Code27Client.get_original_portraits_manifest()` returns `(...).get("data", [])` and is annotated `list[dict[str, Any]]` — but the confirmed payload's `data` is an **object** `{exported_at_ms, portraits[]}`. The method has **no production call site** (only a unit test calls it), and the path is listed in `_UNCONFIRMED_SHAPE_PATHS`, which exempts it from the shape validator and therefore *hides* the mismatch. |
| **Code evidence** | `api.py` — `get_original_portraits_manifest`, `_UNCONFIRMED_SHAPE_PATHS`; `tests/test_bridge_delivery.py::ApiTests.test_original_portraits_manifest_shape_is_not_enforced` |
| **CODE27 evidence** | `_recon/responses/_resources_system_original_portraits_manifest.json`: `{"data": {"exported_at_ms": …, "portraits": [ … ]}}` |
| **Impact** | None today (unreachable). It is a trap for a future maintainer who "reuses the existing helper" and gets a dict where a list is expected. The exemption comment's rationale ("the audit never actually confirmed the shape") is also stale — the shape *is* confirmed. |
| **Not fixed** | See backlog **P2-2**. |

---

## CCF-9 — `music_control` checks the nested failure, `set_music_source` does not (LOW)

| | |
|---|---|
| **Severity** | Low |
| **Affected feature** | Media player source switching |
| **Current behaviour** | `Code27Client.music_control` inspects the response and raises `Code27DeviceError` when the nested `status == "FAILED"`, because the device can report failure inside a `200`/`success:true` envelope. `Code27Client.set_music_source` performs **no such check** and discards the response entirely. |
| **Expected behaviour** | The same failure-in-a-success-envelope detection should apply, since both endpoints belong to the same device action family. |
| **Code evidence** | `api.py` — `music_control` (explicit `Code27DeviceError` branch + explanatory comment) vs `set_music_source` (fire-and-forget) |
| **CODE27 evidence** | The failure-in-a-success envelope is **CONFIRMED LIVE** for `music/control` (`MODE_INACTIVE`, `INVALID_ARGUMENT`). Whether `music/source` can report the same way is **UNKNOWN** — the research marked that endpoint `NOT TESTED`. |
| **Impact** | If the source switch can fail this way, the HA select would appear to succeed while the device did nothing. Unconfirmed, hence Low. |
| **Not fixed** | See backlog **P2-3**. |

---

## Publication / durability finding (HIGH operational risk — not a code defect)

This is not a defect in the provider code, but it defeats the stated purpose of
the two permanent deliverables and must be fixed for them to have any effect.

| | |
|---|---|
| **Finding** | Both permanent artefacts are **git-ignored** and can never be committed. `.gitignore` line 16 is `/docs/*` with explicit allowlists for `de/`, `en/`, `branding/`, `screenshots/` and `examples/` — but **no entry for `code27/`**. `git check-ignore -v` confirms: `.gitignore:16:/docs/*    docs/code27/implemented-api.md` and the same for `implemented-api.yaml`. |
| **Consequence** | The ledger and the reference document exist only on this working copy. They cannot be committed, cannot be reviewed in a pull request, cannot be read by CI, and cannot serve as the "future automated conformance source" they were written to be. A fresh clone of the repository would contain neither. The same applies to the stray audit copy at `docs/code27/CODE27-HASE-CURRENT-IMPLEMENTATION-AUDIT.md` (see DC-9). |
| **Related context** | `.gitignore`'s final rule also ignores the **entire provider** `/custom_components/hase/providers/code27/` under the comment "Private / vendor-specific providers". So the CODE27 provider itself is not tracked either — which is deliberate, but it means neither the implementation nor its ledger is in the published repository. |
| **Required fix (not applied — outside the permitted write set)** | Add a public-docs allowlist so the ledger is tracked: `!/docs/code27/` and `!/docs/code27/**`. Whether the provider directory should also be un-ignored is a product decision, but the *documentation* must be committed for it to be permanent. |
| **Verified** | `git check-ignore -v` on both files; `.gitignore` contents read in full. |

---

# Feature Matrix

| FEATURE | STATUS | CODE EVIDENCE | TEST COVERAGE | GAP |
|---|---|---|---|---|
| Pairing (bootstrap → pair/request → pair/confirm) | IMPLEMENTED | `api.py` `bootstrap`/`pair_request`/`pair_confirm`; `config_flow.py` `async_step_code27_device`/`async_step_pair`/`_async_finish_pairing` | STRONG (`test_stage_three.py::FlowTests`) | `auth_scope` ignored (CCF-7) |
| HMAC signing (REST) | IMPLEMENTED | `api.py` `_sign`, `_call` | STRONG | — |
| Multipart signing (`UNSIGNED-PAYLOAD`) | IMPLEMENTED | `api.py` `_call_multipart` | STRONG (`test_call_multipart_signs_with_unsigned_payload_sentinel`) | — |
| WebSocket signing | IMPLEMENTED | `api.py` `build_ws_url`; `websocket_client.py` | NONE | no test asserts the signed URL |
| Credential persistence / reauth | IMPLEMENTED | `config_flow.py` reauth steps; entry data `user_id`/`local_secret` | STRONG | keyed on `machine_id` (CCF-1) |
| Device identity model | PARTIAL | entry `unique_id` = `machine_id` | PARTIAL (`FlowTests` with a stable id) | `machine_id` treated as durable (CCF-1) |
| Character list & slot selection | IMPLEMENTED | `list_character_settings`, `get_current_slot`, `update_selected_slot`, `select.py` | PARTIAL | `PUT /slot` untested; `cloud_id` not mapped from `slot_number` |
| Character voice assignment | IMPLEMENTED | `update_character_setting`, `hase.set_character_voice` | STRONG | no voice picker (CCF-6) |
| Character portraits (thumbnail → stock → none) | IMPLEMENTED | `get_character_portrait`, `get_cloth_uploaded_models`, `image.py` | STRONG | — |
| Chat text send | IMPLEMENTED | `send_chat_text`, `async_send_chat_reply`, `hase.send_chat_message` | STRONG | — |
| Chat image send | IMPLEMENTED | `send_chat_image`, `hase.send_chat_image` | STRONG | receive/display not implemented; 1 MB device limit |
| Chat reply detection (polling) | IMPLEMENTED | `get_chat_history_snapshot`, `_async_poll_chat` (3 s) | STRONG | does not use `history/range` (C27-038) |
| Chat event emission | IMPLEMENTED | `hase_user_message` / `hase_assistant_message` | STRONG (`DeliveryTests`) | — |
| Chat history cleanup | IMPLEMENTED | `delete_chat_messages`, `async_cleanup_assist_exchange` | **NONE** | TEST DEBT |
| Chat cancel | NOT_IMPLEMENTED | — | NONE | useful; backlog P3 |
| TTS speak | IMPLEMENTED | `tts_audition`, `async_speak_text` | PARTIAL | `FAILED`/timeout branches untested |
| TTS completion semantics | **CORRECT** | polls `tts_audition/state` to `ENDED`/`FAILED` under a 15 s wall clock | PARTIAL | only `ENDED` exercised |
| Memory cards read/edit | IMPLEMENTED | `list_memory_cards`, `update_memory_card`, 2 services | STRONG (edit) / **NONE** (list) | list untested |
| Diary read | IMPLEMENTED | `list_diary_entries`, `get_diary_entry`, 2 services | PARTIAL / **NONE** | `get_diary_entry` untested |
| Diary write | NOT_IMPLEMENTED | — | NONE | source justification wrong (CCF-5) |
| Memos (todo + count) | IMPLEMENTED | `list_memos`, `create_memo`, `update_memo`, `delete_memo`, `todo.py` | PARTIAL (read) / **NONE** (write) | all writes untested |
| Alarms CRUD + switches | IMPLEMENTED | `list/create/update/delete_alarm`, `enable/disable_alarm`, `switch.py` | PARTIAL (smoke) / **NONE** (switches) | day-mask untested |
| Proactive engine | NOT_IMPLEMENTED | — (the word "proactive" is absent from the repo) | NONE | scope boundary, not a defect |
| LED master switch | IMPLEMENTED | `get/set_light_master_switch`, `light.py`, notification restore | PARTIAL | — |
| LED ad-hoc colour/effect (preview) | IMPLEMENTED | `preview_light_color`, `stop_light_preview`, `const.LIGHT_EFFECT_TO_TYPE` | PARTIAL | payload shape not asserted; no readback exists |
| LED projects (list/apply) | IMPLEMENTED | `list_light_projects`, `get_applied_light_project`, `apply_light_project`, `select.py` | PARTIAL / **NONE** (apply) | apply untested |
| LED project editing | NOT_IMPLEMENTED | — | NONE | 7 operations |
| Music playback control | IMPLEMENTED | `music_control` (+ nested-failure detection); `set_music_play_mode`, `set_music_sound_effect` services (M12) | STRONG | — |
| Music source switch | IMPLEMENTED | `set_music_source` | **NONE** | no nested-failure check (CCF-9) |
| Music upload / delete | IMPLEMENTED | `upload_music`, `delete_music_file`, 2 services | STRONG | — |
| Music media browser | PARTIAL | `media_player.py` `_group_music_files` | PARTIAL (tests use the wrong premise) | absolute-path grouping (CCF-2) |
| Music storage inventory | NOT_IMPLEMENTED | — | NONE | WS-only; REST confirmed live (CCF-3) |
| Bluetooth state | IMPLEMENTED | `get_bluetooth_state`, `Code27BluetoothConnectedBinarySensor`, WS | PARTIAL | no control; `paired_devices[]` unexposed |
| Audio route state | IMPLEMENTED | `get_music_audio_route` | PARTIAL | no entity reads it; no control endpoint exists |
| Sound play/stop | IMPLEMENTED | `play_sound`, `stop_sound`, `button.py` | **NONE** | TEST DEBT |
| Local resources (portraits, thumbnails) | IMPLEMENTED | `get_character_portrait`; see the resource ledger | STRONG | — |
| Chat images/audio resources | NOT_IMPLEMENTED | `get_chat_image` (test-only) | NONE | no consumer |
| Vision / camera | **NOT CLAIMED** | no camera/VLM code; only `capabilities` surfaced read-only | NONE | correctly absent |
| Capabilities | READ-ONLY | `Code27CapabilitiesSensor` | PARTIAL | no gating; whole-poll fragility (CCF-4) |
| Admin / AI provider | CAPABILITY_GATED | — | NONE | device flags false |
| Error handling (401/409/envelope/nested-FAILED) | IMPLEMENTED | `_parse_json_response`, `_validate_response_data`, `Code27SlotMismatchError`, `Code27DeviceError` | STRONG (`ResponseTests`, `ApiTests`) | no per-endpoint poll isolation (CCF-4) |

---

# Detailed Findings

## Authentication

**Status: correct, with one gap.**

Verified against the final contract, field by field:

| Contract element | Implementation | Result |
|---|---|---|
| `stringToSign = METHOD\|path\|timestamp\|nonce\|payloadMode\|bodyHash\|userId` | `api.py::_sign` builds exactly this join | ✅ |
| Path includes the `/v1/api` prefix | `url = ("/v1/api" + path).split("?", 1)[0]`, duplicate slashes collapsed | ✅ |
| `payloadMode` = `empty` / `json` / `multipart` | selected by body presence / multipart path | ✅ |
| `bodyHash` = SHA-256 of the **exact bytes sent** | JSON serialized once with `separators=(",", ":")` and the same string used for hashing and transmission | ✅ |
| `bodyHash` = SHA-256 of `""` for empty bodies | `_sha256_hex("")` | ✅ |
| Multipart uses the literal `UNSIGNED-PAYLOAD` | `_call_multipart` | ✅ |
| Six `X-Local-*` headers | all six emitted | ✅ |
| Three signature-exempt routes | `_UNSIGNED_PATHS` | ✅ |
| `user_id` from bootstrap | `pair_confirm` calls `bootstrap()` when unknown; the id echoed by `pair/confirm` is deliberately ignored | ✅ |
| Timestamp (epoch seconds) + 16-char nonce | `str(int(time.time()))`, `random.choice` over `[0-9a-z]` × 16 | ✅ |
| WebSocket signs the same string, passed as query params | `build_ws_url` | ✅ |
| `session_id` for session-scoped secrets | **absent** | ⚠️ **CCF-7** |
| Expired credential → reauth | 401 → `Code27AuthError` → `ConfigEntryAuthFailed` → HA reauth; a *different* device is rejected | ✅ |
| Credential renewal | reauth re-runs pairing and updates `user_id`/`local_secret` | ✅ |

No semantic mismatch was found in the signing scheme itself. The only
authentication gap is the unhandled `auth_scope`/`session_id` dimension.

**Nonce randomness note (for completeness):** `random.choice` is not a
cryptographic RNG. For a LAN-local HMAC scheme whose replay window is bounded by
the signature timestamp/nonce pair and where the secret never leaves the host,
this is a hardening opportunity rather than an exploitable defect — but it is
worth noting that `secrets.choice` would be strictly better. This is recorded as
an observation, not as a security finding: no security vulnerability was
identified in this audit.

## Identity

Three distinct namespaces exist and HASE conflates exactly one of them:

| Namespace | Meaning per final research | HASE usage | Verdict |
|---|---|---|---|
| `slot_number` | runtime/profile selection | slot select entity, chat slot, `?scope=chat`; **never** treated as device identity | ✅ correct |
| `cloud_id` | stable character identity | not auto-mapped; memory/diary services require the caller to pass `character_id`, documented in `strings.json` as the cloud_id | ✅ correct |
| `machine_id` | **transient pairing token** | config-entry `unique_id`, every entity `unique_id`, reauth device matching, remote-recovery verification, `X-DEVICE-ID`, `device_id` for `PUT /data/user/character` | ⚠️ **CCF-1** (the `device_id` use is contract-required; the identity use is not) |
| `app_id` (`com.code27.app`) | companion application identity, not a hardware serial | **the string does not occur anywhere in HASE** | ✅ no stale assumption |
| DHCP hostname serial | best available stable identifier, with a documented DHCP caveat | not used | ✅ (no false claim, and no use) |
| MAC address | network metadata, not application identity | only surfaced as a Bluetooth `device_address` attribute | ✅ correct framing |

## WebSocket

Implemented correctly, with one structural gap.

* **Endpoint/auth:** `ws://<host>:8081/v1/ws/channel` with the same HMAC passed as
  query parameters — matches the contract.
* **Lifecycle:** started from `Code27Coordinator.async_start_realtime`, stopped
  from `async_stop_realtime`; `async_stop_realtime` cancels and awaits in-flight
  tasks and then stops the socket.
* **Frame handling:** `{"type": …, "data": {…}}`; fragmented frames are
  reassembled by aiohttp (the 31-file `music_storage` payload arrives as two
  frames and is handled correctly); binary frames are ignored; malformed JSON is
  dropped; each handler exception is caught individually.
* **Single outstanding receive:** satisfied — one `async for msg in ws` consumer.
* **Reconnect:** exponential back-off 2 s → 60 s cap, retried indefinitely;
  `heartbeat=30`. Reconnect is safe because the device re-sends a full per-topic
  snapshot on every connect, so no resubscription protocol is needed.
* **Auth-failure handling:** no re-pair path on the socket; REST 401 still drives
  HA reauth, which reloads the entry and therefore the socket.
* **State propagation:** a per-field monotonic revision counter plus a two-key
  freshness comparison (`updated_at_ms`, then `version`) guarantees an older HTTP
  response cannot overwrite a newer push. This is a genuinely careful
  implementation of the push/poll race.
* **Fallback polling:** present for 5 of the 6 subscribed topics. **Missing for
  `music_storage`** (CCF-3).
* **`chat_history`:** **correctly not subscribed.** `websocket_client.py` states
  *"There is no push event for new chat messages - that's still polled separately
  and faster"*, matching the final research conclusion that the topic is dead.
* **Topics subscribed (6 of 7):** `music_storage`, `music_page`,
  `music_playback`, `music_source`, `music_audio_route`, `bluetooth_state` — all
  six names match the research exactly.

## Characters

* **Profile mapping:** `GET /data/user/character/list` is polled and mapped to a
  slot select entity (`slot_number`), per-slot portrait image entities
  (`selected_cloth`), and an active-character sensor (`current_slot.slot_number`).
* **Semantic distinction preserved:** `slot_number` drives runtime selection and
  `cloud_id` is stable character identity; neither becomes device identity.
* **Consumed subset:** `slot_number`, `name`, `selected_cloth`, `voice_id`,
  `group_key`. Available but unused: `cloud_id`, `sex`, `animation_intensity`,
  `tts_volume`, `available_cloths`, `original_cloths`, `custom_cloths`,
  `link_other_original_cloth`, `character_prompt`.
* **Outfit/cloth:** read via the character list and the uploaded-models endpoint;
  `GET /data/user/outfit/context` is unused. Cloth download/upload jobs are not
  implemented. `PUT /data/user/character` is used only for `voice_id`/`group_key`
  through the public service, although the client allowlist is wider.
* **Duplicates handled:** two character slots sharing a display name are
  disambiguated by appending the slot number.

## Chat

* **Text submission:** `POST /data/user/chat/text` with `{text, slot[, language]}`;
  the returned `chat_id` is registered as HASE's own output before polling can
  observe it, and the reservation is deliberately **kept** on an ambiguous
  failure (the device may have accepted the write even though the acknowledgement
  was lost).
* **Asynchronous acknowledgement:** handled correctly — the acknowledgement is
  never treated as completion; the reply is detected by the chat poll and emitted
  as `hase_assistant_message`.
* **`chat_id`:** used as the only reliable identifier. The reason is documented at
  length: command-classified messages keep `history_id == 0`, and sending
  `{"history_ids": [0]}` would delete **every** zero-id message at once.
* **History retrieval / count:** whole-day reads plus per-slot `chat_id` dedupe.
  `GET /data/user/chat/history/count` — the multi-slot batch form — is used
  (correctly **without** a `slot` parameter) for the character-overview card, and
  is deliberately **not** the source of the message events.
* **Multi-slot count semantics:** understood. `Code27Data.character_overview`
  merges `[{slot, slot_id, messages[]}]` with the polled slot list and uses only
  `messages[-1]`. `slot_id` is available and unused.
* **Polling strategy:** a dedicated 3-second timer (`CHAT_POLL_INTERVAL`),
  separate from the main poll, with a lock, an in-flight guard, and a late-response
  guard so a slow reply cannot replace another character's state.
* **Change detection:** the bounded `latest` endpoint is explicitly rejected as a
  command-delivery mechanism in the source (*"a burst of messages can move unseen
  commands outside its window"*), and whole-day reads from the last-seen date are
  used instead, with the `latest` window only as a fast path when the previous
  last `chat_id` is still present. This is the correct, conservative choice and it
  is well tested.
* **Best-known mechanism:** **not used.** `GET /data/user/chat/history/range`
  (C27-038) is CONFIRMED LIVE and fetches an entire date range in one call; HASE
  issues one request per day instead. An efficiency gap, not a correctness defect
  — the per-day loop provably covers every selected day.
* **Metadata / VLM:** `content_type` and any VLM metadata are **not** consumed.
  Chat history is read for `chat_id`/`role`/`content`/`created_at` only. Raw
  vision/camera functionality is correctly **not** claimed anywhere.
* **Error handling / timeouts:** a failing chat poll logs at debug and returns,
  leaving the previous state intact — a deliberate, safe degradation.

## TTS

Full lifecycle implemented and semantically complete:

```
resolve active slot → verify expected_slot → resolve voice_id/group_key
   → POST /data/user/tts_audition {voice_id (STRING), text, group_key}
   → poll GET /data/user/tts_audition/state every 0.35 s within a 15 s wall clock
   → ENDED = success | FAILED = failure | timeout = logged, never silent
```

* **`success:true` means accepted, not completed** — HASE does **not** make the
  mistake the brief warns about. It polls to a terminal state and logs
  `tts_ok`/`tts_failed`/`tts_timeout` accordingly.
* **`voice_id` is a string** — passed through as a string and additionally
  type-validated as a string on read. No integer trap.
* **No slot-addressed endpoint** → HASE re-verifies the active slot immediately
  before speaking and **suppresses** a stale request rather than speaking through
  a newly selected character. A thoughtful correctness guard.
* **No voice** → `tts_no_voice` is logged and the endpoint is not called at all.
* **Timeout choice:** 15 s matches the device web UI's own audition timeout, and
  the wall-clock bound replaced an earlier poll-count loop that could in the worst
  case have run for minutes. The fix is sound.
* **Gap:** only the `ENDED` branch is tested; `FAILED` and the timeout branch are
  not.

## Memory

* **Read:** `GET /data/user/memory/cards` via `hase.get_memory_cards`
  (`SupportsResponse.ONLY`), passing `characterId` (the cloud_id), `page`,
  `pageSize`. The payload is returned whole.
* **Write:** `PUT /data/user/memory/cards/{id}` via `hase.update_memory_card`,
  sending only the supplied fields.
* **Distinction honoured:** readable data (list) and mutating operations (edit) are
  exposed separately, and **delete is not implemented at all**.
* **Known trap preserved:** the device permanently sets `isUserEdited: true` on
  the first successful edit, even for identical content, with no undo — HASE's
  service description warns the user *before* the edit.
* **Dead code:** `Code27Client.get_memory_card` (C27-053) has no call site at all.

## Diary

* **Read:** `GET /data/user/diary/entries` and `.../{id}` via two
  `SupportsResponse.ONLY` services; `characterId` must be the cloud_id.
* **Write:** not implemented. The reason recorded in the source is **wrong**
  (CCF-5): only the `feedback` route is genuinely absent (404 at router level);
  `PUT` demonstrably exists and `DELETE` answered 401.
* **Generation metadata / relationships:** diary entries expose `cloud_id`
  relationships; HASE passes the character id through and does not interpret or
  join relationship data across entries.

## Memos

* **Fields:** `id`, `title`, `body`, `memo_type`, `is_expired`, `next_fire_at_ms`.
* **Read:** polled, mapped to a todo entity (`uid` = memo `id`, `summary` =
  `title`, `description` = `body`, `due` from `next_fire_at_ms`, `status` from
  `is_expired`), plus a count sensor.
* **Write:** create / update / delete implemented.
* **Semantics preserved:** `is_expired` is a **time-computed** field, not a
  completion flag — writing it back had no visible effect on the device, and
  CODE27's own memo editor has no "complete" action. HASE therefore **rejects** a
  pure status change with an explicit error instead of silently doing nothing.
  This is exactly the right handling of a misleading field.
* **Gap:** memo creation writes only `title`, `body` and `memo_type: "static"`, so
  a reminder time is never set from HA; a memo created from HA behaves as a note.
  No test covers any memo write.

## Alarms

* **Request semantics:** `{alarm: {hour, minute, label, repeat_mask, enabled}}`
  with `hour` 0–23 and `minute` 0–59; `repeat_mask` is a bitmask from
  `ALARM_DAY_BITS` (`sun=1 … sat=64`).
* **Repeat-mask behaviour:** built correctly for create and update; read back and
  exposed as a switch attribute. It is **not** written by the enable/disable path
  (which uses the dedicated endpoints), which is correct.
* **Not inferred from capability alone:** `lab.alarm` is enabled on the device, but
  HASE's alarm support is a genuine implementation, not an assumption — list,
  CRUD, per-alarm switch entities with dynamic add/remove, and a count sensor.
* **Gaps:** the HA update service requires `hour` and `minute`, so a partial change
  always rewrites the whole alarm; the day-mask mapping is asserted by **no** test;
  the enable/disable switch path is **entirely untested**.

## Proactive

* **Config reading:** **not implemented.** `GET /data/user/proactive/config`
  (C27-063) is CONFIRMED LIVE and unused; the word "proactive" does not occur
  anywhere in the HASE repository.
* **Config writing:** not implemented (C27-126); additionally capability-gated
  (`admin.proactive_config` = false).
* **Triggers / inhibitors / thresholds / cooldowns:** not read, not evaluated, not
  surfaced.
* **Provider exposure:** none. No HA entity, service or diagnostic exposes
  proactive state.
* **Coordination logic:** none. HASE does not coordinate proactive behaviour in
  either direction.
* **Assessment:** HASE makes **no claim** to provide proactive coordination, so
  this is a scope boundary, **not** a correctness bug. It is recorded as a
  deliberate P3 opportunity only.

## LED

* **Readable configuration:** LED projects are listed (C27-049) and the applied
  project is read (C27-051). `ledCount` is read from the first project that
  reports it, falling back to **17**, which matches the known device.
* **Reactive configuration:** **not consumed.** The `reactive` / `normal` /
  `segments` blocks only exist on the single-project GET (C27-050), which HASE
  does not call. HASE does not misrepresent CODE27's reactive-lighting
  functionality as its own.
* **RGB / brightness / style-effect:** implemented through the **draft preview**
  channel (C27-099) with `primaryColor {r,g,b}`, `brightness` 0–255 and an effect
  enum from `LIGHT_EFFECT_TO_TYPE`
  (`STATIC`/`BREATHE`/`PULSE`/`BLINK`/`CHASE`, surfaced in HA as
  static/breathe/pulse/blink/chase; legacy German service values remain accepted).
* **Projects/scenes:** selectable (C27-095) and read back (C27-051); never created,
  edited or deleted.
* **Write operations:** master switch on/off (C27-093), preview render (C27-099),
  preview stop (C27-100), project apply (C27-095).
* Status separation, explicitly:

| Aspect | Status |
|---|---|
| READ IMPLEMENTED | project list, applied project, master switch, `ledCount` |
| WRITE IMPLEMENTED | master switch, ad-hoc colour/effect, project apply |
| KNOWN FROM CODE ONLY | the synthesized preview project's acceptance by the device (the research executed no writes); the stored-project preview/compile family |
| LIVE TESTED | not by this audit — static analysis only |
| NOT LIVE TESTED | preview payload shape; `apply`; project create/update/delete/compile |

* **Quirk:** CODE27 has **no readback** for the preview channel, so the light
  entity's colour/effect attributes describe what HA last requested. HASE states
  this plainly rather than pretending they are device-confirmed state.

## Music

* **Playback:** full transport control + volume + explicit track selection, with
  the nested-failure trap handled (C27-104).
* **Controls:** play/pause/next/previous/volume-set/select-source/play-media/browse.
* **Source:** read (C27-060) and written (C27-105); `LOCAL_TF` and `BLUETOOTH`.
* **Page:** read (C27-058) → "music mode active" binary sensor. Operationally
  relevant, because several actions fail with `MODE_INACTIVE` while the music
  screen is closed, and HASE translates that into actionable guidance.
* **Storage:** **not consumed via REST** — only via the WebSocket topic (CCF-3).
* **Media inventory / per-file metadata:** `music_files[].path` and `.name` are
  consumed from the WebSocket payload for the browser tree and for `track_id`;
  `size_bytes` and `duration_ms` are available and unused.
* **WebSocket updates:** 5 music-related topics subscribed and merged with the
  poll under a freshness guard.
* **Polling fallback:** present for every music topic **except** `music_storage`.
* **Upload/delete:** both implemented — multipart with a repeated `files` field,
  and the returned authoritative `path` reused verbatim for playback.
* **Play mode / sound effect (M12):** `set_music_play_mode` and `set_music_sound_effect`
  HA services added (2026-09-24); both wrap `music_control` with `SET_PLAY_MODE` /
  `SET_SOUND_EFFECT` actions respectively.

## Storage

| Concern | HASE consumption |
|---|---|
| total capacity | consumed from the WS payload (`capacity_total_bytes`) → storage sensor |
| used capacity | consumed (`capacity_used_bytes`) → storage sensor percent |
| free capacity | **not computed** (implied by total − used, never derived) |
| `root_path` | consumed as a sensor attribute only; **not** used to normalise file paths — the root of CCF-2 |
| per-file inventory | `music_files[].path`/`name` consumed for the media browser |
| removable-volume information | **not consumed.** The research observed a removable-volume-style path (`/storage/142E2DF92E2DD4900/…`) and `is_loaded`. HASE exposes `is_loaded` as an attribute but infers **no USB workflow** from it — correctly so, since no official USB workflow was established. |

## Bluetooth

* **Readable state:** fully consumed (`is_connected`, `device_name`,
  `device_address`, `paired_device_count`, `is_bluetooth_enabled`,
  `local_device_name`) and exposed through a diagnostic binary sensor plus
  attributes.
* **Control:** **not implemented.** The only Bluetooth write in the inventory is
  "forget device" (C27-103), which is unused. Notably there is **no pairing
  endpoint in the inventory at all**, so a HASE-side pairing workflow could never
  complete — the read-only scope is well founded.
* **WebSocket updates:** `bluetooth_state` subscribed; poll fallback present.
* **HASE exposure:** binary sensor + attributes; `paired_devices[]` is available
  but not exposed (only the count is).

## Audio

* **Audio-route state:** polled and push-updated (`route`, `wired_headset`), held
  in `Code27Data` — but **no entity reads it**, so it is currently dead state.
* **Control:** **none exists** in the inventory — there is no endpoint to set the
  route. HASE therefore exposes no control, which is correct rather than a gap.
* **WS updates:** `music_audio_route` subscribed; poll fallback present.
* **HASE exposure:** none (state retained internally only).
* **Sound play/stop:** two button entities (C27-107/C27-108), both untested.
* **Chat audio** (`/resources/user/chat-audio`, `-bundle`): not implemented; the
  string `chat-audio` does not occur in the repository.

## Resources

Two of five proven resources are implemented:

| Resource | HASE |
|---|---|
| original-portrait PNG | **IMPLEMENTED** as the stock fallback; the reconstructed path matches the manifest's `portrait_path` exactly |
| custom cloth thumbnail | **IMPLEMENTED** as the preferred source; the `{slot}_{cloth}_{stem}` rule matches the phase-4 download evidence (`5/2/Ginny.vrm` → `thumbnails/5_2_Ginny.png`, HTTP 200) |
| uploaded-model metadata / VRM relationships | **PARTIAL** — only `slot_number`/`cloth_id`/`filename` consumed; `relative_path`, `size_bytes`, `state`, `root`, `total` unused; the VRM binary itself is not HTTP-retrievable |
| chat image | **NOT_IMPLEMENTED** — client method exists, no consumer (test-only) |
| chat audio / bundle | **NOT_IMPLEMENTED** |

No guessed resource path is present in production code: the two paths HASE
actually requests are both backed by captured live evidence. The manifest's own
`portrait_path` values begin
`/v1/api/resources/system/assets/original-portraits/…`, which is exactly what
HASE reconstructs.

## Vision

HASE can observe **only** these vision-adjacent facts, and claims nothing more:

| Topic | Finding |
|---|---|
| `feature.face_tracking_turret` capability | present in the polled `capabilities` object and surfaced **read-only** as diagnostic data (`Code27CapabilitiesSensor`). HASE does **not** gate, enable or interpret it, and exposes no face-tracking entity. |
| Proactive vision-related triggers | not read at all — `GET /data/user/proactive/config` is unused (C27-063), so HASE cannot observe vision triggers. |
| VLM metadata in chat history | present in the payload but **not consumed**. HASE reads only `chat_id`/`role`/`content`/`created_at`. |

Confirmed **not** claimed anywhere in HASE (verified by repo-wide search): raw
camera frames, snapshots, video stream, camera HAL, detector output, camera
process ownership. The only occurrences of the word "camera" are HA-side service
field descriptions for `hase.send_chat_image` ("e.g. from a `camera.snapshot`
action"), which describe where the *image file* comes from on the Home Assistant
side and make no claim about CODE27's camera.

## Capabilities

* **Discovery:** `GET /data/user/system` → `capabilities` object, every poll.
* **Representation:** count of enabled flags as the sensor state; the raw dict as
  an attribute. The device's actual flags on the known device include
  `admin.proactive_config: false`, `admin.service_install: false`,
  `admin.service_uninstall: false`, `admin.system_settings: false`,
  `admin.user_settings: false`, `character.memory: true`,
  `character.model_controls: false`, `chat.audio_debug: false`,
  `feature.face_tracking_turret: true`, `lab.ai_provider: false`,
  `lab.alarm: true`, `lab.light_editor: true`, `lab.memo: true`,
  `lab.system: false`, `mode.lab: true`, `mode.music: true`,
  `voice.clone_record_debug: false`, `voice.group: true`.
* **Gated endpoint handling:** none — no endpoint is skipped on the basis of a
  flag. This is an explicit, documented decision (the sensor docstring explains
  that gating logic could not be verified without a device that has a flag
  disabled).
* **Unavailable feature handling:** the weak point — a gated endpoint's failure
  takes the **whole poll** down (CCF-4).
* **Feature exposure:** capabilities are observable, not enforced. No feature is
  presented as generally usable when the device gates it; conversely, no feature
  is silently disabled either.
* **21 operations are capability-gated** on the known device (see the ledger's
  dedicated table). **A server-side capability gate is not a HASE bug.**

## Admin / AI provider

* 11 admin/AI-provider operations are capability-gated (`admin.user_settings`,
  `admin.system_settings`, `lab.ai_provider` all false on the known device).
* HASE contains **no** admin UI and **no** AI-provider management. It does not
  attempt bypasses, does not probe alternate routes, and **does not contact
  vendor cloud services**.
* No support is claimed for anything the current device capability prevents from
  being verified. The AI-provider family is additionally out of scope on
  principle, because it would mean handling third-party provider credentials.

## Error handling

Assessment: **mostly robust and specific, with one structural weakness.**

| Condition | Behaviour | Verdict |
|---|---|---|
| 401 | `Code27AuthError(code)` → `ConfigEntryAuthFailed` → HA reauth | robust |
| 403 | `Code27Error` with the device `code`/`message` | robust |
| 404 | `Code27Error`; on the portrait path, treated as "no portrait for this slot/cloth" | robust, intentional |
| 400 | `Code27Error`; the 1 MB image limit is additionally pre-checked client-side | robust |
| 409 slot mismatch | dedicated `Code27SlotMismatchError` with `current_slot`/`request_slot` → actionable service error | robust — the best-handled case |
| 5xx | `Code27Error` (message from body or `http_<status>`) | adequate |
| timeout | `Code27ConnectionError` via `asyncio.timeout`; writes are never blindly retried after an ambiguous timeout | robust |
| unavailable device | `UpdateFailed`; optional opt-in remote IP recovery (read-only, 30 s cooldown, locally verified by `machine_id`) | robust |
| expired credentials | distinct `Code27AuthError` path, separate from `Code27ConnectionError` | robust — correctly not swallowed into a generic error |
| malformed response | `invalid_response: …` `Code27Error`; a 401 with a non-JSON body still yields `Code27AuthError` | robust |
| missing fields | tolerated via `.get()` defaults for optional data; required identifiers (list item `id`) are enforced | robust |
| unexpected enum values | playback `status` silently → `IDLE`; TTS state → keeps polling until timeout | **weak** — a silent fallback, though both choices are safe (no crash, no false success) |
| WebSocket disconnect | back-off reconnect; per-handler exception isolation | robust |
| WebSocket reconnect | full-snapshot semantics make it safe; **no** re-pair on socket 401 | adequate |
| partial API availability | **not handled** — one failing endpoint fails the whole poll | **CCF-4** |

No instance of a blanket `except Exception: pass` was found on a data path. The
broad handlers that do exist are either documented as deliberate (bridge output
must never take the entry down) or are per-handler/per-payload guards that log.

---

# Stale Assumptions

Actual stale assumptions found in **current** code, comments, tests and
documentation — each contradicted by the final research. Theoretical problems
that do not exist in the code are not listed.

| # | Stale assumption | Where | Contradicted by | Effect |
|---|---|---|---|---|
| SA-1 | `machine_id` is a stable device identity suitable for `unique_id` and reauth matching | `config_flow.py` `_async_finish_pairing`/`async_step_reauth`; every entity module's `unique_id` | `CODE27-LOCAL-API.md` §4.11 + `CODE27-HASE-CAPABILITY-MATRIX.md` §5: *"machine_id is a transient pairing token — do not key state on it"* | CCF-1; identity-forking risk after re-pair |
| SA-2 | "No confirmed REST endpoint exists" for `music_storage` | `coordinator.py` `Code27Data.music_storage` comment; `sensor.py` `Code27MusicStorageSensor` docstring | `GET /data/user/music/storage` is CONFIRMED LIVE with a captured 31-file response (C27-061) | CCF-3; no poll fallback |
| SA-3 | "no favorite/reaction/delete endpoint works on the device's local API (all HTTP 404)" for diary writes | `api.py` diary comment block | Only `POST …/feedback` 404s; `PUT …/{id}` returns a normal JSON validation error and `DELETE …/{id}` answered 401 | CCF-5; C27-008/C27-122 mis-justified |
| SA-4 | The voice-group listing's REST path "was never confirmed" | `api.py` comment above the omitted voice-group support; repeated in `docs/CODE27-FEATURE-STATUS.md` "Bekannte Grenzen" | `GET /data/user/ability/group-keys?ability_type=tts&language=` is CONFIRMED LIVE with a captured 200 response (C27-018) | CCF-6; no voice picker |
| SA-5 | `music_files[].path` is relative to the music root | `media_player.py` `_group_music_files` (+ its own "flagged for live verification" comment) | the captured payload shows absolute `/storage/<vol>/code27/music/…` paths | CCF-2; empty-named folder |
| SA-6 | The original-portraits manifest's shape is unconfirmed, so it is exempt from validation | `api.py` `_UNCONFIRMED_SHAPE_PATHS` + comment | The shape is confirmed: `{exported_at_ms, portraits[]}` — an object, not a list | CCF-8; the exemption hides a wrong return type |
| SA-7 | `GET /data/user/cloth/uploaded-models` returns a bare list | **not present** — the wrapper correctly expects `{models: [...]}` | verified against the captured response | none (checked, correct) |
| SA-8 | `voice_id` is an integer | **not present** — handled and validated as a string | `voice_id` is a string per research | none (checked, correct) |
| SA-9 | `chat_history` is a usable WebSocket chat-change source | **not present** — explicitly documented as absent and not subscribed | the topic is DEAD | none (checked, correct) |
| SA-10 | `success:true` on `tts_audition` proves playback completed | **not present** — completion comes from state polling | `success:true` means accepted | none (checked, correct) |
| SA-11 | `com.code27.app` is the device runtime | **not present** — the string does not occur in the repository | `app_id` is the companion app's identity | none (checked, correct) |
| SA-12 | `/v1/api` is a device-only namespace | **not present as a claim.** HASE reuses the same path set against the cloud relay for opt-in IP recovery only, which is consistent with the research (the relay accepts the same LAN-HMAC signature) and is documented as such | the relay exposes the same path set | none (checked, correct) |
| SA-13 | USB provides ADB / device mode | **not present** — no `usb`, `adb` or `fastboot` reference exists in HASE | no PC USB enumeration, no ADB, no fastboot | none (checked, correct) |
| SA-14 | OTA can deliver `com.clt01.display` | **not present** — no OTA reference exists in HASE | the companion updater is associated with `app_id=com.code27.app` and was not established as a delivery mechanism for `com.clt01.display` | none (checked, correct) |
| SA-15 | Port 8081 belongs definitively to `com.clt01.display` | **not present** — HASE never attributes the port to a process | ownership not established | none (checked, correct) |
| SA-16 | VLM metadata equals raw camera access | **not present** — VLM metadata is not consumed at all | no camera endpoint exists in the inventory | none (checked, correct) |
| SA-17 | Capability-gated APIs are generally usable | **not present as a claim.** HASE never presents gated operations as usable; it exposes the flags read-only and documents the absence of gating | 21 operations are gated on the known device | none (checked, correct) |
| SA-18 | Undocumented endpoint guesses retained in production code | **none found.** The only non-inventory path in production use (the stock portrait PNG) is reconstructed exactly from the manifest's `portrait_path`, and the thumbnail rule matches the phase-4 download evidence | — | none (checked, correct) |

Net result: **six** genuine stale assumptions (SA-1 … SA-6), all with concrete
code evidence; twelve checked items are already correct.

---

# API Coverage Summary

The complete, canonical coverage table is the permanent ledger:

* **`docs/code27/implemented-api.yaml`** — all **127** operations exactly once,
  with stable `C27-001 … C27-127` ids, plus a separate WebSocket section (7
  topics) and a resource section (5 proven resources).
* **`docs/code27/implemented-api.md`** — the same 127 operations in
  human-readable form, with implementation locations, consumed fields and tests.

| Status | Count | Where documented |
|---|---|---|
| IMPLEMENTED | 51 | `implemented-api.md` §4 (detailed), YAML `operations` |
| PARTIAL | 0 | — (justified in `implemented-api.md` §1) |
| NOT_IMPLEMENTED | 54 | `implemented-api.md` §5 (grouped by reason) |
| INTENTIONALLY_UNSUPPORTED | 1 | `implemented-api.md` §7 (C27-090, proven absent) |
| CAPABILITY_GATED | 21 | `implemented-api.md` §6 (with device state and consequences) |
| NOT_RELEVANT_TO_HASE | 0 | — |
| REMOVED_UPSTREAM | 0 | — |

**Corrections to the inherited ledger, recorded rather than silently merged:**
`C27-127` was `PUT /v1/ws/channel`, which is **not** one of the 127 operations,
while the real operation `PUT /data/user/slot` was **omitted**. `C27-127` now
names `PUT /data/user/slot`; `C27-001 … C27-126` are unchanged so that no id was
renumbered. The WebSocket channel is documented in the dedicated `websocket:`
section instead of occupying a REST id.

---

# Test Debt

Every implemented/partial area with insufficient behavioural coverage. This list
explains why 13 implemented operations carry **NONE** and 24 only **PARTIAL**
coverage.

| # | Area | Operations | Current coverage | Required test |
|---|---|---|---|---|
| TD-1 | Character slot switching | C27-127; `Code27CharacterSlotSelect` | **NONE** | body `{slot_number, scope:"chat"}`; the chat cursor re-baselines on switch |
| TD-2 | Alarm switches | C27-080, C27-081; `Code27AlarmSwitch` | **NONE** | enable/disable calls; dynamic add/remove; `force_remove` on vanish |
| TD-3 | Alarm day mask | C27-079, C27-120 | PARTIAL (smoke only) | `repeat_mask` composition from `ALARM_DAY_BITS`; required `hour`/`minute`; full rewrite on update |
| TD-4 | Memo writes | C27-102, C27-125, C27-012; `Code27MemoTodoList` | **NONE** | request bodies; that a pure status change raises rather than no-op'ing |
| TD-5 | Light project apply | C27-095; `Code27LightProjectSelect` | **NONE** | the id used for apply; duplicate-name disambiguation; `projectId` readback |
| TD-6 | Music source switch | C27-105 | **NONE** | body `{source, request_id}`; nested-failure handling (absent today, CCF-9) |
| TD-7 | Sound buttons | C27-107, C27-108 | **NONE** | button → client call |
| TD-8 | Chat history cleanup | C27-005; `async_cleanup_assist_exchange` | **NONE** | `chat_ids` body; reply-hunting "strictly next after" ordering; the single 2 s retry |
| TD-9 | Memory card list | C27-052 | **NONE** | query construction (`characterId`/`page`/`pageSize`) and pass-through |
| TD-10 | Single diary entry | C27-045 | **NONE** | query parameter and pass-through |
| TD-11 | TTS terminal states | C27-066 | PARTIAL | the `FAILED` branch and the 15 s timeout branch |
| TD-12 | Media player property mapping | C27-059 | PARTIAL | `state`, `media_title`, `media_duration`, `media_position`, `volume_level` from a playback payload |
| TD-13 | Light preview payload | C27-099 | PARTIAL | the synthesized project object's shape and `ledCount` |
| TD-14 | Media browser path handling | `media_player.py` | **misleading** | tests must use **absolute** paths like the live payload, not relative ones (see CCF-2) |
| TD-15 | WebSocket signed URL | `build_ws_url` | **NONE** | the canonical string and query parameters |
| TD-16 | Partial API availability | poll assembly | **NONE** | one failing endpoint must not blank every entity (see CCF-4) |

Reminder: a test that imports a module, or mocks an HTTP 200 without asserting
request/response semantics, is not coverage. `tests/_hase_import_smoke.py` is a
lifecycle smoke test driven by `test_hase_package.py`; it proves the services are
wired to the client but does **not** assert payloads — which is why alarm
create/update/delete are recorded as PARTIAL rather than STRONG.

---

# Recommended Implementation Backlog

**Not implemented by this audit.**

## P0 — correctness bugs

### P0-0 — Make the permanent ledger actually trackable *(durability finding above)*
* **Reason:** `docs/code27/` is git-ignored, so `implemented-api.md` and
  `implemented-api.yaml` will never be committed, reviewed or seen by CI. This
  defeats the entire purpose of the two deliverables.
* **Affected files/components:** `.gitignore` (line 16, `/docs/*`).
* **Expected behaviour:** add `!/docs/code27/` and `!/docs/code27/**` so the
  ledger is tracked like `docs/de/` and `docs/en/`; then commit both files.
* **Tests required:** none. Verify with `git check-ignore -v docs/code27/implemented-api.yaml`
  returning no match, and `git status` listing the two files as untracked/new.

### P0-1 — Stop using `machine_id` as durable device identity *(CCF-1, SA-1)*
* **Reason:** the research explicitly forbids keying long-lived state on a
  transient pairing token; today a re-pair can fork the config entry and abort
  reauth as `wrong_device`.
* **Affected files/components:** `config_flow.py` (`_async_finish_pairing`,
  `async_step_reauth`), every entity module's `unique_id`, `api.py`
  (`attempt_remote_recovery` verification), plus a migration path for already
  stored entries.
* **Expected behaviour:** derive durable identity from a stable source with a
  documented fallback (the DHCP-hostname serial noted by the research, with its
  DHCP caveat); keep `machine_id` as a *comparison* value; treat a changed
  `machine_id` as "same device, re-paired" rather than `wrong_device`.
* **Tests required:** identity survives a `machine_id` change; reauth reattaches
  to the existing entry instead of creating a second one; existing entries migrate
  without losing entity history.

### P0-2 — Fix media-browser path grouping *(CCF-2, SA-5)*
* **Reason:** the live payload uses absolute paths, so the browser collapses every
  track into one empty-named folder.
* **Affected files/components:** `media_player.py` (`_group_music_files`,
  `_files_in_folder`, `_track_browse_item`).
* **Expected behaviour:** group relative to the music root (`root_path`, or the
  segment after `code27/music/`), yielding one folder per real sub-directory.
* **Tests required:** grouping and browsing with **absolute** paths taken from the
  real payload shape, including a root-level file and a nested folder.

## P1 — missing high-value functionality

### P1-1 — Add a REST fallback for `music_storage` *(CCF-3, SA-2)*
* **Reason:** storage state currently depends entirely on the WebSocket; the REST
  endpoint is confirmed live and would make the media browser and storage sensor
  reliable.
* **Affected files/components:** `api.py` (new client method), `coordinator.py`
  (`_poll_once`, merge/freshness rules), `sensor.py` (docstring correction).
* **Expected behaviour:** the storage snapshot survives a lost WebSocket; the
  existing revision/freshness guard prevents a stale poll from overwriting a
  newer push.
* **Tests required:** poll populates storage when no push arrived; push wins over
  an older poll; the existing debounce behaviour for the ~50 s repeat.

### P1-2 — Isolate poll failures per endpoint *(CCF-4)* ✅ UMGESETZT (S2)
* **Reason:** one gated or temporarily failing endpoint blanks every entity.
* **Affected files/components:** `coordinator.py` (`_poll_once`,
  `_async_update_data`).
* **Umsetzung 2026-09-24 (Abschlusskorrektur):** `_poll_once` nutzt
  `return_exceptions=True`. `_check_poll_results` prüft **alle** Ergebnisse auf
  Auth-/Clock-Fehler; System-Konnektivitätsfehler lösen weiterhin Recovery aus.
  `_merge_poll_results` behält bei anderen Endpunktfehlern den letzten gültigen
  Wert (einschließlich neuerer Push-Daten), übernimmt erfolgreiche Antworten und
  veröffentlicht betroffene Felder in `poll_errors` im Diagnose-Download.
  Recovery behält die Unterscheidung zwischen Auth- und Clock-Fehlern bei.
* **Expected behaviour:** per-endpoint tolerance with the last good value retained
  and a surfaced diagnostic; a capability-disabled endpoint degrades only its own
  feature.
* **Tests required:** one failing endpoint leaves the others populated; a
  capability-disabled endpoint is skipped without failing the poll.

### P1-3 — Add the voice-group listing *(CCF-6, SA-4)*
* **Reason:** the endpoint is confirmed live; without it `hase.set_character_voice`
  requires the user to source `voice_id`/`group_key` out of band.
* **Affected files/components:** `api.py` (new method, replacing the stale
  comment), `config_flow.py`/`services.py` (expose options),
  `docs/CODE27-FEATURE-STATUS.md` (correct the "known limitation").
* **Expected behaviour:** `GET /data/user/ability/group-keys?ability_type=tts&language=<code>`
  populates a voice/group picker; the result is cached and invalidated on
  configuration change.
* **Tests required:** request parameters; parsing of
  `{group_key, display_name, provider, voice_ids, clone_voice_ids}`; graceful
  handling of an empty list.

## P2 — useful state/resources

### P2-1 — Handle `auth_scope` / `session_id` *(CCF-7)*
* **Reason:** an unhandled credential class would fail everywhere with no
  diagnostic. Low probability, cheap insurance.
* **Affected files/components:** `api.py` (`pair_confirm`, `_sign`,
  `build_ws_url`), `config_flow.py`, `const.py`.
* **Expected behaviour:** record the returned `auth_scope`; when session-scoped,
  store `local_session_id` and send `session_id` on requests and the WS handshake;
  fail with an explicit, actionable error when a session credential cannot be used.
* **Tests required:** a session-scoped credential produces `session_id` on both
  transports; a permanent credential produces none.

### P2-2 — Remove or correct the dead manifest wrapper *(CCF-8)*
* **Reason:** dead code with a wrong return type, exempted from validation.
* **Affected files/components:** `api.py` (`get_original_portraits_manifest`,
  `_UNCONFIRMED_SHAPE_PATHS`), and either wire it into `get_character_portrait`
  or delete it.
* **Expected behaviour:** if kept, `data.portraits` is returned and the path is
  shape-validated; if deleted, portrait resolution is unchanged.
* **Tests required:** the manifest shape is validated positively rather than
  exempted.

### P2-3 — Apply the nested-failure check to `set_music_source` *(CCF-9)*
* **Reason:** symmetry with `music_control`, which already detects
  failure-inside-a-success-envelope.
* **Affected files/components:** `api.py` `set_music_source`.
* **Expected behaviour:** a nested `status: FAILED` raises `Code27DeviceError`
  with its `error_code`, like `music_control`.
* **Tests required:** a `FAILED` response raises; a normal response does not.

### P2-4 — Use `GET /data/user/chat/history/range` *(C27-038)*
* **Reason:** CONFIRMED LIVE and designed for exactly HASE's "everything since
  date X" need; replaces an N-request per-day loop with one call.
* **Affected files/components:** `api.py` (`get_chat_history_snapshot`).
* **Expected behaviour:** a multi-day gap is recovered in one request, preserving
  the existing whole-day coverage guarantee and burst protection.
* **Tests required:** multi-day recovery returns every day's messages; the
  single-day fast path is unchanged.

### P2-5 — Expose or remove the unused audio-route state *(C27-056)*
* **Reason:** the state is polled and pushed but read by no entity.
* **Affected files/components:** `binary_sensor.py`/`sensor.py`, `coordinator.py`.
* **Expected behaviour:** either a diagnostic sensor/attribute for `route` and
  `wired_headset`, or removal of the poll — no silent dead state either way.
* **Tests required:** the chosen exposure reports the polled/pushed value.

## P3 — optional enhancements

### P3-1 — Correct the diary "all HTTP 404" comment *(CCF-5, SA-3)*
* **Reason:** a factually wrong in-code justification is a maintenance trap.
* **Affected files/components:** `api.py` diary comment block; ledger wording for
  C27-008/C27-122.
* **Expected behaviour:** state precisely that only the `feedback` route is
  unregistered, and record whether read-only remains a product decision.
* **Tests required:** none (documentation/decision only). If diary writing is
  enabled later, tests for `PUT`/`DELETE` payloads.

### P3-2 — Auto-map `slot_number` → `cloud_id` *(characterId ergonomics)*
* **Reason:** every memory/diary service currently requires the caller to supply
  the character's `cloud_id` manually.
* **Affected files/components:** `services.py`, coordinator data,
  `strings.json` descriptions.
* **Expected behaviour:** an optional `slot` input resolves the `cloud_id` from the
  polled character list; an explicit `character_id` still wins.
* **Tests required:** slot-to-cloud_id resolution; rejection of an unknown slot;
  explicit `character_id` unchanged.

### P3-3 — Expose a per-slot proactive-config diagnostic (read-only)
* **Reason:** `GET /data/user/proactive/config` is CONFIRMED LIVE; surfacing it
  read-only would make an otherwise invisible device behaviour observable.
* **Affected files/components:** `api.py`, a diagnostic sensor.
* **Expected behaviour:** read-only exposure; **no** writes and **no** coordination
  logic (HASE makes no proactive claim).
* **Tests required:** parsing of the captured config shape; no write path exists.

### P3-4 — Additional useful reads
* `GET /data/user/music/storage?refresh=1` to force a TF re-scan after
  `hase.upload_music`/`hase.delete_music_file` (removes the need to wait for a
  push).
* `POST /data/user/chat/cancel` (C27-084) to abort a long or wrong generation
  from HA.
* Bluetooth `paired_devices[]` exposure.
* **Tests required:** per feature, request construction and state update.

---

# Intentionally Unsupported / Blocked

Four distinct categories must not be confused with one another.

## 1. HASE DESIGN DECISION

HASE deliberately does not implement these, for stated product/robustness
reasons. They are **not** blocked by CODE27.

| Operations | Reason |
|---|---|
| C27-004, C27-006 (bulk chat history deletion) | destructive; HASE only removes messages it wrote itself |
| C27-011 (delete memory card) | no undo, and edits are already permanent |
| C27-082, C27-083, C27-030, C27-031 (character reset family) | irreversible |
| C27-101 (`POST /data/user/log`) | HASE logs through Home Assistant |
| C27-117 (`PUT /data/user/ai-language`) | device-wide setting; a per-message `language` override covers the HA use case |
| C27-010, C27-050, C27-094, C27-096, C27-097, C27-098, C27-123 (light-project editing) | projects are device-authored content; HASE uses the ephemeral preview channel for ad-hoc colour |
| C27-103 (Bluetooth forget) | read-only Bluetooth: there is no pairing endpoint, so HA could never complete a pairing |
| C27-110, C27-111, C27-114 (voice clone) | requires the vendor cloud; HASE must not contact it |
| C27-063, C27-126 (proactive config) | HASE makes no proactive-coordination claim |
| C27-018 (voice groups) | **currently asserted as "path unconfirmed", which is wrong** — reclassify per CCF-6 |
| C27-008, C27-122 (diary writes) | **currently justified as "all HTTP 404", which is wrong** — see CCF-5 |

## 2. CODE27 CAPABILITY GATE

Blocked by the device, evidenced by `GET /data/user/system` → `capabilities`.
**Never** a HASE defect; HASE must not bypass these.

| Capability | State on the known device | Operations |
|---|---|---|
| `admin.user_settings` | false | C27-001, C27-016, C27-073, C27-116 |
| `admin.system_settings` | false | C27-014, C27-115 |
| `admin.*` (tunnel) | false | C27-015 |
| `lab.ai_provider` | false | C27-002, C27-020, C27-021, C27-022, C27-023, C27-024, C27-076, C27-077, C27-078, C27-118, C27-119 |
| `voice.clone_record_debug` | false | C27-067, C27-112, C27-113 |

Current device state is **known** (captured live). It is not assumed: other flags
on the same device are `true` (`lab.alarm`, `lab.memo`, `lab.light_editor`,
`mode.music`, `voice.group`, `character.memory`,
`feature.face_tracking_turret`), so the gate is selective rather than a blanket
denial. Flags such as `feature.face_tracking_turret`, `character.model_controls`,
`lab.system` and `chat.audio_debug` have no corresponding inventory operation, so
they gate nothing HASE could call.

## 3. CODE27 DOES NOT EXPOSE IT

Proven absent, not assumed.

| Operation | Evidence |
|---|---|
| C27-090 `POST /data/user/diary/entries/{id}/feedback` | router-level plain-text `404 page not found` **with a valid signature**, while the identically shaped `PUT` returns a normal JSON validation error (`empty_update`). The shipped frontend still calls it via `submitDiaryFeedback` — frontend/backend version skew. `inventory.json` records the frontend's intent, not a working route. |

Related: there is **no** camera/frame/snapshot/stream endpoint anywhere in the 696
domain-specific probes and the 127-operation inventory, and **no** Bluetooth
pairing endpoint and **no** audio-route write endpoint. HASE's silence on those
features is therefore correct by construction.

## 4. INTERNAL / UNKNOWN

HASE makes **no** assumption about, and does not depend on, any of the following.
Nothing here needs documenting as a stale assumption:

`com.clt01.display` APK internals · the exact `:8081` process owner · the Go
binary's internals · gRPC service definitions · Piko/tunnel internals ·
capability-gate implementation details · the internal plugin registry · the raw
vision pipeline · camera ownership · Android service-manager internals.

Confirmations: no `clt01`, OTA, `adb`, `fastboot` or `usb` reference exists in the
HASE repository; the only `capabilities` data HASE touches is the public flag
object returned by the documented endpoint; and the only remote endpoint HASE
knows is the cloud relay used for opt-in, read-only IP recovery — documented as
such in the source and never used as a general request fallback.

### Remaining UNKNOWN items (recorded as unknowns, not gaps)

| Item | Status |
|---|---|
| Whether the device can return a session-scoped credential in practice | UNKNOWN (CCF-7); HASE does not request one |
| Whether `music/source` can report failure inside a `200`/`success:true` envelope | UNKNOWN; inferred only from the sibling endpoint's confirmed behaviour (CCF-9) |
| Whether the synthesized light-preview payload is accepted verbatim by the device | UNKNOWN; the endpoint's body shape is CONFIRMED FROM CODE and the research executed no writes |
| Whether any of the 54 unimplemented-but-available operations is wanted by users | not assessed; out of scope for a read-only conformance audit |

---

# Documentation Corrections

Statements that future consolidated CODE27/HASE documentation must make
differently, because the actual current HASE implementation differs from what is
currently written.

| # | Where | Current statement | Must be corrected to |
|---|---|---|---|
| DC-1 | `docs/CODE27-FEATURE-STATUS.md` §"Bekannte Grenzen" ("Dynamische Stimmenliste nicht abrufbar … der Audit kennt nur den Config-Key-Namen, nie den aufgelösten REST-Pfad") | claims the voice-group REST path is unknown | The path **is** known and confirmed live: `GET /data/user/ability/group-keys?ability_type=tts&language=<code>` (captured 200 response). Not implementing it is a HASE gap, not a CODE27 unknown. |
| DC-2 | `providers/code27/api.py` diary comment ("no favorite/reaction/delete endpoint works … all HTTP 404") | claims all diary writes 404 | Only `POST …/{id}/feedback` is unregistered (router-level 404). `PUT …/{id}` returns a normal JSON validation error and `DELETE …/{id}` returns 401, i.e. both routes exist. |
| DC-3 | `providers/code27/coordinator.py` `music_storage` comment + `sensor.py` `Code27MusicStorageSensor` docstring ("No confirmed REST endpoint exists" / "no REST fallback exists") | claims no REST storage endpoint | `GET /data/user/music/storage` is CONFIRMED LIVE (captured 31-file response, optional `?refresh=1`). The truthful statement is: "HASE currently populates this only from the WebSocket push; a REST fallback exists but is not implemented." |
| DC-4 | `providers/code27/api.py` `_UNCONFIRMED_SHAPE_PATHS` comment ("the audit never actually confirmed the shape") | claims the manifest shape is unknown | The shape **is** confirmed: `{exported_at_ms, portraits[]}` — an object, not a list. The wrapper's `list[dict]` annotation and `.get("data", [])` default are therefore wrong. |
| DC-5 | `providers/code27/media_player.py` comment ("not verified against `music_storage.root_path` possibly being an absolute prefix … flagged for live verification") | treats the absolute-path question as open | It is **resolved**: `music_files[].path` is absolute (`/storage/<vol>/code27/music/…`), so the current grouping logic is wrong, not merely unverified. |
| DC-6 | HASE identity documentation generally | treats `machine_id` as the device identity | `machine_id` is a **transient pairing token** per the final research; it is the correct `device_id` value for `PUT /data/user/character`, but must not be presented as durable physical-device identity. |
| DC-7 | CODE27 side: `CODE27-LOCAL-API.md` verification status for `GET /resources/user/assets/thumbnails/{id}` | listed as `CONFIRMED FROM CODE` (route exists) | The later phase-4 local-data work **downloaded** real thumbnails with HTTP 200 (`thumb_5_2_Ginny.png`, `thumb_1_2_Lylia.png`), so the correct status is `CONFIRMED LIVE`. Newest research supersedes. |
| DC-8 | CODE27 side: `CODE27-LOCAL-API.md` internal count | says "124" in an early paragraph and "127" in the coverage table | **127** is ground truth (`inventory.json`, 127 unique `METHOD + PATH`); the growth `124 → 127` is documented in `code27-runtime-architecture-findings.md`. |
| DC-9 | HASE repo: `docs/code27/CODE27-HASE-CURRENT-IMPLEMENTATION-AUDIT.md` | a copy of the one-time audit report sits inside the HASE repository | The one-time report belongs to the CODE27 research directory. Inside HASE, only `implemented-api.md` and `implemented-api.yaml` are the permanent artefacts. (Not removed by this audit — files outside the permitted write set were left untouched.) |
| DC-10 | HASE repo: stale `providers/code27/__pycache__/spellbook.cpython-312.pyc` | a compiled module exists with no corresponding `spellbook.py` source | Recorded for completeness: stale bytecode from a removed module. It has no runtime effect but should not be mistaken for current code. |
| DC-11 | This report's own previous revision | claimed 54 implemented / 1 partial / 18 gated, citing OTA, `/data/system/capabilities`, playlist, Bluetooth scan/connect and `proactive/trigger` endpoints | All of those endpoints are absent from the authoritative inventory. Use the counts in this revision (51 / 0 / 21) and the permanent ledger. |
| DC-12 | `.gitignore` | ignores `/docs/*` with allowlists only for `de/`, `en/`, `branding/`, `screenshots/`, `examples/` | Add `!/docs/code27/` and `!/docs/code27/**`. Without it, the permanent CODE27 implementation ledger is untracked and cannot be committed — see the publication/durability finding above. |

---

# Future Conformance Recommendation

The purpose of `docs/code27/implemented-api.yaml` is to make **this manual
archaeology unnecessary**. The ledger is deliberately shaped so that a small
script can verify it without reading prose.

## What the ledger enables

Each operation carries machine-readable fields for exactly the things that had to
be checked by hand here:

```
id · method · path · category · purpose
code27:      evidence · verification · capability
hase:        status · operation · files · symbols · call_sites · exposure
request:     path_parameters · query_parameters · body · important_types · encoding · multipart
response:    available_fields · consumed_fields
async_semantics: type · states · terminal_states · completion_detection
websocket:   related · topics · fallback_polling
cache:       policy
tests:       coverage · files · cases
quirks · notes
```

Plus the separate top-level `websocket:` section (7 topics) and `resources:`
section (5 proven resources).

## Recommended check (design only — deliberately not implemented)

A single `scripts/check_code27_conformance.py` (or a CI job) should assert:

| # | Check | Why it prevents a repeat of this audit |
|---|---|---|
| 1 | The authoritative `inventory.json` parses and its operation count matches `meta.source_inventory_count` | catches an upstream inventory change immediately |
| 2 | Every inventory `METHOD + PATH` appears **exactly once** in `operations` (normalising `PROBEID` → `{id}`/`{slot}`) | this is the check that **failed** against the inherited ledger (a missing `PUT /data/user/slot` plus a non-inventory `PUT /v1/ws/channel`) |
| 3 | No duplicate `METHOD + PATH`; no duplicate `C27` id | stops silent overwrites |
| 4 | Ids are stable against a recorded baseline (`id → METHOD + PATH`); new ids only appended; none renumbered or reused | enforces the change-control contract mechanically |
| 5 | `implemented-api.md` and `implemented-api.yaml` contain identical `id → METHOD + PATH` mappings, with no duplicates | keeps the two permanent artefacts in sync |
| 6 | Every referenced implementation file exists | catches refactors |
| 7 | Every referenced symbol resolves in one of that operation's declared files | catches renames — this check caught two prose artefacts during this audit |
| 8 | Every referenced test file exists, and every named test case exists in it | catches deleted/renamed tests, which would otherwise silently downgrade coverage claims |
| 9 | Every `IMPLEMENTED`/`PARTIAL` operation has non-empty `files`, `symbols`, `call_sites` and `operation` | makes "implementation without evidence" impossible to merge |
| 10 | `meta.counts` match the status histogram; `meta.implemented_test_coverage` and `meta.implemented_direction` match the computed values | prevents a stale summary headline — the number a reader trusts most |
| 11 | WebSocket topic set and resource set agree between Markdown and YAML | keeps the two secondary ledgers honest |
| 12 | `TEST DEBT` markers remain present and countable; every `tests.coverage: NONE` operation is listed in the Markdown test-debt table | keeps debt visible instead of letting it decay into an undocumented state |

Checks 1–10 and 12 were executed **once, mechanically**, while producing this
revision. No audit script was added to the repository, per the read-only scope of
this task.

## How this removes the need for another manual pass

* **New CODE27 endpoint:** the inventory changes → checks 1 and 2 fail → a new
  `C27` id must be appended (check 4) → the implementer records status, evidence,
  consumed fields and tests. Nothing has to be rediscovered.
* **HASE refactor:** checks 6 and 7 fail on a moved file or a renamed symbol → the
  ledger is updated together with the code.
* **Deleted test:** check 8 fails → coverage claims cannot silently rot.
* **Stale assumption reintroduced:** the `quirks` and `notes` fields carry the
  researched truth for each operation (for example: "`voice_id` is a STRING",
  "`success:true` means accepted only", "`chat_history` is dead", "the
  `music_storage` REST endpoint exists"), so a contradicting comment or docstring
  can be caught by reviewing a single canonical row instead of re-reading the
  whole research corpus.
* **A future "what does HASE actually implement?" question:** answered by one
  command over the ledger, with `consumed_fields` distinguishing what CODE27
  *returns* from what HASE *uses* — the distinction this audit had to build by
  hand.

---

# Appendix — Consistency validation log

All 18 required checks were executed against the current tree.

| # | Required check | Result |
|---|---|---|
| 1 | Authoritative operation count | **PASS** — 127 in `inventory.json`, 127 unique `METHOD + PATH` |
| 2 | Every authoritative `METHOD + PATH` appears exactly once in the permanent ledger | **FAILED on the inherited ledger, now PASS** — `PUT /data/user/slot` was missing and `PUT /v1/ws/channel` had been entered in its place; corrected, ids 001–126 unchanged |
| 3 | No duplicate `METHOD + PATH` | **PASS** — 127 operations, 127 unique pairs |
| 4 | Every `C27` id unique | **PASS** — 127 ids, no duplicates |
| 5 | Every `IMPLEMENTED`/`PARTIAL` operation has actual code evidence | **PASS** — all 51 have non-empty files, symbols, call sites and operation names |
| 6 | Every referenced implementation file exists | **PASS** — 21 distinct referenced files verified |
| 7 | Every referenced implementation symbol exists | **PASS** — all symbol tokens resolve in their declared files (two prose artefacts found and fixed) |
| 8 | Every referenced test file exists | **PASS** |
| 9 | Every referenced test/case exists where practical to verify | **PASS** — all named cases resolve (three mistyped references corrected, incl. two test-file lists that omitted `tests/test_hase_package.py`) |
| 10 | Capability-gated operations classified consistently | **PASS** — 21 ops, each tied to the specific false capability flag observed on the device |
| 11 | HASE claims agree with current repository code | **PASS** — every statement traced to a symbol; six contradicting claims reported as stale assumptions rather than repeated |
| 12 | CODE27 semantics agree with final research | **PASS** — newest research used throughout, with three supersessions recorded (thumbnail status, the 124→127 count, the diary route reality) |
| 13 | `implemented-api.md` and `implemented-api.yaml` have identical operation sets | **PASS after correction** — 127 = 127, identical `id → METHOD + PATH` mappings, no duplicates. Eight Markdown cells initially carried inline query strings in the operation column (e.g. `?scope=chat`), which broke set comparison; they were normalised to the canonical bare path with query parameters documented alongside, so future automated comparison works. |
| 14 | WebSocket topic sets agree between Markdown and YAML | **PASS** — all 7 research-documented topics present in both; 6 subscribed, `chat_history` correctly not subscribed |
| 15 | Resource sets agree between Markdown and YAML | **PASS** — 5 resources in both, none guessed, each backed by captured evidence |
| 16 | No guessed API operation was added | **PASS** — the ledger set equals the inventory set exactly; no operation was invented |
| 17 | No inaccessible/gated feature upgraded to IMPLEMENTED | **PASS** — 21 gated ops remain `CAPABILITY_GATED`; the one proven-absent route is `INTENTIONALLY_UNSUPPORTED`; nothing blocked is presented as working |
| 18 | No endpoint marked IMPLEMENTED solely because a generic HTTP helper could theoretically call it | **PASS** — three endpoints with existing client wrappers but no runtime path (`memory/cards/{id}`, `original-portraits/manifest`, `chat-images`) are `NOT_IMPLEMENTED`, not IMPLEMENTED |

Final mechanical result: **127 operations, 127 unique ids, 127 unique
`METHOD + PATH`, ledger set == inventory set, Markdown set == YAML set ==
inventory set, 0 unresolved file references, 0 unresolved symbols, 0 unresolved
test cases.**
