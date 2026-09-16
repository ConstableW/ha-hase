"""Bridge-context construction - HASE MCP architecture, Phase 3.

Resolves a Bridge's persisted configuration (a raw area_id string, plus
already-typed AreaPolicy/ScopedAreaPolicy/language pieces) into a real
core.mcp_projection.BridgeContext - or reports why it can't, instead of
silently downgrading a misconfigured Bridge into a deliberately area-less
one. Those are two different, equally legitimate-looking-from-the-outside
situations that must never be conflated: a Bridge the admin genuinely
never gave an area (MASTER-style, BridgeContext.area is simply None) versus
a Bridge whose configured area_id no longer resolves in the area registry
(deleted or renamed away from under it - a misconfiguration that must block
execution, not silently degrade).

No Home Assistant import: area lookup goes through the AreaLookup Protocol
below, injected by the caller - the real implementation (backed by
homeassistant.helpers.area_registry) lives in providers/mcp/, mirroring
core.executor's own TargetResolver/ServiceCaller Protocol split between
"what core/ needs" and "how Home Assistant actually provides it".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .mcp_projection import BridgeArea, BridgeContext
from .models import ScopedAreaPolicy


@dataclass(frozen=True)
class AreaInfo:
    """The two facts about one Home Assistant area this module ever needs
    - deliberately not the real area registry entry type, so resolution
    stays testable without Home Assistant installed."""

    id: str
    name: str


class AreaLookup(Protocol):
    def get(self, area_id: str) -> AreaInfo | None:
        """None if area_id no longer exists in the area registry - a
        deleted or renamed-away area, never guessed at or substituted."""
        ...


@dataclass(frozen=True)
class BridgeConfigurationError:
    """A Bridge's persisted configuration cannot currently be resolved
    into a usable BridgeContext - reported, never silently downgraded into
    a deliberately area-less (MASTER) Bridge. Currently the only reason:
    `"area_not_found:<area_id>"` - the Bridge's stored area_id no longer
    resolves. A caller (providers/mcp/'s request handling, in a later
    phase) must refuse to execute rather than proceed as if no area had
    ever been configured."""

    reason: str


def resolve_bridge_area(area_id: str | None, area_lookup: AreaLookup) -> BridgeArea | BridgeConfigurationError | None:
    """None: deliberately no area configured (a valid MASTER-style
    Bridge). BridgeArea: resolved successfully, carrying both the id and
    the current display name. BridgeConfigurationError: an area WAS
    configured but no longer exists - see BridgeConfigurationError's own
    docstring on why this must never collapse into None."""
    if not area_id:
        return None
    info = area_lookup.get(area_id)
    if info is None:
        return BridgeConfigurationError(f"area_not_found:{area_id}")
    return BridgeArea(area_id=info.id, area_name=info.name)


def build_bridge_context(
    *, area_id: str | None, policy: ScopedAreaPolicy, language: str, area_lookup: AreaLookup,
) -> BridgeContext | BridgeConfigurationError:
    """The full assembly a caller uses after resolving an authenticated
    credential to a Bridge: turn that Bridge's persisted configuration
    into a BridgeContext ready for core.mcp_projection/core.mcp_execution,
    or a BridgeConfigurationError if it can't be used right now. `policy`/
    `language` are trusted as-is - they come from the Bridge's own stored
    configuration, never from anything a client supplies (see
    providers/mcp/bridge_context.py for where that trust boundary sits)."""
    area = resolve_bridge_area(area_id, area_lookup)
    if isinstance(area, BridgeConfigurationError):
        return area
    return BridgeContext(area=area, policy=policy, language=language)
