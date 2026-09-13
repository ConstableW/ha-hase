"""Spellbooks: declarative trigger -> template mappings for the Assist
bridge (see bridge.py). A spellbook maps a fixed incantation like
"lumos {area}" onto a natural-language Assist command like "Licht {area}
einschalten" - the bridge resolves the template and forwards the result
through the exact same Assist pipeline it already uses for the codeword
flow. Spellbooks are intentionally dumb: no entity IDs, no service calls,
no Python - only text rewriting. Entity/area resolution stays entirely
Assist's job.

Spellbook files are discovered from two places: bundled under
spellbooks/*.yaml next to this file, and user-authored under
<config>/hase_spellbooks/*.yaml (the user directory wins on an id
collision). Both are re-scanned on every lookup so editing a file on disk
takes effect on the very next chat message - no Home Assistant reload
needed.
"""
from __future__ import annotations

import difflib
import logging
import re
from string import Formatter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Short connector/filler words to skip as single-token phrase-extraction
# windows in extract_named_phrase() below - kept local to this module
# (duplicated from bridge.py's own _STOPWORDS rather than imported, since
# bridge.py imports this module and not the other way around).
_CONNECTOR_STOPWORDS = {
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",
    "und", "auf", "im", "in", "am", "an", "zu", "bitte", "mal",
    "the", "a", "an", "to", "and", "on", "at", "of", "please",
}

BUILTIN_DIR = Path(__file__).parent / "spellbooks"
USER_SUBDIR = "hase_spellbooks"

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

# ("lit", "some words") - a literal phrase to fuzzy-match, or
# ("ph", "name") - a placeholder that greedily captures tokens.
Segment = tuple[str, str]


def tokenize_with_spans(text: str) -> list[tuple[str, int, int]]:
    """Words with their [start, end) character offsets in the original
    text, so a fuzzy match can be trimmed back to the actual span it
    covered (which may differ in length from the configured word) rather
    than the configured word's own length."""
    tokens: list[tuple[str, int, int]] = []
    for m in re.finditer(r"\S+", text):
        stripped = m.group().strip(",.:;!?–—\"'()")
        if not stripped:
            continue
        start = m.start() + m.group().index(stripped)
        tokens.append((stripped, start, start + len(stripped)))
    return tokens


def fuzzy_threshold(word: str) -> float:
    """Short words/phrases need a stricter match - there just isn't
    enough structure in e.g. 4 characters to tell a typo from an
    unrelated word of similar length ("cody" vs "body")."""
    return 0.82 if len(word) <= 6 else 0.75


def resolve_localized(value: str | dict[str, str] | None, lang: str) -> str | None:
    """Resolve a trigger/output/catchphrase field for a language.

    A plain string is used regardless of language - what keeps
    single-language, user-authored spellbooks valid with no extra
    ceremony. A dict resolves the exact language, falling back to
    English, falling back to whatever single variant is defined - a
    language gap never invalidates the entry, it just degrades to the
    best available text."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if not value:
        return None
    lang = (lang or "en").lower()[:2]
    if lang in value:
        return value[lang]
    if "en" in value:
        return value["en"]
    return next(iter(value.values()))


def _compile_trigger(trigger_text: str) -> list[Segment] | None:
    """Split a trigger string into an ordered list of literal-phrase and
    placeholder segments. Returns None (rejecting the trigger) if two
    placeholders are directly adjacent with no literal segment between
    them - the boundary between their captures would be ambiguous."""
    segments: list[Segment] = []
    pos = 0
    for m in _PLACEHOLDER_RE.finditer(trigger_text):
        literal = trigger_text[pos : m.start()].strip()
        if literal:
            segments.append(("lit", literal))
        segments.append(("ph", m.group(1)))
        pos = m.end()
    literal = trigger_text[pos:].strip()
    if literal:
        segments.append(("lit", literal))

    for prev, cur in zip(segments, segments[1:]):
        if prev[0] == "ph" and cur[0] == "ph":
            return None
    return segments


def _literal_word_count(segments: list[Segment]) -> int:
    return sum(len(text.split()) for kind, text in segments if kind == "lit")


def _match_literal(
    literal: str, tokens: list[tuple[str, int, int]], start_from: int
) -> tuple[int, int, float] | None:
    """Find the earliest window of tokens (starting at or after
    start_from) whose words each fuzzy-match the corresponding word of
    `literal`. Returns the matched [start, end) token indices plus the
    average match ratio, mirroring bridge.py's _find_codeword_match
    matching strategy.

    Matched per-word, not as one joined-text blob: comparing the whole
    phrase in one SequenceMatcher call let a long shared prefix dilute
    a completely different tail word into an accepted match (e.g.
    "löscht die feuer von mittelerde" vs "...von gondor" scored 0.759,
    above the 0.75 threshold, purely because 22 of the 32 characters
    matched before the words actually diverge) - ported from
    core/matching.py's own fix, found live while converting real
    spellbooks to the new engine, but this legacy matcher has the exact
    same bug independently and is still the active path for every
    spellbooks/*.yaml command. Requiring every word to independently
    clear ITS OWN length-based threshold keeps typo tolerance for a
    single misspelled word without letting an entirely different word
    ride along on its neighbors' correctness."""
    words = literal.split()
    n = len(words)
    if n == 0 or start_from + n > len(tokens):
        return None
    for start in range(start_from, len(tokens) - n + 1):
        window_words = [w for w, _, _ in tokens[start : start + n]]
        ratios = [
            difflib.SequenceMatcher(None, window_word.lower(), literal_word.lower()).ratio()
            for window_word, literal_word in zip(window_words, words)
        ]
        if all(ratio >= fuzzy_threshold(literal_word) for ratio, literal_word in zip(ratios, words)):
            return start, start + n, sum(ratios) / len(ratios)
    return None


def _match_segments(
    segments: list[Segment], tokens: list[tuple[str, int, int]], text: str
) -> tuple[dict[str, str], str, bool, float] | None:
    """Walk a compiled trigger's segments against the tokenized message,
    fuzzy-matching literal segments and letting each placeholder greedily
    consume the tokens between the previous and next literal match (or to
    the end of the message, for a trailing placeholder). A placeholder
    must consume at least one token.

    Returns (captures, matched_text, is_exact, score) - matched_text is
    the message's own span the whole trigger matched (not the configured
    trigger text, which may differ from a fuzzy/typo match); is_exact is
    False if any literal segment only fuzzy-matched; score is the
    average fuzzy ratio across this trigger's literal segments (1.0 if
    it has none), used by match_spellbook to pick the best-fitting
    candidate among several that all clear the acceptance threshold,
    rather than just the first one declared in the spellbook file.

    The whole trigger must account for the WHOLE message, start to
    end - a literal segment is only accepted at the position already
    reached (never found further ahead by skipping tokens), and
    leftover tokens after the last segment reject the match entirely.
    Ported from core/matching.py's own fix (found live: "lumos maxima
    Küche" used to match the area-less "lumos maxima" trigger by
    silently ignoring the trailing "Küche" token) - this legacy matcher
    has the same gap and is still the active path for spellbooks/*.yaml."""
    captures: dict[str, str] = {}
    tok_idx = 0
    exact = True
    span_start: int | None = None
    span_end: int | None = None
    ratios: list[float] = []

    for i, (kind, value) in enumerate(segments):
        if kind == "lit":
            found = _match_literal(value, tokens, tok_idx)
            if found is None:
                return None
            start_i, end_i, ratio = found
            if start_i != tok_idx:
                # Found further ahead than where we already are - one
                # or more tokens right here were silently skipped, not
                # consumed by anything. Never accept that.
                return None
            ratios.append(ratio)
            if ratio < 0.999:
                exact = False
            lit_start_char = tokens[start_i][1]
            lit_end_char = tokens[end_i - 1][2]
            span_start = lit_start_char if span_start is None else min(span_start, lit_start_char)
            span_end = lit_end_char if span_end is None else max(span_end, lit_end_char)
            tok_idx = end_i
            continue

        next_lit = None
        if i + 1 < len(segments) and segments[i + 1][0] == "lit":
            next_lit = segments[i + 1][1]

        if next_lit is not None:
            boundary = _match_literal(next_lit, tokens, tok_idx)
            if boundary is None:
                return None
            end_idx = boundary[0]
        else:
            end_idx = len(tokens)

        if end_idx <= tok_idx:
            return None
        span_tokens = tokens[tok_idx:end_idx]
        ph_start_char, ph_end_char = span_tokens[0][1], span_tokens[-1][2]
        captures[value] = text[ph_start_char:ph_end_char]
        span_start = ph_start_char if span_start is None else min(span_start, ph_start_char)
        span_end = ph_end_char if span_end is None else max(span_end, ph_end_char)
        tok_idx = end_idx

    if tok_idx != len(tokens):
        # Trailing tokens nothing in the trigger accounted for.
        return None

    matched_text = text[span_start:span_end] if span_start is not None else ""
    score = sum(ratios) / len(ratios) if ratios else 1.0
    return captures, matched_text, exact, score


_VALID_ACTIONS = {"vacuum_clean", "light_all_on", "light_all_off"}


@dataclass
class SpellCommand:
    trigger: str | dict[str, str]
    output: str | dict[str, str] | None
    catchphrase: str | dict[str, str] | None
    action: str | None = None
    compiled: dict[str, list[Segment]] = field(default_factory=dict, repr=False)
    sort_key: int = 0


@dataclass
class Spellbook:
    id: str
    name: str
    commands: list[SpellCommand]


@dataclass
class SpellAmbiguity:
    """Returned by match_spellbook instead of a normal match when two
    same-specificity candidates both clear the fuzzy threshold with
    near-identical scores AND resolve to opposite actions (on/off,
    open/close, ...) - picking either one silently risks doing the exact
    opposite of what was meant, so the caller (bridge.py) should ask
    which one was intended instead of guessing. Each option is
    (resolved_command, catchphrase, trigger_text)."""

    option_a: tuple[str, str | None, str]
    option_b: tuple[str, str | None, str]


# How close two same-tier candidates' match scores must be to treat an
# opposite-action tie as genuinely ambiguous rather than trusting the
# higher score. Deliberately conservative (smaller than the ~0.11 score
# gap observed in the one real mishearing this guard was built from,
# where the higher-scoring candidate was in fact the correct one) so a
# clear winner is still trusted, and only a near-exact tie pauses to ask.
_AMBIGUITY_SCORE_EPSILON = 0.05

# Best-effort, not exhaustive: pairs of word groups whose presence in two
# candidates' resolved output text signals they do opposite things.
# Missing a pair here just means match_spellbook falls back to trusting
# the higher score, same as before this guard existed - it never blocks
# a match, only adds a "ask instead of guess" pause for a tie that looks
# genuinely opposite.
_OPPOSITE_ACTION_PAIRS: list[tuple[set[str], set[str]]] = [
    (
        {"an", "ein", "einschalten", "anschalten", "entzünde", "entzündet",
         "start", "starten", "hochfahren", "on"},
        {"aus", "ausschalten", "lösche", "löscht", "löschen", "stopp", "stoppen",
         "anhalten", "herunterfahren", "off", "stop"},
    ),
    (
        {"öffne", "öffnet", "öffnen", "open"},
        {"schließe", "schließt", "schließen", "close"},
    ),
    (
        {"lauter", "höher", "hoch", "increase", "raise", "louder", "up"},
        {"leiser", "niedriger", "runter", "decrease", "lower", "quieter", "down"},
    ),
    (
        {"verriegeln", "verriegelt", "sperren", "lock"},
        {"entriegeln", "entriegelt", "entsperren", "unlock"},
    ),
]


# "Licht {area} auf 80 Prozent stellen" doesn't contain any literal
# on/off word, but setting a light to a nonzero brightness is an "on"
# action just as much as "einschalten" is - without this, the exact
# on-vs-off tie this guard exists for (e.g. middle_earth.yaml's
# "entzündet ... auf 100 Prozent stellen" vs "löscht ... ausschalten")
# would never register as opposite, defeating the guard's purpose.
_PERCENT_SET_RE = re.compile(r"\bauf\s+\d+\s*(?:prozent|percent|%)\b", re.IGNORECASE)


def _outputs_are_opposite(output_a: str, output_b: str) -> bool:
    words_a = set(re.findall(r"\w+", output_a.lower()))
    words_b = set(re.findall(r"\w+", output_b.lower()))
    if _PERCENT_SET_RE.search(output_a):
        words_a.add("an")
    if _PERCENT_SET_RE.search(output_b):
        words_b.add("an")
    for group_x, group_y in _OPPOSITE_ACTION_PAIRS:
        if (words_a & group_x and words_b & group_y) or (
            words_a & group_y and words_b & group_x
        ):
            return True
    return False


def _valid_localized(value, optional=False) -> bool:
    if value is None:
        return optional
    if isinstance(value, str):
        return optional or bool(value.strip())
    return isinstance(value, dict) and bool(value) and all(
        isinstance(lang, str) and bool(lang.strip())
        and isinstance(text, str) and bool(text.strip())
        for lang, text in value.items()
    )


def _template_fields(text: str) -> set[str]:
    fields = set()
    for _, name, spec, conversion in Formatter().parse(text):
        if name is not None:
            if not name.isidentifier() or spec or conversion:
                raise ValueError("only simple named placeholders are supported")
            fields.add(name)
    return fields


def _valid_book_metadata(raw) -> bool:
    return (isinstance(raw, dict)
            and isinstance(raw.get("id"), str) and bool(raw["id"].strip())
            and isinstance(raw.get("name"), str) and bool(raw["name"].strip())
            and isinstance(raw.get("commands"), list))


def _load_command(raw: dict) -> SpellCommand | None:
    if not isinstance(raw, dict):
        return None
    trigger = raw.get("trigger")
    output = raw.get("output")
    catchphrase = raw.get("catchphrase")
    action = raw.get("action")

    if action is not None and (not isinstance(action, str) or action not in _VALID_ACTIONS):
        _LOGGER.warning(
            "code27 spellbook: unknown action %r (expected one of %s), skipping command: %r",
            action, sorted(_VALID_ACTIONS), raw,
        )
        return None

    # A "vacuum_clean" command never reaches Assist, so it has nothing to
    # resolve {placeholders} against text-wise - output is meaningless
    # for it and stays optional, unlike every other spell.
    output_required = action is None
    if not (_valid_localized(trigger)
            and _valid_localized(output, optional=not output_required)
            and _valid_localized(catchphrase, optional=True)):
        _LOGGER.warning("code27 spellbook: command missing trigger/output, skipping: %r", raw)
        return None

    trigger_variants = list(trigger.values()) if isinstance(trigger, dict) else [trigger]
    compiled: dict[str, list[Segment]] = {}
    placeholder_names: set[str] | None = None
    for variant in trigger_variants:
        try:
            _template_fields(variant)
        except ValueError:
            _LOGGER.warning("code27 spellbook: invalid trigger template, skipping command")
            return None
        segments = _compile_trigger(variant)
        if segments is None:
            _LOGGER.warning(
                "code27 spellbook: trigger %r has two adjacent placeholders with no literal "
                "text between them, skipping command",
                variant,
            )
            return None
        compiled[variant] = segments
        names = {name for kind, name in segments if kind == "ph"}
        placeholder_names = names if placeholder_names is None else placeholder_names & names

    if action == "vacuum_clean" and "area" not in (placeholder_names or set()):
        _LOGGER.warning(
            "code27 spellbook: action 'vacuum_clean' requires an {area} placeholder in "
            "every trigger variant, skipping command: %r",
            raw,
        )
        return None

    for field_name, field_value in (("output", output), ("catchphrase", catchphrase)):
        if field_value is None:
            continue
        variants = (
            list(field_value.values())
            if isinstance(field_value, dict)
            else ([field_value] if field_value else [])
        )
        for text in variants:
            try:
                missing = _template_fields(text) - (placeholder_names or set())
            except ValueError:
                _LOGGER.warning("code27 spellbook: invalid output template, skipping command")
                return None
            if missing:
                _LOGGER.warning(
                    "code27 spellbook: command %r references placeholder(s) %s in %s "
                    "not present in trigger, skipping command",
                    trigger_variants[0],
                    missing,
                    field_name,
                )
                return None

    reference_trigger = resolve_localized(trigger, "en") or trigger_variants[0]
    sort_key = _literal_word_count(compiled[reference_trigger])

    return SpellCommand(
        trigger=trigger,
        output=output,
        catchphrase=catchphrase,
        action=action,
        compiled=compiled,
        sort_key=sort_key,
    )


def _load_spellbook_file(path: Path) -> Spellbook | None:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as err:  # noqa: BLE001 - a bad file must never break the bridge
        _LOGGER.warning("code27 spellbook: could not parse %s: %s", path, err)
        return None
    if not isinstance(raw, dict):
        _LOGGER.warning("code27 spellbook: %s does not contain a mapping, skipping", path)
        return None

    book_id = raw.get("id")
    name = raw.get("name")
    raw_commands = raw.get("commands")
    if not _valid_book_metadata(raw):
        _LOGGER.warning("code27 spellbook: %s is missing id/name/commands, skipping", path)
        return None

    commands: list[SpellCommand] = []
    for raw_command in raw_commands:
        if not isinstance(raw_command, dict):
            continue
        command = _load_command(raw_command)
        if command is not None:
            commands.append(command)

    commands.sort(key=lambda c: c.sort_key, reverse=True)
    return Spellbook(id=book_id, name=name, commands=commands)


def _spellbook_dirs(hass: "HomeAssistant") -> list[Path]:
    return [BUILTIN_DIR, Path(hass.config.path(USER_SUBDIR))]


def _list_spellbook_files(hass: "HomeAssistant") -> dict[str, Path]:
    """id -> winning file path across both spellbook directories; the
    user directory overrides the bundled one on an id collision."""
    files: dict[str, Path] = {}
    for directory in _spellbook_dirs(hass):
        try:
            if not directory.is_dir():
                continue
            paths = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
        except OSError as err:
            _LOGGER.warning("code27 spellbook: could not scan %s: %s", directory, err)
            continue
        for path in paths:
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("code27 spellbook: could not parse %s: %s", path, err)
                continue
            book_id = raw.get("id") if isinstance(raw, dict) else None
            if not _valid_book_metadata(raw):
                continue
            files[book_id] = path
    return files


async def async_discover_spellbooks(hass: "HomeAssistant") -> list[tuple[str, str]]:
    """(id, name) pairs for every discoverable spellbook, for the
    options-flow dropdown."""

    def _discover() -> list[tuple[str, str]]:
        results: list[tuple[str, str]] = []
        for book_id, path in _list_spellbook_files(hass).items():
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if not _valid_book_metadata(raw):
                continue
            name = raw["name"]
            results.append((book_id, name))
        results.sort(key=lambda item: item[1].lower())
        return results

    return await hass.async_add_executor_job(_discover)


_CACHE: dict[str, tuple[Path, float, Spellbook]] = {}


async def async_get_spellbook(hass: "HomeAssistant", spellbook_id: str) -> Spellbook | None:
    """Load (and cache by path+mtime) the spellbook for spellbook_id.
    Re-scans both directories every call so a renamed/moved/deleted file
    is picked up immediately, but only re-parses YAML when the winning
    file's mtime actually changed."""
    if not spellbook_id:
        return None

    def _load() -> Spellbook | None:
        path = _list_spellbook_files(hass).get(spellbook_id)
        if path is None:
            _CACHE.pop(spellbook_id, None)
            return None
        try:
            mtime = path.stat().st_mtime
        except OSError:
            _CACHE.pop(spellbook_id, None)
            return None
        cached = _CACHE.get(spellbook_id)
        if cached is not None and cached[0] == path and cached[1] == mtime:
            return cached[2]
        book = _load_spellbook_file(path)
        if book is not None:
            _CACHE[spellbook_id] = (path, mtime, book)
        else:
            _CACHE.pop(spellbook_id, None)
        return book

    return await hass.async_add_executor_job(_load)


def _spoken_trigger(trigger_text: str, captures: dict[str, str]) -> str:
    """The configured trigger with its {placeholder}s filled in from the
    captured text (e.g. "löscht die leuchtfeuer von {area}" -> "löscht
    die leuchtfeuer von Gondor") - unlike trigger_text used elsewhere
    (activity-log entries show the raw pattern, an established, purely
    internal convention), a SpellAmbiguity's options are read out loud
    to the user, so a literal unfilled "{area}" would be read verbatim
    by TTS instead of the actual area name."""
    try:
        return trigger_text.format(**captures)
    except (KeyError, IndexError, ValueError, AttributeError, TypeError):
        return trigger_text


def match_spellbook(
    book: Spellbook, text: str, lang: str
) -> tuple[str | None, str | None, str, str, bool, dict[str, str], str | None] | SpellAmbiguity | None:
    """Evaluate commands one specificity tier at a time (book.commands is
    pre-sorted by sort_key, most literal words first): try every command
    in the current tier, and only move on to the next, less specific
    tier if nothing in this one matched at all. Within a tier, the
    candidate with the best fuzzy score wins - not just the first one
    declared in the spellbook file, which previously meant two
    same-specificity triggers sharing most of their wording (e.g.
    "entzündet die leuchtfeuer von {area}" vs "löscht die feuer von
    {area}", both 4 literal words) could pick the wrong one purely by
    file order, regardless of which one the fuzzy match actually fit
    better. If the tier's top two candidates are near-tied (see
    _AMBIGUITY_SCORE_EPSILON) AND resolve to opposite actions (see
    _OPPOSITE_ACTION_PAIRS), a SpellAmbiguity is returned instead of
    guessing - a wrong on/off, open/close etc. guess is a real physical
    mistake, not just a bad reply.

    On a normal (non-ambiguous) match, returns (resolved_command,
    resolved_catchphrase, trigger_text, matched_text, is_exact,
    captures, action). trigger_text is the configured trigger (for the
    resolved language); matched_text is what the message itself actually
    said, which may differ if the match was fuzzy (a typo, a
    mishearing); captures is the raw placeholder-name -> captured-text
    mapping, needed by the entity-correction loop to know which captured
    value (an "area" or "target" placeholder) to try correcting if
    Assist can't resolve the resolved command. action is None for a
    normal spell (forward resolved_command to Assist as usual) or e.g.
    "vacuum_clean" for a command that a dedicated resolver handles
    instead - resolved_command is None in that case since there is no
    output template to resolve. Returns None if nothing matched at any
    tier."""
    tokens = tokenize_with_spans(text)

    tier_start = 0
    while tier_start < len(book.commands):
        tier_key = book.commands[tier_start].sort_key
        tier_end = tier_start
        while tier_end < len(book.commands) and book.commands[tier_end].sort_key == tier_key:
            tier_end += 1
        tier = book.commands[tier_start:tier_end]
        tier_start = tier_end

        candidates: list[
            tuple[float, dict[str, str], str, bool, str, str | None, str | None, str | None]
        ] = []
        for command in tier:
            trigger_text = resolve_localized(command.trigger, lang)
            segments = command.compiled.get(trigger_text)
            if segments is None:
                continue
            match = _match_segments(segments, tokens, text)
            if match is None:
                continue
            captures, matched_text, is_exact, score = match

            output_text = resolve_localized(command.output, lang)
            catchphrase_text = resolve_localized(command.catchphrase, lang)
            try:
                resolved_command = output_text.format(**captures) if output_text else None
                resolved_catchphrase = (
                    catchphrase_text.format(**captures) if catchphrase_text else None
                )
            except (KeyError, IndexError, ValueError, AttributeError, TypeError) as err:
                _LOGGER.warning(
                    "code27 spellbook: failed to resolve template for trigger %r: %s",
                    trigger_text,
                    err,
                )
                continue

            candidates.append(
                (
                    score, captures, matched_text, is_exact, trigger_text,
                    resolved_command, resolved_catchphrase, command.action,
                )
            )

        if not candidates:
            continue

        candidates.sort(key=lambda c: c[0], reverse=True)
        best = candidates[0]
        if len(candidates) > 1:
            runner_up = candidates[1]
            if (
                best[0] - runner_up[0] < _AMBIGUITY_SCORE_EPSILON
                and best[5] is not None
                and runner_up[5] is not None
                and _outputs_are_opposite(best[5], runner_up[5])
            ):
                return SpellAmbiguity(
                    option_a=(best[5], best[6], _spoken_trigger(best[4], best[1])),
                    option_b=(runner_up[5], runner_up[6], _spoken_trigger(runner_up[4], runner_up[1])),
                )

        score, captures, matched_text, is_exact, trigger_text, resolved_command, resolved_catchphrase, action = best
        return (
            resolved_command,
            resolved_catchphrase,
            trigger_text,
            matched_text,
            is_exact,
            captures,
            action,
        )

    return None


def _registry_names(hass: "HomeAssistant", include_areas: bool) -> list[str]:
    """Assist-exposed entity names and their areas, including aliases.

    Not cached: registries are already in-memory dicts on
    hass, so this is cheap, and it's only ever called after an Assist
    ERROR (not on every chat message).

    An entity/area's `aliases` can include a `ComputedNameType` sentinel
    (meaning "also match on the computed display name" - HA's newer
    aliases_v2 mechanism) instead of a literal string; that's already
    covered by `name`/`original_name` below, so non-str aliases are
    simply skipped rather than crashing on `.lower()`.

    For a `has_entity_name` entity (the modern convention most
    integrations use - confirmed with a real switch on testzwerg), the
    registry's own `name`/`original_name` is only the entity-local part
    ("Loudness"), not the composed name Assist actually exposes and
    matches on ("Wohnzimmer Loudness" - device name + entity name). Both
    the bare and composed forms are added as separate candidates, so
    whichever one a phrase actually resembles wins on fuzzy ratio."""
    names: list[str] = []
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    exposed_areas: set[str] = set()
    for entry in entity_registry.entities.values():
        if entry.disabled_by is not None or hass.states.get(entry.entity_id) is None:
            continue
        if not async_should_expose(hass, "conversation", entry.entity_id):
            continue
        device = device_registry.devices.get(entry.device_id) if entry.device_id else None
        area_id = entry.area_id or (device.area_id if device else None)
        if area_id:
            exposed_areas.add(area_id)
        local_name = entry.name or entry.original_name
        if local_name:
            names.append(local_name)

        if entry.has_entity_name and entry.device_id:
            device_name = (device.name_by_user or device.name) if device else None
            if device_name:
                composed = f"{device_name} {local_name}" if local_name else device_name
                names.append(composed)

        names.extend(alias for alias in entry.aliases if isinstance(alias, str))

    if include_areas:
        area_registry = ar.async_get(hass)
        for area in area_registry.areas.values():
            if area.id not in exposed_areas:
                continue
            if area.name:
                names.append(area.name)
            names.extend(alias for alias in area.aliases if isinstance(alias, str))

    return names


def _best_matching_name(
    hass: "HomeAssistant", phrase: str, include_areas: bool, exclude: set[str]
) -> tuple[str, float] | None:
    threshold = fuzzy_threshold(phrase)
    best: tuple[str, float] | None = None
    for name in _registry_names(hass, include_areas):
        if name.lower() in exclude:
            continue
        ratio = difflib.SequenceMatcher(None, name.lower(), phrase.lower()).ratio()
        if ratio < threshold:
            continue
        if best is None or ratio > best[1]:
            best = (name, ratio)
    return best


def find_correction_candidate(
    hass: "HomeAssistant",
    failed_phrase: str,
    tried_candidates: list[str],
    include_areas: bool,
) -> tuple[str, float] | None:
    """The best untried entity/area name fuzzy-matching failed_phrase,
    or None if nothing clears fuzzy_threshold() - the same acceptance
    bar used for trigger/codeword matching everywhere else in the
    bridge, not a separate, softer one. A None here is what lets the
    correction loop stop on "no more sufficiently good candidates"
    instead of a magic round-count limit. failed_phrase itself is
    always excluded, since suggesting back the exact name that just
    failed would be pointless."""
    exclude = {c.lower() for c in tried_candidates}
    exclude.add(failed_phrase.lower())
    return _best_matching_name(hass, failed_phrase, include_areas, exclude)


def verify_named_candidate(
    hass: "HomeAssistant", phrase: str, include_areas: bool
) -> tuple[str, float] | None:
    """Does `phrase` itself closely match a real entity/area name? Unlike
    find_correction_candidate, an exact self-match is not excluded -
    this checks whether a user's reply *is* a valid name, rather than
    searching for an alternative to something that already failed."""
    return _best_matching_name(hass, phrase, include_areas, exclude=set())


def extract_named_phrase(
    hass: "HomeAssistant", text: str, include_areas: bool, max_window: int = 4
) -> tuple[str, str, float] | None:
    """Slide a 1..max_window token window over `text`, fuzzy-matching
    each window's joined text against the entity/area name pool, and
    return (window_phrase, matched_candidate_name, ratio) for the single
    best-scoring window - or None if nothing clears fuzzy_threshold().
    Used both to guess which word(s) in a free-text codeword command
    were the bad entity name after an Assist ERROR, and to pull a named
    device out of a correction-dialog reply that wasn't itself a clean
    name ("ich meinte den Schreibtisch"). Single-token windows that are
    only a short connector word are skipped, since those produce
    coincidental matches against unrelated entity names far too easily."""
    tokens = tokenize_with_spans(text)
    names = _registry_names(hass, include_areas)
    if not tokens or not names:
        return None

    best: tuple[str, str, float] | None = None
    n_tokens = len(tokens)
    for start in range(n_tokens):
        for length in range(1, min(max_window, n_tokens - start) + 1):
            window = tokens[start : start + length]
            if length == 1 and (
                len(window[0][0]) <= 2 or window[0][0].lower() in _CONNECTOR_STOPWORDS
            ):
                continue
            window_text = " ".join(w for w, _, _ in window)
            threshold = fuzzy_threshold(window_text)
            for name in names:
                ratio = difflib.SequenceMatcher(
                    None, window_text.lower(), name.lower()
                ).ratio()
                if ratio < threshold:
                    continue
                if best is None or ratio > best[2]:
                    best = (window_text, name, ratio)
    return best
