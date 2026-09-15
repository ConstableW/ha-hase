"""SpellbookRouter - the orchestrator. Calls the engine, then (on a
match) the executor, and turns whatever comes back into a
ConversationResult with deterministic, never-crash-the-turn text.
Owns the InteractionStateStore lookup itself - the store is not an
independent stage something else calls before handing off to the
router, it's a dependency the router consults (stage-1/README.md's
audit finding #1).

SPELLBOOK_ONLY: no backend, ever - a no-match is final. SATELLITE:
exactly one backend, consulted only when the local engine has nothing
- a local match always wins, so "Lumos Schlafzimmer" never pays a
backend round-trip. Any proposal a backend returns is executed through
the exact same ActionExecutor + AreaPolicy as a local match - a
backend never gets a shortcut around policy (Etappe 6's core
guarantee). A misconfigured SATELLITE entry (mode set but no backend
wired) fails loudly rather than silently falling back to
SPELLBOOK_ONLY behavior.
"""
from __future__ import annotations

import asyncio
import dataclasses
import difflib
import logging
import time
from dataclasses import dataclass
from typing import Protocol

from .executor import ActionExecutor
from .interaction_state import (
    DialogCapacityError,
    InteractionStateStore,
    PendingClarification,
    SessionLockRegistry,
)
from .models import (
    AmbiguousOption,
    BackendResult,
    ConversationRequest,
    ConversationResult,
    ConversationStatus,
    EngineAmbiguous,
    EngineMatch,
    EngineNoMatch,
    ExecutionResult,
    ExecutionStatus,
    FrontendMode,
    ScopedAreaPolicy,
)
from .session import ConversationScope, InteractionSession, backend_scope, compute_interaction_session, local_scope
from .spellbook_engine import EngineSpellbook, match as engine_match

_LOGGER = logging.getLogger(__name__)

DEFAULT_NO_MATCH_RESPONSE = "Das kann ich mit diesem Spellbook nicht."

_REJECTED_TEXT = "Das ist mir hier nicht erlaubt."
_ERROR_TEXT = "Das konnte ich nicht ausführen."
_CANCELLED_TEXT = "Abgebrochen."
_TOO_MANY_DIALOGS_TEXT = "Gerade zu viele offene Rückfragen. Bitte gleich nochmal."
_BACKEND_UNAVAILABLE_TEXT = "Ich kann das gerade nicht erreichen."
_MISCONFIGURED_SATELLITE_TEXT = "Kein Backend für diesen Assistenten eingerichtet."

_NEGATIVE_WORDS = {"nein", "nö", "abbrechen", "abbruch", "stopp", "stop", "no", "cancel"}
_PUNCT = " ,.:;!?–—"

# How close two options' distinguishing-word scores must be to re-ask
# instead of picking one.
_PICK_MARGIN = 0.05
# A reply must resemble at least one option's own DISTINGUISHING words
# (see _distinguishing_words) this much, in absolute terms, not just
# beat the other option by _PICK_MARGIN.
#
# This whole mechanism used to score a reply against each option's
# FULL spoken_trigger/catchphrase text (whole-phrase SequenceMatcher
# ratio), raised from an original 0.35 floor to 0.55 after "dein name"
# scored 0.38 against "Deaktiviert." - real, but not the actual
# disease: comparing against the FULL phrase (most of which is filler
# words BOTH options share, e.g. "licht Buero") means any reply
# sharing a stem with the one differing word still scores deceptively
# high through pure accumulated character overlap on the shared part.
# Found live: "deine Aktivität" scored 0.667 against "deaktiviere
# licht Buero" (shared "akti"/"vi" stem plus general letter overlap)
# - comfortably above the 0.55 floor - and would have turned a light
# off in response to a sentence that never said anything resembling a
# deliberate pick. Raising the floor further can't fix this: the
# floor would also have to clear "aktiviere"/"deaktiviere" themselves
# (0.84/0.87 against the full phrase), and "deine Aktivität" sits
# between the two, not below both.
#
# The actual fix: score the reply ONLY against each option's
# DISTINGUISHING words - the ones that differ between the two options
# (here "aktiviere"/"aktiviert" vs "deaktiviere"/"deaktiviert", drawn
# from both the trigger AND the catchphrase, since a reply may echo
# either) - never the shared filler, which carries zero information
# about which one was picked.
#
# 0.75 was NOT far enough: "deine Aktivität"'s best per-word ratio
# against the "on" side is 0.778 - not against "aktiviere" (0.667,
# clears nothing) but against "aktiviert" (the catchphrase's past
# participle), which happens to share more character positions with
# "Aktivität" than the infinitive does. 0.85 clears that specific
# collision while every genuine pick still lands at >=0.9:
# "aktiviere"=1.0/0.9, "deaktiviere"=1.0/0.9, "daktiviere"=0.947/0.952
# (an STT-plausible single-letter drop - correctly still too close to
# call, rejected by the margin below, not the floor), "nein
# deaktivier das"=0.952, "ja aktiviere bitte"=1.0. "dein name" and
# "was ist das wetter morgen" stay far below (0.31-0.47).
_DISTINGUISHING_MATCH_THRESHOLD = 0.85

# Mirrors stage-1/README.md's v1 cap - a runaway backend proposing an
# unbounded action list is a bug, not something to execute all of.
_MAX_PROPOSALS = 8

# request_id dedup window - a retried delivery of the exact same
# request_id (a transport-level retry, not a repeated utterance, which
# always mints a new id) within this window replays the cached result
# instead of executing the underlying action a second time.
_REQUEST_ID_TTL_SECONDS = 60.0
_MAX_TRACKED_REQUEST_IDS = 512


class ConversationBackend(Protocol):
    async def converse(self, text: str, language: str, scope: ConversationScope) -> BackendResult: ...


@dataclass(frozen=True)
class AgentConfig:
    """Everything about one spellbooker entry the router needs to
    handle a turn. For SPELLBOOK_ONLY, `backend` is unused (should be
    None). For SATELLITE, `backend` must be set - the router treats a
    SATELLITE config without one as a configuration error, not as an
    implicit SPELLBOOK_ONLY."""

    mode: FrontendMode
    spellbook: EngineSpellbook
    policy: ScopedAreaPolicy
    no_match_response: str = DEFAULT_NO_MATCH_RESPONSE
    backend: ConversationBackend | None = None
    # Identify the backend *instance* and *profile* this agent is
    # bound to - e.g. which CODE27 device and which character slot.
    # Two agents (even on different devices/frontends) sharing both
    # values share the same conversation_scope, and so the same
    # ongoing conversation - see core/session.py. Unused for
    # SPELLBOOK_ONLY.
    backend_instance_id: str | None = None
    profile_id: str | None = None


def _words(text: str) -> set[str]:
    return {w.strip(_PUNCT).lower() for w in text.split() if w.strip(_PUNCT)}


def _option_vocabulary(option: AmbiguousOption) -> set[str]:
    words = _words(option.spoken_trigger)
    if option.action_draft.catchphrase:
        words |= _words(option.action_draft.catchphrase)
    return words


def _distinguishing_words(mine: AmbiguousOption, other: AmbiguousOption) -> set[str]:
    """Words in `mine`'s own trigger/catchphrase vocabulary that
    `other` does NOT share - the only words that carry any signal
    about which of the two a short reply is picking. See
    _DISTINGUISHING_MATCH_THRESHOLD's own docstring for the concrete
    bug this exists to fix."""
    return _option_vocabulary(mine) - _option_vocabulary(other)


def _best_word_ratio(reply_words: set[str], distinguishing: set[str]) -> float:
    if not reply_words or not distinguishing:
        return 0.0
    return max(
        difflib.SequenceMatcher(None, w, dw).ratio() for w in reply_words for dw in distinguishing
    )


class SpellbookRouter:
    def __init__(
        self,
        executor: ActionExecutor,
        store: InteractionStateStore | None = None,
        *,
        clock=time.monotonic,
    ) -> None:
        self._executor = executor
        self._store = store or InteractionStateStore()
        self._session_locks = SessionLockRegistry()
        self._clock = clock
        # Each entry is either a finished (seen_at, ConversationResult)
        # or an in-flight asyncio.Future for a request_id currently
        # being handled. A plain check-then-act (look up, and only
        # write the result once handling finishes) has a real race: two
        # concurrent calls for the SAME request_id both see a miss
        # before either one's result is ever stored, and both execute
        # - confirmed live with a caller that actually awaits (any real
        # HA service call does). Reserving a Future SYNCHRONOUSLY
        # before any await closes that window - the second caller
        # awaits the first's Future instead of running its own turn.
        self._recent_requests: dict[str, tuple[float, ConversationResult] | asyncio.Future] = {}

    def _expire_stale_requests(self) -> None:
        now = self._clock()
        stale = [
            rid for rid, entry in self._recent_requests.items()
            if not isinstance(entry, asyncio.Future) and now - entry[0] > _REQUEST_ID_TTL_SECONDS
        ]
        for rid in stale:
            del self._recent_requests[rid]

    async def handle(self, request: ConversationRequest, config: AgentConfig) -> ConversationResult:
        self._expire_stale_requests()
        existing = self._recent_requests.get(request.request_id)
        if isinstance(existing, asyncio.Future):
            # asyncio.shield, not a bare await: cancelling THIS caller
            # (e.g. its own timeout, or the wider request being
            # cancelled upstream) must never cancel the SHARED future -
            # a bare `await existing` does exactly that, since asyncio
            # cancels whatever a cancelled task is currently awaiting.
            # The original caller (the one actually running
            # _handle_uncached and who will call future.set_result()
            # below) would then crash with InvalidStateError on an
            # already-cancelled future - after the underlying action
            # already ran. Found live (external audit) with a
            # reproduction needing no physical concurrency, just one
            # cancelled duplicate.
            return await asyncio.shield(existing)
        if existing is not None:
            return existing[1]

        future: asyncio.Future[ConversationResult] = asyncio.get_running_loop().create_future()
        if len(self._recent_requests) >= _MAX_TRACKED_REQUEST_IDS:
            oldest_id = min(
                (rid for rid, e in self._recent_requests.items() if not isinstance(e, asyncio.Future)),
                key=lambda rid: self._recent_requests[rid][0],
                default=None,
            )
            if oldest_id is not None:
                del self._recent_requests[oldest_id]
        self._recent_requests[request.request_id] = future
        try:
            result = await self._handle_uncached(request, config)
        except BaseException as err:
            future.set_exception(err)
            del self._recent_requests[request.request_id]
            raise
        self._recent_requests[request.request_id] = (self._clock(), result)
        future.set_result(result)
        return result

    async def _handle_uncached(self, request: ConversationRequest, config: AgentConfig) -> ConversationResult:
        if config.mode is FrontendMode.SATELLITE and config.backend is None:
            return ConversationResult(
                request_id=request.request_id,
                response_text=_MISCONFIGURED_SATELLITE_TEXT,
                status=ConversationStatus.ERROR,
            )
        if config.mode not in (FrontendMode.SPELLBOOK_ONLY, FrontendMode.SATELLITE):
            return ConversationResult(
                request_id=request.request_id, response_text=_ERROR_TEXT, status=ConversationStatus.ERROR
            )

        scope = (
            local_scope(request.agent_entry_id)
            if config.mode is FrontendMode.SPELLBOOK_ONLY
            else backend_scope(config.backend_instance_id, config.profile_id)
        )
        session = compute_interaction_session(
            agent_entry_id=request.agent_entry_id,
            scope=scope,
            source_namespace=request.source_namespace,
            conversation_token=request.conversation_token,
        )

        # Everything from here on reads and writes this session's
        # InteractionStateStore entry and may execute an action - two
        # turns for the same session (two frontends sharing one
        # conversation, a retried delivery) must never interleave
        # through this, or one could consume/execute the other's
        # pending clarification twice, or an ambiguity answer could
        # race a fresh command.
        async with self._session_locks.acquire(session):
            result = await self._handle_locked(request, config, scope, session)
        # Always echo back the EFFECTIVE token this turn's session
        # was keyed by - normally just request.conversation_token,
        # but session.py may have generated one when the caller gave
        # none at all; that value used to be invisible outside the
        # store, leaving a token-less caller with no way to learn
        # what to send next time to continue this exchange.
        return dataclasses.replace(result, conversation_token=session.conversation_token)

    async def _handle_locked(self, request, config, scope, session) -> ConversationResult:
        pending = self._store.get(session)

        # New-command detection runs BEFORE reply-consumption: a reply
        # to an open clarification is usually short ("die erste",
        # "ja") and won't match the engine at all, but if it DOES
        # match a distinct command, that's a strong, unambiguous
        # signal the user moved on - stronger than any text-similarity
        # score against the two pending options. Only fall back to
        # treating the text as a reply when the engine found nothing.
        result = engine_match(config.spellbook, request.text, request.language)

        if isinstance(result, EngineNoMatch):
            if pending is not None:
                reply_result = await self._consume_reply(request, session, pending, config)
                if reply_result is not None:
                    return reply_result
                # Neither a reply nor a new command - ask it again, but
                # do NOT renew the idle TTL: unrecognized noise isn't
                # engagement, and letting it keep a stale dialog alive
                # indefinitely defeats the TTL's own purpose.
                return ConversationResult(
                    request_id=request.request_id,
                    response_text=self._ask_text(pending),
                    status=ConversationStatus.CONFIRMATION_REQUIRED,
                )
            if config.mode is FrontendMode.SATELLITE:
                return await self._consult_backend(request, config, scope)
            return ConversationResult(
                request_id=request.request_id,
                response_text=config.no_match_response,
                status=ConversationStatus.NO_MATCH,
            )

        if pending is not None:
            # A clear new command discards the old, unanswered question
            # rather than leaving it to confuse a later reply.
            self._store.discard(session)

        if isinstance(result, EngineAmbiguous):
            try:
                self._store.begin(
                    session, result.option_a, result.option_b,
                    language=request.language, reply_route_ref=request.source_id or "",
                )
            except DialogCapacityError:
                return ConversationResult(
                    request_id=request.request_id,
                    response_text=_TOO_MANY_DIALOGS_TEXT,
                    status=ConversationStatus.ERROR,
                )
            return ConversationResult(
                request_id=request.request_id,
                response_text=self._ask_text_for(result.option_a, result.option_b),
                status=ConversationStatus.CONFIRMATION_REQUIRED,
            )

        assert isinstance(result, EngineMatch)
        if result.query_plan is not None:
            return await self._execute_query_plan_and_respond(request, result.query_plan, config.policy)
        return await self._execute_and_respond(request, result.action_draft, config.policy)

    async def _execute_and_respond(self, request, action_draft, policy) -> ConversationResult:
        execution = await self._executor.execute(
            action_draft, policy, context=request.context, source_area_id=request.source_area_id
        )

        if execution.status is ExecutionStatus.QUERY_ANSWER:
            # A catchphrase is what to SAY after doing something - a
            # query has nothing to "do", so speaking it here would
            # silently swallow the actual answer the user asked for.
            text = _response_text_for_success(execution)
            status = ConversationStatus.COMPLETED
        elif execution.status is ExecutionStatus.SUCCESS:
            text = action_draft.catchphrase or _response_text_for_success(execution)
            status = ConversationStatus.COMPLETED
        elif execution.status is ExecutionStatus.DENIED:
            text = _REJECTED_TEXT
            status = ConversationStatus.REJECTED
        else:
            text = _ERROR_TEXT
            status = ConversationStatus.ERROR

        return ConversationResult(
            request_id=request.request_id, response_text=text, status=status,
            execution_results=(execution,), output_mode=action_draft.output_mode,
        )

    async def _execute_query_plan_and_respond(self, request, query_plan, policy) -> ConversationResult:
        """Multi-source-query sibling of _execute_and_respond (item 4/5) -
        same status mapping, minus SUCCESS (a query never has a bare
        "did something" outcome, only QUERY_ANSWER/DENIED/error, same as
        the single-entity entity.get_state path above)."""
        execution = await self._executor.execute_query_plan(
            query_plan, policy, context=request.context, source_area_id=request.source_area_id
        )

        if execution.status is ExecutionStatus.QUERY_ANSWER:
            text = _response_text_for_success(execution)
            status = ConversationStatus.COMPLETED
        elif execution.status is ExecutionStatus.DENIED:
            text = _REJECTED_TEXT
            status = ConversationStatus.REJECTED
        else:
            text = _ERROR_TEXT
            status = ConversationStatus.ERROR

        return ConversationResult(
            request_id=request.request_id, response_text=text, status=status,
            execution_results=(execution,), output_mode=query_plan.output_mode,
        )

    async def _consult_backend(
        self, request: ConversationRequest, config: AgentConfig, scope: ConversationScope
    ) -> ConversationResult:
        assert config.backend is not None
        try:
            backend_result = await config.backend.converse(request.text, request.language, scope)
        except Exception:  # noqa: BLE001 - a backend crashing must not crash the turn
            backend_result = BackendResult(status="unavailable", response_text=None)

        if backend_result.status != "reply":
            return ConversationResult(
                request_id=request.request_id,
                response_text=_BACKEND_UNAVAILABLE_TEXT,
                status=ConversationStatus.BACKEND_UNAVAILABLE,
            )

        proposals = backend_result.proposals
        if len(proposals) > _MAX_PROPOSALS:
            # A silent truncation would run the first 8 of a runaway
            # proposal list without the backend (or whoever's reading
            # logs) ever finding out the rest were dropped - reject the
            # whole batch instead, loudly.
            _LOGGER.warning(
                "spellbooker backend: %d proposals exceeds the cap of %d, rejecting the whole batch",
                len(proposals), _MAX_PROPOSALS,
            )
            return ConversationResult(
                request_id=request.request_id, response_text=_ERROR_TEXT, status=ConversationStatus.ERROR
            )

        # Static shape validation for the WHOLE batch before executing
        # ANY of it - a malformed proposal #3 must not be discovered
        # only after #1 and #2 already had real effects.
        if not all(self._executor.validate_shape(draft) for draft in proposals):
            return ConversationResult(
                request_id=request.request_id, response_text=_ERROR_TEXT, status=ConversationStatus.ERROR
            )

        executions: list[ExecutionResult] = []
        for draft in proposals:
            # Every proposal goes through the SAME executor + policy as
            # a local match - the backend never gets a shortcut. Serial,
            # stop at the first non-success; the rest are recorded as
            # never attempted, not silently skipped.
            execution = await self._executor.execute(
                draft, config.policy, context=request.context, source_area_id=request.source_area_id
            )
            executions.append(execution)
            if execution.status not in (ExecutionStatus.SUCCESS, ExecutionStatus.QUERY_ANSWER):
                break
        if len(executions) < len(proposals):
            executions.extend(
                ExecutionResult(ExecutionStatus.NOT_EXECUTED, "preceding_proposal_failed")
                for _ in range(len(proposals) - len(executions))
            )

        failed = [e for e in executions if e.status not in (ExecutionStatus.SUCCESS, ExecutionStatus.QUERY_ANSWER, ExecutionStatus.NOT_EXECUTED)]
        # v1 keeps final wording simple: the backend's own text if it
        # gave one AND nothing failed, otherwise a generic confirmation
        # or error - the backend's optimistic reply text (e.g. "Klar,
        # mach ich!") must never be spoken over a DENIED/FAILED
        # execution, or the user hears success for something that
        # didn't happen. No round-trip back to the backend to re-phrase
        # after execution (stage-1's "text-only Abschlussantwort"
        # refinement). No backend today actually returns proposals to
        # exercise this path with.
        any_denied = any(e.status is ExecutionStatus.DENIED for e in failed)
        if failed:
            text = _REJECTED_TEXT if any_denied else _ERROR_TEXT
        elif len(executions) == 1 and executions[0].status is ExecutionStatus.QUERY_ANSWER:
            # The backend's own response_text can't know the real
            # current value a query resolves to - it was written
            # before the proposal ever ran. Speak the actual answer,
            # same helper the local-match path already uses for this,
            # never generic backend chatter or "Erledigt." over a
            # question that was just answered with real data.
            text = _response_text_for_success(executions[0])
        else:
            text = backend_result.response_text or "Erledigt."
        status = ConversationStatus.REJECTED if any_denied else (
            ConversationStatus.ERROR if failed else ConversationStatus.COMPLETED
        )
        return ConversationResult(
            request_id=request.request_id, response_text=text, status=status, execution_results=tuple(executions)
        )

    async def _consume_reply(
        self,
        request: ConversationRequest,
        session: InteractionSession,
        pending: PendingClarification,
        config: AgentConfig,
    ) -> ConversationResult | None:
        """None means "not a recognizable reply" - the caller then
        tries the text as a fresh command instead of re-asking blind."""
        stripped = request.text.strip(" ,.:;!?–—")
        if stripped.lower() in _NEGATIVE_WORDS:
            self._store.discard(session)
            return ConversationResult(
                request_id=request.request_id, response_text=_CANCELLED_TEXT, status=ConversationStatus.COMPLETED
            )

        reply_words = _words(request.text)
        dist_a = _distinguishing_words(pending.option_a, pending.option_b)
        dist_b = _distinguishing_words(pending.option_b, pending.option_a)
        score_a = _best_word_ratio(reply_words, dist_a)
        score_b = _best_word_ratio(reply_words, dist_b)
        if max(score_a, score_b) < _DISTINGUISHING_MATCH_THRESHOLD:
            return None
        if abs(score_a - score_b) < _PICK_MARGIN:
            return None

        self._store.discard(session)
        chosen = pending.option_a if score_a > score_b else pending.option_b
        # Policy/target are re-checked from scratch here (not cached
        # from when the ambiguity was first raised) - a config change
        # or a race with a direct action in between must not let a
        # stale decision through.
        return await self._execute_and_respond(request, chosen.action_draft, config.policy)

    def _ask_text(self, pending: PendingClarification) -> str:
        return self._ask_text_for(pending.option_a, pending.option_b)

    @staticmethod
    def _ask_text_for(option_a: AmbiguousOption, option_b: AmbiguousOption) -> str:
        return f'Meinst du "{option_a.spoken_trigger}" oder "{option_b.spoken_trigger}"?'


def _response_text_for_success(execution: ExecutionResult) -> str:
    if execution.status is ExecutionStatus.QUERY_ANSWER:
        if isinstance(execution.data.get("response_text"), str):
            return execution.data["response_text"]
        state = execution.data.get("state")
        unit = execution.data.get("unit")
        if state is None:
            return "Kein Zustand verfügbar."
        return f"{state}{unit}" if unit else str(state)
    return "Erledigt."
