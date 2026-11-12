"""Diagnostics entry point for HASE and its device providers."""
from __future__ import annotations

from .const import (
    CONF_ENTRY_KIND,
    ENTRY_KIND_CODE27_DEVICE,
    ENTRY_KIND_CODE27_EXTERNAL_HUB,
)


async def async_get_config_entry_diagnostics(hass, entry):
    kind = entry.data.get(CONF_ENTRY_KIND)
    if kind == ENTRY_KIND_CODE27_EXTERNAL_HUB:
        from .code27_external import EXPECTED_CONTRACT_VERSION

        binding = hass.data.get("hase", {}).get(entry.entry_id)
        return {
            "entry_kind": kind,
            "external_hub_bound": bool(binding and binding.hub is not None),
            "external_hub_reason": binding.reason if binding else "not_loaded",
            "expected_contract_version": EXPECTED_CONTRACT_VERSION,
            "actual_contract_version": binding.observed_contract_version if binding else None,
        }
    if kind == ENTRY_KIND_CODE27_DEVICE or (kind is None and "host" in entry.data):
        from .providers.code27.diagnostics import (
            async_get_config_entry_diagnostics as diagnostics,
        )

        return await diagnostics(hass, entry)
    return {"entry_kind": kind}


async def async_get_device_diagnostics(hass, entry, device):
    kind = entry.data.get(CONF_ENTRY_KIND)
    if kind == ENTRY_KIND_CODE27_DEVICE or (kind is None and "host" in entry.data):
        from .providers.code27.diagnostics import (
            async_get_device_diagnostics as diagnostics,
        )

        return await diagnostics(hass, entry, device)
    return {"entry_kind": kind}
