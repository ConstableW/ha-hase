"""Per-installation HMAC secret persistence - HASE MCP architecture, Phase 3.

Uses Home Assistant's own `Store` helper, same "hase.<name>" convention
studio_access.py/studio.py already use for .storage/hase.studio_access,
.storage/hase.studio_permissions and .storage/hase.studio_ui. The secret
itself never appears in a log line or a diagnostics payload anywhere in
this module - `async_get_secret()` is the only way to reach it, and every
caller in this integration only ever feeds the return value straight into
providers.mcp.credentials' functions.
"""
from __future__ import annotations

import base64

from .const import STORAGE_KEY_MCP_SECRET, STORAGE_VERSION_MCP_SECRET
from .credentials import generate_secret


class McpSecretStore:
    """Loads the per-installation HMAC secret, generating and persisting a
    fresh one on first use. Generated exactly once per installation -
    never regenerated as a side effect of any single Bridge's own
    credential being (re)issued, or every previously-issued verifier
    across every Bridge would stop matching at once."""

    def __init__(self, hass) -> None:
        from homeassistant.helpers.storage import Store

        self._store = Store(hass, STORAGE_VERSION_MCP_SECRET, STORAGE_KEY_MCP_SECRET)
        self._secret: bytes | None = None

    async def async_get_secret(self) -> bytes:
        if self._secret is not None:
            return self._secret
        stored = await self._store.async_load()
        if isinstance(stored, dict) and isinstance(stored.get("secret"), str):
            try:
                self._secret = base64.b64decode(stored["secret"], validate=True)
            except (ValueError, TypeError):
                self._secret = None
        if self._secret is None:
            self._secret = generate_secret()
            await self._store.async_save({"secret": base64.b64encode(self._secret).decode("ascii")})
        return self._secret
