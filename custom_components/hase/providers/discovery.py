"""Discovery source for provider packages bundled with HASE."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Iterable
from types import ModuleType

from . import __path__ as provider_paths
from .provider import ProviderDescriptor

_LOGGER = logging.getLogger(__name__)
_PROVIDER_ATTRIBUTE = "PROVIDER"


def _candidate_module_names() -> Iterable[str]:
    prefix = f"{__package__}."
    for module_info in pkgutil.iter_modules(provider_paths, prefix):
        short_name = module_info.name.rsplit(".", 1)[-1]
        if module_info.ispkg and not short_name.startswith("_"):
            yield module_info.name


def _descriptor_from_module(module: ModuleType) -> ProviderDescriptor | None:
    descriptor = getattr(module, _PROVIDER_ATTRIBUTE, None)
    if not isinstance(descriptor, ProviderDescriptor):
        _LOGGER.error(
            "Ignoring bundled HASE provider package %s: it does not expose a valid %s",
            module.__name__,
            _PROVIDER_ATTRIBUTE,
        )
        return None
    return descriptor


def discover_bundled_providers() -> tuple[ProviderDescriptor, ...]:
    """Import and return valid bundled providers, isolating each failure."""
    discovered: list[ProviderDescriptor] = []
    for module_name in _candidate_module_names():
        try:
            module = importlib.import_module(module_name)
        except Exception:  # noqa: BLE001 - one optional plugin must not stop HASE
            _LOGGER.exception("Failed to import bundled HASE provider %s", module_name)
            continue
        descriptor = _descriptor_from_module(module)
        if descriptor is not None:
            discovered.append(descriptor)
    return tuple(discovered)
