"""Shared HASE schema, loader and neutral/typed matching views."""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from string import Formatter
from typing import Any

import yaml

from .book_languages import default_language
from .book_languages import text as resolve_localized
from .matching import (
    Segment,
    compile_trigger,
    literal_word_count,
    match_segments,
    outputs_are_opposite,
    tokenize_with_spans,
)
from .models import (
    DEFAULT_OUTPUT_MODE,
    OUTPUT_MODES,
    QUERY_RESPONSE_FIELDS,
    ActionDraft,
    ActionTarget,
    AmbiguousOption,
    EngineAmbiguous,
    EngineMatch,
    EngineNoMatch,
    EngineResult,
    QueryPlan,
    QuerySourceSpec,
)

_LOGGER = logging.getLogger(__name__)

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
_QUERY_SOURCE_KINDS = {"state", "attribute", "count", "min", "max", "avg", "sum"}
_AGGREGATE_SOURCE_KINDS = {"count", "min", "max", "avg", "sum"}
_SINGLE_SOURCE_TARGET_KINDS = {"area_name", "area_id", "entity_name", "entity_id", "source_area"}
_AGGREGATE_SOURCE_TARGET_KINDS = {"area_name", "area_id", "all_allowed", "source_area"}
_MAX_SOURCES_PER_COMMAND = 12

# How close two same-tier candidates' scores must be to treat an
# opposite-outcome tie as genuinely ambiguous rather than trusting the
# higher score. The established empirical margin is retained.
_AMBIGUITY_SCORE_EPSILON = 0.05


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


@dataclass(frozen=True)
class EngineSpellbook:
    id: str
    name: str
    commands: tuple[EngineCommand, ...]


class _KeepMissingFields(dict):
    """Format known captures while leaving runtime response fields intact."""

    def __missing__(self, key):
        return "{" + key + "}"


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
            return None
        return ActionTarget(kind=kind, value=value)
    if "value" in spec and isinstance(spec["value"], str) and spec["value"]:
        return ActionTarget(kind=kind, value=spec["value"])
    return None


def _resolve_arguments(spec: dict[str, Any], captures: dict[str, str]) -> dict[str, Any] | None:
    """Fill an arguments template from captures.

    Captured arguments support boolean word mappings and bounded integers.
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

        if value_spec.get("type") == "int":
            try:
                converted = int(captured_raw, 10)
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
            if conversion_type != "int":
                return False
            if "true_values" in value_spec or "false_values" in value_spec:
                return False
            minimum = value_spec.get("min")
            maximum = value_spec.get("max")
            if minimum is not None and (not isinstance(minimum, int) or isinstance(minimum, bool)):
                return False
            if maximum is not None and (not isinstance(maximum, int) or isinstance(maximum, bool)):
                return False
            if minimum is not None and maximum is not None and minimum > maximum:
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
    spell_id: str, raw_sources: Any, placeholder_names: set[str]
) -> tuple[dict[str, Any], ...] | None:
    """Load-time parse+validate for a multi-source query spell's `sources`
    list (item 4/5). Returns raw per-source dicts (mirroring how
    target_spec/arguments_spec stay raw dicts on EngineCommand, resolved
    into concrete types only once captures are known - see build_query_plan)
    or None on any validation failure, same fail-closed convention as every
    other loader check in this module."""
    if not isinstance(raw_sources, list) or not raw_sources or len(raw_sources) > _MAX_SOURCES_PER_COMMAND:
        _LOGGER.warning(
            "spellbook engine: command %s has an invalid sources list (empty, not a list, or over %d "
            "entries), skipping", spell_id, _MAX_SOURCES_PER_COMMAND,
        )
        return None

    parsed: list[dict[str, Any]] = []
    seen_variables: set[str] = set()
    for i, raw_source in enumerate(raw_sources):
        if not isinstance(raw_source, dict):
            _LOGGER.warning("spellbook engine: command %s's sources[%d] is not a mapping, skipping", spell_id, i)
            return None

        kind = raw_source.get("kind")
        if kind not in _QUERY_SOURCE_KINDS:
            _LOGGER.warning("spellbook engine: command %s's sources[%d] has an invalid kind %r, skipping", spell_id, i, kind)
            return None
        is_aggregate = kind in _AGGREGATE_SOURCE_KINDS

        target_spec = raw_source.get("target")
        allowed_target_kinds = _AGGREGATE_SOURCE_TARGET_KINDS if is_aggregate else _SINGLE_SOURCE_TARGET_KINDS
        if not isinstance(target_spec, dict) or target_spec.get("kind") not in allowed_target_kinds:
            _LOGGER.warning(
                "spellbook engine: command %s's sources[%d] (kind=%s) has an invalid target, skipping",
                spell_id, i, kind,
            )
            return None
        if "capture" in target_spec and (
            not isinstance(target_spec["capture"], str) or target_spec["capture"] not in placeholder_names
        ):
            _LOGGER.warning(
                "spellbook engine: command %s's sources[%d] target references an unknown capture, skipping",
                spell_id, i,
            )
            return None
        if (
            target_spec["kind"] in _TARGET_KINDS_REQUIRING_CAPTURE_OR_VALUE
            and "capture" not in target_spec
            and not (isinstance(target_spec.get("value"), str) and target_spec["value"].strip())
        ):
            _LOGGER.warning(
                "spellbook engine: command %s's sources[%d] target has neither a capture nor a value, "
                "skipping", spell_id, i,
            )
            return None

        domain_filter = raw_source.get("domain_filter")
        if domain_filter is not None and (not isinstance(domain_filter, str) or not domain_filter.strip()):
            _LOGGER.warning("spellbook engine: command %s's sources[%d] has an invalid domain_filter, skipping", spell_id, i)
            return None
        device_class = raw_source.get("device_class")
        if device_class is not None and (not isinstance(device_class, str) or not device_class.strip()):
            _LOGGER.warning("spellbook engine: command %s's sources[%d] has an invalid device_class, skipping", spell_id, i)
            return None

        attribute = raw_source.get("attribute")
        if kind == "attribute":
            if not isinstance(attribute, str) or not attribute.strip():
                _LOGGER.warning(
                    "spellbook engine: command %s's sources[%d] is kind=attribute but has no attribute "
                    "name, skipping", spell_id, i,
                )
                return None
        elif attribute is not None:
            _LOGGER.warning(
                "spellbook engine: command %s's sources[%d] has an attribute name but kind isn't "
                "'attribute', skipping", spell_id, i,
            )
            return None

        state_filter = raw_source.get("state_filter")
        if state_filter is not None and (
            not is_aggregate or not isinstance(state_filter, str) or not state_filter.strip()
        ):
            _LOGGER.warning(
                "spellbook engine: command %s's sources[%d] has a state_filter but isn't an aggregation, "
                "or it isn't a non-empty string, skipping", spell_id, i,
            )
            return None

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
            _LOGGER.warning(
                "spellbook engine: command %s's sources[%d] has an invalid, reserved or duplicate "
                "variable name, skipping", spell_id, i,
            )
            return None
        seen_variables.add(variable)

        unit_variable = raw_source.get("unit_variable")
        if unit_variable is not None:
            if (
                kind != "state" or not isinstance(unit_variable, str) or not unit_variable.isidentifier()
                or unit_variable in placeholder_names or unit_variable in seen_variables
            ):
                _LOGGER.warning(
                    "spellbook engine: command %s's sources[%d] has an invalid, reserved or duplicate "
                    "unit_variable, skipping", spell_id, i,
                )
                return None
            seen_variables.add(unit_variable)

        parsed.append({
            "kind": kind, "target": target_spec, "domain_filter": domain_filter,
            "device_class": device_class, "attribute": attribute,
            "state_filter": state_filter, "variable": variable, "unit_variable": unit_variable,
        })
    return tuple(parsed)


def _load_base_command(spell_id: str, raw: dict[str, Any]) -> EngineCommand | None:
    if not isinstance(raw, dict):
        return None
    trigger = raw.get("trigger")
    operation = raw.get("operation")
    target_spec = raw.get("target")
    arguments_spec = raw.get("arguments", {})
    catchphrase = raw.get("catchphrase")
    output = raw.get("output")
    sources_raw = raw.get("sources")
    output_mode_raw = raw.get("output_mode")

    if not _valid_localized(trigger):
        _LOGGER.warning("spellbook engine: command %s missing a trigger, skipping", spell_id)
        return None
    if operation is not None and (not isinstance(operation, str) or not operation.strip()):
        _LOGGER.warning(
            "spellbook engine: command %s has an operation field that isn't a non-empty string, "
            "skipping", spell_id,
        )
        return None
    has_operation = isinstance(operation, str) and bool(operation.strip())
    has_sources = isinstance(sources_raw, list) and len(sources_raw) > 0
    if has_operation and has_sources:
        # A command is either a single/multi-step action OR a multi-source
        # query, never both - keeps "what does this command DO" a single
        # unambiguous question, matching the same user/expert exclusivity
        # philosophy as the rest of this loader.
        _LOGGER.warning(
            "spellbook engine: command %s has both an operation and sources - a command must be either "
            "an action or a query, skipping", spell_id,
        )
        return None
    if output_mode_raw is not None and (not isinstance(output_mode_raw, str) or output_mode_raw not in OUTPUT_MODES):
        _LOGGER.warning("spellbook engine: command %s has an invalid output_mode, skipping", spell_id)
        return None
    output_mode_value = output_mode_raw or DEFAULT_OUTPUT_MODE
    if output is not None and not _valid_localized(output):
        _LOGGER.warning("spellbook engine: command %s has an invalid output, skipping", spell_id)
        return None
    if not has_operation and not has_sources and output is None:
        # Etappe 7: a command needs a typed form (operation), a
        # multi-source query form (sources), a free-text form for some
        # other caller to use (output, this engine never acts on it
        # itself), or some combination - none of the three means nothing
        # in the entire system could ever do anything with it.
        _LOGGER.warning(
            "spellbook engine: command %s has neither an operation, sources nor output, skipping", spell_id
        )
        return None
    if has_operation and not isinstance(target_spec, dict):
        _LOGGER.warning("spellbook engine: command %s has an invalid target, skipping", spell_id)
        return None
    if has_operation and target_spec.get("kind") not in _VALID_TARGET_KINDS:
        _LOGGER.warning("spellbook engine: command %s has an invalid target, skipping", spell_id)
        return None
    if has_operation and not isinstance(arguments_spec, dict):
        _LOGGER.warning("spellbook engine: command %s has invalid arguments, skipping", spell_id)
        return None
    if catchphrase is not None and not _valid_localized(catchphrase):
        _LOGGER.warning("spellbook engine: command %s has an invalid catchphrase, skipping", spell_id)
        return None

    trigger_variants = list(trigger.values()) if isinstance(trigger, dict) else [trigger]
    compiled: dict[str, list[Segment]] = {}
    for variant in trigger_variants:
        segments = compile_trigger(variant)
        if segments is None:
            _LOGGER.warning(
                "spellbook engine: command %s's trigger %r has two adjacent placeholders, skipping",
                spell_id, variant,
            )
            return None
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

    languages = {"en", "de"}
    for localized in (trigger, output, catchphrase):
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
            allowed = placeholders | (
                QUERY_RESPONSE_FIELDS
                if is_response and operation == "entity.get_state"
                else frozenset()
            ) | (declared_variable_names if is_response and has_sources else frozenset())
            try:
                for _, name, spec, conversion in Formatter().parse(template):
                    if name is not None and (
                        not name.isidentifier() or spec or conversion or name not in allowed
                    ):
                        return None
            except ValueError:
                return None

    if has_sources:
        placeholder_names = {
            name for segments in compiled.values() for kind, name in segments if kind == "ph"
        }
        parsed_sources = _parse_sources(spell_id, sources_raw, placeholder_names)
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
            compiled=compiled,
            sort_key=literal_word_count(compiled[reference_trigger]),
            output=output,
            sources=parsed_sources,
            output_mode=output_mode_value,
        )

    if not has_operation:
        # Output-only: no target/arguments capture-consistency checks
        # apply (there's no target_spec/arguments_spec to check them
        # against) - this command is done loading.
        reference_trigger = resolve_localized(trigger, "en") or trigger_variants[0]
        return EngineCommand(
            spell_id=spell_id,
            trigger=trigger,
            operation=None,
            target_spec={},
            arguments_spec={},
            catchphrase=catchphrase,
            compiled=compiled,
            sort_key=literal_word_count(compiled[reference_trigger]),
            output=output,
            output_mode=output_mode_value,
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
        _LOGGER.warning(
            "spellbook engine: command %s's target references capture %r, which no trigger variant "
            "declares - this command could never resolve a target, skipping",
            spell_id, target_spec.get("capture"),
        )
        return None
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
        _LOGGER.warning(
            "spellbook engine: command %s's target has neither a capture nor a value - "
            "it could never resolve to anything, skipping",
            spell_id,
        )
        return None
    if not _valid_arguments_spec(arguments_spec, placeholder_names):
        _LOGGER.warning(
            "spellbook engine: command %s has an invalid arguments spec "
            "(dangling capture reference, or true_values/false_values not a list of strings), skipping",
            spell_id,
        )
        return None

    reference_trigger = resolve_localized(trigger, "en") or trigger_variants[0]
    return EngineCommand(
        spell_id=spell_id,
        trigger=trigger,
        operation=operation,
        target_spec=target_spec,
        arguments_spec=arguments_spec,
        catchphrase=catchphrase,
        compiled=compiled,
        sort_key=literal_word_count(compiled[reference_trigger]),
        output=output,
        output_mode=output_mode_value,
    )


def _load_command(spell_id: str, raw: dict[str, Any]) -> EngineCommand | None:
    if not isinstance(raw, dict):
        return None
    user = _load_base_command(spell_id, raw) if raw.get("operation") or raw.get("output") or raw.get("sources") else None
    expert = None
    if isinstance(raw.get("expert"), dict):
        technical = {**raw, **raw["expert"]}
        technical.pop("expert", None)
        technical.pop("output", None)
        expert = _load_base_command(spell_id, technical)
    command = user or expert
    if command is None:
        return None
    aliases = raw.get("triggers", [])
    groups = list(aliases.values()) if isinstance(aliases, dict) else [aliases]
    if not all(isinstance(group, list) and all(isinstance(t, str) and t.strip() for t in group) for group in groups):
        return None
    # Compile aliases through the existing parser, including capture validation.
    for trigger in (t for group in groups for t in group):
        if _load_base_command(spell_id, {**raw, **(raw.get("expert", {}) if user is None else {}), "trigger": trigger}) is None:
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
    return EngineSpellbook(id=book_id, name=name, commands=tuple(commands))


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


def build_draft(command: EngineCommand, captures: dict[str, str], lang: str) -> ActionDraft | None:
    if command.expert_command is not None:
        fallback = build_draft(replace(command, expert_command=None), captures, lang)
        expert = build_draft(command.expert_command, captures, lang)
        if expert is not None:
            return replace(expert, expert=True, user_fallback=fallback)
        return fallback
    if command.operation is None:
        # Satellite must never turn free text into an executable proposal.
        return None
    target = _resolve_target(command.target_spec, captures)
    if target is None:
        return None
    arguments = _resolve_arguments(command.arguments_spec, captures)
    if arguments is None:
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
                       output_mode=command.output_mode)


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
        target = _resolve_target(source_spec["target"], captures)
        if target is None:
            return None
        resolved_sources.append(QuerySourceSpec(
            kind=source_spec["kind"],
            target=target,
            domain_filter=source_spec.get("domain_filter"),
            device_class=source_spec.get("device_class"),
            attribute=source_spec.get("attribute"),
            state_filter=source_spec.get("state_filter"),
            variable=source_spec["variable"],
            unit_variable=source_spec.get("unit_variable"),
        ))
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


def _drafts_are_opposite(a: ActionDraft, b: ActionDraft) -> bool:
    """Same operation, same target, but a boolean argument flipped -
    e.g. light.set_power on the same area with on=True vs on=False.
    Purely structural, unlike the conservative word-list heuristic on
    free text - a real improvement the typed catalog makes possible,
    not just a port. Scoped to booleans since that's the only argument
    type the v1 catalog has; a future non-boolean "opposite" notion is
    a deliberately unopened question, not an oversight."""
    if a.operation != b.operation or a.target != b.target:
        return False
    for key, value in a.arguments.items():
        other = b.arguments.get(key)
        if isinstance(value, bool) and isinstance(other, bool) and value != other:
            return True
    return False


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

    @property
    def spoken_trigger(self) -> str:
        return _spoken_trigger(self.trigger_text, self.captures)


@dataclass(frozen=True)
class CommandAmbiguity:
    option_a: CommandMatch
    option_b: CommandMatch


def _opposite(a: CommandMatch, b: CommandMatch, lang: str) -> bool:
    if a.query_plan is not None or b.query_plan is not None:
        # A query reports a value, it doesn't DO anything - there's no
        # "opposite outcome" analogous to a boolean action flipped the
        # wrong way (stage-1/README.md's whole reason this mechanism
        # exists). Never raise ambiguity for a query candidate; just let
        # the higher score win, same as any other non-opposite pair.
        return False
    if a.action_draft is not None and b.action_draft is not None:
        return _drafts_are_opposite(a.action_draft, b.action_draft)
    # Retain opposite-output ambiguity for mixed typed/output pairs. This
    # comparison is only a safety hint; the typed command's output is never run.
    text_a = resolve_localized(a.command.output, lang, a.command.default_language)
    text_b = resolve_localized(b.command.output, lang, b.command.default_language)
    if not text_a or not text_b:
        return False
    return outputs_are_opposite(text_a.format(**a.captures), text_b.format(**b.captures))


def find_command(
    book: EngineSpellbook, text: str, lang: str = "en", *, typed_only: bool = False,
) -> CommandMatch | CommandAmbiguity | None:
    """One ranked matcher; typed consumers exclude free text before ranking."""
    tokens = tokenize_with_spans(text)
    expanded = []
    for command in book.commands:
        if not command.enabled:
            continue
        expanded.append(command)
        aliases = resolve_localized(command.aliases, lang, command.default_language, [])
        for trigger in aliases:
            segments = compile_trigger(trigger)
            expanded.append(replace(command, trigger=trigger, compiled={trigger: segments},
                                    sort_key=literal_word_count(segments), aliases=()))
    commands = sorted(expanded, key=lambda c: c.sort_key, reverse=True)
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
            if (
                typed_only and command.operation is None and command.expert_command is None
                and not command.sources
            ):
                continue
            trigger_text = resolve_localized(command.trigger, lang, command.default_language)
            segments = command.compiled.get(trigger_text)
            if segments is None:
                continue
            found = match_segments(segments, tokens, text)
            if found is None:
                continue
            draft = build_draft(command, found.captures, lang)
            if (command.operation is not None or command.expert_command is not None) and draft is None:
                continue
            query_plan = build_query_plan(command, found.captures, lang) if command.sources else None
            if command.sources and query_plan is None:
                continue
            # An operation/sources permanently selects the typed path, even if execution fails.
            output = None
            catchphrase = resolve_localized(command.catchphrase, lang, command.default_language)
            try:
                if command.operation is None and draft is None and not command.sources:
                    output = resolve_localized(command.output, lang, command.default_language).format(**found.captures)
                if catchphrase:
                    if command.operation == "entity.get_state" or command.sources:
                        catchphrase = catchphrase.format_map(_KeepMissingFields(found.captures))
                    else:
                        catchphrase = catchphrase.format(**found.captures)
            except (KeyError, IndexError, ValueError, AttributeError, TypeError):
                continue
            candidates.append(CommandMatch(
                command, found.captures, trigger_text, found.matched_text,
                found.is_exact, found.score, draft, output, catchphrase, query_plan,
            ))
        if not candidates:
            continue
        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]
        if len(candidates) > 1:
            other = candidates[1]
            if best.score - other.score < _AMBIGUITY_SCORE_EPSILON and _opposite(best, other, lang):
                return CommandAmbiguity(best, other)
        return best
    return None


def match(book: EngineSpellbook, text: str, lang: str = "en") -> EngineResult:
    """Satellite execution view: output-only commands are invisible."""
    result = find_command(book, text, lang, typed_only=True)
    if result is None:
        return EngineNoMatch()
    if isinstance(result, CommandAmbiguity):
        def option(candidate):
            return AmbiguousOption(candidate.action_draft, candidate.command.spell_id,
                                   candidate.matched_text, candidate.spoken_trigger)
        return EngineAmbiguous(option(result.option_a), option(result.option_b))
    return EngineMatch(result.action_draft, result.command.spell_id,
                       result.matched_text, result.is_exact, query_plan=result.query_plan)
