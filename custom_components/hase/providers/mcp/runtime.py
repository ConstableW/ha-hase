"""Stateless, generation-guarded runtime ownership for MCP Bridges."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from .credentials import BridgeIdentity

_LOGGER = logging.getLogger(__name__)


def _freeze(value: Any) -> Any:
    """Recursively detach persisted config from mutable HA containers."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


class BridgeRuntimeUnavailable(RuntimeError):
    """The Bridge does not currently authorize this request generation."""


class BridgeRuntimeState(str, Enum):
    REGISTERED = "registered"
    INVALIDATED = "invalidated"
    UNREGISTERED = "unregistered"


class UpdateReason(str, Enum):
    CONFIG_CHANGED = "config_changed"
    CREDENTIALS_CHANGED = "credentials_changed"
    REGISTERED = "registered"
    REMOVED = "removed"


class RequestPhase(str, Enum):
    PRE_SIDE_EFFECT = "pre_side_effect"
    POST_SIDE_EFFECT = "post_side_effect"


@dataclass(frozen=True)
class BridgeConfig:
    title: str
    data: Mapping[str, Any]

    @classmethod
    def from_values(cls, title: str, data: Mapping[str, Any]) -> BridgeConfig:
        return cls(title=title, data=_freeze(data))


@dataclass(frozen=True)
class BridgeSnapshot:
    bridge_id: BridgeIdentity
    generation: int
    config: BridgeConfig
    context: Any
    credential_verifier: str


@dataclass
class InFlightRequest:
    request_id: str
    generation: int
    task: asyncio.Task[Any]
    phase: RequestPhase = RequestPhase.PRE_SIDE_EFFECT
    cancel_requested: bool = False

    def commit_side_effect(self) -> None:
        self.phase = RequestPhase.POST_SIDE_EFFECT

    def cancel(self) -> None:
        """Idempotently cancel only while reality has not been changed."""
        if self.cancel_requested:
            return
        self.cancel_requested = True
        if self.phase is RequestPhase.PRE_SIDE_EFFECT and not self.task.done():
            self.task.cancel()


@dataclass
class BridgeRuntime:
    bridge_id: BridgeIdentity
    generation: int
    snapshot: BridgeSnapshot
    state: BridgeRuntimeState = BridgeRuntimeState.REGISTERED
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    in_flight: dict[str, InFlightRequest] = field(default_factory=dict)


@dataclass(frozen=True)
class CredentialBinding:
    bridge_id: BridgeIdentity
    generation: int


@dataclass(frozen=True)
class RuntimePublication:
    bridges: Mapping[BridgeIdentity, BridgeSnapshot]
    credentials: Mapping[str, CredentialBinding]


@dataclass(frozen=True)
class RequestLease:
    snapshot: BridgeSnapshot
    request: InFlightRequest


class BridgeRuntimeSupervisor:
    """Owns Bridge generations without inventing a persistent client session."""

    def __init__(self) -> None:
        empty: Mapping[Any, Any] = MappingProxyType({})
        self._publication = RuntimePublication(empty, empty)
        self._runtimes: dict[BridgeIdentity, BridgeRuntime] = {}

    def bridge_ids(self) -> tuple[BridgeIdentity, ...]:
        return tuple(self._runtimes)

    def get_snapshot(self, bridge_id: BridgeIdentity) -> BridgeSnapshot | None:
        return self._publication.bridges.get(bridge_id)

    def get_generation(self, bridge_id: BridgeIdentity) -> int | None:
        snapshot = self.get_snapshot(bridge_id)
        return snapshot.generation if snapshot is not None else None

    def resolve_credential(self, verifier: str) -> tuple[CredentialBinding, BridgeSnapshot] | None:
        """Read credential and generation from one immutable publication."""
        publication = self._publication
        binding = publication.credentials.get(verifier)
        if binding is None:
            return None
        snapshot = publication.bridges.get(binding.bridge_id)
        if snapshot is None or snapshot.generation != binding.generation:
            return None
        return binding, snapshot

    def register_bridge(
        self,
        bridge_id: BridgeIdentity,
        config: BridgeConfig,
        context: Any,
        credential_verifier: str,
    ) -> None:
        if bridge_id in self._runtimes:
            raise ValueError(f"Bridge already registered: {bridge_id}")
        snapshot = BridgeSnapshot(bridge_id, 1, config, context, credential_verifier)
        self._runtimes[bridge_id] = BridgeRuntime(bridge_id, 1, snapshot)
        self._publish(snapshot)

    async def update_bridge(
        self,
        bridge_id: BridgeIdentity,
        config: BridgeConfig,
        context: Any,
        credential_verifier: str,
        reason: UpdateReason = UpdateReason.CONFIG_CHANGED,
    ) -> None:
        runtime = self._runtimes[bridge_id]
        async with runtime.lock:
            if self._runtimes.get(bridge_id) is not runtime:
                raise BridgeRuntimeUnavailable("bridge_unregistered")
            if (
                runtime.state is BridgeRuntimeState.REGISTERED
                and runtime.snapshot.config == config
                and runtime.snapshot.context == context
                and runtime.snapshot.credential_verifier == credential_verifier
            ):
                return
            old_generation = runtime.generation
            new_generation = old_generation + 1
            snapshot = BridgeSnapshot(bridge_id, new_generation, config, context, credential_verifier)
            runtime.generation = new_generation
            runtime.snapshot = snapshot
            runtime.state = BridgeRuntimeState.REGISTERED
            old_in_flight = tuple(runtime.in_flight.values())
            # Credential mapping and Bridge generation become visible through
            # the same reference assignment, with no await/preemption window.
            self._publish(snapshot)
        _LOGGER.info(
            "MCP Bridge runtime update bridge=%s generation=%s->%s reason=%s",
            bridge_id.subentry_id,
            old_generation,
            new_generation,
            reason.value,
        )
        if reason is UpdateReason.CREDENTIALS_CHANGED:
            _LOGGER.info(
                "MCP credential publication bridge=%s generation=%s->%s strategy=immutable_copy_and_swap",
                bridge_id.subentry_id,
                old_generation,
                new_generation,
            )
        for request in old_in_flight:
            if request.generation != new_generation:
                request.cancel()

    async def remove_bridge(self, bridge_id: BridgeIdentity) -> None:
        runtime = self._runtimes.get(bridge_id)
        if runtime is None:
            return
        async with runtime.lock:
            if self._runtimes.get(bridge_id) is not runtime:
                return
            old_generation = runtime.generation
            runtime.generation += 1
            runtime.state = BridgeRuntimeState.UNREGISTERED
            old_in_flight = tuple(runtime.in_flight.values())
            self._unpublish(bridge_id)
            self._runtimes.pop(bridge_id, None)
        _LOGGER.info(
            "MCP Bridge runtime removed bridge=%s generation=%s->%s reason=%s",
            bridge_id.subentry_id,
            old_generation,
            old_generation + 1,
            UpdateReason.REMOVED.value,
        )
        for request in old_in_flight:
            request.cancel()

    async def resolve_for_request(
        self,
        bridge_id: BridgeIdentity,
        authenticated_generation: int,
        request_id: object | None = None,
    ) -> RequestLease:
        runtime = self._runtimes.get(bridge_id)
        if runtime is None:
            raise BridgeRuntimeUnavailable("bridge_unregistered")
        async with runtime.lock:
            if self._runtimes.get(bridge_id) is not runtime:
                raise BridgeRuntimeUnavailable("bridge_unregistered")
            if (
                runtime.state is not BridgeRuntimeState.REGISTERED
                or runtime.generation != authenticated_generation
            ):
                raise BridgeRuntimeUnavailable("bridge_generation_unavailable")
            task = asyncio.current_task()
            if task is None:  # pragma: no cover - coroutine execution always has a task in production
                raise RuntimeError("MCP request has no owning asyncio task")
            internal_id = f"{request_id!s}:{uuid4().hex}"
            request = InFlightRequest(internal_id, runtime.generation, task)
            runtime.in_flight[internal_id] = request
            return RequestLease(runtime.snapshot, request)

    async def may_publish(self, lease: RequestLease) -> bool:
        runtime = self._runtimes.get(lease.snapshot.bridge_id)
        if runtime is None:
            return False
        async with runtime.lock:
            current = (
                self._runtimes.get(lease.snapshot.bridge_id) is runtime
                and
                runtime.state is BridgeRuntimeState.REGISTERED
                and runtime.generation == lease.snapshot.generation
            )
            current_generation = runtime.generation
        if not current:
            _LOGGER.debug(
                "discarded stale MCP response bridge=%s request_generation=%s current_generation=%s request_id=%s",
                lease.snapshot.bridge_id.subentry_id,
                lease.snapshot.generation,
                current_generation,
                lease.request.request_id,
            )
        return current

    async def unregister_in_flight(self, lease: RequestLease) -> None:
        runtime = self._runtimes.get(lease.snapshot.bridge_id)
        if runtime is None:
            return
        async with runtime.lock:
            runtime.in_flight.pop(lease.request.request_id, None)

    def _publish(self, snapshot: BridgeSnapshot) -> None:
        old = self._publication
        bridges = dict(old.bridges)
        credentials = {
            verifier: binding
            for verifier, binding in old.credentials.items()
            if binding.bridge_id != snapshot.bridge_id
        }
        bridges[snapshot.bridge_id] = snapshot
        if snapshot.credential_verifier:
            credentials[snapshot.credential_verifier] = CredentialBinding(
                snapshot.bridge_id, snapshot.generation
            )
        self._publication = RuntimePublication(
            MappingProxyType(bridges), MappingProxyType(credentials)
        )

    def _unpublish(self, bridge_id: BridgeIdentity) -> None:
        old = self._publication
        self._publication = RuntimePublication(
            MappingProxyType({key: value for key, value in old.bridges.items() if key != bridge_id}),
            MappingProxyType({key: value for key, value in old.credentials.items() if value.bridge_id != bridge_id}),
        )
