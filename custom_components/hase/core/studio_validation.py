"""Per-wizard-step validation orchestration - one shared functional
validation base (describe_spell_basics, ActionExecutor.describe_shape via
wizard_blocker_details, _load_command's structured diagnostics,
static_report), composed here per wizard step. No new business rules are
invented in this module - every check below is a thin composition/
filtering call into a primitive that already lives in core/studio.py,
core/executor.py or core/spellbook_engine.py, so there is exactly one
place each rule is expressed, never a parallel per-step rule set.

Each wizard step is additive: step N's result is a superset (in coverage)
of step N-1's, so whatever Step 5 (Review & Test) would find was already
findable, and already found, by the earliest step that made it checkable
- Step 5 is a safety net, never a first discovery.
"""
from __future__ import annotations

from typing import Any

from .spellbook_engine import _load_command
from .studio import describe_spell_basics, static_report, wizard_blocker_details

# Codes _fail() (core/spellbook_engine.py) can attach that concern trigger
# syntax, placeholders and capture consistency - the checks Step 2 (Trigger)
# newly makes possible once a trigger is known. Deliberately excludes the
# response/output-only codes (those belong to Step 3 - see _RESPONSE_CODES).
_TRIGGER_CAPTURE_CODES = frozenset({
    "missing_trigger", "adjacent_placeholders", "unknown_trigger_placeholder",
    "malformed_placeholder_syntax", "unknown_capture", "target_missing_capture_or_value",
    "invalid_arguments_spec", "invalid_aliases", "steps_with_expert_variant",
    "invalid_target", "invalid_operation", "multiple_command_forms", "empty_command",
    "invalid_output_mode", "invalid_assist_redirect",
})
# Codes that concern the response/output text itself - the checks Step 3
# (Response) newly makes possible (available capture values, aliases,
# unknown/multi-step references).
_RESPONSE_CODES = frozenset({
    "unknown_response_placeholder", "invalid_output", "invalid_catchphrase",
    "invalid_failure_text", "unknown_failure_text_placeholder",
})


def _describe_step_list(candidate: dict) -> list[dict]:
    """Step 1 (Actions & Queries): every action/query in the spell fully
    checked for local structure - operation known, target kind allowed,
    arguments required/typed/ranged. Reuses wizard_blocker_details (which
    already runs ActionExecutor.describe_shape per action step via
    _action_step_blockers) rather than re-implementing any of those
    rules, filtered to classification="invalid" rows only - an
    "expert_only" row (valid, just not a Wizard preset) is not a Step-1
    blocker, only a Wizard-representability concern (see classify_spell)."""
    return [
        {
            "code": row["code"], "severity": "error", "path": row.get("path", ""),
            "message": row["message"], "step_index": row.get("step_index"),
            "classification": row.get("classification"),
        }
        for row in wizard_blocker_details(candidate)
        if row.get("classification") == "invalid"
    ]


def _describe_trigger_or_response(candidate: dict, codes: frozenset[str]) -> list[dict]:
    diagnostics: list[dict[str, Any]] = []
    _load_command(candidate.get("id") or "spell", candidate, diagnostics=diagnostics)
    return [
        {
            "code": d["code"], "severity": "error", "path": d["path"], "message": d["message"],
            "step_index": None, "classification": None,
        }
        for d in diagnostics if d["code"] in codes
    ]


def _describe_full_document(candidate: dict, *, book: dict) -> list[dict]:
    """Step 4 (Review & Test) safety net: the same engine-level checks
    static_report() already runs for the whole book (real matching/
    query/step-plan compilation, ActionExecutor.validate_shape per
    action), scoped to just this one candidate spell by substituting it
    into a copy of the book. Never re-implements static_report's rules,
    only calls it."""
    spell_id = candidate.get("id")
    if not isinstance(spell_id, str) or not spell_id.strip():
        return []
    # static_report() assumes every command it's given already loads (the
    # normal check_document()-before-static_report() sequencing - see
    # core/studio.py:static_report's own `command.expert_only` access,
    # which would otherwise crash on a `None` command). validate_wizard_step
    # is called on in-progress, possibly still-broken candidates, so that
    # assumption cannot be relied on here - confirm loadability first and
    # report a precise, structured reason instead of ever reaching that
    # crash, using the exact same diagnostics the loader itself produces.
    diagnostics: list[dict] = []
    if _load_command(spell_id, candidate, diagnostics=diagnostics) is None:
        return [
            {
                "code": d["code"], "severity": "error", "path": d["path"],
                "message": d["message"], "step_index": None, "classification": None,
            }
            for d in diagnostics
        ] or [{
            "code": "invalid_structure", "severity": "error", "path": "",
            "message": "Trigger, Captures oder Aktionsstruktur sind ungültig.",
            "step_index": None, "classification": None,
        }]
    commands = [
        c for c in (book.get("commands") or [])
        if isinstance(c, dict) and c.get("id") != spell_id
    ]
    commands.append(candidate)
    report = static_report({**book, "commands": commands})
    row = next((r for r in report["spells"] if r["id"] == spell_id), None)
    if not row:
        return []
    issues: list[dict] = []
    for message in row.get("errors", []):
        issues.append({
            "code": "static_report_error", "severity": "error", "path": "",
            "message": message, "step_index": None, "classification": None,
        })
    for message in row.get("warnings", []):
        issues.append({
            "code": "static_report_warning", "severity": "warning", "path": "",
            "message": message, "step_index": None, "classification": None,
        })
    return issues


def validate_wizard_step(
    step: int, candidate: dict, *, book: dict, owner_spell_id: str | None = None,
) -> list[dict]:
    """Runs only the checks wizard step `step` (0-4: Basic info, Actions &
    Queries, Trigger, Response, Review & Test) is capable of running,
    ADDITIVELY - step N includes every check step N-1 already ran, plus
    its own new ones. `candidate` is the best-effort spell dict already
    assembled for this stage (see custom_components/hase/studio.py's
    wizard_spell(..., stage=step)) - this function only validates it, it
    never assembles it."""
    issues: list[dict] = []
    if step >= 0:
        issues += describe_spell_basics(candidate, book=book, owner_spell_id=owner_spell_id)
    if step >= 1:
        issues += _describe_step_list(candidate)
    if step >= 2:
        issues += _describe_trigger_or_response(candidate, _TRIGGER_CAPTURE_CODES)
    if step >= 3:
        issues += _describe_trigger_or_response(candidate, _RESPONSE_CODES)
    if step >= 4:
        issues += _describe_full_document(candidate, book=book)
    return issues
