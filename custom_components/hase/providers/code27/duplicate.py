"""Cross-domain CODE27 setup guard for identities the device can prove."""

from __future__ import annotations

from .const import CONF_MACHINE_ID, CONF_USER_ID


def find_existing_device_entry(hass, machine_id: str, *, excluding: str | None = None):
    """Find a known matching HASE or standalone entry.

    A mismatch is not proof of different hardware: CODE27's machine_id may
    change after pairing. This guard only claims a positive match.
    """
    entries = hass.config_entries.async_entries
    for domain in ("hase", "code27"):
        for entry in entries(domain):
            if entry.entry_id == excluding:
                continue
            if domain == "hase" and entry.data.get("entry_kind") not in (None, "code27_device"):
                continue
            if domain == "hase" and entry.data.get("entry_kind") is None and "host" not in entry.data:
                continue
            stored = entry.data.get(CONF_MACHINE_ID) or entry.unique_id or entry.data.get(CONF_USER_ID)
            if stored and stored == machine_id:
                return entry
    return None
