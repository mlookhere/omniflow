"""Qt/preview isolation and non-blocking switching smoke tests."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication
    from omniflow.ui import VideoThread, OmniWindow, preview_image
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, 'PySide6 is required for offscreen video tests')
class VideoGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_latest_frame_always_wins(self):
        stream = VideoThread('127.0.0.1:16384')
        stream._publish(np.full((480,270,3), 80, np.uint8), 'video')
        stream._publish(np.full((480,270,3), 130, np.uint8), 'video')
        image, kind = stream.take()
        self.assertEqual((image.width(), image.height(), kind), (270, 480, 'video'))
        self.assertEqual(image.pixelColor(0, 0).red(), 130)
        self.assertIsNone(stream.take()[0])

    def test_landscape_preview_uses_available_display_area(self):
        stream = VideoThread('127.0.0.1:16384')
        stream._publish(np.full((270,480,3), 120, np.uint8), 'video')
        image, kind = stream.take()
        self.assertEqual((image.width(), image.height(), kind), (480, 270, 'video'))
        self.assertTrue(stream.landscape)

    def test_detector_fallback_uses_full_frame_not_cropped_left_corner(self):
        portrait = np.zeros((960, 540, 3), np.uint8)
        portrait[:, 270:, 0] = 240  # Right half is red in RGB.
        image, _ = preview_image(portrait)
        self.assertEqual((image.width(), image.height()), (270, 480))
        self.assertGreater(image.pixelColor(250, 250).red(), 220)
        self.assertEqual(image.pixelColor(20, 250).red(), 0)

        landscape = np.zeros((540, 960, 3), np.uint8)
        landscape[270:, :, 1] = 200  # Bottom half is green.
        image, _ = preview_image(landscape)
        self.assertEqual((image.width(), image.height()), (480, 270))
        self.assertGreater(image.pixelColor(300, 250).green(), 180)
        self.assertEqual(image.pixelColor(300, 20).green(), 0)

    def test_stream_startup_grace_and_stall_interrupt(self):
        stream = VideoThread('127.0.0.1:16384')
        encoder = MagicMock()
        now = time.monotonic()
        stream.stream, stream.stream_started_at = encoder, now-5
        self.assertFalse(stream.interrupt_if_stale(now))
        self.assertTrue(stream.interrupt_if_stale(now+6))
        self.assertFalse(stream.interrupt_if_stale(now+7))
        encoder.interrupt.assert_called_once_with()

    def test_stall_watchdog_uses_cached_frame_for_static_screen(self):
        stream = VideoThread('127.0.0.1:16384')
        # Same screen in both RGB video and BGR detector cache.
        video = np.full((480, 270, 3), (90, 50, 10), dtype=np.uint8)
        cached = video[:, :, ::-1].copy()
        stream.snapshot_source = lambda: cached
        encoder = MagicMock()
        old = time.monotonic() - 10
        stream.stream = encoder
        stream.last_rgb = video
        stream.last_frame_at = old
        stream.stream_started_at = old
        with patch.object(stream.stopped, 'wait', side_effect=[False, True]), \
             patch('omniflow.ui.ADB') as adb:
            stream._watchdog()
        adb.assert_not_called()
        encoder.interrupt.assert_not_called()
        self.assertGreater(stream.last_frame_at, old)
        self.assertFalse(stream.interrupted)

    def test_stall_watchdog_restarts_if_cached_screen_changed(self):
        stream = VideoThread('127.0.0.1:16384')
        stream.snapshot_source = lambda: np.full((480, 270, 3), 255, np.uint8)
        old = time.monotonic() - 10
        stream.stream = MagicMock()
        stream.last_rgb = np.zeros((480, 270, 3), np.uint8)
        stream.last_frame_at = old
        stream.stream_started_at = old
        messages = []
        stream.error.connect(messages.append)
        with patch.object(stream.stopped, 'wait', side_effect=[False, True]), \
             patch('omniflow.ui.ADB') as adb:
            stream._watchdog()
        adb.assert_not_called()
        stream.stream.interrupt.assert_called_once_with()
        self.assertTrue(stream.interrupted)
        self.assertTrue(any('stalled' in text for text in messages))

    def test_stall_watchdog_uses_adb_if_cached_frame_missing(self):
        stream = VideoThread('127.0.0.1:16384', snapshot_source=lambda: None)
        old = time.monotonic() - 10
        stream.stream = MagicMock()
        stream.last_rgb = np.zeros((480, 270, 3), np.uint8)
        stream.last_frame_at = old
        stream.stream_started_at = old
        with patch.object(stream.stopped, 'wait', side_effect=[False, True]), \
             patch('omniflow.ui.ADB') as adb:
            adb.return_value.screenshot.return_value = np.zeros((480, 270, 3),
                                                                  dtype=np.uint8)
            stream._watchdog()
        adb.assert_called_once_with(serial='127.0.0.1:16384')
        adb.return_value.screenshot.assert_called_once_with(timeout=4)
        stream.stream.interrupt.assert_not_called()

    def test_snapshot_fallback_preserves_device_identity(self):
        stream = VideoThread('127.0.0.1:16384')
        fake = MagicMock()
        fake.screenshot.return_value = np.full((960,540,3), (15,80,120), np.uint8)
        with patch('omniflow.ui.ADB', return_value=fake) as factory:
            stream._fallback_screenshot()
        factory.assert_called_once_with(serial='127.0.0.1:16384')
        fake.screenshot.assert_called_once_with(timeout=4)
        image, kind = stream.take()
        self.assertEqual(kind, 'snapshot')
        self.assertEqual(image.pixelColor(0,0).red(), 120)

    def test_stop_while_encoder_starts_never_enters_blocking_decoder(self):
        """Stop can precede registration of the just-launched ADB process."""
        stream = VideoThread('127.0.0.1:16384')
        encoder = MagicMock()

        def start_after_stop():
            stream.stopped.set()  # Cancellation during blocking __enter__.
            return encoder

        encoder.__enter__.side_effect = start_after_stop
        with patch('omniflow.ui.ADB') as adb, \
             patch('omniflow.ui.AndroidVideoStream', return_value=encoder):
            adb.return_value.size.return_value = (540, 960)
            stream.run()

        encoder.interrupt.assert_called_once_with()
        encoder.frames.assert_not_called()
        encoder.__exit__.assert_called_once()
        self.assertIsNone(stream.stream)
        self.assertTrue(stream.stopped.is_set())

    def test_cache_uses_same_mumu_device_across_localhost_alias(self):
        from types import SimpleNamespace
        local = MagicMock()
        local.serial = 'localhost:16384'
        local.recent_image.return_value = np.full((4, 4, 3), 35, dtype=np.uint8)
        other = MagicMock()
        other.serial = '127.0.0.1:16416'
        other.recent_image.return_value = np.full((4, 4, 3), 99, dtype=np.uint8)
        window = SimpleNamespace(manager=MagicMock())
        window.manager.snapshot.return_value = [other, local]
        actual = OmniWindow._cached_session_frame(window, '127.0.0.1:16384')
        self.assertEqual(actual[0, 0, 0], 35)
        local.recent_image.assert_called_once_with()
        other.recent_image.assert_not_called()
        self.assertIsNone(OmniWindow._cached_session_frame(window, 'localhost:16512'))

    def test_preview_alias_change_does_not_restart_same_encoder(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('omniflow.ui.root_dir', return_value=Path(tmp)), \
             patch('omniflow.ui.DeviceScan.start'):
            window = OmniWindow()
            self.addCleanup(window.close)
            running = MagicMock()
            running.serial = 'localhost:16384'
            running.isRunning.return_value = True
            running.stopped = threading.Event()
            window.video = running
            with patch.object(window, '_selected_serial', return_value='127.0.0.1:16384'):
                window._toggle_video(True)
                window.video_serial = 'localhost:16384'
                running.take.return_value = (None, '')
                running.is_fresh.return_value = True
                window.last_video_serial = 'localhost:16384'
                window._frame()
            running.stop.assert_not_called()
            running.wait.assert_not_called()
            running.take.assert_called_once_with()
            window.video = None  # Keep mock away from closeEvent.

    def test_switch_does_not_block_gui_waiting_for_encoder(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('omniflow.ui.root_dir', return_value=Path(tmp)), \
             patch('omniflow.ui.DeviceScan.start'):
            window = OmniWindow()
            self.addCleanup(window.close)
            running = MagicMock()
            running.serial = '127.0.0.1:16384'
            running.isRunning.return_value = True
            running.stopped = threading.Event()
            window.video = running
            with patch.object(window, '_selected_serial', return_value='127.0.0.1:16416'):
                window._toggle_video(True)
            self.assertEqual(window.requested_video_serial, '127.0.0.1:16416')
            running.stop.assert_called_once_with()
            running.wait.assert_not_called()
            window.video = None  # Do not hand the mock back to closeEvent.


if __name__ == '__main__':
    unittest.main()