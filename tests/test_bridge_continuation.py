"""Regression coverage for _forward_to_assist's catchphrase-substitution
guard against a continuing conversation (second-round audit finding on
bridge.py / _ask_assist's continue_conversation plumbing).
"""
import unittest
from unittest.mock import AsyncMock, patch

import test_bridge_delivery as support

N = support.N
bridge = support.bridge


class ContinuationCatchphraseTests(unittest.IsolatedAsyncioTestCase):
    setUp = support.DeliveryTests.setUp

    async def _forward(self, *, continue_conversation, catchphrase="Mach ich."):
        response = N(response_type=bridge.intent.IntentResponseType.ACTION_DONE)
        with patch.object(
            bridge, "_ask_assist",
            AsyncMock(return_value=("Meinst du Buero oder Garten?", response, continue_conversation)),
        ), patch.object(bridge, "_output_speech", AsyncMock()) as output, \
             patch.object(bridge, "_log_activity"):
            speech, _response, _command = await bridge._forward_to_assist(
                self.hass, self.entry, self.c, "aktiviere", None, catchphrase=catchphrase,
            )
        return speech, output

    async def test_continuing_conversation_keeps_the_real_question(self):
        # Found via a second-round audit: response_type is ACTION_DONE
        # even when the agent (any conversation agent can be configured
        # here, a spellbooker entry included) is asking a follow-up
        # question - HA has no distinct response_type for that. Without
        # this guard, a configured catchphrase would silently replace
        # the actual question the user needs to hear to answer it.
        speech, output = await self._forward(continue_conversation=True)
        self.assertEqual(speech, "Meinst du Buero oder Garten?")
        output.assert_awaited_once()
        self.assertEqual(output.call_args.args[3], "Meinst du Buero oder Garten?")

    async def test_one_shot_action_done_still_gets_the_catchphrase(self):
        # The guard must not suppress the existing, correct behavior
        # for a genuinely finished one-shot action.
        speech, output = await self._forward(continue_conversation=False)
        self.assertEqual(speech, "Mach ich.")
        self.assertEqual(output.call_args.args[3], "Mach ich.")


if __name__ == "__main__":
    unittest.main()
