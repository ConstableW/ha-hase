"""HASE MCP architecture, Phase 3: core.mcp_bridge_context table-driven
tests - area_id/area_name resolution and the misconfigured-vs-area-less
distinction. Same hase_spellbook_core loading convention as the other
core.mcp_* test files.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "hase"

_core_spec = importlib.util.spec_from_file_location(
    "hase_spellbook_core", ROOT / "core/__init__.py",
    submodule_search_locations=[str(ROOT / "core")],
)
if _core_spec.name not in sys.modules:
    _core_module = importlib.util.module_from_spec(_core_spec)
    sys.modules[_core_spec.name] = _core_module
    _core_spec.loader.exec_module(_core_module)

models = importlib.import_module("hase_spellbook_core.models")
mcp_projection = importlib.import_module("hase_spellbook_core.mcp_projection")
bridge_context = importlib.import_module("hase_spellbook_core.mcp_bridge_context")

AreaPolicy = models.AreaPolicy
ScopedAreaPolicy = models.ScopedAreaPolicy
BridgeArea = mcp_projection.BridgeArea
BridgeContext = mcp_projection.BridgeContext
AreaInfo = bridge_context.AreaInfo
BridgeConfigurationError = bridge_context.BridgeConfigurationError
resolve_bridge_area = bridge_context.resolve_bridge_area
build_bridge_context = bridge_context.build_bridge_context


class FakeAreaLookup:
    """Stands in for the real Home Assistant area-registry-backed
    AreaLookup (see providers/mcp/bridge_context.py) - a plain dict is
    enough to exercise resolve_bridge_area()'s own logic in isolation."""

    def __init__(self, areas: dict[str, AreaInfo] | None = None):
        self._areas = areas or {}

    def get(self, area_id: str) -> AreaInfo | None:
        return self._areas.get(area_id)


def scoped_unrestricted() -> ScopedAreaPolicy:
    p = AreaPolicy(allowed_areas=None, unrestricted=True)
    return ScopedAreaPolicy(actions=p, queries=p)


class ResolveBridgeAreaTests(unittest.TestCase):
    def test_none_area_id_is_deliberately_area_less(self):
        result = resolve_bridge_area(None, FakeAreaLookup())
        self.assertIsNone(result)

    def test_empty_string_area_id_is_deliberately_area_less(self):
        result = resolve_bridge_area("", FakeAreaLookup())
        self.assertIsNone(result)

    def test_configured_area_id_resolves_to_both_id_and_name(self):
        lookup = FakeAreaLookup({"wohnzimmer": AreaInfo(id="wohnzimmer", name="Wohnzimmer")})
        result = resolve_bridge_area("wohnzimmer", lookup)
        self.assertIsInstance(result, BridgeArea)
        self.assertEqual(result.area_id, "wohnzimmer")
        self.assertEqual(result.area_name, "Wohnzimmer")

    def test_deleted_area_id_is_reported_not_silently_dropped(self):
        result = resolve_bridge_area("no_longer_exists", FakeAreaLookup())
        self.assertIsInstance(result, BridgeConfigurationError)
        self.assertEqual(result.reason, "area_not_found:no_longer_exists")

    def test_deleted_area_id_is_never_confused_with_deliberate_area_less(self):
        # The two must be structurally distinguishable by type, not just
        # by convention - a caller that only checks `is None` for
        # "area-less" would otherwise treat a misconfigured Bridge as a
        # valid MASTER one.
        deliberate = resolve_bridge_area(None, FakeAreaLookup())
        misconfigured = resolve_bridge_area("gone", FakeAreaLookup())
        self.assertIsNone(deliberate)
        self.assertNotIsInstance(misconfigured, type(None))
        self.assertIsInstance(misconfigured, BridgeConfigurationError)


class BuildBridgeContextTests(unittest.TestCase):
    def test_area_less_bridge_is_valid(self):
        result = build_bridge_context(
            area_id=None, policy=scoped_unrestricted(), language="de", area_lookup=FakeAreaLookup(),
        )
        self.assertIsInstance(result, BridgeContext)
        self.assertIsNone(result.area)

    def test_resolved_area_bridge_carries_both_id_and_name(self):
        lookup = FakeAreaLookup({"buero": AreaInfo(id="buero", name="Büro")})
        result = build_bridge_context(
            area_id="buero", policy=scoped_unrestricted(), language="de", area_lookup=lookup,
        )
        self.assertIsInstance(result, BridgeContext)
        self.assertEqual(result.area.area_id, "buero")
        self.assertEqual(result.area.area_name, "Büro")

    def test_invalid_area_id_never_becomes_an_area_less_master_bridge(self):
        result = build_bridge_context(
            area_id="deleted_area", policy=scoped_unrestricted(), language="de", area_lookup=FakeAreaLookup(),
        )
        self.assertIsInstance(result, BridgeConfigurationError)
        self.assertNotIsInstance(result, BridgeContext)

    def test_policy_and_language_pass_through_unmodified(self):
        p = ScopedAreaPolicy(
            actions=AreaPolicy(allowed_areas=frozenset({"a"})),
            queries=AreaPolicy(allowed_areas=frozenset({"a", "b"})),
        )
        result = build_bridge_context(area_id=None, policy=p, language="fr", area_lookup=FakeAreaLookup())
        self.assertIs(result.policy, p)
        self.assertEqual(result.language, "fr")


if __name__ == "__main__":
    unittest.main()
