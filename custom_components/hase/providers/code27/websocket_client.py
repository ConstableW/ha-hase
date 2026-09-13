"""Push updates from CODE27's own WebSocket channel.

Reverse engineered from the device's web UI: /v1/ws/channel takes the same
HMAC-signed auth as the REST API, just passed as query parameters instead of
headers (browsers can't set custom headers on a WebSocket handshake), and
pushes JSON frames shaped {"type": "<event>", "data": {...}}.

Only a handful of event types exist on this channel (music/light-page state).
There is no push event for new chat messages - that's still polled
separately and faster, see coordinator.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import aiohttp

from .api import Code27Client
from ...const import (DOMAIN)

_LOGGER = logging.getLogger(f"{DOMAIN}.websocket")

_MIN_RECONNECT_DELAY = 2
_MAX_RECONNECT_DELAY = 60


class Code27WebSocket:
    """Maintains a reconnecting WS connection and dispatches typed events."""

    def __init__(self, hass, client: Code27Client) -> None:
        self._hass = hass
        self._client = client
        self._handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._task: asyncio.Task | None = None
        self._stopped = True

    def on(self, event_type: str, handler: Callable[[dict[str, Any]], None]) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def start(self) -> None:
        if self._task is not None:
            return
        self._stopped = False
        self._task = self._hass.async_create_background_task(
            self._run(), name="code27_websocket"
        )

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            task = self._task
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        delay = _MIN_RECONNECT_DELAY
        session = aiohttp.ClientSession()
        try:
            while not self._stopped:
                try:
                    url = await self._client.build_ws_url()
                    async with session.ws_connect(url, heartbeat=30) as ws:
                        _LOGGER.debug("CODE27 WebSocket connected")
                        delay = _MIN_RECONNECT_DELAY
                        async for msg in ws:
                            if self._stopped:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                self._dispatch(msg.data)
                            elif msg.type in (
                                aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.CLOSING,
                                aiohttp.WSMsgType.ERROR,
                            ):
                                break
                except asyncio.CancelledError:
                    raise
                except Exception as err:  # noqa: BLE001 - keep retrying regardless
                    _LOGGER.debug("CODE27 WebSocket error, will retry: %s", err)

                if self._stopped:
                    break
                await asyncio.sleep(delay)
                delay = min(delay * 2, _MAX_RECONNECT_DELAY)
        finally:
            await session.close()

    def _dispatch(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        event_type = message.get("type")
        data = message.get("data")
        if not isinstance(event_type, str) or not isinstance(data, dict):
            return
        for handler in self._handlers.get(event_type, []):
            try:
                handler(data)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("CODE27 WebSocket handler for %s failed", event_type)
