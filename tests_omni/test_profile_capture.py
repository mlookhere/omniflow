"""Stages of MuMu screenshot capture are independently measured; no input."""
from __future__ import annotations

import json
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
            ('adb_exec_ms', 'png_decode_ms', 'normalize_ms')), places=3)

    def test_healthy_profile_reports_each_stage_and_observed_fps(self):
        result = profiler.profile('127.0.0.1:16384', .08, .02, 0,
                                  device_factory=FakeDevice)
        self.assertTrue(result['passed'], result['failures'])
        self.assertTrue(result['read_only'])
        self.assertGreater(result['frames'], 0)
        self.assertGreater(result['effective_fps'], 0)
        self.assertEqual(set(result['timings_ms']),
                         {'adb_exec_ms', 'png_decode_ms', 'normalize_ms', 'total_ms'})
        self.assertTrue(all(s['p95'] is not None for s in result['timings_ms'].values()))

    def test_five_consecutive_errors_stop_and_fail_closed(self):
        result = profiler.profile('127.0.0.1:16384', .2, .01, 0,
                                  device_factory=BrokenDevice)
        self.assertFalse(result['passed'])
        self.assertEqual(result['frames'], 0)
        self.assertEqual(result['errors'], 5)
        self.assertIn('ADB disconnected', result['last_error'])

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
