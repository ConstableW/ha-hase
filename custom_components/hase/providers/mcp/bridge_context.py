"""Home Assistant wiring for Bridge-context construction - HASE MCP
architecture, Phase 3.

The only genuinely HA-touching piece of Bridge-context resolution: adapts
`homeassistant.helpers.area_registry` into core.mcp_bridge_context's
AreaLookup Protocol, and reads one Bridge subentry's stored configuration
into the plain values core.mcp_bridge_context.build_bridge_context needs.
All the actual resolution logic (the area_id/area_name distinction, and
the misconfigured-vs-deliberately-area-less distinction) lives in core/
and is independently tested there without any Home Assistant install -
this module is only the thin edge that supplies real data to it.
"""
from __future__ import annotations

from ...const import (
    CONF_SPELLBOOKER_ALLOWED_AREAS,
    CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS,
    CONF_SPELLBOOKER_QUERIES_UNRESTRICTED,
    CONF_SPELLBOOKER_UNRESTRICTED,
)
from ...core.mcp_bridge_context import (
    AreaInfo,
    BridgeConfigurationError,
    build_bridge_context,
)
from ...core.mcp_projection import BridgeContext
from ...core.models import AreaPolicy, ScopedAreaPolicy
from .const import CONF_MCP_BRIDGE_AREA_ID, CONF_MCP_BRIDGE_LANGUAGE


class HaAreaLookup:
    """Real core.mcp_bridge_context.AreaLookup, backed by Home Assistant's
    own area registry. A deleted/renamed-away area_id simply isn't in
    `.areas` any more - `get()` returning None for it is what lets
    core.mcp_bridge_context report a misconfigured Bridge instead of
    guessing."""

    def __init__(self, hass) -> None:
        self._hass = hass

    def get(self, area_id: str) -> AreaInfo | None:
        from homeassistant.helpers import area_registry as ar

        area = ar.async_get(self._hass).areas.get(area_id)
        return AreaInfo(id=area.id, name=area.name) if area is not None else None


def _policy_from(data: dict) -> ScopedAreaPolicy:
    actions = AreaPolicy(
        allowed_areas=frozenset(data.get(CONF_SPELLBOOKER_ALLOWED_AREAS, [])),
        unrestricted=bool(data.get(CONF_SPELLBOOKER_UNRESTRICTED, False)),
    )
    queries = AreaPolicy(
        allowed_areas=frozenset(data.get(CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS, [])),
        unrestricted=bool(data.get(CONF_SPELLBOOKER_QUERIES_UNRESTRICTED, False)),
    )
    return ScopedAreaPolicy(actions=actions, queries=queries)


def build_bridge_context_for_subentry(
    hass, subentry_data: dict, *, default_language: str,
    entry_id: str = "", subentry_id: str = "", bridge_name: str = "",
) -> BridgeContext | BridgeConfigurationError:
    """The transport's request handling calls this once it has resolved an
    authenticated credential to a Bridge subentry: turn that subentry's
    stored configuration into a ready-to-use BridgeContext, or a
    BridgeConfigurationError if its configured area no longer exists.
    Every value fed into BridgeContext comes from `subentry_data` (the
    Bridge's own trusted, persisted configuration) or `default_language`
    (the caller's own fallback, e.g. hass.config.language, mirroring
    conversation.py's own `language or hass.config.language or "de"`
    pattern) - never from anything an MCP client supplies on the wire.

    `entry_id`/`subentry_id`/`bridge_name` are optional and used ONLY to
    keep the Bridge's repair-issue state (see .repairs) in sync with
    whatever this call just found - every caller that resolves a Bridge's
    current health (Server setup and every live request alike) keeps the
    user-visible "this Bridge is broken" state fresh for free, with no
    separate polling path. Omitting them (the pure core.mcp_bridge_context
    tests do) simply skips that side effect."""
    result = build_bridge_context(
        area_id=subentry_data.get(CONF_MCP_BRIDGE_AREA_ID) or None,
        policy=_policy_from(subentry_data),
        language=subentry_data.get(CONF_MCP_BRIDGE_LANGUAGE) or default_language,
        area_lookup=HaAreaLookup(hass),
    )
    if entry_id and subentry_id:
        from .repairs import sync_bridge_area_issue

        sync_bridge_area_issue(
            hass,
            entry_id=entry_id,
            subentry_id=subentry_id,
            bridge_name=bridge_name,
            area_id=subentry_data.get(CONF_MCP_BRIDGE_AREA_ID) or None,
            is_misconfigured=isinstance(result, BridgeConfigurationError),
        )
    return result
