"""Code27FrontendAdapter - the part of CODE27's existing chat bridge
that now shares core.executor.ActionExecutor with every other
frontend, for the two action types Etappe 4 gave a typed equivalent
(vacuum_clean, light_all_on/off).

Deliberately narrow: everything else in bridge.py (the codeword flow,
free-text Assist forwarding, entity-correction and spell-choice
dialogs) is unchanged in Etappe 5 - those have no typed equivalent to
move to (free text was ruled out as a backdoor back in Etappe 1), and
the correction-dialog state (coordinator.pending_correction) is a
real, working, well-tested system that migrating to
InteractionStateStore would risk regressing without its own dedicated
live-verification pass. See docs/spellbook-engine's Etappe 5 notes.

CODE27's own chat is treated as fully unrestricted here - matches its
existing, unchanged behavior (it's the owner's primary interface, not
a scoped satellite like a spellbooker entry can be), so policy denial
is never the reason an action fails through this adapter.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ...core.executor import ActionExecutor
from ...core.models import ActionDraft, ActionTarget, AreaPolicy, ExecutionResult, ExecutionStatus
from ...spellbooker_ha import HaServiceCaller, HaTargetResolver

# Renders to every non-unavailable/unknown, non-hidden light entity
# that's assigned to an area - the user's own hardened version of the
# query they first verified working after the 2026.9.2 upgrade, when
# HaTargetResolver's entity_registry-based "all_allowed" resolution
# started silently producing zero targets for the light domain (root
# cause not pinned down - the registry-walk path is unusually heavy for
# what should be a simple "every light" query, and template-based
# enumeration turned out to just be more robust in practice). Each
# condition skips a category the original entity_registry walk also
# excluded (unassigned/stray entities, entities hidden from the UI) -
# except "exposed to the conversation assistant", which has no Jinja
# equivalent (async_should_expose is a plain Python helper, not a
# template function) - see set_all_lights below, which applies that
# filter itself, in Python, to this template's rendered result.
_ALL_LIGHTS_TEMPLATE = (
    "{% set ns = namespace(lights=[]) %}"
    "{% for l in states.light | rejectattr('state', 'in', ['unavailable', 'unknown']) %}"
    "{% if area_name(l.entity_id) is not none and not is_hidden_entity(l.entity_id) %}"
    "{% set ns.lights = ns.lights + [l.entity_id] %}"
    "{% endif %}"
    "{% endfor %}"
    "{{ ns.lights }}"
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_UNRESTRICTED_POLICY = AreaPolicy(allowed_areas=None, unrestricted=True)


class Code27FrontendAdapter:
    def __init__(self, hass: "HomeAssistant") -> None:
        self._executor = ActionExecutor(HaTargetResolver(hass), HaServiceCaller(hass))

    async def clean_vacuum_area(self, area_alias: str) -> ExecutionResult:
        draft = ActionDraft(
            operation="vacuum.clean_area", target=ActionTarget("area_name", area_alias), arguments={}
        )
        return await self._executor.execute(draft, _UNRESTRICTED_POLICY)

    async def set_all_lights(self, on: bool) -> ExecutionResult:
        """Bypasses ActionExecutor/HaTargetResolver's "all_allowed" kind
        entirely - see _ALL_LIGHTS_TEMPLATE above for why. CODE27's chat
        is unrestricted anyway (module docstring), so nothing here ever
        needed AreaPolicy in the first place."""
        from homeassistant.components.homeassistant.exposed_entities import (
            async_should_expose,
        )
        from homeassistant.exceptions import TemplateError
        from homeassistant.helpers import template as template_helper

        try:
            candidate_ids = template_helper.Template(_ALL_LIGHTS_TEMPLATE, self._hass).async_render()
        except TemplateError as err:
            return ExecutionResult(status=ExecutionStatus.FAILED, reason_code=str(err))
        entity_ids = [
            eid for eid in candidate_ids if async_should_expose(self._hass, "conversation", eid)
        ]
        if not entity_ids:
            return ExecutionResult(status=ExecutionStatus.INVALID, reason_code="no_matching_targets")

        try:
            await self._hass.services.async_call(
                "light", "turn_on" if on else "turn_off",
                {"entity_id": entity_ids}, blocking=True,
            )
        except Exception as err:  # noqa: BLE001 - mirrors HaServiceCaller.set_light_power's own catch-all
            return ExecutionResult(status=ExecutionStatus.FAILED, reason_code=str(err))
        return ExecutionResult(
            status=ExecutionStatus.SUCCESS, reason_code="ok", data={"entity_ids": entity_ids}
        )
