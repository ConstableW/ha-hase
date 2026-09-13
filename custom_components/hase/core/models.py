"""Contract types for the Spellbook Engine core - v1.

docs/spellbook-engine/stage-1/README.md settled the full shape of
these contracts. As of Etappe 6: SPELLBOOK_ONLY and SATELLITE modes,
ambiguity/clarification, a typed operation catalog including
vacuum.clean_area, and a ConversationBackend contract for SATELLITE's
no-match fallback. Code27ConversationBackend (providers/code27/backend.py)
implements that contract today with proposals always empty - CODE27's
API has no proven structured-action protocol, only free conversation.

No `homeassistant` import anywhere in this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


# ---------------------------------------------------------------------------
# Inbound request
# ---------------------------------------------------------------------------


class FrontendMode(str, Enum):
    SPELLBOOK_ONLY = "spellbook_only"
    SATELLITE = "satellite"


@dataclass(frozen=True)
class ConversationRequest:
    """The trusted inbound envelope a FrontendAdapter builds. Every
    field the adapter fills in is treated as trustworthy by the
    router/executor - a backend's output must never be allowed to
    overwrite any of these (see BackendResult; nothing in Etappe 2
    consumes a backend, but the boundary is already drawn here)."""

    request_id: str
    agent_entry_id: str
    text: str
    language: str
    source_namespace: str
    source_id: str | None = None
    conversation_token: str | None = None
    backend_instance_id: str | None = None
    profile_id: str | None = None
    # Where the request physically came from (the speaking device's/
    # voice satellite's own HA area) - not where the user explicitly
    # asked to act. Named source_area_id, not e.g. default_area,
    # because it names WHERE it came from, not just "a fallback" - the
    # same field is the natural home for later context like "hier"/
    # "dieser Raum" too. Optional, normalized by the adapter, and
    # grants no extra authorization by itself (stage-1/README.md was
    # explicit about that from the start) - it only ever substitutes
    # into an ordinary area_id target, which the SAME AreaPolicy check
    # as any explicit area then applies to, normally, every time.
    source_area_id: str | None = None
    # Opaque (this package has zero homeassistant imports) - the
    # adapter's own homeassistant.core.Context, forwarded verbatim
    # down to every service call this turn triggers so it's
    # attributed to whoever/whatever started the conversation, not to
    # nothing (Logbook/History/automation traces all key off this).
    context: object | None = None


# ---------------------------------------------------------------------------
# Engine result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionTarget:
    """A target as named by whoever proposed the action (spellbook
    capture, eventually a backend) - not yet resolved to anything.
    `kind` says what `value` means; `all_allowed`, `none` and
    `source_area` carry no value of their own. The executor is the
    only thing allowed to turn this into a concrete, policy-checked
    set of entities.

    `source_area` is distinct from `all_allowed`: a spellbook command
    opts into it explicitly (unlike area_name/entity_name, it needs no
    capture - there's nothing in the utterance to capture), and it
    means "whichever area this specific request came from, or refuse
    - never every area". A command written this way stays scoped to
    one area even without the user naming one; a command that's
    supposed to be whole-house-global keeps using all_allowed, exactly
    as before - this kind never turns one into the other."""

    kind: Literal["area_name", "area_id", "entity_name", "entity_id", "all_allowed", "none", "source_area"]
    value: str | None = None


@dataclass(frozen=True)
class ActionDraft:
    """What the engine proposes for a MATCH - not yet a validated
    ActionRequest (the executor still checks operation/target/argument
    shape against the catalog), just what the spellbook declared."""

    operation: str
    target: ActionTarget
    arguments: dict = field(default_factory=dict)
    catchphrase: str | None = None


@dataclass(frozen=True)
class EngineMatch:
    action_draft: ActionDraft
    spell_id: str
    matched_text: str
    is_exact: bool


@dataclass(frozen=True)
class EngineNoMatch:
    pass


@dataclass(frozen=True)
class AmbiguousOption:
    action_draft: ActionDraft
    spell_id: str
    matched_text: str
    # The configured trigger template with captures filled in (e.g.
    # "aktiviere licht Buero") - distinct per option, unlike
    # matched_text, which is the span of the user's own utterance both
    # candidates matched against and is therefore IDENTICAL for both.
    # Read out loud / shown in the clarifying question instead.
    spoken_trigger: str


@dataclass(frozen=True)
class EngineAmbiguous:
    """Two same-tier candidates both cleared the fuzzy threshold with a
    near-tied score AND propose opposite outcomes (same operation and
    target, a boolean argument flipped) - picking either one silently
    risks doing the exact opposite of what was meant. Etappe 3."""

    option_a: AmbiguousOption
    option_b: AmbiguousOption


EngineResult = EngineMatch | EngineAmbiguous | EngineNoMatch


# ---------------------------------------------------------------------------
# Action / execution
# ---------------------------------------------------------------------------


class ExecutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    QUERY_ANSWER = "QUERY_ANSWER"
    DENIED = "DENIED"
    INVALID = "INVALID"
    UNSUPPORTED = "UNSUPPORTED"
    FAILED = "FAILED"
    # A proposal after the first failed one in a multi-proposal backend
    # reply (Etappe 6) - never attempted, not the same as FAILED.
    NOT_EXECUTED = "NOT_EXECUTED"


@dataclass(frozen=True)
class ExecutionResult:
    status: ExecutionStatus
    reason_code: str
    data: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AreaPolicy:
    """Etappe 1's rule, implemented literally: a missing allowed_areas
    is an invalid configuration (never "treat as unrestricted"), an
    empty frozenset means no areas at all, and unrestricted access
    only exists via the explicit `unrestricted=True` flag."""

    allowed_areas: frozenset[str] | None
    unrestricted: bool = False

    def __post_init__(self) -> None:
        if not self.unrestricted and self.allowed_areas is None:
            raise ValueError(
                "AreaPolicy requires an explicit allowed_areas set (possibly "
                "empty) unless unrestricted=True is set explicitly"
            )

    def permits_area(self, area_id: str) -> bool:
        if self.unrestricted:
            return True
        assert self.allowed_areas is not None
        return area_id in self.allowed_areas


@dataclass(frozen=True)
class ScopedAreaPolicy:
    """Actions and queries each get their own independently-configured
    AreaPolicy - stage-1/README.md's Policy-und-Entry-Lifecycle
    section is explicit that this is "je Action-/Query-Policy", not
    one shared policy for both. An agent might reasonably answer
    status queries (entity.get_state) anywhere while still being
    restricted on which areas it may act in (light.set_power,
    vacuum.clean_area, ...) - the executor picks whichever of the two
    applies per operation, based on the operation catalog's own
    action/query classification (see executor.py's execute())."""

    actions: AreaPolicy
    queries: AreaPolicy


# ---------------------------------------------------------------------------
# Outbound conversation result
# ---------------------------------------------------------------------------


class ConversationStatus(str, Enum):
    COMPLETED = "completed"
    # Nothing in the spellbook matched, and (SATELLITE only) no backend
    # was consulted or the backend had nothing either - distinct from
    # COMPLETED so a frontend adapter (see conversation.py) can map it
    # onto HA's own NO_INTENT_MATCH rather than reporting a "handled"
    # turn that did nothing.
    NO_MATCH = "no_match"
    REJECTED = "rejected"
    CONFIRMATION_REQUIRED = "confirmation_required"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Conversation backend (Etappe 6, SATELLITE mode)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackendResult:
    """What a ConversationBackend hands back for text the local
    spellbook didn't match. `proposals` is deliberately usually empty
    in practice today - CODE27's own API has no proven structured
    action protocol (docs/spellbook-engine/stage-1/backend-readiness.md),
    so Code27ConversationBackend only ever returns free conversation
    text, never proposals. The field exists so the contract doesn't
    have to change again once a backend that *can* propose actions
    exists."""

    status: Literal["reply", "unavailable", "error"]
    response_text: str | None
    proposals: tuple[ActionDraft, ...] = ()


@dataclass(frozen=True)
class ConversationResult:
    request_id: str
    response_text: str
    status: ConversationStatus
    execution_results: tuple[ExecutionResult, ...] = ()
    # The EFFECTIVE token this turn's session was keyed by - normally
    # just request.conversation_token echoed back, but when the caller
    # gave none at all, session.py generates a random one so two
    # token-less callers never collide into the same session. That
    # generated value used to be invisible outside the store itself -
    # a caller who genuinely wanted to continue this exchange had no
    # way to learn what to send next time. Always populated by the
    # router (None only for the handful of pre-session config-error
    # rejections, e.g. a misconfigured SATELLITE entry).
    conversation_token: str | None = None
