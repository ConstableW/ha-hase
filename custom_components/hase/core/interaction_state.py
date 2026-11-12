"""InteractionStateStore: at most one open clarification per session,
holding a structured candidate pair (never executable text), with an
idle TTL that only a consumed reply renews - a passive lookup must not
keep a stale dialog alive forever. In-memory only, no persistence
across restarts (stage-1/README.md is explicit: a restart must not
resurrect a half-answered dialog someone might not even remember
starting).
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from .models import AmbiguousOption
from .session import InteractionSession

IDLE_TTL_SECONDS = 120.0
MAX_OPEN_DIALOGS = 256


class SessionLockRegistry:
    """One asyncio.Lock per InteractionSession, refcounted so the
    registry doesn't grow forever over a long-running entity's
    lifetime (a fresh conversation_id per dialogue means unbounded
    distinct sessions over uptime). An entry is only ever removed
    after the last currently-interested caller has released it -
    never while a lock might still be held or waited on - so a caller
    can never be handed a lock object that's about to be silently
    replaced by an unrelated one for the same session.

    This is what makes two turns for the SAME session (two frontends
    sharing one conversation, a retried delivery, ...) run one at a
    time instead of racing on InteractionStateStore reads/writes or
    executing the same clarification's answer twice."""

    def __init__(self) -> None:
        self._locks: dict[InteractionSession, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def acquire(self, session: InteractionSession) -> AsyncIterator[None]:
        lock, refcount = self._locks.get(session, (None, 0))
        if lock is None:
            lock = asyncio.Lock()
        self._locks[session] = (lock, refcount + 1)
        try:
            async with lock:
                yield
        finally:
            lock, refcount = self._locks[session]
            if refcount <= 1:
                del self._locks[session]
            else:
                self._locks[session] = (lock, refcount - 1)


class DialogCapacityError(Exception):
    """Raised by begin() when the store is full and this session isn't
    already one of the open dialogs. Never evicts someone else's
    pending clarification to make room - a full store rejects the new
    one instead."""


@dataclass
class PendingClarification:
    option_a: AmbiguousOption
    option_b: AmbiguousOption
    language: str
    reply_route_ref: str
    created_at: float
    last_activity: float


class InteractionStateStore:
    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._dialogs: dict[InteractionSession, PendingClarification] = {}

    def _expire_stale(self, now: float) -> None:
        stale = [
            session for session, dialog in self._dialogs.items()
            if now - dialog.last_activity > IDLE_TTL_SECONDS
        ]
        for session in stale:
            del self._dialogs[session]

    def begin(
        self,
        session: InteractionSession,
        option_a: AmbiguousOption,
        option_b: AmbiguousOption,
        *,
        language: str,
        reply_route_ref: str,
    ) -> None:
        now = self._clock()
        self._expire_stale(now)
        if session not in self._dialogs and len(self._dialogs) >= MAX_OPEN_DIALOGS:
            raise DialogCapacityError()
        self._dialogs[session] = PendingClarification(
            option_a=option_a,
            option_b=option_b,
            language=language,
            reply_route_ref=reply_route_ref,
            created_at=now,
            last_activity=now,
        )

    def get(self, session: InteractionSession) -> PendingClarification | None:
        self._expire_stale(self._clock())
        return self._dialogs.get(session)

    def touch(self, session: InteractionSession) -> None:
        dialog = self._dialogs.get(session)
        if dialog is not None:
            dialog.last_activity = self._clock()

    def discard(self, session: InteractionSession) -> None:
        self._dialogs.pop(session, None)
