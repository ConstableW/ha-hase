"""Provider registry and its shared Home Assistant lifetime."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from ..const import CONF_ENTRY_KIND, DOMAIN
from .discovery import discover_bundled_providers
from .provider import ProviderDescriptor

_LOGGER = logging.getLogger(__name__)
DATA_PROVIDER_REGISTRY = "provider_registry"


class ProviderRegistry:
    """Atomically registers providers and their entry-kind ownership."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderDescriptor] = {}
        self._entry_kinds: dict[str, ProviderDescriptor] = {}

    def register(self, provider: ProviderDescriptor) -> bool:
        """Register one provider, rejecting all conflicts without mutation."""
        error = self._validation_error(provider)
        if error is not None:
            _LOGGER.error("Rejected HASE provider %r: %s", getattr(provider, "id", None), error)
            return False

        duplicate = self._providers.get(provider.id)
        if duplicate is not None:
            _LOGGER.error(
                "Rejected HASE provider %s: provider ID is already owned by %s",
                provider.id,
                duplicate.name,
            )
            return False

        conflicts = {
            kind: self._entry_kinds[kind]
            for kind in provider.entry_kinds
            if kind in self._entry_kinds
        }
        if conflicts:
            owners = ", ".join(
                f"{kind} ({owner.id})" for kind, owner in sorted(conflicts.items())
            )
            _LOGGER.error(
                "Rejected HASE provider %s: entry kind ownership conflicts with %s",
                provider.id,
                owners,
            )
            return False

        self._providers[provider.id] = provider
        self._entry_kinds.update({kind: provider for kind in provider.entry_kinds})
        return True

    def register_many(self, providers: Iterable[ProviderDescriptor]) -> None:
        for provider in providers:
            self.register(provider)

    @staticmethod
    def _validation_error(provider: object) -> str | None:
        if not isinstance(provider, ProviderDescriptor):
            return "object does not implement ProviderDescriptor"
        for field in ("id", "name", "version"):
            if not isinstance(getattr(provider, field), str) or not getattr(provider, field):
                return f"{field} must be a non-empty string"
        if not isinstance(provider.entry_kinds, frozenset) or not provider.entry_kinds:
            return "entry_kinds must be a non-empty frozenset"
        if any(not isinstance(kind, str) or not kind for kind in provider.entry_kinds):
            return "entry_kinds must contain only non-empty strings"
        if provider.minimum_hase_version is not None and not isinstance(
            provider.minimum_hase_version, str
        ):
            return "minimum_hase_version must be a string or None"
        if not callable(provider.async_setup_entry) or not callable(
            provider.async_unload_entry
        ):
            return "setup and unload handlers must be callable"
        return None

    @property
    def providers(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(self._providers.values())

    def get(self, provider_id: str) -> ProviderDescriptor | None:
        return self._providers.get(provider_id)

    def resolve_entry_kind(self, entry_kind: str) -> ProviderDescriptor | None:
        return self._entry_kinds.get(entry_kind)

    def resolve_entry(self, entry: Any) -> ProviderDescriptor | None:
        entry_kind = entry.data.get(CONF_ENTRY_KIND)
        if entry_kind is not None:
            return self.resolve_entry_kind(entry_kind)
        for provider in self._providers.values():
            matcher = provider.legacy_entry_matcher
            if matcher is not None and matcher(entry):
                return provider
        return None


_discovered_registry: ProviderRegistry | None = None


def _get_discovered_registry() -> ProviderRegistry:
    global _discovered_registry
    if _discovered_registry is None:
        registry = ProviderRegistry()
        registry.register_many(discover_bundled_providers())
        _discovered_registry = registry
    return _discovered_registry


def get_provider_registry(hass: Any | None = None) -> ProviderRegistry:
    """Return the one discovered registry and attach it to hass when available."""
    registry = _get_discovered_registry()
    if hass is None:
        return registry
    domain_data = hass.data.setdefault(DOMAIN, {})
    existing = domain_data.get(DATA_PROVIDER_REGISTRY)
    if existing is None:
        domain_data[DATA_PROVIDER_REGISTRY] = registry
        return registry
    return existing
