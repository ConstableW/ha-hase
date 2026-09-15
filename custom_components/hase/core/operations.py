"""Declarative Home Assistant action/query catalog for the Spellbook Engine.

The ONE source of truth for what a spell can typecheck against: operation
id, HA domain/service, USER vs EXPERT tier, allowed target kinds, the
domain filter used when resolving a target, argument types/ranges, curated
user-choice presets (what the Wizard's simple action picker offers), query
response fields, and an optional capability hint the Studio's
entity-capability metadata (spellbooker_ha.py) matches against a specific
entity's live attributes to decide whether to OFFER an operation for it
(never to block execution - HA's own service call is still the real
authority on whether an entity actually supports something).

`core/executor.py` (`ActionExecutor`) is execution/validation ONLY - target
resolution, policy checks, argument shape/range checks, dispatch - and
imports `OPERATION_CATALOG`/`OperationSpec` from here rather than owning a
second copy. `core/studio.py`'s `user_actions()` (the Wizard's action-picker
data) and `spell_capability()` read the exact same dict. No second registry,
no duplicated operation/domain/service mapping anywhere else in this
package - if a new operation needs adding, this module is the only place.

Still plain, typed, internal Python - not a user-editable YAML/JSON config.
A spellbook only ever *references* an operation id from here by name; it
never defines one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import QUERY_RESPONSE_FIELDS_BY_DOMAIN


@dataclass(frozen=True)
class OperationSpec:
    kind: str  # "action" | "query"
    allowed_target_kinds: frozenset[str]
    argument_types: dict[str, type]  # primary accepted argument schema
    # The domain_filter TargetResolver.resolve() is called with when
    # resolving this operation's target (e.g. "light", "climate") - None
    # means "any domain" (entity.get_state) or "resolved separately, never
    # reaches the generic resolve() call" (vacuum.clean_area).
    domain_filter: str | None = None
    # (ha_domain, ha_service) for the generic direct-service dispatch path
    # in executor.py's _dispatch_action - None for operations with bespoke
    # dispatch logic there (light.set_power, media_player.skip_track,
    # entity.get_state, vacuum.clean_area).
    service: tuple[str, str] | None = None
    user_choices: tuple[tuple[str, dict], ...] = ()
    argument_variants: tuple[dict[str, type], ...] = ()
    # Additive, independently-optional arguments layered on top of
    # argument_types (the REQUIRED schema) - any subset (including none)
    # may be present alongside the required keys, each validated against
    # its own declared type here. Distinct from argument_variants, which
    # is a set of MUTUALLY EXCLUSIVE alternate schemas (e.g. light.set_
    # power's {"percentage": int} vs {"on": bool} - never both at once);
    # optional_argument_types is for parameters of the SAME operation that
    # can combine freely (light.turn_on's color_name and brightness_pct -
    # one call may set either, both, or neither, in one HA service call).
    optional_argument_types: dict[str, type] = field(default_factory=dict)
    int_ranges: dict[str, tuple[int, int]] = field(default_factory=dict)
    # Same bounds-checking as int_ranges, for argument_types entries typed `float`
    # (climate.set_temperature, number.set_value, ...) - HA itself still enforces
    # the real per-entity min/max; this is only a sanity bound against nonsense
    # payloads, same non-goal as int_ranges.
    float_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    # Expert-only operations are blocked unless the local Expert setting is enabled,
    # even if an imported spell forgot/omitted draft.expert=True.
    expert_only: bool = False
    response_fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Tag the Studio's entity-capability metadata can match against a
    # specific entity's live attributes/supported_features to decide
    # whether to OFFER this operation for it in the Wizard - e.g. "rgb"/
    # "color_temp"/"effect" for light, "tilt"/"position" for cover,
    # "hvac_modes"/"preset_modes"/"fan_modes" for climate. None means
    # "always offered wherever the domain/target kind otherwise matches".
    capability_hint: str | None = None


_BROAD_TARGETS = frozenset(
    {"area_name", "area_id", "entity_name", "entity_id", "entity_ids", "template", "all_allowed", "source_area"}
)

# A reference DE/EN spoken-word -> Home Assistant color_name mapping, for a
# spell author to drop straight into light.turn_on's optional `color_name`
# capture as `value_map` (core/spellbook_engine.py's _resolve_arguments) -
# e.g. `arguments: {color_name: {capture: color, value_map: LIGHT_COLOR_NAME_WORDS,
# optional: true}}`. Not consulted by the loader/executor itself (the
# capture mechanism is fully generic - any value_map a spell declares
# works, this is just a ready-made one so "Licht blau machen" works out
# of the box without every book author curating their own word list).
# Values are Home Assistant's own CSS3 color_name vocabulary.
LIGHT_COLOR_NAME_WORDS: dict[str, str] = {
    "rot": "red", "red": "red",
    "grün": "green", "gruen": "green", "green": "green",
    "blau": "blue", "blue": "blue",
    "gelb": "yellow", "yellow": "yellow",
    "orange": "orange",
    "lila": "purple", "violett": "violet", "purple": "purple", "violet": "violet",
    "pink": "pink", "rosa": "pink",
    "weiß": "white", "weiss": "white", "white": "white",
    "schwarz": "black", "black": "black",
    "grau": "gray", "gray": "gray", "grey": "gray",
    "braun": "brown", "brown": "brown",
    "türkis": "turquoise", "tuerkis": "turquoise", "turquoise": "turquoise",
    "gold": "gold", "silber": "silver", "silver": "silver",
    "cyan": "cyan", "magenta": "magenta", "indigo": "indigo",
    "koralle": "coral", "coral": "coral",
    "lindgrün": "chartreuse", "lindgruen": "chartreuse", "chartreuse": "chartreuse",
    "limette": "lime", "lime": "lime",
    "oliv": "olive", "olive": "olive",
    "marineblau": "navy", "navy": "navy",
    "bordeaux": "maroon", "maroon": "maroon",
    "beige": "beige", "lachs": "salmon", "salmon": "salmon",
    "türkisblau": "teal", "teal": "teal",
    "karmesinrot": "crimson", "crimson": "crimson",
    "khaki": "khaki", "lavendel": "lavender", "lavender": "lavender",
    "pflaume": "plum", "plum": "plum", "orchidee": "orchid", "orchid": "orchid",
}
# Domains whose entities name a single specific "thing to trigger", not a
# physical fixture spread across a room - "activate scene X" has no natural
# "every scene in the living room" reading the way lights/covers/climate do.
# Scoping these to entity-only targeting keeps the Wizard's target picker
# honest about what the operation actually means, and sidesteps having to
# invent area-membership semantics for automations/scripts/timers/buttons.
_ENTITY_ONLY_TARGETS = frozenset({"entity_name", "entity_id", "entity_ids", "template", "all_allowed"})

OPERATION_CATALOG: dict[str, OperationSpec] = {
    "light.set_power": OperationSpec(
        kind="action",
        allowed_target_kinds=_BROAD_TARGETS,
        domain_filter="light",
        argument_types={"percentage": int},
        argument_variants=({"on": bool},),
        int_ranges={"percentage": (0, 100)},
        user_choices=((
            "Licht dimmen",
            {"percentage": {"capture": "value", "type": "int", "min": 0, "max": 100}},
        ),),
    ),
    "entity.get_state": OperationSpec(
        kind="query",
        allowed_target_kinds=frozenset({"entity_name", "entity_id"}),
        argument_types={},
        user_choices=(("Zustand abfragen", {}),),
        response_fields=QUERY_RESPONSE_FIELDS_BY_DOMAIN,
    ),
    "vacuum.clean_area": OperationSpec(
        kind="action",
        # Area-only, deliberately - "which vacuum, which room" is a
        # discovery problem (native HA mapping, or a per-brand
        # provider), not something a spoken entity_name could name
        # directly. The generic source_area resolver supplies an area_id
        # before vacuum dispatch. See executor.py's
        # _execute_vacuum_clean_area, which never consults domain_filter
        # for RESOLUTION - it's set here purely so the Wizard's
        # domain-filtered action picker (core/studio.py's user_actions())
        # can group this under "vacuum" instead of leaking into every
        # other domain's filtered view.
        allowed_target_kinds=frozenset({"area_name", "area_id", "source_area"}),
        domain_filter="vacuum",
        argument_types={},
        user_choices=(("Raum reinigen lassen", {}),),
    ),
    "media_player.skip_track": OperationSpec(
        kind="action",
        allowed_target_kinds=_BROAD_TARGETS,
        domain_filter="media_player",
        argument_types={"forward": bool},
        user_choices=(("Nächster Titel", {"forward": True}), ("Vorheriger Titel", {"forward": False})),
    ),

    # Direct Home Assistant services. Most are USER-tier (buildable and
    # testable in the Wizard without the local Expert toggle) - see
    # docs/SPELLBOOK-STUDIO-NEXT-PASS.md's capability-expansion note: a
    # normal user must be able to build/test these without knowing they're
    # "raw HA services" underneath. The catalog stays the closed whitelist
    # either way; arbitrary domain/service calls are never accepted from a
    # spellbook, expert or not.
    "light.turn_on": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="light",
        service=("light", "turn_on"), argument_types={},
        # color_name and brightness_pct are independently optional and
        # freely combinable - {"color_name": "blue"}, {"brightness_pct": 100}
        # and {"color_name": "blue", "brightness_pct": 100} are ALL valid
        # arguments for this ONE operation/HA call (see OperationSpec.
        # optional_argument_types), never a sequence of separate turn-on/
        # set-color/set-brightness steps, and never modeled as a Combined
        # Spell either - a light that's currently off still turns on and
        # gets these values in the one call, since that's simply what
        # HA's own light.turn_on already does.
        # A spell wanting "Licht blau machen" / "Licht 30 Prozent" /
        # "Licht blau 100 Prozent" as three spoken phrasings needs THREE
        # commands sharing this operation (each declaring only the
        # argument(s) its own trigger actually captures), not one command
        # with an "optional capture" - every alias in a single command's
        # `triggers:` list is validated against the SAME arguments_spec
        # (see core/spellbook_engine.py's _load_command), so a placeholder
        # referenced there must appear in every phrasing that command can
        # match, never just some. See tests/test_light_color_parameters.py
        # for the three-command pattern. No live Wizard picker for the
        # spoken captures yet - same "Manual/Expert editor reachable now"
        # status light.set_effect/climate.set_fan_mode already have.
        optional_argument_types={"color_name": str, "brightness_pct": int},
        int_ranges={"brightness_pct": (0, 100)},
        user_choices=(("Licht einschalten", {}),),
    ),
    "light.turn_off": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="light",
        service=("light", "turn_off"), argument_types={},
        user_choices=(("Licht ausschalten", {}),),
    ),
    "light.toggle": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="light",
        service=("light", "toggle"), argument_types={},
        user_choices=(("Licht umschalten", {}),),
    ),
    "light.set_color_temperature": OperationSpec(
        # Same bug class as light.set_color just below: HA has no
        # standalone "light.set_color_temperature" service either - it's
        # a light.turn_on data field, and that field is named
        # "color_temp_kelvin", not "kelvin". The catalog's user-facing
        # argument key stays "kelvin" (no schema change for any spell
        # already authored against it - see executor.py's
        # _dispatch_action, same "translate only at the final HA-facing
        # call" pattern media_player.volume_set/volume_mute already use
        # below); only the actual service + HA field name are fixed.
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="light",
        service=("light", "turn_on"),
        argument_types={"kelvin": int}, int_ranges={"kelvin": (2000, 6500)},
        capability_hint="color_temp",
        user_choices=((
            "Farbtemperatur einstellen",
            {"kelvin": {"capture": "value", "type": "int", "min": 2000, "max": 6500}},
        ),),
    ),
    "light.set_color": OperationSpec(
        # HA has no standalone "light.set_color" service - color (like
        # brightness and effect) is set via light.turn_on's own
        # rgb_color data field. Confirmed live: the old service name
        # made every call here fail with service_call_rejected,
        # regardless of which named color was picked.
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="light",
        service=("light", "turn_on"),
        argument_types={"rgb_color": list},
        # HA's light.turn_on also accepts a plain color_name string
        # (e.g. "chartreuse") as an alternative to rgb_color - both stay
        # valid; a fixed spell picks exactly one representation, never
        # both in the same call (that's what argument_variants means -
        # a mutually exclusive alternate schema, not an additive optional
        # one; see light.turn_on above for the additive kind).
        argument_variants=({"color_name": str},),
        capability_hint="rgb",
        # Curated presets, not a spoken RGB triple - "keine freie
        # Jinja/YAML-Eingabe" applies just as much to raw color numbers as
        # to templates; a normal user picks a named color. The Wizard also
        # offers a visual color picker (arbitrary rgb_color) and a free
        # HA color-name field (color_name) alongside these presets.
        user_choices=(
            ("Rot", {"rgb_color": [255, 0, 0]}),
            ("Grün", {"rgb_color": [0, 255, 0]}),
            ("Blau", {"rgb_color": [0, 0, 255]}),
            ("Gelb", {"rgb_color": [255, 255, 0]}),
            ("Warmweiß", {"rgb_color": [255, 197, 143]}),
            ("Kaltweiß", {"rgb_color": [201, 226, 255]}),
            ("Lila", {"rgb_color": [160, 32, 240]}),
            ("Orange", {"rgb_color": [255, 140, 0]}),
            ("Pink", {"rgb_color": [255, 105, 180]}),
        ),
    ),
    "light.set_effect": OperationSpec(
        # No such HA service as "light.set_effect" - effects are set
        # through light.turn_on's own `effect` data field. Effect names
        # are entirely device-specific (no HA-wide standard list) -
        # reachable via Manual/Expert editing (literal
        # arguments={"effect": "..."}); a live per-entity effect-list
        # picker in the Wizard's simple action picker is a documented
        # follow-up (see final report), not built this pass - empty
        # user_choices keeps it out of that picker without blocking
        # Manual Editor use.
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="light",
        service=("light", "turn_on"),
        argument_types={"effect": str},
        capability_hint="effect",
    ),
    "lock.lock": OperationSpec(
        # Left Expert-gated deliberately (physical security, unlike the
        # comfort/media/climate functions above) - not one of the
        # capabilities the brief's per-domain examples asked to promote.
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="lock",
        service=("lock", "lock"), argument_types={}, expert_only=True,
    ),
    "lock.unlock": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="lock",
        service=("lock", "unlock"), argument_types={}, expert_only=True,
    ),
    "cover.open_cover": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="cover",
        service=("cover", "open_cover"), argument_types={},
        user_choices=(("Öffnen", {}),),
    ),
    "cover.close_cover": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="cover",
        service=("cover", "close_cover"), argument_types={},
        user_choices=(("Schließen", {}),),
    ),
    "cover.stop_cover": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="cover",
        service=("cover", "stop_cover"), argument_types={},
        user_choices=(("Stoppen", {}),),
    ),
    "cover.toggle": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="cover",
        service=("cover", "toggle"), argument_types={},
        user_choices=(("Umschalten", {}),),
    ),
    "cover.set_cover_position": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="cover",
        service=("cover", "set_cover_position"),
        argument_types={"position": int}, int_ranges={"position": (0, 100)},
        capability_hint="position",
        user_choices=((
            "Position einstellen",
            {"position": {"capture": "value", "type": "int", "min": 0, "max": 100}},
        ),),
    ),
    "cover.set_cover_tilt_position": OperationSpec(
        # Offered generically; entities without tilt support simply reject
        # the service call (FAILED/service_call_rejected) - the Wizard's
        # capability-driven filtering (spellbooker_ha.py's entity
        # capability metadata, matched against capability_hint) is what
        # keeps this from being OFFERED for a non-tilt cover in the first
        # place, this catalog entry itself doesn't know per-entity.
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="cover",
        service=("cover", "set_cover_tilt_position"),
        argument_types={"tilt_position": int}, int_ranges={"tilt_position": (0, 100)},
        capability_hint="tilt",
        user_choices=((
            "Lamellen einstellen",
            {"tilt_position": {"capture": "value", "type": "int", "min": 0, "max": 100}},
        ),),
    ),
    "cover.open_cover_tilt": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="cover",
        service=("cover", "open_cover_tilt"), argument_types={},
        capability_hint="tilt",
        user_choices=(("Lamellen öffnen", {}),),
    ),
    "cover.close_cover_tilt": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="cover",
        service=("cover", "close_cover_tilt"), argument_types={},
        capability_hint="tilt",
        user_choices=(("Lamellen schließen", {}),),
    ),
    "cover.stop_cover_tilt": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="cover",
        service=("cover", "stop_cover_tilt"), argument_types={},
        capability_hint="tilt",
        user_choices=(("Lamellen stoppen", {}),),
    ),
    "climate.set_temperature": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="climate",
        service=("climate", "set_temperature"),
        argument_types={"temperature": float}, float_ranges={"temperature": (5.0, 35.0)},
        user_choices=((
            "Temperatur einstellen",
            {"temperature": {"capture": "value", "type": "float", "min": 5, "max": 35}},
        ),),
    ),
    "climate.set_hvac_mode": OperationSpec(
        # A fixed, curated subset of HA's standard HVACMode values (not
        # spoken-captured - not every mode applies to every device, and
        # HA's own service call rejects an unsupported one gracefully,
        # same as any other capability mismatch).
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="climate",
        service=("climate", "set_hvac_mode"),
        argument_types={"hvac_mode": str},
        capability_hint="hvac_modes",
        user_choices=(
            ("Heizen", {"hvac_mode": "heat"}),
            ("Kühlen", {"hvac_mode": "cool"}),
            ("Automatik", {"hvac_mode": "auto"}),
            ("Nur Lüfter", {"hvac_mode": "fan_only"}),
            ("Trocknen", {"hvac_mode": "dry"}),
            ("Aus", {"hvac_mode": "off"}),
        ),
    ),
    "climate.set_preset_mode": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="climate",
        service=("climate", "set_preset_mode"),
        argument_types={"preset_mode": str},
        capability_hint="preset_modes",
        user_choices=(
            ("Eco", {"preset_mode": "eco"}),
            ("Abwesend", {"preset_mode": "away"}),
            ("Komfort", {"preset_mode": "comfort"}),
            ("Zuhause", {"preset_mode": "home"}),
            ("Schlafen", {"preset_mode": "sleep"}),
            ("Boost", {"preset_mode": "boost"}),
        ),
    ),
    "climate.set_fan_mode": OperationSpec(
        # No HA-wide standard fan-mode list (device-specific) - Manual
        # Editor reachable, same documented follow-up as light.set_effect.
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="climate",
        service=("climate", "set_fan_mode"), argument_types={"fan_mode": str},
        capability_hint="fan_modes",
    ),
    "fan.turn_on": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="fan",
        service=("fan", "turn_on"), argument_types={},
        user_choices=(("Ventilator einschalten", {}),),
    ),
    "fan.turn_off": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="fan",
        service=("fan", "turn_off"), argument_types={},
        user_choices=(("Ventilator ausschalten", {}),),
    ),
    "fan.toggle": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="fan",
        service=("fan", "toggle"), argument_types={},
        user_choices=(("Ventilator umschalten", {}),),
    ),
    "fan.set_percentage": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="fan",
        service=("fan", "set_percentage"),
        argument_types={"percentage": int}, int_ranges={"percentage": (0, 100)},
        capability_hint="percentage",
        user_choices=((
            "Geschwindigkeit einstellen",
            {"percentage": {"capture": "value", "type": "int", "min": 0, "max": 100}},
        ),),
    ),
    "fan.set_preset_mode": OperationSpec(
        # No HA-wide standard preset list (device-specific) - same
        # documented follow-up as light.set_effect/climate.set_fan_mode.
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="fan",
        service=("fan", "set_preset_mode"), argument_types={"preset_mode": str},
        capability_hint="preset_modes",
    ),
    "fan.oscillate": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="fan",
        service=("fan", "oscillate"),
        argument_types={"oscillating": bool},
        capability_hint="oscillate",
        user_choices=(("Oszillieren ein", {"oscillating": True}), ("Oszillieren aus", {"oscillating": False})),
    ),
    "media_player.volume_up": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="media_player",
        service=("media_player", "volume_up"), argument_types={},
        user_choices=(("Lauter", {}),),
    ),
    "media_player.volume_down": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="media_player",
        service=("media_player", "volume_down"), argument_types={},
        user_choices=(("Leiser", {}),),
    ),
    "media_player.volume_mute": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="media_player",
        service=("media_player", "volume_mute"), argument_types={},
        user_choices=(("Stummschalten", {}),),
    ),
    "media_player.stop": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="media_player",
        service=("media_player", "media_stop"), argument_types={},
        user_choices=(("Stopp", {}),),
    ),
    "media_player.pause": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="media_player",
        service=("media_player", "media_pause"), argument_types={},
        user_choices=(("Pause", {}),),
    ),
    "media_player.media_play": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="media_player",
        service=("media_player", "media_play"), argument_types={},
        user_choices=(("Abspielen", {}),),
    ),
    "media_player.media_play_pause": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="media_player",
        service=("media_player", "media_play_pause"), argument_types={},
        user_choices=(("Play/Pause", {}),),
    ),
    "media_player.media_next_track": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="media_player",
        service=("media_player", "media_next_track"), argument_types={},
        user_choices=(("Nächster Titel", {}),),
    ),
    "media_player.media_previous_track": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="media_player",
        service=("media_player", "media_previous_track"), argument_types={},
        user_choices=(("Vorheriger Titel", {}),),
    ),
    "media_player.volume_set": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="media_player",
        service=("media_player", "volume_set"),
        argument_types={"volume_level": int}, int_ranges={"volume_level": (0, 10)},
        user_choices=((
            "Lautstärke einstellen",
            {"volume_level": {"capture": "value", "type": "int", "min": 0, "max": 10}},
        ),),
    ),
    "media_player.select_source": OperationSpec(
        # Source names are per-installation (media_player's own
        # source_list attribute) - same documented follow-up as
        # light.set_effect: Manual Editor reachable now, a live per-entity
        # source picker in the simple Wizard action picker is a follow-up.
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="media_player",
        service=("media_player", "select_source"), argument_types={"source": str},
        capability_hint="source_list",
    ),
    "vacuum.start": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="vacuum",
        service=("vacuum", "start"), argument_types={},
        user_choices=(("Saugen starten", {}),),
    ),
    "vacuum.pause": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="vacuum",
        service=("vacuum", "pause"), argument_types={},
        user_choices=(("Saugen pausieren", {}),),
    ),
    "vacuum.stop": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="vacuum",
        service=("vacuum", "stop"), argument_types={},
        user_choices=(("Saugen stoppen", {}),),
    ),
    "vacuum.return_to_base": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="vacuum",
        service=("vacuum", "return_to_base"), argument_types={},
        user_choices=(("Zur Ladestation schicken", {}),),
    ),
    "scene.turn_on": OperationSpec(
        kind="action", allowed_target_kinds=_ENTITY_ONLY_TARGETS, domain_filter="scene",
        service=("scene", "turn_on"), argument_types={},
        user_choices=(("Szene aktivieren", {}),),
    ),
    "script.turn_on": OperationSpec(
        kind="action", allowed_target_kinds=_ENTITY_ONLY_TARGETS, domain_filter="script",
        service=("script", "turn_on"), argument_types={},
        user_choices=(("Skript starten", {}),),
    ),
    "automation.trigger": OperationSpec(
        kind="action", allowed_target_kinds=_ENTITY_ONLY_TARGETS, domain_filter="automation",
        service=("automation", "trigger"), argument_types={},
        user_choices=(("Automatisierung auslösen", {}),),
    ),
    "automation.turn_on": OperationSpec(
        kind="action", allowed_target_kinds=_ENTITY_ONLY_TARGETS, domain_filter="automation",
        service=("automation", "turn_on"), argument_types={},
        user_choices=(("Automatisierung aktivieren", {}),),
    ),
    "automation.turn_off": OperationSpec(
        kind="action", allowed_target_kinds=_ENTITY_ONLY_TARGETS, domain_filter="automation",
        service=("automation", "turn_off"), argument_types={},
        user_choices=(("Automatisierung deaktivieren", {}),),
    ),
    "button.press": OperationSpec(
        kind="action", allowed_target_kinds=_ENTITY_ONLY_TARGETS, domain_filter="button",
        service=("button", "press"), argument_types={},
        user_choices=(("Knopf drücken", {}),),
    ),
    "number.set_value": OperationSpec(
        kind="action", allowed_target_kinds=_ENTITY_ONLY_TARGETS, domain_filter="number",
        service=("number", "set_value"),
        argument_types={"value": float}, float_ranges={"value": (-1000.0, 1000.0)},
        user_choices=((
            "Wert einstellen",
            {"value": {"capture": "value", "type": "float", "min": -1000, "max": 1000}},
        ),),
    ),
    "input_number.set_value": OperationSpec(
        kind="action", allowed_target_kinds=_ENTITY_ONLY_TARGETS, domain_filter="input_number",
        service=("input_number", "set_value"),
        argument_types={"value": float}, float_ranges={"value": (-1000.0, 1000.0)},
        user_choices=((
            "Wert einstellen",
            {"value": {"capture": "value", "type": "float", "min": -1000, "max": 1000}},
        ),),
    ),
    "select.select_option": OperationSpec(
        # Options are entirely per-entity - same documented follow-up as
        # light.set_effect/media_player.select_source.
        kind="action", allowed_target_kinds=_ENTITY_ONLY_TARGETS, domain_filter="select",
        service=("select", "select_option"), argument_types={"option": str},
        capability_hint="options",
    ),
    "input_select.select_option": OperationSpec(
        kind="action", allowed_target_kinds=_ENTITY_ONLY_TARGETS, domain_filter="input_select",
        service=("input_select", "select_option"), argument_types={"option": str},
        capability_hint="options",
    ),
    "timer.start": OperationSpec(
        # Uses the timer entity's own configured default duration; a
        # custom spoken duration is a documented follow-up, not built here.
        kind="action", allowed_target_kinds=_ENTITY_ONLY_TARGETS, domain_filter="timer",
        service=("timer", "start"), argument_types={},
        user_choices=(("Timer starten", {}),),
    ),
    "timer.pause": OperationSpec(
        kind="action", allowed_target_kinds=_ENTITY_ONLY_TARGETS, domain_filter="timer",
        service=("timer", "pause"), argument_types={},
        user_choices=(("Timer pausieren", {}),),
    ),
    "timer.cancel": OperationSpec(
        kind="action", allowed_target_kinds=_ENTITY_ONLY_TARGETS, domain_filter="timer",
        service=("timer", "cancel"), argument_types={},
        user_choices=(("Timer abbrechen", {}),),
    ),
    "timer.finish": OperationSpec(
        kind="action", allowed_target_kinds=_ENTITY_ONLY_TARGETS, domain_filter="timer",
        service=("timer", "finish"), argument_types={},
        user_choices=(("Timer beenden", {}),),
    ),
    "switch.turn_on": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="switch",
        service=("switch", "turn_on"), argument_types={},
        user_choices=(("Schalter einschalten", {}),),
    ),
    "switch.turn_off": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="switch",
        service=("switch", "turn_off"), argument_types={},
        user_choices=(("Schalter ausschalten", {}),),
    ),
    "switch.toggle": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="switch",
        service=("switch", "toggle"), argument_types={},
        user_choices=(("Schalter umschalten", {}),),
    ),
    "input_boolean.turn_on": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="input_boolean",
        service=("input_boolean", "turn_on"), argument_types={},
        user_choices=(("Einschalten", {}),),
    ),
    "input_boolean.turn_off": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="input_boolean",
        service=("input_boolean", "turn_off"), argument_types={},
        user_choices=(("Ausschalten", {}),),
    ),
    "input_boolean.toggle": OperationSpec(
        kind="action", allowed_target_kinds=_BROAD_TARGETS, domain_filter="input_boolean",
        service=("input_boolean", "toggle"), argument_types={},
        user_choices=(("Umschalten", {}),),
    ),
}
