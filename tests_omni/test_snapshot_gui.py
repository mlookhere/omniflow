"""H.264 failure uses selected-device screenshots without freezing the Qt worker."""
import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication
    from omniflow.ui import VideoThread
except ImportError:
    VideoThread = None


@unittest.skipIf(VideoThread is None, 'PySide6 unavailable')
class SnapshotGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_missing_decoder_still_produces_selected_device_snapshots(self):
        worker = VideoThread('127.0.0.1:16384')
        calls = []
        def screenshot():
            calls.append(worker.serial)
            worker.stop()
        with patch.dict(sys.modules, {'av': None}), \
             patch.object(worker, '_fallback_screenshot', side_effect=screenshot):
            worker.run()  # Test synchronously; avoid an unnecessary Qt thread.
        self.assertTrue(worker.decoder_unavailable)
        self.assertEqual(calls, ['127.0.0.1:16384'])
        self.assertTrue(worker.stopped.is_set())

    def test_error_22_switches_to_snapshot_fallback(self):
        import errno
        worker = VideoThread('127.0.0.1:16384')
        messages = []
        worker.error.connect(messages.append)
        with patch('omniflow.ui.ADB') as adb, \
             patch('omniflow.ui.AndroidVideoStream.__enter__',
                   side_effect=OSError(errno.EINVAL, 'Invalid argument')), \
             patch.object(worker, '_fallback_screenshot', side_effect=worker.stop) as fallback:
            adb.return_value.size.return_value = (540, 960)
            worker.run()
        fallback.assert_called_once_with()
        self.assertTrue(worker.stopped.is_set())
        self.assertTrue(any('error 22' in message for message in messages))

    def test_fresh_worker_snapshot_replaces_redundant_adb_fallback(self):
        import numpy as np
        frame = np.full((480, 270, 3), (15, 25, 35), dtype=np.uint8)
        worker = VideoThread('127.0.0.1:16384', snapshot_source=lambda: frame)
        with patch('omniflow.ui.ADB') as adb, \
             patch.object(worker, '_publish') as publish:
            worker._fallback_screenshot()
        adb.assert_not_called()
        self.assertEqual(publish.call_args.args[1], 'snapshot')
        self.assertEqual(publish.call_args.args[0][0, 0].tolist(), [35, 25, 15])

    def test_cache_miss_still_uses_selected_device_adb_snapshot(self):
        import numpy as np
        worker = VideoThread('127.0.0.1:16384', snapshot_source=lambda: None)
        with patch('omniflow.ui.ADB') as adb, \
             patch.object(worker, '_publish') as publish:
            adb.return_value.screenshot.return_value = np.zeros((960, 540, 3),
                                                                  dtype=np.uint8)
            worker._fallback_screenshot()
        adb.assert_called_once_with(serial='127.0.0.1:16384')
        adb.return_value.screenshot.assert_called_once_with(timeout=4)
        self.assertEqual(publish.call_args.args[1], 'snapshot')

    def test_stopped_preview_does_not_read_cache_or_adb(self):
        worker = VideoThread('127.0.0.1:16384',
                             snapshot_source=lambda: self.fail('cache read after Stop'))
        worker.stop()
        with patch('omniflow.ui.ADB') as adb:
            worker._fallback_screenshot()
        adb.assert_not_called()

    def test_repeated_identical_snapshot_failures_are_rate_limited(self):
        worker = VideoThread('127.0.0.1:16384')
        messages = []
        worker.error.connect(messages.append)
        from swordx.adb import ADBError
        with patch('omniflow.ui.ADB') as adb, \
             patch('omniflow.ui.time.monotonic', side_effect=[10.0, 12.0, 26.0]):
            adb.return_value.screenshot.side_effect = ADBError('device offline')
            for _ in range(3):
                worker._fallback_screenshot()
        self.assertEqual(len(messages), 2)
        self.assertTrue(all('device offline' in message for message in messages))


if __name__ == '__main__':
    unittest.main()