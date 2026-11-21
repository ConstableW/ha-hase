"""Music-mode-after-recovery inconsistency - user-visible surfacing.

A device that just recovered from `unavailable` and immediately reports
its music page as open cannot be trusted blindly (see coordinator.py's
own comment on _async_update_data - confirmed live across two separate
reboots, sometimes as a stale pre-outage cache echo and sometimes as a
freshly timestamped but still-wrong value spanning several clean polls,
suspected CODE27 firmware behaviour HASE cannot resolve by guessing).
Rather than silently picking a value that might itself be wrong, this
surfaces the inconsistency via Home Assistant's own Repairs mechanism and
tells the user the one manual action that has reliably resolved it live:
toggle into music mode and back on the device once, to force a real
resync.
"""
from __future__ import annotations

from ...const import DOMAIN


def _issue_id(entry_id: str) -> str:
    return f"code27_music_mode_recovery_{entry_id}"


def sync_music_mode_recovery_issue(hass, *, entry_id: str, suspected: bool) -> None:
    """Creates (or refreshes) the repair issue while the inconsistency is
    suspected, and clears it once the coordinator's own outage/recovery
    logic no longer flags it - called every poll cycle from
    _async_update_data, so it never needs a separate health-check path."""
    from homeassistant.helpers import issue_registry as ir

    issue_id = _issue_id(entry_id)
    if suspected:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="code27_music_mode_recovery",
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
