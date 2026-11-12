"""Shared HASE schema, loader and neutral/typed matching views."""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from string import Formatter
from typing import Any

import yaml

from .book_languages import default_language
from .book_languages import languages as book_languages
from .book_languages import text as resolve_localized
from .action_semantics import (
    ActionVariant,
    describe_variant,
    draft_action_variants,
    safety_opposite_variants,
)
from .matching import (
    Segment,
    compile_trigger,
    literal_word_count,
    match_segments,
    outputs_are_opposite,
    tokenize_with_spans,
)
from .operations import OPERATION_CATALOG
from .models import (
    DEFAULT_ASSIST_REDIRECT,
    DEFAULT_OUTPUT_MODE,
    OUTPUT_MODES,
    QUERY_RESPONSE_FIELDS,
    QUERY_RESPONSE_FIELDS_BY_DOMAIN,
    ActionDraft,
    ActionStep,
    ActionTarget,
    AmbiguousOption,
    EngineAmbiguous,
    EngineMatch,
    EngineNoMatch,
    EngineResult,
    QueryPlan,
    QuerySourceSpec,
    QueryStep,
    ResidualAlternative,
    StepPlan,
)
from .step_variables import action_step_fields

_LOGGER = logging.getLogger(__name__)


def _numeric_capture_names(arguments_spec: Any, steps: Any = None) -> frozenset[str]:
    """Every capture name declared `type: "int"`/`type: "float"` across a
    command's flat `arguments` spec and (for a Combined Spell) each
    action step's own `arguments` - the one place this is computed, fed
    to both compile_trigger (load time, decides whether an adjacent-
    placeholder pair is admissible) and match_segments (match time,
    decides which placeholder claims the digit run). Works on both raw,
    not-yet-parsed dicts (`_load_base_command`'s own `arguments_spec`/
    `steps_raw`) and already-parsed EngineCommand fields
    (`command.arguments_spec`/`command.steps`) - both shapes expose a
    plain `{"capture": ..., "type": ...}` dict per declared argument."""
    names: set[str] = set()

    def scan(spec: Any) -> None:
        if not isinstance(spec, dict):
            return
        for value in spec.values():
            if isinstance(value, dict) and value.get("type") in ("int", "float") and isinstance(value.get("capture"), str):
                names.add(value["capture"])

    scan(arguments_spec)
    for step in steps or ():
        if isinstance(step, dict):
            scan(step.get("arguments"))
    return frozenset(names)


def _vocabulary_capture_words(arguments_spec: Any, steps: Any = None) -> dict[str, frozenset[str]]:
    """Every capture declared with a `value_map`/`true_values`/
    `false_values` conversion, mapped to its lowercased set of valid
    spoken words - a CLOSED, known vocabulary, so (like a numeric
    capture) it's recognizable by shape alone and can sit directly next
    to another placeholder with no literal boundary between them. Same
    call-site pattern as _numeric_capture_names (compile time and match
    time, raw or already-parsed shapes) - the two are siblings, not a
    single merged concept, because match_segments resolves them with
    different rules (a digit-token run vs. an exact word/phrase lookup)."""
    words: dict[str, set[str]] = {}

    def scan(spec: Any) -> None:
        if not isinstance(spec, dict):
            return
        for value in spec.values():
            if not isinstance(value, dict) or not isinstance(value.get("capture"), str):
                continue
            vocabulary: set[str] = set()
            if isinstance(value.get("value_map"), dict):
                vocabulary.update(k.lower() for k in value["value_map"] if isinstance(k, str))
            for key in ("true_values", "false_values"):
                if isinstance(value.get(key), list):
                    vocabulary.update(v.lower() for v in value[key] if isinstance(v, str))
            if vocabulary:
                words.setdefault(value["capture"], set()).update(vocabulary)

    scan(arguments_spec)
    for step in steps or ():
        if isinstance(step, dict):
            scan(step.get("arguments"))
    return {name: frozenset(vocab) for name, vocab in words.items()}


def _fail(diagnostics: list[dict[str, Any]] | None, code: str, path: str, message: str, *args: Any) -> None:
    """Log-and-return-None, exactly as every loader failure already did,
    plus (only when a caller opted in with a `diagnostics` list) append a
    structured {code, path, message} record. This is the single point
    that turns the loader's existing fail-closed checks into the
    structured-diagnostics source of truth for per-wizard-step
    validation (core/studio_validation.py) - no rule is ever
    re-implemented anywhere else, only observed here. `diagnostics=None`
    (every pre-existing call site) is byte-for-byte today's behavior."""
    _LOGGER.warning(message, *args)
    if diagnostics is not None:
        diagnostics.append({"code": code, "path": path, "message": (message % args) if args else message})
    return None

_VALID_TARGET_KINDS = {
    "area_name", "area_id", "entity_name", "entity_id", "entity_ids", "template", "all_allowed", "none", "source_area",
}
# all_allowed/none/source_area name nothing - the other four MUST have
# either a capture (something spoken fills it in) or a fixed value.
_TARGET_KINDS_REQUIRING_CAPTURE_OR_VALUE = {"area_name", "area_id", "entity_name", "entity_id"}

# Multi-source query spells (item 4/5) - a `sources` list, mutually
# exclusive with a top-level `operation`. state/attribute sources resolve
# to exactly one entity (area+domain_filter+device_class is expected to
# narrow to one, same as a spoken entity_name); aggregations resolve to a
# SET. "entity_ids"/"template"/"none" are deliberately not offered for
# sources - the Wizard's whole point here is a normal user picking
# area+domain+aggregation, not writing a template.
# "names"/"states" (matched entities' friendly names / individual state
# strings) are additional aggregate kinds - usable both here and inside a
# Combined Spell step's own `sources`, since they're plain new
# QuerySourceSpec kinds, not a steps-only concept.
_QUERY_SOURCE_KINDS = {"state", "attribute", "count", "min", "max", "avg", "sum", "names", "states"}
_AGGREGATE_SOURCE_KINDS = {"count", "min", "max", "avg", "sum", "names", "states"}
_SINGLE_SOURCE_TARGET_KINDS = {"area_name", "area_id", "entity_name", "entity_id", "source_area"}
_AGGREGATE_SOURCE_TARGET_KINDS = {"area_name", "area_id", "all_allowed", "source_area"}
_MAX_SOURCES_PER_COMMAND = 12
# Combined Spells (docs/SPELLBOOK-STUDIO-CAPABILITY-EXPANSION.md's deferred
# "Multi-Step Aktionen") - a `steps` list, mutually exclusive with both a
# top-level `operation` and a top-level `sources` list. Each entry is
# either a single action (operation+target+arguments) or a query (its own
# `sources` list, same shape as the flat field above) - see _parse_steps.
_MAX_STEPS_PER_COMMAND = 12

# How close two same-tier candidates' scores must be to treat an
# opposite-outcome tie as genuinely ambiguous rather than trusting the
# higher score. The established empirical margin is retained.
_AMBIGUITY_SCORE_EPSILON = 0.05


def _valid_response_field_name(name: str) -> bool:
    """A response placeholder's field name is either a bare identifier
    (unchanged, existing behavior - trigger captures, entity.get_state's
    fixed vocabulary, a flat `sources:` variable) or exactly one
    `alias.field` pair (Combined Spells' namespaced step outputs) where
    both halves are identifiers - chained/nested dots (`a.b.c`) are
    naturally rejected because the field half would itself contain a dot
    and fail its own .isidentifier() check."""
    if name.isidentifier():
        return True
    alias, sep, field = name.partition(".")
    return bool(sep) and alias.isidentifier() and bool(field) and field.isidentifier()


def _valid_localized(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, dict) and bool(value) and all(
        isinstance(lang, str) and bool(lang.strip()) and isinstance(text, str) and bool(text.strip())
        for lang, text in value.items()
    )


@dataclass(frozen=True)
class EngineCommand:
    spell_id: str
    trigger: str | dict[str, str]
    # A command has a typed operation, a multi-source `sources` query, a
    # free-text output, or some combination of output with the other two -
    # operation and sources are mutually exclusive with each other, though
    # (see _load_base_command's mutual-exclusivity check).
    operation: str | None
    target_spec: dict[str, Any]
    arguments_spec: dict[str, Any]
    catchphrase: str | dict[str, str] | None
    compiled: dict[str, list[Segment]]  # resolved trigger text (per language) -> segments
    sort_key: int
    output: str | dict[str, str] | None = None
    expert_command: EngineCommand | None = None
    expert_only: bool = False
    enabled: bool = True
    aliases: tuple[str, ...] | dict[str, list[str]] = ()
    default_language: str | None = None
    # Raw per-source dicts (see _parse_sources) - resolved into a QueryPlan
    # by build_query_plan once captures are known, same two-stage pattern
    # as target_spec/arguments_spec -> ActionDraft via build_draft.
    sources: tuple[dict[str, Any], ...] = ()
    output_mode: str = DEFAULT_OUTPUT_MODE
    # Raw per-step dicts (see _parse_steps) - resolved into a StepPlan by
    # build_step_plan once captures are known, same two-stage pattern as
    # target_spec/arguments_spec -> ActionDraft and sources -> QueryPlan.
    steps: tuple[dict[str, Any], ...] = ()
    # ONLY meaningful for a text-only command (operation/sources/steps all
    # absent) - see models.DEFAULT_ASSIST_REDIRECT's own docstring for
    # what this execution kind means, how it differs from output_mode,
    # and why it is never inferred from placeholder names. Present
    # unconditionally (like output_mode) rather than only on text-only
    # commands, so every EngineCommand has one uniform shape.
    assist_redirect: bool = DEFAULT_ASSIST_REDIRECT
    description: str | dict[str, str] | None = None
    # Optional spell-specific wording for a FAILED execution (e.g. "Die
    # Wohnzimmerlampe reagiert gerade nicht."), localized like `output`.
    # Absent: the fixed sentences of executor.response_text_for_failure.
    # May only use the trigger's own placeholders - there are no response
    # values after a failure.
    failure_text: str | dict[str, str] | None = None


@dataclass(frozen=True)
class EngineSpellbook:
    id: str
    name: str
    commands: tuple[EngineCommand, ...]
    disabled: bool = False
    # The book's own declared+detected language codes (book_languages.
    # languages(raw), computed once at load time) - the SAME set Spellbook
    # Studio's own language management already reads/writes, never a
    # second per-frontend list. Used by the MCP Bridge config flow to
    # offer only languages this book actually has, rather than a free-text
    # field with no relation to what the book contains.
    languages: tuple[str, ...] = ()


class _MissingField:
    """Returned by _KeepMissingFields.__missing__ for a name not yet known
    at this early capture-substitution stage (e.g. a Combined Spell's
    {alias.field} response variable, only filled in after execution).
    Python's format machinery does getattr(mapping[key], "field") for a
    dotted {key.field} placeholder - the plain string _KeepMissingFields
    used to return broke exactly this case with an AttributeError (caught
    by the caller's bare `except`), which meant the WHOLE template's
    substitution silently failed, not just the missing field. This proxy
    chains attribute access instead and reconstructs the original
    "{path}" text on stringification, so two-stage substitution keeps
    working for dotted names exactly as it already does for bare ones."""

    def __init__(self, path: str) -> None:
        self._path = path

    def __getattr__(self, name: str) -> _MissingField:
        return _MissingField(f"{self._path}.{name}")

    def __format__(self, spec: str) -> str:
        return "{" + self._path + "}"

    def __str__(self) -> str:
        return "{" + self._path + "}"


class _KeepMissingFields(dict):
    """Format known captures while leaving runtime response fields intact."""

    def __missing__(self, key):
        return _MissingField(key)


def _resolve_target(spec: dict[str, Any], captures: dict[str, str]) -> ActionTarget | None:
    kind = spec.get("kind")
    if kind not in _VALID_TARGET_KINDS:
        return None
    if kind in ("all_allowed", "none", "source_area"):
        return ActionTarget(kind=kind, value=None)
    if kind == "entity_ids":
        value = spec.get("value")
        if isinstance(value, list) and value and all(isinstance(v, str) and v.strip() for v in value):
            return ActionTarget(kind=kind, value=tuple(value))
        return None
    if kind == "template":
        value = spec.get("value")
        return ActionTarget(kind=kind, value=value) if isinstance(value, str) and value.strip() else None
    if "capture" in spec:
        capture_name = spec["capture"]
        if not isinstance(capture_name, str):
            return None
        value = captures.get(capture_name)
        if not value:
            if kind == "area_name" and spec.get("fallback_source_area") is True:
                return ActionTarget(kind="source_area", value=None)
            return None
        return ActionTarget(kind=kind, value=value)
    if "value" in spec and isinstance(spec["value"], str) and spec["value"]:
        return ActionTarget(kind=kind, value=spec["value"])
    return None


def _resolve_arguments(spec: dict[str, Any], captures: dict[str, str]) -> dict[str, Any] | None:
    """Fill an arguments template from captures.

    Captured arguments support boolean word mappings, bounded integers/
    floats, and multi-value word mappings (value_map - e.g. a spoken
    color word to Home Assistant's color_name).

    There is deliberately no "this capture is optional, skip it if
    absent" mode here: every alias in a command's `triggers:` list is
    validated independently against the SAME arguments_spec (see
    _load_command, which re-runs _load_base_command per alias with only
    `trigger` swapped) - a placeholder referenced by arguments_spec must
    therefore appear in EVERY phrasing that command can match, not just
    some. A spell needing "color only" / "brightness only" / "color and
    brightness" phrasings is three separate commands sharing the same
    operation (each with the argument subset ITS OWN trigger actually
    captures), not one command with an optional argument - core/
    operations.py's OperationSpec.optional_argument_types is what makes
    all three of those distinct argument shapes valid for that one
    operation/HA call.
    """
    resolved: dict[str, Any] = {}
    for key, value_spec in spec.items():
        if not isinstance(value_spec, dict):
            resolved[key] = value_spec
            continue
        if "capture" not in value_spec:
            resolved[key] = value_spec
            continue

        capture_name = value_spec["capture"]
        if not isinstance(capture_name, str):
            return None
        captured_raw = (captures.get(capture_name) or "").strip()
        if not captured_raw:
            return None

        conversion_type = value_spec.get("type")
        if conversion_type == "str":
            resolved[key] = captured_raw
            continue
        if conversion_type in ("int", "float"):
            try:
                converted = int(captured_raw, 10) if conversion_type == "int" else float(captured_raw)
            except ValueError:
                return None
            minimum = value_spec.get("min")
            maximum = value_spec.get("max")
            if minimum is not None and converted < minimum:
                return None
            if maximum is not None and converted > maximum:
                return None
            resolved[key] = converted
            continue

        if "value_map" in value_spec:
            value_map = {k.lower(): v for k, v in value_spec["value_map"].items()}
            mapped = value_map.get(captured_raw.lower())
            if mapped is None:
                return None
            resolved[key] = mapped
            continue

        captured = captured_raw.lower()
        true_values = {v.lower() for v in value_spec.get("true_values", []) if isinstance(v, str)}
        false_values = {v.lower() for v in value_spec.get("false_values", []) if isinstance(v, str)}
        if captured in true_values:
            resolved[key] = True
        elif captured in false_values:
            resolved[key] = False
        else:
            return None
    return resolved



def _valid_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _valid_arguments_spec(spec: dict[str, Any], placeholder_names: set[str]) -> bool:
    """Load-time validation for captured argument conversion specs."""
    for value_spec in spec.values():
        if not isinstance(value_spec, dict) or "capture" not in value_spec:
            continue
        if not isinstance(value_spec["capture"], str) or value_spec["capture"] not in placeholder_names:
            return False

        conversion_type = value_spec.get("type")
        if conversion_type is not None:
            if conversion_type not in ("int", "float", "str"):
                return False
            if conversion_type == "str":
                # Plain string pass-through: captured text is used verbatim.
                # No min/max, no boolean, no value_map checks apply.
                continue
            if "true_values" in value_spec or "false_values" in value_spec:
                return False
            minimum = value_spec.get("min")
            maximum = value_spec.get("max")
            # "int" keeps its original strict-int bound check unchanged;
            # "float" (climate.set_temperature, number.set_value,
            # input_number.set_value's own user_choices in
            # core/operations.py all declare "min"/"max" as plain Python
            # ints, e.g. min=5/max=35 for a float capture) accepts either
            # - this was the actual missing piece: _resolve_arguments
            # below never had a "float" branch at all, so every spell
            # built from one of those three operations' Wizard presets
            # failed to even LOAD (rejected right here), not just to
            # match - found via direct repro, not just static reading.
            if conversion_type == "int":
                if minimum is not None and (not isinstance(minimum, int) or isinstance(minimum, bool)):
                    return False
                if maximum is not None and (not isinstance(maximum, int) or isinstance(maximum, bool)):
                    return False
            else:
                if minimum is not None and (not isinstance(minimum, (int, float)) or isinstance(minimum, bool)):
                    return False
                if maximum is not None and (not isinstance(maximum, (int, float)) or isinstance(maximum, bool)):
                    return False
            if minimum is not None and maximum is not None and minimum > maximum:
                return False
            continue

        if "value_map" in value_spec:
            value_map = value_spec["value_map"]
            if "true_values" in value_spec or "false_values" in value_spec:
                return False
            if not isinstance(value_map, dict) or not value_map or any(
                not isinstance(word, str) or not word.strip() or not isinstance(mapped, str) or not mapped.strip()
                for word, mapped in value_map.items()
            ):
                return False
            continue

        if "true_values" in value_spec and not _valid_string_list(value_spec["true_values"]):
            return False
        if "false_values" in value_spec and not _valid_string_list(value_spec["false_values"]):
            return False
        if not value_spec.get("true_values") and not value_spec.get("false_values"):
            return False
    return True


def _parse_sources(
    spell_id: str, raw_sources: Any, placeholder_names: set[str],
    *, diagnostics: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], ...] | None:
    """Load-time parse+validate for a multi-source query spell's `sources`
    list (item 4/5). Returns raw per-source dicts (mirroring how
    target_spec/arguments_spec stay raw dicts on EngineCommand, resolved
    into concrete types only once captures are known - see build_query_plan)
    or None on any validation failure, same fail-closed convention as every
    other loader check in this module."""
    if not isinstance(raw_sources, list) or not raw_sources or len(raw_sources) > _MAX_SOURCES_PER_COMMAND:
        return _fail(
            diagnostics, "invalid_sources_list", "sources",
            "spellbook engine: command %s has an invalid sources list (empty, not a list, or over %d "
            "entries), skipping", spell_id, _MAX_SOURCES_PER_COMMAND,
        )

    parsed: list[dict[str, Any]] = []
    seen_variables: set[str] = set()
    for i, raw_source in enumerate(raw_sources):
        if not isinstance(raw_source, dict):
            return _fail(
                diagnostics, "invalid_source", f"sources[{i}]",
                "spellbook engine: command %s's sources[%d] is not a mapping, skipping", spell_id, i,
            )

        kind = raw_source.get("kind")
        if kind not in _QUERY_SOURCE_KINDS:
            return _fail(
                diagnostics, "invalid_source_kind", f"sources[{i}].kind",
                "spellbook engine: command %s's sources[%d] has an invalid kind %r, skipping", spell_id, i, kind,
            )
        is_aggregate = kind in _AGGREGATE_SOURCE_KINDS

        target_spec = raw_source.get("target")
        allowed_target_kinds = _AGGREGATE_SOURCE_TARGET_KINDS if is_aggregate else _SINGLE_SOURCE_TARGET_KINDS
        if not isinstance(target_spec, dict) or target_spec.get("kind") not in allowed_target_kinds:
            return _fail(
                diagnostics, "invalid_source_target", f"sources[{i}].target",
                "spellbook engine: command %s's sources[%d] (kind=%s) has an invalid target, skipping",
                spell_id, i, kind,
            )
        if "capture" in target_spec and (
            not isinstance(target_spec["capture"], str) or target_spec["capture"] not in placeholder_names
        ):
            return _fail(
                diagnostics, "unknown_capture", f"sources[{i}].target.capture",
                "spellbook engine: command %s's sources[%d] target references an unknown capture, skipping",
                spell_id, i,
            )
        if (
            target_spec["kind"] in _TARGET_KINDS_REQUIRING_CAPTURE_OR_VALUE
            and "capture" not in target_spec
            and not (isinstance(target_spec.get("value"), str) and target_spec["value"].strip())
        ):
            return _fail(
                diagnostics, "target_missing_capture_or_value", f"sources[{i}].target",
                "spellbook engine: command %s's sources[%d] target has neither a capture nor a value, "
                "skipping", spell_id, i,
            )

        domain_filter = raw_source.get("domain_filter")
        if domain_filter is not None and (not isinstance(domain_filter, str) or not domain_filter.strip()):
            return _fail(
                diagnostics, "invalid_domain_filter", f"sources[{i}].domain_filter",
                "spellbook engine: command %s's sources[%d] has an invalid domain_filter, skipping", spell_id, i,
            )
        device_class = raw_source.get("device_class")
        if device_class is not None and (not isinstance(device_class, str) or not device_class.strip()):
            return _fail(
                diagnostics, "invalid_device_class", f"sources[{i}].device_class",
                "spellbook engine: command %s's sources[%d] has an invalid device_class, skipping", spell_id, i,
            )

        attribute = raw_source.get("attribute")
        if kind == "attribute":
            if not isinstance(attribute, str) or not attribute.strip():
                return _fail(
                    diagnostics, "missing_attribute", f"sources[{i}].attribute",
                    "spellbook engine: command %s's sources[%d] is kind=attribute but has no attribute "
                    "name, skipping", spell_id, i,
                )
        elif attribute is not None:
            return _fail(
                diagnostics, "unexpected_attribute", f"sources[{i}].attribute",
                "spellbook engine: command %s's sources[%d] has an attribute name but kind isn't "
                "'attribute', skipping", spell_id, i,
            )

        state_filter = raw_source.get("state_filter")
        if state_filter is not None and (
            not is_aggregate or not isinstance(state_filter, str) or not state_filter.strip()
        ):
            return _fail(
                diagnostics, "invalid_state_filter", f"sources[{i}].state_filter",
                "spellbook engine: command %s's sources[%d] has a state_filter but isn't an aggregation, "
                "or it isn't a non-empty string, skipping", spell_id, i,
            )

        # Reserved against the trigger's OWN capture names (e.g. a source
        # named "area" would shadow the {area} placeholder used to resolve
        # its own or another source's target) - NOT against
        # QUERY_RESPONSE_FIELDS, which belongs to the entity.get_state path
        # only. A command is either operation-based (entity.get_state) or
        # sources-based, mutually exclusive, so there's no actual
        # collision risk between a source variable and that vocabulary -
        # blocking names like "temperature"/"humidity" would only get in
        # the way of exactly the natural names a multi-source query wants.
        variable = raw_source.get("variable")
        if (
            not isinstance(variable, str) or not variable.isidentifier()
            or variable in placeholder_names or variable in seen_variables
        ):
            return _fail(
                diagnostics, "invalid_source_variable", f"sources[{i}].variable",
                "spellbook engine: command %s's sources[%d] has an invalid, reserved or duplicate "
                "variable name, skipping", spell_id, i,
            )
        seen_variables.add(variable)

        unit_variable = raw_source.get("unit_variable")
        if unit_variable is not None:
            if (
                kind != "state" or not isinstance(unit_variable, str) or not unit_variable.isidentifier()
                or unit_variable in placeholder_names or unit_variable in seen_variables
            ):
                return _fail(
                    diagnostics, "invalid_source_unit_variable", f"sources[{i}].unit_variable",
                    "spellbook engine: command %s's sources[%d] has an invalid, reserved or duplicate "
                    "unit_variable, skipping", spell_id, i,
                )
            seen_variables.add(unit_variable)

        parsed.append({
            "kind": kind, "target": target_spec, "domain_filter": domain_filter,
            "device_class": device_class, "attribute": attribute,
            "state_filter": state_filter, "variable": variable, "unit_variable": unit_variable,
        })
    return tuple(parsed)


def _parse_action_step(
    spell_id: str, index: int, raw_step: dict[str, Any], placeholder_names: set[str],
    *, diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Load-time parse+validate for one action-shaped `steps[]` entry -
    the exact same operation/target/arguments checks _load_base_command
    already applies to a flat `operation`+`target`+`arguments` command,
    reused verbatim rather than duplicated."""
    operation = raw_step.get("operation")
    if not isinstance(operation, str) or not operation.strip():
        return _fail(
            diagnostics, "invalid_operation", f"steps[{index}].operation",
            "spellbook engine: command %s's steps[%d] has an invalid operation, skipping", spell_id, index,
        )
    target_spec = raw_step.get("target")
    if not isinstance(target_spec, dict) or target_spec.get("kind") not in _VALID_TARGET_KINDS:
        return _fail(
            diagnostics, "invalid_target", f"steps[{index}].target",
            "spellbook engine: command %s's steps[%d] has an invalid target, skipping", spell_id, index,
        )
    if "capture" in target_spec and (
        not isinstance(target_spec["capture"], str) or target_spec["capture"] not in placeholder_names
    ):
        return _fail(
            diagnostics, "unknown_capture", f"steps[{index}].target.capture",
            "spellbook engine: command %s's steps[%d] target references an unknown capture, skipping", spell_id, index,
        )
    if (
        target_spec["kind"] in _TARGET_KINDS_REQUIRING_CAPTURE_OR_VALUE
        and "capture" not in target_spec
        and not (isinstance(target_spec.get("value"), str) and target_spec["value"].strip())
    ):
        return _fail(
            diagnostics, "target_missing_capture_or_value", f"steps[{index}].target",
            "spellbook engine: command %s's steps[%d] target has neither a capture nor a value, skipping",
            spell_id, index,
        )
    arguments_spec = raw_step.get("arguments", {})
    if not isinstance(arguments_spec, dict) or not _valid_arguments_spec(arguments_spec, placeholder_names):
        return _fail(
            diagnostics, "invalid_arguments_spec", f"steps[{index}].arguments",
            "spellbook engine: command %s's steps[%d] has invalid arguments, skipping", spell_id, index,
        )
    return {"operation": operation, "target": target_spec, "arguments": arguments_spec}


def _parse_steps(
    spell_id: str, raw_steps: Any, placeholder_names: set[str],
    *, diagnostics: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], ...] | None:
    """Load-time parse+validate for a Combined Spell's `steps` list - each
    entry is either an action (operation+target+arguments, validated by
    _parse_action_step) or a query (its own `sources` list, validated by
    the exact same _parse_sources already used for the flat `sources:`
    field). Returns raw dicts tagged with kind="action"|"query" (mirroring
    how target_spec/arguments_spec/sources stay raw on EngineCommand,
    resolved into concrete types only once captures are known - see
    build_step_plan) or None on any validation failure."""
    if not isinstance(raw_steps, list) or not raw_steps or len(raw_steps) > _MAX_STEPS_PER_COMMAND:
        return _fail(
            diagnostics, "invalid_steps_list", "steps",
            "spellbook engine: command %s has an invalid steps list (empty, not a list, or over %d "
            "entries), skipping", spell_id, _MAX_STEPS_PER_COMMAND,
        )

    parsed: list[dict[str, Any]] = []
    seen_aliases: set[str] = set()
    for i, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            return _fail(
                diagnostics, "invalid_step", f"steps[{i}]",
                "spellbook engine: command %s's steps[%d] is not a mapping, skipping", spell_id, i,
            )

        alias = raw_step.get("alias")
        if alias is not None:
            if (
                not isinstance(alias, str) or not alias.isidentifier()
                or alias in placeholder_names or alias in seen_aliases
            ):
                return _fail(
                    diagnostics, "invalid_step_alias", f"steps[{i}].alias",
                    "spellbook engine: command %s's steps[%d] has an invalid, reserved or duplicate alias, "
                    "skipping", spell_id, i,
                )
            seen_aliases.add(alias)

        has_op = isinstance(raw_step.get("operation"), str) and bool(raw_step["operation"].strip())
        has_src = isinstance(raw_step.get("sources"), list) and len(raw_step["sources"]) > 0
        if has_op == has_src:
            # Exactly one of the two is required - a step is either an
            # action or a query, never both, never neither.
            return _fail(
                diagnostics, "step_shape_ambiguous", f"steps[{i}]",
                "spellbook engine: command %s's steps[%d] must be either an action (operation) or a "
                "query (sources), skipping", spell_id, i,
            )

        if has_op:
            if set(raw_step) - {"alias", "operation", "target", "arguments"}:
                return _fail(
                    diagnostics, "unknown_step_fields", f"steps[{i}]",
                    "spellbook engine: command %s's steps[%d] has unknown fields, skipping", spell_id, i,
                )
            action = _parse_action_step(spell_id, i, raw_step, placeholder_names, diagnostics=diagnostics)
            if action is None:
                return None
            parsed.append({"alias": alias, "kind": "action", **action})
        else:
            if set(raw_step) - {"alias", "sources"}:
                return _fail(
                    diagnostics, "unknown_step_fields", f"steps[{i}]",
                    "spellbook engine: command %s's steps[%d] has unknown fields, skipping", spell_id, i,
                )
            sources = _parse_sources(
                f"{spell_id}#steps[{i}]", raw_step["sources"], placeholder_names, diagnostics=diagnostics,
            )
            if sources is None:
                return None
            parsed.append({"alias": alias, "kind": "query", "sources": sources})
    return tuple(parsed)


def _load_base_command(
    spell_id: str, raw: dict[str, Any], *, diagnostics: list[dict[str, Any]] | None = None,
) -> EngineCommand | None:
    if not isinstance(raw, dict):
        return None
    trigger = raw.get("trigger")
    operation = raw.get("operation")
    target_spec = raw.get("target")
    arguments_spec = raw.get("arguments", {})
    catchphrase = raw.get("catchphrase")
    failure_text = raw.get("failure_text")
    # Optional display metadata must never change command admission/execution.
    description = raw.get("description")
    if not _valid_localized(description):
        description = None
    output = raw.get("output")
    sources_raw = raw.get("sources")
    steps_raw = raw.get("steps")
    output_mode_raw = raw.get("output_mode")
    assist_redirect_raw = raw.get("assist_redirect")

    if not _valid_localized(trigger):
        return _fail(
            diagnostics, "missing_trigger", "trigger",
            "spellbook engine: command %s missing a trigger, skipping", spell_id,
        )
    if operation is not None and (not isinstance(operation, str) or not operation.strip()):
        return _fail(
            diagnostics, "invalid_operation", "operation",
            "spellbook engine: command %s has an operation field that isn't a non-empty string, "
            "skipping", spell_id,
        )
    has_operation = isinstance(operation, str) and bool(operation.strip())
    has_sources = isinstance(sources_raw, list) and len(sources_raw) > 0
    has_steps = isinstance(steps_raw, list) and len(steps_raw) > 0
    if sum((has_operation, has_sources, has_steps)) > 1:
        # A command is a single action (operation), a multi-source query
        # (sources) OR a Combined Spell (steps) - never more than one of
        # the three, keeps "what does this command DO" a single
        # unambiguous question, matching the same user/expert exclusivity
        # philosophy as the rest of this loader.
        return _fail(
            diagnostics, "multiple_command_forms", "",
            "spellbook engine: command %s has more than one of operation/sources/steps - a command must "
            "be exactly one of these, skipping", spell_id,
        )
    if output_mode_raw is not None and (not isinstance(output_mode_raw, str) or output_mode_raw not in OUTPUT_MODES):
        return _fail(
            diagnostics, "invalid_output_mode", "output_mode",
            "spellbook engine: command %s has an invalid output_mode, skipping", spell_id,
        )
    output_mode_value = output_mode_raw or DEFAULT_OUTPUT_MODE
    if assist_redirect_raw is not None and not isinstance(assist_redirect_raw, bool):
        return _fail(
            diagnostics, "invalid_assist_redirect", "assist_redirect",
            "spellbook engine: command %s has a non-boolean assist_redirect, skipping", spell_id,
        )
    assist_redirect_value = DEFAULT_ASSIST_REDIRECT if assist_redirect_raw is None else assist_redirect_raw
    if output is not None and not _valid_localized(output):
        return _fail(
            diagnostics, "invalid_output", "output",
            "spellbook engine: command %s has an invalid output, skipping", spell_id,
        )
    if not has_operation and not has_sources and not has_steps and output is None:
        # Etappe 7: a command needs a typed form (operation), a
        # multi-source query form (sources), a Combined Spell form
        # (steps), a free-text form for some other caller to use (output,
        # this engine never acts on it itself), or some combination -
        # none of the four means nothing in the entire system could ever
        # do anything with it.
        return _fail(
            diagnostics, "empty_command", "",
            "spellbook engine: command %s has none of operation, sources, steps or output, skipping", spell_id,
        )
    if has_operation and not isinstance(target_spec, dict):
        return _fail(
            diagnostics, "invalid_target", "target",
            "spellbook engine: command %s has an invalid target, skipping", spell_id,
        )
    if has_operation and target_spec.get("kind") not in _VALID_TARGET_KINDS:
        return _fail(
            diagnostics, "invalid_target", "target.kind",
            "spellbook engine: command %s has an invalid target, skipping", spell_id,
        )
    if has_operation and not isinstance(arguments_spec, dict):
        return _fail(
            diagnostics, "invalid_arguments_spec", "arguments",
            "spellbook engine: command %s has invalid arguments, skipping", spell_id,
        )
    if catchphrase is not None and not _valid_localized(catchphrase):
        return _fail(
            diagnostics, "invalid_catchphrase", "catchphrase",
            "spellbook engine: command %s has an invalid catchphrase, skipping", spell_id,
        )
    if failure_text is not None and not _valid_localized(failure_text):
        return _fail(
            diagnostics, "invalid_failure_text", "failure_text",
            "spellbook engine: command %s has an invalid failure_text, skipping", spell_id,
        )

    trigger_variants = list(trigger.values()) if isinstance(trigger, dict) else [trigger]
    numeric_placeholders = _numeric_capture_names(arguments_spec, steps_raw)
    vocabulary_placeholders = _vocabulary_capture_words(arguments_spec, steps_raw)
    disambiguable_placeholders = numeric_placeholders | vocabulary_placeholders.keys()
    compiled: dict[str, list[Segment]] = {}
    for variant in trigger_variants:
        segments = compile_trigger(variant, disambiguable_placeholders)
        if segments is None:
            return _fail(
                diagnostics, "adjacent_placeholders", "trigger",
                "spellbook engine: command %s's trigger %r has two adjacent placeholders, skipping",
                spell_id, variant,
            )
        compiled[variant] = segments

    # A lightweight pre-extraction (not yet full validation - see
    # _parse_sources for that) of declared variable names, needed here only
    # to widen the response-placeholder allowlist below before sources are
    # actually parsed. Mirrors QUERY_RESPONSE_FIELDS' role for
    # entity.get_state responses.
    declared_variable_names = frozenset(
        name
        for source in (sources_raw or [])
        if isinstance(source, dict)
        for name in (source.get("variable"), source.get("unit_variable"))
        if isinstance(name, str) and name.isidentifier()
    ) if has_sources else frozenset()

    # Same lightweight pre-extraction as declared_variable_names above -
    # needed only to widen the response-placeholder allowlist below before
    # steps are actually parsed by _parse_steps. Reads the raw per-step
    # dicts directly rather than calling _parse_steps twice.
    declared_step_field_names: frozenset[str] = frozenset()
    if has_steps:
        names: set[str] = set()
        for raw_step in steps_raw:
            if not isinstance(raw_step, dict):
                continue
            alias = raw_step.get("alias")
            if not isinstance(alias, str) or not alias.isidentifier():
                continue
            step_operation = raw_step.get("operation")
            if isinstance(step_operation, str) and step_operation.strip():
                step_target = raw_step.get("target")
                target_kind = step_target.get("kind") if isinstance(step_target, dict) else None
                if isinstance(target_kind, str):
                    for field_name in action_step_fields(step_operation, target_kind):
                        names.add(f"{alias}.{field_name}")
            elif isinstance(raw_step.get("sources"), list):
                for source in raw_step["sources"]:
                    if not isinstance(source, dict):
                        continue
                    for field_name in (source.get("variable"), source.get("unit_variable")):
                        if isinstance(field_name, str) and field_name.isidentifier():
                            names.add(f"{alias}.{field_name}")
        declared_step_field_names = frozenset(names)

    languages = {"en", "de"}
    for localized in (trigger, output, catchphrase, failure_text):
        if isinstance(localized, dict):
            languages.update(localized)
    for language in languages:
        trigger_text = resolve_localized(trigger, language)
        if trigger_text is None:
            continue
        placeholders = {name for kind, name in compiled[trigger_text] if kind == "ph"}
        for template, is_response in (
            (trigger_text, False),
            (resolve_localized(output, language), True),
            (resolve_localized(catchphrase, language), True),
        ):
            if template is None:
                continue
            # Operation-specific query response fields are declared on the
            # OperationSpec directly (e.g. todo.list_items → items, count).
            # Fall back to QUERY_RESPONSE_FIELDS_BY_DOMAIN for any domain
            # not yet migrated to per-operation response_fields.
            if is_response and has_operation and operation:
                _op_spec = OPERATION_CATALOG.get(operation)
                if _op_spec is not None and _op_spec.response_fields:
                    op_response_fields = frozenset(
                        name
                        for fields in _op_spec.response_fields.values()
                        for name in fields
                    )
                else:
                    op_domain = operation.split(".")[0] if "." in operation else ""
                    op_response_fields = frozenset(QUERY_RESPONSE_FIELDS_BY_DOMAIN.get(op_domain, ()))
            else:
                op_response_fields = frozenset()
            allowed = placeholders | (
                QUERY_RESPONSE_FIELDS
                if is_response and operation == "entity.get_state"
                else frozenset()
            ) | op_response_fields | (
                declared_variable_names if is_response and has_sources else frozenset()
            ) | (declared_step_field_names if is_response and has_steps else frozenset())
            try:
                for _, name, spec, conversion in Formatter().parse(template):
                    if name is not None and (
                        not _valid_response_field_name(name) or spec or conversion or name not in allowed
                    ):
                        return _fail(
                            diagnostics,
                            "unknown_response_placeholder" if is_response else "unknown_trigger_placeholder",
                            "output" if is_response else "trigger",
                            "spellbook engine: command %s's %s %r references unknown placeholder %r, skipping",
                            spell_id, "output/catchphrase" if is_response else "trigger", template, name,
                        )
            except ValueError:
                return _fail(
                    diagnostics,
                    "malformed_placeholder_syntax", "output" if is_response else "trigger",
                    "spellbook engine: command %s's %s %r has malformed placeholder syntax, skipping",
                    spell_id, "output/catchphrase" if is_response else "trigger", template,
                )

    if failure_text is not None:
        # A failure has no response values, so only the trigger's own
        # captures (union over every trigger variant) may appear here.
        trigger_placeholders = {
            name for segments in compiled.values() for kind, name in segments if kind == "ph"
        }
        for language in languages:
            template = resolve_localized(failure_text, language)
            if template is None:
                continue
            try:
                for _, name, spec, conversion in Formatter().parse(template):
                    if name is not None and (spec or conversion or name not in trigger_placeholders):
                        return _fail(
                            diagnostics, "unknown_failure_text_placeholder", "failure_text",
                            "spellbook engine: command %s's failure_text %r references unknown placeholder %r, skipping",
                            spell_id, template, name,
                        )
            except ValueError:
                return _fail(
                    diagnostics, "malformed_placeholder_syntax", "failure_text",
                    "spellbook engine: command %s's failure_text %r has malformed placeholder syntax, skipping",
                    spell_id, template,
                )

    if has_sources:
        placeholder_names = {
            name for segments in compiled.values() for kind, name in segments if kind == "ph"
        }
        parsed_sources = _parse_sources(spell_id, sources_raw, placeholder_names, diagnostics=diagnostics)
        if parsed_sources is None:
            return None
        reference_trigger = resolve_localized(trigger, "en") or trigger_variants[0]
        return EngineCommand(
            spell_id=spell_id,
            trigger=trigger,
            operation=None,
            target_spec={},
            arguments_spec={},
            catchphrase=catchphrase,
            failure_text=failure_text,
            compiled=compiled,
            sort_key=literal_word_count(compiled[reference_trigger]),
            output=output,
            sources=parsed_sources,
            description=description,
            output_mode=output_mode_value,
        )

    if has_steps:
        placeholder_names = {
            name for segments in compiled.values() for kind, name in segments if kind == "ph"
        }
        parsed_steps = _parse_steps(spell_id, steps_raw, placeholder_names, diagnostics=diagnostics)
        if parsed_steps is None:
            return None
        reference_trigger = resolve_localized(trigger, "en") or trigger_variants[0]
        return EngineCommand(
            spell_id=spell_id,
            trigger=trigger,
            operation=None,
            target_spec={},
            arguments_spec={},
            catchphrase=catchphrase,
            failure_text=failure_text,
            compiled=compiled,
            sort_key=literal_word_count(compiled[reference_trigger]),
            output=output,
            steps=parsed_steps,
            description=description,
            output_mode=output_mode_value,
        )

    if not has_operation:
        # Output-only: no target/arguments capture-consistency checks
        # apply (there's no target_spec/arguments_spec to check them
        # against) - this command is done loading. assist_redirect is
        # only meaningful here (see models.DEFAULT_ASSIST_REDIRECT).
        reference_trigger = resolve_localized(trigger, "en") or trigger_variants[0]
        return EngineCommand(
            spell_id=spell_id,
            trigger=trigger,
            operation=None,
            target_spec={},
            arguments_spec={},
            catchphrase=catchphrase,
            failure_text=failure_text,
            compiled=compiled,
            sort_key=literal_word_count(compiled[reference_trigger]),
            output=output,
            output_mode=output_mode_value,
            assist_redirect=assist_redirect_value,
            description=description,
        )

    # Union across every language variant - a capture used by only one
    # variant's trigger is still legitimate (see resolve_localized's
    # own "a language gap never invalidates the entry" rule).
    placeholder_names = {
        name for segments in compiled.values() for kind, name in segments if kind == "ph"
    }
    if "capture" in target_spec and (
        not isinstance(target_spec["capture"], str) or target_spec["capture"] not in placeholder_names
    ):
        return _fail(
            diagnostics, "unknown_capture", "target.capture",
            "spellbook engine: command %s's target references capture %r, which no trigger variant "
            "declares - this command could never resolve a target, skipping",
            spell_id, target_spec.get("capture"),
        )
    if (
        target_spec["kind"] in _TARGET_KINDS_REQUIRING_CAPTURE_OR_VALUE
        and "capture" not in target_spec
        and not (isinstance(target_spec.get("value"), str) and target_spec["value"].strip())
    ):
        # Neither a capture (something spoken to fill it in) nor a
        # fixed value - this target could never resolve to anything,
        # for ANY input, ever. Silently un-matchable (EngineNoMatch on
        # every attempt) rather than a load error is exactly the
        # earlier dangling-capture bug's sibling: same consequence,
        # different way to write a target with nothing behind it.
        return _fail(
            diagnostics, "target_missing_capture_or_value", "target",
            "spellbook engine: command %s's target has neither a capture nor a value - "
            "it could never resolve to anything, skipping",
            spell_id,
        )
    if not _valid_arguments_spec(arguments_spec, placeholder_names):
        return _fail(
            diagnostics, "invalid_arguments_spec", "arguments",
            "spellbook engine: command %s has an invalid arguments spec "
            "(dangling capture reference, or true_values/false_values not a list of strings), skipping",
            spell_id,
        )

    reference_trigger = resolve_localized(trigger, "en") or trigger_variants[0]
    return EngineCommand(
        spell_id=spell_id,
        trigger=trigger,
        operation=operation,
        description=description,
        target_spec=target_spec,
        arguments_spec=arguments_spec,
        catchphrase=catchphrase,
        failure_text=failure_text,
        compiled=compiled,
        sort_key=literal_word_count(compiled[reference_trigger]),
        output=output,
        output_mode=output_mode_value,
    )


def _load_command(
    spell_id: str, raw: dict[str, Any], *, diagnostics: list[dict[str, Any]] | None = None,
) -> EngineCommand | None:
    """The loader entry point every caller uses - runtime matching
    (diagnostics=None, unchanged behavior) and per-wizard-step validation
    (core/studio_validation.py, diagnostics=[...]) alike. This function
    (together with _load_base_command/_parse_sources/_parse_steps/
    _parse_action_step) is the single source of truth for trigger/
    placeholder/capture/target/argument-spec structural rules - nothing
    about them is re-implemented anywhere else."""
    if not isinstance(raw, dict):
        return None
    if raw.get("steps") and isinstance(raw.get("expert"), dict):
        # Combined Spells are USER-tier only in v1 (see core/models.py's
        # ActionStep docstring) - no separate per-command Expert override
        # layer for a `steps` command. Reject explicitly rather than
        # silently ignoring the `expert` dict an author supplied.
        return _fail(
            diagnostics, "steps_with_expert_variant", "",
            "spellbook engine: command %s declares both steps and an expert variant, which isn't "
            "supported yet, skipping", spell_id,
        )
    user = _load_base_command(spell_id, raw, diagnostics=diagnostics) if raw.get("operation") or raw.get("output") or raw.get("sources") or raw.get("steps") else None
    expert = None
    if isinstance(raw.get("expert"), dict):
        technical = {**raw, **raw["expert"]}
        technical.pop("expert", None)
        technical.pop("output", None)
        expert = _load_base_command(spell_id, technical, diagnostics=diagnostics if user is None else None)
    command = user or expert
    if command is None:
        return None
    aliases = raw.get("triggers", [])
    groups = list(aliases.values()) if isinstance(aliases, dict) else [aliases]
    if not all(isinstance(group, list) and all(isinstance(t, str) and t.strip() for t in group) for group in groups):
        return _fail(diagnostics, "invalid_aliases", "triggers", "spellbook engine: command %s has invalid triggers, skipping", spell_id)
    # Compile aliases through the existing parser, including capture validation.
    for trigger in (t for group in groups for t in group):
        if _load_base_command(
            spell_id, {**raw, **(raw.get("expert", {}) if user is None else {}), "trigger": trigger},
            diagnostics=diagnostics,
        ) is None:
            return None
    return replace(command, expert_command=expert if user else None,
                   expert_only=user is None or command.target_spec.get("kind") == "template",
                   enabled=raw.get("enabled", True) is True, aliases=aliases if isinstance(aliases, dict) else tuple(aliases))


def load_spellbook_from_dict(book_id: str, raw: dict[str, Any]) -> EngineSpellbook | None:
    """Shared by load_spellbook_from_text (parses YAML first) and
    core/legacy_migration.py (builds this shape directly from a
    converted legacy spellbook, no YAML round-trip needed)."""
    if not isinstance(raw, dict):
        return None
    if not isinstance(raw.get("disabled", False), bool):
        _LOGGER.warning("spellbook engine: spellbook %s has an invalid disabled flag", book_id)
        return None
    declared_id = raw.get("id", book_id)
    if not isinstance(declared_id, str) or not declared_id.strip():
        return None
    book_id = declared_id
    if not _valid_localized(raw.get("name")):
        return None
    default = default_language(raw)
    name = resolve_localized(raw.get("name"), "en", default, book_id)
    raw_commands = raw.get("commands")
    if not isinstance(name, str) or not name.strip() or not isinstance(raw_commands, list):
        _LOGGER.warning("spellbook engine: spellbook %s is missing name/commands, skipping", book_id)
        return None

    commands: list[EngineCommand] = []
    for i, raw_command in enumerate(raw_commands):
        if not isinstance(raw_command, dict):
            continue
        spell_id = raw_command.get("id") or f"{book_id}#{i}"
        command = _load_command(spell_id, raw_command)
        if command is not None:
            command = replace(command, default_language=default,
                expert_command=replace(command.expert_command, default_language=default) if command.expert_command else None)
            commands.append(command)

    if raw_commands and not commands:
        # Every declared command was individually rejected as invalid
        # (each already logged its own warning in _load_command) - a
        # book in this state is not a legitimate empty book (that's
        # `commands: []` from the start, still returned normally
        # below), it's a broken one. Returning it here would make it
        # indistinguishable from "no local book configured" to every
        # caller - conversation.py's own fail-closed handling for a
        # concretely configured-but-broken book (const.
        # CONF_SPELLBOOKER_BOOK set) depends on load failure actually
        # propagating as None, not as a valid-looking empty book.
        _LOGGER.warning(
            "spellbook engine: spellbook %s declared %d command(s) but none were valid - "
            "treating as a load failure, not an empty book",
            book_id, len(raw_commands),
        )
        return None

    commands.sort(key=lambda c: c.sort_key, reverse=True)
    return EngineSpellbook(id=book_id, name=name, commands=tuple(commands),
                           disabled=raw.get("disabled", False), languages=tuple(book_languages(raw)))


def load_spellbook_from_text(book_id: str, raw_yaml: str) -> EngineSpellbook | None:
    try:
        raw = yaml.safe_load(raw_yaml)
    except Exception as err:  # noqa: BLE001 - a bad file must never crash the engine
        _LOGGER.warning("spellbook engine: could not parse spellbook %s: %s", book_id, err)
        return None
    return load_spellbook_from_dict(book_id, raw)


def load_spellbook_from_path(path: Path) -> EngineSpellbook | None:
    try:
        raw_yaml = path.read_text(encoding="utf-8")
    except OSError as err:
        _LOGGER.warning("spellbook engine: could not read %s: %s", path, err)
        return None
    return load_spellbook_from_text(path.stem, raw_yaml)


def build_text_response(command: EngineCommand, captures: dict[str, str], lang: str) -> str | None:
    """The plain-text rendering for a command with none of operation/
    sources/steps - only `output` (see _load_base_command's own "needs a
    typed form, a query form, a Combined Spell form, or a free-text
    output form" rule). Mirrors find_command()'s own inline text-only
    branch exactly (same catchphrase-then-output precedence would
    over-complicate MCP's single-TextContent response model, so this
    intentionally uses ONLY `output` - the field the loader's own
    docstring calls "a free-text form for some other caller to use"; a
    device-only catchphrase has no equivalent in a tools/call response)
    and the same strict `.format(**captures)` substitution, factored out
    here so core.mcp_execution can render the identical text for an
    EXACT spell_id call - never running find_command()/match() (text/
    fuzzy matching) to get there, since an MCP call has already named the
    exact Spell."""
    if command.operation is not None or command.sources or command.steps:
        return None
    output = resolve_localized(command.output, lang, command.default_language)
    if output is None:
        return None
    try:
        return output.format(**captures)
    except (KeyError, IndexError, ValueError, AttributeError, TypeError):
        return None


def _render_failure_text(command: EngineCommand, captures: dict[str, str], lang: str) -> str | None:
    """The spell's own failure wording for this turn, captures filled in;
    None when the spell has none (callers then use the fixed sentences)."""
    text = resolve_localized(command.failure_text, lang, command.default_language)
    if not text:
        return None
    try:
        return text.format_map(_KeepMissingFields(captures))
    except (KeyError, IndexError, ValueError, AttributeError, TypeError):
        return text


def build_draft(
    command: EngineCommand, captures: dict[str, str], lang: str,
    *, diagnostics: list[dict] | None = None,
) -> ActionDraft | None:
    """``diagnostics`` is Engine-layer observation only (Explain): when a list
    is passed, the reason a variant could not be built is appended.  It never
    changes control flow or the result."""
    if command.expert_command is not None:
        fallback = build_draft(replace(command, expert_command=None), captures, lang,
                               diagnostics=diagnostics)
        mark = len(diagnostics) if diagnostics is not None else 0
        expert = build_draft(command.expert_command, captures, lang, diagnostics=diagnostics)
        if diagnostics is not None:
            for entry in diagnostics[mark:]:
                entry["variant"] = "expert"
        if expert is not None:
            return replace(expert, expert=True, user_fallback=fallback)
        return fallback
    variant = "expert" if command.expert_only else "user"
    if command.operation is None:
        # Satellite must never turn free text into an executable proposal.
        if diagnostics is not None:
            diagnostics.append({"variant": variant, "reason": "no_operation"})
        return None
    target = _resolve_target(command.target_spec, captures)
    if target is None:
        if diagnostics is not None:
            diagnostics.append({"variant": variant, "reason": "target_unresolved"})
        return None
    arguments = _resolve_arguments(command.arguments_spec, captures)
    if arguments is None:
        if diagnostics is not None:
            diagnostics.append({"variant": variant, "reason": "argument_invalid"})
        return None
    catchphrase = resolve_localized(command.catchphrase, lang, command.default_language)
    if catchphrase:
        try:
            catchphrase = catchphrase.format_map(_KeepMissingFields(captures))
        except (KeyError, IndexError, ValueError, AttributeError, TypeError):
            pass
    output = resolve_localized(command.output, lang, command.default_language)
    if output:
        try:
            output = output.format_map(_KeepMissingFields(captures))
        except (KeyError, IndexError, ValueError, AttributeError, TypeError):
            pass
    return ActionDraft(operation=command.operation, target=target, arguments=arguments,
                       catchphrase=catchphrase,
                       response_template=(catchphrase or output)
                       if command.operation == "entity.get_state" else None,
                       expert=command.expert_only or target.kind == "template",
                       output_mode=command.output_mode,
                       failure_text=_render_failure_text(command, captures, lang))


def _resolve_query_source_spec(source_spec: dict[str, Any], captures: dict[str, str]) -> QuerySourceSpec | None:
    """Resolve one raw per-source dict (from either the flat `sources:`
    field or a Combined Spell step's own `sources`) into a concrete
    QuerySourceSpec, shared by build_query_plan and build_step_plan so
    the two never duplicate this resolution."""
    target = _resolve_target(source_spec["target"], captures)
    if target is None:
        return None
    return QuerySourceSpec(
        kind=source_spec["kind"],
        target=target,
        domain_filter=source_spec.get("domain_filter"),
        device_class=source_spec.get("device_class"),
        attribute=source_spec.get("attribute"),
        state_filter=source_spec.get("state_filter"),
        variable=source_spec["variable"],
        unit_variable=source_spec.get("unit_variable"),
    )


def build_query_plan(command: EngineCommand, captures: dict[str, str], lang: str) -> QueryPlan | None:
    """The multi-source-query sibling of build_draft - resolves each raw
    source dict's target through the exact same _resolve_target captures
    are already used for, then defers actual HA reads/aggregation to
    core.executor.ActionExecutor.execute_query_plan (this module has zero
    HA imports). response_template goes through the same two-stage
    substitution as entity.get_state's: captures fill in here (via
    _KeepMissingFields, leaving declared source variables untouched),
    the resolved variable values fill in at execution time."""
    if not command.sources:
        return None
    resolved_sources = []
    for source_spec in command.sources:
        source = _resolve_query_source_spec(source_spec, captures)
        if source is None:
            return None
        resolved_sources.append(source)
    catchphrase = resolve_localized(command.catchphrase, lang, command.default_language)
    if catchphrase:
        try:
            catchphrase = catchphrase.format_map(_KeepMissingFields(captures))
        except (KeyError, IndexError, ValueError, AttributeError, TypeError):
            pass
    output = resolve_localized(command.output, lang, command.default_language)
    if output:
        try:
            output = output.format_map(_KeepMissingFields(captures))
        except (KeyError, IndexError, ValueError, AttributeError, TypeError):
            pass
    return QueryPlan(
        sources=tuple(resolved_sources),
        response_template=catchphrase or output,
        catchphrase=catchphrase,
        output_mode=command.output_mode,
        failure_text=_render_failure_text(command, captures, lang),
    )


def build_step_plan(
    command: EngineCommand, captures: dict[str, str], lang: str,
    *, diagnostics: list[dict] | None = None,
) -> StepPlan | None:
    """Combined Spells' sibling of build_draft/build_query_plan - resolves
    each raw step dict's target(s) through the same _resolve_target
    captures already fill in for a single action or a flat multi-source
    query, deferring actual HA reads/dispatch to
    core.executor.ActionExecutor.execute_steps (this module has zero HA
    imports).

    ``diagnostics`` is Engine-layer observation only (Explain / Studio
    validation): when a list is passed, the first step that cannot be
    built is recorded ({"step", "alias", "kind", "reason"}).  It never
    changes control flow or the result - the plan is all-or-nothing."""
    if not command.steps:
        return None

    def _fail(index: int, step_spec: dict[str, Any], reason: str) -> None:
        if diagnostics is not None:
            diagnostics.append({
                "step": index, "alias": step_spec.get("alias"),
                "kind": step_spec["kind"], "reason": reason,
            })
        return None

    resolved_steps: list[ActionStep | QueryStep] = []
    for index, step_spec in enumerate(command.steps):
        alias = step_spec.get("alias")
        if step_spec["kind"] == "action":
            target = _resolve_target(step_spec["target"], captures)
            if target is None:
                return _fail(index, step_spec, "target_unresolved")
            arguments = _resolve_arguments(step_spec["arguments"], captures)
            if arguments is None:
                return _fail(index, step_spec, "argument_invalid")
            resolved_steps.append(ActionStep(
                alias=alias, operation=step_spec["operation"], target=target, arguments=arguments,
            ))
        else:
            sources = []
            for source_spec in step_spec["sources"]:
                source = _resolve_query_source_spec(source_spec, captures)
                if source is None:
                    return _fail(index, step_spec, "target_unresolved")
                sources.append(source)
            resolved_steps.append(QueryStep(alias=alias, sources=tuple(sources)))

    catchphrase = resolve_localized(command.catchphrase, lang, command.default_language)
    if catchphrase:
        try:
            catchphrase = catchphrase.format_map(_KeepMissingFields(captures))
        except (KeyError, IndexError, ValueError, AttributeError, TypeError):
            pass
    output = resolve_localized(command.output, lang, command.default_language)
    if output:
        try:
            output = output.format_map(_KeepMissingFields(captures))
        except (KeyError, IndexError, ValueError, AttributeError, TypeError):
            pass
    return StepPlan(
        steps=tuple(resolved_steps),
        response_template=catchphrase or output,
        catchphrase=catchphrase,
        output_mode=command.output_mode,
        failure_text=_render_failure_text(command, captures, lang),
    )


def _spoken_trigger(trigger_text: str, captures: dict[str, str]) -> str:
    """The resolved (language-specific) trigger with its {placeholder}s
    filled in from the captures (e.g. "aktiviere licht {area}" ->
    "aktiviere licht Buero") - what a clarifying question reads out,
    since the two ambiguous candidates' own matched_text is identical
    (both matched the same span of the user's utterance)."""
    try:
        return trigger_text.format(**captures)
    except (KeyError, IndexError, ValueError, AttributeError, TypeError):
        return trigger_text


@dataclass(frozen=True)
class OppositeProof:
    """Why two top-2 candidates count as opposite outcomes.

    basis: "action_outcomes" (shared P5 typed proof over executable
    variants; ``variants`` holds the proving pair), "v1_boolean_flip"
    (Semantics-v1 fallback) or "output_text" (outputs_are_opposite)."""

    basis: str
    variants: tuple[ActionVariant, ActionVariant] | None = None


def _drafts_opposite_proof(a: ActionDraft, b: ActionDraft) -> OppositeProof | None:
    """Central runtime opposite decision for two typed action drafts.

    1. P5 shared typed safety proof (core.action_semantics): some pair of
       the candidates' executable variants (the draft itself plus any
       user_fallback - no permission simulation) has a provably SAME
       effective target and canonical opposite ActionOutcomes - e.g.
       light.turn_on vs light.turn_off, or light.set_power(on=True) vs
       light.turn_off.  The static Collision Analyzer uses the exact same
       variant projection and proof, so a statically provable
       safety_ambiguity can never silently execute at runtime.  UNKNOWN
       target relation / unknown outcome is not a proof and falls through.

    2. Unchanged Semantics-v1 fallback: same operation, same target, but a
       boolean argument flipped - e.g. light.set_power on the same area with
       on=True vs on=False.  Purely structural, unlike the conservative
       word-list heuristic on free text.  Scoped to booleans since that's the
       only argument type the v1 catalog has; a future non-boolean
       "opposite" notion is a deliberately unopened question.

    Queries and Combined Spells never reach this function (see _opposite);
    the loader guarantees operation/sources/steps are mutually exclusive, so
    that guard never hides a typed action pair from this proof."""
    pair = safety_opposite_variants(draft_action_variants(a), draft_action_variants(b))
    if pair is not None:
        return OppositeProof("action_outcomes", pair)
    # Semantics-v1 fallback on the primary drafts - behavior kept verbatim.
    if a.operation != b.operation or a.target != b.target:
        return None
    for key, value in a.arguments.items():
        other = b.arguments.get(key)
        if isinstance(value, bool) and isinstance(other, bool) and value != other:
            return OppositeProof("v1_boolean_flip")
    return None


def _drafts_are_opposite(a: ActionDraft, b: ActionDraft) -> bool:
    return _drafts_opposite_proof(a, b) is not None


@dataclass(frozen=True)
class CommandMatch:
    command: EngineCommand
    captures: dict[str, str]
    trigger_text: str
    matched_text: str
    is_exact: bool
    score: float
    action_draft: ActionDraft | None
    output: str | None
    catchphrase: str | None
    query_plan: QueryPlan | None = None
    step_plan: StepPlan | None = None

    @property
    def spoken_trigger(self) -> str:
        return _spoken_trigger(self.trigger_text, self.captures)


@dataclass(frozen=True)
class CommandAmbiguity:
    option_a: CommandMatch
    option_b: CommandMatch


def _opposite_proof(a: CommandMatch, b: CommandMatch, lang: str) -> OppositeProof | None:
    if a.query_plan is not None or b.query_plan is not None or a.step_plan is not None or b.step_plan is not None:
        # A query (or a Combined Spell, which may itself contain queries
        # and/or several actions) reports a value or a batch outcome, not
        # a single boolean flipped the wrong way - there's no "opposite
        # outcome" analogous to stage-1/README.md's original reason this
        # mechanism exists. Never raise ambiguity for either candidate;
        # just let the higher score win, same as any other non-opposite pair.
        return None
    if a.action_draft is not None and b.action_draft is not None:
        return _drafts_opposite_proof(a.action_draft, b.action_draft)
    # Retain opposite-output ambiguity for mixed typed/output pairs. This
    # comparison is only a safety hint; the typed command's output is never run.
    text_a = resolve_localized(a.command.output, lang, a.command.default_language)
    text_b = resolve_localized(b.command.output, lang, b.command.default_language)
    if not text_a or not text_b:
        return None
    if outputs_are_opposite(text_a.format(**a.captures), text_b.format(**b.captures)):
        return OppositeProof("output_text")
    return None


def _opposite(a: CommandMatch, b: CommandMatch, lang: str) -> bool:
    return _opposite_proof(a, b, lang) is not None


def duplicate_spell_ids(book: EngineSpellbook) -> frozenset[str]:
    """Every spell_id that appears more than once among this book's own
    commands. The loader never enforced id uniqueness (raw_command.get(
    "id") is taken as-is - see load_spellbook_from_dict) - a real but
    narrow authoring mistake, not a load failure. Any caller that treats
    spell_id as a stable, unique identity (find_command_by_id below,
    core.mcp_projection.project_spellbook) must detect this and refuse to
    arbitrarily pick one of the conflicting commands, rather than silently
    executing or exposing whichever happened to come first."""
    from collections import Counter

    counts = Counter(c.spell_id for c in book.commands)
    return frozenset(spell_id for spell_id, n in counts.items() if n > 1)


def find_command_by_id(book: EngineSpellbook, spell_id: str) -> EngineCommand | None:
    """Direct, non-matching lookup by a command's own declared id - the
    integration point for a caller that has ALREADY selected an exact
    Spell by some other means (an MCP tools/call naming it directly, see
    core/mcp_execution.py) and must never run this book's own text/fuzzy
    matcher a second time to find it. Purely additive: reads book.commands
    the same way find_command()/match() already do, but never tokenizes,
    scores or ranks anything, and never expands aliases - an alias is a
    phrasing of a command, never a separate spell_id. Returns a command
    regardless of its own `enabled` flag; a caller that must not act on a
    disabled command (core/mcp_execution.py does) checks that separately,
    the same way project_spellbook() already does before ever calling
    project_command().

    A spell_id shared by more than one command in this book (see
    duplicate_spell_ids) is never resolved here either - None, exactly as
    if it didn't exist, rather than an arbitrary pick of one of the
    conflicting commands."""
    if book.disabled or spell_id in duplicate_spell_ids(book):
        return None
    return next((c for c in book.commands if c.spell_id == spell_id), None)



# ---------------------------------------------------------------------------
# Matching Semantics v1
# ---------------------------------------------------------------------------
#
# The rules below define the observable matching semantics of HASE v1.
# They document the existing behavior; they are not a new ranking model.
#
# 1. Specificity
#    A command/trigger's specificity is the number of literal words in its
#    compiled trigger (`literal_word_count`). Placeholders do not contribute
#    to specificity.
#
# 2. Tier selection
#    Commands are evaluated from highest to lowest specificity.
#    As soon as one specificity tier produces at least one eligible candidate,
#    lower-specificity tiers are not considered.
#
# 3. Candidate eligibility
#    A trigger match alone does not make a candidate eligible. The complete
#    command must be resolvable for the captured values: its action draft,
#    query plan, step plan, or text-only response must be constructible.
#    Candidates that cannot be resolved are discarded.
#
# 4. Ranking within a tier
#    Eligible candidates in the selected specificity tier are ranked by their
#    fuzzy literal-match score in descending order.
#
# 5. Conservative ambiguity
#    Only the two highest-ranked eligible candidates in the selected
#    specificity tier participate in ambiguity detection.
#
#    If their scores differ by less than `_AMBIGUITY_SCORE_EPSILON` and
#    they represent opposite outcomes, the result is ambiguous rather
#    than silently selecting either outcome.
#
#    Additional candidates, including candidates whose scores also fall
#    within the ambiguity epsilon, do not participate in this decision
#    in Matching Semantics v1.
#
#    "Opposite outcomes" are defined by `_opposite()`. For typed actions
#    this uses structural action comparison; for applicable rendered
#    outputs it delegates to `outputs_are_opposite()` in matching.py,
#    including its fixed antonym vocabulary and percentage-setting rule.
#
#    Queries and Combined Spells do not participate in the
#    opposite-outcome ambiguity rule.
#
#    Tie-break by validated decomposition: among candidates whose scores are
#    within `_AMBIGUITY_SCORE_EPSILON` of the best one, the candidate that
#    successfully fills MORE semantically validated placeholders (numeric
#    `type: int/float` captures and closed-vocabulary `value_map`/
#    `true_values`/`false_values` captures - see `_validated_placeholder_count`)
#    outranks the others. A more general trigger therefore cannot win by
#    letting one free-text placeholder swallow words a more specific trigger
#    would have split into valid, typed values ("Lumos {area}" vs
#    "Lumos {area} {color}" for "Lumos Küche rot"). This is generic: nothing
#    here knows any spell, word or color. A trigger whose validated
#    placeholders cannot be satisfied never matches at all (match_segments
#    rejects it) and so never gains this preference. The preference also
#    replaces the opposite-outcome ambiguity check between candidates with
#    different validated counts: the better-explained decomposition is not
#    a coin flip.
#
# 6. Final selection
#    If the conservative ambiguity rule does not apply, the highest-ranked
#    candidate is selected.
#
#    Equal-scoring, non-opposite candidates are therefore resolved by the
#    existing stable command/alias ordering. This is deterministic for an
#    unchanged spellbook, but it is not a semantic declaration that one such
#    candidate is more specific than the other.
#
# 7. Full-input matching
#    Trigger matching must account for the complete utterance. Unconsumed
#    leading, intermediate, or trailing tokens invalidate that trigger match.
#
# 8. Residual fallback (recovery, not matching)
#    Rule 7 is unchanged: on a normal successful match no token ever
#    disappears. Separately, when a command's target capture (area_name /
#    entity_name) is the LAST placeholder of its trigger and spans more than
#    one word, the engine records shorter leading parts of that capture as
#    `ActionDraft.residual_alternatives` (longest first, at most
#    `_MAX_RESIDUAL_WORDS` trailing words dropped). They take no part in
#    matching, ranking or ambiguity. Only `ActionExecutor.execute` may use
#    them, and only when the full target failed with `target_not_found`
#    (never after a target that resolved, never for another failure):
#    each alternative is probed side-effect-free, exactly one must resolve
#    unambiguously AND be permitted by policy, else the original failure
#    stands. The discarded words are reported in
#    `ExecutionResult.data["residual_fallback"]` and, ahead of execution, in
#    the Explain candidate's `debug["residual_alternatives"]`.
#
# Any intentional change to these rules is a matching-semantics change and
# must not be introduced as an incidental refactor.

# At most this many trailing words may be dropped by a residual alternative.
_MAX_RESIDUAL_WORDS = 3


def _residual_alternatives(
    command: EngineCommand, segments: list, captures: dict[str, str], lang: str, draft: ActionDraft,
) -> tuple[ResidualAlternative, ...]:
    """Recovery interpretations for a resolvable target capture (area_name /
    entity_name) that is the LAST placeholder of the trigger and spans more
    than one word: the same command built from each shorter leading part of
    that capture, longest first, with the trailing words recorded as the
    residual. Generic - nothing here knows which capture is an "area" or a
    "color", only that the command's own target is filled from a capture at
    the end of the utterance.

    These never take part in matching or ranking (Matching Semantics rule
    7, full consumption, is untouched): the executor alone may try them,
    and only after the full target failed to resolve (see
    ActionExecutor._residual_fallback)."""
    if draft.expert or draft.user_fallback is not None or draft.target.kind not in ("area_name", "entity_name"):
        return ()
    spec = command.target_spec
    name = spec.get("capture") if isinstance(spec, dict) else None
    if not isinstance(name, str) or not segments or segments[-1] != ("ph", name):
        return ()
    value = captures.get(name)
    if not value or value != draft.target.value:
        return ()
    words = value.split()
    alternatives: list[ResidualAlternative] = []
    for keep in range(len(words) - 1, 0, -1):
        if len(words) - keep > _MAX_RESIDUAL_WORDS:
            break
        shortened = " ".join(words[:keep])
        alt_draft = build_draft(command, {**captures, name: shortened}, lang)
        if alt_draft is None or alt_draft.target.kind != draft.target.kind:
            continue
        alternatives.append(ResidualAlternative(
            capture=name, original=value, shortened=shortened,
            residual=" ".join(words[keep:]), draft=alt_draft,
        ))
    return tuple(alternatives)


def _validated_placeholder_count(candidate: CommandMatch) -> int:
    """How many of the candidate's semantically validated (numeric or
    closed-vocabulary) placeholders were filled with a real captured value.
    Only placeholders match_segments itself recognized by shape/vocabulary
    can be filled, so a count > 0 is proof of a valid typed decomposition."""
    command = candidate.command
    validated = _numeric_capture_names(command.arguments_spec, command.steps) | _vocabulary_capture_words(
        command.arguments_spec, command.steps,
    ).keys()
    return sum(1 for name in validated if candidate.captures.get(name))


def _rank_candidates(candidates: list[CommandMatch]) -> list[CommandMatch]:
    """Rank by score, then prefer the more validated decomposition among
    the candidates within `_AMBIGUITY_SCORE_EPSILON` of the best score."""
    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
    top = ranked[0].score
    near = [c for c in ranked if top - c.score < _AMBIGUITY_SCORE_EPSILON]
    counts = {id(c): _validated_placeholder_count(c) for c in near}
    most = max(counts.values())
    if most == min(counts.values()):
        return ranked
    preferred = [c for c in near if counts[id(c)] == most]
    return preferred + [c for c in ranked if c not in preferred]


def _within_ambiguity_epsilon(best: CommandMatch, other: CommandMatch) -> bool:
    return (
        best.score - other.score < _AMBIGUITY_SCORE_EPSILON
        and _validated_placeholder_count(best) == _validated_placeholder_count(other)
    )


@dataclass(frozen=True)
class MatchTraceCandidate:
    """One trigger candidate observed during the real Semantics-v1 matcher.

    ``reason`` is the raw trace code (debug).  ``rejection``, ``disposition``
    and ``outcome_reason`` are filled only with ``collect_trace=True`` and
    feed the stable MatchExplain contract (core/match_explain.py).  ``engine``
    carries Draft/Engine-layer causes separately from matcher reasons.
    """

    spell_id: str
    trigger_text: str
    specificity: int
    evaluated: bool
    matched: bool = False
    eligible: bool = False
    score: float | None = None
    captures: dict[str, str] | None = None
    reason: str | None = None
    is_exact: bool | None = None
    is_alias: bool = False
    order: int | None = None
    rejection: str | None = None
    disposition: str | None = None
    outcome_reason: str | None = None
    engine: dict[str, Any] | None = None
    actions: tuple[dict[str, Any], ...] = ()
    debug: dict[str, Any] | None = None


@dataclass(frozen=True)
class MatchTrace:
    """Diagnostic observation of one real find_command() run.

    This is deliberately not a second matcher. The trace is populated by the
    same control flow that makes the production decision.
    """

    semantics: int
    text: str
    language: str
    typed_only: bool
    candidates: tuple[MatchTraceCandidate, ...]
    decision: str
    selected_spell_id: str | None = None
    ambiguity_spell_ids: tuple[str, str] | None = None
    reason: str | None = None
    opposite_basis: str | None = None
    opposite_variants: tuple[dict[str, Any], dict[str, Any]] | None = None
    via_source_area_fallback: bool = False
    selection_reason: str | None = None
    epsilon: float = _AMBIGUITY_SCORE_EPSILON


@dataclass(frozen=True)
class CommandExplanation:
    result: CommandMatch | CommandAmbiguity | None
    trace: MatchTrace


def _find_command_impl(
    book: EngineSpellbook,
    text: str,
    lang: str = "en",
    *,
    typed_only: bool = False,
    collect_trace: bool = False,
) -> tuple[CommandMatch | CommandAmbiguity | None, MatchTrace | None]:
    # Invariant: every `collect_trace` branch below only records what the
    # production control flow already decided.  No diagnostic value is ever
    # read back into ranking, scoring, tiering, eligibility or ambiguity.
    if book.disabled:
        trace = MatchTrace(semantics=1, text=text, language=lang, typed_only=typed_only,
                           candidates=(), decision="no_match", reason="book_disabled") if collect_trace else None
        return None, trace
    tokens = tokenize_with_spans(text)
    expanded = []
    trace_entries: list[MatchTraceCandidate] = []
    alias_ids: set[int] = set()
    order_of: dict[int, int] = {}
    for command in book.commands:
        if not command.enabled:
            if collect_trace:
                trace_entries.append(MatchTraceCandidate(
                    command.spell_id,
                    resolve_localized(command.trigger, lang, command.default_language) or "",
                    command.sort_key, False, reason="spell_disabled",
                    disposition="excluded", outcome_reason="spell_disabled",
                ))
            continue
        expanded.append(command)
        aliases = resolve_localized(command.aliases, lang, command.default_language, [])
        alias_disambiguable = (
            _numeric_capture_names(command.arguments_spec, command.steps)
            | _vocabulary_capture_words(command.arguments_spec, command.steps).keys()
        )
        for trigger in aliases:
            segments = compile_trigger(trigger, alias_disambiguable)
            expanded.append(replace(command, trigger=trigger, compiled={trigger: segments},
                                    sort_key=literal_word_count(segments), aliases=()))
            if collect_trace:
                alias_ids.add(id(expanded[-1]))
    if collect_trace:
        order_of = {id(c): i for i, c in enumerate(expanded)}
    commands = sorted(expanded, key=lambda c: c.sort_key, reverse=True)
    # Trace index of every eligible CommandMatch (trace mode only).
    entry_of: dict[int, int] = {}

    def _entry(command, trigger_text, tier_key, evaluated, **fields):
        return MatchTraceCandidate(
            command.spell_id, trigger_text or "", tier_key, evaluated,
            is_alias=id(command) in alias_ids, order=order_of.get(id(command)), **fields,
        )

    def _mark(candidate, disposition, outcome_reason):
        index = entry_of.get(id(candidate))
        if index is not None:
            trace_entries[index] = replace(
                trace_entries[index], disposition=disposition, outcome_reason=outcome_reason,
            )

    def _finish(decision, best, ranked, reason, *, proof=None, ambiguous_pair=None,
                via_fallback=False, within_epsilon=False):
        """Trace-only: record dispositions of the already-decided result."""
        selection = None
        if decision == "match":
            if len(ranked) == 1:
                selection = "single_candidate"
            elif ranked[1].score == best.score:
                selection = "stable_order"
            elif within_epsilon:
                selection = "within_epsilon_not_opposite"
            else:
                selection = "clear_winner"
        if ambiguous_pair is not None:
            for candidate in ambiguous_pair:
                _mark(candidate, "ambiguous", "safety_ambiguity")
            for candidate in ranked[2:]:
                _mark(candidate, "outranked",
                      "not_in_top_two" if best.score - candidate.score < _AMBIGUITY_SCORE_EPSILON
                      else "lower_score")
        elif best is not None:
            _mark(best, "selected", selection)
            for position, candidate in enumerate(ranked[1:], start=1):
                if position >= 2 and best.score - candidate.score < _AMBIGUITY_SCORE_EPSILON:
                    # Semantics v1: only the top two take part in ambiguity.
                    reason = "not_in_top_two"
                elif candidate.score == best.score:
                    reason = "stable_order"
                else:
                    reason = "lower_score"
                _mark(candidate, "outranked", reason)
        return MatchTrace(
            semantics=1, text=text, language=lang, typed_only=typed_only,
            candidates=tuple(trace_entries), decision=decision,
            selected_spell_id=best.command.spell_id if best is not None and ambiguous_pair is None else None,
            ambiguity_spell_ids=(
                (ambiguous_pair[0].command.spell_id, ambiguous_pair[1].command.spell_id)
                if ambiguous_pair is not None else None
            ),
            reason=reason,
            opposite_basis=proof.basis if proof is not None else None,
            opposite_variants=(
                tuple(describe_variant(v) for v in proof.variants)
                if proof is not None and proof.variants is not None else None
            ),
            via_source_area_fallback=via_fallback,
            selection_reason=selection,
        )

    # An empty capture admitted solely by fallback_source_area is weaker than
    # any real captured value. Keep the first (most specific) fallback-only
    # tier in reserve, but continue looking for a lower-tier phrasing that
    # actually captured the user's words.
    deferred_fallback_candidates = None
    tier_start = 0
    while tier_start < len(commands):
        tier_key = commands[tier_start].sort_key
        tier_end = tier_start
        while tier_end < len(commands) and commands[tier_end].sort_key == tier_key:
            tier_end += 1
        tier = commands[tier_start:tier_end]
        tier_start = tier_end
        candidates = []
        for command in tier:
            trigger_text = resolve_localized(command.trigger, lang, command.default_language)
            if (
                typed_only and command.operation is None and command.expert_command is None
                and not command.sources and not command.steps
            ):
                if collect_trace:
                    trace_entries.append(_entry(
                        command, trigger_text, tier_key, True, reason="typed_only_excluded",
                        disposition="excluded", outcome_reason="typed_only_excluded",
                    ))
                continue
            segments = command.compiled.get(trigger_text)
            if segments is None:
                if collect_trace:
                    trace_entries.append(_entry(
                        command, trigger_text, tier_key, True, reason="trigger_unavailable",
                        disposition="rejected", outcome_reason="trigger_unavailable",
                    ))
                continue
            allow_empty_placeholders = frozenset()
            target_spec = command.target_spec
            if (
                isinstance(target_spec, dict)
                and target_spec.get("kind") == "area_name"
                and target_spec.get("fallback_source_area") is True
                and isinstance(target_spec.get("capture"), str)
            ):
                allow_empty_placeholders = frozenset({target_spec["capture"]})
            segment_diagnostics: list[dict] | None = [] if collect_trace else None
            found = match_segments(
                segments, tokens, text,
                allow_empty_placeholders=allow_empty_placeholders,
                numeric_placeholders=_numeric_capture_names(command.arguments_spec, command.steps),
                vocabulary_placeholders=_vocabulary_capture_words(command.arguments_spec, command.steps),
                diagnostics=segment_diagnostics,
            )
            if found is None:
                if collect_trace:
                    detail = segment_diagnostics[-1] if segment_diagnostics else {"reason": "no_trigger_match"}
                    trace_entries.append(_entry(
                        command, trigger_text, tier_key, True, reason="trigger_no_match",
                        rejection=detail["reason"], disposition="rejected",
                        outcome_reason=detail["reason"], debug=dict(detail),
                    ))
                continue
            draft_diagnostics: list[dict] | None = [] if collect_trace else None
            draft = build_draft(command, found.captures, lang, diagnostics=draft_diagnostics)
            residual_alternatives = (
                _residual_alternatives(command, segments, found.captures, lang, draft)
                if draft is not None else ()
            )
            if residual_alternatives:
                draft = replace(draft, residual_alternatives=residual_alternatives)
            if (command.operation is not None or command.expert_command is not None) and draft is None:
                if collect_trace:
                    reasons = list(dict.fromkeys(d["reason"] for d in draft_diagnostics or ()))
                    trace_entries.append(_entry(
                        command, trigger_text, tier_key, True, matched=True,
                        score=found.score, captures=dict(found.captures), is_exact=found.is_exact,
                        reason="draft_unresolvable", disposition="rejected",
                        outcome_reason="candidate_unresolvable",
                        engine={"stage": "action_draft",
                                "reason": reasons[0] if reasons else "draft_build_failed",
                                "variants": [dict(d) for d in draft_diagnostics or ()]},
                    ))
                continue
            query_plan = build_query_plan(command, found.captures, lang) if command.sources else None
            if command.sources and query_plan is None:
                if collect_trace:
                    trace_entries.append(_entry(
                        command, trigger_text, tier_key, True, matched=True,
                        score=found.score, captures=dict(found.captures), is_exact=found.is_exact,
                        reason="query_plan_unresolvable", disposition="rejected",
                        outcome_reason="candidate_unresolvable",
                        engine={"stage": "query_plan", "reason": "query_plan_build_failed"},
                    ))
                continue
            step_diagnostics: list[dict] | None = [] if collect_trace else None
            step_plan = (
                build_step_plan(command, found.captures, lang, diagnostics=step_diagnostics)
                if command.steps else None
            )
            if command.steps and step_plan is None:
                if collect_trace:
                    first = (step_diagnostics or [{}])[0]
                    trace_entries.append(_entry(
                        command, trigger_text, tier_key, True, matched=True,
                        score=found.score, captures=dict(found.captures), is_exact=found.is_exact,
                        reason="step_plan_unresolvable", disposition="rejected",
                        outcome_reason="candidate_unresolvable",
                        engine={"stage": "step_plan", "reason": first.get("reason", "step_plan_build_failed"),
                                "steps": [dict(d) for d in step_diagnostics or ()]},
                    ))
                continue
            output = None
            catchphrase = resolve_localized(command.catchphrase, lang, command.default_language)
            try:
                if command.operation is None and draft is None and not command.sources and not command.steps:
                    output = resolve_localized(command.output, lang, command.default_language).format(**found.captures)
                if catchphrase:
                    if command.operation == "entity.get_state" or command.sources or command.steps:
                        catchphrase = catchphrase.format_map(_KeepMissingFields(found.captures))
                    else:
                        catchphrase = catchphrase.format(**found.captures)
            except (KeyError, IndexError, ValueError, AttributeError, TypeError):
                if collect_trace:
                    trace_entries.append(_entry(
                        command, trigger_text, tier_key, True, matched=True,
                        score=found.score, captures=dict(found.captures), is_exact=found.is_exact,
                        reason="response_format_error", disposition="rejected",
                        outcome_reason="candidate_unresolvable",
                        engine={"stage": "response", "reason": "response_format_error"},
                    ))
                continue
            candidate = CommandMatch(
                command, found.captures, trigger_text, found.matched_text,
                found.is_exact, found.score, draft, output, catchphrase, query_plan,
                step_plan=step_plan,
            )
            candidates.append(candidate)
            if collect_trace:
                entry_of[id(candidate)] = len(trace_entries)
                trace_entries.append(_entry(
                    command, trigger_text, tier_key, True, matched=True,
                    eligible=True, score=found.score, captures=dict(found.captures),
                    is_exact=found.is_exact, reason="eligible",
                    actions=tuple(describe_variant(v) for v in draft_action_variants(draft))
                    if draft is not None else (),
                    debug=(
                        {"residual_alternatives": [
                            {"capture": a.capture, "original": a.original,
                             "shortened": a.shortened, "residual": a.residual}
                            for a in residual_alternatives
                        ]} if residual_alternatives else None
                    ),
                ))
        if not candidates:
            continue

        fallback_candidates = []
        for candidate in candidates:
            target_spec = candidate.command.target_spec
            capture_name = (
                target_spec.get("capture")
                if isinstance(target_spec, dict)
                and target_spec.get("kind") == "area_name"
                and target_spec.get("fallback_source_area") is True
                else None
            )
            if (
                isinstance(capture_name, str)
                and not candidate.captures.get(capture_name)
                and candidate.action_draft is not None
                and candidate.action_draft.target.kind == "source_area"
            ):
                fallback_candidates.append(candidate)

        if fallback_candidates:
            normal_candidates = [c for c in candidates if c not in fallback_candidates]
            if normal_candidates:
                # A real capture always beats the synthetic source-area
                # fallback, even when both phrasings are in the same tier.
                if collect_trace:
                    for candidate in fallback_candidates:
                        _mark(candidate, "outranked", "superseded_by_real_capture")
                candidates = normal_candidates
            else:
                # Do not let an empty high-specificity phrasing shadow a
                # lower-specificity alias that can consume real input.
                if deferred_fallback_candidates is None:
                    deferred_fallback_candidates = candidates
                    if collect_trace:
                        for candidate in candidates:
                            _mark(candidate, "outranked", "fallback_deferred")
                elif collect_trace:
                    for candidate in candidates:
                        _mark(candidate, "outranked", "superseded_by_real_capture")
                continue

        # Semantics v1 stops here: no lower-specificity tier is evaluated once
        # this tier has at least one eligible candidate. Record that fact for
        # diagnostics without running those candidates through matching.
        if collect_trace:
            for command in commands[tier_start:]:
                trigger_text = resolve_localized(command.trigger, lang, command.default_language)
                trace_entries.append(_entry(
                    command, trigger_text, command.sort_key, False,
                    reason="lower_tier_not_considered", disposition="not_evaluated",
                    outcome_reason="lower_specificity_tier",
                ))
            if deferred_fallback_candidates is not None:
                for candidate in deferred_fallback_candidates:
                    _mark(candidate, "outranked", "superseded_by_real_capture")

        candidates = _rank_candidates(candidates)
        best = candidates[0]
        within_epsilon = False
        if len(candidates) > 1:
            other = candidates[1]
            within_epsilon = _within_ambiguity_epsilon(best, other)
            if within_epsilon:
                proof = _opposite_proof(best, other, lang)
                if proof is not None:
                    result = CommandAmbiguity(best, other)
                    trace = _finish(
                        "ambiguous", best, candidates, "top_two_opposite_within_epsilon",
                        proof=proof, ambiguous_pair=(best, other),
                    ) if collect_trace else None
                    return result, trace
        trace = _finish(
            "match", best, candidates, "highest_ranked_candidate",
            within_epsilon=within_epsilon,
        ) if collect_trace else None
        return best, trace

    if deferred_fallback_candidates:
        candidates = _rank_candidates(deferred_fallback_candidates)
        best = candidates[0]
        within_epsilon = False
        if len(candidates) > 1:
            other = candidates[1]
            within_epsilon = _within_ambiguity_epsilon(best, other)
            if within_epsilon:
                proof = _opposite_proof(best, other, lang)
                if proof is not None:
                    result = CommandAmbiguity(best, other)
                    trace = _finish(
                        "ambiguous", best, candidates, "fallback_top_two_opposite_within_epsilon",
                        proof=proof, ambiguous_pair=(best, other), via_fallback=True,
                    ) if collect_trace else None
                    return result, trace
        trace = _finish(
            "match", best, candidates, "source_area_fallback_after_no_real_capture",
            via_fallback=True, within_epsilon=within_epsilon,
        ) if collect_trace else None
        return best, trace

    trace = _finish(
        "no_match", None, [], "no_eligible_candidate",
    ) if collect_trace else None
    return None, trace


def find_command(
    book: EngineSpellbook, text: str, lang: str = "en", *, typed_only: bool = False,
) -> CommandMatch | CommandAmbiguity | None:
    """One ranked matcher; typed consumers exclude free text before ranking."""
    result, _ = _find_command_impl(book, text, lang, typed_only=typed_only)
    return result


def explain(
    book: EngineSpellbook, text: str, lang: str = "en", *, typed_only: bool = False,
) -> CommandExplanation:
    """Run the production matcher once and return its decision plus diagnostics."""
    result, trace = _find_command_impl(
        book, text, lang, typed_only=typed_only, collect_trace=True,
    )
    assert trace is not None
    return CommandExplanation(result=result, trace=trace)


def match(book: EngineSpellbook, text: str, lang: str = "en") -> EngineResult:
    """Satellite/SPELLBOOK_ONLY execution view. Ranks and matches across
    EVERY execution kind a Spell can have - action, query, Combined, and
    text-only (assist_redirect true or false) - through the SAME
    find_command() ranking/ambiguity logic every other frontend
    (providers/code27/bridge.py, core/mcp_execution.py) already uses
    (typed_only=False, the default) - never a separate, narrower view.

    A text-only match carries no action_draft/query_plan/step_plan (all
    three stay None, the only combination find_command() itself can
    produce for one) - `command`/`captures` on EngineMatch/AmbiguousOption
    are what a caller (core.router.SpellbookRouter) needs to dispatch it,
    via core.text_only_execution.execute_text_only_command reused
    verbatim - the same neutral, shared function core.mcp_execution also
    calls directly, never a second, router-specific implementation of
    what a text-only Spell's `output`/`assist_redirect` fields mean.

    Until this change, this function passed typed_only=True, making
    EVERY text-only Spell invisible to SATELLITE/SPELLBOOK_ONLY matching
    regardless of assist_redirect - a real, empirically-confirmed gap
    (see tests/test_router.py's own VoiceReachesEveryExecutionKindTests
    for the end-to-end proof, driven through the exact same real request
    path conversation.py uses) against every other frontend already
    reaching this execution kind. Fixed here, at the one shared matcher
    every SATELLITE/SPELLBOOK_ONLY turn goes through - never a
    second-guessed reclassification per caller."""
    result = find_command(book, text, lang, typed_only=False)
    if result is None:
        return EngineNoMatch()
    if isinstance(result, CommandAmbiguity):
        def option(candidate):
            return AmbiguousOption(
                candidate.action_draft, candidate.command.spell_id,
                candidate.matched_text, candidate.spoken_trigger,
                query_plan=candidate.query_plan, step_plan=candidate.step_plan,
                command=candidate.command, captures=candidate.captures,
                catchphrase=candidate.catchphrase,
            )
        return EngineAmbiguous(option(result.option_a), option(result.option_b))
    return EngineMatch(result.action_draft, result.command.spell_id,
                       result.matched_text, result.is_exact, query_plan=result.query_plan,
                       step_plan=result.step_plan, command=result.command, captures=result.captures)
