"""MCP Bridge credential verification - HASE MCP architecture, Phase 3.

Deterministic HMAC-SHA256 bearer-token verification for MCP Bridges - not
password hashing (Argon2/bcrypt/PBKDF2): a Bridge credential is a
high-entropy generated token (secrets.token_urlsafe(32)), never
user-chosen, so a slow, salted KDF buys nothing a deterministic keyed hash
doesn't already give. The security property actually needed here is "the
stored verifier can't be reversed or forged without the per-installation
secret", not "resist offline guessing of a low-entropy human password" -
this is the approved architecture's own distinction, not a shortcut.

Zero third-party or Home Assistant imports - stdlib only (secrets, hmac,
hashlib), so every function/class here is directly unit-testable and
independently auditable without any Home Assistant install. The
per-installation HMAC secret's persistence (an HA `Store`) and the actual
Bridge subentry storage are both genuinely Home Assistant concerns and
live in providers/mcp/bridge_context.py and providers/mcp/config_flow.py
instead - this module only ever receives the secret as plain bytes.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field

TOKEN_ENTROPY_BYTES = 32
SECRET_ENTROPY_BYTES = 32
DISPLAY_SUFFIX_LENGTH = 6


def generate_token() -> str:
    """A fresh, cryptographically secure bearer token for one Bridge.
    Shown to the user in full exactly once - at initial creation or
    explicit regeneration - and never persisted in this form; callers must
    not retain it beyond handing it back in that one response."""
    return secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)


def generate_secret() -> bytes:
    """A fresh per-installation HMAC secret. Generated once, ever, by the
    caller's persistence layer (providers/mcp/'s HA `Store` wiring) - never
    regenerated just because one Bridge's own token was regenerated, or
    every previously-issued verifier would stop matching at once."""
    return secrets.token_bytes(SECRET_ENTROPY_BYTES)


def compute_verifier(secret: bytes, token: str) -> str:
    """HMAC-SHA256(secret, token), hex-encoded - the ONLY form of a Bridge
    credential ever persisted. Deterministic: the same (secret, token)
    pair always yields the same verifier, which is what makes an O(1)
    verifier lookup possible without ever storing or comparing the
    plaintext token itself."""
    return hmac.new(secret, token.encode("utf-8"), hashlib.sha256).hexdigest()


def display_suffix(token: str) -> str:
    """The only fragment of a token ever shown again after its one-time
    reveal - too short to reconstruct the token (the full token has
    TOKEN_ENTROPY_BYTES=32 bytes of underlying entropy; this exposes a
    handful of trailing base64url characters), long enough to tell two
    credentials apart at a glance in a settings UI."""
    return token[-DISPLAY_SUFFIX_LENGTH:]


@dataclass(frozen=True)
class IssuedCredential:
    """The one-time result of generating (or regenerating) a Bridge
    credential. `token` is the full secret - the caller shows it to the
    user exactly once and MUST NOT persist it; only `verifier`/`suffix`
    are ever written to storage."""

    token: str
    verifier: str
    suffix: str


def issue_credential(secret: bytes) -> IssuedCredential:
    token = generate_token()
    return IssuedCredential(token=token, verifier=compute_verifier(secret, token), suffix=display_suffix(token))


def verify(secret: bytes, token: str, *, expected_verifier: str) -> bool:
    """Constant-time comparison against ONE already-known verifier - for a
    caller that already knows which Bridge it's checking against.
    The runtime supervisor uses this value as the O(1) key for the actual
    TokenVerifier question: "which current Bridge generation, if any,
    does this unknown token belong to"."""
    return hmac.compare_digest(compute_verifier(secret, token), expected_verifier)


@dataclass(frozen=True)
class BridgeIdentity:
    """Which Bridge a verifier resolves to - just enough to look up the
    rest of its stored configuration later. Never carries area/Spellbook/
    policy itself, so refreshing the index never risks it going stale
    relative to the Bridge's real, persisted configuration."""

    entry_id: str
    subentry_id: str


@dataclass
class CredentialIndex:
    """Legacy diagnostic view of verifier -> BridgeIdentity.

    Production authentication uses BridgeRuntimeSupervisor's immutable
    credential/generation publication. This view remains for existing
    diagnostics and is updated bridge-by-bridge, never cleared globally
    for one Bridge's credential rotation.
    """

    _by_verifier: dict[str, BridgeIdentity] = field(default_factory=dict)

    def put(self, verifier: str, identity: BridgeIdentity) -> None:
        self.remove_identity(identity)
        self._by_verifier[verifier] = identity

    def remove(self, verifier: str) -> None:
        self._by_verifier.pop(verifier, None)

    def remove_identity(self, identity: BridgeIdentity) -> None:
        """Remove only one Bridge's mapping, leaving every peer untouched."""
        self._by_verifier = {
            verifier: current
            for verifier, current in self._by_verifier.items()
            if current != identity
        }

    def clear(self) -> None:
        self._by_verifier.clear()

    def resolve(self, secret: bytes, token: str) -> BridgeIdentity | None:
        """Hash the incoming token the same way every stored verifier was
        computed, then a single dict lookup - the actual Phase 4
        TokenVerifier seam."""
        return self._by_verifier.get(compute_verifier(secret, token))

    def __len__(self) -> int:
        return len(self._by_verifier)
