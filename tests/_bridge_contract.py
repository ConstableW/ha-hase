"""Shared Bridge/frontend-adapter contract fixtures and assertions - see
docs/HASE-NEW-BRIDGES.md for the full explanation. Any new frontend adapter
(a new "channel" onto the shared core.spellbook_engine/core.executor
machinery) gets its own test module that imports `CONTRACT_BOOK` and the
`assert_*` helpers below, builds whatever real dispatch object that
adapter uses, and calls each helper with a small callback that runs ONE
Spell through the adapter end to end.

Why this file exists: providers/code27/bridge.py's own `_handle_bridge_
message` once had a genuine, silently-swallowed missing dispatch branch
for `query_plan` (a flat multi-source query) - `match.query_plan` was
never checked at all, so a query-only Spell fell through into the
Assist-forwarding branch with `command=None`, which raised inside
`conversation.async_converse` and was silently caught. The bug shipped
undetected because no test exercised "every execution kind, through this
specific adapter's own real dispatch function" as one property - existing
tests each covered one kind in isolation. These helpers exist so that
property is checked automatically for every adapter, present and future,
instead of relying on someone remembering to write it by hand each time.

No Home Assistant import here - CONTRACT_BOOK is loaded through core.
spellbook_engine like every other core/ fixture in this suite, and the
callbacks below run against whatever executor/dispatch fixture the calling
adapter test module wires up (mocked HA calls, per this repo's own
binding rule that no test ever performs a real device action).
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "hase"

_core_spec = importlib.util.spec_from_file_location(
    "hase_spellbook_core", ROOT / "core/__init__.py",
    submodule_search_locations=[str(ROOT / "core")],
)
if _core_spec.name not in sys.modules:
    _core_module = importlib.util.module_from_spec(_core_spec)
    sys.modules[_core_spec.name] = _core_module
    _core_spec.loader.exec_module(_core_module)

engine = importlib.import_module("hase_spellbook_core.spellbook_engine")
models = importlib.import_module("hase_spellbook_core.models")

# One Spell per execution kind (core.mcp_capability.execution_kind's own
# five-way classification - the SAME five a new adapter must dispatch,
# never a different or narrower list invented per adapter). Every trigger
# is unique and unambiguous on purpose - these fixtures are for dispatch
# coverage, not matching-quality tests.
CONTRACT_ACTION = {
    "id": "contract_action", "trigger": "contract turn on the light in {area}",
    "operation": "light.set_power",
    "target": {"kind": "area_name", "capture": "area"},
    "arguments": {"on": True},
    "catchphrase": "Licht an.",
}
CONTRACT_QUERY = {
    "id": "contract_query", "trigger": "contract how many lights are on in {area}",
    "sources": [
        {"kind": "count", "target": {"kind": "area_name", "capture": "area"},
         "domain_filter": "light", "state_filter": "on", "variable": "n"},
    ],
    "output": "{n}",
}
CONTRACT_COMBINED = {
    "id": "contract_combined", "trigger": "contract evening routine in {area}",
    "steps": [
        {"alias": "light", "operation": "light.turn_on", "target": {"kind": "area_name", "capture": "area"}},
        {"alias": "count", "sources": [
            {"kind": "count", "target": {"kind": "area_name", "capture": "area"},
             "domain_filter": "light", "variable": "n"},
        ]},
    ],
    "output": "{light.target_count} lights, {count.n} total",
}
CONTRACT_TEXT_ANSWER = {
    "id": "contract_text_answer", "trigger": "contract tell me a fact",
    "output": "Bruchtal is a fine place to visit.",
    "assist_redirect": False,
}
CONTRACT_ASSIST_REDIRECT = {
    "id": "contract_assist_redirect", "trigger": "contract sunrise over {area}",
    "output": "Set light {area} to 100 percent.",
    "catchphrase": "Der neue Tag beginnt.",
}
CONTRACT_SPELLS = (
    CONTRACT_ACTION, CONTRACT_QUERY, CONTRACT_COMBINED, CONTRACT_TEXT_ANSWER, CONTRACT_ASSIST_REDIRECT,
)


def build_contract_book(book_id: str = "contract") -> Any:
    raw = {"id": book_id, "name": "Bridge contract fixtures", "commands": list(CONTRACT_SPELLS)}
    book = engine.load_spellbook_from_dict(book_id, raw)
    assert book is not None, "contract fixture spellbook failed to load"
    return book


DispatchOutcome = Any  # whatever shape the calling adapter's own dispatch returns
DispatchFn = Callable[[str, dict[str, str]], Awaitable[DispatchOutcome]]


async def assert_every_execution_kind_is_dispatched(
    dispatch: DispatchFn, *, extract_status: Callable[[DispatchOutcome], str],
) -> None:
    """Calls `dispatch(spell_id, captures)` once per CONTRACT_SPELLS entry
    and asserts every one of them reaches SOME real, non-error outcome -
    `extract_status` turns whatever the adapter's own dispatch return
    shape is into a short status string the adapter itself defines (e.g.
    "SUCCESS"/"QUERY_ANSWER"/"handled") purely for this assertion; this
    helper never assumes a shared response envelope, only that a real
    outcome (not a crash, not `None`, not a caught-and-swallowed
    exception) was reached for every kind. This is the exact property
    providers/code27/bridge.py's own query_plan gap violated - `match.
    query_plan` was silently never checked, so the whole request fell
    through into a Different (wrong) branch instead of raising or
    returning cleanly - a missing dispatch branch, not a wrong answer, so
    a plain "was the response correct" test would not have caught it
    either; this helper specifically checks that EVERY kind was reached
    AT ALL."""
    results: dict[str, str] = {}
    for spell in CONTRACT_SPELLS:
        captures = {"area": "Bruchtal"} if "{area}" in spell["trigger"] else {}
        outcome = await dispatch(spell["id"], captures)
        results[spell["id"]] = extract_status(outcome)
    missing = [spell_id for spell_id, status in results.items() if not status]
    assert not missing, f"these Spell kinds never reached a real dispatch outcome: {missing}"
    return results
