"""Session identity: structured, hashable tuples - never ad-hoc string
concatenation (stage-1/README.md was explicit about this after the
audit flagged real collision risk in an earlier draft: two backend
instances both profiled "lilya" must not collide, and source_id must
never leak into the identity that decides whether two turns share a
dialog).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class ConversationScope:
    """Who is "speaking" - stable across every device/frontend that
    reaches the same backend+profile. backend_kind "local" is what a
    SPELLBOOK_ONLY agent (no backend at all) always gets, keyed by its
    own entry so two different spellbooker entries never share a
    scope."""

    backend_kind: str
    backend_instance_id: str
    profile_id: str


@dataclass(frozen=True)
class InteractionSession:
    """Which laptop-open-right-now dialog this is. Two frontends can
    share a ConversationScope (same persona) while each keeping its
    own InteractionSession (own conversation_token) - a clarification
    started on one never leaks into the other unless something
    explicitly threads the same conversation_token through."""

    agent_entry_id: str
    scope: ConversationScope
    source_namespace: str
    conversation_token: str


def local_scope(agent_entry_id: str) -> ConversationScope:
    return ConversationScope(backend_kind="local", backend_instance_id=agent_entry_id, profile_id="default")


def backend_scope(backend_instance_id: str, profile_id: str | None) -> ConversationScope:
    return ConversationScope(
        backend_kind="backend", backend_instance_id=backend_instance_id, profile_id=profile_id or "default"
    )


def compute_interaction_session(
    *, agent_entry_id: str, scope: ConversationScope, source_namespace: str, conversation_token: str | None
) -> InteractionSession:
    # No conversation_token means the caller gave us no way to
    # recognize a follow-up turn as belonging to this same exchange -
    # a single shared fallback string (e.g. keyed only by
    # source_namespace) would silently pool every such caller from
    # that namespace into ONE InteractionSession, so one person's open
    # clarification could be answered (or discarded) by a completely
    # unrelated turn from someone else on the same namespace. A fresh
    # random token per call guarantees no such collision - it costs
    # this one turn any chance of a follow-up reaching its own
    # clarification later, but that was never actually possible
    # without a real token anyway; an orphaned entry still expires via
    # InteractionStateStore's own IDLE_TTL.
    return InteractionSession(
        agent_entry_id=agent_entry_id,
        scope=scope,
        source_namespace=source_namespace,
        conversation_token=conversation_token or f"__no_token__:{uuid.uuid4()}",
    )
