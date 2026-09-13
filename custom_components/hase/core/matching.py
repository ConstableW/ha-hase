"""Pure trigger-template matching: tokenize a message, fuzzy-match a
compiled trigger's literal segments against it, let placeholders
greedily consume whatever's between them.

Deliberately self-contained - no import from custom_components.hase
anywhere in this package, not even its already-pure parts, so `core/`
never depends on the legacy integration module's internals. The
approach mirrors spellbook.py's proven matcher (tokenize_with_spans,
fuzzy_threshold, _compile_trigger, _match_segments); that module stays
untouched, this is a from-scratch reimplementation for the new
engine's own command schema.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

# ("lit", "some words") - a literal phrase to fuzzy-match, or
# ("ph", "name") - a placeholder that greedily captures tokens.
Segment = tuple[str, str]


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
    text. Ported verbatim from spellbook.py's proven original (kept
    here instead of imported from it - core/ stays independent of
    that module's internals, see core/matching.py's own module
    docstring)."""
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


def compile_trigger(trigger_text: str) -> list[Segment] | None:
    """Split a trigger string into literal-phrase/placeholder segments.
    Returns None if two placeholders are directly adjacent with no
    literal text between them - the boundary would be ambiguous."""
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


@dataclass(frozen=True)
class SegmentMatch:
    captures: dict[str, str]
    matched_text: str
    is_exact: bool
    score: float


def match_segments(
    segments: list[Segment], tokens: list[tuple[str, int, int]], text: str
) -> SegmentMatch | None:
    """Walk a compiled trigger's segments against the tokenized message.
    A placeholder greedily consumes the tokens between the previous and
    next literal match (or to the end of the message for a trailing
    placeholder) and must consume at least one token.

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
    literal match at a position other than the one already reached."""
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
                # Found further ahead than where we already are - that
                # means one or more tokens right here were silently
                # skipped, not consumed by anything. Never accept that.
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
        # Trailing tokens nothing in the trigger accounted for - e.g.
        # "lumos maxima" fully matching the first two words of "lumos
        # maxima Küche" and silently dropping "Küche" on the floor.
        return None

    matched_text = text[span_start:span_end] if span_start is not None else ""
    score = sum(ratios) / len(ratios) if ratios else 1.0
    return SegmentMatch(captures=captures, matched_text=matched_text, is_exact=exact, score=score)
