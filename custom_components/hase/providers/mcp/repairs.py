"""Misconfigured-Bridge user-visible surfacing - HASE MCP architecture,
final completion pass.

A Bridge whose configured area_id no longer resolves must be visible to
the Home Assistant user as broken - not just a log line an admin might
never read. Home Assistant's own Repairs/issue registry is the existing,
native mechanism for exactly this ("prefer existing HA/HASE mechanisms...
such as a repair issue" - no second, MCP-specific error UI/architecture).

Never raised for a deliberately area-less Bridge (BridgeContext.area is
simply None) - that is a healthy, valid configuration, not an issue.
"""
from __future__ import annotations

from ...const import DOMAIN


def _issue_id(entry_id: str, subentry_id: str) -> str:
    return f"mcp_bridge_invalid_area_{entry_id}_{subentry_id}"


def sync_bridge_area_issue(
    hass, *, entry_id: str, subentry_id: str, bridge_name: str, area_id: str | None, is_misconfigured: bool,
) -> None:
    """Creates (or refreshes) a repair issue while a Bridge's configured
    area_id doesn't resolve, and clears it the moment that stops being
    true - called from build_bridge_context_for_subentry, so every place
    that already resolves a Bridge's current health (Server setup and
    every live request alike) keeps this in sync for free, without a
    separate polling/health-check path to maintain."""
    from homeassistant.helpers import issue_registry as ir

    issue_id = _issue_id(entry_id, subentry_id)
    if is_misconfigured:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="mcp_bridge_invalid_area",
            translation_placeholders={"bridge_name": bridge_name, "area_id": area_id or ""},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


def clear_bridge_issue(hass, *, entry_id: str, subentry_id: str) -> None:
    """A removed Bridge can never be "misconfigured" again, so any stale
    issue for it must go too."""
    from homeassistant.helpers import issue_registry as ir

    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry_id, subentry_id))


def gc_stale_bridge_issues(hass, *, entry_id: str, current_subentry_ids) -> None:
    """Clears any HASE MCP Bridge repair issue whose Bridge is no longer
    among this Server's current subentries - the "Bridge was deleted"
    half of keeping the repair issue accurate, since a deleted subentry is
    simply absent from future health checks rather than producing its own
    explicit "now clear" event. Called once per MCP Server setup/reload
    (see providers/mcp/__init__.py), which already runs on every Bridge
    add/edit/regenerate/delete via the entry's own "any change reloads"
    update listener."""
    from homeassistant.helpers import issue_registry as ir

    registry = ir.async_get(hass)
    prefix = f"mcp_bridge_invalid_area_{entry_id}_"
    current = set(current_subentry_ids)
    for issue in list(registry.issues.values()):
        if issue.domain != DOMAIN or not issue.issue_id.startswith(prefix):
            continue
        subentry_id = issue.issue_id[len(prefix):]
        if subentry_id not in current:
            ir.async_delete_issue(hass, DOMAIN, issue.issue_id)
