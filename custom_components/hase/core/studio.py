"""Book-scoped Studio data operations. No HA calls or runtime permissions here."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from difflib import SequenceMatcher

import yaml

from .book_languages import LIST_FIELDS, TEXT_FIELDS, language_objects
from .executor import OPERATION_CATALOG, ActionExecutor, _argument_type_matches
from .mcp_capability import check_book_mcp_capability
from .models import QUERY_RESPONSE_FIELDS_BY_DOMAIN, ActionDraft, ActionStep
from .spellbook_engine import (
    _AGGREGATE_SOURCE_KINDS,
    _AGGREGATE_SOURCE_TARGET_KINDS,
    _MAX_STEPS_PER_COMMAND,
    _QUERY_SOURCE_KINDS,
    _SINGLE_SOURCE_TARGET_KINDS,
    _load_base_command,
    _load_command,
    _valid_arguments_spec,
    build_draft,
    build_query_plan,
    build_step_plan,
    load_spellbook_from_dict,
)
from .step_variables import action_step_fields

BOOK_FIELDS = {"id", "name", "commands", "disabled", "description", "author", "version", "language",
               "languages", "default_language", "examples", "hase_min_version", "tags", "metadata"}
SPELL_FIELDS = {"id", "name", "trigger", "triggers", "operation", "target", "arguments", "alias",
                "output", "catchphrase", "examples", "expert", "enabled", "tags", "description", "metadata",
                "sources", "output_mode", "steps", "assist_redirect", "failure_text"}
EXPERT_FIELDS = {"operation", "target", "arguments", "catchphrase"}
MAX_BYTES = 1_000_000


class StudioError(ValueError):
    """A controlled, user-visible validation or revision error."""


def parse_document(text: str) -> dict:
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES:
        raise StudioError("Die Datei ist zu groß (maximal 1 MB).")
    # Reject aliases: recursive/amplifying YAML is not a portable spell format.
    try:
        if any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken))
               for token in yaml.scan(text)):
            raise StudioError("YAML-Anker und Referenzen werden nicht unterstützt.")
        class UniqueLoader(yaml.SafeLoader):
            def construct_mapping(self, node, deep=False):
                keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
                if len(set(keys)) != len(keys):
                    raise StudioError("Doppelte YAML-Felder sind nicht erlaubt.")
                return super().construct_mapping(node, deep=deep)
        raw = yaml.load(text, Loader=UniqueLoader)
    except yaml.YAMLError as err:
        raise StudioError("Die YAML-Datei kann nicht gelesen werden.") from err
    check_document(raw)
    return raw


def _spell_language_field_issues(obj: dict, *, path_prefix: str = "") -> list[dict]:
    """The exact per-object TEXT_FIELDS/LIST_FIELDS language-shape rule
    check_document already applies (via book_languages.language_objects)
    to the book itself and to every spell/expert dict - isolated here so
    describe_spell_basics can run it against a single spell (and its
    `expert` dict) without needing the whole book's language_objects
    pass. Not itself a new rule: check_document's own book-wide loop
    still runs unchanged and still catches these first for a real save,
    this is purely the single-spell projection of the same check."""
    code = r"[a-zA-Z]{2,3}(?:[-_][a-zA-Z0-9]{2,8})*"
    issues: list[dict] = []
    for field in TEXT_FIELDS + LIST_FIELDS:
        if field not in obj:
            continue
        value = obj[field]
        path = f"{path_prefix}{field}"
        if isinstance(value, dict):
            if not all(isinstance(k, str) and re.fullmatch(code, k) for k in value):
                issues.append({"code": "invalid_language_code", "path": path, "message": "Invalid language code"})
                continue
            values = value.values()
        else:
            values = [value]
        if field in LIST_FIELDS:
            valid = all(isinstance(v, list) and all(isinstance(t, str) for t in v) for v in values)
        else:
            valid = all(isinstance(v, str) for v in values)
        if not valid:
            issues.append({"code": "invalid_translated_content", "path": path, "message": "Invalid translated content"})
    return issues


def describe_spell_basics(raw_spell: dict, *, book: dict, owner_spell_id: str | None = None) -> list[dict]:
    """Per-spell projection of check_document()'s per-command field rules
    (unknown fields, id/uniqueness, enabled/alias/tags types, expert-field
    allowlist, per-language text/list field shape) - isolated to run
    against ONE (possibly still-incomplete) spell candidate rather than
    only reachable at whole-book scope. This is the Step 1 (Basic info)
    wizard-step validator (core/studio_validation.py); check_document()
    calls it per command instead of inlining the same checks, so the
    rules exist in exactly one place. `owner_spell_id` (the id of the
    spell actually being edited, if any) is excluded once from the
    duplicate-id count, the same "don't flag myself as my own duplicate"
    convention custio/studio.py's _validation_issues already uses."""
    issues: list[dict] = []

    def add(code: str, path: str, message: str) -> None:
        issues.append({
            "code": code, "severity": "error", "path": path, "message": message,
            "step_index": None, "classification": None,
        })

    if not isinstance(raw_spell, dict):
        add("invalid_spell", "", "Ein Spell enthält unbekannte Felder.")
        return issues
    if set(raw_spell) - SPELL_FIELDS:
        add("unknown_spell_fields", "", "Ein Spell enthält unbekannte Felder.")

    sid = raw_spell.get("id")
    if not isinstance(sid, str) or not sid.strip():
        add("missing_id", "id", "Jeder Spell benötigt eine eindeutige ID.")
    else:
        count = sum(
            1 for c in (book.get("commands") or [])
            if isinstance(c, dict) and c.get("id") == sid
        )
        if count > (1 if owner_spell_id == sid else 0):
            add("duplicate_id", "id", "Jeder Spell benötigt eine eindeutige ID.")

    if "enabled" in raw_spell and not isinstance(raw_spell["enabled"], bool):
        add("invalid_enabled", "enabled", "Aktiviert muss Ja oder Nein sein.")
    if "alias" in raw_spell and raw_spell["alias"] is not None and not (
        isinstance(raw_spell["alias"], str) and raw_spell["alias"].isidentifier()
    ):
        add("invalid_alias", "alias", "Der Schrittname muss ein gültiger String sein.")
    if "tags" in raw_spell and (not isinstance(raw_spell["tags"], list) or
                                not all(isinstance(t, str) for t in raw_spell["tags"])):
        add("invalid_tags", "tags", "Sammlungen müssen eine Liste von Namen sein.")
    expert = raw_spell.get("expert")
    if expert is not None and (not isinstance(expert, dict) or set(expert) - EXPERT_FIELDS):
        add("invalid_expert_fields", "expert", "Expert darf nur Operation, Ziel, Argumente und Antwort enthalten.")

    for field_issue in _spell_language_field_issues(raw_spell):
        add(field_issue["code"], field_issue["path"], field_issue["message"])
    if isinstance(expert, dict):
        for field_issue in _spell_language_field_issues(expert, path_prefix="expert."):
            add(field_issue["code"], field_issue["path"], field_issue["message"])

    return issues


def check_document(raw: dict) -> None:
    if not isinstance(raw, dict) or set(raw) - BOOK_FIELDS:
        raise StudioError("Unbekannte Buchfelder. Lokale Freigaben gehören nicht in Spell-Dateien.")
    if not isinstance(raw.get("id"), str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", raw["id"]):
        raise StudioError("Die Buch-ID darf nur Buchstaben, Ziffern, Bindestrich und Unterstrich enthalten.")
    if not isinstance(raw.get("disabled", False), bool):
        raise StudioError("Deaktiviert muss Ja oder Nein sein.")
    name = raw.get("name")
    if not (isinstance(name, str) and name.strip() or isinstance(name, dict) and any(name.values())):
        raise StudioError("Ein Buchname ist erforderlich.")
    commands = raw.get("commands")
    if not isinstance(commands, list) or len(commands) > 5000:
        raise StudioError("Ein Buch benötigt eine Liste mit höchstens 5000 Spells.")
    code = r"[a-zA-Z]{2,3}(?:[-_][a-zA-Z0-9]{2,8})*"
    for field in ("language", "default_language"):
        if field in raw and (not isinstance(raw[field], str) or not re.fullmatch(code, raw[field])):
            raise StudioError("Invalid default language")
    if "languages" in raw and (not isinstance(raw["languages"], list) or
                               not all(isinstance(v, str) and re.fullmatch(code, v) for v in raw["languages"])):
        raise StudioError("Invalid languages")
    for obj in language_objects(raw):
        if not isinstance(obj, dict):
            raise StudioError("Ungültiger Spell.")
        for field in TEXT_FIELDS + LIST_FIELDS:
            if field not in obj:
                continue
            value = obj[field]
            if isinstance(value, dict):
                if not all(isinstance(k, str) and re.fullmatch(code, k) for k in value):
                    raise StudioError("Invalid language code")
                values = value.values()
            else:
                values = [value]
            if field in LIST_FIELDS:
                valid = all(isinstance(v, list) and all(isinstance(t, str) for t in v) for v in values)
            else:
                valid = all(isinstance(v, str) for v in values)
            if not valid:
                raise StudioError("Invalid translated content")
    for command in commands:
        # describe_spell_basics is the single source of truth for these
        # per-command field rules (also used standalone by Step 1 of the
        # per-wizard-step validator) - check_document just raises on its
        # first reported issue, same fail-fast contract as before. Its
        # own duplicate-id count is checked against the whole book, so
        # a duplicate is caught here regardless of which of the two
        # occurrences is reached first - no separate running `ids` set
        # needed.
        basics = describe_spell_basics(command, book=raw, owner_spell_id=command.get("id"))
        if basics:
            raise StudioError(basics[0]["message"])
        if _load_command(command["id"], command) is None:
            raise StudioError(f"Spell {command['id']}: Trigger, Captures oder Aktionsstruktur sind ungültig.")
    if len(yaml.safe_dump(raw, allow_unicode=True).encode("utf-8")) > MAX_BYTES:
        raise StudioError("Das Buch ist zu groß (maximal 1 MB).")


def _step_needs_expert_permission(step: dict) -> bool:
    """Whether one `steps:` entry would hit executor.py's own expert-permission
    gate at runtime (see execute()'s `draft.target.kind == "template"` / `spec.
    expert_only` check, applied per-step by _execute_action_step) - a template
    target or a catalog operation explicitly marked expert_only. Query steps
    (`sources` key) have no expert-permission concept of their own (ActionStep's
    docstring: steps are USER-tier only, no per-step fallback layer)."""
    if not isinstance(step, dict) or "sources" in step:
        return False
    if step.get("target", {}).get("kind") == "template":
        return True
    spec = OPERATION_CATALOG.get(step.get("operation"))
    return bool(spec is not None and spec.expert_only)


def spell_capability(raw: dict) -> str:
    command = (
        _load_base_command(raw.get("id", "spell"), raw)
        if raw.get("operation") or raw.get("output") or raw.get("sources") or raw.get("steps") else None
    )
    has_user = command is not None and raw.get("target", {}).get("kind") != "template"
    if has_user and command.operation:
        spec = OPERATION_CATALOG.get(command.operation)
        has_user = spec is not None and bool(spec.user_choices) and command.target_spec.get("kind") in spec.allowed_target_kinds
    # A steps spell has no per-command Expert *override* of its own (no
    # `expert:` block, no user_fallback - see core/spellbook_engine.py's
    # _load_command) - but an individual step can still force EXPERT ONLY
    # at runtime (template target / expert_only operation), so the book-wide
    # capability badge must look inside `steps` instead of assuming USER.
    has_expert = "expert" in raw or raw.get("target", {}).get("kind") == "template" or any(
        _step_needs_expert_permission(step) for step in raw.get("steps", []) if isinstance(step, dict)
    )
    return "USER + EXPERT" if has_user and has_expert else "EXPERT ONLY" if has_expert else "USER"


def _is_freeform_light_color(arguments) -> bool:
    """light.set_color's Wizard color picker and free HA-color-name field
    (frontend lightColorPickerView) are a first-class Wizard capability
    that deliberately does not match one of the fixed named user_choices
    presets (see operations.py's own comment there) - an arbitrary RGB
    triple or a free color name, never a template/Jinja value."""
    if not isinstance(arguments, dict) or len(arguments) != 1:
        return False
    rgb, name = arguments.get("rgb_color"), arguments.get("color_name")
    if rgb is not None:
        return isinstance(rgb, list) and len(rgb) == 3 and all(isinstance(v, int) and 0 <= v <= 255 for v in rgb)
    if name is not None:
        return isinstance(name, str) and bool(name.strip())
    return False


def _catalog_match(operation, arguments) -> str | None:
    """None if operation+arguments exactly match one of the operation's own
    curated `user_choices` presets - the only thing the Wizard's guided action
    picker can select (see user_actions()) - otherwise a concrete reason why
    not. The single place both the Wizard-compatibility classifier below and
    (indirectly, via user_actions()) the Wizard's own action picker agree on
    what counts as "a Wizard action". light.set_color's free color widget
    (_is_freeform_light_color) is the one documented exception to "must
    match a preset exactly"."""
    if not isinstance(operation, str) or not operation:
        return "keine gültige Operation angegeben."
    spec = OPERATION_CATALOG.get(operation)
    if spec is None:
        return f"Operation „{operation}“ ist nicht im Katalog vorhanden."
    if spec.expert_only:
        return f"Operation „{operation}“ ist als Expert-only markiert."
    if operation == "light.set_color" and _is_freeform_light_color(arguments):
        return None
    if not any(arguments == choice_arguments for _, choice_arguments in spec.user_choices):
        return f"Operation „{operation}“ mit diesen Argumenten hat keine Wizard-Voreinstellung."
    return None


def _describe_action_arguments_shape(spec, arguments) -> list[dict]:
    """Step 1 (Actions & Queries)'s own argument-shape check, run BEFORE a
    trigger exists. A raw spell's `arguments` dict mixes two kinds of
    entries - a literal value (passed through to HA as-is) and a capture
    spec ({"capture": "value", "type": "int", "min": 0, "max": 100},
    resolved from spoken input only once a trigger declares that capture,
    see _resolve_arguments) - most Wizard-guided presets use captures
    (operations.py's own user_choices), so this must NOT reuse
    ActionExecutor.describe_shape/_describe_argument_errors, which expect
    already-RESOLVED literal values and would misjudge every capture spec
    dict as a wrongly-typed literal.

    A literal entry's type is checked directly against the operation's
    own declared type (reusing _argument_type_matches, never a second
    copy). A capture-spec entry's OWN structural rules (conversion type,
    min/max, value_map, true_values/false_values) are checked via
    spellbook_engine._valid_arguments_spec - passing it a placeholder-
    names set built from the capture names actually referenced in THIS
    arguments dict, so its one relational check (does a declared capture
    exist among the known placeholders) is trivially satisfied and never
    fires here; that real trigger-relationship check is deliberately
    deferred to Step 2 (Trigger), the earliest point it becomes
    checkable - see core/studio_validation.py's _TRIGGER_CAPTURE_CODES."""
    if not isinstance(arguments, dict):
        return [{"code": "invalid_arguments", "path": "arguments", "message": "Arguments müssen ein Objekt sein."}]
    issues: list[dict] = []
    required = set(spec.argument_types)
    optional = spec.optional_argument_types
    allowed = required | set(optional)
    variant_names = {name for schema in spec.argument_variants for name in schema}
    present = set(arguments)
    # A required argument may be missing from the primary schema and the
    # whole call still be valid, PROVIDED it instead matches one of
    # argument_variants exactly (light.set_power's {"on": bool} instead
    # of {"percentage": int}) - only flag "missing" when neither shape
    # could possibly apply.
    if required - present and not any(present == set(schema) for schema in spec.argument_variants):
        for name in sorted(required - present):
            issues.append({"code": "missing_required_argument", "path": f"arguments.{name}", "message": f"Pflichtargument '{name}' fehlt."})
    for name, value in arguments.items():
        if isinstance(value, dict) and "capture" in value:
            continue
        if name not in allowed and name not in variant_names:
            issues.append({"code": "unknown_argument", "path": f"arguments.{name}", "message": f"Unbekanntes Argument '{name}'."})
            continue
        expected = spec.argument_types.get(name, optional.get(name))
        if expected is None:
            for schema in spec.argument_variants:
                if name in schema:
                    expected = schema[name]
                    break
        if expected is not None and not _argument_type_matches(value, expected):
            issues.append({"code": "argument_type", "path": f"arguments.{name}", "message": f"Argument '{name}' hat einen ungültigen Typ."})
    self_placeholder_names = {
        value["capture"] for value in arguments.values()
        if isinstance(value, dict) and isinstance(value.get("capture"), str)
    }
    if not _valid_arguments_spec(arguments, self_placeholder_names):
        issues.append({
            "code": "invalid_argument_capture_spec", "path": "arguments",
            "message": "Ungültige Capture-Spezifikation (Typ, min/max oder value_map).",
        })
    return issues


def _action_step_blockers(step: dict, *, label: str, allow_area_name: bool, step_index=0) -> list[dict]:
    """Everything that keeps ONE action step (a `steps:` entry, or a flat
    single-action spell treated as its own one-item step for this check) out
    of the Wizard-representable subset. `allow_area_name` is True only for a
    flat, non-`steps` single action - the ORIGINAL Wizard already supports a
    spoken-area capture there (one fixed capture name per operation, see
    USER_ACTION_TARGET_CAPTURES); the guided step-list builder still doesn't
    (see steps_spell()'s _resolve_step_target docstring) - Expert can edit
    either, this only gates what counts as *Wizard*-representable.

    Every returned row now also carries `classification`: "invalid" for a
    row ActionExecutor.describe_shape (operation unknown, target kind not
    allowed for the operation, argument shape/type/range wrong) or an
    unknown operation would ALSO reject at runtime, vs "expert_only" for a
    row that is a perfectly valid, executable spell - it simply isn't one
    of the Wizard's own guided presets/UI affordances (a template target, a
    free-form entity_name capture, or valid arguments that don't match a
    user_choices preset). Only "invalid" rows may block a wizard step's
    Next; "expert_only" rows only ever block the Expert->Wizard conversion
    gate. See classify_spell() for the composed three-way verdict."""
    target = step.get("target") if isinstance(step.get("target"), dict) else {}
    kind = target.get("kind")
    reasons = []
    def add(code, path, message, classification):
        reasons.append({"code": code, "step_index": step_index, "path": path,
                        "message": f"{label}: {message}", "classification": classification})
    if kind == "template":
        add("template_target", "target.value", "Ziel-Art „template“ (Jinja) wird vom Wizard nicht unterstützt.", "expert_only")
    elif kind == "entity_name":
        add("entity_name_target", "target.kind", "Ziel-Art „entity_name“ wird vom Wizard nicht unterstützt.", "expert_only")
    elif kind == "area_name" and not allow_area_name:
        add("area_capture_target", "target.kind", "gesprochener Bereichs-Platzhalter wird für Schritte noch nicht unterstützt.", "expert_only")
    if kind != "template":
        operation = step.get("operation")
        arguments = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
        spec = OPERATION_CATALOG.get(operation) if isinstance(operation, str) and operation else None
        if spec is None:
            add(
                "unknown_operation", "operation",
                f"Operation „{operation}“ ist nicht im Katalog vorhanden." if operation else "keine gültige Operation angegeben.",
                "invalid",
            )
        elif kind is not None and kind not in spec.allowed_target_kinds:
            add("target_kind_not_allowed", "target.kind", f"Zieltyp „{kind}“ ist für „{operation}“ nicht zulässig.", "invalid")
        else:
            argument_errors = _describe_action_arguments_shape(spec, arguments)
            if argument_errors:
                for err in argument_errors:
                    add(err["code"], err["path"], err["message"], "invalid")
            else:
                reason = _catalog_match(operation, arguments)
                if reason:
                    add("operation_arguments", "operation" if spec.expert_only else "arguments", reason, "expert_only")
    return reasons


def _action_step_incompatibilities(step: dict, *, label: str, allow_area_name: bool) -> list[str]:
    return [row["message"] for row in _action_step_blockers(step, label=label, allow_area_name=allow_area_name)]


def wizard_blocker_details(raw: dict) -> list[dict]:
    """The single, authoritative definition of "the Wizard can build and
    re-save this spell identically" (item 17: a spell's editor is derived
    from its content, never a stored marker - see spellRequiresExpert in the
    frontend, which calls this via the `validate`/`read` response instead of
    re-deriving its own rules). Returns code, zero-based step_index (None
    for spell-level issues), field path and a backward-compatible message.
    An empty list means Wizard-representable. Reused for: which editor a saved
    spell opens in, the Expert->Wizard conversion gate, and the per-step
    "Expert" badges in a multi-step spell's overview.

    Query steps/sources have no reason of their own: `sources` never allows
    a `template` target (_SINGLE_SOURCE_TARGET_KINDS/_AGGREGATE_SOURCE_
    TARGET_KINDS in spellbook_engine.py have no "template" entry) and every
    other source field (kind/domain_filter/device_class/attribute/
    state_filter/variable) is already a guided form field - a query step can
    never force Expert."""
    reasons: list[dict] = []
    if "expert" in raw:
        reasons.append({"code": "expert_variant", "step_index": None, "path": "expert",
                        "message": "Enthält eine separate Expert-Variante (`expert:`-Block).",
                        "classification": "expert_only"})
    steps = raw.get("steps")
    if isinstance(steps, list) and steps:
        for i, step in enumerate(steps):
            label = f"Schritt {i + 1}"
            if not isinstance(step, dict):
                reasons.append({"code": "invalid_step", "step_index": i, "path": "",
                                "message": f"{label}: ungültige Struktur.", "classification": "invalid"})
            else:
                alias = step.get("alias")
                if alias is not None and not isinstance(alias, str):
                    reasons.append({"code": "invalid_step_alias", "step_index": i, "path": "alias",
                                    "message": f"{label}: Name muss ein String sein; der vorhandene Wert wurde nicht verändert.",
                                    "classification": "invalid"})
                if "sources" not in step:
                    reasons.extend(_action_step_blockers(step, label=label, allow_area_name=False, step_index=i))
    elif raw.get("operation"):
        alias = raw.get("alias")
        if alias is not None and not isinstance(alias, str):
            reasons.append({"code": "invalid_step_alias", "step_index": 0, "path": "alias",
                            "message": "Schritt 1: Name muss ein String sein; der vorhandene Wert wurde nicht verändert.",
                            "classification": "invalid"})
        reasons.extend(_action_step_blockers(raw, label="Schritt 1", allow_area_name=True))
    # A flat `sources:` (query, no `operation`/`steps`) and a pure text/
    # output-only spell (none of operation/sources/steps) are always
    # Wizard-representable - nothing further to check for either.
    return reasons


def wizard_incompatibilities(raw: dict) -> list[str]:
    """Backward-compatible text projection of the authoritative classifier."""
    return [row["message"] for row in wizard_blocker_details(raw)]


def classify_spell(raw: dict) -> str:
    """The one place the Wizard-compatible / Expert-only / Invalid
    three-way verdict is decided (per-wizard-step validation's "Expert
    is not an exception" requirement: Expert means freely editable
    structure, not unvalidated structure). Returns "wizard" (valid AND
    losslessly Wizard-representable), "expert_only" (valid, but not
    fully representable in the Wizard), or "invalid" (neither).
    Reused by: editor-mode-on-open routing, the Expert<->Wizard
    conversion gate, and Step 2 of the per-wizard-step validator - a
    step classified "expert_only" here is a valid, executable action
    that simply can't render as a Wizard preset, not a Step-2 error."""
    if _load_command(raw.get("id", "spell"), raw) is None:
        return "invalid"
    blockers = wizard_blocker_details(raw)
    if any(row.get("classification") == "invalid" for row in blockers):
        return "invalid"
    return "expert_only" if blockers else "wizard"


def as_logical_steps(raw: dict | None) -> list[dict]:
    """Normalizes any persisted spell shape (flat `operation`, flat
    `sources`, or `steps:`) into a uniform 0-N list of step dicts, so the
    Wizard/Expert step editor and its "preserve this step verbatim" escape
    hatch (wizard_spell()'s `kind: "preserve"`) work the same regardless of
    which shape the spell is currently stored in. A flat single action or
    query carries the optional top-level `alias` used for its shared Step
    metadata. Never mutates `raw`."""
    raw = raw or {}
    if isinstance(raw.get("steps"), list):
        return [step for step in raw["steps"] if isinstance(step, dict)]
    if raw.get("operation"):
        return [{"alias": raw.get("alias"), "operation": raw.get("operation"),
                 "target": raw.get("target"), "arguments": raw.get("arguments")}]
    if isinstance(raw.get("sources"), list) and raw["sources"]:
        return [{"alias": raw.get("alias"), "sources": raw["sources"]}]
    return []


def wizard_step_flags(raw: dict) -> list[bool]:
    """Per-step companion to wizard_incompatibilities() (same underlying
    rule, see _action_step_incompatibilities): one bool per as_logical_steps(raw)
    entry, True where THAT step alone needs the Expert editor - the per-step
    "Expert"/"Jinja" badge in a multi-step spell's overview (item 15) so a
    12-step spell can show exactly which one step blocks a Wizard
    conversion, without the frontend re-deriving the classification rule
    itself. A flat single action's one-item list uses the same
    `allow_area_name=True` leniency wizard_incompatibilities() gives it."""
    steps = as_logical_steps(raw)
    is_flat_single = not isinstance(raw.get("steps"), list)
    return [
        bool("sources" not in step and _action_step_incompatibilities(
            step, label="", allow_area_name=is_flat_single and len(steps) == 1,
        ))
        for step in steps
    ]


def spell_variables(raw: dict) -> list[dict]:
    """Every variable name this spell's response/catchphrase text may
    legally reference (item 6/7) - the ONE catalog the Wizard's response-
    chip UI should read instead of maintaining its own separate lists for
    trigger captures, entity.get_state's fixed per-domain fields, and
    multi-source query variables. Each row is
    {"name": ..., "source": "capture" | "query_field" | "source_variable"
    | "step_field"}, with "domain" added for query_field rows (which HA
    domain that field applies to, or "*"), "kind" for source_variable rows
    (the QuerySourceSpec kind it came from), and "alias"/"field"/"kind"
    (kind: "action" | "query") for step_field rows (a Combined Spell's own
    {alias.field} variables, "name" already the fully-qualified
    "alias.field" the response template actually uses) - purely
    descriptive metadata for the frontend to group/label chips with,
    never re-derived by it."""
    rows: list[dict] = []
    command = (
        _load_base_command(raw.get("id", "spell"), raw)
        if raw.get("operation") or raw.get("output") or raw.get("sources") or raw.get("steps") else None
    )
    if command is None:
        return rows
    seen: set[str] = set()
    for segments in command.compiled.values():
        for kind, name in segments:
            if kind == "ph" and name not in seen:
                seen.add(name)
                rows.append({"name": name, "source": "capture"})
    _op_spec = OPERATION_CATALOG.get(command.operation)
    if _op_spec is not None and _op_spec.kind == "query" and _op_spec.response_fields:
        for domain, fields in _op_spec.response_fields.items():
            for name in fields:
                if name not in seen:
                    seen.add(name)
                    rows.append({"name": name, "source": "query_field", "domain": domain})
    for source in command.sources:
        for name in (source.get("variable"), source.get("unit_variable")):
            if isinstance(name, str) and name and name not in seen:
                seen.add(name)
                rows.append({"name": name, "source": "source_variable", "kind": source.get("kind")})
    for step in command.steps:
        alias = step.get("alias")
        if not isinstance(alias, str):
            continue
        if step["kind"] == "action":
            target_kind = step["target"].get("kind")
            for field in action_step_fields(step["operation"], target_kind):
                name = f"{alias}.{field}"
                if name not in seen:
                    seen.add(name)
                    rows.append({"name": name, "source": "step_field", "kind": "action",
                                "alias": alias, "field": field, "operation": step["operation"]})
        else:
            for source in step["sources"]:
                for field in (source.get("variable"), source.get("unit_variable")):
                    if isinstance(field, str) and field:
                        name = f"{alias}.{field}"
                        if name not in seen:
                            seen.add(name)
                            rows.append({"name": name, "source": "step_field", "kind": "query",
                                        "alias": alias, "field": field})
    return rows


def book_capability(raw: dict) -> str:
    values = {spell_capability(c) for c in raw["commands"]}
    return "EXPERT REQUIRED" if "EXPERT ONLY" in values else "USER + EXPERT" if "USER + EXPERT" in values else "USER"


USER_ACTION_TARGET_CAPTURES = {
    # vacuum.clean_area resolves a spoken room/area name through the vacuum
    # resolver. Keep its historic/public spellbook placeholder {target}
    # instead of forcing every area_name action to use {area}.
    "vacuum.clean_area": "target",
}


def query_source_capabilities() -> dict:
    """The small, fixed vocabulary a Combined Spell's query-step builder
    needs (kind/target-kind choices) - backend-derived from
    core/spellbook_engine.py's own private constants (the single source
    of truth `_parse_sources` already validates against) so the Wizard
    never hardcodes a second copy, same rule `user_actions()` already
    follows for operations/domains."""
    return {
        "kinds": sorted(_QUERY_SOURCE_KINDS),
        "aggregate_kinds": sorted(_AGGREGATE_SOURCE_KINDS),
        "single_target_kinds": sorted(_SINGLE_SOURCE_TARGET_KINDS),
        "aggregate_target_kinds": sorted(_AGGREGATE_SOURCE_TARGET_KINDS),
        "max_steps": _MAX_STEPS_PER_COMMAND,
    }


def user_actions() -> list[dict]:
    rows = [{"id": f"{name}:{i}", "label": label, "operation": name,
             "arguments": deepcopy(arguments),
             "targets": sorted(spec.allowed_target_kinds - {"template", "none"}),
             "response_fields": {
                 domain: list(fields) for domain, fields in spec.response_fields.items()
             },
             "target_capture": USER_ACTION_TARGET_CAPTURES.get(name, "area")
             if "area_name" in spec.allowed_target_kinds else None,
             # None means "always offered" - the Wizard only needs to cross-
             # check this against entity_capability_flags() when it's set
             # (RGB/tilt/presets/... on a specific entity). See that
             # function's own docstring for the tag vocabulary.
             "capability_hint": spec.capability_hint,
             # Drives the Wizard's domain-filtered action picker (item 13) -
             # None means "domain-agnostic" (entity.get_state works on any
             # domain) and is always offered regardless of the selected
             # filter, never hidden by a specific domain choice.
             "domain_filter": spec.domain_filter}
            for name, spec in OPERATION_CATALOG.items()
            for i, (label, arguments) in enumerate(spec.user_choices)]
    priority = {
        "light.turn_on": 0,
        "light.turn_off": 1,
        "light.toggle": 2,
        "light.set_power": 3,
    }
    return sorted(rows, key=lambda row: priority.get(row["operation"], 100))


# HA's raw CoverEntityFeature/FanEntityFeature bit flags (core/ has zero
# homeassistant imports, so these are copied as plain ints rather than
# imported from homeassistant.components.*.const - they're a stable part of
# HA's public entity-feature contract, not internal implementation detail).
_COVER_FEATURE_SET_POSITION = 4
_COVER_FEATURE_OPEN_TILT = 16
_COVER_FEATURE_CLOSE_TILT = 32
_COVER_FEATURE_STOP_TILT = 64
_COVER_FEATURE_SET_TILT_POSITION = 128
_FAN_FEATURE_SET_SPEED = 1
_FAN_FEATURE_OSCILLATE = 2
_FAN_FEATURE_PRESET_MODE = 8


def entity_capability_flags(domain: str, attributes: dict) -> dict:
    """Turn one entity's live state attributes into the small set of
    booleans/lists the Wizard's capability-driven UI (item 13) cross-checks
    a `user_actions()` row's `capability_hint` against, to decide whether to
    OFFER an action for the currently-selected entity - never to enforce it
    (HA's own service call is still the real authority; an offered-but-
    actually-unsupported action just fails gracefully like any other
    capability mismatch, same non-goal as OperationSpec's int_ranges).

    Tag vocabulary (must stay in sync with OperationSpec.capability_hint
    values in core/operations.py): rgb, color_temp, effect, position, tilt,
    hvac_modes, preset_modes, fan_modes, percentage, oscillate, source_list,
    options.
    """
    if not isinstance(attributes, dict):
        attributes = {}
    supported_features = attributes.get("supported_features")
    supported_features = supported_features if isinstance(supported_features, int) else 0
    color_modes = attributes.get("supported_color_modes")
    color_modes = set(color_modes) if isinstance(color_modes, list) else set()

    if domain == "light":
        return {
            "rgb": bool(color_modes & {"rgb", "rgbw", "rgbww", "hs", "xy"}),
            "color_temp": "color_temp" in color_modes,
            "effect": list(attributes.get("effect_list") or []),
        }
    if domain == "cover":
        return {
            "position": bool(supported_features & _COVER_FEATURE_SET_POSITION),
            "tilt": bool(supported_features & (
                _COVER_FEATURE_OPEN_TILT | _COVER_FEATURE_CLOSE_TILT
                | _COVER_FEATURE_STOP_TILT | _COVER_FEATURE_SET_TILT_POSITION
            )),
        }
    if domain == "climate":
        return {
            "hvac_modes": list(attributes.get("hvac_modes") or []),
            "preset_modes": list(attributes.get("preset_modes") or []),
            "fan_modes": list(attributes.get("fan_modes") or []),
        }
    if domain == "fan":
        return {
            "percentage": bool(supported_features & _FAN_FEATURE_SET_SPEED),
            "oscillate": bool(supported_features & _FAN_FEATURE_OSCILLATE),
            "preset_modes": list(attributes.get("preset_modes") or []) if supported_features & _FAN_FEATURE_PRESET_MODE else [],
        }
    if domain == "media_player":
        return {"source_list": list(attributes.get("source_list") or [])}
    if domain in ("select", "input_select"):
        return {"options": list(attributes.get("options") or [])}
    return {}


def preserve_user(old: dict | None, new: dict, *, remove_user: bool = False) -> dict:
    new = deepcopy(new)
    if old and spell_capability(old) != "EXPERT ONLY" and spell_capability(new) == "EXPERT ONLY" and not remove_user:
        if "expert" not in new:
            new["expert"] = {k: new[k] for k in EXPERT_FIELDS if k in new}
        for key in ("operation", "target", "arguments", "output", "catchphrase"):
            if key in old:
                new[key] = deepcopy(old[key])
            else:
                new.pop(key, None)
        if _load_base_command(new.get("id", "spell"), new) is None:
            # Shared triggers must still supply the existing fallback's captures.
            new["trigger"] = deepcopy(old["trigger"])
            new["triggers"] = deepcopy(old.get("triggers", []))
    return new


def merge_book(existing: dict, incoming: dict, decisions: dict, *, remove_user=False) -> dict:
    check_document(incoming)
    result = deepcopy(existing)
    by_id = {c["id"]: i for i, c in enumerate(result["commands"])}
    for command in incoming["commands"]:
        sid = command["id"]
        decision = decisions.get(sid, "keep" if sid in by_id else "add")
        if decision not in {"keep", "skip", "replace", "copy", "add"}:
            raise StudioError("Unbekannte Konfliktentscheidung.")
        if decision in {"keep", "skip"}:
            continue
        if sid in by_id and decision == "add":
            raise StudioError("Ein vorhandener Spell darf nicht still überschrieben werden.")
        if decision == "replace" and sid in by_id:
            result["commands"][by_id[sid]] = preserve_user(result["commands"][by_id[sid]], command, remove_user=remove_user)
        else:
            command = deepcopy(command)
            if sid in by_id:
                i = 2
                while f"{sid}_copy_{i}" in by_id:
                    i += 1
                command["id"] = f"{sid}_copy_{i}"
            by_id[command["id"]] = len(result["commands"])
            result["commands"].append(command)
    check_document(result)
    return result


def export_book(raw: dict, ids: list[str] | None = None, collection: str | None = None) -> dict:
    result = deepcopy(raw)
    result["commands"] = [c for c in result["commands"] if
                          (ids is None or c["id"] in ids) and
                          (collection is None or collection in c.get("tags", []))]
    # metadata.mcp (attach_mcp_capability_cache) is Studio's own derived,
    # recomputed-on-every-save cache - authored content only belongs in an
    # export, the same reason instance-local permissions never appear here
    # either (those simply live outside the book document; this one has to
    # be stripped explicitly since it lives inside it). Re-attached fresh
    # the moment an imported copy is next saved, never lost information.
    for command in result["commands"]:
        metadata = command.get("metadata")
        if isinstance(metadata, dict) and "mcp" in metadata:
            metadata = dict(metadata)
            del metadata["mcp"]
            if metadata:
                command["metadata"] = metadata
            else:
                del command["metadata"]
    return result


def _sample_captures(command, arguments_specs) -> dict[str, str]:
    """Plausible sample values for a static check: "test" for every trigger
    placeholder, but a valid in-range number / declared choice for captures
    used as typed arguments.  Shared by flat actions and Combined Spell
    steps so both are checked the same way."""
    captures = {name: "test" for segments in command.compiled.values() for kind, name in segments if kind == "ph"}
    for arguments_spec in arguments_specs:
        for value in arguments_spec.values():
            if isinstance(value, dict) and value.get("capture"):
                if value.get("type") in ("int", "float"):
                    minimum = value.get("min", 0)
                    maximum = value.get("max", 100)
                    sample = 50 if minimum <= 50 <= maximum else minimum
                    captures[value["capture"]] = str(sample)
                    continue
                choices = value.get("true_values") or value.get("false_values") or list(value.get("value_map") or {})
                if choices:
                    captures[value["capture"]] = choices[0]
    return captures


def static_report(raw: dict) -> dict:
    rows, triggers = [], []
    checker = ActionExecutor(None, None)
    for spell in raw["commands"]:
        command = _load_command(spell["id"], spell)
        errors, warnings, infos = [], [], []
        for variant in ("user", "expert"):
            candidate = command if variant == "user" else command.expert_command
            if variant == "user" and command.expert_only:
                candidate = None
            if variant == "expert" and command.expert_only:
                candidate = command
            if candidate is None:
                if variant == "expert" and "expert" in spell:
                    errors.append("Expert-Variante ungültig; vorhandener User-Fallback bleibt erhalten.")
                continue
            if candidate.sources:
                captures = {name: "test" for segments in candidate.compiled.values() for kind, name in segments if kind == "ph"}
                from dataclasses import replace as _replace
                plan = build_query_plan(_replace(candidate, expert_command=None), captures, "de")
                if plan is None:
                    errors.append(f"{variant}: Datenquellen oder Ziel ungültig.")
                continue
            if candidate.steps:
                captures = _sample_captures(
                    candidate, [step["arguments"] for step in candidate.steps if step.get("kind") == "action"],
                )
                from dataclasses import replace as _replace
                step_diagnostics: list[dict] = []
                plan = build_step_plan(
                    _replace(candidate, expert_command=None), captures, "de", diagnostics=step_diagnostics,
                )
                if plan is None:
                    failed = step_diagnostics[0] if step_diagnostics else {}
                    label = f"Schritt {failed['step'] + 1}" if "step" in failed else "Schritte"
                    detail = {"target_unresolved": "Ziel ungültig", "argument_invalid": "Parameter ungültig"}.get(
                        failed.get("reason"), "Schritte oder Ziel ungültig")
                    errors.append(f"{variant}: {label}: {detail}.")
                else:
                    # Every invalid action step is named (previously one
                    # generic message for the whole spell).
                    for index, step in enumerate(plan.steps, start=1):
                        if isinstance(step, ActionStep) and not checker.validate_shape(
                            ActionDraft(operation=step.operation, target=step.target, arguments=step.arguments)
                        ):
                            errors.append(
                                f"{variant}: Schritt {index} ({step.operation}): Aktion, Ziel oder Parameter ungültig."
                            )
                continue
            if candidate.operation is None:
                infos.append("Reine Textausgabe: Ausführung hängt vom Client ab.")
                continue
            captures = _sample_captures(candidate, [candidate.arguments_spec])
            # Assess this variant independently, not its fallback.
            from dataclasses import replace
            draft = build_draft(replace(candidate, expert_command=None), captures, "de")
            if draft is None or not checker.validate_shape(draft):
                errors.append(f"{variant}: Aktion, Ziel oder Parameter ungültig.")
        texts = list(command.trigger.values()) if isinstance(command.trigger, dict) else [command.trigger]
        texts.extend(t for group in command.aliases.values() for t in group) if isinstance(command.aliases, dict) else texts.extend(command.aliases)
        for text in texts:
            normalized = " ".join(text.casefold().split())
            for sid, previous in triggers:
                if normalized == previous:
                    warnings.append(f"Doppelter Trigger mit {sid}.")
                elif SequenceMatcher(None, normalized, previous).ratio() >= .9:
                    infos.append(f"Sehr ähnlicher Trigger mit {sid}.")
            triggers.append((spell["id"], normalized))
        rows.append({"id": spell["id"], "capability": spell_capability(spell),
                     "valid": not errors, "errors": errors, "warnings": warnings, "infos": infos,
                     "enabled": spell.get("enabled", True)})
    return {"capability": book_capability(raw), "spells": rows,
            "errors": sum(len(r["errors"]) for r in rows),
            "warnings": sum(len(r["warnings"]) for r in rows),
            "infos": sum(len(r["infos"]) for r in rows)}


# Bumped whenever core.mcp_capability's own classification logic changes in
# a way that could change a previously-computed, persisted result - lets a
# book saved under an older HASE version be recognized as stale even though
# its own spell content never changed (see _mcp_spell_hash's own docstring
# for the complementary content-based half of staleness detection).
MCP_CAPABILITY_SCHEMA_VERSION = 1


def _mcp_spell_hash(spell: dict) -> str:
    """A content hash over everything about this spell EXCEPT its own
    `metadata` field - which is where the persisted MCP-capability cache
    itself lives (see attach_mcp_capability_cache), so including it would
    make the hash depend on its own previous output. Deliberately broader
    than the fields core.mcp_capability.execution_kind actually reads
    (covers e.g. `name`/`description` too, which never affect
    projectability) - a spurious cache invalidation is free and safe, a
    missed one silently shows a stale saved status as current, which is
    not."""
    payload = {k: v for k, v in spell.items() if k != "metadata"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
    ).hexdigest()


def mcp_capability_report(raw: dict, *, mark_stale: bool = False) -> dict:
    """The "MCP prüfen" button's own report - whether each enabled Spell
    can be exposed as an MCP tool at all (STRUCTURAL capability) and
    whether it additionally needs a Bridge's own policy to be fully
    unrestricted to actually be callable (ACTUAL grant) - see
    core.mcp_capability's own module docstring for why these are always
    reported as two separate questions, never one combined verdict.

    Loads `raw` through the exact same load_spellbook_from_dict every real
    tools/list call already goes through (never a second, hand-rolled
    per-spell loader) - a Spell that fails to load at all here would also
    fail to load for a real Bridge, so this never needs its own separate
    "book failed to load" bookkeeping beyond what check_document/write()
    already guarantee before this is ever called.

    The returned `execution_kind`/`projectable`/etc values are ALWAYS
    freshly computed from `raw` as given - this function never reads or
    trusts a spell's own persisted `metadata.mcp` cache for the numbers it
    reports, only (when `mark_stale=True`) to decide whether that cache
    still MATCHES what was just computed, exposed per spell as `stale`.
    `stale=True` means the book's own saved copy (if any) does not yet
    reflect this result - either because it was never saved with a cache
    at all (a book from before this feature, a fresh import, an
    externally-edited YAML file), or the cache is for a different
    MCP_CAPABILITY_SCHEMA_VERSION - not because the numbers themselves are
    in doubt. Used by the "read" operation so Spellbook Studio can show a
    book's MCP badges immediately on open, before anyone clicks "MCP
    prüfen" - the runtime execution path (core/mcp_execution.py) never
    calls this function and never reads `metadata.mcp` either way."""
    book = load_spellbook_from_dict(raw.get("id", ""), raw)
    if book is None:
        empty = {"spells": [], "projectable": 0, "not_projectable": 0, "requires_unrestricted": 0}
        if mark_stale:
            empty["stale_count"] = 0
        return empty
    results = check_book_mcp_capability(book)
    spells_by_id = {
        spell["id"]: spell for spell in raw.get("commands", [])
        if isinstance(spell, dict) and isinstance(spell.get("id"), str)
    }
    stale_count = 0
    entries = []
    for r in results:
        entry = {
            "id": r.spell_id,
            "execution_kind": r.execution_kind,
            "projectable": r.projectable,
            "skip_reason": r.skip_reason,
            "read_only": r.read_only,
            "requires_unrestricted_bridge": r.requires_unrestricted_bridge,
            "grant_reason": r.grant_reason,
        }
        if mark_stale:
            spell = spells_by_id.get(r.spell_id)
            cache = spell.get("metadata", {}).get("mcp") if isinstance(spell, dict) else None
            is_stale = not (
                isinstance(cache, dict)
                and cache.get("schema_version") == MCP_CAPABILITY_SCHEMA_VERSION
                and spell is not None and cache.get("spell_hash") == _mcp_spell_hash(spell)
            )
            entry["stale"] = is_stale
            if is_stale:
                stale_count += 1
        entries.append(entry)
    report = {
        "spells": entries,
        "projectable": sum(1 for r in results if r.projectable),
        "not_projectable": sum(1 for r in results if not r.projectable),
        "requires_unrestricted": sum(1 for r in results if r.requires_unrestricted_bridge),
    }
    if mark_stale:
        report["stale_count"] = stale_count
    # Tab badge summary: one warning per spell that is not projectable,
    # needs an unrestricted bridge, or shows a stale cached state.
    attention = {
        e["id"] for e in entries
        if not e["projectable"] or e["requires_unrestricted_bridge"] or e.get("stale")
    }
    report["status"] = {
        "state": "warning" if attention else "ok",
        "errors": 0, "warnings": len(attention), "infos": 0,
    }
    return report


def attach_mcp_capability_cache(raw: dict) -> dict:
    """The "automatically saved MCP status" - mutates `raw` in place (and
    returns it) so every spell's own `metadata.mcp` carries this book's
    CURRENT, freshly-computed MCP capability once this is called, keyed by
    _mcp_spell_hash/MCP_CAPABILITY_SCHEMA_VERSION so a later
    mcp_capability_report(..., mark_stale=True) call can tell whether a
    saved copy still matches. Called from studio.py's own write() - so
    EVERY Studio save (a spell edit, an import, a metadata change, a
    language edit, or an explicit "MCP prüfen") refreshes it, never a
    second place that must remember to call this. This is Studio's own
    informational cache for the editor UI only - core/mcp_execution.py's
    real tools/call path never reads `metadata.mcp` and always recomputes
    capability/permissions from scratch; a stale or even missing cache
    here can never change what a real MCP call is allowed to do."""
    report = mcp_capability_report(raw)
    by_id = {entry["id"]: entry for entry in report["spells"]}
    for spell in raw.get("commands", []):
        if not isinstance(spell, dict):
            continue
        result = by_id.get(spell.get("id"))
        if result is None:
            # Only a disabled spell has no capability result at all (see
            # check_book_mcp_capability) - a duplicate-id spell still
            # gets one (skip_reason="duplicate_spell_id"). Drop any
            # leftover cache rather than leave a misleading one in place.
            if isinstance(spell.get("metadata"), dict):
                spell["metadata"].pop("mcp", None)
            continue
        cache = {
            "execution_kind": result["execution_kind"],
            "projectable": result["projectable"],
            "skip_reason": result["skip_reason"],
            "read_only": result["read_only"],
            "requires_unrestricted_bridge": result["requires_unrestricted_bridge"],
            "grant_reason": result["grant_reason"],
            "spell_hash": _mcp_spell_hash(spell),
            "schema_version": MCP_CAPABILITY_SCHEMA_VERSION,
        }
        spell.setdefault("metadata", {})["mcp"] = cache
    return raw
