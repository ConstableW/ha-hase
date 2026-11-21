"""Regression tests for WP-D and WP-E fixes:

- S2: return_exceptions=True in _poll_once + _check_poll_results
- E1: _chat_dates never stores None; get(slot, "") not get(slot) or ""
- B3: asyncio.timeout around conversation.async_converse in _ask_assist
- B4: TTL tuples in _pending_chat_replies; stale entries pruned before match
- C1: Code27RequestExpiredError is logged, not treated as a credential failure
- C3: Code27RequestExpiredError is a distinct subclass of Code27AuthError
- C7: chat poller backs off after idle period, reducing signed requests
"""
from __future__ import annotations

import time
import unittest
from unittest.mock import AsyncMock, patch

import test_harness_support as support

N = support.N
api = support.api
coord = support.coordinator_module


# ---------------------------------------------------------------------------
# C3 / C1 — Code27RequestExpiredError
# ---------------------------------------------------------------------------
class RequestExpiredErrorTests(unittest.TestCase):
    def test_is_subclass_of_auth_error(self):
        err = api.Code27RequestExpiredError()
        self.assertIsInstance(err, api.Code27AuthError)
        self.assertIsInstance(err, api.Code27Error)

    def test_code_is_REQUEST_EXPIRED(self):
        err = api.Code27RequestExpiredError()
        self.assertEqual(err.code, "REQUEST_EXPIRED")

    def test_parse_json_response_raises_request_expired_on_401(self):
        client = api.Code27Client.__new__(api.Code27Client)
        with self.assertRaises(api.Code27RequestExpiredError):
            client._parse_json_response(401, '{"code": "REQUEST_EXPIRED"}')

    def test_parse_json_response_raises_auth_error_on_other_401(self):
        client = api.Code27Client.__new__(api.Code27Client)
        with self.assertRaises(api.Code27AuthError) as ctx:
            client._parse_json_response(401, '{"code": "UNAUTHORIZED"}')
        self.assertNotIsInstance(ctx.exception, api.Code27RequestExpiredError)

    def test_plain_401_without_json_raises_auth_error_not_expired(self):
        client = api.Code27Client.__new__(api.Code27Client)
        with self.assertRaises(api.Code27AuthError) as ctx:
            client._parse_json_response(401, "not json")
        self.assertNotIsInstance(ctx.exception, api.Code27RequestExpiredError)


# ---------------------------------------------------------------------------
# S2 — _check_poll_results
# ---------------------------------------------------------------------------
class CheckPollResultsTests(unittest.TestCase):
    def _ok_results(self, n=15):
        return [{"ok": True}] * n

    def test_returns_results_when_no_exceptions(self):
        results = self._ok_results()
        cleaned = coord.Code27Coordinator._check_poll_results(results)
        self.assertIs(cleaned, results)

    def test_re_raises_auth_error(self):
        results = self._ok_results()
        results[2] = api.Code27AuthError("UNAUTHORIZED")
        with self.assertRaises(api.Code27AuthError):
            coord.Code27Coordinator._check_poll_results(results)

    def test_re_raises_request_expired_as_auth_error_subtype(self):
        results = self._ok_results()
        results[0] = api.Code27RequestExpiredError()
        with self.assertRaises(api.Code27RequestExpiredError):
            coord.Code27Coordinator._check_poll_results(results)

    def test_re_raises_connection_error(self):
        results = self._ok_results()
        results[0] = api.Code27ConnectionError("offline")
        with self.assertRaises(api.Code27ConnectionError):
            coord.Code27Coordinator._check_poll_results(results)

    def test_auth_error_takes_priority_over_connection_error(self):
        results = self._ok_results()
        results[0] = api.Code27ConnectionError("offline")
        results[1] = api.Code27AuthError("UNAUTHORIZED")
        with self.assertRaises(api.Code27AuthError):
            coord.Code27Coordinator._check_poll_results(results)

    def test_optional_storage_connection_failure_is_mergeable(self):
        """Non-auth optional failures remain available for per-field merging."""
        results = self._ok_results(16)
        results[15] = api.Code27ConnectionError("music_storage_offline")
        # Should NOT raise — last slot is _poll_music_storage, excluded from scan.
        cleaned = coord.Code27Coordinator._check_poll_results(results)
        self.assertIs(cleaned, results)


# ---------------------------------------------------------------------------
# C1 — RequestExpired treated as UpdateFailed, not ConfigEntryAuthFailed
# ---------------------------------------------------------------------------
class RequestExpiredCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    setUp = support.DeliveryTests.setUp

    def _setup_full_client(self):
        for method in [
            "get_system", "get_music_page", "get_music_source", "get_music_playback",
            "get_music_audio_route", "get_bluetooth_state", "get_light_master_switch",
            "get_applied_light_project", "get_current_slot", "get_music_storage",
            "get_chat_history_count", "get_proactive_config",
        ]:
            setattr(self.client, method, AsyncMock(return_value={}))
        for method in ["list_light_projects", "list_alarms", "list_memos",
                       "list_character_settings"]:
            setattr(self.client, method, AsyncMock(return_value=[]))

    async def test_request_expired_raises_update_failed_not_auth_failed(self):
        """REQUEST_EXPIRED must not trigger a re-auth flow (C1)."""
        class UpdateFailed(Exception):
            pass
        class AuthFailed(Exception):
            pass
        self._setup_full_client()
        self.client.get_system.side_effect = api.Code27RequestExpiredError()
        with patch.object(coord, "UpdateFailed", UpdateFailed, create=True), \
             patch.object(coord, "ConfigEntryAuthFailed", AuthFailed, create=True), \
             self.assertRaises(UpdateFailed):
            await self.c._async_update_data()

    async def test_real_auth_error_still_raises_config_entry_auth_failed(self):
        """A genuine credential failure must still trigger re-auth (C1)."""
        class UpdateFailed(Exception):
            pass
        class AuthFailed(Exception):
            pass
        self._setup_full_client()
        self.client.get_system.side_effect = api.Code27AuthError("UNAUTHORIZED")
        with patch.object(coord, "UpdateFailed", UpdateFailed, create=True), \
             patch.object(coord, "ConfigEntryAuthFailed", AuthFailed, create=True), \
             self.assertRaises(AuthFailed):
            await self.c._async_update_data()


# ---------------------------------------------------------------------------
# E1 — _chat_dates never stores None
# ---------------------------------------------------------------------------
class ChatDateSentinelTests(unittest.IsolatedAsyncioTestCase):
    setUp = support.DeliveryTests.setUp

    def _prime_coordinator(self):
        self.c.data = coord.Code27Data(
            current_slot={"slot_number": 1},
        )
        self.c._chat_active_slot = 1

    async def test_null_api_date_does_not_clobber_existing_date(self):
        """If the API returns date=None, the previous good date is kept (E1)."""
        self._prime_coordinator()
        self.c._chat_dates[1] = "2024-01-15"
        self.c._chat_seen[1] = set()

        async def _fake_snapshot(slot, since_date, last_id):
            return None, []  # date=None

        self.client.get_chat_history_snapshot = _fake_snapshot
        await self.c._async_poll_chat()
        self.assertEqual(self.c._chat_dates[1], "2024-01-15")

    async def test_null_api_date_initialises_slot_with_empty_string(self):
        """First poll returning date=None initialises slot to '' not None (E1)."""
        self._prime_coordinator()

        async def _fake_snapshot(slot, since_date, last_id):
            return None, []

        self.client.get_chat_history_snapshot = _fake_snapshot
        await self.c._async_poll_chat()
        self.assertIn(1, self.c._chat_dates)
        self.assertEqual(self.c._chat_dates[1], "")

    async def test_since_date_uses_get_with_default_not_or_empty(self):
        """slot not yet in _chat_dates uses '' not the None-coercion path (E1)."""
        self._prime_coordinator()
        seen_since_dates = []

        async def _capture_snapshot(slot, since_date, last_id):
            seen_since_dates.append(since_date)
            return "2024-01-16", []

        self.client.get_chat_history_snapshot = _capture_snapshot
        # Slot 1 not in _chat_dates — must pass "" not None
        await self.c._async_poll_chat()
        self.assertEqual(seen_since_dates[0], "")


# ---------------------------------------------------------------------------
# B4 — TTL on _pending_chat_replies
# ---------------------------------------------------------------------------
class PendingChatReplyTTLTests(unittest.IsolatedAsyncioTestCase):
    setUp = support.DeliveryTests.setUp

    def _prime_coordinator(self):
        self.c.data = coord.Code27Data(current_slot={"slot_number": 1})
        self.c._chat_active_slot = 1

    async def test_expired_reservation_does_not_suppress_user_message(self):
        """A stale reservation must not silently swallow a real user message (B4)."""
        self._prime_coordinator()
        self.c._chat_seen[1] = set()
        self.c._chat_dates[1] = ""
        fired_events = []

        async def _fake_snapshot(slot, since_date, last_id):
            return "2024-01-16", [
                {"chat_id": "msg1", "role": "user", "content": "ok"}
            ]

        self.client.get_chat_history_snapshot = _fake_snapshot

        EVENT_USER = coord.EVENT_USER_MESSAGE
        fired_events = []

        def _capture_event(event_type, data):
            if event_type == EVENT_USER:
                fired_events.append(data)

        self.hass.bus.async_fire = _capture_event

        # Inject an already-expired reservation
        past_deadline = time.monotonic() - 1.0
        self.c._pending_chat_replies.append((1, "ok", past_deadline))

        self.c._chat_last_polled_at = 0.0  # bypass C7 backoff
        await self.c._async_poll_chat()
        # Expired reservation cleaned up → message fires as user event
        self.assertEqual(len(fired_events), 1)
        self.assertEqual(self.c._pending_chat_replies, [])

    async def test_fresh_reservation_still_suppresses_echo(self):
        """A reservation within TTL should still mark the message as own (B4)."""
        self._prime_coordinator()
        self.c._chat_seen[1] = set()
        self.c._chat_dates[1] = ""
        fired_events = []

        async def _fake_snapshot(slot, since_date, last_id):
            return "2024-01-16", [
                {"chat_id": "msg2", "role": "user", "content": "starte licht"}
            ]

        self.client.get_chat_history_snapshot = _fake_snapshot

        EVENT_USER = coord.EVENT_USER_MESSAGE
        fired_events = []

        def _capture_event(event_type, data):
            if event_type == EVENT_USER:
                fired_events.append(data)

        self.hass.bus.async_fire = _capture_event

        # Fresh reservation (well within TTL)
        future_deadline = time.monotonic() + 30.0
        self.c._pending_chat_replies.append((1, "starte licht", future_deadline))

        await self.c._async_poll_chat()
        # Fresh reservation → message is own → no user event fired
        self.assertEqual(len(fired_events), 0)
        # Reservation consumed by match
        self.assertEqual(self.c._pending_chat_replies, [])


# ---------------------------------------------------------------------------
# C7 — idle backoff for chat poller
# ---------------------------------------------------------------------------
class ChatPollerBackoffTests(unittest.IsolatedAsyncioTestCase):
    """_async_poll_chat must skip ticks when no events have fired for a while (C7)."""
    setUp = support.DeliveryTests.setUp

    def _prime_coordinator(self):
        self.c.data = coord.Code27Data(current_slot={"slot_number": 1})
        self.c._chat_active_slot = 1
        self.c._chat_seen[1] = set()
        self.c._chat_dates[1] = ""

    async def test_normal_interval_polls_every_tick(self):
        """Within 5 min of last event, every tick runs the poll."""
        self._prime_coordinator()
        poll_count = 0

        async def _fake_snapshot(slot, since, last_id):
            nonlocal poll_count
            poll_count += 1
            return "", []

        self.client.get_chat_history_snapshot = _fake_snapshot
        self.c._chat_last_event_at = time.monotonic()  # just now
        self.c._chat_last_polled_at = 0.0

        # Three ticks at normal interval (> 3s apart, so all should poll)
        for _ in range(3):
            self.c._chat_last_polled_at -= 4.0  # pretend 4s have passed
            await self.c._async_poll_chat()

        self.assertEqual(poll_count, 3)

    async def test_backoff_skips_ticks_after_5_min_idle(self):
        """After 5+ min of no events, ticks closer than 15s apart are skipped."""
        self._prime_coordinator()
        poll_count = 0

        async def _fake_snapshot(slot, since, last_id):
            nonlocal poll_count
            poll_count += 1
            return "", []

        self.client.get_chat_history_snapshot = _fake_snapshot
        # Simulate 6 minutes of idle time
        self.c._chat_last_event_at = time.monotonic() - 6 * 60
        self.c._chat_last_polled_at = 0.0

        # First tick: allowed (polled_at=0 → gap is huge → polls)
        await self.c._async_poll_chat()
        self.assertEqual(poll_count, 1)

        # Next tick immediately (< 15s later): should be skipped
        await self.c._async_poll_chat()
        self.assertEqual(poll_count, 1, "tick within 15s should be skipped during 5-min backoff")

    async def test_new_event_resets_backoff(self):
        """After a new chat event, polling resumes at normal rate."""
        self._prime_coordinator()
        poll_count = 0

        async def _fake_snapshot(slot, since, last_id):
            nonlocal poll_count
            poll_count += 1
            return "2024-01-01", [
                {"chat_id": "msg1", "role": "user", "content": "hallo"}
            ]

        self.client.get_chat_history_snapshot = _fake_snapshot
        self.hass.bus.async_fire = lambda *a, **kw: None

        # Start in 15-min backoff territory
        self.c._chat_last_event_at = time.monotonic() - 16 * 60
        self.c._chat_last_polled_at = 0.0

        # First tick fires the poll (large gap since last poll)
        await self.c._async_poll_chat()
        self.assertEqual(poll_count, 1)
        # Firing the event reset _chat_last_event_at to ~now
        self.assertGreater(self.c._chat_last_event_at, time.monotonic() - 2)

        # Next tick immediately is still skipped (polled_at was just set)
        await self.c._async_poll_chat()
        self.assertEqual(poll_count, 1)

        # But after 4s (normal 3s interval threshold), a tick should go through
        self.c._chat_last_polled_at -= 4.0
        await self.c._async_poll_chat()
        self.assertEqual(poll_count, 2)


if __name__ == "__main__":
    unittest.main()
