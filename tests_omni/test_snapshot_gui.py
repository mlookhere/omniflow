"""H.264 failure uses selected-device screenshots without freezing the Qt worker."""
import os
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
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

    def test_watchdog_and_retry_share_one_inflight_adb_screenshot(self):
        import numpy as np
        worker = VideoThread('127.0.0.1:16384', snapshot_source=lambda: None)
        captured = threading.Event()
        release = threading.Event()
        image = np.full((960, 540, 3), 73, np.uint8)

        def slow_screenshot(*, timeout):
            self.assertEqual(timeout, 4)
            captured.set()
            if not release.wait(3):
                raise AssertionError('The screenshot was never released')
            return image

        with patch('omniflow.ui.ADB') as adb, ThreadPoolExecutor(max_workers=2) as pool:
            adb.return_value.screenshot.side_effect = slow_screenshot
            first = pool.submit(worker._read_selected_snapshot)
            self.assertTrue(captured.wait(2))
            second = pool.submit(worker._read_selected_snapshot)
            release.set()
            first_frame, second_frame = first.result(timeout=5), second.result(timeout=5)
        adb.assert_called_once_with(serial='127.0.0.1:16384')
        adb.return_value.screenshot.assert_called_once_with(timeout=4)
        np.testing.assert_array_equal(first_frame, second_frame)
        self.assertIsNot(first_frame, second_frame)

    def test_snapshot_cache_uses_acquisition_time_and_expires(self):
        import numpy as np
        worker = VideoThread('127.0.0.1:16384', snapshot_source=lambda: None)
        with patch('omniflow.ui.ADB') as adb, \
             patch('omniflow.ui.time.monotonic',
                   side_effect=[100.0, 101.0, 102.2]):
            adb.return_value.screenshot.side_effect = [
                np.full((8, 8, 3), 10, np.uint8),
                np.full((8, 8, 3), 20, np.uint8),
            ]
            first = worker._read_selected_snapshot()
            first[:] = 99
            reused = worker._read_selected_snapshot()
            updated = worker._read_selected_snapshot()
        self.assertEqual(reused[0, 0, 0], 10)
        self.assertEqual(updated[0, 0, 0], 20)
        self.assertEqual(adb.return_value.screenshot.call_count, 2)

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
             patch('omniflow.ui.time.monotonic',
                   side_effect=[10.0, 10.0, 12.0, 12.0, 26.0, 26.0]):
            adb.return_value.screenshot.side_effect = ADBError('device offline')
            for _ in range(3):
                worker._fallback_screenshot()
        self.assertEqual(len(messages), 2)
        self.assertTrue(all('device offline' in message for message in messages))


if __name__ == '__main__':
    unittest.main()