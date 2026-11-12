"""MCP provider constants - HASE MCP architecture, Phase 3.

Fields genuinely specific to an MCP Bridge only. Everything an MCP Bridge
shares in meaning with a Satellite (assigned Spellbook, action/query area
scopes) reuses the existing CONF_SPELLBOOKER_* keys from ...const directly
- see providers/mcp/config_flow.py - never redeclared here under a
different name.
"""

MANUFACTURER = "HASE"

# The MCP Server is a singleton (see config_flow.py's own
# async_mcp_server_exists) - its title is fixed, never a user-entered name,
# so "which MCP Server" is never a real question anywhere in the UI
# (including the generic HA "pick a config entry for this subentry type"
# picker every "Add MCP Bridge" action goes through - only this one entry
# ever supports the mcp_bridge subentry type at all).
MCP_SERVER_TITLE = "HASE MCP Server"

CONF_MCP_BRIDGE_AREA_ID = "mcp_bridge_area_id"

CONF_MCP_BRIDGE_LANGUAGE = "mcp_bridge_language"

CONF_MCP_BRIDGE_CREDENTIAL_VERIFIER = "mcp_bridge_credential_verifier"

CONF_MCP_BRIDGE_CREDENTIAL_SUFFIX = "mcp_bridge_credential_suffix"

# The per-installation HMAC secret's own Store key (homeassistant.helpers.
# storage.Store), same "hase.<name>" convention docs/SPELLBOOK-STUDIO.md
# already documents for .storage/hase.studio_permissions and
# .storage/hase.studio_ui - a global, not-config-entry-scoped file.
STORAGE_KEY_MCP_SECRET = "hase.mcp_secret"
STORAGE_VERSION_MCP_SECRET = 1

# The MCP Server's own listener - infrastructure, not execution semantics,
# so it lives on the server entry itself (Phase 4), configurable via
# McpServerOptionsFlow. Deliberately distinct from CODE27's own pairing
# port (8081) and Home Assistant's own (8123).
CONF_MCP_SERVER_PORT = "mcp_server_port"
DEFAULT_MCP_SERVER_PORT = 8815

# The Streamable HTTP path on the MCP Server's OWN dedicated listener -
# never HA's own /api/ namespace (this integration never merges its
# transport into hass.http - see the Phase 0B report on why). An optional
# user-managed reverse proxy is free to map any external path, including
# one under HA's own domain, onto this internal one; HASE has no part in
# configuring that proxy.
STREAMABLE_HTTP_PATH = "/mcp"
