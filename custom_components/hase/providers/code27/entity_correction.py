"""CODE27 entity-name correction against Assist-exposed registry names."""
from __future__ import annotations

import difflib
from typing import TYPE_CHECKING

from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ...core.matching import fuzzy_threshold, tokenize_with_spans

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_CONNECTOR_STOPWORDS = {
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",
    "und", "auf", "im", "in", "am", "an", "zu", "bitte", "mal",
    "the", "a", "to", "and", "on", "at", "of", "please",
}

def _registry_names(hass: HomeAssistant, include_areas: bool) -> list[str]:
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
    hass: HomeAssistant, phrase: str, include_areas: bool, exclude: set[str]
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
    hass: HomeAssistant,
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
    hass: HomeAssistant, phrase: str, include_areas: bool
) -> tuple[str, float] | None:
    """Does `phrase` itself closely match a real entity/area name? Unlike
    find_correction_candidate, an exact self-match is not excluded -
    this checks whether a user's reply *is* a valid name, rather than
    searching for an alternative to something that already failed."""
    return _best_matching_name(hass, phrase, include_areas, exclude=set())


def extract_named_phrase(
    hass: HomeAssistant, text: str, include_areas: bool, max_window: int = 4
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
