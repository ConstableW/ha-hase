"""Static, declarative per-step response-variable vocabulary for Combined
Spells (docs/SPELLBOOK-STUDIO-CAPABILITY-EXPANSION.md's deferred "Multi-Step
Aktionen" item, extended to also cover queries and named per-step response
variables).

core/spellbook_engine.py (placeholder validation at load time),
core/studio.py's spell_variables() (the Wizard's chip catalogue) and
core/executor.py's execute_steps() (namespace population at run time) all
read the SAME functions here, so the three can never drift apart - exactly
the failure mode that motivated `spell_variables()` becoming "the ONE
catalog" in the first place.

Zero homeassistant imports, same convention as the rest of core/.
"""
from __future__ import annotations

from .models import QUERY_RESPONSE_FIELDS_BY_DOMAIN
from .operations import OPERATION_CATALOG

# Target kinds that resolve to EXACTLY ONE entity at declare time - the same
# distinction core/spellbook_engine.py's _SINGLE_SOURCE_TARGET_KINDS already
# draws for query sources, reused here for action steps. Every other target
# kind (area_name/area_id/all_allowed/entity_ids/source_area/template) can
# resolve to any number of entities at runtime, including exactly one, so it
# always gets the aggregate/batch-outcome vocabulary below - this is decided
# purely from the step's own DECLARED target kind, never guessed from a
# runtime resolution result.
SINGLE_ENTITY_TARGET_KINDS = frozenset({"entity_id", "entity_name"})

# An action step targeting more than one entity (or one entity via a named
# area/all_allowed target) has no single "the state" to report - it gets a
# batch-outcome summary instead. "failed_names" is the only field naming
# individual entities; there is deliberately no "success_names" symmetric
# counterpart in v1 (not asked for, and would double every per-entity
# confirmation read this pass already bounds via MAX_CONFIRMATION_ENTITIES).
_AGGREGATE_ACTION_FIELDS: tuple[str, ...] = ("target_count", "success_count", "failed_count", "failed_names")

# Operations whose end-state is unconditionally determined by the operation
# id alone - never by arguments (toggle, set_power, set_value, ...) and
# never by whatever state the entity happened to already be in. These are
# the ONLY operations execute_steps() spends extra state re-reads on to
# report a REAL per-entity success/failure count; every other operation
# (scripts, scenes, media transport, vacuum, anything not listed here, or a
# step over MAX_CONFIRMATION_ENTITIES) falls back to the honest batch
# accept/reject semantics documented on execute_steps() itself. A single HA
# service call is a batch accept/reject, not per-entity confirmation - this
# table is deliberately small and explicit rather than guessing.
CONFIRMABLE_ACTION_END_STATES: dict[str, str] = {
    "light.turn_on": "on", "light.turn_off": "off",
    "switch.turn_on": "on", "switch.turn_off": "off",
    "input_boolean.turn_on": "on", "input_boolean.turn_off": "off",
    "cover.open_cover": "open", "cover.close_cover": "closed",
    "lock.lock": "locked", "lock.unlock": "unlocked",
    "fan.turn_on": "on", "fan.turn_off": "off",
}

# Bounded/safe, mirroring core/spellbook_engine.py's _MAX_SOURCES_PER_COMMAND
# convention: a confirmable action step targeting more entities than this
# falls back to honest batch semantics rather than issuing unbounded extra
# per-entity state reads.
MAX_CONFIRMATION_ENTITIES = 40

# New QuerySourceSpec aggregate kinds (item: "lists of matching entity
# friendly names, not only count/min/max/avg/sum") - additive to the
# existing count/min/max/avg/sum, usable by BOTH the legacy flat `sources:`
# field and a step's own `sources`, since they're plain new QuerySourceSpec
# kinds, not a steps-only concept.
LIST_AGGREGATE_SOURCE_KINDS = frozenset({"names", "states"})


def action_step_fields(operation: str, target_kind: str) -> tuple[str, ...]:
    """The response-variable suffixes ({alias.<suffix>}) a declared action
    step may legally reference, derived purely from the operation id and
    the step's own declared target kind."""
    if target_kind in SINGLE_ENTITY_TARGET_KINDS:
        spec = OPERATION_CATALOG.get(operation)
        domain = spec.domain_filter if spec is not None else None
        fields = {"success", *QUERY_RESPONSE_FIELDS_BY_DOMAIN.get("*", ())}
        if domain:
            fields.update(QUERY_RESPONSE_FIELDS_BY_DOMAIN.get(domain, ()))
        return tuple(sorted(fields))
    return _AGGREGATE_ACTION_FIELDS
