"""Shared HA-independent trigger matching and conservative ambiguity helpers."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

# ("lit", "some words") - a literal phrase to fuzzy-match, or
# ("ph", "name") - a placeholder that greedily captures tokens.
Segment = tuple[str, str]

_NUMERIC_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)?")


def is_numeric_token(word: str) -> bool:
    """Whether a single spoken token looks like a plain number (optionally
    with one decimal separator) - "50", "3,5" - used to let a `type: int`/
    `type: float` capture claim its own token(s) by lexical shape instead
    of needing a literal word to mark where it starts or ends. Deliberately
    narrow (no leading sign, no thousands separator): the goal is only to
    recognize an unambiguous number STT would produce for a spoken digit
    sequence, not to parse arbitrary numeric text."""
    return bool(_NUMERIC_TOKEN_RE.fullmatch(word))


def tokenize_with_spans(text: str) -> list[tuple[str, int, int]]:
    """Words with their [start, end) character offsets in the original
    text, so a fuzzy match can be trimmed back to the actual span it
    covered rather than the configured word's own length."""
    tokens: list[tuple[str, int, int]] = []
    for m in re.finditer(r"\S+", text):
        stripped = m.group().strip(",.:;!?–—\"'()")
        if not stripped:
            continue
        start = m.start() + m.group().index(stripped)
        tokens.append((stripped, start, start + len(stripped)))
    return tokens


def resolve_localized(value: str | dict[str, str] | None, lang: str) -> str | None:
    """A plain string is used regardless of language. A dict resolves
    the exact language, falling back to English, falling back to
    whatever single variant is defined - a language gap never
    invalidates the entry, it just degrades to the best available
    text."""
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


def fuzzy_threshold(word: str) -> float:
    """Short words/phrases need a stricter match - there isn't enough
    structure in e.g. 4 characters to tell a typo from an unrelated
    word of similar length."""
    return 0.82 if len(word) <= 6 else 0.75


def compile_trigger(trigger_text: str, disambiguable_placeholders: frozenset[str] = frozenset()) -> list[Segment] | None:
    """Split a trigger string into literal-phrase/placeholder segments -
    never rejected for adjacent placeholders (two placeholders directly
    next to each other, no literal text between them, are always
    admitted). What differs is how match_segments resolves the boundary
    between an adjacent pair at match time:

    - A "disambiguable" placeholder (named in `disambiguable_placeholders`
      - a `type: int`/`type: float` capture, or a `value_map`/
      `true_values`/`false_values` capture) is recognizable by SHAPE
      alone: a number is lexically distinct from a word, and a closed
      vocabulary word either is or isn't "rot"/"grün"/..., so its span is
      found directly, independent of its neighbor.
    - Two adjacent placeholders with no such shape (both free text)
      still split deterministically: each one before the last in the
      run claims exactly one token, and the last one claims the rest -
      see match_segments' own fallback. Not always the semantically
      "right" split for a genuinely multi-word capture in a non-final
      position, but always a real, working split rather than a load-time
      rejection."""
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
    return segments


def literal_word_count(segments: list[Segment]) -> int:
    return sum(len(text.split()) for kind, text in segments if kind == "lit")


def _match_literal(
    literal: str, tokens: list[tuple[str, int, int]], start_from: int
) -> tuple[int, int, float] | None:
    """Matched per-word, not as one blob: comparing the whole joined
    phrase in one SequenceMatcher call let a long shared prefix dilute
    a completely different tail word into an accepted match (e.g.
    "löscht die feuer von mittelerde" vs "...von gondor" scored 0.759,
    above the 0.75 threshold, purely because 22 of the 32 characters
    matched before the words actually diverge) - found live while
    converting real spellbooks, not hypothetical. Requiring every word
    to independently clear ITS OWN length-based threshold keeps typo
    tolerance for a single misspelled word without letting an entirely
    different word ride along on its neighbors' correctness."""
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


def _recognized_span(
    tokens: list[tuple[str, int, int]], start_from: int, name: str,
    numeric_placeholders: frozenset[str], vocabulary_placeholders: dict[str, frozenset[str]],
) -> tuple[int, int] | None:
    """The span placeholder `name` would claim starting at `start_from`,
    IF it's disambiguable (numeric or a known vocabulary) - None if it
    isn't one, or if it is one but nothing recognizable starts here."""
    if name in numeric_placeholders:
        end = start_from
        while end < len(tokens) and is_numeric_token(tokens[end][0]):
            end += 1
        return (start_from, end) if end > start_from else None
    vocabulary = vocabulary_placeholders.get(name)
    if vocabulary:
        return _match_vocabulary(tokens, start_from, vocabulary)
    return None


def _scan_for_recognized_start(
    tokens: list[tuple[str, int, int]], start_from: int, name: str,
    numeric_placeholders: frozenset[str], vocabulary_placeholders: dict[str, frozenset[str]],
) -> int | None:
    """Where placeholder `name`'s recognizable span first starts, scanning
    forward from `start_from` - used to bound a free-text placeholder
    that's immediately followed by a disambiguable one."""
    for j in range(start_from, len(tokens)):
        if _recognized_span(tokens, j, name, numeric_placeholders, vocabulary_placeholders) is not None:
            return j
    return None


def _match_vocabulary(
    tokens: list[tuple[str, int, int]], start_from: int, vocabulary: frozenset[str],
) -> tuple[int, int] | None:
    """Longest-match lookup against a closed, known word set (a capture's
    `value_map`/`true_values`/`false_values` keys, lowercased) - exact
    per-word comparison, no fuzz: the same `.lower()` exact lookup
    `_resolve_arguments` itself does once a span is captured, just
    applied here to also decide the span's boundary. Tries the longest
    plausible phrase first (multi-word vocabulary entries, e.g. "hell
    blau") down to a single word, so a longer valid phrase is never
    shadowed by a shorter prefix that also happens to be valid."""
    if not vocabulary or start_from >= len(tokens):
        return None
    max_words = min(max((len(v.split()) for v in vocabulary), default=1), len(tokens) - start_from)
    for n in range(max_words, 0, -1):
        candidate = " ".join(w for w, _, _ in tokens[start_from : start_from + n]).lower()
        if candidate in vocabulary:
            return start_from, start_from + n
    return None


@dataclass(frozen=True)
class SegmentMatch:
    captures: dict[str, str]
    matched_text: str
    is_exact: bool
    score: float


def match_segments(
    segments: list[Segment], tokens: list[tuple[str, int, int]], text: str,
    *, allow_empty_placeholders: frozenset[str] = frozenset(),
    numeric_placeholders: frozenset[str] = frozenset(),
    vocabulary_placeholders: dict[str, frozenset[str]] = {},  # noqa: B006 (never mutated)
    diagnostics: list[dict] | None = None,
) -> SegmentMatch | None:
    """Walk a compiled trigger's segments against the tokenized message.
    A placeholder greedily consumes the tokens between the previous and
    next literal match (or to the end of the message for a trailing
    placeholder) and must consume at least one token.

    A "disambiguable" placeholder - named in `numeric_placeholders` (a
    `type: int`/`type: float` capture) or a key of
    `vocabulary_placeholders` (a `value_map`/`true_values`/
    `false_values` capture, mapped to its lowercased word set - see
    spellbook_engine._numeric_capture_names/_vocabulary_capture_words)
    - instead consumes exactly its own recognizable span (a digit-token
    run, or a known vocabulary word/phrase) regardless of what follows
    it. This is what lets it sit directly next to another placeholder
    with no literal boundary between them (compile_trigger only admits
    that pairing for this same reason). A placeholder immediately
    followed by a disambiguable one (no literal between) stops where
    that one's recognizable span starts, instead of consuming to the
    next literal/end, handing it the rest.

    The whole trigger must account for the WHOLE message, start to
    end - a literal segment is only accepted at the position already
    reached (never found further ahead by skipping tokens), and
    leftover tokens after the last segment reject the match entirely.
    Found live: "lumos maxima Küche" matched the area-less "lumos
    maxima" trigger by silently ignoring the trailing "Küche" token,
    turning on every light in the house instead of recognizing this as
    a distinct (and here, unsupported) per-area command. _match_literal
    still scans ahead internally - that scan is used only to find
    where a PLACEHOLDER's greedy capture should end, never to accept a
    literal match at a position other than the one already reached.

    ``diagnostics`` is observation only (Explain): when a list is passed, the
    reason for a rejection is appended before returning None.  It never
    changes control flow or the result."""

    def _reject(reason: str, segment: int | None, token: int) -> None:
        if diagnostics is not None:
            diagnostics.append({"reason": reason, "segment_index": segment, "token_index": token})
        return None

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
                return _reject("no_trigger_match", i, tok_idx)
            start_i, end_i, ratio = found
            if start_i != tok_idx:
                # Found further ahead than where we already are - that
                # means one or more tokens right here were silently
                # skipped, not consumed by anything. Never accept that.
                return _reject("input_not_fully_consumed", i, tok_idx)
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

        own_span = _recognized_span(tokens, tok_idx, value, numeric_placeholders, vocabulary_placeholders)
        if own_span is not None:
            end_idx = own_span[1]
        elif next_lit is not None:
            boundary = _match_literal(next_lit, tokens, tok_idx)
            if boundary is None:
                return _reject("no_trigger_match", i + 1, tok_idx)
            end_idx = boundary[0]
        elif i + 1 < len(segments) and segments[i + 1][0] == "ph" and (
            segments[i + 1][1] in numeric_placeholders or segments[i + 1][1] in vocabulary_placeholders
        ):
            # A free-text placeholder immediately followed by a
            # disambiguable one (no literal between): stop where the
            # next one's recognizable span starts, leaving it that span.
            end_idx = _scan_for_recognized_start(
                tokens, tok_idx, segments[i + 1][1], numeric_placeholders, vocabulary_placeholders,
            )
            if end_idx is None:
                end_idx = tok_idx
        elif i + 1 < len(segments) and segments[i + 1][0] == "ph":
            # Two free-text placeholders directly adjacent, neither
            # recognizable by shape - nothing marks the real boundary,
            # so fall back to a fixed, always-working split: this one
            # claims exactly one token, the placeholder(s) after it
            # (applying the same rule in turn) get the rest. Not always
            # the semantically "right" split for a genuinely multi-word
            # capture in a non-final position, but compile_trigger no
            # longer rejects this pairing, so match_segments must always
            # produce SOME deterministic result for it.
            end_idx = min(tok_idx + 1, len(tokens))
        else:
            end_idx = len(tokens)

        if end_idx < tok_idx:
            return _reject("no_trigger_match", i, tok_idx)
        if end_idx == tok_idx:
            # Empty captures are opt-in only.  The matcher does not invent a
            # value; target resolution decides whether the missing capture
            # becomes source_area.
            if value not in allow_empty_placeholders:
                return _reject("required_capture_missing", i, tok_idx)
            continue
        span_tokens = tokens[tok_idx:end_idx]
        ph_start_char, ph_end_char = span_tokens[0][1], span_tokens[-1][2]
        captures[value] = text[ph_start_char:ph_end_char]
        span_start = ph_start_char if span_start is None else min(span_start, ph_start_char)
        span_end = ph_end_char if span_end is None else max(span_end, ph_end_char)
        tok_idx = end_idx

    if tok_idx != len(tokens):
        # Trailing tokens nothing in the trigger accounted for - e.g.
        # "lumos maxima" fully matching the first two words of "lumos
        # maxima Küche" and silently dropping "Küche" on the floor.
        return _reject("input_not_fully_consumed", None, tok_idx)

    matched_text = text[span_start:span_end] if span_start is not None else ""
    score = sum(ratios) / len(ratios) if ratios else 1.0
    return SegmentMatch(captures=captures, matched_text=matched_text, is_exact=exact, score=score)


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
# on-vs-off tie this guard exists for (e.g. a themed spellbook's
# "entzündet ... auf 100 Prozent stellen" vs "löscht ... ausschalten")
# would never register as opposite, defeating the guard's purpose.
_PERCENT_SET_RE = re.compile(r"\bauf\s+\d+\s*(?:prozent|percent|%)\b", re.IGNORECASE)


# Standalone particles that are ALSO common German articles/prepositions.
# "ein" = "a/one"; "aus" = "out of/from". Only count them as action verbs
# when they end the imperative clause, allowing common polite suffixes
# after German separable-verb particles
# ("Licht ein", "alle Lichter aus"). In the middle of a phrase they are
# almost certainly articles/prepositions, not commands.
_POSITIONAL_PARTICLES: frozenset[str] = frozenset({"ein", "aus"})


def _group_matches(words: set[str], group: set[str], tokens: list[str]) -> bool:
    hits = words & group
    if not hits:
        return False
    definitive = hits - _POSITIONAL_PARTICLES
    if definitive:
        return True
    # Only positional particles hit; accept only if one lands at the end.
    tail = list(tokens)
    while tail and tail[-1] in {"bitte", "please", "jetzt", "now", "danke"}:
        tail.pop()
    return bool(tail) and tail[-1] in hits


def outputs_are_opposite(output_a: str, output_b: str) -> bool:
    toks_a = re.findall(r"\w+", output_a.lower())
    toks_b = re.findall(r"\w+", output_b.lower())
    words_a = set(toks_a)
    words_b = set(toks_b)
    if _PERCENT_SET_RE.search(output_a):
        words_a.add("an")
    if _PERCENT_SET_RE.search(output_b):
        words_b.add("an")
    for group_x, group_y in _OPPOSITE_ACTION_PAIRS:
        if (_group_matches(words_a, group_x, toks_a) and _group_matches(words_b, group_y, toks_b)) or (
            _group_matches(words_a, group_y, toks_a) and _group_matches(words_b, group_x, toks_b)
        ):
            return True
    return False
