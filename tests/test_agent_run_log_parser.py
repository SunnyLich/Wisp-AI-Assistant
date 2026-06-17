"""Tests for test agent run log parser."""

from __future__ import annotations

import unittest

from ui.agent.log_parser import log_body, parse_live_log_event


class AgentRunLogParserTests(unittest.TestCase):
    """Test case for agent run log parser tests behavior."""
    def test_strips_timestamp(self):
        """Verify strips timestamp behavior."""
        self.assertEqual(log_body("[12:34:56] agent turn 1/3: Builder"), "agent turn 1/3: Builder")

    def test_parses_agent_turn(self):
        """Verify parses agent turn behavior."""
        event = parse_live_log_event("[12:34:56] agent turn 1/3: Builder")

        self.assertEqual(event.kind, "agent_turn")
        self.assertEqual(event.agent, "Builder")

    def test_parses_direct_message(self):
        """Verify parses direct message behavior."""
        event = parse_live_log_event("[12:34:56] message: Builder -> Reviewer: Ready")

        self.assertEqual(event.kind, "message")


if __name__ == "__main__":
    unittest.main()

