"""Preview availability throughout MediaCodec retries (no emulator required)."""
import unittest
from unittest.mock import patch

from swordx.preview import snapshot_during_retry


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.stop = False
        self.waited = []

    def is_set(self):
        return self.stop

    def wait(self, seconds):
        self.waited.append(seconds)
        self.now += seconds
        return self.stop


class SnapshotRetryTests(unittest.TestCase):
    def test_eight_second_retry_keeps_display_fresh(self):
        clock = FakeClock()
        captures = []
        with patch('swordx.preview.time.monotonic', side_effect=lambda: clock.now):
            snapshot_during_retry(clock, 8.0, lambda: captures.append(clock.now))
        self.assertEqual(captures, [100.0, 101.5, 103.0, 104.5, 106.0, 107.5])
        self.assertAlmostEqual(clock.now, 108.0)
        self.assertTrue(all(x <= 1.5 for x in clock.waited))

    def test_retry_never_outlasts_its_budget_on_slow_screenshot(self):
        clock = FakeClock()
        captures = []
        def slow_capture():
            captures.append(clock.now)
            clock.now += 2.2
        with patch('swordx.preview.time.monotonic', side_effect=lambda: clock.now):
            snapshot_during_retry(clock, 3.0, slow_capture)
        self.assertEqual(len(captures), 1)
        self.assertAlmostEqual(clock.now, 103.0)

    def test_stop_interrupts_between_snapshot_attempts(self):
        clock = FakeClock()
        captures = []
        def capture():
            captures.append(clock.now)
            clock.stop = True
        with patch('swordx.preview.time.monotonic', side_effect=lambda: clock.now):
            snapshot_during_retry(clock, 30, capture)
        self.assertEqual(len(captures), 1)
        self.assertEqual(clock.waited, [1.5])

    def test_zero_delay_still_allows_one_immediate_screenshot(self):
        clock = FakeClock()
        captures = []
        with patch('swordx.preview.time.monotonic', side_effect=lambda: clock.now):
            snapshot_during_retry(clock, 0, lambda: captures.append(clock.now))
        self.assertEqual(captures, [100.0])
        self.assertEqual(clock.waited, [])

    def test_invalid_cadence_rejected(self):
        with self.assertRaises(ValueError):
            snapshot_during_retry(FakeClock(), 2, lambda: None, interval=0)


if __name__ == '__main__':
    unittest.main()