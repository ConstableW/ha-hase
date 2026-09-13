"""Regression coverage for spellbooker_ha.py's pure-logic helpers.
Loaded via test_bridge_delivery's load_source (AST-stripped of
homeassistant/relative imports) - none of these functions actually
touch homeassistant at module-exec time (all real HA imports inside
them are function-local, gated behind TYPE_CHECKING, or simply unused
by the specific function under test), so no fakes are needed at load
time. A few functions (_switch_vacuum_map) DO import
homeassistant.helpers.* inside their own body, at call time - those
tests inject a minimal fake module into sys.modules first.
"""
import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import test_bridge_delivery as support

HASE_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "hase"


def _load_core_executor_module():
    if "hase_spellbook_core" in sys.modules:
        core_pkg = sys.modules["hase_spellbook_core"]
    else:
        spec = importlib.util.spec_from_file_location(
            "hase_spellbook_core",
            HASE_ROOT / "core" / "__init__.py",
            submodule_search_locations=[str(HASE_ROOT / "core")],
        )
        core_pkg = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = core_pkg
        spec.loader.exec_module(core_pkg)
    import importlib as _importlib
    return _importlib.import_module("hase_spellbook_core.executor")


_executor_mod = _load_core_executor_module()

spellbooker_ha = support.load_source(
    "spellbooker_ha",
    ResolvedEntity=_executor_mod.ResolvedEntity,
    ResolvedTargets=_executor_mod.ResolvedTargets,
    TargetNotFound=_executor_mod.TargetNotFound,
    VacuumCleanOutcome=_executor_mod.VacuumCleanOutcome,
    vacuum_provider=types.SimpleNamespace(),
)


class FakeArea:
    def __init__(self, id, name, aliases=()):
        self.id = id
        self.name = name
        self.aliases = list(aliases)


class FakeAreaRegistry:
    def __init__(self, areas):
        self.areas = {a.id: a for a in areas}


class FakeDevice:
    def __init__(self, area_id):
        self.area_id = area_id


class FakeDeviceRegistry:
    def __init__(self, devices):
        self.devices = devices

    def async_get(self, device_id):
        return self.devices.get(device_id)


class ResolveSourceAreaIdTests(unittest.TestCase):
    """core/models.py's source_area_id: the HA adapter's own job of
    turning a device_id into an area_id, with no fallback to guessing
    when either piece is missing."""

    def setUp(self):
        self.devices = {"device-samwise": FakeDevice(area_id="zwergstadt")}
        sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
        sys.modules.setdefault("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
        sys.modules["homeassistant.helpers.device_registry"] = types.SimpleNamespace(
            async_get=lambda hass: FakeDeviceRegistry(self.devices)
        )
        self.hass = types.SimpleNamespace()

    def test_resolves_the_devices_own_area(self):
        area_id = spellbooker_ha.resolve_source_area_id(self.hass, "device-samwise")
        self.assertEqual(area_id, "zwergstadt")

    def test_no_device_id_returns_none(self):
        self.assertIsNone(spellbooker_ha.resolve_source_area_id(self.hass, None))

    def test_unknown_device_id_returns_none(self):
        self.assertIsNone(spellbooker_ha.resolve_source_area_id(self.hass, "device-ghost"))

    def test_device_with_no_area_returns_none(self):
        self.devices["device-homeless"] = FakeDevice(area_id=None)
        self.assertIsNone(spellbooker_ha.resolve_source_area_id(self.hass, "device-homeless"))


class FindAreaByNameTests(unittest.TestCase):
    def test_matches_by_canonical_name(self):
        registry = FakeAreaRegistry([FakeArea("buero", "Buero")])
        area = spellbooker_ha._find_area_by_name(registry, "Buero")
        self.assertEqual(area.id, "buero")

    def test_matches_by_unique_alias(self):
        registry = FakeAreaRegistry([FakeArea("zwergstadt", "Zwergstadt", aliases=["Gondor"])])
        area = spellbooker_ha._find_area_by_name(registry, "gondor")
        self.assertEqual(area.id, "zwergstadt")

    def test_no_match_returns_none(self):
        registry = FakeAreaRegistry([FakeArea("buero", "Buero")])
        self.assertIsNone(spellbooker_ha._find_area_by_name(registry, "Nirgendwo"))

    def test_duplicate_alias_across_two_areas_is_rejected_not_guessed(self):
        # Found via a second-round audit: nothing in HA's own area
        # registry stops two DIFFERENT areas from both declaring the
        # same alias. Returning whichever came first in registry
        # iteration order would silently execute in the wrong room -
        # ambiguous must resolve to nothing, same "ask rather than
        # guess" rule this codebase applies to entity names.
        registry = FakeAreaRegistry([
            FakeArea("kitchen", "Kitchen", aliases=["Gondor"]),
            FakeArea("office", "Office", aliases=["Gondor"]),
        ])
        self.assertIsNone(spellbooker_ha._find_area_by_name(registry, "Gondor"))

    def test_same_area_aliased_twice_is_not_a_false_ambiguity(self):
        # One area with the SAME alias listed twice (or matching case-
        # insensitively twice) must still resolve - only genuinely
        # DIFFERENT areas competing for one alias is ambiguous.
        registry = FakeAreaRegistry([FakeArea("zwergstadt", "Zwergstadt", aliases=["Gondor", "gondor"])])
        area = spellbooker_ha._find_area_by_name(registry, "Gondor")
        self.assertEqual(area.id, "zwergstadt")


class FakeEntry:
    def __init__(self, entity_id, domain, platform=None, device_id=None, translation_key=None):
        self.entity_id = entity_id
        self.domain = domain
        self.platform = platform
        self.device_id = device_id
        self.translation_key = translation_key


class FakeEntityRegistry:
    def __init__(self, entities):
        self.entities = {e.entity_id: e for e in entities}

    def async_get(self, entity_id):
        return self.entities.get(entity_id)


class FakeProvider:
    platform = "roborock"
    select_map = {"translation_key": "selected_map"}


class SwitchVacuumMapTests(unittest.IsolatedAsyncioTestCase):
    """_switch_vacuum_map has its own `from homeassistant.helpers import
    entity_registry as er` inside the function body (a real, runtime
    import, not stripped by load_source) - inject a fake module for it
    before calling."""

    def setUp(self):
        registry = FakeEntityRegistry([
            FakeEntry("vacuum.kreacher", "vacuum", device_id="dev1"),
            FakeEntry(
                "select.kreacher_map", "select",
                platform="roborock", device_id="dev1", translation_key="selected_map",
            ),
        ])
        fake_er_module = types.SimpleNamespace(async_get=lambda hass: registry)
        sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
        sys.modules.setdefault("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
        sys.modules["homeassistant.helpers.entity_registry"] = fake_er_module

        self.hass = types.SimpleNamespace()
        self.hass.states = types.SimpleNamespace(get=lambda entity_id: None)
        self.hass.services = types.SimpleNamespace(async_call=AsyncMock())
        self.match = spellbooker_ha._VacuumRoomMatch(
            provider=FakeProvider(), vacuum_entity_id="vacuum.kreacher",
            map_name="EG", segment_id="2", room_name="Zwergstadt",
        )

    async def test_forwards_the_conversation_context_to_the_map_switch(self):
        # Found via a second-round audit: the actual clean_area service
        # call carries the turn's HA Context, but the map-switch step
        # right before it did not - Logbook/History would show the
        # room-cleaning service call attributed to the right turn, but
        # the map switch that made it possible attributed to no one.
        sentinel_context = object()
        await spellbooker_ha._switch_vacuum_map(self.hass, self.match, context=sentinel_context)
        self.hass.services.async_call.assert_awaited_once_with(
            "select", "select_option", {"option": "EG"},
            target={"entity_id": "select.kreacher_map"}, blocking=True, context=sentinel_context,
        )


class FakeArea2:
    def __init__(self, id, name):
        self.id = id
        self.name = name


class FakeAreaRegistry2:
    def __init__(self, areas):
        self.areas = {a.id: a for a in areas}


class FakeVacuumEntry:
    domain = "vacuum"
    disabled_by = None

    def __init__(self, entity_id):
        self.entity_id = entity_id


class FakeEntityRegistry2:
    def __init__(self, entries):
        self.entities = {e.entity_id: e for e in entries}


class FakeState:
    def __init__(self, supported_features):
        self.attributes = {"supported_features": supported_features}


def _install_vacuum_fakes(area_registry, entity_registry, states):
    ha_module = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
    helpers_module = sys.modules.setdefault("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
    sys.modules["homeassistant.helpers.area_registry"] = types.SimpleNamespace(async_get=lambda hass: area_registry)
    sys.modules["homeassistant.helpers.entity_registry"] = types.SimpleNamespace(async_get=lambda hass: entity_registry)

    class ServiceValidationError(Exception):
        pass

    sys.modules["homeassistant.exceptions"] = types.SimpleNamespace(ServiceValidationError=ServiceValidationError)

    components_module = sys.modules.setdefault(
        "homeassistant.components", types.ModuleType("homeassistant.components")
    )
    ha_components_module = sys.modules.setdefault(
        "homeassistant.components.homeassistant", types.ModuleType("homeassistant.components.homeassistant")
    )
    sys.modules["homeassistant.components.homeassistant.exposed_entities"] = types.SimpleNamespace(
        async_should_expose=lambda hass, category, entity_id: True
    )
    return ServiceValidationError


class VacuumUnclearOutcomeTests(unittest.IsolatedAsyncioTestCase):
    """clean_vacuum_area's native attempt: a clean pre-execution
    rejection (ServiceValidationError) is safe to try the next vacuum
    for, but anything else (timeout, connection drop, ...) must stop
    and report the outcome as unclear - the device may already have
    accepted the command."""

    def setUp(self):
        self.area_registry = FakeAreaRegistry2([FakeArea2("zwergstadt", "Zwergstadt")])
        self.entities = [FakeVacuumEntry("vacuum.a"), FakeVacuumEntry("vacuum.b")]
        self.entity_registry = FakeEntityRegistry2(self.entities)
        self.states = {"vacuum.a": FakeState(16384), "vacuum.b": FakeState(16384)}
        self.ServiceValidationError = _install_vacuum_fakes(self.area_registry, self.entity_registry, self.states)

        self.hass = types.SimpleNamespace()
        self.hass.states = types.SimpleNamespace(get=lambda eid: self.states.get(eid))
        self.hass.services = types.SimpleNamespace(async_call=AsyncMock())
        self.caller = spellbooker_ha.HaServiceCaller(self.hass)

    async def test_rejection_from_one_vacuum_tries_the_next(self):
        async def side_effect(domain, service, data, *, target, blocking, context=None):
            if target["entity_id"] == "vacuum.a":
                raise self.ServiceValidationError("no such cleaning area")
            return None

        self.hass.services.async_call.side_effect = side_effect
        outcome = await self.caller.clean_vacuum_area("zwergstadt")
        self.assertEqual(outcome.status, "started")
        self.assertEqual(outcome.vacuum_entity_id, "vacuum.b")

    async def test_unclear_failure_from_one_vacuum_stops_immediately(self):
        # Found via a second-round audit: only TimeoutError used to be
        # treated this way - a plain connection-drop-style exception
        # from the SAME native attempt used to be swallowed as "this
        # vacuum doesn't cover the area" and silently tried the next
        # one, risking two vacuums cleaning the same area at once.
        async def side_effect(domain, service, data, *, target, blocking, context=None):
            if target["entity_id"] == "vacuum.a":
                raise ConnectionError("device unreachable")
            return None

        self.hass.services.async_call.side_effect = side_effect
        outcome = await self.caller.clean_vacuum_area("zwergstadt")
        self.assertEqual(outcome.status, "error")
        # vacuum.b was never tried - an unclear outcome must not fall
        # through to another candidate.
        self.assertEqual(self.hass.services.async_call.await_count, 1)


if __name__ == "__main__":
    unittest.main()
