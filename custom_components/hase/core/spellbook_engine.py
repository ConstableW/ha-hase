"""Shared HASE schema, loader and neutral/typed matching views."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from string import Formatter
from typing import Any

import yaml

from .matching import (
    Segment,
    compile_trigger,
    literal_word_count,
    match_segments,
    outputs_are_opposite,
    resolve_localized,
    tokenize_with_spans,
)
from .models import (
    ActionDraft,
    ActionTarget,
    AmbiguousOption,
    EngineAmbiguous,
    EngineMatch,
    EngineNoMatch,
    EngineResult,
)

_LOGGER = logging.getLogger(__name__)

_VALID_TARGET_KINDS = {
    "area_name", "area_id", "entity_name", "entity_id", "all_allowed", "none", "source_area",
}
# all_allowed/none/source_area name nothing - the other four MUST have
# either a capture (something spoken fills it in) or a fixed value.
_TARGET_KINDS_REQUIRING_CAPTURE_OR_VALUE = {"area_name", "area_id", "entity_name", "entity_id"}

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
    # A command has a typed operation, free-text output, or both.
    operation: str | None
    target_spec: dict[str, Any]
    arguments_spec: dict[str, Any]
    catchphrase: str | dict[str, str] | None
    compiled: dict[str, list[Segment]]  # resolved trigger text (per language) -> segments
    sort_key: int
    output: str | dict[str, str] | None = None


@dataclass(frozen=True)
class EngineSpellbook:
    id: str
    name: str
    commands: tuple[EngineCommand, ...]


def _resolve_target(spec: dict[str, Any], captures: dict[str, str]) -> ActionTarget | None:
    kind = spec.get("kind")
    if kind not in _VALID_TARGET_KINDS:
        return None
    if kind in ("all_allowed", "none", "source_area"):
        return ActionTarget(kind=kind, value=None)
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
    """Fill an arguments template from captures. Each entry is either a
    literal value (passed through as-is) or a `{capture, true_values,
    false_values}` boolean mapping (the only typed conversion the v1
    catalog needs - light.set_power's `on: bool`). An unrecognized
    captured word for a boolean argument means this command doesn't
    actually match this text, not a broken match - callers treat None
    as "try the next candidate", like other template-resolution failures."""
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
        captured = (captures.get(capture_name) or "").strip().lower()
        # `_load_command`/`_valid_arguments_spec` already rejects a
        # spec where these aren't string lists - the isinstance guard
        # here is defense in depth for any other caller of this
        # function (e.g. a future caller building EngineCommand
        # without going through the YAML loader), not the primary
        # check: a malformed entry never matches, but must not crash
        # the whole turn either way.
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
    """Load-time validation for the arguments template, so a broken
    spellbook is rejected once (with a clear log line) instead of
    crashing _resolve_arguments on whatever conversation turn
    eventually reaches this command's trigger. A boolean-argument
    entry's true_values/false_values are meant to be lists of spoken
    words to match against - YAML happily parses a bare `true`/`false`
    literal as a Python bool instead of the string the author meant,
    which would otherwise reach `.lower()` on a bool and crash the
    whole turn (not just this command) mid-match."""
    for value_spec in spec.values():
        if not isinstance(value_spec, dict) or "capture" not in value_spec:
            continue
        if not isinstance(value_spec["capture"], str) or value_spec["capture"] not in placeholder_names:
            return False
        if "true_values" in value_spec and not _valid_string_list(value_spec["true_values"]):
            return False
        if "false_values" in value_spec and not _valid_string_list(value_spec["false_values"]):
            return False
    return True


def _load_command(spell_id: str, raw: dict[str, Any]) -> EngineCommand | None:
    if not isinstance(raw, dict):
        return None
    trigger = raw.get("trigger")
    operation = raw.get("operation")
    target_spec = raw.get("target")
    arguments_spec = raw.get("arguments", {})
    catchphrase = raw.get("catchphrase")
    output = raw.get("output")

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
    if output is not None and not _valid_localized(output):
        _LOGGER.warning("spellbook engine: command %s has an invalid output, skipping", spell_id)
        return None
    if not has_operation and output is None:
        # Etappe 7: a command needs a typed form (operation), a
        # free-text form for some other caller to use (output, this
        # engine never acts on it itself), or both - neither means
        # nothing in the entire system could ever do anything with it.
        _LOGGER.warning(
            "spellbook engine: command %s has neither an operation nor output, skipping", spell_id
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

    languages = {"en", "de"}
    for localized in (trigger, output, catchphrase):
        if isinstance(localized, dict):
            languages.update(localized)
    for language in languages:
        trigger_text = resolve_localized(trigger, language)
        placeholders = {name for kind, name in compiled[trigger_text] if kind == "ph"}
        for template in (trigger_text, resolve_localized(output, language),
                         resolve_localized(catchphrase, language)):
            if template is None:
                continue
            try:
                for _, name, spec, conversion in Formatter().parse(template):
                    if name is not None and (
                        not name.isidentifier() or spec or conversion or name not in placeholders
                    ):
                        return None
            except ValueError:
                return None

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
    )


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
    name = raw.get("name")
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
    if command.operation is None:
        # Satellite must never turn free text into an executable proposal.
        return None
    target = _resolve_target(command.target_spec, captures)
    if target is None:
        return None
    arguments = _resolve_arguments(command.arguments_spec, captures)
    if arguments is None:
        return None
    catchphrase = resolve_localized(command.catchphrase, lang)
    if catchphrase:
        try:
            catchphrase = catchphrase.format(**captures)
        except (KeyError, IndexError, ValueError, AttributeError, TypeError):
            pass
    return ActionDraft(operation=command.operation, target=target, arguments=arguments, catchphrase=catchphrase)


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

    @property
    def spoken_trigger(self) -> str:
        return _spoken_trigger(self.trigger_text, self.captures)


@dataclass(frozen=True)
class CommandAmbiguity:
    option_a: CommandMatch
    option_b: CommandMatch


def _opposite(a: CommandMatch, b: CommandMatch, lang: str) -> bool:
    if a.action_draft is not None and b.action_draft is not None:
        return _drafts_are_opposite(a.action_draft, b.action_draft)
    # Retain opposite-output ambiguity for mixed typed/output pairs. This
    # comparison is only a safety hint; the typed command's output is never run.
    text_a = resolve_localized(a.command.output, lang)
    text_b = resolve_localized(b.command.output, lang)
    if not text_a or not text_b:
        return False
    return outputs_are_opposite(text_a.format(**a.captures), text_b.format(**b.captures))


def find_command(
    book: EngineSpellbook, text: str, lang: str = "en", *, typed_only: bool = False,
) -> CommandMatch | CommandAmbiguity | None:
    """One ranked matcher; typed consumers exclude free text before ranking."""
    tokens = tokenize_with_spans(text)
    commands = sorted(book.commands, key=lambda c: c.sort_key, reverse=True)
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
            if typed_only and command.operation is None:
                continue
            trigger_text = resolve_localized(command.trigger, lang)
            segments = command.compiled.get(trigger_text)
            if segments is None:
                continue
            found = match_segments(segments, tokens, text)
            if found is None:
                continue
            draft = build_draft(command, found.captures, lang)
            if command.operation is not None and draft is None:
                continue
            # An operation permanently selects the typed path, even if execution fails.
            output = None
            catchphrase = resolve_localized(command.catchphrase, lang)
            try:
                if command.operation is None:
                    output = resolve_localized(command.output, lang).format(**found.captures)
                if catchphrase:
                    catchphrase = catchphrase.format(**found.captures)
            except (KeyError, IndexError, ValueError, AttributeError, TypeError):
                continue
            candidates.append(CommandMatch(
                command, found.captures, trigger_text, found.matched_text,
                found.is_exact, found.score, draft, output, catchphrase,
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
                       result.matched_text, result.is_exact)
