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
    from omniflow.ui import VideoThread, OmniWindow
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

    def test_stream_startup_grace_and_stall_interrupt(self):
        stream = VideoThread('127.0.0.1:16384')
        encoder = MagicMock()
        now = time.monotonic()
        stream.stream, stream.stream_started_at = encoder, now-5
        self.assertFalse(stream.interrupt_if_stale(now))
        self.assertTrue(stream.interrupt_if_stale(now+6))
        self.assertFalse(stream.interrupt_if_stale(now+7))
        encoder.interrupt.assert_called_once_with()

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