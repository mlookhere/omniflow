"""Android-stream preview regression tests (no desktop window capture)."""
import io
import subprocess
import threading
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from swordx.preview import (AndroidVideoStream, DISPLAY_SIZE, STREAM_SIZES,
                            fit_preview, screenrecord_command)


class AndroidPreviewTests(unittest.TestCase):
    def test_preview_command_is_bound_to_selected_device(self):
        command = screenrecord_command('127.0.0.1:16384', STREAM_SIZES[0], 'adb.exe')
        self.assertEqual(command[:5], ['adb.exe', '-s', '127.0.0.1:16384',
                                        'exec-out', 'screenrecord'])
        self.assertEqual(command[-1], '-')
        self.assertIn('--output-format=h264', command)
        self.assertIn('--size', command)
        self.assertIn('432x768', command)

    def test_two_instances_never_share_device_stream(self):
        one = screenrecord_command('127.0.0.1:16384', STREAM_SIZES[0], 'adb.exe')
        two = screenrecord_command('127.0.0.1:16416', STREAM_SIZES[0], 'adb.exe')
        self.assertNotEqual(one, two)
        self.assertEqual(one[2], '127.0.0.1:16384')
        self.assertEqual(two[2], '127.0.0.1:16416')

    def test_no_unscoped_stream(self):
        with self.assertRaises(ValueError):
            screenrecord_command('', STREAM_SIZES[0])

    def test_encoder_friendly_dimensions(self):
        with self.assertRaises(ValueError):
            screenrecord_command('mumu', (360, 640))

    def test_letterboxing_preserves_rgb_aspect_ratio(self):
        rgb = np.full((500, 500, 3), 111, dtype=np.uint8)
        displayed = fit_preview(rgb)
        self.assertEqual(displayed.shape, (640, 360, 3))
        self.assertEqual(displayed[0, 0, 0], 0)
        self.assertEqual(displayed[320, 180, 0], 111)

    def test_portrait_stream_fills_ui(self):
        rgb = np.full((768, 432, 3), 111, dtype=np.uint8)
        self.assertTrue(np.all(fit_preview(rgb) == 111))

    def test_temporary_process_is_terminated_when_preview_stops(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.stdout = io.BytesIO()
        with patch('swordx.preview.subprocess.Popen', return_value=proc):
            with AndroidVideoStream('127.0.0.1:7555') as stream:
                stream.interrupt()
                proc.kill.assert_called()
        self.assertGreaterEqual(proc.kill.call_count, 1)
        proc.wait.assert_called()

    def test_stream_start_failure_closes_stderr_capture(self):
        with patch('swordx.preview.subprocess.Popen', side_effect=OSError('adb absent')):
            with self.assertRaisesRegex(OSError, 'adb absent'):
                with AndroidVideoStream('127.0.0.1:7555'):
                    pass

    def test_android_error_report_contains_stderr(self):
        proc = MagicMock()
        proc.poll.return_value = 1
        proc.returncode = 1
        proc.stdout = io.BytesIO()
        with patch('swordx.preview.subprocess.Popen', return_value=proc):
            with AndroidVideoStream('127.0.0.1:7555') as stream:
                stream.stderr_file.write(b'ERROR: screenrecord encoder unavailable')
                self.assertIn('encoder unavailable', stream.error())

    def test_all_sources_are_android_device_scoped(self):
        import inspect
        import swordx.preview as module
        source = inspect.getsource(module)
        self.assertNotIn('WindowsGDICapture', source)
        self.assertNotIn('choose_mumu_window', source)
        self.assertNotIn('mss', source)


try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from app import ClickablePreview, EmulatorPreviewThread
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, 'PySide6 is not installed in this environment')
class PreviewQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_clickable_preview_toggles(self):
        preview = ClickablePreview('Preview off — click to enable')
        preview.resize(230, 409)
        clicked = []
        preview.clicked.connect(lambda: clicked.append(True))
        QTest.mouseClick(preview, Qt.LeftButton)
        QTest.mouseClick(preview, Qt.LeftButton)
        self.assertEqual(clicked, [True, True])

    def test_latest_frame_buffer_discards_old_frames(self):
        from PySide6.QtGui import QImage
        viewer = EmulatorPreviewThread('127.0.0.1:16384', threading.Event())
        first = QImage(10, 10, QImage.Format_RGB32)
        second = QImage(20, 20, QImage.Format_RGB32)
        with viewer.lock:
            viewer.latest = first
            viewer.latest = second
        self.assertEqual(viewer.take_latest().width(), 20)
        self.assertIsNone(viewer.take_latest())

    def test_interruption_kills_only_own_device_stream(self):
        one = EmulatorPreviewThread('127.0.0.1:16384', threading.Event())
        two = EmulatorPreviewThread('127.0.0.1:16416', threading.Event())
        one.stream, two.stream = MagicMock(), MagicMock()
        one.interrupt()
        one.stream.interrupt.assert_called_once()
        two.stream.interrupt.assert_not_called()
