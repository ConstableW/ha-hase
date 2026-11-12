# HASE — Implemented CODE27 API reference

**Status:** permanent implementation reference (not a one-off report)
**Machine-readable companion:** [`implemented-api.yaml`](implemented-api.yaml) — same operation set, same IDs
**CODE27 API authority:** `C:\USERDATA\HA-Code27 API` (`_recon/inventory.json` = 127 operations)

This document answers, for the current HASE codebase:

* which CODE27 local-API operations HASE implements, and which it does not;
* where each one is implemented and which subsystem consumes it;
* which request/response semantics matter (including the known traps);
* which response fields HASE actually consumes (as opposed to merely receives);
* which tests prove it, and where test coverage is missing;
* which quirks must not be forgotten.

Everything here was derived from the **actual current source** under
`custom_components/hase/providers/code27/`. The existence of a path string, a
test fixture, a docstring or a stale comment is **not** treated as evidence that
a feature exists — only a real runtime call path is.

> **⚠️ TRACKING WARNING — this document is currently git-ignored.**
> `.gitignore` line 16 is `/docs/*` with allowlists for `de/`, `en/`, `branding/`,
> `screenshots/` and `examples/`, but **no entry for `code27/`**.
> `git check-ignore -v docs/code27/implemented-api.yaml` matches `.gitignore:16`.
> Until `!/docs/code27/` and `!/docs/code27/**` are added, this file and its YAML
> companion cannot be committed, will not appear in a clone, and cannot be read by
> CI — which defeats their purpose as the permanent, machine-checkable record.
> (The provider directory `custom_components/hase/providers/code27/` is itself
> ignored as "Private / vendor-specific providers".)

---

## 1. Implementation completeness summary

```
TOTAL CODE27 OPERATIONS:            127

IMPLEMENTED:                         51
PARTIAL:                              0
NOT_IMPLEMENTED:                     54
INTENTIONALLY_UNSUPPORTED:            1
CAPABILITY_GATED:                    21
NOT_RELEVANT_TO_HASE:                 0
REMOVED_UPSTREAM:                     0

IMPLEMENTED WITH STRONG TESTS:       14
IMPLEMENTED WITH PARTIAL TESTS:      24
IMPLEMENTED WITH NO TESTS:           13

READ OPERATIONS IMPLEMENTED:         24
WRITE OPERATIONS IMPLEMENTED:        27
```

No generic "percentage complete" is published: not every CODE27 endpoint belongs
in a Home Assistant integration, so such a figure would be meaningless.

**Why `PARTIAL` is 0.** No endpoint is called in a way that leaves a documented
asynchronous or multi-stage contract unfinished. The three cases where that
would have been the natural classification were resolved explicitly instead:

| Case | Resolution |
|---|---|
| TTS (`POST /data/user/tts_audition` + `GET /data/user/tts_audition/state`) | **IMPLEMENTED** — HASE submits, then polls to a terminal `ENDED`/`FAILED` state under a wall-clock deadline. The `success:true` acknowledgement is never treated as playback completion. |
| Chat (`POST /data/user/chat/text`) | **IMPLEMENTED** — the acknowledgement carries the written `chat_id`, which is registered as HASE's own so the poller does not mistake it for user input; the character's reply is detected by the dedicated chat poll. |
| `GET /data/user/memory/cards/{id}` and `GET /resources/user/chat-images` | **NOT_IMPLEMENTED**, not PARTIAL — a client method exists but no runtime path calls it (dead / test-only code). |

---

## 2. Transport and authentication contract

`custom_components/hase/providers/code27/api.py`, class `Code27Client`.

```
base URL        http://<host>:<port>            (default port 8081)
REST prefix     /v1/api
WebSocket       ws://<host>:<port>/v1/ws/channel
```

### 2.1 Canonical string and signature

```
stringToSign = METHOD "|" /v1/api<path> "|" timestamp "|" nonce "|"
               payloadMode "|" bodyHash "|" userId
X-Local-Sig  = lowerhex(HMAC_SHA256(local_secret, stringToSign))
```

| Component | Rule implemented by HASE |
|---|---|
| `METHOD` | upper-cased HTTP verb |
| path | full path **including** the `/v1/api` prefix, query string removed, duplicate slashes collapsed |
| `timestamp` | epoch seconds as a string |
| `nonce` | 16 random chars from `[0-9a-z]` |
| `payloadMode` | `empty` (no body) · `json` (string body) · `multipart` (FormData) |
| `bodyHash` | `json` → lowerhex SHA-256 of the **exact bytes sent**; `empty` → SHA-256 of `""`; `multipart` → the literal `UNSIGNED-PAYLOAD` |
| `userId` | `data.user_id` from `GET /auth/bootstrap` |

Headers sent: `X-Local-Sig`, `X-Local-Nonce`, `X-Local-Timestamp`,
`X-Local-Payload-Mode`, `X-Local-Body-Hash`, `X-Local-User-Id`.

**Signature-exempt paths** (`_UNSIGNED_PATHS`): `GET /auth/bootstrap`,
`POST /auth/pair/request`, `POST /auth/pair/confirm`.

**WebSocket signing** (`Code27Client.build_ws_url`): the same HMAC over
`GET|/v1/ws/channel|<ts>|<nonce>|empty|<sha256("")>|<user_id>`, passed as query
parameters `sig`, `nonce`, `timestamp`, `payload_mode`, `body_hash`, `user_id`
(a browser cannot set headers on a WebSocket handshake).

### 2.2 Verified against the CODE27 authentication contract

| Contract point | HASE | Notes |
|---|---|---|
| Canonical string layout | ✅ matches | field-for-field |
| Payload modes / body hashing | ✅ matches | JSON body hashed byte-exactly, including compact separators |
| Multipart `UNSIGNED-PAYLOAD` | ✅ matches | body deliberately not hashed |
| Header names | ✅ matches | |
| WebSocket query-param signing | ✅ matches | |
| Pairing is signature-exempt | ✅ matches | |
| `user_id` source | ✅ bootstrap, not the id echoed by `pair/confirm` | deliberate |
| Credential persistence | ✅ `local_secret` + `user_id` in the HA config entry | |
| Credential renewal | ✅ HA reauth flow (`async_step_reauth`), rejects a *different* device | |
| Expired credential behaviour | ✅ HTTP 401 → `Code27AuthError` → `ConfigEntryAuthFailed` → HA repair/reauth | |
| `auth_scope` / `local_session_id` | ⚠️ **not handled at all** | see quirk Q-A below |

**Quirk Q-A — session-scoped credentials are unsupported.** `pair_confirm` does
not send `auth_scope`, and the strings `auth_scope`, `session_id` and
`local_session_id` appear nowhere in the repository. HASE therefore relies on
receiving a `permanent` credential (the device default). If a device ever issued
a session-scoped credential, every signed request **and** the WebSocket
handshake would return 401 with no recovery path other than re-pairing. Not
live-verified either way.

### 2.3 Error mapping (behaviour that must be preserved)

| Condition | HASE result |
|---|---|
| HTTP 401 | `Code27AuthError` (carrying the device `code` when present) → reauth |
| HTTP 400/403/404/5xx, or envelope `success:false` | `Code27Error` with `message`/`error`/`code` |
| HTTP 409 with `current_slot`/`request_slot` | `Code27SlotMismatchError` — surfaces as "switch the active slot first" |
| HTTP 200 + `success:true` + nested `status:"FAILED"` | `Code27DeviceError(error_code)` — never a silent no-op |
| Non-JSON body | `Code27AuthError` on 401, else `Code27Error` |
| Response shape ≠ documented shape | `Code27Error` (`_validate_response_data`) |
| Connection failure / timeout | `Code27ConnectionError` → `UpdateFailed` (or opt-in remote IP recovery) |

---

## 3. Identity model (must not be conflated)

```
slot_number  → runtime / profile selection   (chat slot, ?slot=, PUT /data/user/slot)
cloud_id     → stable character identity     (memory + diary `characterId`)
machine_id   → transient pairing token       (NOT durable physical-device identity)
```

* HASE keeps `slot_number` and `cloud_id` strictly separate: the character-slot
  select entity uses `slot_number`; `hase.get_memory_cards`,
  `hase.get_diary_entries` and `hase.get_diary_entry` require the caller to pass
  `character_id`, documented in `strings.json` as "the character's **cloud_id**,
  not the slot number".
* `slot_number` is never reinterpreted as device identity.
* `machine_id` **is** used by HASE as durable identity (config-entry
  `unique_id`, every entity `unique_id`, reauth device matching, remote-recovery
  device verification). This contradicts the final CODE27 research, which states
  explicitly that `machine_id` is a transient pairing token and that long-lived
  state must not be keyed on it. It is, however, the correct value for the
  `device_id` field of `PUT /data/user/character`. See the audit report's
  identity finding — **not fixed here, deliberately**.
* `app_id` (`com.code27.app`) does not appear anywhere in HASE, so it is never
  silently reinterpreted as a hardware serial.
* No DHCP-hostname serial, MAC address or `serial` field is used as identity.

---

## 4. Implemented operations (51)

Legend: **WS** = also updated from the WebSocket push; **tests** refers to
coverage of the operation's semantics.

### 4.1 Authentication (3)

| ID | Operation | HASE implementation | Consumes | Tests |
|---|---|---|---|---|
| C27-017 | `GET /auth/bootstrap` | `Code27Client.bootstrap` ← `Code27FlowMixin.async_step_code27_device` (reachability) and `pair_confirm` (`user_id`) | `user_id` | STRONG — `test_stage_three.py::FlowTests` |
| C27-074 | `POST /auth/pair/confirm` | `Code27Client.pair_confirm` ← `async_step_pair` | `local_secret` | STRONG — `FlowTests.test_matching_device_reauth_persists_reloads_aborts_without_creating`, `test_wrong_device_reauth_does_not_change_existing_entry`, `test_identity_read_retry_does_not_pair_twice` |
| C27-075 | `POST /auth/pair/request` | `Code27Client.pair_request` ← `_async_request_pairing_code` | `challenge_id` | STRONG — pairing + reauth flows |

Request body for `pair/confirm`: `{pairing_code: str, challenge_id: str}`.
A burned code (`PAIRING_CODE_EXPIRED` / `PAIRING_CODE_USED`) triggers a fresh
code request instead of a hard failure.

### 4.2 System, slot and capability state (3)

| ID | Operation | HASE implementation | Consumes | Tests |
|---|---|---|---|---|
| C27-064 | `GET /data/user/slot` | `get_current_slot` ← coordinator poll, `Code27CharacterSlotSelect.current_option`, `async_speak_text` (slot guard), chat/image services (default slot); always queried with `?scope=chat` | `slot_number` | PARTIAL — `test_stage_three.py`, `test_bridge_safety.py` |
| C27-065 | `GET /data/user/system` | `get_system` ← coordinator poll, `async_step_verify`, `Code27SystemSensor`, `Code27CapabilitiesSensor`, `Code27Use24HourTimeBinarySensor`, remote-recovery validation | `machine_id`, `local_ip`, `capabilities`, `ai_language`, `timezone`, `build_type`, `use_24_hour_time` | PARTIAL — `FlowTests.test_invalid_system_response_returns_verification_form`, `ResponseTests` |
| C27-127 | `PUT /data/user/slot` | `update_selected_slot` ← `Code27CharacterSlotSelect.async_select_option` | write ack (readback via C27-064) | **NONE — TEST DEBT** |

Quirks:
* `version` is a firmware **string** here but a numeric revision counter on the
  `music/*` endpoints; the response validator special-cases this.
* The `capabilities` object is surfaced **read-only as diagnostics**; it does
  **not** gate any HASE feature (an explicit, documented decision).
* A slot switch re-baselines the chat poller so the new character's history is
  not replayed as fresh events, and drops any open correction dialog.

### 4.3 Characters, portraits and outfit (4)

| ID | Operation | HASE implementation | Consumes | Tests |
|---|---|---|---|---|
| C27-028 | `GET /data/user/character/list` | `list_character_settings` ← coordinator poll → select entity, portrait images, active-character sensor, character overview | `slot_number`, `name`, `selected_cloth`, `voice_id`, `group_key` | PARTIAL |
| C27-043 | `GET /data/user/cloth/uploaded-models` | `get_cloth_uploaded_models` ← `get_character_portrait` (first step) ← `Code27PortraitImage.async_image` | `models[].slot_number`, `models[].cloth_id`, `models[].filename` | PARTIAL |
| C27-069 | `GET /resources/user/assets/thumbnails/{id}` | `get_character_portrait` (preferred source) ← `Code27PortraitImage.async_image` | image bytes + `Content-Type` | STRONG — `test_get_character_portrait_prefers_custom_thumbnail`, `test_call_binary_*` |
| C27-121 | `PUT /data/user/character` | `update_character_setting` ← `hase.set_character_voice` | write ack (refresh follows) | STRONG — `test_update_character_setting_sends_slot_and_device_id`, `test_update_character_setting_rejects_unknown_fields` |

Portrait resolution order (`get_character_portrait`), matching the confirmed
live behaviour of the device:

```
custom thumbnail  /resources/user/assets/thumbnails/{slot}_{cloth}_{stem}.png
        ↓ (404 or no uploaded model for this slot/cloth)
stock portrait    /resources/system/assets/original-portraits/{slot}_{cloth}.png
        ↓ (404)
unavailable       → image entity reports no image
```

Quirks:
* The thumbnail filename segment is the VRM filename's **stem** — the device
  rejects the request when the extension is included.
* The stock-portrait path is not one of the 127 inventory operations; it is the
  `portrait_path` the manifest reports, and HASE reconstructs it exactly.
* `PUT /data/user/character` is sent with `device_id` = `machine_id`, and an
  unknown update field is rejected client-side so a typo fails fast rather than
  being silently ignored. `CLOTH_UPLOAD_IN_PROGRESS` is translated into a
  specific user-facing message.
* Only `voice_id` + `group_key` are exposed through the public service, although
  the wrapper's allowlist is wider.

### 4.4 Chat (7)

| ID | Operation | HASE implementation | Consumes | Tests |
|---|---|---|---|---|
| C27-032 | `GET /data/user/chat/history` | `get_chat_history_snapshot` ← `Code27Coordinator._async_poll_chat` (every 3 s); query `?date=&slot=` | `chat_id`, `history_id`, `role`, `content`, `created_at` | STRONG — burst/midnight/multi-day tests |
| C27-033 | `GET /data/user/chat/history/count` | `get_chat_history_count` ← coordinator poll → `_build_character_overview` | `slot`, `messages[].content/role/created_at` | STRONG — `test_get_chat_history_count_*` |
| C27-035 | `GET /data/user/chat/history/dates` | `get_chat_history_dates` ← inside `get_chat_history_snapshot` / `get_latest_chat_messages`; query `?slot=` | all returned dates (day cursor) | PARTIAL |
| C27-036 | `GET /data/user/chat/history/latest` | `get_latest_chat_messages` ← `async_cleanup_assist_exchange`; fast path inside `get_chat_history_snapshot`; query `?date=&slot=&limit=` | `chat_id`, `role`, `content`, `created_at` | STRONG |
| C27-085 | `POST /data/user/chat/image-text` | `send_chat_image` ← `hase.send_chat_image` | `chat_id`, `image_chat_id` | STRONG |
| C27-086 | `POST /data/user/chat/text` | `send_chat_text` ← `Code27Coordinator.async_send_chat_reply`, `hase.send_chat_message`, bridge `_output_speech` | `chat_id` | STRONG |
| C27-005 | `DELETE /data/user/chat/history/items` | `delete_chat_messages` ← `async_cleanup_assist_exchange` (once + one retry) | — | **NONE — TEST DEBT** |

Change detection strategy (the important semantic):

```
every 3 s, per active slot:
  dates = GET /chat/history/dates?slot=N
  if last seen date unchanged and the last seen chat_id is still inside the
  bounded latest window → GET /chat/history/latest (fast path)
  otherwise → GET /chat/history?date=<each day from last seen date onward>
  diff by chat_id → fire hase_user_message / hase_assistant_message
```

Whole-day reads are used deliberately: a bounded window can let a burst push an
unseen command out of view (this is documented in the source and tested).

Request contract of `chat/text`: `{text: str, slot: int, language?: str}` —
`language` is omitted entirely when not supplied. HTTP 409 raises
`Code27SlotMismatchError` carrying `current_slot`/`request_slot`.

`chat/image-text` is multipart with fields `text`, `slot` (as a string),
`language?` and one file part named `image`. A 1 MB client-side limit is
enforced **before** upload (device hard limit, HTTP 400 otherwise).

Quirks:
* `chat_id` (string) is the only reliable identifier. `history_id` is **not**
  used: messages CODE27 classifies as commands keep `history_id == 0`, and
  sending `{"history_ids": [0]}` deletes every zero-id message at once.
* VLM metadata that may be attached to image messages is **not** consumed — this
  is not camera or vision access.
* `GET /data/user/chat/history/range` (C27-038) is CONFIRMED LIVE but unused;
  HASE issues one request per day instead. See §11 backlog.
* `chat_history` is deliberately **not** subscribed as a WebSocket topic (§9).

### 4.5 TTS (2)

| ID | Operation | HASE implementation | Consumes | Tests |
|---|---|---|---|---|
| C27-109 | `POST /data/user/tts_audition` | `tts_audition` ← `async_speak_text` ← `hase.speak`, `hase.notify(speak_text)`, bridge reply | acknowledgement only | PARTIAL |
| C27-066 | `GET /data/user/tts_audition/state` | `get_tts_audition_state` ← `async_speak_text` poll loop | `state` | PARTIAL |

Lifecycle implemented (`bridge.async_speak_text`):

```
resolve active slot (+ verify expected_slot before speaking)
        ↓
resolve voice_id / group_key from the polled character list
        ↓
POST /data/user/tts_audition      {"voice_id": "<string>", "text": ..., "group_key": ...}
        ↓  success:true means ACCEPTED ONLY
poll GET /data/user/tts_audition/state every 0.35 s inside a 15 s wall clock
        ↓
ENDED  → logbook "spoken"        FAILED → logbook "failed"
timeout → logbook "timeout" (never silent)
```

Request contract traps preserved:
* `voice_id` is a **STRING** (e.g. `"11"`), never an integer.
* `group_key` is a string (e.g. `us_tts_de_group`); an empty string is sent when
  the character has none.
* TTS has **no slot-addressed endpoint**, so HASE verifies the active slot first
  and silently suppresses a stale request rather than speaking through a newly
  selected character.
* If the character has no `voice_id`, HASE logs `tts_no_voice` and does not call
  the endpoint at all.

Quirks: only the `ENDED` branch is exercised by tests; `FAILED` and the 15 s
timeout branch are untested. The 15 s deadline matches the device web UI's own
audition timeout, so a very long utterance can report `tts_timeout` while
playback continues.

### 4.6 Memory, diary and memos (7)

| ID | Operation | HASE implementation | Consumes | Tests |
|---|---|---|---|---|
| C27-052 | `GET /data/user/memory/cards` | `list_memory_cards` ← `hase.get_memory_cards`; query `?characterId=&page=&pageSize=` | whole payload passed through | **NONE — TEST DEBT** |
| C27-124 | `PUT /data/user/memory/cards/{id}` | `update_memory_card` ← `hase.update_memory_card` | ack | STRONG — `test_update_memory_card_*` |
| C27-044 | `GET /data/user/diary/entries` | `list_diary_entries` ← `hase.get_diary_entries`; query `?characterId=&slot=&page=&pageSize=[&date=]` | whole payload passed through | PARTIAL |
| C27-045 | `GET /data/user/diary/entries/{id}` | `get_diary_entry` ← `hase.get_diary_entry`; query `?characterId=` | whole payload passed through | **NONE — TEST DEBT** |
| C27-054 | `GET /data/user/memos` | `list_memos` ← coordinator poll → todo entity + memo count sensor | `id`, `title`, `body`, `is_expired`, `next_fire_at_ms` | PARTIAL |
| C27-102 | `POST /data/user/memos` | `create_memo` ← `Code27MemoTodoList.async_create_todo_item` | ack | **NONE — TEST DEBT** |
| C27-125 | `PUT /data/user/memos/{id}` | `update_memo` ← `Code27MemoTodoList.async_update_todo_item` | ack | **NONE — TEST DEBT** |
| C27-012 | `DELETE /data/user/memos/{id}` | `delete_memo` ← `async_delete_todo_items` | ack | **NONE — TEST DEBT** |

Semantics that matter:
* `characterId` must be the character's **`cloud_id`** (camelCase query name on
  memory/diary routes).
* Editing a memory card permanently sets `isUserEdited: true`, even when the new
  content is identical — there is no way to undo it, so the service description
  warns the user **before** editing.
* `is_expired` on a memo is time-computed, not a "done" flag. Writing it back has
  no effect, so a pure status change is **rejected** with an explicit error
  instead of silently doing nothing.
* Memo creation writes only `title`, `body` and the literal `memo_type: "static"`.
  A reminder time is never set from HA, so a memo created from HA behaves as a
  note rather than a timed reminder.
* Diary is **read-only by design** (see C27-008/C27-122 for the stale
  justification stored in the source).

### 4.7 Alarms (5)

| ID | Operation | HASE implementation | Consumes | Tests |
|---|---|---|---|---|
| C27-026 | `GET /data/user/alarm/list` | `list_alarms` ← coordinator poll → per-alarm switch entities + alarm count sensor | `id`, `hour`, `minute`, `label`, `tag`, `repeat_mask`, `enabled` | PARTIAL |
| C27-079 | `POST /data/user/alarm` | `create_alarm` ← `hase.create_alarm` | ack | PARTIAL (lifecycle smoke only) |
| C27-120 | `PUT /data/user/alarm/{id}` | `update_alarm` ← `hase.update_alarm` | ack | PARTIAL (lifecycle smoke only) |
| C27-003 | `DELETE /data/user/alarm/{id}` | `delete_alarm` ← `hase.delete_alarm` | ack | PARTIAL (lifecycle smoke only) |
| C27-080 | `POST /data/user/alarm/{id}/disable` | `disable_alarm` ← `Code27AlarmSwitch.async_turn_off` | ack | **NONE — TEST DEBT** |
| C27-081 | `POST /data/user/alarm/{id}/enable` | `enable_alarm` ← `Code27AlarmSwitch.async_turn_on` | ack | **NONE — TEST DEBT** |

Request contract: `{alarm: {hour, minute, label, repeat_mask, enabled}}` where
`hour` 0–23, `minute` 0–59 and `repeat_mask` is a bitmask built from
`ALARM_DAY_BITS` (`sun=1, mon=2, tue=4, wed=8, thu=16, fri=32, sat=64`).

Quirks: `repeat_mask` is exposed as a switch attribute but never written back by
the enable/disable path; the HA update service always rewrites the full alarm
because `hour` and `minute` are required. The day-mask mapping is **not asserted
by any test**. Alarm switches are added/removed dynamically as the polled list
changes, using `force_remove=True` so a reappearing alarm id does not collide.

### 4.8 LED / light (6)

| ID | Operation | HASE implementation | Consumes | Tests |
|---|---|---|---|---|
| C27-048 | `GET /data/user/light-master-switch` | `get_light_master_switch` ← coordinator poll → light `is_on`, notification restore | `enabled` | PARTIAL |
| C27-093 | `POST /data/user/light-master-switch` | `set_light_master_switch` ← light on/off, notification trigger/acknowledge | ack | PARTIAL |
| C27-049 | `GET /data/user/light-projects` | `list_light_projects` ← coordinator poll → light-scene select, LED count | `id`, `name`, `ledCount` | PARTIAL |
| C27-051 | `GET /data/user/light-projects/applied` | `get_applied_light_project` ← coordinator poll → select current option | `projectId` | PARTIAL |
| C27-095 | `POST /data/user/light-projects/{id}/apply` | `apply_light_project` ← `Code27LightProjectSelect.async_select_option` | ack | **NONE — TEST DEBT** |
| C27-099 | `POST /data/user/light-projects/preview` | `preview_light_color` ← light `async_turn_on`, notification blink/restore | ack | PARTIAL |
| C27-100 | `POST /data/user/light-projects/preview/stop` | `stop_light_preview` ← light `async_turn_off`, notification acknowledge | ack | PARTIAL |

Two different CODE27 mechanisms sit behind the single HA light entity:

* **on/off** → the LED master switch (`C27-048`/`C27-093`);
* **colour / brightness / effect** → the ephemeral **draft preview** channel
  (`C27-099`/`C27-100`), which the device's own Light Editor uses while
  designing and which never touches a saved project.

Read vs write distinction, explicitly:

| Aspect | Status |
|---|---|
| Read LED configuration (`ledCount`, project list, applied project) | READ IMPLEMENTED (C27-049, C27-051) |
| Read the full reactive block / `segments` / `normal` | **NOT implemented** — the list endpoint does not return them, and C27-050 is unused |
| Write master switch | WRITE IMPLEMENTED (C27-093) |
| Write ad-hoc colour/effect | WRITE IMPLEMENTED (C27-099/100) |
| Write/apply a stored project | APPLY implemented (C27-095); create/update/delete **NOT implemented** |
| Live test status | NOT LIVE TESTED by this audit (static analysis only) |

Quirks:
* The preview project object is **authored by HASE** from the documented
  `LightProject` schema, with `ledCount` taken from the first polled project and
  defaulting to 17 (matching the known device's configuration).
* CODE27 has **no readback** for the preview channel. HASE therefore tracks the
  last requested colour/effect on the coordinator and documents that the light's
  colour/effect attributes describe what HA last asked for, not a
  device-confirmed state.
* Project names may legitimately collide; HASE disambiguates duplicate names by
  appending the project id so both remain selectable.
* The list endpoint exposes `id` while the single/applied endpoints expose
  `projectId` — HASE reads the right one in each place.
* Notification blink/restore order matters: stop the preview first, then switch
  the master off, and only commit local state after restoration succeeded.

### 4.9 Music, storage, Bluetooth and audio (11)

| ID | Operation | HASE implementation | Consumes | WS | Tests |
|---|---|---|---|---|---|
| C27-058 | `GET /data/user/music/page` | `get_music_page` ← poll → `Code27MusicModeBinarySensor` | `is_open` | ✅ | PARTIAL |
| C27-059 | `GET /data/user/music/playback` | `get_music_playback` ← poll → media player properties | `status`, `track_name`, `duration_ms`, `position_ms`, `volume_level`, `volume_max` | ✅ | PARTIAL |
| C27-060 | `GET /data/user/music/source` | `get_music_source` ← poll → media player `source` | `source` | ✅ | PARTIAL |
| C27-056 | `GET /data/user/music/audio-route` | `get_music_audio_route` ← poll (state kept, no entity) | stored whole, nothing reads fields | ✅ | PARTIAL |
| C27-057 | `GET /data/user/music/bluetooth` | `get_bluetooth_state` ← poll → `Code27BluetoothConnectedBinarySensor` | `is_connected`, `device_name`, `device_address`, `paired_device_count`, `is_bluetooth_enabled`, `local_device_name` | ✅ | PARTIAL |
| C27-104 | `POST /data/user/music/control` | `music_control` ← media player transport/volume/play_media, `hase.upload_music(play=true)` | `status`, `error_code`, `error_message` | ✅ | STRONG |
| C27-105 | `POST /data/user/music/source` | `set_music_source` ← `async_select_source` | ack | ✅ | **NONE — TEST DEBT** |
| C27-106 | `POST /data/user/music/upload` | `upload_music` ← `hase.upload_music` | `uploaded[].path`, `rejected[]` | ✅ | STRONG |
| C27-013 | `DELETE /data/user/music/file` | `delete_music_file` ← `hase.delete_music_file`; takes `?path=<absolute device path>` (percent-encoded) | ack | ✅ | STRONG |
| C27-107 | `POST /data/user/sound/play` | `play_sound` ← `Code27PlaySoundButton` | ack | — | **NONE — TEST DEBT** |
| C27-108 | `POST /data/user/sound/stop` | `stop_sound` ← `Code27StopSoundButton` | ack | — | **NONE — TEST DEBT** |

Request contract for `music/control`:

```
{action, source: "LOCAL_TF", request_id: "ha-<epoch_ms>", ...}
   action ∈ PLAY | PAUSE | NEXT | PREVIOUS | SET_VOLUME
   SET_VOLUME adds volume_level (int 0-100; HA's 0.0-1.0 float × 100)
   play_media adds track_id = "local:<absolute device path>"
```

**The trap that matters:** the device can answer HTTP 200 with envelope
`success:true` while the action actually failed — the real outcome is in a nested
`status`/`error_code` (`MODE_INACTIVE` when the music screen is closed,
`INVALID_ARGUMENT` when there is no active track). `music_control` detects this
and raises `Code27DeviceError`; the media player translates both codes into
actionable messages naming what the user must do on the device.

`music/upload` is multipart with the file field named `files`, repeated once per
file; a non-empty `rejected[]` is raised as a user-facing error rather than
ignored, and the authoritative `uploaded[].path` is reused verbatim as
`track_id = "local:<path>"` when `play=true`.

Quirks:
* An unrecognised playback `status` silently maps to `MediaPlayerState.IDLE`.
* `set_music_source` does **not** inspect the response for a nested `FAILED`
  status (unlike `music_control`), so a device-side source-switch failure would
  not be raised. Low severity — see the audit report.
* `is_applying`, `is_playing`, `play_mode` and `track_id` are available but
  unused by the media player entity.
* `paired_devices[]` is available but not exposed; only the count is.
* **`GET /data/user/music/storage` (C27-061)** was CONFIRMED LIVE and unused; it
  is now polled by `Code27Coordinator._poll_music_storage` as a second,
  authoritative source for the TF-card inventory (see §4.9 and §11).

### 4.10 Storage state (1 implemented)

Storage data reaches HASE through **two** sources, resolved into one field
(`Code27Data.music_storage`) by `providers/code27/music.py:merge_music_inventory`:

* the `music_storage` WebSocket topic (`_handle_ws_music_storage`), and
* the REST read of `GET /data/user/music/storage` in every full poll
  (`_poll_music_storage`), which also makes the inventory available before any
  push has been seen - including right after a Home Assistant restart.

| Field | Consumed by |
|---|---|
| `capacity_total_bytes`, `capacity_used_bytes` | `Code27MusicStorageSensor` (percent used) |
| `is_loaded`, `root_path`, `capacity_*`, file count | storage sensor attributes |
| `music_files[].path`, `music_files[].name` | media-player browser tree, `track_id`, the HASEAMP card's browser |

Merge policy (newest valid inventory wins): a push that arrived while the poll
was in flight beats that poll; otherwise a usable polled payload wins unless it
is older; an unusable payload (no `music_files` list) never replaces a valid
inventory. A push without an inventory is rejected outright
(`_handle_ws_music_storage`). There is no cache policy beyond that - the field
always holds the newest inventory HASE has actually seen, and starts `{}` only
when neither source has answered yet.

---

## 5. Operations NOT implemented (54)

Complete list. For these, no implementation location is recorded, because none
exists — recording a hypothetical one would be misleading.

### 5.1 Deliberately not offered: destructive or device-owner actions (9)

| ID | Method + path | Why not in HASE |
|---|---|---|
| C27-004 | `DELETE /data/user/chat/history` | wipes a whole conversation; HASE only removes messages it wrote itself |
| C27-006 | `DELETE /data/user/chat/history/slot/{slot}` | destructive per-slot wipe |
| C27-011 | `DELETE /data/user/memory/cards/{id}` | no undo; HASE reads and edits memory cards only |
| C27-082 | `POST /data/user/character/reset` | irreversible character reset |
| C27-083 | `POST /data/user/character/reset/authorize` | confirmation step of the same destructive flow |
| C27-030 | `GET /data/user/character/reset/active` | status half of a flow HASE never starts |
| C27-031 | `GET /data/user/character/reset/status` | status half of a flow HASE never starts |
| C27-117 | `PUT /data/user/ai-language` | a device-wide setting; HASE passes a per-message `language` override instead |
| C27-101 | `POST /data/user/log` | HASE logs through Home Assistant, not the device's log sink |

### 5.2 Absent feature families (24)

| ID | Method + path | Feature family |
|---|---|---|
| C27-007 | `DELETE /data/user/cloth/cache` | cloth cache management |
| C27-039 | `GET /data/user/cloth/cache` | cloth cache management |
| C27-040 | `GET /data/user/cloth/download/result` | async cloth download job |
| C27-041 | `GET /data/user/cloth/download/status` | async cloth download job |
| C27-042 | `GET /data/user/cloth/upload/status` | async cloth upload job |
| C27-087 | `POST /data/user/cloth/download/async` | async cloth download job |
| C27-088 | `POST /data/user/cloth/upload/job` | VRM upload job |
| C27-089 | `POST /data/user/cloth/upload/job/{id}/file` | VRM upload job |
| C27-009 | `DELETE /data/user/feedback/jobs/{id}` | feedback/diagnostic jobs |
| C27-046 | `GET /data/user/feedback/jobs/{id}` | feedback/diagnostic jobs |
| C27-047 | `GET /data/user/feedback/jobs/{id}/download` | feedback/diagnostic jobs |
| C27-091 | `POST /data/user/feedback/jobs` | feedback/diagnostic jobs |
| C27-092 | `POST /data/user/feedback/jobs/{id}/submit` | feedback/diagnostic jobs |
| C27-110 | `POST /data/user/voice_clone/confirm` | voice cloning (vendor cloud) |
| C27-111 | `POST /data/user/voice_clone/delete` | voice cloning (vendor cloud) |
| C27-114 | `POST /data/user/voice_clone/upload` | voice cloning (vendor cloud) |
| C27-062 | `GET /data/user/outfit/context` | outfit UI context (state read from the character list instead) |
| C27-029 | `GET /data/user/character/loading/state` | model-loading indicator |
| C27-084 | `POST /data/user/chat/cancel` | abort an in-flight generation — **useful, see §11** |
| C27-034 | `GET /data/user/chat/history/count/slot/{slot}` | bare per-slot count; unusable for change detection |
| C27-037 | `GET /data/user/chat/history/page` | cursor paging; whole-day reads already suffice |
| C27-038 | `GET /data/user/chat/history/range` | one-call date range — **CONFIRMED LIVE, unused, see §11** |
| C27-070 | `GET /resources/user/chat-audio` | chat audio clip |
| C27-071 | `GET /resources/user/chat-audio-bundle` | chat audio bundle |

### 5.3 Light-project editing (7)

| ID | Method + path | Note |
|---|---|---|
| C27-010 | `DELETE /data/user/light-projects/{id}` | HASE treats projects as device-authored content |
| C27-050 | `GET /data/user/light-projects/{id}` | only the applied project is needed (C27-051) |
| C27-094 | `POST /data/user/light-projects` | |
| C27-096 | `POST /data/user/light-projects/{id}/compile` | the device compiles implicitly on apply |
| C27-123 | `PUT /data/user/light-projects/{id}` | |
| C27-097 | `POST /data/user/light-projects/{id}/preview` | stored-project preview; HASE uses the draft preview (C27-099) |
| C27-098 | `POST /data/user/light-projects/{id}/preview/stop` | stored-project preview stop |

### 5.4 Proactive engine (2)

| ID | Method + path | Note |
|---|---|---|
| C27-063 | `GET /data/user/proactive/config` | the word "proactive" does not occur anywhere in HASE |
| C27-126 | `PUT /data/user/proactive/config` | capability-gated as well (`admin.proactive_config` = false) |

HASE neither reads, writes, exposes, nor coordinates proactive behaviour. It
makes no claim to do so, so this is a scope boundary rather than a defect.

### 5.5 Redundant or low-value reads (6)

| ID | Method + path | Why unused |
|---|---|---|
| C27-018 | `GET /data/user/ability/group-keys` | **stale premise** — the path IS confirmed live; see §10 |
| C27-019 | `GET /data/user/ai-language` | the same value arrives in `GET /data/user/system` |
| C27-025 | `GET /data/user/alarm/{id}` | the full list is already polled |
| C27-027 | `GET /data/user/character` | the list endpoint returns every slot in one call |
| C27-055 | `GET /data/user/memos/{id}` | the to-do entity resolves from the polled list |
| C27-103 | `POST /data/user/music/bluetooth/devices/forget` | the only Bluetooth write; there is no pairing endpoint, so HA could never complete a pairing |

### 5.6 Implemented in code but with NO runtime path (3) — dead or test-only

| ID | Method + path | Evidence of dead code |
|---|---|---|
| C27-053 | `GET /data/user/memory/cards/{id}` | `Code27Client.get_memory_card` exists and has **no call site at all** — not even a test. Deliberately classified NOT_IMPLEMENTED: a path string is not an implementation. |
| C27-068 | `GET /resources/system/original-portraits/manifest` | `Code27Client.get_original_portraits_manifest` is called **only from a test**. Its return expression reads `.get("data", [])` and is annotated `list[dict]`, but the confirmed payload is an **object** `{exported_at_ms, portraits[]}` — so the wrapper is both dead and shape-wrong. HASE sidesteps it by attempting the portrait path directly and treating 404 as "no portrait". |
| C27-072 | `GET /resources/user/chat-images` | `Code27Client.get_chat_image` is called **only from a test**. No service, entity or card renders chat images (HASE's own `docs/CODE27-FEATURE-STATUS.md` records that card display was deliberately deferred). |

### 5.7 Diary writes (2)

| ID | Method + path | Status and reason |
|---|---|---|
| C27-008 | `DELETE /data/user/diary/entries/{id}` | NOT_IMPLEMENTED — the source justification is wrong (see §10) |
| C27-122 | `PUT /data/user/diary/entries/{id}` | NOT_IMPLEMENTED — the route demonstrably exists (an empty update answers a normal JSON validation error, `empty_update`), so the "all HTTP 404" claim in the source is false |

### 5.8 Storage (1)

| ID | Method + path | Status and reason |
|---|---|---|
| C27-061 | `GET /data/user/music/storage` | **IMPLEMENTED** (was the audit's highest-value unused read). `Code27Client.get_music_storage()` (+ optional `?refresh=1`) is polled by `Code27Coordinator._poll_music_storage` and merged with the `music_storage` WebSocket topic in `providers/code27/music.py:merge_music_inventory`, so the TF-card inventory no longer depends on a push arriving. |

---

## 6. Capability-gated operations (21)

These are blocked by the **CODE27 device**, not by HASE. Evidence: the
`capabilities` object returned by `GET /data/user/system` on the known device.

### 6.1 Capability flags on the known device

| Capability flag | Device state | Affected operations (ids only; full paths in §6.2) |
|---|---|---|
| `admin.user_settings` | **false** | C27-001, C27-016, C27-073, C27-116 |
| `admin.system_settings` | **false** | C27-014, C27-115 |
| `admin.*` (tunnel status) | **false** | C27-015 |
| `lab.ai_provider` | **false** | C27-002, C27-020, C27-021, C27-022, C27-023, C27-024, C27-076, C27-077, C27-078, C27-118, C27-119 |
| `voice.clone_record_debug` | **false** | C27-067, C27-112, C27-113 |

### 6.2 The 21 capability-gated operations

| ID | Method + path | Capability | Device state | Why HASE does not use it | If the capability became available |
|---|---|---|---|---|---|
| C27-001 | `DELETE /admin/user/settings/{id}` | `admin.user_settings` | false | no admin feature in HASE | nothing would change without an admin-UI design; HASE's remit is the device API, not device administration |
| C27-002 | `DELETE /data/user/ai/provider-profiles/{id}` | `lab.ai_provider` | false | no AI-provider management in HASE | still out of scope: it means handling third-party provider credentials and vendor-cloud reachability |
| C27-014 | `GET /admin/system/settings` | `admin.system_settings` | false | no admin feature | see C27-001 |
| C27-015 | `GET /admin/tunnel/status` | `admin.*` | false | tunnel internals are explicitly out of scope | a diagnostic sensor could be added, but no assumption about tunnel internals should be introduced |
| C27-016 | `GET /admin/user/settings` | `admin.user_settings` | false | no admin feature | see C27-001 |
| C27-020 | `GET /data/user/ai/backend` | `lab.ai_provider` | false | no AI-provider management | see C27-002 |
| C27-021 | `GET /data/user/ai/capability-routes` | `lab.ai_provider` | false | no AI-provider management | see C27-002 |
| C27-022 | `GET /data/user/ai/provider-errors/latest` | `lab.ai_provider` | false | no AI-provider management | see C27-002 |
| C27-023 | `GET /data/user/ai/provider-profiles` | `lab.ai_provider` | false | no AI-provider management | see C27-002 |
| C27-024 | `GET /data/user/ai/providers/catalog` | `lab.ai_provider` | false | no AI-provider management | see C27-002 |
| C27-067 | `GET /data/user/voice_clone/device_record/state` | `voice.clone_record_debug` | false | no voice-clone feature | still out of scope: voice cloning requires the vendor cloud |
| C27-073 | `POST /admin/user/settings` | `admin.user_settings` | false | no admin feature | see C27-001 |
| C27-076 | `POST /data/user/ai/backend/switch` | `lab.ai_provider` | false | no AI-provider management | switching a device's AI backend is the device owner's decision on the device |
| C27-077 | `POST /data/user/ai/provider-profiles` | `lab.ai_provider` | false | no AI-provider management | see C27-002 |
| C27-078 | `POST /data/user/ai/provider-profiles/{id}/test` | `lab.ai_provider` | false | no AI-provider management | see C27-002; HASE must not contact vendor cloud services |
| C27-112 | `POST /data/user/voice_clone/device_record/start` | `voice.clone_record_debug` | false | no voice-clone feature | see C27-067 |
| C27-113 | `POST /data/user/voice_clone/device_record/stop` | `voice.clone_record_debug` | false | no voice-clone feature | see C27-067 |
| C27-115 | `PUT /admin/system/settings/{id}` | `admin.system_settings` | false | no admin feature | see C27-001 |
| C27-116 | `PUT /admin/user/settings/{id}` | `admin.user_settings` | false | no admin feature | see C27-001 |
| C27-118 | `PUT /data/user/ai/capability-routes/{id}` | `lab.ai_provider` | false | no AI-provider management | see C27-002 |
| C27-119 | `PUT /data/user/ai/provider-profiles/{id}` | `lab.ai_provider` | false | no AI-provider management | see C27-002 |

Rules for keepers of this list:

* A server-side capability gate is **never** a HASE implementation bug.
* HASE must not attempt bypasses, and must not contact vendor cloud services.
* HASE surfaces the capability flags read-only (diagnostics sensor) rather than
  building gating logic that cannot be verified on a device that has the flag
  disabled — an explicit, documented decision.

### 6.3 Capability discovery and representation

| Concern | Current HASE behaviour |
|---|---|
| Discovery | `GET /data/user/system` → `capabilities` object, polled every cycle |
| Representation | `Code27CapabilitiesSensor`: state = count of enabled flags, attribute = the raw dict |
| Gated endpoint handling | none — no endpoint is skipped based on a flag |
| Unavailable feature handling | the endpoint fails and the **whole poll** fails (see the correctness finding in the audit report) |
| Feature exposure | a disabled capability is *observable*, not *enforced* |

| Endpoint | Capability | Device state | HASE status | Why not used | If the capability became available |
|---|---|---|---|---|---|
| `/admin/*` | `admin.user_settings`, `admin.system_settings` | false | CAPABILITY_GATED | code absent | nothing would change without an admin feature design; HASE's remit is the device API, not device administration |
| `/data/user/ai/*` | `lab.ai_provider` | false | CAPABILITY_GATED | code absent | still out of scope: managing AI providers means handling third-party credentials and vendor cloud reachability |
| `/voice_clone/device_record/*` | `voice.clone_record_debug` | false | CAPABILITY_GATED | code absent | voice cloning requires the vendor cloud, so HASE would still not implement it |

---

## 7. Intentionally unsupported (1)

| ID | Method + path | Status | Evidence |
|---|---|---|---|
| C27-090 | `POST /data/user/diary/entries/{id}/feedback` | **INTENTIONALLY_UNSUPPORTED** | The route answers a **router-level plain-text `404 page not found`** even with a valid signature, while the identically shaped `PUT` answers a normal JSON validation error. The shipped frontend calls it via `submitDiaryFeedback`; the backend does not register it (frontend/backend version skew). |

This is the one operation where "CODE27 does not expose it" is **proven** rather
than assumed. `inventory.json` records the frontend's *intent* to call the route;
only the live 404 proves the route is absent.

---

## 8. Request/response contract notes that must not be lost

* `voice_id` is a **STRING**, never an integer.
* `chat_id` is a **STRING**; `history_id` is unusable for messages CODE27
  classifies as commands (they stay `0`, and `[0]` is a dangerous wildcard).
* `characterId` on memory/diary routes is the character's **`cloud_id`**, not
  `slot_number`.
* `slot` is an **int** in JSON bodies but a **string** in the multipart field of
  `chat/image-text`.
* Multipart uploads are signed with `UNSIGNED-PAYLOAD`; the body is not hashed.
* The raw JSON body is hashed **byte-exactly** — HASE serializes with
  `separators=(",", ":")` and sends those same bytes.
* `DELETE /data/user/music/file` takes an **absolute** path as a query
  parameter; the query is stripped before signing (payload mode `empty`).
* `delete_music_file` percent-encodes the path.
* `GET /data/user/music/storage`'s `music_files[].path` is **absolute**
  (`/storage/<vol>/code27/music/...`) — see the media-browser finding.
* `light-projects` list items expose `id`; the single/applied endpoints expose
  `projectId`.
* `version` means a numeric revision counter on `music/*` and a firmware version
  **string** on `/data/user/system`.
* The 1 MB limit on chat images is a **device** limit enforced client-side.
* Empty write acknowledgements (no `data` member, e.g. a 204) are valid.

### 8.1 Response contract: available vs actually consumed

| Endpoint | CODE27 provides | HASE consumes |
|---|---|---|
| `GET /data/user/system` | `ai_language, build_type, capabilities, feature_tag, language, local_ip, machine_id, timezone, use_24_hour_time, user_id, username, voice_audition_text` | `machine_id, local_ip, capabilities, ai_language, timezone, build_type, use_24_hour_time` |
| `GET /data/user/music/storage` | `is_loaded, root_path, music_files[], capacity_total_bytes, capacity_used_bytes, updated_at_ms, version` | (all of it, but **only** via the WebSocket topic — the REST endpoint is unused) |
| `GET /data/user/music/playback` | `request_id, status, track_id, track_name, duration_ms, position_ms, is_playing, volume_level, volume_max, error_code, error_message, play_mode, updated_at_ms, version` | `status, track_name, duration_ms, position_ms, volume_level, volume_max` |
| `GET /data/user/music/source` | `source, origin, request_id, is_applying, error_code, error_message, updated_at_ms, version, music_session_id` | `source` |
| `GET /data/user/music/bluetooth` | `is_connected, device_name, device_address, paired_device_count, is_bluetooth_enabled, local_device_name, paired_devices[], operation` | all except `paired_devices[]` and `operation` |
| `GET /data/user/character/list` | every character field incl. `cloud_id`, `sex`, `animation_intensity`, `character_prompt`, `tts_volume`, cloth lists | `slot_number, name, selected_cloth, voice_id, group_key` |
| `GET /data/user/cloth/uploaded-models` | `models[].filename/relative_path/size_bytes/slot_number/cloth_id/state`, `root`, `total` | `slot_number, cloth_id, filename` |
| `GET /data/user/chat/history/count` | `[{slot, slot_id, messages[]}]` | `slot`, `messages[-1].content/role/created_at` |
| `GET /data/user/chat/history` | full `ChatMessage` incl. `content_type` and metadata/VLM fields | `chat_id, history_id, role, content, created_at` |
| `GET /data/user/light-projects` | project items incl. `ledCount`, `mode` | `id, name, ledCount` |
| `GET /data/user/light-projects/applied` | applied project object | `projectId` |
| `GET /data/user/memos` | `id, title, body, memo_type, is_expired, next_fire_at_ms, ...` | `id, title, body, is_expired, next_fire_at_ms` |
| `GET /cache`/diary/memory reads | full payloads | passed through unchanged to the service caller |
| multi-value query support (`chat-audio-bundle`'s repeatable `clip_id`) | available | not used anywhere |

Claiming that HASE "supports" a field merely because CODE27 returns it is
explicitly wrong; the table above is the authority.

---

## 9. WebSocket ledger

Endpoint `ws://<host>:<port>/v1/ws/channel`, implemented by
`Code27WebSocket` (`websocket_client.py`), URL built by
`Code27Client.build_ws_url`, started by
`Code27Coordinator.async_start_realtime`.

Frame shape: `{"type": "<topic>", "data": {...}}`. Fragmented frames are
reassembled by aiohttp (iteration yields complete messages); binary frames are
ignored; malformed JSON is dropped; a handler exception is caught per handler and
does not kill the connection. Reconnect is exponential back-off (2 s doubling to
a 60 s cap, retried indefinitely) with `heartbeat=30`.

| Topic | CODE27 semantics | HASE subscribed | Handler | State updated | Fallback polling | Reconnect safe | Test | Known limitation |
|---|---|---|---|---|---|---|---|---|
| `music_storage` | full TF snapshot; re-sent unprompted ~every 50 s | ✅ | `_handle_ws_music_storage` | `Code27Data.music_storage` | ✅ C27-061 (`_poll_music_storage`) | ✅ | PARTIAL | Resolved: the REST read of `GET /data/user/music/storage` is polled, so a lost WebSocket no longer leaves the media browser + storage sensor empty. An inventory-less push is rejected instead of replacing a valid one. |
| `music_page` | `is_open`, version | ✅ | `_handle_ws_music_page` | `music_page` | ✅ C27-058 | ✅ | PARTIAL | — |
| `music_playback` | status, track, position, volume, play mode | ✅ | `_handle_ws_music_playback` | `music_playback` | ✅ C27-059 | ✅ | PARTIAL | — |
| `music_source` | source, origin, `is_applying` | ✅ | `_handle_ws_music_source` | `music_source` | ✅ C27-060 | ✅ | PARTIAL | `is_applying` unused |
| `music_audio_route` | route, wired_headset | ✅ | `_handle_ws_music_audio_route` | `music_audio_route` | ✅ C27-056 | ✅ | PARTIAL | no control endpoint exists anyway |
| `bluetooth_state` | connection, paired devices | ✅ | `_handle_ws_bluetooth_state` | `bluetooth_state` | ✅ C27-057 | ✅ | PARTIAL | `paired_devices[]` not exposed |
| `chat_history` | `event`, `version`, `changed_at_ms`, `scope` — **DEAD** | ❌ **correctly not subscribed** | — | — | ✅ 3 s chat poll | ✅ | NONE | None — HASE does **not** treat this as a reliable chat source, matching the final research |

**Ordering/freshness rule:** an incoming push and a poll response race. A
per-field monotonic revision counter plus a two-key freshness comparison
(`updated_at_ms`, then `version`) ensures an older HTTP response can never
overwrite a newer push. A partial push deliberately neither reschedules the full
poll nor repairs its error state.

**Reconnect safety** comes from the device re-sending a full snapshot per topic
on every (re)connect, so no resubscription protocol is needed.

**Known WebSocket limitations:**

1. `music_storage` is WS-only — if the socket never connects or is permanently
   lost, the media browser and storage sensor stay empty even though
   `GET /data/user/music/storage` is available.
2. A session-scoped credential would make the handshake fail with 401 and there
   is no recovery path other than re-pairing (see quirk Q-A).
3. `music_storage` is re-sent with the full 31-file list roughly every 50 s; the
   duplicate/stale guard absorbs this, but it is chatty traffic by design.

---

## 10. Resource ledger

Only resources proven by the final research are listed. No guessed paths.

| Resource | Discovery source | Addressing rule | Local | Auth | HASE status | Cache policy | Implementation | Test |
|---|---|---|---|---|---|---|---|---|
| original-portrait PNG | `GET /resources/system/original-portraits/manifest` → `portraits[].portrait_path` | `/resources/system/assets/original-portraits/{slot_number}_{cloth_id}.png` | ✅ | ✅ signed | **IMPLEMENTED** (stock fallback) | none; re-fetched only when that slot's `selected_cloth` changes | `Code27Client.get_character_portrait` ← `Code27PortraitImage.async_image` | STRONG |
| custom cloth thumbnail | `GET /data/user/cloth/uploaded-models` → `models[].filename` | `/resources/user/assets/thumbnails/{slot_number}_{cloth_id}_{filename-stem}.png` | ✅ | ✅ signed | **IMPLEMENTED** (preferred source) | none | `get_character_portrait` + `get_cloth_uploaded_models` | STRONG |
| uploaded model metadata / VRM relationships | `GET /data/user/cloth/uploaded-models` | JSON: `models[].filename, relative_path, size_bytes, slot_number, cloth_id, state`; `root`, `total` | ✅ | ✅ signed | **PARTIAL** — only `slot_number`/`cloth_id`/`filename` consumed; the VRM binary itself is **not** HTTP-retrievable | none | `get_cloth_uploaded_models` | PARTIAL |
| chat image | message with `content_type == "image"`; its `content` carries the uuid | `/resources/user/chat-images?uuid=<uuid>` | ✅ | ✅ signed | **NOT_IMPLEMENTED** — client method exists, no production caller (test-only) | none | — | NONE |
| chat audio / audio bundle | inventory | `/resources/user/chat-audio`, `/resources/user/chat-audio-bundle?clip_id=` (repeatable) | ✅ | ✅ signed | **NOT_IMPLEMENTED** — the string `chat-audio` does not occur in HASE | none | — | NONE |

For cloth thumbnails, only the addressing relationship supported by the evidence
is relied on: **thumbnail name = `{slot}_{cloth}_{stem}` of the uploaded model
file** (`relative_path` `5/2/Ginny.vrm` → `thumbnails/5_2_Ginny.png`, HTTP 200).
A 404 is a valid answer meaning "no custom thumbnail for this slot/cloth".

Documentation correction recorded for the record: `CODE27-LOCAL-API.md` lists
`GET /resources/user/assets/thumbnails/{id}` as *CONFIRMED FROM CODE*, but the
later phase-4 local-data work actually **downloaded** two thumbnails with HTTP
200 — so its correct verification status is **VERIFIED_LIVE**. The newest
research supersedes the older note.

---

## 11. Test debt

Implemented or partial areas with insufficient behavioural tests. This list is
the reason to trust the counts in §1 rather than the presence of a test file.

| Operations | Gap | What a test should assert |
|---|---|---|
| C27-127 `PUT /data/user/slot`; `Code27CharacterSlotSelect` | **no test at all** | the exact body `{slot_number, scope:"chat"}`; that a slot switch re-baselines the chat cursor |
| C27-080 / C27-081 alarm enable/disable; `Code27AlarmSwitch` | **no test at all** | the enable/disable call; dynamic add/remove of switches; `force_remove` behaviour |
| C27-003 / C27-079 / C27-120 alarm delete/create/update | smoke only | `repeat_mask` bitmask from `ALARM_DAY_BITS`; required `hour`/`minute` |
| C27-102 / C27-125 / C27-012 memo create/update/delete; `Code27MemoTodoList` | **no test at all** | request bodies; that a pure status change raises instead of no-op'ing |
| C27-095 `light-projects/{id}/apply`; `Code27LightProjectSelect` | **no test at all** | the id used for apply; duplicate-name disambiguation; `projectId` readback |
| C27-105 `POST /data/user/music/source` | **no test at all** | body `{source, request_id}`; error handling (currently absent) |
| C27-107 / C27-108 sound play/stop buttons | **no test at all** | the button → client call |
| C27-005 `DELETE /chat/history/items`; `async_cleanup_assist_exchange` | **no test at all** | `chat_ids` body; reply-hunting ordering; single 2 s retry |
| C27-052 `GET /memory/cards`; C27-045 `GET /diary/entries/{id}` | **no test at all** | query parameters (`characterId`, `page`, `pageSize`) and pass-through |
| C27-066 TTS state polling | partial | the `FAILED` branch and the 15 s timeout branch |
| C27-059 media player property mapping | partial | `state`, `media_title`, `media_duration`, `media_position`, `volume_level` from a playback payload |
| C27-099 preview payload contract | partial | the synthesized project object's shape/`ledCount` |
| `_group_music_files` / media browser | **misleading** | all existing tests use **relative** paths, while the live payload is absolute — see the correctness finding in the audit report |

Reminder: a test that merely imports a module, or that mocks an HTTP 200 without
asserting the request/response semantics, is **not** coverage.

---

## 12. Known quirks that must not be forgotten

| # | Quirk |
|---|---|
| Q-A | `auth_scope` / `local_session_id` are not handled anywhere. HASE relies on receiving a `permanent` credential; a session-scoped one would 401 everywhere with no recovery but re-pairing. |
| Q-B | `machine_id` is used as durable identity (entry `unique_id`, entity `unique_id`s, reauth matching) although the final research states it is a transient pairing token. It is simultaneously the required `device_id` value for `PUT /data/user/character`. |
| Q-C | Resolved: `music_storage` now has a REST fallback (`GET /data/user/music/storage`, C27-061), and the two source comments that wrongly claimed no REST endpoint exists have been corrected. |
| Q-D | Resolved: the media browser grouped `music_files[].path` as if relative while the live payload is absolute (one oddly named top-level folder). `providers/code27/music.py:relative_music_path` now normalises against `root_path` and the device's own `code27/music` marker, and the browser tests use the live absolute-path shape. |
| Q-E | `GET /data/user/ability/group-keys` is claimed in a source comment (and in `docs/CODE27-FEATURE-STATUS.md`) to have an unconfirmed path; it is CONFIRMED LIVE. |
| Q-F | Diary writes are described in a source comment as "all HTTP 404"; only the `feedback` route is genuinely absent. |
| Q-G | `Code27Client.get_original_portraits_manifest` is dead code **and** shape-wrong (`data` is an object, not an array). It is exempted from shape validation, hiding the defect. |
| Q-H | `Code27Client.get_memory_card` and `Code27Client.get_chat_image` have no production call sites (dead / test-only). |
| Q-I | TTS: the `success:true` acknowledgement means *accepted only*; completion comes from state polling. `voice_id` is a string. |
| Q-J | `music/control` can fail inside a `200`/`success:true` envelope (nested `FAILED`); `music/source` does **not** check for that. |
| Q-K | `chat_history` is a dead WebSocket topic — HASE correctly polls instead. |
| Q-L | Duplicate light-project and character-slot names are disambiguated by appending the id/slot, because the device allows duplicates. |
| Q-M | `is_expired` is time-computed, not a completion flag; memory-card edits permanently set `isUserEdited`. |
| Q-N | One failing polled endpoint fails the entire 14-endpoint poll (no per-endpoint isolation). |
| Q-O | The preview light's colour/effect reflect what HA last requested, because CODE27 has no preview readback. |
| Q-P | Remote IP recovery is opt-in, read-only, cooldown-limited (30 s) and validates the recovered address locally by `machine_id` before trusting it; it is never used as a general request fallback and never retried for writes. |

---

## 13. Change control contract

### 13.1 When the CODE27 API changes

1. Update the authoritative CODE27 API analysis under `C:\USERDATA\HA-Code27 API`.
2. Add a **new** stable `C27` id for every new operation (next free number).
3. **Never renumber** an existing id, and never reuse a retired one.
4. Update this document (`implemented-api.md`).
5. Update the machine-readable ledger (`implemented-api.yaml`).
6. Implement or change HASE if appropriate.
7. Add or update tests.
8. Update the semantic notes and the **consumed** response fields.
9. Run the conformance validation described in §14.

### 13.2 When the HASE CODE27 implementation changes

1. Update the implementation location (file + symbol).
2. Update the call path and call sites.
3. Update the consumed response fields.
4. Update the tests and their coverage classification.
5. Update the implementation status.
6. Update the semantic/quirk notes.
7. Keep Markdown and YAML synchronized — they must always contain the identical
   operation set.

### 13.3 When an upstream endpoint disappears

Do **not** delete its historical entry.

1. Keep the entry and its `C27` id.
2. Mark it `REMOVED_UPSTREAM`.
3. Record the version/date where known.
4. Never reuse the id.

---

## 14. Conformance check (design recommendation — not yet implemented)

A lightweight script can make a future manual audit unnecessary. It should
assert:

1. the authoritative inventory parses and still contains the expected count;
2. every inventory `METHOD + PATH` appears **exactly once** in the ledger;
3. no duplicate `METHOD + PATH` and no duplicate `C27` id;
4. `C27` ids are stable against a recorded baseline (no renumbering/reuse);
5. every referenced implementation file exists;
6. every referenced symbol exists in one of that operation's declared files;
7. every referenced test file exists;
8. every referenced test case exists (where practical to verify);
9. every `IMPLEMENTED`/`PARTIAL` operation has non-empty implementation evidence
   (files, symbols, call sites, operation);
10. `implemented-api.md` and `implemented-api.yaml` contain identical operation
    sets (compare the id → `METHOD + PATH` mapping extracted from the tables);
11. the WebSocket topic set and the resource set agree between Markdown and YAML;
12. summary counts inside the ledger match the operations it contains;
13. `TEST DEBT` markers remain present and countable.

Checks 1–12 above were executed once, mechanically, while producing this
revision; the result and the exact commands are recorded in the audit report
`CODE27-HASE-CURRENT-IMPLEMENTATION-AUDIT.md`.
