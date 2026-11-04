"""Read-only 10-instance benchmark reliability and failure accounting."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from tools import benchmark_mumu as benchmark


class FakeDevice:
    def __init__(self, serial):
        self.serial = serial
        self.calls = 0

    def ensure_connected(self):
        return None

    def screenshot(self):
        self.calls += 1
        return np.zeros((960, 540, 3), dtype=np.uint8)


class BrokenDevice(FakeDevice):
    def screenshot(self):
        raise OSError('device disconnected')


class ConnectionFailure(FakeDevice):
    def ensure_connected(self):
        raise OSError('device offline')


class Detector:
    def __init__(self):
        self.calls = 0

    def detect(self, frame):
        self.calls += 1
        assert frame.shape == (960, 540, 3)


class BenchmarkTests(unittest.TestCase):
    def test_percentile_uses_nearest_rank_and_empty_is_none(self):
        self.assertIsNone(benchmark.percentile([], 0.95))
        self.assertEqual(benchmark.percentile([1, 3, 5, 7], 0.95), 7)
        self.assertEqual(benchmark.percentile([1, 3, 5, 7], 0.5), 3)

    def test_ten_synchronized_workers_with_optional_real_detection_path(self):
        barrier = threading.Barrier(10)
        detectors = []

        def make_detector():
            detector = Detector()
            detectors.append(detector)
            return detector

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(benchmark.measure, f'mumu:{i}', 0.18, 0.05, barrier,
                                   device_factory=FakeDevice, detector_factory=make_detector)
                       for i in range(10)]
            results = [f.result(timeout=8) for f in futures]
        self.assertEqual(len(results), 10)
        self.assertTrue(all(r.frames >= 1 and r.errors == 0 for r in results))
        self.assertTrue(all(r.detection_p95_ms is not None for r in results))
        self.assertFalse(benchmark.evaluate(results, requested=10, min_fps=0.0))
        self.assertTrue(all(x.calls >= 1 for x in detectors))

    def test_detection_error_is_reported_not_counted_as_valid_frame(self):
        class BrokenDetector:
            def detect(self, frame):
                raise ValueError('invalid OCR rule')

        result = benchmark.measure('mumu:2', 0.06, 0.01, device_factory=FakeDevice,
                                   detector_factory=BrokenDetector)
        self.assertEqual(result.frames, 0)
        self.assertGreater(result.errors, 0)
        self.assertTrue(benchmark.evaluate([result], 1, 0.0))

    def test_connection_failure_does_not_deadlock_healthy_devices(self):
        barrier = threading.Barrier(2)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(benchmark.measure, 'healthy', 0.08, 0.04, barrier,
                            device_factory=FakeDevice)
            b = pool.submit(benchmark.measure, 'disconnected', 0.08, 0.04, barrier,
                            device_factory=ConnectionFailure)
            results = [a.result(timeout=5), b.result(timeout=5)]
        self.assertGreater(results[0].frames, 0)
        self.assertEqual(results[1].frames, 0)
        self.assertEqual(results[1].errors, 1)
        self.assertIn('offline', results[1].last_error)

    def test_failure_detection_for_missing_device_errors_and_minimum_fps(self):
        success = benchmark.DeviceResult('one', 10, 0, 10.0, 1.0, 10.0, 20.0, None)
        failed = benchmark.DeviceResult('two', 2, 1, 10.0, 0.2, 300.0, 500.0, None)
        checks = benchmark.evaluate([success, failed], requested=3, min_fps=0.8)
        self.assertEqual(len(checks), 3)
        self.assertTrue(any('Expected 3' in check for check in checks))
        self.assertTrue(any('capture/detector error' in check for check in checks))
        self.assertTrue(any('below' in check for check in checks))

    def test_json_report_atomic_and_parseable(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'results' / 'ten-emulators.json'
            benchmark.write_json(output, {'passed': True, 'devices': [{'serial': 'emulator-1'}]})
            self.assertEqual(json.loads(output.read_text())['devices'][0]['serial'], 'emulator-1')
            self.assertEqual(len(list(output.parent.glob('*.tmp'))), 0)

    def test_main_fails_if_fewer_instances_found(self):
        with patch.object(benchmark, 'discover_mumu', return_value=['one']):
            self.assertEqual(benchmark.main(['--instances', '10', '--seconds', '3']), 1)

    def test_main_fails_on_device_capture_errors_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'report.json'
            original_measure = benchmark.measure
            def fast_broken_measure(serial, seconds, poll, barrier, **kwargs):
                return original_measure(serial, 0.04, 0.01, barrier,
                                        device_factory=BrokenDevice, **kwargs)
            with patch.object(benchmark, 'discover_mumu', return_value=['bad']), \
                 patch.object(benchmark, 'measure', side_effect=fast_broken_measure):
                status = benchmark.main(['--instances', '1', '--seconds', '3', '--output', str(output)])
            self.assertEqual(status, 1)
            data = json.loads(output.read_text())
            self.assertFalse(data['passed'])
            self.assertGreater(data['devices'][0]['errors'], 0)


if __name__ == '__main__':
    unittest.main()
