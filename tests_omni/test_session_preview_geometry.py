"""Dashboard snapshots reuse the existing capture without stretching landscapes."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from omniflow.engine import Match, OmniRunner
from omniflow.manager import InstanceManager, Session
from omniflow.schedule import ScheduleStore
from swordx.workflows import create_workflow, record_step


class CapturedScreen:
    def __init__(self, frame):
        self.frame = frame

    def screenshot(self):
        return self.frame.copy()


class DashboardPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.manager = InstanceManager(ScheduleStore(root / 'sessions.db'))
        self.folder = create_workflow('Preview geometry', root)
        template = np.random.default_rng(53).integers(0, 256, (960, 540, 3), dtype=np.uint8)
        record_step(self.folder, 'Idle', template, [(110, 200, 250, 320)], passive=True)
        self.template = template
        self.session = Session('127.0.0.1:16384', 'com.example', 'Preview', self.folder, 'owner')
        self.match = Match('idle', 'Idle', 1.0)

    def test_portrait_snapshot_is_small_and_not_stretched(self):
        frame = cv2.resize(self.template, (1080, 1920))
        self.manager._image(self.session, self.match, frame)
        image = self.session.latest()
        self.assertEqual(image.shape, (480, 270, 3))
        self.assertEqual((self.session.current, self.session.confidence), ('Idle', 1.0))

    def test_landscape_snapshot_preserves_true_orientation(self):
        # When the source image is landscape, the stored preview must be too.
        frame = cv2.resize(self.template, (1920, 1080))
        self.manager._image(self.session, self.match, frame)
        image = self.session.latest()
        self.assertEqual(image.shape, (270, 480, 3))

    def test_four_by_three_and_square_do_not_get_stretched(self):
        for size, expected in [((800, 600), (360, 480, 3)),
                               ((600, 800), (480, 360, 3)),
                               ((700, 700), (480, 480, 3))]:
            with self.subTest(size=size):
                self.manager._image(self.session, self.match, np.zeros((size[1], size[0], 3), np.uint8))
                self.assertEqual(self.session.latest().shape, expected)

    def test_thumbnail_is_an_independent_copy(self):
        source = np.full((960, 540, 3), 77, np.uint8)
        self.manager._image(self.session, self.match, source)
        prior = self.session.latest()
        prior[:] = 0
        self.assertTrue(np.all(self.session.latest() == 77))

    def test_cached_preview_age_is_from_adb_capture_not_publication(self):
        frame = np.zeros((960, 540, 3), dtype=np.uint8)
        acquisition = time.monotonic() - 5.0
        self.manager._image(self.session, self.match, frame,
                            captured_at=acquisition)
        self.assertEqual(self.session.image_at, acquisition)
        self.assertIsNone(self.session.recent_image())
        self.assertEqual(self.session.latest().shape, (480, 270, 3))

        self.manager._image(self.session, self.match, frame,
                            captured_at=time.monotonic())
        self.assertIsNotNone(self.session.recent_image())

    def test_capture_latency_telemetry_is_bounded_and_ignores_missing_values(self):
        frame = np.zeros((960, 540, 3), dtype=np.uint8)
        self.assertIsNone(self.session.capture_p95_ms())
        for latency in (120.0, 800.0, 300.0, None, float('nan')):
            self.manager._image(self.session, self.match, frame,
                                capture_latency_ms=latency)
        self.assertEqual(list(self.session.capture_latencies_ms), [120, 800, 300])
        self.assertEqual(self.session.capture_p95_ms(), 800.0)
        for _ in range(125):
            self.manager._image(self.session, self.match, frame,
                                capture_latency_ms=1400.0)
        self.assertEqual(len(self.session.capture_latencies_ms), 120)
        self.assertEqual(self.session.capture_p95_ms(), 1400.0)

    def test_runner_passes_original_capture_timestamp_to_preview(self):
        device = CapturedScreen(self.template.copy())
        holder = {}
        runner = OmniRunner(
            device, self.folder, 'com.example',
            image=lambda match, image: self.manager._image(
                self.session, match, image,
                captured_at=holder['runner'].last_capture_started_at),
            auto_relaunch=False, dry_run=True)
        holder['runner'] = runner
        # Recognition/publishing ends three seconds after acquisition,
        # without making the test sleep in real time.
        with patch('omniflow.engine.time.monotonic',
                   side_effect=[100.0, 100.1, 103.0]):
            runner.step(100)
        self.assertEqual(runner.last_capture_started_at, 100.0)
        self.assertEqual(self.session.image_at, 100.0)
        # CI runner uptime can itself be ~100 seconds. Assert freshness
        # against the simulated clock, not the host's unrelated uptime.
        with patch('omniflow.manager.time.monotonic', return_value=103.0):
            self.assertIsNone(self.session.recent_image())

    def test_runner_passes_raw_screen_to_dashboard_not_detection_canvas(self):
        # A distorted 540x960 canvas is still used for matching old workflow
        # coordinates; it must not also be the display image for a landscape app.
        frame = cv2.resize(self.template, (1920, 1080))
        device = CapturedScreen(frame)
        runner = OmniRunner(device, self.folder, 'com.example',
                            image=lambda match, image: self.manager._image(self.session, match, image),
                            auto_relaunch=False, dry_run=True)
        runner.step(100)
        self.assertEqual(self.session.latest().shape, (270, 480, 3))
        self.assertEqual(runner.last_frame.shape, (960, 540, 3))


if __name__ == '__main__':
    unittest.main()
