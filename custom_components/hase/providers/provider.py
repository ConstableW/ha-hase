"""Public contract implemented by every HASE provider."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

ProviderEntryHandler = Callable[[Any, Any], Awaitable[bool]]
ProviderAvailability = Callable[[Any], bool]
ProviderOptionsFlowFactory = Callable[[Any], Any]
LegacyEntryMatcher = Callable[[Any], bool]


@dataclass(frozen=True, slots=True)
class ProviderConfigFlow:
    """Optional Home Assistant config-flow surfaces owned by a provider."""

    step_id: str
    flow_mixin: type[Any]
    is_available: ProviderAvailability
    options_flow_factory: ProviderOptionsFlowFactory | None = None
    subentry_flows: tuple[tuple[str, type[Any]], ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Metadata and config-entry lifecycle exposed by a HASE provider."""

    id: str
    name: str
    version: str
    entry_kinds: frozenset[str]
    minimum_hase_version: str | None
    async_setup_entry: ProviderEntryHandler
    async_unload_entry: ProviderEntryHandler
    config_flow: ProviderConfigFlow | None = None
    legacy_entry_matcher: LegacyEntryMatcher | None = None
