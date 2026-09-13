"""CODE27 executes typed intent with the owner's unrestricted action/query policy.

Exposure and resolver safety checks still apply. Transport and dialogue stay in
bridge.py; operation semantics belong to the shared executor.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ...core.executor import ActionExecutor
from ...core.models import ActionDraft, AreaPolicy, ExecutionResult, ScopedAreaPolicy
from ...spellbooker_ha import HaServiceCaller, HaTargetResolver

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_UNRESTRICTED_POLICY = ScopedAreaPolicy(
    actions=AreaPolicy(allowed_areas=None, unrestricted=True),
    queries=AreaPolicy(allowed_areas=None, unrestricted=True),
)


class Code27FrontendAdapter:
    def __init__(self, hass: HomeAssistant) -> None:
        self._executor = ActionExecutor(HaTargetResolver(hass), HaServiceCaller(hass))

    async def execute(
        self, draft: ActionDraft, *, context: object | None = None,
        source_area_id: str | None = None,
    ) -> ExecutionResult:
        return await self._executor.execute(
            draft, _UNRESTRICTED_POLICY, context=context, source_area_id=source_area_id,
        )
