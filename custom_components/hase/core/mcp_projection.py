"""Spell -> MCP tool projection - HASE MCP architecture, Phase 1.

Turns an already-loaded EngineSpellbook plus one MCP Bridge's own context
(speaking-device area, action/query AreaPolicy, language) into MCP tool
descriptors: one per projectable Spell, with a tool name, a routing-
instruction description, and an inputSchema whose shape depends on BOTH the
Spell and the calling Bridge's context.

No Home Assistant or provider imports - same boundary rule as the rest of
`core/` (see docs/HASE-ARCHITECTURE.md). A Bridge is represented here purely
as BridgeContext (an area name plus the exact ScopedAreaPolicy the executor
itself will enforce) - never as any provider- or HA-specific config shape,
and never as a second authorization model: every Bridge-context decision
made here is read-only and is re-checked for real by core.executor at call
time regardless of what this module projected.

This module answers exactly one question: "given this Spellbook and this
Bridge, what does tools/list return, and how does a tools/call's arguments
map back onto the plain `captures` dict build_draft/build_query_plan/
build_step_plan already consume unchanged?" It never resolves a target,
never talks to Home Assistant, and never reimplements matching, planning or
execution - see project_spellbook()/project_command() for projection and
build_captures() for the (equally pure) reverse mapping Phase 2's execution
adapter needs.

Every typed AND every text-only Spell in the assigned Spellbook is
projectable in principle - this module does not maintain an MCP-specific
allowlist or blocklist of Spell kinds (expert-only and template-targeted
Spells project exactly like any other; core.executor's own expert-tier
gating is what authorizes them at call time, unchanged). A text-only Spell
(no operation/sources/steps, only `output` - see _project_text_only)
projects as a real, callable tool that always performs the Spell's
configured function, never one that merely echoes text back - a fixed/
templated answer (assist_redirect=False) is read-only; a command handed to
Home Assistant's own configured conversation agent for real execution
(assist_redirect=True) is not, since it may perform a device action. This
is WIDER than core.spellbook_engine.match()'s own typed_only visibility for
SATELLITE/voice execution, deliberately: an MCP client can act on a tool's
result in ways spoken/typed voice has no equivalent for. A Spell is only
skipped, as a ProjectionSkip, when it is structurally impossible to derive
a coherent tool from - see ProjectionSkip's own docstring for the
enumerated reasons.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .book_languages import text as resolve_localized
from .models import AreaPolicy, ScopedAreaPolicy
from .operations import OPERATION_CATALOG
from .spellbook_engine import EngineCommand, EngineSpellbook, duplicate_spell_ids

# ---------------------------------------------------------------------------
# Bridge context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BridgeArea:
    """A Bridge's own speaking-device area, resolved from Home Assistant's
    area registry - area_id and area_name are NEVER interchangeable, and
    this module never collapses them into one ambiguous string (Phase 3's
    binding correction):

    - `area_id` is what source_area_id and any area_id-kind capture need -
      core.executor resolves an area_id-kind target by a direct registry
      id lookup (see executor.py's own `_execute`: a source_area target
      becomes `ActionTarget(kind="area_id", value=source_area_id)`
      verbatim).
    - `area_name` is what an area_name-kind capture's CASE A/C implicit
      default needs - that capture resolves via name/alias matching
      (spellbooker_ha.py's `_find_area_by_name`), not by id.

    Both fields are always a real Home Assistant area's id/name together -
    never independently missing. See core.mcp_bridge_context for how this
    is resolved (and how an invalid/deleted area_id is reported instead of
    silently downgrading the Bridge to area-less)."""

    area_id: str
    area_name: str


@dataclass(frozen=True)
class BridgeContext:
    """Everything projection needs to know about the calling MCP Bridge -
    never more. `area` is the Bridge's own speaking-device area (None for a
    "master" Bridge with no area of its own - see the architecture
    assessment's CASE A/B/C). `policy` is the SAME ScopedAreaPolicy
    core.executor will enforce at call time; projection only ever reads it,
    to decide whether the Bridge's own area can serve as an implicit
    default for an omitted argument - it is never a second authorization
    model, and an implicit default this module chooses is re-checked by the
    real AreaPolicy at execution exactly like any explicitly-typed target.
    `language` selects which trigger/alias text becomes the routing
    description; callers fall back to their own default (e.g.
    hass.config.language) before constructing this, the same way
    conversation.py already does for spoken requests."""

    area: BridgeArea | None
    policy: ScopedAreaPolicy
    language: str = "en"


# ---------------------------------------------------------------------------
# Projected argument / tool
# ---------------------------------------------------------------------------

JsonType = Literal["string", "integer", "number"]
# No "boolean" here - see _value_argument's own note on why a
# true_values/false_values capture projects as a string enum of the
# accepted words instead of a native JSON boolean.


@dataclass(frozen=True)
class McpArgument:
    """One inputSchema property, and how to turn a supplied (or omitted)
    MCP argument back into an engine capture value. `capture_name` is
    exactly the key build_draft/build_query_plan/build_step_plan's
    `captures` dict expects - the MCP property name is always that same
    string, so there is no separate name-mapping table to keep in sync."""

    capture_name: str
    json_type: JsonType
    description: str
    required: bool
    enum: tuple[str, ...] | None = None
    minimum: float | None = None
    maximum: float | None = None
    # Set only for an omittable area capture (CASE C) - the value to
    # synthesize into captures[capture_name] when the MCP caller doesn't
    # supply this argument, exactly as if the user had spoken the Bridge's
    # own area. None for every other argument: a genuine user capture
    # (arguments_spec) has no Bridge-derived default, and an entity_name/
    # entity_id capture has no natural single-entity default either.
    implicit_default: str | None = None


@dataclass(frozen=True)
class McpToolProjection:
    spell_id: str
    # Always == spell_id - the architecture assessment is explicit that
    # tool names are Bridge-local, never globally disambiguated (no
    # "hase_lumos", no area/credential prefix). Kept as its own field
    # anyway so a caller never has to know that equivalence holds.
    name: str
    description: str
    input_schema: dict[str, Any]
    arguments: tuple[McpArgument, ...]
    # CASE A captures - omitted from inputSchema/arguments entirely (see
    # _area_argument's own CASE A branch), but their value still has to
    # reach build_draft/build_query_plan/build_step_plan's `captures` dict
    # somehow: this is that carrier. Never populated for a capture that
    # DOES appear in `arguments` (required or optional) - those already
    # carry their own value (explicit or, for CASE C, McpArgument.
    # implicit_default) through the normal build_captures() path.
    implicit_captures: dict[str, str] = field(default_factory=dict)
    # True for a Spell whose call performs no Home Assistant action at all
    # - a flat query (sources, no steps), a Combined Spell whose every
    # step is a query, or a text-only Spell (see _project_text_only).
    # Surfaced to MCP clients as ToolAnnotations.read_only_hint
    # (providers/mcp/transport.py) - purely an advisory hint for the
    # calling agent, never a second enforcement layer; core.executor's
    # own action/query AreaPolicy split is what actually decides what a
    # call may do, unchanged by this flag either way.
    read_only: bool = False


@dataclass(frozen=True)
class ProjectionSkip:
    """A Spell that exists in the book but could not be represented as one
    coherent MCP tool - reported, never silently dropped and never forced
    into a misleading shape. Three reasons, all genuine structural problems
    with the Spell definition itself, not a category this module chooses to
    exclude (assist_redirect is NOT one of these reasons - both
    assist_redirect values project as a real, callable tool, see
    _project_text_only):

    - "unknown_operation:<name>": the Spell's operation isn't in
      core.operations.OPERATION_CATALOG at all, so this module cannot even
      tell whether its target belongs to the action or the query policy
      view - and core.executor would reject any call to it as UNSUPPORTED
      regardless, so listing it as a callable tool would only mislead.
    - "conflicting_capture_definition:<names>": the same {placeholder} name
      is used in two structurally incompatible roles within one command
      (e.g. once as an area target, once as a typed numeric argument) -
      nothing in the spellbook schema forbids an author from doing this,
      but it cannot be projected as one JSON Schema property.
    - "unresolvable_text": a text-only Spell (no operation/sources/steps)
      whose `output` doesn't resolve to real text in ANY language at all
      (core.book_languages.text()'s own fallback chain exhausted) - the
      loader already guarantees `output` is present for such a Spell, but
      not that it resolves; see _project_text_only.
    """

    spell_id: str
    reason: str


# ---------------------------------------------------------------------------
# Per-language routing/argument text
# ---------------------------------------------------------------------------
# Infrastructure boilerplate, not Spellbook content - the same category of
# hardcoded user-facing string core.router already carries (e.g.
# DEFAULT_NO_MATCH_RESPONSE). English/German only, English as the fallback
# for anything else, mirroring docs/SPELLBOOK-STUDIO.md's own documented
# "missing languages fall back to English" rule - never a per-Spellbook or
# per-capture translation table, which no spellbook data provides anyway.
#
# Deliberately factual, never imperative ("MUSS ausgeführt werden, wenn...").
# This text lands in inputSchema/description fields an MCP client's own LLM
# reads as passive metadata about a tool, not as an instruction directed at
# it - phrasing it as a command ("you MUST call this when...") reads too
# close to an instruction embedded in tool data, which is exactly what an
# MCP client's own prompt-injection defenses are supposed to be suspicious
# of. Stating what the tool DOES, and offering the matching voice phrase
# only as a labeled example, keeps the decision of whether/when to call it
# entirely with the calling agent - HASE is not published as a real HA
# integration or exposed to strangers' agents, but the wording should not
# rely on that for its safety property.

_ROUTING_TEXT = {
    "de": {
        "example": " Beispiel-Sprachbefehl: {phrasings}.",
        "join_or": " oder ",
        "default_note": " Ohne genannten Wert wird der Bereich dieses Geräts verwendet.",
    },
    "en": {
        "example": " Example voice command: {phrasings}.",
        "join_or": " or ",
        "default_note": " If no value is named, this device's own area is used.",
    },
}

_ARG_TEXT = {
    "de": {
        "area_required": (
            "Der vom Benutzer ausdrücklich genannte Bereich. Nur einen Bereich verwenden, den der "
            "Benutzer tatsächlich genannt hat - keinen Bereich erfinden oder raten."
        ),
        "area_optional": (
            "Optionaler, vom Benutzer ausdrücklich genannter Bereich. Nur einen Bereich verwenden, den "
            "der Benutzer tatsächlich genannt hat - keinen Bereich erfinden oder raten."
        ),
        "entity": "Der vom Benutzer ausdrücklich genannte Name.",
        "value": "Der vom Benutzer genannte Wert.",
        "text_placeholder": "Der vom Benutzer genannte Wert für diesen Teil der Formulierung.",
    },
    "en": {
        "area_required": (
            "The area explicitly named by the user. Only use an area the user actually named - never "
            "invent or guess one."
        ),
        "area_optional": (
            "Optional area explicitly named by the user. Only use an area the user actually named - "
            "never invent or guess one."
        ),
        "entity": "The name explicitly given by the user.",
        "value": "The value given by the user.",
        "text_placeholder": "The value the user gave for this part of the phrase.",
    },
}

# What each operation actually DOES, for the tool description's opening
# sentence - reused, never re-derived: for every operation with a plain
# (domain, service) tuple in OPERATION_CATALOG, the sentence is generated
# straight from that real Home Assistant service name (single source of
# truth, core.executor's own dispatch reads the exact same tuple). Only the
# handful of operations core.executor's own _dispatch_action already
# special-cases with bespoke dispatch logic (no service tuple to read a
# name from) get a hand-written sentence here - the same fixed small set,
# never a duplicated general-purpose catalog.
_BESPOKE_EFFECT_TEXT = {
    "light.set_power": {
        "de": "Schaltet Licht ein oder aus, oder stellt die Helligkeit ein.",
        "en": "Turns lights on or off, or sets their brightness.",
    },
    "media_player.skip_track": {
        "de": "Springt zum nächsten oder vorherigen Titel.",
        "en": "Skips to the next or previous track.",
    },
    "entity.get_state": {
        "de": "Fragt den aktuellen Zustand einer Entität ab.",
        "en": "Reports the current state of one entity.",
    },
    "vacuum.clean_area": {
        "de": "Schickt einen Saugroboter zur Reinigung eines Bereichs los.",
        "en": "Sends a vacuum robot to clean an area.",
    },
}

_QUERY_EFFECT_TEXT = {
    "de": "Liefert eine berechnete Statusantwort - ändert nichts an Home Assistant.",
    "en": "Reports a computed status answer - does not change anything in Home Assistant.",
}
_COMBINED_EFFECT_TEXT = {
    "de": "Führt mehrere Schritte aus (Aktionen und/oder Statusabfragen) - siehe Beispiel-Sprachbefehl.",
    "en": "Runs several steps (actions and/or status queries) - see the example voice command.",
}
_TEXT_ONLY_EFFECT_TEXT = {
    "de": "Liefert einen vordefinierten Text - führt keine Home-Assistant-Aktion aus.",
    "en": "Returns a predefined text response - performs no Home Assistant action.",
}
_ASSIST_REDIRECT_EFFECT_TEXT = {
    "de": (
        "Leitet den Befehl zur echten Ausführung an den konfigurierten Home-Assistant-Assistenten weiter - "
        "kann eine Geräteaktion auslösen."
    ),
    "en": (
        "Forwards the command to the configured Home Assistant conversation agent for real execution - "
        "may perform a device action."
    ),
}
_GENERIC_EFFECT_TEXT = {"de": "Home-Assistant-Aktion.", "en": "Home Assistant action."}


def _effect_text(command: EngineCommand, language: str) -> str:
    """The factual opening sentence of a tool's description: what calling
    it actually does, derived from the SAME OPERATION_CATALOG core.executor
    dispatches through - never a second, hand-authored copy of what each
    operation means. A query (sources, no steps) or Combined Spell (steps)
    gets a generic sentence instead, since neither maps to one single
    operation the way a plain single-operation command does. A text-only
    Spell (no operation/sources/steps - see _project_text_only) gets its
    own sentence, making explicit that it returns fixed/templated text and
    never touches Home Assistant at all."""
    key = _lang_key(language)
    if command.steps:
        return _COMBINED_EFFECT_TEXT[key]
    if command.sources:
        return _QUERY_EFFECT_TEXT[key]
    if command.operation is None:
        return _ASSIST_REDIRECT_EFFECT_TEXT[key] if command.assist_redirect else _TEXT_ONLY_EFFECT_TEXT[key]
    bespoke = _BESPOKE_EFFECT_TEXT.get(command.operation)
    if bespoke is not None:
        return bespoke[key]
    spec = OPERATION_CATALOG.get(command.operation)
    if spec is not None and spec.service is not None:
        domain, service = spec.service
        return (
            f"Ruft den Home-Assistant-Dienst `{domain}.{service}` auf."
            if key == "de"
            else f"Calls Home Assistant's `{domain}.{service}` service."
        )
    # Unreachable in practice - project_command already turns an unknown
    # operation into a ProjectionSkip before _build_description is ever
    # called - kept only as a defensive fallback, never silently blank.
    return _GENERIC_EFFECT_TEXT[key]


def _lang_key(language: str | None) -> str:
    key = (language or "en").split("-")[0].lower()
    return key if key in _ARG_TEXT else "en"


# ---------------------------------------------------------------------------
# Per-command target/argument walk
# ---------------------------------------------------------------------------


def _is_projectable(command: EngineCommand) -> bool:
    """Whether the OUTER (user-tier) shape of this command gives MCP
    anything to turn into a tool at all - the same four shapes
    core.spellbook_engine's own loader already requires at least one of
    (see _load_base_command: operation, sources, steps, or output). A
    command with only `output` (no operation/sources/steps) is NOT
    invisible to MCP the way it is to SATELLITE/voice's own typed_only
    filter (core.spellbook_engine.match()) - MCP turns it into a
    read-only text tool instead (see _project_text_only), since an MCP
    client can act on a returned text answer in ways spoken/typed voice
    execution has no equivalent for. `command.expert_command` never needs
    checking separately here: it can only be set when the outer command
    itself already had operation/output/sources/steps (see
    core.spellbook_engine._load_command - `user` is only truthy, and
    therefore expert_command only ever gets attached, when one of those
    four raw fields was present at the top level)."""
    return command.operation is not None or bool(command.sources) or bool(command.steps) or command.output is not None


def _operation_view(operation: str) -> Literal["action", "query"] | None:
    spec = OPERATION_CATALOG.get(operation)
    return spec.kind if spec is not None else None


def _iter_specs(
    command: EngineCommand,
) -> tuple[list[tuple[dict[str, Any], str]], list[dict[str, Any]]] | str:
    """Every target_spec (tagged with which policy view - "action" or
    "query" - governs it) and every capturing arguments_spec entry a
    command can contain, across all three shapes a typed command may take
    (a single operation, a multi-source query, or a Combined Spell's
    steps) - or a `"unknown_operation:<name>"` skip reason. Reuses
    core.executor's own action/query classification (OPERATION_CATALOG)
    rather than inventing a second one.

    Only the OUTER (user-tier) command is walked - command.expert_command,
    when set, is a hidden execution-time variant/fallback pair
    (core.spellbook_engine.build_draft already resolves it), never a
    second externally-addressable shape. An expert-only command (no user
    tier at all) has no expert_command and is walked normally: its OWN
    target_spec/arguments_spec are exactly what's projected, unchanged -
    core.executor's existing expert-tier gating is what authorizes calling
    it, not this module.
    """
    targets: list[tuple[dict[str, Any], str]] = []
    arguments: list[dict[str, Any]] = []

    if command.sources:
        for source in command.sources:
            targets.append((source["target"], "query"))
        return targets, arguments

    if command.steps:
        for step in command.steps:
            if step["kind"] == "action":
                view = _operation_view(step["operation"])
                if view is None:
                    return f"unknown_operation:{step['operation']}"
                targets.append((step["target"], view))
                arguments.extend(
                    spec for spec in step["arguments"].values() if isinstance(spec, dict) and "capture" in spec
                )
            else:
                for source in step["sources"]:
                    targets.append((source["target"], "query"))
        return targets, arguments

    view = _operation_view(command.operation)
    if view is None:
        return f"unknown_operation:{command.operation}"
    targets.append((command.target_spec, view))
    arguments.extend(
        spec for spec in command.arguments_spec.values() if isinstance(spec, dict) and "capture" in spec
    )
    return targets, arguments


# ---------------------------------------------------------------------------
# One capture -> McpArgument | None
# ---------------------------------------------------------------------------


def _area_argument(
    capture_name: str, policy: AreaPolicy, bridge_area: BridgeArea | None, language: str, *, target_kind: str,
) -> McpArgument | None:
    """CASE A/B/C from the architecture assessment, for one area_name/
    area_id capture under one policy view. Reuses AreaPolicy.permits_area
    directly rather than re-deriving membership - no parallel policy logic
    - and ALWAYS checks membership against bridge_area.area_id, since
    AreaPolicy.allowed_areas holds Home Assistant area ids throughout HASE
    (core.executor's own `_entity_permitted` checks `entity.area_id`, never
    a display name). `target_kind` (the capture's own declared "area_name"
    or "area_id") picks which of BridgeArea's two fields gets INJECTED as
    the implicit default value - membership and the injected value are
    deliberately two separate questions, only the second one depends on
    target_kind."""
    texts = _ARG_TEXT[_lang_key(language)]
    if bridge_area is None:
        # CASE B: no Bridge-owned area exists at all - nothing to default to.
        return McpArgument(capture_name, "string", texts["area_required"], required=True)
    injected_value = bridge_area.area_name if target_kind == "area_name" else bridge_area.area_id
    if policy.unrestricted or (policy.allowed_areas is not None and len(policy.allowed_areas) > 1):
        # CASE C: the Bridge's own area is a usable default, but other
        # areas are genuinely reachable too - a real choice, so the
        # argument stays visible, just optional.
        return McpArgument(capture_name, "string", texts["area_optional"], required=False, implicit_default=injected_value)
    if policy.permits_area(bridge_area.area_id):
        # CASE A: the Bridge's own area is the ONLY area this policy view
        # could ever permit - offering the argument at all would only
        # invite a value that can never be anything but itself or a denial.
        return None
    # The Bridge's own area isn't even permitted by its own policy - a
    # misconfiguration upstream of MCP projection. Failing toward
    # "required, no default offered" is the safe choice: synthesizing a
    # default guaranteed to be denied would silently look like success to
    # the calling LLM until execution actually rejects it.
    return McpArgument(capture_name, "string", texts["area_required"], required=True)


def _entity_argument(capture_name: str, language: str) -> McpArgument:
    # No Bridge-derived default exists for "which entity" - always a real,
    # required user capture, for every Bridge context.
    return McpArgument(capture_name, "string", _ARG_TEXT[_lang_key(language)]["entity"], required=True)


def _value_argument(value_spec: dict[str, Any], language: str) -> McpArgument | None:
    """A genuine arguments_spec capture (core.spellbook_engine._resolve_
    arguments' own int/float/value_map/true-false-words shapes) - always
    required, for every Bridge: there is no context-derived default for
    "what temperature did the user ask for".

    true_values/false_values projects as a STRING enum of the accepted
    words, not a native JSON boolean - a projection semantic that could not
    be represented cleanly as `type: boolean`. The underlying capture is a
    WORD choice (an author-defined vocabulary - "an"/"aus", "on"/"off",
    anything), not a protocol-level true/false; collapsing multiple
    synonym words down to one canonical boolean per branch would be lossy,
    and a JSON `true`/`false` value has no principled mapping back onto an
    arbitrary word list at all. A string enum of exactly the words the
    Spell already accepts is the honest, information-preserving projection,
    and needs no special-casing at all in build_captures() below - it
    passes through exactly like a value_map capture already does.
    """
    if not isinstance(value_spec, dict) or "capture" not in value_spec:
        return None
    capture_name = value_spec["capture"]
    if not isinstance(capture_name, str):
        return None
    description = _ARG_TEXT[_lang_key(language)]["value"]
    conversion_type = value_spec.get("type")
    if conversion_type == "int":
        return McpArgument(capture_name, "integer", description, required=True,
                            minimum=value_spec.get("min"), maximum=value_spec.get("max"))
    if conversion_type == "float":
        return McpArgument(capture_name, "number", description, required=True,
                            minimum=value_spec.get("min"), maximum=value_spec.get("max"))
    if "value_map" in value_spec:
        enum = tuple(sorted(value_spec["value_map"]))
        return McpArgument(capture_name, "string", description, required=True, enum=enum)
    true_values = tuple(value_spec.get("true_values", ()))
    false_values = tuple(value_spec.get("false_values", ()))
    enum = tuple(sorted({*true_values, *false_values}))
    return McpArgument(capture_name, "string", description, required=True, enum=enum)


def _merge_area_facts(facts: list[McpArgument | None]) -> McpArgument | None:
    """Merge every occurrence of ONE area capture name across a command's
    several targets (e.g. the same {area} used by two Combined-Spell
    steps, possibly under different policy views). Priority is
    REQUIRED > OPTIONAL > OMIT: an occurrence that genuinely needs a real
    value can never be satisfied by omitting the argument, so the merged
    result must ask for it; only when every single occurrence agrees the
    Bridge's own area is the sole possible value does the argument
    disappear entirely. A required/optional McpArgument's own fields are
    identical across occurrences for a fixed (capture_name, bridge_area,
    language) - only which policy produced it differs - so picking any
    representative loses no information."""
    required = next((f for f in facts if f is not None and f.required), None)
    if required is not None:
        return required
    return next((f for f in facts if f is not None), None)


# ---------------------------------------------------------------------------
# Description text
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _trigger_phrasings(command: EngineCommand, language: str) -> tuple[str, ...]:
    """Every phrasing this Bridge's language resolves for this command -
    the primary trigger plus every declared alias - mechanically read from
    the schema fields Studio already validates, never hand-authored per
    Spellbook."""
    phrasings: list[str] = []
    primary = resolve_localized(command.trigger, language, command.default_language)
    if primary:
        phrasings.append(primary)
    for alias in resolve_localized(command.aliases, language, command.default_language, []) or ():
        if alias and alias not in phrasings:
            phrasings.append(alias)
    return tuple(phrasings)


def _render_phrasing(trigger_text: str, stripped: frozenset[str]) -> str:
    """One trigger phrasing for the description: a capture in `stripped`
    (Bridge-defaulted or otherwise contextual) is removed entirely - the
    Bridge already fills it in, so the phrasing reads as what the user
    would actually need to say through THIS Bridge. Every other capture is
    rendered as <capture_name>: a generic, mechanical placeholder tied
    directly to the same name used in inputSchema, rather than inventing a
    translated label (e.g. "<Bereich>") no spellbook data provides."""
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return "" if name in stripped else f"<{name}>"

    rendered = _PLACEHOLDER_RE.sub(replace, trigger_text)
    return re.sub(r"\s+", " ", rendered).strip()


def _join_quoted(phrasings: tuple[str, ...], join_or: str) -> str:
    quoted = [f"'{p}'" for p in phrasings]
    if len(quoted) == 1:
        return quoted[0]
    return ", ".join(quoted[:-1]) + join_or + quoted[-1]


def _build_description(command: EngineCommand, language: str, omitted: set[str], optional: set[str]) -> str:
    texts = _ROUTING_TEXT[_lang_key(language)]
    phrasings: list[str] = []
    for trigger_text in _trigger_phrasings(command, language):
        strip_sets = [omitted | optional] if optional else [omitted]
        if optional:
            # CASE C: both the bare form (everything optional/contextual
            # stripped) and the explicit form (only contextual stripped)
            # are genuine ways to invoke this tool through this Bridge -
            # "Scourgify" or "Scourgify <area>".
            strip_sets.append(omitted)
        for strip_set in strip_sets:
            rendered = _render_phrasing(trigger_text, strip_set)
            if rendered and rendered not in phrasings:
                phrasings.append(rendered)
    if not phrasings:
        phrasings = [command.spell_id]
    # A factual effect sentence first, the matching voice phrase only as a
    # labeled example second - never framed as an instruction to call this
    # (see this module's own note above _ROUTING_TEXT).
    description = _effect_text(command, language) + texts["example"].format(
        phrasings=_join_quoted(tuple(phrasings), texts["join_or"])
    )
    if optional:
        description += texts["default_note"]
    return description


def _order_like_trigger(command: EngineCommand, language: str, arguments: list[McpArgument]) -> list[McpArgument]:
    """Deterministic, human-sensible ordering: as first named in the
    primary trigger text, then any remaining capture (a Combined-Spell
    step's own target/argument, which the flat top-level trigger never
    mentions) alphabetically."""
    primary = resolve_localized(command.trigger, language, command.default_language) or ""
    order = [m.group(1) for m in _PLACEHOLDER_RE.finditer(primary)]
    by_name = {a.capture_name: a for a in arguments}
    ordered = [by_name.pop(name) for name in order if name in by_name]
    ordered.extend(sorted(by_name.values(), key=lambda a: a.capture_name))
    return ordered


def _build_input_schema(arguments: list[McpArgument]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for arg in arguments:
        prop: dict[str, Any] = {"type": arg.json_type, "description": arg.description}
        if arg.enum is not None:
            prop["enum"] = list(arg.enum)
        if arg.minimum is not None:
            prop["minimum"] = arg.minimum
        if arg.maximum is not None:
            prop["maximum"] = arg.maximum
        properties[arg.capture_name] = prop
        if arg.required:
            required.append(arg.capture_name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _project_text_only(command: EngineCommand, bridge: BridgeContext) -> McpToolProjection | ProjectionSkip:
    """A Spell with no operation/sources/steps - only `output` (the
    loader already guarantees one of the four is present, see
    _is_projectable). Every {placeholder} in the PRIMARY trigger becomes a
    required plain string argument - there is no target_spec/
    arguments_spec to consult for a text-only command (core.spellbook_
    engine's own loader never validates either field unless an operation
    is present), so the trigger's own placeholders are the only real
    source of "what does the caller need to supply", exactly as
    core.spellbook_engine.find_command()'s own inline text-only rendering
    already relies on trigger-pattern captures alone. No area/entity
    semantics apply here at all - a text-only Spell has no target, so
    there is nothing to invent an area for in the first place.

    command.assist_redirect (models.DEFAULT_ASSIST_REDIRECT) decides WHAT
    calling this tool does, never WHETHER it is projected - both values
    are genuinely callable tools, always executing the Spell's configured
    function (see core.text_only_execution._execute_assist_redirect), never
    merely returning `output`'s raw text back to the caller as if reading
    it were the same as performing it:
      - False: `output` is a fixed/templated answer HASE renders itself.
      - True: `output` is a command phrase handed to Home Assistant's own
        configured conversation agent for REAL interpretation/execution -
        the same capability providers.code27.bridge._forward_to_assist
        already has. This MAY perform a device action, so unlike the
        False case it is never advertised as read-only (see `read_only`
        below); the real authorization check (is this Bridge's own policy
        even allowed to reach Assist at all) happens at call time, not
        here - projection is shape only, exactly like every other command
        kind (see this module's own top-of-file docstring)."""
    trigger_text = resolve_localized(command.trigger, bridge.language, command.default_language)
    if not trigger_text:
        return ProjectionSkip(command.spell_id, "unresolvable_text")
    texts = _ARG_TEXT[_lang_key(bridge.language)]
    placeholder_names = list(dict.fromkeys(_PLACEHOLDER_RE.findall(trigger_text)))
    arguments = [
        McpArgument(name, "string", texts["text_placeholder"], required=True) for name in placeholder_names
    ]
    return McpToolProjection(
        spell_id=command.spell_id,
        name=command.spell_id,
        description=_build_description(command, bridge.language, omitted=set(), optional=set()),
        input_schema=_build_input_schema(arguments),
        arguments=tuple(arguments),
        # A genuine fixed answer (assist_redirect=False) changes nothing
        # in Home Assistant - read-only. An assist_redirect=True tool MAY
        # perform a real device action (Home Assistant's own configured
        # conversation agent decides, not HASE) - never read-only, even
        # though many callers will in practice only ask a question with
        # it; "may" is enough to withhold the hint (see
        # McpToolProjection.read_only's own docstring: advisory only,
        # never a second enforcement layer, so erring toward "not
        # read-only" here costs nothing but a slightly more cautious
        # calling agent).
        read_only=not command.assist_redirect,
    )


def project_command(command: EngineCommand, bridge: BridgeContext) -> McpToolProjection | ProjectionSkip:
    if command.operation is None and not command.sources and not command.steps:
        return _project_text_only(command, bridge)
    result = _iter_specs(command)
    if isinstance(result, str):
        return ProjectionSkip(command.spell_id, result)
    target_specs, argument_specs = result

    area_facts: dict[str, list[McpArgument | None]] = {}
    area_kinds: dict[str, set[str]] = {}
    entity_facts: dict[str, McpArgument] = {}

    for target_spec, view in target_specs:
        kind = target_spec.get("kind")
        capture_name = target_spec.get("capture")
        if not isinstance(capture_name, str):
            # No capture at all: source_area / all_allowed / none /
            # entity_ids / template, or a fixed `value` - HASE already
            # knows this from the Spell/Bridge; never an MCP argument.
            continue
        if kind in ("area_name", "area_id"):
            policy = bridge.policy.actions if view == "action" else bridge.policy.queries
            area_facts.setdefault(capture_name, []).append(
                _area_argument(capture_name, policy, bridge.area, bridge.language, target_kind=kind)
            )
            area_kinds.setdefault(capture_name, set()).add(kind)
        elif kind in ("entity_name", "entity_id"):
            arg = _entity_argument(capture_name, bridge.language)
            if capture_name in entity_facts and entity_facts[capture_name] != arg:
                return ProjectionSkip(command.spell_id, f"conflicting_capture_definition:{capture_name}")
            entity_facts[capture_name] = arg
        # Every other kind (all_allowed/none/source_area/entity_ids/
        # template) has no `capture` key and never reaches here.

    value_facts: dict[str, McpArgument] = {}
    for value_spec in argument_specs:
        arg = _value_argument(value_spec, bridge.language)
        if arg is None:
            continue
        if arg.capture_name in value_facts and value_facts[arg.capture_name] != arg:
            return ProjectionSkip(command.spell_id, f"conflicting_capture_definition:{arg.capture_name}")
        value_facts[arg.capture_name] = arg

    clashing = (set(area_facts) & set(entity_facts)) | (set(area_facts) & set(value_facts)) | (set(entity_facts) & set(value_facts))
    if clashing:
        # One physical {placeholder} name used as two structurally
        # incompatible roles (e.g. an area target here, a typed numeric
        # argument there) - the schema/arguments_spec allow authoring this
        # even though it can never be projected as one JSON Schema
        # property. See ProjectionSkip's own docstring.
        return ProjectionSkip(command.spell_id, f"conflicting_capture_definition:{','.join(sorted(clashing))}")

    mixed_kind = {name for name, kinds in area_kinds.items() if len(kinds) > 1}
    if mixed_kind:
        # The same {placeholder} used once as an area_name target and once
        # as an area_id target (e.g. across two Combined-Spell steps) -
        # each occurrence would need a DIFFERENT injected value (a name vs
        # an id), which one captures[name] string can never be both of.
        # Same "structurally incompatible roles" family as the clash check
        # above, just within the area-capture family itself.
        return ProjectionSkip(command.spell_id, f"conflicting_capture_definition:{','.join(sorted(mixed_kind))}")

    arguments: list[McpArgument] = []
    omitted_capture_names: set[str] = set()
    optional_capture_names: set[str] = set()
    implicit_captures: dict[str, str] = {}
    for name, facts in area_facts.items():
        merged = _merge_area_facts(facts)
        if merged is None:
            omitted_capture_names.add(name)
            # CASE A only ever merges to None when every occurrence's own
            # policy permits bridge.area (see _area_argument), and
            # area_kinds[name] is now a single-element set (mixed kinds
            # were already rejected above) - always resolvable, never None.
            (kind,) = area_kinds[name]
            implicit_captures[name] = bridge.area.area_name if kind == "area_name" else bridge.area.area_id  # type: ignore[union-attr]
            continue
        if not merged.required:
            optional_capture_names.add(name)
        arguments.append(merged)
    arguments.extend(entity_facts.values())
    arguments.extend(value_facts.values())
    arguments = _order_like_trigger(command, bridge.language, arguments)

    return McpToolProjection(
        spell_id=command.spell_id,
        name=command.spell_id,
        description=_build_description(command, bridge.language, omitted_capture_names, optional_capture_names),
        input_schema=_build_input_schema(arguments),
        arguments=tuple(arguments),
        implicit_captures=implicit_captures,
        # Read-only iff EVERY target this command touches is a query view
        # (core.operations.OPERATION_CATALOG's own action/query
        # classification, already computed above as `view` per target) -
        # a single query operation, a flat multi-source query, or a
        # Combined Spell whose every step is a query. Any action step
        # makes the whole tool non-read-only, even alongside query steps.
        read_only=all(view == "query" for _, view in target_specs),
    )


def project_spellbook(
    book: EngineSpellbook, bridge: BridgeContext
) -> tuple[tuple[McpToolProjection, ...], tuple[ProjectionSkip, ...]]:
    """The whole assigned Spellbook, projected for one Bridge. Every
    enabled, projectable Spell is attempted - no MCP-specific allowlist, no
    second command database. A disabled command stays silently absent, the
    exact same invisibility it already has everywhere else - never
    reported as a ProjectionSkip (nothing about it is structurally broken,
    it's simply turned off). A projectable-but-broken command (unknown
    operation, conflicting capture roles, unresolvable text) becomes a
    ProjectionSkip instead, always with a concrete reason - see
    ProjectionSkip's own docstring.

    Every genuinely typed Spell (operation/sources/steps) AND every
    text-only Spell (output only - see _project_text_only) is exposed as
    an MCP tool; only a Spell with NONE of operation/sources/steps/output
    has nothing to project at all (_is_projectable), the same "no typed
    form and no output" case core.spellbook_engine's own loader already
    rejects outright, so it can never actually occur here in practice.
    This is deliberately WIDER than core.spellbook_engine.match()'s own
    typed_only filter for SATELLITE/voice execution, which still treats a
    pure free-text command as invisible - an MCP client can act on a
    returned text answer in ways spoken/typed voice execution has no
    equivalent for, so there is no reason to hide it there too.

    A spell_id shared by more than one command in this book (an authoring
    mistake the loader itself never rejects - see
    core.spellbook_engine.duplicate_spell_ids) is never exposed as a tool
    at all: two same-named tools would be ambiguous to any MCP client, and
    silently picking one to expose (and, at call time, to execute) would
    silently do whichever the author least expected. Reported once per
    conflicting command as a ProjectionSkip, so it is visible (transport.py
    logs every skip) rather than merely absent."""
    duplicates = duplicate_spell_ids(book)
    projections: list[McpToolProjection] = []
    skips: list[ProjectionSkip] = []
    for command in book.commands:
        if not command.enabled or not _is_projectable(command):
            continue
        if command.spell_id in duplicates:
            skips.append(ProjectionSkip(command.spell_id, "duplicate_spell_id"))
            continue
        result = project_command(command, bridge)
        if isinstance(result, ProjectionSkip):
            skips.append(result)
        else:
            projections.append(result)
    return tuple(projections), tuple(skips)


def build_captures(projection: McpToolProjection, mcp_arguments: dict[str, Any] | None) -> dict[str, str] | None:
    """The mechanical inverse of project_command's schema derivation: turn
    one tools/call `arguments` payload back into the plain captures dict
    build_draft/build_query_plan/build_step_plan already consume, applying
    a CASE-C implicit default exactly where project_command declared one.
    Returns None when a required argument is missing - an ill-formed call
    Phase 2's execution adapter should reject as a protocol error, before
    ever reaching the engine.

    Every value becomes a plain string here, matching what a spoken
    utterance's own tokenizer would already have produced - the SAME
    int/float/value_map/word-enum conversion in core.spellbook_engine.
    _resolve_arguments applies unchanged regardless of whether the raw
    string came from speech or from an MCP tool call.
    """
    mcp_arguments = mcp_arguments or {}
    captures: dict[str, str] = dict(projection.implicit_captures)
    for arg in projection.arguments:
        if arg.capture_name in mcp_arguments and mcp_arguments[arg.capture_name] is not None:
            captures[arg.capture_name] = str(mcp_arguments[arg.capture_name])
            continue
        if arg.required:
            return None
        if arg.implicit_default is not None:
            captures[arg.capture_name] = arg.implicit_default
    return captures
