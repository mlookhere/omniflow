"""Capture health is measured from real displayed frames, not just screenshots."""
import unittest
from omniflow.capture_health import CaptureHealth, evaluate_capture


class CaptureHealthTests(unittest.TestCase):
    def test_snapshot_fallback_keeps_preview_alive_but_does_not_count_as_video(self):
        health = CaptureHealth(100)
        for t in (101, 102, 103, 104):
            health.observe(t, 'snapshot')
        report = health.summary(105)
        self.assertEqual(report['displayed_video_frames'], 0)
        self.assertEqual(report['fallback_snapshots'], 4)
        self.assertEqual(report['max_preview_gap_seconds'], 1)
        self.assertEqual(report['max_video_gap_seconds'], 5)
        self.assertIn('snapshot-only', ' '.join(evaluate_capture(report)))
        self.assertFalse(evaluate_capture(report, allow_snapshot_only=True))

    def test_error_22_recovery_needs_subsequent_h264_frame(self):
        health = CaptureHealth(100)
        health.record_error(101, 'MediaCodec error 22: invalid argument')
        health.record_error(102, 'EINVAL (22)')
        health.observe(102, 'snapshot')
        report = health.summary(103)
        self.assertEqual((report['error22_events'], report['error22_unresolved']), (2, 1))
        self.assertTrue(evaluate_capture(report, allow_snapshot_only=True))
        health.observe(106, 'video')
        report = health.summary(107)
        self.assertEqual(report['error22_recovered'], 1)
        self.assertEqual(report['error22_unresolved'], 0)
        self.assertEqual(report['max_error22_recovery_seconds'], 5)

    def test_preview_blackout_and_fps_fail_closed(self):
        health = CaptureHealth(0)
        health.observe(0.1, 'video')
        health.observe(2.0, 'snapshot')
        health.observe(3, 'video')
        report = health.summary(4)
        self.assertGreaterEqual(report['max_preview_gap_seconds'], 1.8)
        self.assertEqual(report['displayed_video_fps'], .5)
        failures = evaluate_capture(report, min_video_fps=2, max_preview_gap=1)
        self.assertEqual(len(failures), 2)

    def test_no_frames_not_accepted_even_if_fallback_only_allowed(self):
        report = CaptureHealth(100).summary(102)
        self.assertTrue(evaluate_capture(report, allow_snapshot_only=True))

    def test_unrecognized_frame_type_and_invalid_sample_time(self):
        health = CaptureHealth(100)
        with self.assertRaises(ValueError):
            health.observe(99, 'video')
        with self.assertRaises(ValueError):
            health.observe(101, 'corrupt')


if __name__ == '__main__':
    unittest.main()
