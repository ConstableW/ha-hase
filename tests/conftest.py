"""Pytest bootstrap for HASE tests without a Home Assistant installation.

The repository's pure/core tests import modules below ``custom_components.hase``.
Normal Python package loading would execute ``custom_components/hase/__init__.py``
first, which imports Home Assistant.  For unit tests that do not require a real
HA runtime, expose the package paths without executing that integration entry
point.

This is test infrastructure only; production code is untouched.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CUSTOM_COMPONENTS = ROOT / "custom_components"
HASE = CUSTOM_COMPONENTS / "hase"


def _namespace_package(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    module.__package__ = name
    sys.modules[name] = module


# Prevent Python from executing custom_components/hase/__init__.py merely to
# reach custom_components.hase.core.* during test collection.
_namespace_package("custom_components", CUSTOM_COMPONENTS)
_namespace_package("custom_components.hase", HASE)
