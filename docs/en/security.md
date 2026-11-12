🇩🇪 [Deutsch](../de/security.md) | 🇬🇧 English

# Security model

HASE separates authoring, structural projection, metadata and runtime authorization.

Spellbook Studio is an **authoring and management interface**. Studio access and a book's local Expert enablement affect who can edit or intentionally run Expert content; Studio is not the authorization engine for Assist or MCP calls.

Runtime enforcement includes:

- **Bridge identity:** an MCP bearer token resolves server-side to exactly one Bridge. The client never supplies a trusted Bridge ID.
- **Action and Query `AreaPolicy`:** independently configured. `ActionExecutor` applies the relevant policy after resolving real Home Assistant targets.
- **Assist Exposure:** target entities must be exposed through Home Assistant's Assist model where the resolver requires it.
- **Expert tier:** Expert-only operations and template targets need the book's local Expert enablement.
- **`assist_redirect`:** forwarding to Home Assistant's conversation agent cannot carry a narrow per-call area scope. MCP therefore requires both action and query policies to be unrestricted before forwarding.

MCP projection is not a second authorization engine. JSON schema, descriptions and `readOnlyHint` describe a tool; they grant nothing. `tools/call` re-derives the projection from the current server-held Bridge configuration and runs the shared executor checks.

Each MCP Bridge receives a high-entropy bearer token, shown in full once. HASE persists only an HMAC-SHA256 verifier plus a short display suffix; the per-installation HMAC secret is stored in Home Assistant storage. Regeneration invalidates the old token.

The MCP listener binds all interfaces on its configured port and provides no TLS or OAuth. Treat the LAN and token as security boundaries; use a properly secured reverse proxy for any external exposure.

If a configured Bridge area is deleted, HASE fails closed, refuses execution and creates a Repairs issue instead of silently turning the Bridge into an area-less master Bridge.
