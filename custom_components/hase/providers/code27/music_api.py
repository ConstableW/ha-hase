"""CODE27's own music WebSocket contract (``hase/code27/music``).

The contract is namespaced under the provider that defines it instead of
claiming a generic HASE music facility: CODE27 owns the operations, the payload
shape and the error codes. Its consumer today is the provider-neutral HASEAMP
card, which never talks to the CODE27 REST or WebSocket API itself and reaches
the runtime only through these Home Assistant WebSocket commands - the same
request shape the HASE Studio panel uses for ``hase/studio``
(``studio_api.py``), just provider-scoped.

Two commands:

* ``hase/code27/music`` - one operation per request, dispatched by the CODE27
  music runtime (``OPERATIONS`` is the complete list).
* ``hase/code27/music/subscribe`` - pushes a fresh snapshot whenever the
  runtime's queue/playlist/playback state changes, so the card needs no polling
  of its own. The subscription carries no browser state: reloading the
  dashboard only re-subscribes.
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.components import websocket_api

from .music_model import MusicError
from .music_runtime import (
    OPERATIONS,
    async_get_music_runtime,
    async_setup_music,
    code27_bucket,
)

_LOGGER = logging.getLogger(__name__)

NAMESPACE = "hase/code27/music"
COMMAND_TYPE = NAMESPACE
SUBSCRIBE_TYPE = f"{NAMESPACE}/subscribe"

_COMMANDS_REGISTERED = "music_commands"


@websocket_api.websocket_command(
    {
        vol.Required("type"): COMMAND_TYPE,
        vol.Required("operation"): vol.In(sorted(OPERATIONS)),
        vol.Optional("data", default=dict): dict,
    }
)
@websocket_api.async_response
async def music_command(hass, connection, msg):
    """One CODE27 music operation.

    The backend is created on first use if the entry-level bootstrap has not
    run yet, so the command is never a mystery failure just because of load
    order.
    """
    try:
        backend = async_get_music_runtime(hass) or await async_setup_music(hass)
    except Exception:
        _LOGGER.exception("CODE27 music runtime could not be initialised")
        connection.send_error(
            msg["id"], "music_unavailable", "Die CODE27-Musik konnte nicht geladen werden."
        )
        return
    try:
        result = await backend.async_dispatch(msg["operation"], msg.get("data"))
    except MusicError as err:
        connection.send_error(msg["id"], "music_validation", str(err))
    except Exception:
        _LOGGER.exception("CODE27 music operation %s failed", msg["operation"])
        connection.send_error(
            msg["id"],
            "music_error",
            "Die CODE27-Musik-Anfrage konnte nicht abgeschlossen werden.",
        )
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): SUBSCRIBE_TYPE,
        vol.Optional("target"): vol.Any(str, None),
    }
)
@websocket_api.async_response
async def music_subscribe(hass, connection, msg):
    """Push snapshots for one CODE27 target until the card unsubscribes."""
    try:
        backend = async_get_music_runtime(hass) or await async_setup_music(hass)
    except Exception:
        _LOGGER.exception("CODE27 music runtime could not be initialised")
        connection.send_error(
            msg["id"], "music_unavailable", "Die CODE27-Musik konnte nicht geladen werden."
        )
        return

    target = msg.get("target")
    create_task = getattr(hass, "async_create_task", None)

    async def _send(entry_id: str) -> None:
        try:
            snapshot = await backend.async_snapshot(entry_id, include_tree=False)
        except Exception:
            _LOGGER.exception("CODE27 music snapshot push failed for %s", entry_id)
            return
        # M8: discard if the snapshot resolved to a different device than the
        # one the card subscribed to (race between _on_change filter and the
        # async gap before the snapshot is built).
        if target and snapshot.get("target", {}).get("id") != target:
            return
        connection.send_message(
            websocket_api.event_message(
                msg["id"], {"type": "snapshot", "snapshot": snapshot}
            )
        )

    def _on_change(entry_id: str) -> None:
        if target and entry_id != target:
            return
        if not callable(create_task):
            return
        create_task(_send(entry_id))

    connection.subscriptions[msg["id"]] = backend.async_add_listener(_on_change)
    connection.send_result(msg["id"])


def async_register_music_commands(hass) -> bool:
    """Register both commands once per Home Assistant instance."""
    bucket = code27_bucket(hass, create=True)
    if bucket.get(_COMMANDS_REGISTERED):
        return False
    websocket_api.async_register_command(hass, music_command)
    websocket_api.async_register_command(hass, music_subscribe)
    bucket[_COMMANDS_REGISTERED] = True
    return True


async def async_setup_music_entry(hass, entry, coordinator) -> None:
    """Wire one CODE27 device into the music runtime.

    Creates/loads the shared runtime, registers the WebSocket contract and
    subscribes to the coordinator's playback updates, which is what drives
    queue autoadvance. Needs ``hass.async_create_task`` because the coordinator
    listener is synchronous while the advance itself is asynchronous - a
    lightweight harness without an event loop is skipped instead of being
    half-wired.
    """
    create_task = getattr(hass, "async_create_task", None)
    if not callable(create_task):
        return

    backend = await async_setup_music(hass)
    async_register_music_commands(hass)

    def _on_playback_update() -> None:
        data = getattr(coordinator, "data", None)
        playback = getattr(data, "music_playback", None)
        create_task(
            backend.async_note_playback(
                entry.entry_id, playback if isinstance(playback, dict) else {}
            )
        )

    entry.async_on_unload(coordinator.async_add_listener(_on_playback_update))
    # Seed the previous observation so the first sight of an already running
    # track can never be mistaken for a finished one.
    _on_playback_update()
