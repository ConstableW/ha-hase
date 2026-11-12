# MCP Bridge runtime lifecycle

HASE runs MCP Streamable HTTP with `stateless_http=True`. There is no
persistent server-side client/session identity to supervise. Runtime
ownership is therefore modeled as a long-lived Bridge plus a monotonically
increasing generation and request-scoped leases.

## Runtime rules

- Authentication resolves a verifier through one immutable publication
  containing both the Bridge snapshot and its generation.
- A request captures that snapshot under the Bridge's own lock, executes
  without holding the lock, and rechecks the generation before publishing.
- Bridge configuration and credential updates replace only that Bridge's
  snapshot. They do not restart the Uvicorn listener.
- Work before its first Home Assistant or Assist dispatch can be cancelled.
  Once dispatch begins, the side effect is allowed to finish, but a stale
  response is discarded.
- Listener ownership is separate. Port/bind changes use
  `cancel -> await termination -> start`, serialized by a listener restart
  lock.

The retained `CredentialIndex` in `hass.data` is a compatibility/diagnostic
view only. Authentication uses `BridgeRuntimeSupervisor`'s immutable
copy-and-swap publication, so credentials and generations cannot be observed
in a mixed state.

## Open risks

- **R1 — client-only reconnect:** A hang caused solely by a client reconnect,
  without a server-side Bridge/config change, is a separate bug. The
  stateless server has no persistent client object that this supervisor can
  reclaim. Reproduce and root-cause it separately.
- **R2 — historical SSE abort:** The historical live SSE abort has not been
  root-caused. Awaited listener cancellation may help, but no causal link is
  claimed. Re-test it as a separate issue.
- **R3 — dependency range:** `manifest.json` currently allows `mcp>=2.2.0`.
  This design relies on the verified 2.2.0 stateless semantics. Pin the SDK or
  add a compatibility check if later SDK behavior changes.
