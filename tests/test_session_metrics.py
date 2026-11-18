"""Session-only metrics use monotonic time and exclude pauses from run durations."""
import unittest

from swordx.stats import SessionStats, format_duration


class SessionMetricsTests(unittest.TestCase):
    def test_initial_session_has_no_synthetic_average(self):
        stats = SessionStats(started=100)
        current = stats.snapshot(130)
        self.assertEqual((current.runs, current.elapsed, current.active), (0, 30, 30))
        self.assertIsNone(current.average)
        self.assertIsNone(current.latest)
        self.assertEqual(current.per_hour, 0)

    def test_multiple_runs_fastest_average_and_rate(self):
        stats = SessionStats(started=100)
        self.assertEqual(stats.complete(190), 90)
        self.assertEqual(stats.complete(250), 60)
        self.assertEqual(stats.complete(370), 120)
        current = stats.snapshot(400)
        self.assertEqual(current.runs, 3)
        self.assertEqual(current.elapsed, 300)
        self.assertEqual(current.average, 90)
        self.assertEqual(current.fastest, 60)
        self.assertEqual(current.latest, 120)
        self.assertAlmostEqual(current.per_hour, 36)

    def test_paused_time_does_not_inflate_runs(self):
        stats = SessionStats(started=0)
        stats.pause(10)
        stats.pause(15)
        stats.resume(110)
        stats.resume(111)
        stats.complete(130)
        current = stats.snapshot(140)
        self.assertEqual(current.elapsed, 140)
        self.assertEqual(current.active, 40)
        self.assertEqual(current.average, 30)

    def test_stop_freezes_metrics_and_prevents_extra_completion(self):
        stats = SessionStats(started=0)
        stats.complete(10)
        stats.finish(30)
        self.assertEqual(stats.snapshot(999).elapsed, 30)
        with self.assertRaises(ValueError):
            stats.complete(999)

    def test_formatting(self):
        self.assertEqual(format_duration(None), '—')
        self.assertEqual(format_duration(12), '00:12')
        self.assertEqual(format_duration(125), '02:05')
        self.assertEqual(format_duration(3675), '1:01:15')


if __name__ == '__main__':
    unittest.main()
