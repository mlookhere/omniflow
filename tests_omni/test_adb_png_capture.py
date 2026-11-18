"""ADB screenshot transport failures must be recoverable, never authorize stale touches."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from omniflow.engine import MAX_CAPTURE_AGE_SECONDS, Match, OmniRunner
from omniflow.devices import Device as MuMuDevice
from swordx.adb import ADB, ADBError
from swordx.workflows import create_workflow, record_step


class ScreenshotTransportTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((960, 540, 3), dtype=np.uint8)
        self.frame[700:830, 150:440] = (100, 160, 210)
        cv2.putText(self.frame, 'MATCH', (185, 770),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.5, (250, 250, 250), 4)
        success, encoded = cv2.imencode('.png', self.frame)
        self.assertTrue(success)
        self.png = encoded.tobytes()
        self.adb = ADB(serial='127.0.0.1:16384')

    def test_valid_png_uses_only_requested_device_and_returns_bgr(self):
        with patch.object(self.adb, '_run', return_value=self.png) as execute:
            screenshot = self.adb.screenshot(timeout=4)
        execute.assert_called_once_with('exec-out', 'screencap', '-p', timeout=4)
        np.testing.assert_array_equal(screenshot, self.frame)

    def test_builtin_device_plain_screenshot_uses_same_bounded_png_command(self):
        device = MuMuDevice(serial='127.0.0.1:16384')
        with patch.object(device, '_run', return_value=self.png) as execute:
            screenshot = device.screenshot()
        execute.assert_called_once_with(
            'exec-out', 'screencap', '-p', timeout=MAX_CAPTURE_AGE_SECONDS)
        np.testing.assert_array_equal(screenshot, self.frame)
        with patch.object(device, '_run', return_value=self.png) as execute:
            screenshot = device.screenshot(timeout=4)
        execute.assert_called_once_with(
            'exec-out', 'screencap', '-p', timeout=4)
        np.testing.assert_array_equal(screenshot, self.frame)

    def test_production_timed_png_splits_adb_and_decode_without_second_capture(self):
        device = MuMuDevice(serial='127.0.0.1:16384')
        with patch.object(device, '_run', return_value=self.png) as command, \
             patch('omniflow.devices.monotonic',
                   side_effect=[10.0, 11.2, 11.26]):
            pixels, timing = device.screenshot_timed(timeout=4)
        np.testing.assert_array_equal(pixels, self.frame)
        command.assert_called_once_with('exec-out', 'screencap', '-p', timeout=4)
        self.assertAlmostEqual(timing['adb_exec_ms'], 1200)
        self.assertAlmostEqual(timing['png_decode_ms'], 60)

    def test_production_timed_png_does_not_credit_failed_decode(self):
        device = MuMuDevice(serial='127.0.0.1:16384')
        with patch.object(device, '_run', return_value=self.png[:25]) as command:
            with self.assertRaises(ADBError):
                device.screenshot_timed()
        command.assert_called_once_with('exec-out', 'screencap', '-p',
                                        timeout=MAX_CAPTURE_AGE_SECONDS)

    def test_runner_screenshot_timeout_rearms_before_next_touch(self):
        # Production PNG stalls must stop at the input-freshness deadline
        # instead of blocking for the generic ADB 15-second timeout.
        self.assertEqual(MAX_CAPTURE_AGE_SECONDS, 6.0)
        with tempfile.TemporaryDirectory() as temp:
            folder = create_workflow('Screenshot timeout recovery', Path(temp))
            key = record_step(folder, 'Match', self.frame,
                              [(150, 700, 440, 830)], taps=[(290, 770)])
            device = MuMuDevice(serial='127.0.0.1:16384')
            runner = OmniRunner(device, folder, 'com.example.game')
            good = subprocess.CompletedProcess(
                args=['adb'], returncode=0, stdout=self.png, stderr=b'')
            stalled = subprocess.TimeoutExpired(
                cmd=['adb', 'exec-out', 'screencap', '-p'],
                timeout=MAX_CAPTURE_AGE_SECONDS)
            with patch('swordx.adb.subprocess.run',
                       side_effect=[good, good, stalled, good, good]) as command, \
                 patch.object(device, 'tap') as touch, \
                 patch.object(runner.detector, 'detect',
                              return_value=Match(key, 'Match', 1.0)):
                runner.step(100)
                runner.step(101)
                self.assertEqual(touch.call_count, 1)
                with self.assertRaises(ADBError):
                    runner.step(102)
                self.assertIsNone(runner.last_frame)
                self.assertIsNone(runner.last_capture_latency_ms)
                self.assertIsNone(runner.last_capture_stages_ms)
                self.assertEqual(runner._streak, 0)
                runner.step(103)
                self.assertEqual(touch.call_count, 1)
                runner.step(104)
                self.assertEqual(touch.call_count, 2)
            self.assertEqual(command.call_count, 5)
            for call in command.call_args_list:
                self.assertEqual(call.kwargs['timeout'],
                                 MAX_CAPTURE_AGE_SECONDS)
                self.assertEqual(call.args[0][-3:],
                                 ['exec-out', 'screencap', '-p'])

    def test_empty_diagnostics_and_truncated_png_fail_closed(self):
        for payload in (b'', b'error: device offline', self.png[:25]):
            with self.subTest(size=len(payload)), patch.object(self.adb, '_run',
                                                               return_value=payload):
                with self.assertRaises(ADBError):
                    self.adb.screenshot()

    def test_opencv_decode_exception_is_adb_error(self):
        with patch.object(self.adb, '_run', return_value=self.png), \
             patch('swordx.adb.cv2.imdecode', side_effect=cv2.error('bad image')):
            with self.assertRaisesRegex(ADBError, 'decode failed'):
                self.adb.screenshot()

    def test_invalid_decoded_frame_is_not_returned(self):
        for frame in (None, np.zeros((10, 10), np.uint8),
                      np.zeros((10, 10, 3), np.float32)):
            with self.subTest(frame_type=type(frame).__name__), \
                 patch.object(self.adb, '_run', return_value=self.png), \
                 patch('swordx.adb.cv2.imdecode', return_value=frame):
                with self.assertRaisesRegex(ADBError, 'unreadable'):
                    self.adb.screenshot()

    def test_corrupt_png_resets_pending_chain_until_two_fresh_frames(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = create_workflow('Capture recovery', Path(temp))
            key = record_step(folder, 'Match', self.frame, [(150, 700, 440, 830)],
                              taps=[(290, 770)])

            class Transport(ADB):
                def __init__(self, packets):
                    super().__init__(serial='127.0.0.1:16384')
                    self.packets = iter(packets)
                    self.taps = []

                def _run(self, *args, **kwargs):
                    return next(self.packets)

                def tap(self, x, y):
                    self.taps.append((x, y))

            device = Transport([self.png, self.png, b'corrupted', self.png, self.png])
            runner = OmniRunner(device, folder, 'com.example.game')
            detected = Match(key, 'Match', 1.0)
            with patch.object(runner.detector, 'detect', return_value=detected):
                runner.step(100)
                runner.step(101)
                self.assertEqual(len(device.taps), 1)
                with self.assertRaises(ADBError):
                    runner.step(102)
                self.assertEqual(runner.current, '')
                self.assertEqual(runner._streak, 0)
                self.assertIsNone(runner.last_frame)
                runner.step(103)
                self.assertEqual(len(device.taps), 1)
                runner.step(104)
                self.assertEqual(len(device.taps), 2)
