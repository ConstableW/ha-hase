# Adding a new Bridge/frontend adapter to HASE

HASE has three real frontend adapters today — CODE27
(`providers/code27/`), Assist/conversation, and MCP (`providers/mcp/`,
see [docs/HASE-MCP.md](HASE-MCP.md)) — plus Spellbook Studio for
authoring. All three execution-facing adapters are thin: they own how a
call *arrives* and how a reply is *delivered*, never what a Spell's fields
mean or how it is actually run against Home Assistant. This document is
for anyone adding a fourth (or fifth) one — a different chat platform, a
different voice satellite, a different automation trigger — and explains
the one contract every adapter must satisfy, using a real, minimal,
working example.

Every claim below is checked against real, currently-passing code —
nothing here is aspirational.

## 1. Overview: what an adapter owns vs. what it never owns

An adapter is a thin translation layer between one specific transport and
the shared engine. It owns exactly three things:

1. **How a call arrives.** A plain string (MCP's `find_command_by_id`
   looks up an already-disambiguated `spell_id`; CODE27/a chat bot's
   `find_command` does natural-language fuzzy matching instead), a
   structured payload, or anything else the transport hands it.
2. **Its own permission scope.** A `ScopedAreaPolicy` — CODE27's owner-only
   frontend uses an intentionally unrestricted one
   (`providers/code27/frontend.py::_UNRESTRICTED_POLICY`); a real
   multi-tenant chat Bridge would very likely scope this to the areas it
   is actually allowed to touch, the same way an MCP Bridge's own
   `spellbooker_allowed_areas`/`spellbooker_unrestricted` fields do
   (`docs/HASE-MCP.md`, section 3).
3. **Its own reply shape.** CODE27 speaks a reply via TTS; MCP wraps it in
   an MCP `TextContent`; the reference adapter below returns a plain
   Python string.

An adapter **never** re-derives or reinterprets any of the following —
these are the shared engine's job, identical for every frontend:

- What a Spell's `operation`/`sources`/`steps`/`output`/`assist_redirect`
  fields actually *mean* or *do* — only `core/spellbook_engine.py`'s
  builders (`build_draft`/`build_query_plan`/`build_step_plan`/
  `build_text_response`) ever turn a matched command into something
  executable.
- Whether a target is permitted, or even exposed — only
  `core/executor.py::ActionExecutor`, given the adapter's own
  `ScopedAreaPolicy`, ever decides that.
- How a successful response is worded — only
  `core/executor.py::response_text_for_success` ever renders that text.

If you find yourself writing a second implementation of any of the three
bullets above inside a new adapter, stop — that is exactly the shape of
bug this document exists to prevent (section 3).

## 2. Shared entry points

| You have... | Call | Returns |
|---|---|---|
| Free natural-language text, ambiguity is possible | `core.spellbook_engine.find_command(book, text, language)` | `CommandMatch` (already built — `.action_draft`/`.query_plan`/`.step_plan`/`.output`/`.catchphrase` are ready to execute) or `CommandAmbiguity`, or `None` |
| An already-disambiguated exact `spell_id` (e.g. a tool call, a button press) | `core.spellbook_engine.find_command_by_id(book, spell_id)` | The raw `EngineCommand` only — you must call the builders yourself with your own `captures` dict |
| A built `ActionDraft` | `core.executor.ActionExecutor.execute(draft, policy, context=...)` | `ExecutionResult` |
| A built `QueryPlan` | `core.executor.ActionExecutor.execute_query_plan(plan, policy, context=...)` | `ExecutionResult` |
| A built `StepPlan` (Combined Spell) | `core.executor.ActionExecutor.execute_steps(plan, policy, context=...)` | `ExecutionResult` |
| An `ExecutionResult` you want worded | `core.executor.response_text_for_success(execution)` | `str` |
| A text-only Spell (either `assist_redirect` value) | `core.text_only_execution.execute_text_only_command(command, captures, language=..., policy=..., assist_forwarder=..., context=...)` — the neutral, shared function both MCP (`core/mcp_execution.py`) and the native HASE-Agent (`core/router.py::SpellbookRouter`) call directly; your own `AssistForwarder` (see `core/text_only_execution.py::AssistForwarder` Protocol) is only consulted for `assist_redirect=True` | `CommandExecutionResult` |

The choice between `find_command` and `find_command_by_id` is the main
thing that varies per adapter — everything after it is identical.

## 3. The bug this pattern exists to prevent

`providers/code27/bridge.py`'s own `_handle_bridge_message` once had a
genuine, silently-swallowed missing dispatch branch: `match.query_plan`
(a flat multi-source query — a Spell with `sources` but no `operation`)
was never checked at all. A query-only Spell fell through into the
Assist-forwarding branch with `command=None`, which raised inside
`conversation.async_converse` and was silently caught, so the bug shipped
completely undetected. No existing test caught it because every existing
test covered one execution kind in isolation — nothing checked "every
execution kind, through this one adapter's own real dispatch function" as
a single property.

This is a **missing dispatch branch**, not a wrong answer — a
"does the response look right" test would not have caught it either,
since there was no response at all to compare against a *wrong* one; the
whole request silently went to the wrong place. That is exactly what
`tests/_bridge_contract.py` exists to catch, for every adapter, present
and future, automatically — see section 5.

## 4. The five execution kinds every adapter must dispatch

Every Spell falls into exactly one of these, decided by which fields it
has (`core.mcp_capability.execution_kind` computes this the same way for
Studio's capability check — see `docs/HASE-MCP.md` section 3 — **never**
by keyword/placeholder/catchphrase inspection):

1. **Action** (`operation` present, no `sources`) — `execute()`.
2. **Query** (`sources` present, no `operation`, no `steps`) —
   `execute_query_plan()`.
3. **Combined** (`steps` present) — `execute_steps()`.
4. **Text answer** (`operation`/`sources`/`steps` all absent,
   `assist_redirect: false`) — render `output` directly
   (`build_text_response`, or reuse `find_command`'s own already-rendered
   `.output` if you matched via fuzzy text).
5. **Assist-redirect** (`operation`/`sources`/`steps` all absent,
   `assist_redirect: true`, the default — see
   `core/models.py::DEFAULT_ASSIST_REDIRECT`'s own docstring) — `output`
   is a **command phrase**, not an answer. Never return it as if it were
   one. Either forward it to a real conversation agent by calling
   `core.text_only_execution.execute_text_only_command` with your own
   injected `AssistForwarder` (see `core/text_only_execution.py::
   _execute_assist_redirect` and `docs/HASE-MCP.md` section 5 for the
   full permission-gate/catchphrase-substitution rule it applies — the
   SAME shared function both MCP and the native HASE-Agent call directly,
   never a copy), or, if your adapter genuinely has no such agent to
   forward to (like the minimal reference adapter below), report it
   honestly as unsupported — never silently drop it and never fabricate
   success.

## 5. The reference adapter

[docs/examples/reference_bridge_adapter.py](examples/reference_bridge_adapter.py)
is a real, minimal, working `ReferenceChatAdapter` — a hypothetical "one
text message in, one text reply out" chat Bridge. It is deliberately
small enough to read start to finish, and every call it makes is a real
`core/` API, never pseudocode. Its own module docstring spells out, field
by field, what it owns vs. never owns (section 1 above, restated in code
terms). The whole dispatch is four branches in
`ReferenceChatAdapter.handle_message`:

```python
result = find_command(self._book, text, self._language)
if result is None:
    return ChatReply(handled=False, text=None)
if isinstance(result, CommandAmbiguity):
    return ChatReply(handled=True, text=None, status="ambiguous")

command = result.command
if result.query_plan is not None:
    execution = await self._executor.execute_query_plan(result.query_plan, self._policy, context=context)
    return ChatReply(handled=True, text=response_text_for_success(execution), status=execution.status.value)
if result.step_plan is not None:
    execution = await self._executor.execute_steps(result.step_plan, self._policy, context=context)
    return ChatReply(handled=True, text=response_text_for_success(execution), status=execution.status.value)
if result.action_draft is not None:
    execution = await self._executor.execute(result.action_draft, self._policy, context=context)
    text_out = result.catchphrase if execution.status is ExecutionStatus.SUCCESS and result.catchphrase else response_text_for_success(execution)
    return ChatReply(handled=True, text=text_out, status=execution.status.value)

if command.assist_redirect:
    # No live conversation agent to forward to in this minimal adapter -
    # an explicit "unsupported" is the only honest outcome. NEVER return
    # result.output here - that's a command phrase, not an answer.
    return ChatReply(handled=True, text=None, status="assist_redirect_unsupported")
return ChatReply(handled=True, text=result.output, status=ExecutionStatus.QUERY_ANSWER.value)
```

Note it matches via `find_command` (natural-language), so `result` already
has `.action_draft`/`.query_plan`/`.step_plan`/`.output` built — it never
calls `build_draft`/`build_query_plan`/`build_step_plan`/
`build_text_response` itself. An adapter using exact lookup instead (like
MCP's `find_command_by_id`) gets back only the raw `EngineCommand` and
must call those builders itself with its own captures — see
`core/mcp_execution.py::execute_mcp_tool_call` for that shape.

## 6. Step-by-step checklist for a new adapter

1. Decide your matching strategy: `find_command` (fuzzy, ambiguity
   possible) or `find_command_by_id` (exact, pre-disambiguated).
2. Construct your adapter's own `ScopedAreaPolicy` from however your
   transport configures permissions (a config entry, a per-channel
   setting, a hardcoded unrestricted policy for an owner-only tool —
   whatever fits your transport).
3. Write the four/five-branch dispatch from section 4, reusing the shared
   builders/executor calls from section 2 — never a parallel
   implementation of what a field means.
4. Decide how (or whether) your adapter forwards `assist_redirect`
   Spells. The simplest, correct choice is usually not to write this
   gate yourself at all: call `core.text_only_execution.
   execute_text_only_command` (section 2's table) with your own
   `ScopedAreaPolicy` and an injected `AssistForwarder` — it already
   applies the fully-unrestricted-policy gate before ever calling the
   forwarder, the one permission rule with no equivalent elsewhere,
   because `conversation.async_converse` has no per-call area scoping of
   its own to rely on instead. Both MCP and the native HASE-Agent reuse
   this exact function rather than re-implementing the gate.
5. Write your adapter's own test module that imports `tests/
   _bridge_contract.py` and calls
   `assert_every_execution_kind_is_dispatched` against your real dispatch
   function (see section 5's own test file for the pattern) — **this is
   mandatory**, not optional polish; it is the check that would have
   caught the real CODE27 `query_plan` bug on day one instead of after a
   live user report (section 3).
6. Mock every Home Assistant call in your tests — no test in this
   repository ever performs a real device action.

## 7. Using the shared contract tests

[tests/_bridge_contract.py](../tests/_bridge_contract.py) provides:

- `CONTRACT_SPELLS` / `build_contract_book()` — one fixture Spell per
  execution kind (action, query, combined, text-answer, assist-redirect),
  loaded through the same `core.spellbook_engine.load_spellbook_from_dict`
  every other adapter uses — no HA import required to load it.
- `assert_every_execution_kind_is_dispatched(dispatch, *,
  extract_status)` — calls your `dispatch(spell_id, captures)` once per
  fixture Spell and asserts every one of them reaches **some** real,
  non-`None`, non-crashing outcome. `extract_status` is a small callback
  you supply that turns your adapter's own return shape into a short
  status string — the helper never assumes a shared response envelope,
  only that a real outcome was reached for every kind.

[tests/test_reference_bridge_adapter.py](../tests/test_reference_bridge_adapter.py)
is the fully worked example: it wires up the reference adapter with a
`FakeResolver`/`FakeCaller` (mocked HA calls, one fixture area with one
light entity), runs it through `assert_every_execution_kind_is_dispatched`,
and — this is the part worth reading closely —
`ContractHelperCatchesAMissingDispatchBranchTests` deliberately builds a
**broken** dispatch function that mirrors the real historical CODE27 bug
(every kind handled except `query_plan`, which returns `None`) and proves
the contract helper actually fails, naming `contract_query` in its
assertion error, rather than passing quietly:

```python
async def broken_dispatch(spell_id, captures):
    if spell_id == "contract_query":
        return None  # the missing branch - nothing ever handled this
    return "handled"

with self.assertRaises(AssertionError) as caught:
    await contract.assert_every_execution_kind_is_dispatched(broken_dispatch, extract_status=lambda o: o or "")
self.assertIn("contract_query", str(caught.exception))
```

Run your new adapter's tests (and the whole suite) with
`python -B -m pytest -q -p no:cacheprovider` — see
`docs/HASE-ARCHITECTURE.md`'s own Verification section.

## 8. See also

- [docs/HASE-MCP.md](HASE-MCP.md) — the MCP adapter as a complete worked
  example of everything in this document applied for real, including its
  own `assist_redirect` permission gate (section 5) and its own contract
  test usage (section 7's "Automated tests").
- [docs/HASE-ARCHITECTURE.md](HASE-ARCHITECTURE.md) — the whole-repository
  module boundaries this document's `core/` entry points live inside.
