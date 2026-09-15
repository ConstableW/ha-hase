"""Book-scoped Studio data operations. No HA calls or runtime permissions here."""
from __future__ import annotations

import re
from copy import deepcopy
from difflib import SequenceMatcher

import yaml

from .book_languages import LIST_FIELDS, TEXT_FIELDS, language_objects
from .executor import OPERATION_CATALOG, ActionExecutor
from .models import QUERY_RESPONSE_FIELDS_BY_DOMAIN, ActionDraft, ActionStep
from .spellbook_engine import (
    _AGGREGATE_SOURCE_KINDS,
    _AGGREGATE_SOURCE_TARGET_KINDS,
    _MAX_STEPS_PER_COMMAND,
    _QUERY_SOURCE_KINDS,
    _SINGLE_SOURCE_TARGET_KINDS,
    _load_base_command,
    _load_command,
    build_draft,
    build_query_plan,
    build_step_plan,
)
from .step_variables import action_step_fields

BOOK_FIELDS = {"id", "name", "commands", "description", "author", "version", "language",
               "languages", "default_language", "examples", "hase_min_version", "tags", "metadata"}
SPELL_FIELDS = {"id", "name", "trigger", "triggers", "operation", "target", "arguments",
                "output", "catchphrase", "examples", "expert", "enabled", "tags", "description", "metadata",
                "sources", "output_mode", "steps"}
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


def check_document(raw: dict) -> None:
    if not isinstance(raw, dict) or set(raw) - BOOK_FIELDS:
        raise StudioError("Unbekannte Buchfelder. Lokale Freigaben gehören nicht in Spell-Dateien.")
    if not isinstance(raw.get("id"), str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", raw["id"]):
        raise StudioError("Die Buch-ID darf nur Buchstaben, Ziffern, Bindestrich und Unterstrich enthalten.")
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
    ids = set()
    for command in commands:
        if not isinstance(command, dict) or set(command) - SPELL_FIELDS:
            raise StudioError("Ein Spell enthält unbekannte Felder.")
        sid = command.get("id")
        if not isinstance(sid, str) or not sid.strip() or sid in ids:
            raise StudioError("Jeder Spell benötigt eine eindeutige ID.")
        ids.add(sid)
        if "enabled" in command and not isinstance(command["enabled"], bool):
            raise StudioError("Aktiviert muss Ja oder Nein sein.")
        if "tags" in command and (not isinstance(command["tags"], list) or
                                  not all(isinstance(t, str) for t in command["tags"])):
            raise StudioError("Sammlungen müssen eine Liste von Namen sein.")
        expert = command.get("expert")
        if expert is not None and (not isinstance(expert, dict) or set(expert) - EXPERT_FIELDS):
            raise StudioError("Expert darf nur Operation, Ziel, Argumente und Antwort enthalten.")
        if _load_command(sid, command) is None:
            raise StudioError(f"Spell {sid}: Trigger, Captures oder Aktionsstruktur sind ungültig.")
    if len(yaml.safe_dump(raw, allow_unicode=True).encode("utf-8")) > MAX_BYTES:
        raise StudioError("Das Buch ist zu groß (maximal 1 MB).")


def spell_capability(raw: dict) -> str:
    command = (
        _load_base_command(raw.get("id", "spell"), raw)
        if raw.get("operation") or raw.get("output") or raw.get("sources") or raw.get("steps") else None
    )
    has_user = command is not None and raw.get("target", {}).get("kind") != "template"
    if has_user and command.operation:
        spec = OPERATION_CATALOG.get(command.operation)
        has_user = spec is not None and bool(spec.user_choices) and command.target_spec.get("kind") in spec.allowed_target_kinds
    # Multi-source queries (item 4/5) and Combined Spells' `steps` (both
    # USER-tier only, no per-command Expert override for either - see
    # core/spellbook_engine.py's _load_command) have no expert_only
    # concept of their own - a successfully-parsed sources/steps command
    # is always USER-tier, same as command.operation being None (falls
    # through has_user's default True) already implies above.
    has_expert = "expert" in raw or raw.get("target", {}).get("kind") == "template"
    return "USER + EXPERT" if has_user and has_expert else "EXPERT ONLY" if has_expert else "USER"


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
    if command.operation == "entity.get_state":
        for domain, fields in QUERY_RESPONSE_FIELDS_BY_DOMAIN.items():
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
    return result


def static_report(raw: dict) -> dict:
    rows, triggers = [], []
    checker = ActionExecutor(None, None)
    for spell in raw["commands"]:
        command = _load_command(spell["id"], spell)
        errors, warnings = [], []
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
                captures = {name: "test" for segments in candidate.compiled.values() for kind, name in segments if kind == "ph"}
                from dataclasses import replace as _replace
                plan = build_step_plan(_replace(candidate, expert_command=None), captures, "de")
                if plan is None:
                    errors.append(f"{variant}: Schritte oder Ziel ungültig.")
                elif any(
                    isinstance(step, ActionStep) and not checker.validate_shape(
                        ActionDraft(operation=step.operation, target=step.target, arguments=step.arguments)
                    )
                    for step in plan.steps
                ):
                    errors.append(f"{variant}: Schritt-Aktion, Ziel oder Parameter ungültig.")
                continue
            if candidate.operation is None:
                warnings.append("Reine Textausgabe: keine typisierte HASE-Aktion; Ausführung hängt vom Client ab.")
                continue
            captures = {name: "test" for segments in candidate.compiled.values() for kind, name in segments if kind == "ph"}
            for value in candidate.arguments_spec.values():
                if isinstance(value, dict) and value.get("capture"):
                    if value.get("type") in ("int", "float"):
                        minimum = value.get("min", 0)
                        maximum = value.get("max", 100)
                        sample = 50 if minimum <= 50 <= maximum else minimum
                        captures[value["capture"]] = str(sample)
                        continue
                    choices = value.get("true_values") or value.get("false_values") or []
                    if choices:
                        captures[value["capture"]] = choices[0]
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
                    warnings.append(f"Sehr ähnlicher Trigger mit {sid}.")
            triggers.append((spell["id"], normalized))
        rows.append({"id": spell["id"], "capability": spell_capability(spell),
                     "valid": not errors, "errors": errors, "warnings": warnings,
                     "enabled": spell.get("enabled", True)})
    return {"capability": book_capability(raw), "spells": rows,
            "errors": sum(len(r["errors"]) for r in rows),
            "warnings": sum(len(r["warnings"]) for r in rows)}
