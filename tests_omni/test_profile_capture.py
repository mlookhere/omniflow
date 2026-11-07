"""Stages of MuMu screenshot capture are independently measured; no input."""
from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from tools import profile_capture as profiler


class FakeDevice:
    def __init__(self, serial):
        self.serial = serial
        self.calls = []
        good, png = cv2.imencode('.png', np.zeros((960, 540, 3), dtype=np.uint8))
        assert good
        self.png = png.tobytes()

    def ensure_connected(self):
        pass

    def _run(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.png


class BrokenDevice(FakeDevice):
    def _run(self, *args, **kwargs):
        raise OSError('ADB disconnected')


class ProfileTests(unittest.TestCase):
    def test_one_adb_png_capture_is_timed_and_never_touches_the_device(self):
        device = FakeDevice('127.0.0.1:16384')
        sample = profiler.capture_sample(device)
        self.assertEqual(device.calls, [
            (('exec-out', 'screencap', '-p'), {'timeout': 15})])
        self.assertGreater(sample['png_bytes'], 0)
        for key in ('adb_exec_ms', 'png_decode_ms', 'normalize_ms', 'total_ms'):
            self.assertGreaterEqual(sample[key], 0)
        self.assertAlmostEqual(sample['total_ms'], sum(sample[k] for k in
            ('adb_exec_ms', 'decode_ms', 'normalize_ms')), places=3)
        self.assertEqual(sample['decode_ms'], sample['png_decode_ms'])

    def test_healthy_profile_reports_each_stage_and_observed_fps(self):
        result = profiler.profile('127.0.0.1:16384', .08, .02, 0,
                                  device_factory=FakeDevice)
        self.assertTrue(result['passed'], result['failures'])
        self.assertTrue(result['read_only'])
        self.assertGreater(result['frames'], 0)
        self.assertGreater(result['effective_fps'], 0)
        self.assertEqual(set(result['timings_ms']),
                         {'adb_exec_ms', 'decode_ms', 'png_decode_ms',
                          'normalize_ms', 'total_ms'})
        self.assertTrue(all(s['p95'] is not None for s in result['timings_ms'].values()))

    def test_five_consecutive_errors_stop_and_fail_closed(self):
        result = profiler.profile('127.0.0.1:16384', .2, .01, 0,
                                  device_factory=BrokenDevice)
        self.assertFalse(result['passed'])
        self.assertEqual(result['frames'], 0)
        self.assertEqual(result['errors'], 5)
        self.assertIn('ADB disconnected', result['last_error'])

    def test_raw_mode_uses_uncompressed_screencap_without_touching_game(self):
        class RawDevice(FakeDevice):
            def __init__(self, serial):
                super().__init__(serial)
                self.png = struct.pack('<III', 2, 1, 1) + bytes(
                    (10, 20, 30, 255, 40, 50, 60, 255))
        device = RawDevice('127.0.0.1:16384')
        sample = profiler.capture_sample(device, capture_format='raw')
        self.assertEqual(device.calls, [
            (('exec-out', 'screencap'), {'timeout': 15})])
        self.assertEqual(sample['payload_bytes'], 20)
        self.assertEqual(sample['decode_ms'], sample['raw_decode_ms'])
        self.assertNotIn('png_decode_ms', sample)
        report = profiler.profile('127.0.0.1:16384', .08, .02, 0,
                                  capture_format='raw', device_factory=RawDevice)
        self.assertTrue(report['passed'], report['failures'])
        self.assertEqual(report['capture_format'], 'raw')
        self.assertIsNone(report['png_bytes_mean'])
        self.assertGreater(report['payload_bytes_mean'], 0)
        self.assertIn('raw_decode_ms', report['timings_ms'])

    def test_invalid_capture_format_fails_before_any_capture(self):
        device = FakeDevice('127.0.0.1:16384')
        with self.assertRaisesRegex(ValueError, 'Unsupported capture format'):
            profiler.capture_sample(device, capture_format='unknown')
        self.assertEqual(device.calls, [])

    def test_disconnected_device_replaces_stale_success_report(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / 'profile.json'
            profiler.write_json(output, {'passed': True})
            with patch.object(profiler, 'discover_mumu', return_value=[]):
                code = profiler.main(['--serial', '127.0.0.1:16384',
                                      '--output', str(output)])
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(output.read_text())['passed'])

    def test_discovery_exception_replaces_stale_success_report(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / 'profile.json'
            profiler.write_json(output, {'passed': True})
            with patch.object(profiler, 'discover_mumu', side_effect=OSError('offline')):
                code = profiler.main(['--output', str(output)])
            self.assertEqual(code, 1)
            self.assertIn('offline', output.read_text())
            self.assertFalse(json.loads(output.read_text())['passed'])

    def test_fps_failure_is_not_reported_as_pass(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / 'profile.json'
            failed = {'passed': False, 'serial': '127.0.0.1:16384',
                      'effective_fps': .719, 'timings_ms': {},
                      'failures': ['below 0.8 FPS']}
            with patch.object(profiler, 'discover_mumu', return_value=['127.0.0.1:16384']), \
                 patch.object(profiler, 'profile', return_value=failed):
                code = profiler.main(['--seconds', '3', '--output', str(output)])
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(output.read_text())['passed'])


if __name__ == '__main__':
    unittest.main()
