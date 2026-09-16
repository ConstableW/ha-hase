# HASE MCP — Model Context Protocol frontend

A fourth HASE frontend (alongside CODE27, Assist/conversation and Spellbook
Studio): external MCP clients (agents, other LLM tools) call an already-known
Spell by id over the network, the same way CODE27/Assist already trigger one
through their own frontends. MCP is never a second control engine — every
call runs through the existing `core.spellbook_engine`/`core.executor`
machinery, with every existing security boundary (Assist exposure,
action/query `AreaPolicy`, expert-tier gating) enforced exactly as it is for
any other frontend.

This document covers the whole MCP frontend: architecture, setting up a
Server/Bridge, the wire protocol, what each Spell looks like as a tool,
permissions/limits, diagnostics (including confirmed live findings), and a
pointer to the developer guide for building a *different* Bridge/frontend
adapter. Every claim below is checked against the real implementation the
files it cites — nothing here is aspirational or planned-but-not-built.

## 1. Overview and architecture

```
MCP client  --Streamable HTTP, Bearer token-->  providers/mcp/transport.py
                                                  (tools/list, tools/call)
                                                        |
                                                        v
                                    core/mcp_projection.py (tools/list only)
                                    core/mcp_execution.py  (tools/call only)
                                                        |
                                                        v
                        core/spellbook_engine.py  (find_command_by_id,
                        build_draft / build_query_plan / build_step_plan /
                        build_text_response)
                                                        |
                                                        v
                             core/executor.py::ActionExecutor
                        (AreaPolicy/exposure enforcement, real HA calls
                         via spellbooker_ha.py's HaTargetResolver/
                         HaServiceCaller — or, for assist_redirect,
                         spellbooker_ha.py::HaAssistForwarder ->
                         Home Assistant's own configured conversation agent)
                                                        |
                                                        v
                         optional response text  -->  MCP TextContent
```

**What MCP itself decides** (`providers/mcp/`, `core/mcp_projection.py`,
`core/mcp_execution.py`): which Bridge a bearer token belongs to, what a
Spell's `tools/list` shape looks like *for that Bridge* (argument
required/optional/omitted — see CASE A/B/C below), and how to map a
`tools/call`'s JSON `arguments` back onto HASE's own `captures` dict
(`build_captures`).

**What only the shared engine ever decides** (`core/spellbook_engine.py`,
`core/executor.py`) — identical for every frontend, MCP included: what a
Spell's `operation`/`sources`/`steps`/`output`/`assist_redirect` actually
*mean*, whether a target is permitted (`AreaPolicy`), whether it is even
*exposed* to Assist, expert-tier gating, and how a response is worded
(`response_text_for_success`). MCP never re-implements or second-guesses
any of this — a call that reaches `core/mcp_execution.py::execute_mcp_tool_
call` runs through the exact same `ActionExecutor` any other frontend uses.

## 2. Setup and connecting a client

**Prerequisites**: HASE installed, Home Assistant reachable from wherever
the MCP client runs, and at least one Spellbook already assigned to a
Bridge (see section 3). The `mcp`/`uvicorn` Python packages are installed
automatically as part of HASE's own `manifest.json` requirements — nothing
extra to install for the HA side.

**Listener**: one "HASE MCP Server" entry owns a single TCP listener,
configured with one field — `CONF_MCP_SERVER_PORT` (default `8815`,
`providers/mcp/const.py`). It binds `0.0.0.0` (reachable from the whole
LAN, not just `localhost`) — the per-Bridge bearer token is the *only*
access control; there is no separate network-level restriction. Put it
behind your own reverse proxy (with its own TLS) if you want it reachable
from outside your LAN — HASE itself never does this, and never should be
exposed to the open internet without one.

**Endpoint**: `http://<home-assistant-host>:<configured port>/mcp` — the
one and only path, Streamable HTTP (`mcp.server.lowlevel.Server`,
`stateless_http=True`, see section 4 for what that means for sessions).
This is a **different port and a different service** from Home Assistant's
own web UI (`http://<host>:8123` or your own reverse-proxy origin) — do not
assume the two share a port, and do not guess a port; read it from the MCP
Server's own "Listener-Port" field in **Settings → Devices & Services →
HASE MCP Server**.

**Authentication**: every request carries `Authorization: Bearer <token>` —
a Bridge's own 32-byte `secrets.token_urlsafe` token (section 3, section
7). There is no other authentication mechanism and no session cookie;
Streamable HTTP + Bearer is the whole story.

### Copy-paste examples

Replace `<host>`, `<port>` and `<TOKEN>` — **never commit a real token
anywhere**; the examples below use an obviously-fake placeholder on
purpose.

**curl** (a minimal `initialize` call — MCP's Streamable HTTP responses are
Server-Sent Events, so a real client library is normally what you want, but
this proves the endpoint and token are reachable/valid):

```bash
curl -N -X POST "http://<host>:<port>/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer <TOKEN>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl-check","version":"1.0"}}}'
```

Both `application/json` *and* `text/event-stream` must be present in
`Accept` — the server replies `406 Not Acceptable` (a real, verified
response — see section 6) otherwise. This is a genuine MCP/Streamable-HTTP
protocol requirement, not a HASE-specific quirk.

**PowerShell** (same call, `Invoke-WebRequest` does not stream SSE well —
use this only to confirm reachability/auth, not for a real session):

```powershell
$headers = @{ "Authorization" = "Bearer <TOKEN>"; "Content-Type" = "application/json"; "Accept" = "application/json, text/event-stream" }
$body = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"pwsh-check","version":"1.0"}}}'
Invoke-WebRequest -Uri "http://<host>:<port>/mcp" -Method Post -Headers $headers -Body $body -UseBasicParsing
```

**Official MCP Inspector** (`npx @modelcontextprotocol/inspector`): choose
transport **Streamable HTTP**, URL `http://<host>:<port>/mcp`, and add an
`Authorization: Bearer <TOKEN>` header in its own "Headers" panel before
connecting. This is the same client-side mechanism `tests/_mcp_protocol_
check.py` drives programmatically via the official `mcp` Python SDK
(`streamable_http_client`) — see section 7.

**A supported client generally**: any MCP client that supports Streamable
HTTP with a custom `Authorization` header works — HASE adds nothing
client-specific. If a client only supports stdio transport or OAuth-style
auth flows, it cannot reach this endpoint at all (see section 6).

## 3. HASE MCP pages and UI

- **MCP Server** (`ENTRY_KIND_MCP_SERVER`, `providers/mcp/config_flow.py`
  `McpFlowMixin`/`McpServerOptionsFlow`): a singleton top-level `ConfigEntry`.
  Pure infrastructure — no Spellbook, area or scope of its own, only a
  listener port (`CONF_MCP_SERVER_PORT`, default `8815`). Exactly one may
  exist; the HASE root menu (`config_flow.py::HaseConfigFlow.async_step_user`)
  hides the option once it does (`async_mcp_server_exists`).
- **MCP Bridge** (`SUBENTRY_TYPE_MCP_BRIDGE`, `McpBridgeSubentryFlow`): a
  `ConfigSubentry` of the MCP Server, conceptually equivalent to a Satellite —
  it reuses the exact same `CONF_SPELLBOOKER_BOOK`/
  `CONF_SPELLBOOKER_*_ALLOWED_AREAS`/`CONF_SPELLBOOKER_*_UNRESTRICTED` keys a
  Satellite subentry already uses, never a parallel field set. One per
  external MCP client/device. Owns its own bearer credential, its own
  optional speaking-device area, and its own action/query `AreaPolicy` scope.

### Setting up the MCP Server

**Settings → Devices & Services → Add Integration → HASE → "Set up MCP
Server"** — offered only while none exists yet (`async_mcp_server_exists`
hides it afterwards). One field: **Listener-Port** (`CONF_MCP_SERVER_PORT`,
default `8815`). Submitting creates the singleton "HASE MCP Server" device.

![HASE integration overview: the MCP Server entry with its two Bridges and the "+ MCP-Bridge hinzufügen" button](screenshots/mcp-01-integration-overview.png)

### Adding an MCP Bridge

From the HASE integration's own page, **"+ MCP-Bridge hinzufügen"** — if
more than one HASE entry/subentry exists, a picker appears first; choose
**"HASE MCP Server"**. The form (`McpBridgeSubentryFlow`, `step: user`),
field by field, in the order it is actually rendered
(`strings.json`/`translations/*.json`'s own `data` key order governs this —
**not** the Python schema's own key order, a real, previously-live distinction
documented in `providers/mcp/config_flow.py`'s own comment above the
`mcp_bridge_area_id` field):

![MCP Bridge create/reconfigure form, all fields in their real rendered order](screenshots/mcp-02-bridge-form-fields.png)

| Field (German label) | Key | Meaning |
|---|---|---|
| Name | `name` | Free text, this Bridge's display name. |
| Spellbook | `spellbooker_book` | Which Spellbook this Bridge exposes as MCP tools. Changing it later changes the exposed tools immediately (re-resolved on every `tools/list`, never cached). |
| Erlaubte Bereiche (Aktionen) | `spellbooker_allowed_areas` | Which areas this Bridge may *act* in. Ignored if the next field is on. |
| Aktionen uneingeschränkt (alle Bereiche erlauben) | `spellbooker_unrestricted` | Allows actions in every area. At least this OR a non-empty allowed-areas list is required. |
| Erlaubte Bereiche (Abfragen) | `spellbooker_queries_allowed_areas` | Which areas this Bridge may *answer status queries* about — an independent policy from actions. |
| Abfragen uneingeschränkt (alle Bereiche erlauben) | `spellbooker_queries_unrestricted` | Same idea, for queries. |
| Bereich (optional) | `mcp_bridge_area_id` | This Bridge's own speaking-device area — the implicit target for a Spell that names none. Leave empty for a "master" Bridge (CASE B below). |
| Sprachüberschreibung | `mcp_bridge_language` | Optional. Which trigger/alias phrasing language MCP tool descriptions use. Offers the assigned Spellbook's own languages plus "Home-Assistant-Sprache verwenden" (leave empty for the same effect). |

Submitting shows the bearer token **exactly once** (`async_step_reveal_
credential`) — copy it now; afterwards only a masked suffix is shown in
the Bridge's own settings. Reconfiguring an existing Bridge (the gear icon)
shows the same form pre-filled, plus a **"Neues Token erzeugen"** checkbox
that immediately invalidates the old token when checked.

A real, now-fixed bug from a live testing session on `testzwerg`
(2026-09-16): two `AreaSelector`
fields placed immediately adjacent in this form's own field order caused a
selection meant for one field to be silently written into the *other* one
instead (a genuine Home Assistant frontend defect, not merely a display
glitch — confirmed live, reproduced twice under different browser states).
Fixed by re-ordering the fields (see the table above, and `providers/mcp/
config_flow.py`'s own comment on why the *translation files'* key order,
not the Python schema's, controls the real rendered order) — pinned by
`tests/test_mcp_config_flow.py::BridgeFormAreaPickerAdjacencyTests` and
`::TranslationFieldOrderMatchesSchemaTests`, the latter reading `strings.
json`/`translations/de.json`/`translations/en.json` directly so it fails
if the two ever drift apart again.

### Spellbook Studio's "MCP prüfen" (capability check)

From an open Spellbook in **Spellbook Studio**, the **"MCP prüfen"**
button (`core/mcp_capability.py::check_book_mcp_capability`, wired through
`studio.py`'s `"check_mcp"` dispatch operation) always **computes fresh**
— there is no manual `mcp_enabled` flag, and the numbers shown are never
a stale author-set value — two SEPARATE facts per Spell:

- **Structural capability** (`projectable`): can this Spell become an MCP
  tool at all, independent of which Bridge eventually uses it? Re-derives
  the exact same `core.mcp_projection.project_command` a real `tools/list`
  call uses, against a reference (area-less, fully unrestricted)
  `BridgeContext` — the skip *reasons* (`unknown_operation:<name>`,
  `conflicting_capture_definition:<names>`, `unresolvable_text`,
  `duplicate_spell_id`) never depend on a specific Bridge's own policy.
- **Actual grant** (`requires_unrestricted_bridge`/`grant_reason`): does
  it additionally need a Bridge's own policy to satisfy something beyond
  mere projectability? Today this is a real, precomputable yes/no only for
  `execution_kind: "assist_redirect"` — it needs a **fully unrestricted**
  Bridge (both actions AND queries), for the exact reason section 6
  explains, surfaced with the identical `reason_code`
  (`assist_redirect_requires_unrestricted_policy`) the real runtime
  `DENIED` response uses. Every other execution kind's actual per-call
  outcome depends on the concrete target/arguments a real call would
  supply, which a book-level check has none of — correctly left
  unresolved rather than guessing.

The per-book summary (projectable / not projectable / requires-
unrestricted counts) and each Spell's own badge (`MCP: <kind>`, `· nur
lesend` when read-only, and the grant line) render in the existing spell
list — see `custom_components/hase/frontend/studio.js`'s `mcpBadge()`.

![Spellbook Studio's "MCP prüfen" summary and per-Spell MCP badges](screenshots/mcp-04-studio-mcp-check.png)

**The result is also saved — automatically, in the Spellbook itself.**
Every Studio save (`studio.py::SpellbookStudio.write`) recomputes this
same result and writes it onto each spell's own `metadata.mcp`
(`core/studio.py::attach_mcp_capability_cache`), keyed by a content hash
and a schema version (`_mcp_spell_hash`/`MCP_CAPABILITY_SCHEMA_VERSION`)
— so a spell edit, an import, a language change, or an explicit "MCP
prüfen" click all keep it current, with no separate step to remember.
Opening a book (`"read"`) already returns this saved status
(`mcp_report`), so badges show a real, current result *without* clicking
"MCP prüfen" first. A book saved before this feature existed, imported
fresh, or edited outside Studio (a direct YAML change) has no matching
cache yet — its Spells still show their real, freshly-computed numbers
immediately, just marked **"(noch nicht gespeichert)"** ("not yet
saved") until the next save or an explicit "MCP prüfen" reconciles it.
`export_book` strips this cache from exported YAML — it is Studio's own
derived, instance-reproducible bookkeeping, not authored Spell content,
and is rebuilt fresh the moment an imported copy is next saved. None of
this is ever read by the real execution path: `core/mcp_execution.py`
recomputes every permission/capability decision from scratch on every
real `tools/call`, exactly as before — a stale or even entirely missing
cache can never change what a real MCP call is allowed to do.

## 4. Protocol reference

Streamable HTTP (`mcp.server.lowlevel.Server`, `stateless_http=True` —
`providers/mcp/transport.py`), protocol version `2025-06-18`. Every method
below is real and verified against `tests/_mcp_protocol_check.py` (a real
network round-trip via the official `mcp` Python SDK) and, where noted, a
live call against `testzwerg`.

**Session behavior**: `stateless_http=True` means no server-side session is
persisted across requests — every `tools/list`/`tools/call` independently
re-authenticates its bearer token against the *current* `CredentialIndex`
and re-resolves the Bridge's configuration fresh (Spellbook, area, policy —
see section 6). Nothing about a Bridge is ever cached between calls, so a
Spellbook swap, a credential regeneration, or a policy edit takes effect on
the very next request, with no reload/reconnect needed on the server side.
`tools/list_changed` push notifications are deliberately **not**
implemented — the SDK only advertises `tools.listChanged=True` when a
`subscriptions/listen` handler is registered, which needs exactly the
per-connection session state `stateless_http=True` exists to avoid (see
`transport.py`'s own module docstring). A client still sees any change on
its very next `tools/list` call regardless.

**`initialize`** — real, captured response from a live call:

```json
{"jsonrpc":"2.0","id":1,"result":{"capabilities":{"experimental":{},"tools":{"listChanged":false}},"protocolVersion":"2025-06-18","serverInfo":{"name":"hase-mcp","version":"1.0.0"}}}
```

`notifications/initialized` follows per the MCP spec; HASE's own handler
does nothing observable with it (stateless — there is no per-connection
state to mark ready). `ping` is answered by the SDK's own default handler,
never HASE-specific.

**`tools/list`** — one entry per non-skipped Spell (section 5). A real
captured entry (`middle_earth_22`, an `assist_redirect` Spell) shows the
execution-kind-aware `readOnlyHint` from section 5 in practice:

```json
{"name":"middle_earth_22","description":"Sagt Hause Rohan, das Licht in {area} auf Sonnenaufgang zu stellen.","inputSchema":{"type":"object","properties":{"area":{"type":"string"}},"required":["area"]},"annotations":{"readOnlyHint":false}}
```

**`tools/call`** — `arguments` maps 1:1 onto the tool's own `inputSchema`
properties, converted back into HASE's `captures` dict
(`build_captures`, `core/mcp_execution.py`). Two DISTINCT layers of error
reporting exist, and a client must handle both:

- **HTTP/transport-level**: e.g. a real captured `406 Not Acceptable` when
  a client's `Accept` header omits either required media type (a genuine
  MCP/Streamable-HTTP requirement, not HASE-specific):
  ```json
  {"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Not Acceptable: Client must accept both application/json and text/event-stream"}}
  ```
  Bearer-token failures behave the same way, at the transport layer, before
  any Spell/Bridge logic runs.
- **Tool-level** (`CallToolResult.isError=True`, HTTP 200 — the call
  reached HASE, but the *Spell* could not run): HASE's own `_call_result`
  (`providers/mcp/transport.py`) renders these as plain `TextContent` with
  a fixed prefix convention, always `is_error=True`:
  - `"DENIED: <reason>"` — a real policy/permission refusal (e.g.
    `assist_redirect_requires_unrestricted_policy`, section 6)
  - `"FAILED: <reason>"` — the Spell ran but did not complete successfully
    (e.g. `assist_error` — Assist itself reported an error, section 5)
  - `"error: unknown_tool:<id>"` — no Spell with that id projects for this
    Bridge (renamed/removed/never existed/filtered out by policy)
  - `"error: unprojectable_tool:<reason>"` — the id exists but
    `project_command` itself would skip it (section 5's `ProjectionSkip`
    reasons)

  A **success** (`SUCCESS`/`QUERY_ANSWER`) returns `result.response_text`
  as plain `TextContent`, `isError` absent/false — never a JSON envelope
  distinct from what a human would be told; there is no separate "status"
  field to parse out of the text.

No `resources` or `prompts` are implemented — `tools/list`/`tools/call` are
the whole surface (see `initialize`'s own `capabilities` above: no
`resources`/`prompts` keys are advertised).

**Headers** every request needs: `Content-Type: application/json`,
`Accept: application/json, text/event-stream` (both, always — see the
406 example above), `Authorization: Bearer <token>`.

**Transport implementation notes**: served by `uvicorn` as a plain
`asyncio` task on Home Assistant's own event loop (`providers/mcp/
transport.py` + `providers/mcp/__init__.py`), never the aiohttp/ASGI shim
spiked and rejected during design (real bugs, approximate disconnect
detection, undocumented SDK reliance). The listener binds `0.0.0.0` — the
whole LAN can reach it, not just `localhost` — with the per-Bridge bearer
token as the *only* access control (see section 2's own reverse-proxy
note). `uvicorn`/`mcp`'s own first-time imports (real disk I/O + module
compilation) run via `hass.async_add_executor_job` (`_build_uvicorn_
server`), never directly on the event loop — real UAT against a live Home
Assistant instance caught both HA's own blocking-call detector firing on
this cold import, and a narrower follow-up: uvicorn's `Server._serve()`
also lazily imports its HTTP protocol implementation via `Config.load()`,
so `_build_uvicorn_server` calls `config.load()` itself, ahead of time, in
the same executor job — `_serve()` then finds `config.loaded` already
`True` and skips it.

## 5. Spell tools

`core/mcp_projection.py` turns one Bridge's `(Spellbook, BridgeContext)` into
MCP tool descriptors — read-only, never a second authorization model (every
decision it makes is re-checked for real by `core.executor` at call time).

For each area capture, the Bridge's own speaking-device area and its
`ScopedAreaPolicy` decide the shape of the corresponding `inputSchema`
property:

| Case | Bridge situation | Argument shape |
|---|---|---|
| A | Room-bound Bridge, its own area is the *only* area its policy could ever permit | Omitted entirely — injected via `McpToolProjection.implicit_captures` |
| B | Area-less ("MASTER") Bridge | Required |
| C | Bridge has its own area, but the policy reaches wider | Optional, `McpArgument.implicit_default` = the Bridge's own area |

Tool names are Bridge-local (`name == spell_id`, never prefixed); the same
Spellbook projects a *different* schema/description per Bridge. A `spell_id`
shared by more than one command in a book (an authoring mistake the loader
itself never rejects — see `spellbook_engine.duplicate_spell_ids`) is never
exposed as a tool at all, on either side (`project_spellbook` /
`find_command_by_id`) — reported as a `ProjectionSkip`, not silently resolved
to one arbitrary pick.

Known, accepted limitations: a `true_values`/`false_values` capture projects
as a string enum of the accepted words, not a native JSON boolean (see
`_value_argument`'s own docstring); a `{placeholder}` used as two
structurally incompatible roles in one command (e.g. once as an area target,
once as a typed numeric argument) cannot be projected as one JSON Schema
property and is skipped (`conflicting_capture_definition`).

### Text-only Spells and `assist_redirect`

A Spell with none of `operation`/`sources`/`steps` — only `output` — always
projects too (`_project_text_only`), never a `ProjectionSkip` on account of
its kind. **What calling it actually does** is a fifth, explicit,
author-declared execution kind (`EngineCommand.assist_redirect: bool`, see
`core/models.py::DEFAULT_ASSIST_REDIRECT`'s own docstring) — entirely
separate from `output_mode` (whether/how a response is delivered), exactly
like `operation`/`sources`/`steps` are:

- **`assist_redirect: false`** — `output` is a genuine fixed/templated
  answer HASE renders itself (`build_text_response`). **Read-only**
  (`McpToolProjection.read_only = True`) — touches nothing in Home
  Assistant.
- **`assist_redirect: true`** (the current default for every currently-
  bundled Spell, pending its own content review — see `DEFAULT_ASSIST_
  REDIRECT`) — `output` is a command phrase handed to Home Assistant's own
  configured conversation agent (`conversation.async_converse`) for REAL
  interpretation/execution — the exact same mechanism `providers/code27/
  bridge.py::_forward_to_assist` already uses for CODE27, now also reachable
  through MCP (`core/mcp_execution.py::_execute_assist_redirect`). **Never
  read-only** — it may perform a real device action, Home Assistant's own
  agent decides, not HASE.

MCP is not the only frontend that reaches this execution kind — the
native **HASE-Agent** (the HA `ConversationEntity` a Home Assistant Voice
Assist pipeline is configured to use as its conversation agent, whether a
standalone SPELLBOOK_ONLY agent or a Satellite subentry) reaches every
text-only Spell too, through the exact same shared, neutral core — never
MCP itself, and never a fourth implementation:

- **`core/text_only_execution.py::execute_text_only_command`** is the one
  neutral, shared function both adapters depend on directly. It has zero
  Home Assistant imports and zero MCP-specific concepts — just
  `command`/`captures`/`language`/`policy`/an injected `AssistForwarder`.
  `core/mcp_execution.py` (MCP's own `tools/call` adapter) calls it, and
  `core/router.py::SpellbookRouter._execute_text_only_and_respond` (the
  HASE-Agent's own dispatch, reached from `conversation.py`) calls it
  too — **neither adapter depends on the other's own module.** A HASE-Agent
  turn never touches MCP, the MCP protocol, or the network in any way —
  it only reuses a plain, neutral Python function, same as it already
  reuses `core.spellbook_engine`/`core.executor` for every other
  execution kind.
- `core/router.py::SpellbookRouter` matches through
  `core.spellbook_engine.match()`, which ranks EVERY execution kind
  (action, query, Combined, text-only) in one pass via `find_command()`
  (`typed_only=False`, the same default `providers/code27/bridge.py`
  already uses — never a separate, narrower view). A resolved text-only
  match dispatches through `_execute_text_only_and_respond` into the
  shared function above, wired to Home Assistant's real conversation
  agent via `HaAssistForwarder` (`spellbooker_ha.py`) — the same class
  `providers/mcp/transport.py` injects for MCP; `conversation.py`
  constructs its own `SpellbookRouter` with
  `assist_forwarder=HaAssistForwarder(hass)` identically. Verified live,
  end-to-end, by tracing the real bundled `middle_earth_07` Spell ("die
  sonne geht auf {area}") through this exact `router.handle()` path for
  SPELLBOOK_ONLY and SATELLITE modes alike, confirming the Assist
  forwarder is actually called and the Spell's own catchphrase is spoken
  — see `tests/test_core_engine.py::
  VoiceReachesEveryExecutionKindTests` for the permanent regression
  coverage (action, query, `assist_redirect` true/false, and a mixed
  typed/text-only ambiguity resolving correctly either way).
- `providers/code27/bridge.py::_handle_bridge_message` (CODE27's own
  chat/voice-command Bridge on the physical device) already used
  `find_command()` this same way and needed no change — a separate,
  historical implementation of the same behavioral contract, predating
  (and the original reference for) the shared function above.
- When `SpellbookRouter`'s matcher finds nothing at all and the agent is
  a SATELLITE, it still falls back to `_consult_backend`
  (`core/router.py`) — for a CODE27-backed Satellite this reaches
  `providers/code27/backend.py::Code27ConversationBackend`, CODE27's own
  free-form chat persona (always `proposals=()`, never touches HASE's own
  declarative Spellbook `output` field). This fallback is now reached
  strictly *less* often than before: any text-only Spell that matches is
  handled directly, before this fallback is ever consulted.

There is no `target_spec`/`arguments_spec` for a text-only command (the
loader never validates either field unless an operation is present), so
every `{placeholder}` in the *primary* trigger becomes a required plain
string argument instead — no area/entity semantics apply, since there is no
target to attach them to. `project_command`/`project_spellbook` still reuse
the same duplicate-`spell_id` and language-fallback handling as every other
Spell; a text-only Spell whose `output` fails to resolve to real text in any
language at all becomes a `ProjectionSkip("unresolvable_text")`, never a
silently-dropped tool.

#### `assist_redirect` execution and its permission gate

`conversation.async_converse` has no per-call area/entity scoping parameter
of its own — there is no way to hand it "only within these areas". So a
Bridge whose own `ScopedAreaPolicy` is not FULLY unrestricted (`policy.
actions.unrestricted and policy.queries.unrestricted`, mirroring `providers/
code27/frontend.py::_UNRESTRICTED_POLICY`, the only other caller of this
mechanism) must never reach it at all: `_execute_assist_redirect` denies the
call outright (`ExecutionStatus.DENIED`, `reason_code=
"assist_redirect_requires_unrestricted_policy"`) *before* the forwarder is
ever invoked. This is what keeps a scoped Bridge's own permissions from
being bypassed by routing a command through Assist instead of a typed
operation. The check runs at call time (never at projection time — an
`assist_redirect` tool is still listed in `tools/list` for every Bridge,
exactly like a named-target action with a target outside its policy is;
projection is shape only, never a second authorization model — see this
document's Studio section for how the *predictable* half of this — policy
alone, no arguments needed — is surfaced up front).

Once forwarded, the real Assist reply is mapped using the exact same
three-way rule `_forward_to_assist` already applies (`AssistForwardOutcome`,
mirroring HA's own `IntentResponseType` values):

| Outcome | `ExecutionStatus` | Response text |
|---|---|---|
| `ERROR` | `FAILED` | never a fabricated success |
| `QUERY_ANSWER` (Assist merely *answered*, did nothing) | `QUERY_ANSWER` | Assist's own reply — the Spell's own `catchphrase` is **never** substituted here, since nothing was *done* |
| confirmed completion (not a follow-up) | `SUCCESS` | the Spell's own `catchphrase`, if configured — **not** Assist's generic reply, mirroring `_forward_to_assist`'s own substitution rule exactly |
| confirmed completion, no `catchphrase` configured, and Assist gave no speech | `SUCCESS` | the same generic fallback (`response_text_for_success`) any catchphrase-less action already gets — a silently-successful action is never downgraded to `FAILED` for having nothing to say |
| follow-up question (`continue_conversation=True`) | `SUCCESS` | Assist's own reply (the question) — never hidden behind the catchphrase |

`providers/mcp/` supplies the real `AssistForwarder` (`spellbooker_ha.py::
HaAssistForwarder`) — deliberately narrower than CODE27's own `_ask_assist`:
no AI cleanup, no activity logging, no entity-correction retry loop (all
CODE27-specific UX with no MCP equivalent). Compared against CODE27 on the
two things that cross the Home-Assistant-API boundary: **agent selection**
— CODE27 reads a per-Bridge `CONF_BRIDGE_AGENT_ID` option, falling back to
HA's built-in agent when unset; MCP Bridges have no equivalent option today,
so `agent_id` is always `None` (every MCP Bridge behaves like a CODE27
Bridge that left its own agent option unset — a documented simplification,
not a bug: a per-MCP-Bridge agent choice would be a separate feature).
**Language** — CODE27 passes the live request's own resolved language; MCP
has no live "turn", so it passes `BridgeContext.language` instead, the same
per-Bridge-configuration-or-`hass.config.language` fallback chain every
other per-Bridge decision already uses.

### Execution mapping

`core/mcp_execution.py::execute_mcp_tool_call` resolves a `tools/call`'s
`spell_id` via `find_command_by_id` (exact lookup — never
`find_command`/`match`, HASE's text/fuzzy matcher, and never
`core.router.SpellbookRouter` or its clarification/session machinery: a tool
call has already named the exact Spell, there is nothing left to
disambiguate). It re-derives the same projection `tools/list` published,
converts the MCP `arguments` back into a plain `captures` dict
(`build_captures`), then runs the existing
`build_draft`/`build_query_plan`/`build_step_plan` → `ActionExecutor` path
unchanged. The response text is always HASE's own rendered
catchphrase/`response_template`, via the same `response_text_for_success`
`core.router` uses — never a second wording system.

A text-only Spell never reaches `ActionExecutor` at all — there is no
target/area to resolve or HA service to call. `assist_redirect: false`:
`core.spellbook_engine.build_text_response` renders its `output` directly
from the already-built captures (mirroring `find_command()`'s own inline
text-only rendering, just driven by an exact spell_id instead of a text
match), wrapped in a synthetic `QUERY_ANSWER` `ExecutionResult` so it flows
through `response_text_for_success` unchanged — no second response-wording
rule. `assist_redirect: true`: `_execute_assist_redirect` instead — see
"Text-only Spells and `assist_redirect`" above.

## 6. Permissions and limits

**A tool's advertised metadata is never a security/authorization
mechanism, even though it is an accurate description.** Two different
claims, not to be conflated:

- **Not an authorization mechanism**: a tool's `description`, argument
  names/types, and `annotations.readOnlyHint` (section 5) are never
  checked, consulted, or read back by the SERVER itself when a
  `tools/call` actually runs — `execute_mcp_tool_call` re-derives the
  real projection and re-runs the real `core.executor`/
  `_execute_assist_redirect` permission checks (`AreaPolicy`, Assist
  exposure, expert-tier gating, the `assist_redirect` unrestricted-policy
  gate) from scratch, from server-held Bridge configuration, on every
  single call. `readOnlyHint=false` grants nothing, and a client that
  ignores it changes nothing server-side either way — there is no
  server-side trust placed in this metadata to begin with, so there is
  nothing for a client to bypass by disregarding it.
- **But an accurate, truthful description of THIS server's own
  behavior** — `readOnlyHint` (`McpToolProjection.read_only`,
  `core/mcp_projection.py`) is computed from the exact same fields
  (`operation`/`sources`/`steps`/`assist_redirect`) that decide real
  execution, by the same code, never asserted independently. A client
  reading its OWN server's freshly-fetched `tools/list` can rely on
  `readOnlyHint` as correct information about what calling that tool
  does — it just can never rely on it as a permission grant, and the
  server never treats a client's belief about it as authoritative for
  anything. Section 7's E2E script (`tests/e2e_testzwerg_mcp_check.py`)
  leans on exactly this distinction: reading `read_only_hint` from the
  live server's own `tools/list` to decide which tools are safe to call
  is a client-side judgment call based on trustworthy information, never
  a security boundary the script is asserting on the server's behalf.

The two enforcement points that actually decide whether a call is allowed:

- **`ScopedAreaPolicy`** (`core.models.AreaPolicy` × 2 — one for actions,
  one for queries, independently configured per Bridge, section 3):
  `core.executor.ActionExecutor` resolves every target against it for
  real, for every action/query/Combined Spell, exactly as CODE27/Satellite
  already do — MCP adds no separate scoping logic.
- **The `assist_redirect` unrestricted-policy gate** (section 5): the one
  MCP-specific permission rule, because `conversation.async_converse` has
  no per-call area scoping of its own to check against. A Bridge whose
  `ScopedAreaPolicy` is not fully unrestricted (both actions AND queries)
  is denied `assist_redirect` calls outright, before Assist is ever asked
  — `DENIED: assist_redirect_requires_unrestricted_policy`. This is not
  automatically disqualifying for a Bridge that legitimately needs it
  (the live-tested `testzwerg` Bridges used exactly this), but it does mean
  an unrestricted policy is a real, broad grant — restrict a Bridge's
  policy whenever its Spellbook has no `assist_redirect` Spells that need
  it.

### 6.1 Area identity and Bridge scope

`core/mcp_bridge_context.py::BridgeArea(area_id, area_name)` keeps a Bridge's
area id and display name deliberately distinct — `area_id` for `AreaPolicy`
membership and `source_area_id`-kind targets, `area_name` for an
`area_name`-kind capture's implicit default. A Bridge's configured
`area_id` is re-resolved against the *current* area registry on every single
request (`providers/mcp/bridge_context.py::build_bridge_context_for_subentry`,
called from `transport.py::_resolve_current_bridge`) — never cached. If the
configured area no longer exists, `BridgeConfigurationError` is returned (a
frozen dataclass, never raised) and the Bridge exposes no tools / refuses
`tools/call` with `bridge_misconfigured:<reason>`, rather than silently
downgrading to an area-less MASTER Bridge. The same resolution also
raises/clears a Home Assistant Repair issue
(`providers/mcp/repairs.py`, `mcp_bridge_invalid_area`) so a misconfigured
Bridge is visible in Settings → Repairs even if no MCP client ever calls in —
`async_check_bridge_health` runs it once on every MCP Server setup/reload.

### 6.2 Credentials

`providers/mcp/credentials.py` (stdlib-only, no HA import): a Bridge
credential is `secrets.token_urlsafe(32)`, shown to the user exactly once
(`McpBridgeSubentryFlow.async_step_reveal_credential`); only
`HMAC-SHA256(per-installation secret, token)` (hex) is ever persisted. The
per-installation secret is generated once and stored via HA's own `Store`
helper (`providers/mcp/secret_store.py`,
`.storage/hase.mcp_secret`). `CredentialIndex` is an in-memory
`verifier → BridgeIdentity` dict, rebuilt from scratch on every MCP Server
setup/reload (`async_rebuild_credential_index`) — never incrementally
patched, so a regenerated or deleted Bridge's credential stops resolving
immediately, with no separate invalidation step to get right. The client
never supplies a `bridge_id` anywhere in the protocol; identity is
determined entirely by which verifier the presented token's HMAC matches.

## 7. Diagnostics and tests

### Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| `401`/connection refused before any JSON-RPC | Wrong/missing `Authorization: Bearer` header, or wrong host/port | Section 2 — confirm the *MCP Server's own* configured port, not Home Assistant's web-UI port; regenerate the token if unsure (section 3) |
| `406 Not Acceptable` | `Accept` header missing `application/json` or `text/event-stream` | Send both, always — this is a genuine MCP/Streamable-HTTP requirement (section 4), not a HASE bug |
| Expected tool missing from `tools/list` | The Spell is skipped (`ProjectionSkip`) for this Bridge, or its `spell_id` collides with another command in the book | Run Spellbook Studio's "MCP prüfen" (section 3) for the exact reason; a duplicate `spell_id` is never exposed to *any* Bridge, not just this one |
| `DENIED: <reason>` from `tools/call` | A real, server-side policy refusal — not a bug | Match `<reason>` against section 6 (`AreaPolicy` scope, `assist_redirect_requires_unrestricted_policy`, expert-tier gating) |
| `FAILED: <reason>` | The Spell ran but did not complete (e.g. `assist_error` — Assist itself reported an error) | Check Home Assistant's own conversation-agent logs for the underlying cause; HASE only relays Assist's own outcome (section 5) |
| SSE connection aborts mid-`initialize` (`HTTP 200`, `text/event-stream`, then no message, connection closed) | **Not root-caused.** Seen once, live, against `testzwerg` (2026-09-16), reproduced independently via `curl` and via a Chrome `fetch()` call, ruling out a client-specific bug | A full Home Assistant restart made a fresh `initialize` call succeed immediately afterward — but this is **not confirmed as the actual fix**; the underlying trigger (hypothesized: transient server-side state after repeated credential-index rebuilds) was never proven. If this recurs, capture Home Assistant's own log around the failing request before restarting, so the real cause has a chance of being found |

### Live findings vs. the automated test suite

Everything in the "Automated tests" list below runs against mocked Home
Assistant calls, per this repository's own rule that no automated test
ever performs a real device action. Two calls in this document's own
history are different: they were made against the real, live `testzwerg`
instance, by the user, with a real physical outcome, and are recorded here
precisely because they are **not** reproducible by re-running the test
suite:

- `light_all_middle_earth` — server reported success; the user
  independently confirmed the real light actually turned on.
- `clean_the_halls` with `area=Gondor` (the Middle-earth alias for the
  real Zwergstadt area) — server reported `"Die Hallen werden gereinigt."`
  (`SUCCESS`); the *physical* cleaning effect was not independently
  confirmed by the user afterward, only the server-reported outcome.

Treat these two as proof that a real `tools/call` reaches a real device
through this exact code path — not as a substitute for the automated
suite's own broader coverage, and not as proof that every Spell/execution
kind behaves correctly (only these two were exercised live). A safe,
repeatable way to re-check basic live connectivity (never a device action)
is `tests/e2e_testzwerg_mcp_check.py` — see below.

### Automated tests

- `tests/test_mcp_projection.py`, `tests/test_mcp_execution.py`,
  `tests/test_mcp_bridge_context.py` — pure `core/` unit tests (no HA
  install needed, same `importlib`-based loading as the rest of `core/`'s
  test suite).
- `tests/test_mcp_credentials.py`, `tests/test_mcp_config_flow.py` —
  `providers/mcp/` unit tests via `tests/test_bridge_delivery.py`'s
  `load_source()` (AST-strips HA imports, injects fakes).
- `tests/_mcp_protocol_check.py` (wrapped by `tests/test_mcp_protocol.py`) —
  a real network protocol check: real `mcp`/`uvicorn` packages, a real
  Streamable HTTP server on a real (ephemeral) TCP port, driven by the
  official MCP Python SDK client (`ClientSession`,
  `streamable_http_client`), against a hand-built but functionally real HA
  surface (working area/entity/device/issue registries). Covers CASE A/B/C,
  Action/Query/Combined Spells, AreaPolicy denial, Assist-exposure
  enforcement, credential regeneration/Bridge deletion taking effect
  immediately, a deleted-area Bridge failing safe (never silently MASTER)
  and raising/clearing its repair issue, and resource/lifecycle checks
  (task actually running/actually finished, port immediately rebindable, no
  live server reference left in `hass.data` after unload). Also loads the
  REAL bundled `spellbooks/middle_earth.yaml` (42 commands, 10 typed + 32
  text-only, all still carrying the pending-review `assist_redirect`
  default) under an unrestricted test Bridge and asserts all 42 appear in a
  real `tools/list`, zero `ProjectionSkip`s; a representative `assist_
  redirect` Spell (`middle_earth_07`) actually forwards its rendered command
  text to a fake Assist agent and returns its own configured `catchphrase`
  (never the raw command phrase, never Assist's generic reply); a restricted
  Bridge's `assist_redirect` call is denied before Assist is ever asked; an
  `ERROR` reply from Assist is reported `FAILED`, never a fabricated
  success; a representative `assist_redirect=False` text Spell and a
  representative action Spell both still execute for real (mocked HA call).
- `tests/_hase_import_smoke.py` — real import + `async_setup_mcp_server_entry`/
  `async_unload_mcp_server_entry` end-to-end, including the same
  lifecycle/resource assertions, run as part of the whole-package smoke test.

Run the whole suite with `python -B -m pytest -q -p no:cacheprovider`
(see `docs/HASE-ARCHITECTURE.md`'s own Verification section).

### Optional: E2E check against a real host

`tests/e2e_testzwerg_mcp_check.py` — opt-in, never picked up by `pytest`/
`unittest discover` automatically (no `test_` prefix, on purpose). Proves
a real deployed build, on a real Home Assistant instance, with its real
installed `mcp`/`uvicorn`/`starlette` package versions, actually completes
an `initialize` + `tools/list` round-trip over the network — exactly the
class of failure the SSE connection-abort finding above was. It only ever
calls `initialize` and `tools/list` by default; a `tools/call` is only
attempted for a tool this script's own `tools/list` response marked
`read_only_hint: true`, with no override flag to bypass that check. This
is a safety choice grounded in section 6's own distinction: `read_only_
hint` is not an authorization mechanism the SERVER enforces, but it IS a
truthful, freshly-fetched description of what the SAME server's own code
just said this exact call will do (computed from `operation`/`sources`/
`steps`/`assist_redirect`, never asserted independently) — reading it
from the live server this script is about to call, and refusing to call
anything it doesn't mark read-only, is a sound client-side safety rule,
not a claim that this metadata is itself a security boundary. Usage is in
the script's own module docstring (`HASE_E2E_URL`, `HASE_E2E_TOKEN`,
optional `HASE_E2E_READONLY_TOOL` environment variables).

## 8. Developer guide

Building a *different* frontend adapter (a new Bridge/channel onto the
same shared `core.spellbook_engine`/`core.executor` machinery MCP itself
uses) is documented separately in
[docs/HASE-NEW-BRIDGES.md](HASE-NEW-BRIDGES.md) — the reference adapter,
the mandatory shared contract tests, and the CODE27 `query_plan` bug this
whole pattern exists to prevent from recurring.
