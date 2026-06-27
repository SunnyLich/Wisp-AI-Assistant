"""Tests for core.system.safe (swallow / safe best-effort helpers)."""
import logging
import unittest

from core.system.safe import safe, swallow


class SwallowTests(unittest.TestCase):
    def test_suppresses_exception_by_default(self):
        ran_after = False
        with swallow():
            raise ValueError("boom")
        ran_after = True  # reached only because the error was swallowed
        self.assertTrue(ran_after)

    def test_no_error_runs_block_normally(self):
        seen = []
        with swallow():
            seen.append(1)
        self.assertEqual(seen, [1])

    def test_only_listed_exceptions_are_suppressed(self):
        with self.assertRaises(KeyError):
            with swallow(ValueError):
                raise KeyError("not suppressed")

    def test_does_not_swallow_keyboard_interrupt(self):
        with self.assertRaises(KeyboardInterrupt):
            with swallow():
                raise KeyboardInterrupt

    def test_logs_at_debug_when_requested(self):
        with self.assertLogs("core.system.safe", level="DEBUG") as cm:
            with swallow(log="probe failed"):
                raise RuntimeError("nope")
        self.assertTrue(any("probe failed" in line for line in cm.output))

    def test_silent_when_no_log_message(self):
        logger = logging.getLogger("core.system.safe")
        with self.assertNoLogs(logger, level="DEBUG"):
            with swallow():
                raise RuntimeError("quiet")


class SafeTests(unittest.TestCase):
    def test_returns_value_on_success(self):
        self.assertEqual(safe(lambda: 21 * 2), 42)

    def test_returns_default_on_failure(self):
        self.assertEqual(safe(lambda: int("nope"), default=0), 0)

    def test_default_is_none(self):
        self.assertIsNone(safe(lambda: 1 / 0))

    def test_reraises_unlisted_exception(self):
        with self.assertRaises(ZeroDivisionError):
            safe(lambda: 1 / 0, default=0, exceptions=(ValueError,))

    def test_logs_at_debug_when_requested(self):
        with self.assertLogs("core.system.safe", level="DEBUG") as cm:
            safe(lambda: 1 / 0, default=0, log="compute")
        self.assertTrue(any("compute" in line for line in cm.output))


if __name__ == "__main__":
    unittest.main()
