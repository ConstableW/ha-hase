"""Focused tests for the generic HASE provider boundary."""

from __future__ import annotations

import importlib.util
import logging
import sys
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HASE = ROOT / "custom_components" / "hase"


def _load(name: str, path: Path, *, package: bool = False):
    spec = importlib.util.spec_from_file_location(
        name,
        path,
        submodule_search_locations=[str(path.parent)] if package else None,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ProviderArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.names = [
            "hase_arch",
            "hase_arch.const",
            "hase_arch.providers",
            "hase_arch.providers.provider",
            "hase_arch.providers.discovery",
            "hase_arch.providers.registry",
        ]
        package = types.ModuleType("hase_arch")
        package.__path__ = [str(HASE)]
        sys.modules["hase_arch"] = package
        _load("hase_arch.const", HASE / "const.py")
        _load("hase_arch.providers", HASE / "providers" / "__init__.py", package=True)
        cls.contract = _load(
            "hase_arch.providers.provider", HASE / "providers" / "provider.py"
        )
        cls.discovery = _load(
            "hase_arch.providers.discovery", HASE / "providers" / "discovery.py"
        )
        cls.registry_module = _load(
            "hase_arch.providers.registry", HASE / "providers" / "registry.py"
        )

    @classmethod
    def tearDownClass(cls):
        for name in cls.names:
            sys.modules.pop(name, None)

    def descriptor(self, provider_id: str, *entry_kinds: str):
        async def setup(hass, entry):
            return True

        return self.contract.ProviderDescriptor(
            id=provider_id,
            name=provider_id.upper(),
            version="1.0.0",
            entry_kinds=frozenset(entry_kinds),
            minimum_hase_version=None,
            async_setup_entry=setup,
            async_unload_entry=setup,
        )

    def test_duplicate_provider_id_rejects_later_provider_atomically(self):
        registry = self.registry_module.ProviderRegistry()
        first = self.descriptor("same", "first_kind")
        later = self.descriptor("same", "later_kind")
        self.assertTrue(registry.register(first))
        with self.assertLogs(level=logging.ERROR):
            self.assertFalse(registry.register(later))
        self.assertIs(registry.get("same"), first)
        self.assertIsNone(registry.resolve_entry_kind("later_kind"))

    def test_entry_kind_conflict_rejects_whole_later_provider(self):
        registry = self.registry_module.ProviderRegistry()
        first = self.descriptor("first", "owned")
        later = self.descriptor("later", "owned", "unclaimed")
        registry.register(first)
        with self.assertLogs(level=logging.ERROR):
            self.assertFalse(registry.register(later))
        self.assertIsNone(registry.get("later"))
        self.assertIsNone(registry.resolve_entry_kind("unclaimed"))
        self.assertIs(registry.resolve_entry_kind("owned"), first)

    def test_unknown_and_missing_entry_kinds_do_not_fall_back(self):
        registry = self.registry_module.ProviderRegistry()
        registry.register(self.descriptor("known", "known_kind"))
        unknown = types.SimpleNamespace(data={"entry_kind": "unknown_kind"})
        missing = types.SimpleNamespace(data={})
        self.assertIsNone(registry.resolve_entry(unknown))
        self.assertIsNone(registry.resolve_entry(missing))

    def test_legacy_matcher_is_provider_owned(self):
        provider = replace(
            self.descriptor("legacy", "current"),
            legacy_entry_matcher=lambda entry: "old_key" in entry.data,
        )
        registry = self.registry_module.ProviderRegistry()
        registry.register(provider)
        self.assertIs(
            registry.resolve_entry(types.SimpleNamespace(data={"old_key": True})),
            provider,
        )

    def test_broken_discovered_provider_is_isolated(self):
        good = self.descriptor("good", "good_kind")
        good_module = types.SimpleNamespace(
            __name__="hase_arch.providers.good", PROVIDER=good
        )

        def import_candidate(name):
            if name.endswith("broken"):
                raise RuntimeError("broken optional provider")
            return good_module

        with (
            patch.object(
                self.discovery,
                "_candidate_module_names",
                return_value=(
                    "hase_arch.providers.broken",
                    "hase_arch.providers.good",
                ),
            ),
            patch.object(self.discovery.importlib, "import_module", import_candidate),
            self.assertLogs(level=logging.ERROR),
        ):
            self.assertEqual(self.discovery.discover_bundled_providers(), (good,))

    def test_get_or_create_attaches_one_shared_registry(self):
        shared = self.registry_module.ProviderRegistry()
        first_hass = types.SimpleNamespace(data={})
        second_hass = types.SimpleNamespace(data={})
        with patch.object(
            self.registry_module, "_get_discovered_registry", return_value=shared
        ):
            first = self.registry_module.get_provider_registry(first_hass)
            second = self.registry_module.get_provider_registry(second_hass)
        self.assertIs(first, shared)
        self.assertIs(second, shared)
        self.assertIs(
            first_hass.data["hase"][self.registry_module.DATA_PROVIDER_REGISTRY],
            shared,
        )


if __name__ == "__main__":
    unittest.main()
