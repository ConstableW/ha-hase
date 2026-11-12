"""The one place this feature actually touches Home Assistant to
execute something: real TargetResolver/ServiceCaller implementations
for core.executor.ActionExecutor, plus discovery/loading of the
core.spellbook_engine YAML books.

Needs live HA verification (see docs/spellbook-engine/stage-1's own
"Live-Abnahme getrennt von Adaptertests" principle) - registry/service
call shapes here are written against documented HA behavior but
weren't exercised against a running instance as part of Etappe 2.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import vacuum_provider
from .core.executor import (
    ResolvedEntity,
    ResolvedTargets,
    ServiceCallOutcome,
    TargetNotFound,
    VacuumCleanOutcome,
)
from .core.home_overview import OVERVIEW_DOMAINS, OverviewArea, OverviewEntity
from .core.models import ActionTarget
from .core.text_only_execution import AssistForwardOutcome

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Bit position confirmed against bridge.py's own _VACUUM_CLEAN_AREA_FEATURE
# constant (VacuumEntityFeature.CLEAN_AREA in current HA core).
_VACUUM_CLEAN_AREA_FEATURE = 16384

def resolve_source_area_id(hass: HomeAssistant, device_id: str | None) -> str | None:
    """The HA area of the device that actually sent this turn (a voice
    satellite, typically) - Core has no notion of "Samwise" or any
    other concrete device, it only ever sees this normalized area id
    via ConversationRequest.source_area_id, exactly like it only ever
    sees a resolved HA Context, never a device. None (no device_id, no
    area assigned to that device) means no fallback is available -
    core/executor.py's own source_area target kind treats that as a
    controlled rejection, never as license to go wider (all_allowed).

    Synchronous, on the event loop, not an executor job - registry
    reads only, see spellbooker_ha's own Fund #20 fix for why."""
    if not device_id:
        return None
    from homeassistant.helpers import device_registry as dr

    device = dr.async_get(hass).async_get(device_id)
    return device.area_id if device is not None else None


# ---------------------------------------------------------------------------
# TargetResolver
# ---------------------------------------------------------------------------


def _find_area_by_name(area_registry, wanted: str):
    """Name first, then alias - matches bridge.py's own _resolve_area
    exactly (area aliases are a real, used HA feature; a resolver that
    only checked the canonical name would be a silent regression for
    anyone using one). Area names are unique in HA's own registry, but
    aliases are NOT - nothing stops two different areas both declaring
    "Gondor" as an alias. Returning whichever came first in registry
    iteration order in that case is exactly the "ask rather than
    guess" rule this codebase applies everywhere else being violated -
    ambiguous resolves to nothing, never to an arbitrary pick."""
    lowered = wanted.strip().lower()
    if not lowered:
        return None
    for area in area_registry.areas.values():
        if area.name and area.name.strip().lower() == lowered:
            return area
    matches_by_alias = [
        area
        for area in area_registry.areas.values()
        for alias in area.aliases
        if isinstance(alias, str) and alias.strip().lower() == lowered
    ]
    distinct = {area.id for area in matches_by_alias}
    if len(distinct) != 1:
        return None
    return matches_by_alias[0]


class HaTargetResolver:
    """Resolves an ActionTarget against the real entity/area/device
    registries. Exact (case-insensitive) name matching only - the
    fuzzy "did you mean" correction loop is bridge.py's own feature
    (entity-correction), out of scope here; a Spellbooker agent that
    can't find a target reports that plainly instead."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def resolve(self, target: ActionTarget, *, domain_filter: str | None):
        # Deliberately NOT hass.async_add_executor_job: entity/area/
        # device registries and hass.states are event-loop-owned data
        # structures, safe to read only from the event loop itself.
        # Reading them from a worker thread while the loop concurrently
        # mutates them (any other integration's own update) is a real
        # race - a rare, hard-to-reproduce crash or silently-wrong
        # result, not a performance optimization worth that risk. The
        # lookups here are in-memory dict/attribute access, not I/O -
        # there was never a blocking-call reason to offload them.
        if target.kind in ("template", "entity_ids"):
            raw = target.value
            if target.kind == "template":
                from homeassistant.helpers.template import Template
                try:
                    template = Template(target.value, self._hass)
                    template.ensure_valid()
                    raw = template.async_render(parse_result=True)
                except Exception:  # noqa: BLE001 - untrusted template failures are diagnostics.
                    return ResolvedTargets((), ({"reason": "template_error"},), None)
            if isinstance(raw, str):
                values = [v.strip() for v in raw.split(",")] if raw.strip() else []
            elif isinstance(raw, (list, tuple)):
                values = list(raw)
            else:
                return ResolvedTargets((), ({"reason": "invalid_template_result"},), str(raw)[:4096])
            import re
            if len(values) > 256 or not all(isinstance(v, str) and re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", v) for v in values):
                return ResolvedTargets((), ({"reason": "invalid_template_result"},), str(raw)[:4096])
            entities, rejected = [], []
            for value in dict.fromkeys(values):
                result = self._resolve_sync(ActionTarget("entity_id", value), domain_filter)
                if isinstance(result, TargetNotFound):
                    state = self._hass.states.get(value)
                    reason = "not_found" if state is None else (
                        "wrong_domain" if domain_filter and value.split(".")[0] != domain_filter else
                        "unavailable" if state.state in ("unavailable", "unknown") else "not_exposed_or_ineligible")
                    rejected.append({"entity_id": value, "reason": reason})
                else:
                    entities.extend(result.entities)
            return ResolvedTargets(tuple(entities), tuple(rejected), raw)
        return self._resolve_sync(target, domain_filter)

    async def resolve_area(self, target: ActionTarget) -> str | TargetNotFound:
        return self._resolve_area_sync(target)

    def _resolve_area_sync(self, target: ActionTarget) -> str | TargetNotFound:
        from homeassistant.helpers import area_registry as ar

        area_registry = ar.async_get(self._hass)
        if target.kind == "area_id":
            area = area_registry.areas.get(target.value)
            return area.id if area is not None else TargetNotFound()
        if target.kind == "area_name":
            area = _find_area_by_name(area_registry, target.value or "")
            return area.id if area is not None else TargetNotFound()
        return TargetNotFound()

    def _resolve_sync(self, target: ActionTarget, domain_filter: str | None):
        from homeassistant.components.homeassistant.exposed_entities import (
            async_should_expose,
        )
        from homeassistant.helpers import area_registry as ar
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er

        hass = self._hass
        entity_registry = er.async_get(hass)
        device_registry = dr.async_get(hass)
        area_registry = ar.async_get(hass)

        def area_id_of(entry) -> str | None:
            if entry.area_id:
                return entry.area_id
            device = device_registry.devices.get(entry.device_id) if entry.device_id else None
            return device.area_id if device else None

        def is_group(entry) -> bool:
            # A group entity's own state exposes its members as an
            # "entity_id" list attribute (light groups, and HA's
            # generic group.light). A group's mapped area says nothing
            # about where its members actually are - resolving one as
            # a normal single target would let a policy-permitted
            # group silently reach members in a forbidden area, since
            # HA forwards the service call straight to them. Excluded
            # entirely rather than guessed at.
            state = hass.states.get(entry.entity_id)
            return bool(state is not None and isinstance(state.attributes.get("entity_id"), list))

        def exposed_entries(*, filter_unavailable_lights: bool = True):
            for entry in entity_registry.entities.values():
                state = hass.states.get(entry.entity_id)
                if entry.disabled_by is not None or state is None:
                    continue
                if domain_filter == "light" and getattr(entry, "hidden_by", None) is not None:
                    continue
                # Preserve the removed lighting path's eligibility filters
                # for batch/named-membership targets (area_name/area_id/
                # all_allowed): an unavailable light is silently skipped
                # there, same as a forbidden one. Queries must still be
                # able to report an unavailable state. An explicit
                # entity_id/entity_name target is a different question -
                # existence and availability are separate (see the two
                # callers below): the caller resolves regardless of
                # availability, and it is core.executor's job to reject
                # the concrete operation afterward, never this resolver's.
                if (
                    filter_unavailable_lights
                    and domain_filter == "light"
                    and state.state in ("unavailable", "unknown")
                ):
                    continue
                if domain_filter and entry.entity_id.split(".", 1)[0] != domain_filter:
                    continue
                if not async_should_expose(hass, "conversation", entry.entity_id):
                    continue
                if is_group(entry):
                    continue
                yield entry

        if target.kind == "entity_id":
            # Same checks as every other target kind - a direct ID is
            # not a bypass of exposure/disabled/domain/group filtering.
            # Unavailable/unknown is NOT excluded here (see
            # exposed_entries' own docstring comment) - an explicitly
            # named entity that exists but is offline must resolve, so
            # core.executor can reject the operation with a distinct
            # target_unavailable reason instead of a misleading
            # target_not_found.
            matches = [e for e in exposed_entries(filter_unavailable_lights=False) if e.entity_id == target.value]
            if not matches:
                return TargetNotFound()
            entry = matches[0]
            return ResolvedTargets((ResolvedEntity(entry.entity_id, area_id_of(entry)),))

        if target.kind == "entity_name":
            wanted = (target.value or "").strip().lower()
            matches = []
            for entry in exposed_entries(filter_unavailable_lights=False):
                state = hass.states.get(entry.entity_id)
                friendly = (state.attributes.get("friendly_name") or "") if state else ""
                if wanted in (
                    (entry.name or entry.original_name or "").strip().lower(),
                    friendly.strip().lower(),
                ):
                    matches.append(entry)
            if not matches:
                return TargetNotFound()
            if len(matches) > 1 and len({m.entity_id for m in matches}) > 1:
                # Two distinct entities share this name - never guess
                # which one was meant, same "ask rather than guess"
                # rule as everywhere else in this feature.
                return TargetNotFound()
            entry = matches[0]
            return ResolvedTargets((ResolvedEntity(entry.entity_id, area_id_of(entry)),))

        if target.kind in ("area_name", "area_id"):
            if target.kind == "area_id":
                area = area_registry.areas.get(target.value)
            else:
                area = _find_area_by_name(area_registry, target.value or "")
            if area is None:
                return TargetNotFound()
            entities = tuple(
                ResolvedEntity(entry.entity_id, area_id_of(entry))
                for entry in exposed_entries()
                if area_id_of(entry) == area.id
            )
            return ResolvedTargets(entities)

        if target.kind == "all_allowed":
            entities = tuple(
                ResolvedEntity(entry.entity_id, area_id_of(entry)) for entry in exposed_entries()
            )
            if not entities:
                _LOGGER.debug(
                    "all_allowed: no eligible %s entities among %d registry entries; "
                    "check live state, disabled/hidden status, conversation exposure and groups",
                    domain_filter or "*", len(entity_registry.entities),
                )
            return ResolvedTargets(entities)

        return TargetNotFound()


# ---------------------------------------------------------------------------
# ServiceCaller
# ---------------------------------------------------------------------------

# How long an action call waits for Home Assistant before HASE answers.
# HA's blocking service call only returns once EVERY targeted entity's
# integration has finished - for "all lights off" across a whole house that
# can take a minute or more (slow/unreachable bulbs, radio retries), while
# the lights themselves switch one after the other. MCP clients (HASEC) and
# spoken turns give up long before that and then report a timeout for a
# command that actually ran. HA's own intent handlers solve the same problem
# the same way (ServiceIntentHandler.service_timeout): wait briefly, then let
# the call finish in the background. A call that fails within the window is
# still reported as a failure; a later failure is logged.
SERVICE_ACCEPT_WAIT_SECONDS = 8.0

# Strong references to calls still running after the wait window, so the
# event loop cannot garbage-collect them before they finish.
_BACKGROUND_CALLS: set[asyncio.Task] = set()


def _log_late_outcome(label: str, started: float, task: asyncio.Task) -> None:
    _BACKGROUND_CALLS.discard(task)
    elapsed = asyncio.get_running_loop().time() - started if not task.cancelled() else 0.0
    if task.cancelled():
        _LOGGER.warning("spellbooker: %s was cancelled while finishing in the background", label)
        return
    err = task.exception()
    if err is not None:
        _LOGGER.warning(
            "spellbooker: %s failed after %.1f s (answer already sent as accepted): %s",
            label, elapsed, err,
        )
    else:
        _LOGGER.info("spellbooker: %s finished in the background after %.1f s", label, elapsed)


async def _await_accepted(label: str, call: Awaitable[object]) -> None:
    """Await a blocking HA service call for at most SERVICE_ACCEPT_WAIT_SECONDS.

    Raises whatever the call raised if it ended within the window (so the
    callers' existing failure handling is unchanged). If it is still running,
    returns normally and lets it finish in the background - same "accepted,
    not proof every device acted" meaning ServiceCallOutcome already has."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    task = asyncio.ensure_future(call)
    done, _ = await asyncio.wait({task}, timeout=SERVICE_ACCEPT_WAIT_SECONDS)
    if task in done:
        task.result()
        return
    _BACKGROUND_CALLS.add(task)
    task.add_done_callback(lambda t: _log_late_outcome(label, started, t))
    _LOGGER.info(
        "spellbooker: %s still running after %.0f s - answering as accepted, finishing in the background",
        label, SERVICE_ACCEPT_WAIT_SECONDS,
    )


class HaServiceCaller:
    def __init__(
        self,
        hass: HomeAssistant,
        before_side_effect: Callable[[], None] | None = None,
    ) -> None:
        self._hass = hass
        self._before_side_effect = before_side_effect or (lambda: None)

    async def set_light_power(
        self, entity_ids: tuple[str, ...], on: bool, *, area_id: str | None = None,
        context: object | None = None
    ) -> ServiceCallOutcome:
        try:
            self._before_side_effect()
            service = "turn_on" if on else "turn_off"
            if area_id:
                await _await_accepted(f"light.{service}", self._hass.services.async_call(
                    "light", service, {},
                    target={"area_id": area_id}, blocking=True, context=context,
                ))
            else:
                # Keep the established payload shape used by existing HASE
                # clients and tests; native area targeting is an optional path.
                await _await_accepted(f"light.{service}", self._hass.services.async_call(
                    "light", service,
                    {"entity_id": list(entity_ids)}, blocking=True, context=context,
                ))
        except Exception as err:  # noqa: BLE001 - executor turns this into FAILED, not a crash
            _LOGGER.warning("spellbooker: light.set_power service call failed: %s", err)
            return ServiceCallOutcome(False, type(err).__name__, str(err))
        return ServiceCallOutcome(True)

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_ids: tuple[str, ...],
        data: dict,
        *,
        area_id: str | None = None,
        context: object | None = None,
    ) -> ServiceCallOutcome:
        """Execute a service selected by ActionExecutor's closed whitelist.

        The spellbook never supplies `domain` or `service` directly. Targets
        have already been resolved/exposure-checked/policy-checked by the
        executor before this method is reached.
        """
        target = {"area_id": area_id} if area_id else {"entity_id": list(entity_ids)}
        try:
            self._before_side_effect()
            await _await_accepted(f"{domain}.{service}", self._hass.services.async_call(
                domain,
                service,
                dict(data),
                target=target,
                blocking=True,
                context=context,
            ))
        except Exception as err:  # noqa: BLE001 - executor converts this to FAILED
            _LOGGER.warning(
                "spellbooker: %s.%s service call failed: %s",
                domain,
                service,
                err,
            )
            return ServiceCallOutcome(False, type(err).__name__, str(err))
        return ServiceCallOutcome(True)

    async def skip_track(
        self, entity_ids: tuple[str, ...], forward: bool, *, area_id: str | None = None,
        context: object | None = None
    ) -> ServiceCallOutcome:
        try:
            self._before_side_effect()
            service = "media_next_track" if forward else "media_previous_track"
            if area_id:
                await _await_accepted(f"media_player.{service}", self._hass.services.async_call(
                    "media_player", service,
                    {}, target={"area_id": area_id}, blocking=True, context=context,
                ))
            else:
                await _await_accepted(f"media_player.{service}", self._hass.services.async_call(
                    "media_player", service,
                    {"entity_id": list(entity_ids)}, blocking=True, context=context,
                ))
        except Exception as err:  # noqa: BLE001 - executor turns this into FAILED, not a crash
            _LOGGER.warning("spellbooker: media_player.skip_track service call failed: %s", err)
            return ServiceCallOutcome(False, type(err).__name__, str(err))
        return ServiceCallOutcome(True)

    async def get_entity_state(self, entity_id: str) -> dict | None:
        state = self._hass.states.get(entity_id)
        if state is None:
            return None
        from homeassistant.helpers import area_registry as ar
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er

        entry = er.async_get(self._hass).async_get(entity_id)
        area_id = entry.area_id if entry is not None else None
        if area_id is None and entry is not None and entry.device_id:
            device = dr.async_get(self._hass).async_get(entry.device_id)
            area_id = device.area_id if device is not None else None
        area = ar.async_get(self._hass).areas.get(area_id) if area_id else None
        attributes = state.attributes
        result = {
            "state": state.state,
            "entity_id": entity_id,
            "entity_name": attributes.get("friendly_name")
            or (entry.name if entry is not None else None)
            or entity_id,
            "area": area.name if area is not None else "",
            # For multi-source query sources (core/models.py's
            # QuerySourceSpec): "device_class" disambiguates same-domain
            # sensors within one area (temperature vs humidity), and
            # "attributes" is the full raw attribute dict so kind="attribute"
            # sources can read ANY attribute HA exposes, not just the
            # curated per-domain subset added below - both additive, no
            # existing consumer of this dict's other keys is affected.
            "device_class": attributes.get("device_class"),
            "attributes": dict(attributes),
        }
        domain = entity_id.split(".", 1)[0]
        if domain == "sensor":
            result["unit"] = attributes.get("unit_of_measurement")
        elif domain == "light":
            brightness = attributes.get("brightness")
            result.update({
                "brightness": brightness,
                "brightness_percent": round(brightness * 100 / 255) if isinstance(brightness, (int, float)) else None,
                "color_temp_kelvin": attributes.get("color_temp_kelvin"),
                "rgb_color": ", ".join(map(str, attributes["rgb_color"]))
                if isinstance(attributes.get("rgb_color"), (list, tuple)) else None,
                "effect": attributes.get("effect"),
            })
        elif domain == "cover":
            result["current_position"] = attributes.get("current_position")
        elif domain == "climate":
            result.update({name: attributes.get(name) for name in (
                "current_temperature", "temperature", "hvac_action"
            )})
        elif domain == "fan":
            result.update({name: attributes.get(name) for name in ("percentage", "preset_mode")})
        elif domain == "media_player":
            volume = attributes.get("volume_level")
            result.update({
                "volume_level": volume,
                "volume_percent": round(volume * 100) if isinstance(volume, (int, float)) else None,
                "media_title": attributes.get("media_title"),
                "media_artist": attributes.get("media_artist"),
            })
        return result

    async def clean_vacuum_area(
        self, area_id: str, *, context: object | None = None
    ) -> VacuumCleanOutcome:
        """Ported from bridge.py's _handle_vacuum_clean_action /
        _try_native_clean_area: try HA's own native vacuum.clean_area
        feature first (brand-agnostic, needs only a one-time
        room-to-area mapping done in the vacuum's own entity
        settings), then fall back to the pluggable per-brand provider
        YAMLs in vacuum_provider.py. Never guesses between more than
        one matching room - see VacuumCleanOutcome's own docstring."""
        from homeassistant.helpers import area_registry as ar

        hass = self._hass
        area = ar.async_get(hass).areas.get(area_id)
        if area is None:
            return VacuumCleanOutcome(status="no_match")

        try:
            native_entity_id = await self._try_native_clean_area(area_id, context=context)
        except Exception as err:  # noqa: BLE001 - any non-rejection failure is unclear, not safe to guess about
            # _try_native_clean_area already lets through anything
            # that ISN'T a clean pre-execution rejection (a timeout, a
            # connection drop, ...) - the device may already have
            # started cleaning, so falling through to the provider
            # path here could start a SECOND vacuum on the same area.
            # Report it as failed and stop; don't guess at what
            # actually happened.
            _LOGGER.warning(
                "spellbooker: native clean_area for area %s had an unclear outcome, not falling back to provider: %s",
                area_id, err,
            )
            return VacuumCleanOutcome(status="error")
        if native_entity_id is not None:
            return VacuumCleanOutcome(status="started", vacuum_entity_id=native_entity_id, room_name=area.name)

        providers = await vacuum_provider.async_list_providers(hass)
        matches: list[_VacuumRoomMatch] = []
        for provider in providers:
            matches.extend(await _discover_vacuum_rooms(hass, provider, area_id, area.name))

        if not matches:
            return VacuumCleanOutcome(status="no_match")
        if len(matches) > 1:
            description = ", ".join(f"{m.vacuum_entity_id}/{m.room_name}" for m in matches)
            return VacuumCleanOutcome(status="ambiguous", candidates_description=description)

        match = matches[0]
        try:
            await _switch_vacuum_map(
                hass,
                match,
                context=context,
                before_side_effect=self._before_side_effect,
            )
            data = vacuum_provider.resolve_clean_service_data(match.provider, match.segment_id)
            self._before_side_effect()
            await hass.services.async_call(
                match.provider.clean["domain"],
                match.provider.clean["service"],
                data,
                target={"entity_id": match.vacuum_entity_id},
                blocking=True,
                context=context,
            )
        except Exception as err:  # noqa: BLE001 - executor turns this into FAILED, not a crash
            _LOGGER.warning("spellbooker: vacuum clean command failed: %s", err)
            return VacuumCleanOutcome(status="error")

        return VacuumCleanOutcome(status="started", vacuum_entity_id=match.vacuum_entity_id, room_name=match.room_name)

    async def call_service_returning(
        self,
        domain: str,
        service: str,
        entity_id: str,
        data: dict,
        *,
        context: object | None = None,
    ) -> dict | None:
        """Call a response-returning HA service and return the raw response dict.

        HA returns ``{entity_id: {...response data...}}``; the executor extracts
        the per-entity result using the operation's response_list_path metadata.
        Returns None on any hard error so the executor can surface FAILED.
        """
        try:
            response = await self._hass.services.async_call(
                domain,
                service,
                dict(data),
                target={"entity_id": [entity_id]},
                blocking=True,
                return_response=True,
                context=context,
            )
            return response or {}
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "spellbooker: %s.%s response-service failed for %s: %s",
                domain, service, entity_id, err,
            )
            return None

    async def _try_native_clean_area(self, area_id: str, *, context: object | None = None) -> str | None:
        from homeassistant.components.homeassistant.exposed_entities import (
            async_should_expose,
        )
        from homeassistant.exceptions import ServiceValidationError
        from homeassistant.helpers import entity_registry as er

        hass = self._hass
        registry = er.async_get(hass)
        candidates = [
            entry.entity_id
            for entry in registry.entities.values()
            if entry.domain == "vacuum"
            and entry.disabled_by is None
            and async_should_expose(hass, "conversation", entry.entity_id)
        ]
        for vacuum_entity_id in candidates:
            state = hass.states.get(vacuum_entity_id)
            if state is None:
                continue
            supported = state.attributes.get("supported_features", 0)
            if not isinstance(supported, int) or not (supported & _VACUUM_CLEAN_AREA_FEATURE):
                continue
            try:
                self._before_side_effect()
                await hass.services.async_call(
                    "vacuum",
                    "clean_area",
                    {"cleaning_area_id": [area_id]},
                    target={"entity_id": vacuum_entity_id},
                    blocking=True,
                    context=context,
                )
            except ServiceValidationError as err:
                # HA's own service-input-validation failure ("this
                # vacuum has no such cleaning area") happens before the
                # device is ever asked to do anything - provably safe
                # to try the next candidate. Needs live confirmation
                # against a real unsupported-area case (see this
                # file's own module docstring) - if a real device turns
                # out to raise something else for this, that case
                # belongs here too, not folded into a catch-all.
                # Everything else (TimeoutError, a connection drop, any
                # exception we can't positively identify as a clean
                # pre-execution rejection) propagates uncaught instead
                # of being treated as "try the next one" - the device
                # may already have accepted the command, and trying a
                # second vacuum on the same area risks running two at
                # once. clean_vacuum_area below catches it and reports
                # the outcome as unclear rather than guessing.
                _LOGGER.debug(
                    "spellbooker: native clean_area on %s for area %s rejected, trying next: %s",
                    vacuum_entity_id, area_id, err,
                )
                continue
            return vacuum_entity_id
        return None


@dataclass(frozen=True)
class _VacuumRoomMatch:
    provider: vacuum_provider.VacuumProvider
    vacuum_entity_id: str
    map_name: str
    segment_id: str
    room_name: str


def _vacuum_area_mapping(entry) -> dict[str, list[str]]:
    """Return HA's native area -> segment mapping for a vacuum entity.

    Home Assistant stores the mapping configured in the vacuum entity's
    "Zuordnung von Staubsauger-Abschnitten zu Bereichen" dialog under
    entity-registry options["vacuum"]["area_mapping"]. Keep this helper
    deliberately defensive because registry options are persisted user data.
    """
    options = entry.options.get("vacuum", {})
    if not isinstance(options, dict) and not hasattr(options, "get"):
        return {}
    mapping = options.get("area_mapping", {})
    if not isinstance(mapping, dict) and not hasattr(mapping, "items"):
        return {}

    normalized: dict[str, list[str]] = {}
    for mapped_area_id, segment_ids in mapping.items():
        if not isinstance(mapped_area_id, str):
            continue
        if not isinstance(segment_ids, (list, tuple)):
            continue
        cleaned = [str(segment_id) for segment_id in segment_ids if segment_id is not None]
        if cleaned:
            normalized[mapped_area_id] = cleaned
    return normalized


def _mapped_segments_for_area(entry, area_id: str) -> set[str]:
    return set(_vacuum_area_mapping(entry).get(area_id, ()))


async def _discover_vacuum_rooms(
    hass: HomeAssistant, provider: vacuum_provider.VacuumProvider, area_id: str, area_name: str
) -> list[_VacuumRoomMatch]:
    source = provider.discover.get("source", "service")
    if source == "service":
        return await _discover_vacuum_rooms_service(hass, provider, area_id, area_name)
    if source == "entity_attributes":
        return await _discover_vacuum_rooms_entity_attributes(hass, provider, area_id, area_name)
    return []


async def _discover_vacuum_rooms_service(
    hass: HomeAssistant, provider: vacuum_provider.VacuumProvider, area_id: str, area_name: str
) -> list[_VacuumRoomMatch]:
    from homeassistant.components.homeassistant.exposed_entities import (
        async_should_expose,
    )
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    vacuum_entity_ids = [
        entry.entity_id
        for entry in registry.entities.values()
        if entry.domain == "vacuum"
        and entry.platform == provider.platform
        and entry.disabled_by is None
        and async_should_expose(hass, "conversation", entry.entity_id)
    ]
    if not vacuum_entity_ids:
        return []

    try:
        response = await hass.services.async_call(
            provider.discover["domain"],
            provider.discover["service"],
            {},
            target={"entity_id": vacuum_entity_ids},
            blocking=True,
            return_response=True,
        )
    except Exception as err:  # noqa: BLE001 - an uninstalled/erroring provider must not block others
        _LOGGER.debug("spellbooker: vacuum provider %r discovery failed, skipping: %s", provider.id, err)
        return []

    if not isinstance(response, dict):
        return []

    maps_key = provider.discover["maps_key"]
    name_key = provider.discover["name_key"]
    rooms_key = provider.discover["rooms_key"]
    lowered_area = area_name.strip().lower()

    matches: list[_VacuumRoomMatch] = []
    for vacuum_entity_id in vacuum_entity_ids:
        entry = registry.async_get(vacuum_entity_id)
        mapped_segments = _mapped_segments_for_area(entry, area_id) if entry is not None else set()
        vacuum_has_native_mapping = bool(_vacuum_area_mapping(entry)) if entry is not None else False

        vacuum_data = response.get(vacuum_entity_id)
        if not isinstance(vacuum_data, dict):
            continue
        maps = vacuum_data.get(maps_key)
        if not isinstance(maps, list):
            continue
        for one_map in maps:
            if not isinstance(one_map, dict):
                continue
            rooms = one_map.get(rooms_key)
            if not isinstance(rooms, dict):
                continue
            map_name = str(one_map.get(name_key) or "")
            for segment_id, room_name in rooms.items():
                segment_id = str(segment_id)
                mapped_match = segment_id in mapped_segments
                # If this vacuum has HA area mappings, those mappings are the
                # source of truth. Only unmapped legacy vacuums retain the old
                # room-name fallback for backwards compatibility.
                legacy_name_match = (
                    not vacuum_has_native_mapping
                    and isinstance(room_name, str)
                    and room_name.strip().lower() == lowered_area
                )
                if mapped_match or legacy_name_match:
                    matches.append(
                        _VacuumRoomMatch(
                            provider, vacuum_entity_id, map_name, segment_id,
                            room_name if isinstance(room_name, str) else segment_id,
                        )
                    )
    return matches


async def _discover_vacuum_rooms_entity_attributes(
    hass: HomeAssistant, provider: vacuum_provider.VacuumProvider, area_id: str, area_name: str
) -> list[_VacuumRoomMatch]:
    from homeassistant.components.homeassistant.exposed_entities import (
        async_should_expose,
    )
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    entity_domain = provider.discover.get("entity_domain", "vacuum")
    vacuum_entity_ids = [
        entry.entity_id
        for entry in registry.entities.values()
        if entry.domain == entity_domain
        and entry.platform == provider.platform
        and entry.disabled_by is None
        and async_should_expose(hass, "conversation", entry.entity_id)
    ]
    if not vacuum_entity_ids:
        return []

    rooms_key = provider.discover["rooms_key"]
    room_id_key = provider.discover["room_id_key"]
    room_name_key = provider.discover["room_name_key"]
    lowered_area = area_name.strip().lower()

    matches: list[_VacuumRoomMatch] = []
    for vacuum_entity_id in vacuum_entity_ids:
        entry = registry.async_get(vacuum_entity_id)
        mapped_segments = _mapped_segments_for_area(entry, area_id) if entry is not None else set()
        vacuum_has_native_mapping = bool(_vacuum_area_mapping(entry)) if entry is not None else False

        state = hass.states.get(vacuum_entity_id)
        if state is None:
            continue
        rooms = state.attributes.get(rooms_key)
        if isinstance(rooms, dict):
            room_entries = [r for r in rooms.values() if isinstance(r, dict)]
        elif isinstance(rooms, list):
            room_entries = [r for r in rooms if isinstance(r, dict)]
        else:
            room_entries = []
        for room in room_entries:
            room_name = room.get(room_name_key)
            room_id = room.get(room_id_key)
            if room_id is None:
                continue
            room_id = str(room_id)
            mapped_match = room_id in mapped_segments
            legacy_name_match = (
                not vacuum_has_native_mapping
                and isinstance(room_name, str)
                and room_name.strip().lower() == lowered_area
            )
            if mapped_match or legacy_name_match:
                matches.append(
                    _VacuumRoomMatch(
                        provider, vacuum_entity_id, "", room_id,
                        room_name if isinstance(room_name, str) else room_id,
                    )
                )
    return matches


async def _switch_vacuum_map(
    hass: HomeAssistant,
    match: _VacuumRoomMatch,
    *,
    context: object | None = None,
    before_side_effect: Callable[[], None] | None = None,
) -> None:
    from homeassistant.helpers import entity_registry as er

    select_map = match.provider.select_map
    if not select_map:
        return
    registry = er.async_get(hass)
    vacuum_entry = registry.async_get(match.vacuum_entity_id)
    if vacuum_entry is None or vacuum_entry.device_id is None:
        return
    translation_key = select_map["translation_key"]
    select_entity_id = next(
        (
            e.entity_id
            for e in registry.entities.values()
            if e.domain == "select"
            and e.platform == match.provider.platform
            and e.device_id == vacuum_entry.device_id
            and e.translation_key == translation_key
        ),
        None,
    )
    if select_entity_id is None:
        return
    state = hass.states.get(select_entity_id)
    if state is not None and state.state == match.map_name:
        return
    if before_side_effect is not None:
        before_side_effect()
    await hass.services.async_call(
        "select",
        "select_option",
        {"option": match.map_name},
        target={"entity_id": select_entity_id},
        blocking=True,
        context=context,
    )


# ---------------------------------------------------------------------------
# Home overview (read-only MCP tool, see core/home_overview.py)
# ---------------------------------------------------------------------------


def collect_home_overview(hass: HomeAssistant) -> tuple[list[OverviewArea], list[OverviewEntity]]:
    """Plain snapshots of areas and eligible entities for the overview tool.

    Eligibility mirrors HaTargetResolver's own filter: enabled, not hidden,
    has a state, exposed to conversation (Assist) and not a group entity -
    the overview never shows anything a Spell could not target anyway.
    Policy filtering happens in core.home_overview.render_overview.
    Registry reads only, on the event loop."""
    from homeassistant.components.homeassistant.exposed_entities import (
        async_should_expose,
    )
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    area_registry = ar.async_get(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    areas = [
        OverviewArea(area.id, area.name, tuple(sorted(a for a in (area.aliases or ()) if a)))
        for area in area_registry.areas.values()
    ]
    entities: list[OverviewEntity] = []
    for entry in entity_registry.entities.values():
        domain = entry.entity_id.split(".", 1)[0]
        if domain not in OVERVIEW_DOMAINS:
            continue
        if entry.disabled_by is not None or getattr(entry, "hidden_by", None) is not None:
            continue
        state = hass.states.get(entry.entity_id)
        if state is None or isinstance(state.attributes.get("entity_id"), list):
            continue
        if not async_should_expose(hass, "conversation", entry.entity_id):
            continue
        area_id = entry.area_id
        if not area_id and entry.device_id:
            device = device_registry.devices.get(entry.device_id)
            area_id = device.area_id if device is not None else None
        name = (
            state.attributes.get("friendly_name")
            or entry.name
            or entry.original_name
            or entry.entity_id
        )
        entities.append(OverviewEntity(entry.entity_id, str(name), state.state, area_id))
    return areas, entities


# ---------------------------------------------------------------------------
# AssistForwarder
# ---------------------------------------------------------------------------


class HaAssistForwarder:
    """Real core.text_only_execution.AssistForwarder implementation - the
    exact same conversation.async_converse call providers.code27.bridge.
    _ask_assist already makes for CODE27's own assist_redirect handling,
    factored out here so every OTHER caller of the shared
    core.text_only_execution.execute_text_only_command reaches Home
    Assistant's configured conversation agent through the identical
    mechanism, never a second one. Two real callers wire this up today,
    symmetrically, neither depending on the other's own module:
    providers/mcp/transport.py (an MCP tools/call, via core.
    mcp_execution) and conversation.py (a spoken/typed HASE-Agent turn -
    native HA-Voice or a Satellite subentry - via core.router.
    SpellbookRouter directly; this never touches MCP or the network).

    Deliberately narrower than _ask_assist: no AI cleanup, no activity
    logging, no entity-correction retry loop - all CODE27-specific UX
    neither of those two callers has an equivalent surface for. Each
    caller's own response envelope (core.mcp_execution.McpExecutionResult
    for MCP, core.models.ConversationResult for a HASE-Agent turn) is
    what carries the outcome back from there instead.

    Compared against _ask_assist on the two things that actually cross the
    Home-Assistant-API boundary:
    - agent_id: _ask_assist reads a per-CODE27-Bridge CONF_BRIDGE_AGENT_ID
      option, falling back to Home Assistant's own built-in agent
      (agent_id=None) when unset. Neither an MCP Bridge nor a HASE-Agent
      entity has an equivalent option today - agent_id is always None
      here, i.e. every caller of this class behaves exactly like a
      CODE27 Bridge that left its own agent option unset. A deliberate,
      documented simplification, not a bug: adding a per-Bridge/per-Agent
      choice is a real, separate feature (new config field + Studio UI),
      not something this execution-path fix should silently grow into.
    - language: _ask_assist passes the live request's own language
      (ConversationRequest.language, resolved per CODE27 turn). An MCP
      call has no live per-call "turn" of its own - core.mcp_execution
      passes its Bridge's own configured language instead, the same
      per-Bridge-configuration-or-hass.config.language fallback chain
      providers.mcp.bridge_context.build_bridge_context_for_subentry
      already resolves for every OTHER per-Bridge decision (see its own
      docstring). A HASE-Agent turn, on the other hand, DOES have a live
      per-call language (ConversationRequest.language, resolved the same
      way CODE27's own turn is) and core.router.SpellbookRouter passes
      that directly - the natural equivalent of "the current caller's
      effective language" for each of the two shapes, never a second
      scheme invented per caller."""

    def __init__(
        self,
        hass: HomeAssistant,
        before_side_effect: Callable[[], None] | None = None,
    ) -> None:
        self._hass = hass
        self._before_side_effect = before_side_effect or (lambda: None)

    async def forward(
        self, command_text: str, *, language: str, context: object | None
    ) -> AssistForwardOutcome:
        from homeassistant.components import conversation
        from homeassistant.core import Context
        from homeassistant.helpers import intent

        try:
            self._before_side_effect()
            result = await conversation.async_converse(
                self._hass,
                text=command_text,
                conversation_id=None,
                context=context if context is not None else Context(),
                agent_id=None,
                language=language,
            )
        except Exception as err:  # noqa: BLE001 - reported as a FAILED execution, never a crash
            _LOGGER.warning("spellbooker: assist-redirect could not process %r: %s", command_text, err)
            return AssistForwardOutcome(speech=None, is_error=True, is_query_answer=False, continue_conversation=False)

        response = getattr(result, "response", None)
        speech = None
        if response is not None and getattr(response, "speech", None):
            speech = response.speech.get("plain", {}).get("speech")
        is_error = response is not None and response.response_type == intent.IntentResponseType.ERROR
        is_query_answer = response is not None and response.response_type == intent.IntentResponseType.QUERY_ANSWER
        continue_conversation = bool(getattr(result, "continue_conversation", False))
        return AssistForwardOutcome(
            speech=speech, is_error=is_error, is_query_answer=is_query_answer,
            continue_conversation=continue_conversation,
        )
