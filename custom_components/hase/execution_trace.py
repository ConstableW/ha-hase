"""Bounded, redacted recent Spellbook execution diagnostics.

The ring is process-local and intentionally contains no arguments, chat text,
service payloads, bearer credentials or raw exception messages. Home Assistant
Diagnostics/Repairs remain the persistent system-level support channels.
"""
from __future__ import annotations

import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

from .core.models import ExecutionResult

_KEY = "hase_execution_traces"
_LIMIT = 100


def _safe_reports(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    reports: list[dict[str, Any]] = []
    for item in value[:40]:
        if not isinstance(item, dict):
            continue
        reports.append({
            key: item[key]
            for key in ("index", "alias", "status", "reason_code", "kind")
            if key in item and isinstance(item[key], (str, int, float, bool))
        })
    return reports


def record_execution(
    hass: Any,
    *,
    channel: str,
    book_id: str | None,
    spell_id: str | None,
    result: ExecutionResult,
    started_at: float,
    correlation_id: str | None = None,
) -> str:
    """Record only known-safe fields; return the trace id for UI correlation."""
    trace_id = uuid.uuid4().hex
    data = result.data if isinstance(result.data, dict) else {}
    entry = {
        "id": trace_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_ms": max(0, round((time.monotonic() - started_at) * 1000)),
        "channel": channel,
        "book_id": book_id,
        "spell_id": spell_id,
        "correlation_id": correlation_id,
        "status": result.status.value,
        "reason_code": result.reason_code,
        "outcome_certainty": data.get("outcome_certainty"),
        "error_type": data.get("error_type"),
        "target_count": len(data.get("entity_ids") or ()),
        "steps": _safe_reports(data.get("steps")),
        "sources": _safe_reports(data.get("sources")),
    }
    bucket = hass.data.setdefault(_KEY, deque(maxlen=_LIMIT))
    bucket.append(entry)
    return trace_id


def recent_executions(hass: Any) -> list[dict[str, Any]]:
    """Newest first, detached from the mutable ring."""
    return list(reversed(hass.data.get(_KEY, ())))
