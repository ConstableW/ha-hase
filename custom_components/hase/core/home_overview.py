"""Read-only home overview tool for the MCP Bridge.

Every other MCP tool HASE exposes is a Spell. A Spell can switch "the
lights in <area>" or "all lights", but an MCP client (an LLM agent such as
HASEC) had no way to find out which areas exist, which lamps are in them,
or which are on. A request like "turn off all lights except the bedside
lamp" then ends with the agent asking the user for a list of rooms. This
tool answers exactly that question, read-only:

* lists only areas the Bridge's QUERY policy permits (the same
  ScopedAreaPolicy core.executor enforces for entity.get_state), and marks
  each area whose ACTION policy does not permit switching;
* lists only entities the caller already collected as eligible (enabled,
  exposed to Assist/conversation, not a group) - this module never decides
  exposure itself;
* never performs a side effect and never runs a Spell.

No Home Assistant imports - same boundary as the rest of core/. The HA
adapter (spellbooker_ha.collect_home_overview) supplies plain snapshots.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import ScopedAreaPolicy

TOOL_NAME = "home_overview"

# Domains a Spell can act on or report about; anything else is noise for
# the caller and stays out of the listing.
OVERVIEW_DOMAINS: tuple[str, ...] = ("light", "cover", "fan", "media_player", "switch", "vacuum")

MAX_ENTITIES = 400


@dataclass(frozen=True)
class OverviewArea:
    area_id: str
    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class OverviewEntity:
    entity_id: str
    name: str
    state: str
    area_id: str | None

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]


_TEXT = {
    "de": {
        "description": (
            "Nur lesend: listet die Bereiche (Räume) mit ihren Lichtern, Rollos, Lüftern, "
            "Mediaplayern, Schaltern und Saugrobotern samt aktuellem Zustand. Vor Befehlen "
            "wie 'alles aus außer X' oder wenn unklar ist, welcher Bereich gemeint ist, "
            "zuerst hier nachsehen statt den Benutzer zu fragen. Optional `area` (Name "
            "oder Alias) und `domain` zum Eingrenzen."
        ),
        "area": "Optional: nur diesen Bereich (Name oder Alias) zeigen.",
        "domain": "Optional: nur diese Geräteart zeigen.",
        "header": "Bereiche und Geräte (Zustand jetzt):",
        "read_only": " (nur lesen, Schalten hier nicht erlaubt)",
        "aliases": "auch genannt",
        "none_in_area": "keine passenden Geräte",
        "no_area": "Ohne Bereich",
        "nothing": "Keine passenden Bereiche oder Geräte gefunden.",
        "unknown_area": "Bereich '{area}' nicht gefunden oder nicht freigegeben. Bekannte Bereiche: {known}.",
        "truncated": "(Liste gekürzt, mit `area` oder `domain` eingrenzen.)",
        "domains": {
            "light": "Lichter", "cover": "Rollos/Abdeckungen", "fan": "Lüfter",
            "media_player": "Mediaplayer", "switch": "Schalter", "vacuum": "Saugroboter",
        },
        "states": {"on": "an", "off": "aus", "open": "offen", "closed": "zu",
                   "opening": "öffnet", "closing": "schließt", "playing": "spielt",
                   "paused": "pausiert", "idle": "bereit", "unavailable": "nicht erreichbar",
                   "unknown": "unbekannt", "docked": "in Station", "cleaning": "reinigt"},
    },
    "en": {
        "description": (
            "Read-only: lists the areas (rooms) with their lights, covers, fans, media players, "
            "switches and vacuums and their current state. Before commands like 'everything off "
            "except X', or when it is unclear which area is meant, look here first instead of "
            "asking the user. Optional `area` (name or alias) and `domain` narrow the list."
        ),
        "area": "Optional: only show this area (name or alias).",
        "domain": "Optional: only show this kind of device.",
        "header": "Areas and devices (current state):",
        "read_only": " (read-only, switching not permitted here)",
        "aliases": "also called",
        "none_in_area": "no matching devices",
        "no_area": "No area",
        "nothing": "No matching areas or devices found.",
        "unknown_area": "Area '{area}' not found or not permitted. Known areas: {known}.",
        "truncated": "(List truncated, narrow it with `area` or `domain`.)",
        "domains": {
            "light": "Lights", "cover": "Covers", "fan": "Fans",
            "media_player": "Media players", "switch": "Switches", "vacuum": "Vacuums",
        },
        "states": {},
    },
}


def _texts(language: str | None) -> dict:
    key = (language or "en").split("-")[0].lower()
    return _TEXT.get(key, _TEXT["en"])


def tool_description(language: str | None) -> str:
    return _texts(language)["description"]


def input_schema(language: str | None) -> dict:
    t = _texts(language)
    return {
        "type": "object",
        "properties": {
            "area": {"type": "string", "description": t["area"]},
            "domain": {"type": "string", "enum": list(OVERVIEW_DOMAINS), "description": t["domain"]},
        },
        "additionalProperties": False,
    }


def _area_matches(area: OverviewArea, wanted: str) -> bool:
    lowered = wanted.strip().lower()
    return area.name.strip().lower() == lowered or any(a.strip().lower() == lowered for a in area.aliases)


def render_overview(
    areas: list[OverviewArea] | tuple[OverviewArea, ...],
    entities: list[OverviewEntity] | tuple[OverviewEntity, ...],
    policy: ScopedAreaPolicy,
    *,
    language: str | None = "en",
    area: str | None = None,
    domain: str | None = None,
) -> str:
    """Plain-text overview, filtered by the Bridge's query policy.

    Entities without an area are only listed when the query policy is
    unrestricted - an area-restricted Bridge never sees devices it cannot
    place inside one of its permitted areas."""
    t = _texts(language)
    if domain is not None and domain not in OVERVIEW_DOMAINS:
        domain = None
    visible = [a for a in areas if policy.queries.permits_area(a.area_id)]
    visible.sort(key=lambda a: a.name.lower())
    if area:
        chosen = [a for a in visible if _area_matches(a, area)]
        if not chosen:
            known = ", ".join(a.name for a in visible) or "-"
            return t["unknown_area"].format(area=area, known=known)
        visible = chosen

    by_area: dict[str | None, list[OverviewEntity]] = {}
    for e in entities:
        if e.domain not in OVERVIEW_DOMAINS or (domain and e.domain != domain):
            continue
        by_area.setdefault(e.area_id, []).append(e)

    lines = [t["header"]]
    count = 0
    truncated = False
    groups: list[tuple[str, str | None, bool]] = [
        (a.name + (f" ({t['aliases']}: {', '.join(a.aliases)})" if a.aliases else ""), a.area_id,
         policy.actions.permits_area(a.area_id))
        for a in visible
    ]
    if not area and policy.queries.unrestricted and by_area.get(None):
        groups.append((t["no_area"], None, policy.actions.unrestricted))

    for title, area_id, may_act in groups:
        members = sorted(by_area.get(area_id, ()), key=lambda e: (e.domain, e.name.lower()))
        if area_id is None and not members:
            continue
        lines.append(f"- {title}{'' if may_act else t['read_only']}")
        if not members:
            lines.append(f"  {t['none_in_area']}")
            continue
        current_domain = None
        parts: list[str] = []
        for e in members:
            if count >= MAX_ENTITIES:
                truncated = True
                break
            if e.domain != current_domain:
                if parts:
                    lines.append(f"  {t['domains'][current_domain]}: {', '.join(parts)}")
                current_domain, parts = e.domain, []
            state = t["states"].get(e.state, e.state)
            parts.append(f"{e.name} [{e.entity_id}] {state}")
            count += 1
        if parts:
            lines.append(f"  {t['domains'][current_domain]}: {', '.join(parts)}")
        if truncated:
            break
    if len(lines) == 1:
        return t["nothing"]
    if truncated:
        lines.append(t["truncated"])
    return "\n".join(lines)
