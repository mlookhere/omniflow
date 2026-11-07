"""Read-only 10-instance benchmark reliability and failure accounting."""
from __future__ import annotations

import json
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from omniflow.devices import MAX_CAPTURE_AGE_SECONDS, Device as MuMuDevice
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

    def test_builtin_png_benchmark_uses_production_transport_and_deadline(self):
        ok, png = cv2.imencode('.png', np.zeros((960, 540, 3), np.uint8))
        self.assertTrue(ok)
        with patch.object(MuMuDevice, 'ensure_connected'), \
             patch.object(MuMuDevice, '_run',
                          return_value=png.tobytes()) as command:
            result = benchmark.measure('127.0.0.1:16384', .07, .02,
                                       device_factory=MuMuDevice)
        self.assertGreater(result.frames, 0)
        self.assertEqual(result.errors, 0)
        self.assertIsInstance(result.started_at_unix, float)
        self.assertIsInstance(result.ended_at_unix, float)
        self.assertGreater(result.ended_at_unix, result.started_at_unix)
        self.assertAlmostEqual(result.ended_at_unix - result.started_at_unix,
                               result.elapsed_seconds, places=2)
        self.assertEqual(len(result.valid_frame_times_unix), result.frames)
        self.assertTrue(all(result.started_at_unix <= x <= result.ended_at_unix
                            for x in result.valid_frame_times_unix))
        self.assertGreaterEqual(command.call_count, result.frames)
        for call in command.call_args_list:
            self.assertEqual(call.args, ('exec-out', 'screencap', '-p'))
            self.assertEqual(call.kwargs, {'timeout': MAX_CAPTURE_AGE_SECONDS})

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
        success = benchmark.DeviceResult('one', 10, 0, 10.0, 1.0, 10.0, 20.0, None,
                                         max_frame_gap_seconds=1.0)
        failed = benchmark.DeviceResult('two', 2, 1, 10.0, 0.2, 300.0, 500.0, None,
                                        max_frame_gap_seconds=1.0)
        checks = benchmark.evaluate([success, failed], requested=3, min_fps=0.8)
        self.assertEqual(len(checks), 3)
        self.assertTrue(any('Expected 3' in check for check in checks))
        self.assertTrue(any('capture/detector error' in check for check in checks))
        self.assertTrue(any('below' in check for check in checks))

    def test_slow_but_successful_screenshot_reports_rejected_gap(self):
        class Clock:
            def __init__(self):
                self.now = 0.0

            def __call__(self):
                return self.now

            def wait(self, seconds):
                self.now += seconds

        clock = Clock()

        class SlowSecondFrame(FakeDevice):
            def screenshot(self):
                self.calls += 1
                clock.now += 7.0 if self.calls == 2 else .1
                return np.zeros((960, 540, 3), dtype=np.uint8)

        result = benchmark.measure('mumu:test', 9.0, .9,
                                   device_factory=SlowSecondFrame,
                                   clock=clock, wait=clock.wait)
        self.assertGreater(result.max_frame_gap_seconds, 6.0)
        self.assertEqual(result.errors, 0)
        self.assertIn('screenshot gap', ' '.join(
            benchmark.evaluate([result], requested=1, min_fps=0.0)))

    def test_missing_gap_and_high_fps_do_not_pass(self):
        for gap in (None, float('nan'), -1.0, 7.0):
            with self.subTest(gap=gap):
                result = benchmark.DeviceResult(
                    '127.0.0.1:16384', 120, 0, 120.0, 1.0, 100.0, 200.0, None,
                    max_frame_gap_seconds=gap)
                self.assertTrue(benchmark.evaluate([result], 1, .8))
        good = benchmark.DeviceResult(
            '127.0.0.1:16384', 120, 0, 120.0, 1.0, 100.0, 200.0, None,
            max_frame_gap_seconds=2.0)
        self.assertEqual(benchmark.evaluate([good], 1, .8), [])

    def test_requested_capture_duration_is_measured_not_assumed(self):
        incomplete = benchmark.DeviceResult(
            '127.0.0.1:16384', 116, 0, 116.0, 1.0, 100.0, 200.0, None,
            max_frame_gap_seconds=2.0)
        failures = benchmark.evaluate(
            [incomplete], requested=1, min_fps=.8, minimum_seconds=120)
        self.assertIn('required at least 120', ' '.join(failures))
        self.assertEqual(benchmark.evaluate(
            [incomplete], 1, .8, minimum_seconds=116), [])

    def test_png_orientation_change_fails_instead_of_normalizing_it_away(self):
        class RotatingDevice(FakeDevice):
            def screenshot(self):
                self.calls += 1
                return np.zeros((960, 540, 3) if self.calls % 2 else
                                (540, 960, 3), dtype=np.uint8)

        result = benchmark.measure('mumu:test', .10, .01,
                                   device_factory=RotatingDevice)
        self.assertGreater(result.frames, 0)
        self.assertGreater(result.errors, 0)
        self.assertIn('source geometry changed', result.last_error)
        self.assertTrue(benchmark.evaluate([result], 1, 0.0))

    def test_raw_orientation_change_fails_without_png_substitution(self):
        class RotatingRawDevice(FakeDevice):
            def screenshot(self):
                raise AssertionError('Raw mode must never request PNG')

            def _run(self, *args, **kwargs):
                self.calls += 1
                width, height = ((540, 960) if self.calls % 2 else (960, 540))
                return (struct.pack('<III', width, height, 1) +
                        bytes((12, 24, 36, 255)) * (width * height))

        result = benchmark.measure('mumu:test', .10, .01,
                                   device_factory=RotatingRawDevice,
                                   capture_format='raw')
        self.assertGreater(result.frames, 0)
        self.assertGreater(result.errors, 0)
        self.assertIn('source geometry changed', result.last_error)
        self.assertTrue(benchmark.evaluate([result], 1, 0.0))

    def test_raw_format_decodes_read_only_capture_without_png_path(self):
        class RawDevice(FakeDevice):
            def screenshot(self):
                raise AssertionError('PNG screenshot must not run in raw mode')

            def _run(self, *args, **kwargs):
                self.calls += 1
                if args != ('exec-out', 'screencap') or kwargs != {'timeout': 15}:
                    raise AssertionError('Unexpected raw capture command')
                pixels = bytes((12, 24, 36, 255)) * (540 * 960)
                return struct.pack('<III', 540, 960, 1) + pixels

        result = benchmark.measure('mumu:test', .05, .01,
                                   device_factory=RawDevice, capture_format='raw')
        self.assertGreater(result.frames, 0)
        self.assertEqual(result.errors, 0)
        self.assertIsNotNone(result.max_frame_gap_seconds)

    def test_corrupted_raw_capture_is_a_failure_not_valid_frame(self):
        class InvalidRawDevice(FakeDevice):
            def screenshot(self):
                raise AssertionError('PNG cannot substitute for failed raw capture')

            def _run(self, *args, **kwargs):
                self.calls += 1
                return b'bad framebuffer'

        result = benchmark.measure('mumu:test', .05, .01,
                                   device_factory=InvalidRawDevice, capture_format='raw')
        self.assertEqual(result.frames, 0)
        self.assertGreater(result.errors, 0)
        self.assertIn('Raw screencap', result.last_error)

    def test_raw_benchmark_is_labeled_and_not_hardware_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'raw.json'
            observed = []
            def measured(serial, seconds, poll, barrier, **kwargs):
                observed.append(kwargs['capture_format'])
                return benchmark.DeviceResult(
                    serial, 4, 0, 3.0, 1.33, 500.0, 600.0, None,
                    max_frame_gap_seconds=2.0)
            with patch.object(benchmark, 'discover_mumu',
                              return_value=['127.0.0.1:16384']), \
                 patch.object(benchmark, 'measure', side_effect=measured), \
                 patch.object(benchmark, 'HostResourceMonitor') as monitor:
                monitor.return_value.stop.return_value = {'metrics': {}}
                status = benchmark.main(['--instances', '1', '--seconds', '3',
                                         '--capture-format', 'raw',
                                         '--min-fps', '.8', '--output', str(output)])
            report = json.loads(output.read_text())
            self.assertEqual(status, 0)
            self.assertEqual(observed, ['raw'])
            self.assertEqual(report['capture_format'], 'raw')
            self.assertEqual(report['adb_timeout_seconds'], 15.0)
            self.assertFalse(report['hardware_acceptance_complete'])
            self.assertTrue(report['passed'])

    def test_invalid_measure_capture_format_raises_before_any_device_call(self):
        with patch.object(benchmark, 'Device') as factory:
            with self.assertRaisesRegex(ValueError, 'Capture format'):
                benchmark.measure('mumu:test', .1, .05, capture_format='jpeg',
                                  device_factory=factory)
            factory.assert_not_called()

    def test_json_report_atomic_and_parseable(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'results' / 'ten-emulators.json'
            benchmark.write_json(output, {'passed': True, 'devices': [{'serial': 'emulator-1'}]})
            self.assertEqual(json.loads(output.read_text())['devices'][0]['serial'], 'emulator-1')
            self.assertEqual(len(list(output.parent.glob('*.tmp'))), 0)

    def test_main_fails_if_fewer_instances_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'report.json'
            benchmark.write_json(output, {'passed': True, 'devices': [{'serial': 'stale'}]})
            with patch.object(benchmark, 'discover_mumu', return_value=['one']):
                status = benchmark.main(['--instances', '10', '--seconds', '3',
                                         '--output', str(output)])
            self.assertEqual(status, 1)
            report = json.loads(output.read_text())
            self.assertIs(report['passed'], False)
            self.assertEqual(report['requested_instances'], 10)
            self.assertEqual(report['discovered_instances'], 1)
            self.assertEqual(report['tested_instances'], 0)
            self.assertEqual(report['devices'], [])
            self.assertTrue(any('Found 1 of 10' in issue for issue in report['failures']))

    def test_reports_all_discovered_devices_when_only_one_is_benchmarked(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'single.json'
            sample = benchmark.DeviceResult('127.0.0.1:16384', 5, 0, 3.0,
                                            1.67, 300.0, 400.0, None,
                                            max_frame_gap_seconds=1.0)
            with patch.object(benchmark, 'discover_mumu',
                              return_value=['127.0.0.1:16384',
                                            '127.0.0.1:16416',
                                            '127.0.0.1:16416']), \
                 patch.object(benchmark, 'measure', return_value=sample), \
                 patch.object(benchmark, 'HostResourceMonitor') as monitor:
                monitor.return_value.stop.return_value = {'metrics': {}}
                status = benchmark.main(['--instances', '1', '--seconds', '3',
                                         '--output', str(output)])
            report = json.loads(output.read_text())
            self.assertEqual(status, 0)
            self.assertTrue(report['passed'])
            self.assertEqual(report['requested_instances'], 1)
            self.assertEqual(report['adb_timeout_seconds'], MAX_CAPTURE_AGE_SECONDS)
            self.assertEqual(report['discovered_instances'], 2)
            self.assertEqual(report['tested_instances'], 1)
            self.assertEqual(report['devices'][0]['serial'], '127.0.0.1:16384')

    def test_explicit_serial_selects_matching_discovered_device_not_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'selected.json'
            observed = []
            def measure(serial, seconds, poll, barrier, **kwargs):
                observed.append(serial)
                return benchmark.DeviceResult(serial, 5, 0, 3.0, 1.67, 300.0, 400.0, None,
                                              max_frame_gap_seconds=1.0)
            with patch.object(benchmark, 'discover_mumu',
                              return_value=['127.0.0.1:16384', 'localhost:16416',
                                            '127.0.0.1:16416']), \
                 patch.object(benchmark, 'measure', side_effect=measure), \
                 patch.object(benchmark, 'HostResourceMonitor') as monitor:
                monitor.return_value.stop.return_value = {'metrics': {}}
                result = benchmark.main(['--instances', '1', '--seconds', '3',
                                         '--serial', '127.0.0.1:16416',
                                         '--output', str(output)])
            report = json.loads(output.read_text())
            self.assertEqual(result, 0)
            self.assertEqual(observed, ['localhost:16416'])
            self.assertEqual(report['requested_serial'], '127.0.0.1:16416')
            self.assertEqual(report['discovered_instances'], 2)
            self.assertEqual(report['tested_instances'], 1)
            self.assertEqual(report['devices'][0]['serial'], 'localhost:16416')

    def test_missing_requested_serial_fails_without_sampling_substitute(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'missing.json'
            benchmark.write_json(output, {'passed': True})
            with patch.object(benchmark, 'discover_mumu',
                              return_value=['127.0.0.1:16384']), \
                 patch.object(benchmark, 'measure') as measure:
                status = benchmark.main(['--instances', '1', '--seconds', '3',
                                         '--serial', '127.0.0.1:16416',
                                         '--output', str(output)])
            measure.assert_not_called()
            report = json.loads(output.read_text())
            self.assertEqual(status, 1)
            self.assertFalse(report['passed'])
            self.assertEqual(report['discovered_instances'], 1)
            self.assertEqual(report['tested_instances'], 0)
            self.assertIn('no substitutes', ' '.join(report['failures']))

    def test_invalid_requested_serial_overwrites_stale_green_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'invalid.json'
            benchmark.write_json(output, {'passed': True})
            with patch.object(benchmark, 'discover_mumu') as discover:
                status = benchmark.main(['--instances', '1', '--seconds', '3',
                                         '--serial', 'usb:123',
                                         '--output', str(output)])
            discover.assert_not_called()
            self.assertEqual(status, 1)
            self.assertFalse(json.loads(output.read_text())['passed'])

    def test_main_records_discovery_exception_as_failure_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'failures' / 'benchmark.json'
            with patch.object(benchmark, 'discover_mumu', side_effect=OSError('adb offline')):
                status = benchmark.main(['--instances', '10', '--seconds', '3',
                                         '--output', str(output)])
            self.assertEqual(status, 1)
            report = json.loads(output.read_text())
            self.assertFalse(report['passed'])
            self.assertEqual(report['discovered_instances'], 0)
            self.assertEqual(report['tested_instances'], 0)
            self.assertEqual(report['devices'], [])
            self.assertIn('adb offline', ' '.join(report['failures']))
            self.assertFalse(list(output.parent.glob('*.tmp')))

    def test_unhandled_worker_exception_preserves_failure_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'result.json'
            benchmark.write_json(output, {'passed': True, 'devices': [{'serial': 'stale'}]})
            with patch.object(benchmark, 'discover_mumu', return_value=['127.0.0.1:16384']), \
                 patch.object(benchmark, 'measure', side_effect=RuntimeError('worker crashed')):
                result = benchmark.main(['--instances', '1', '--seconds', '3',
                                         '--output', str(output)])
            self.assertEqual(result, 1)
            report = json.loads(output.read_text())
            self.assertFalse(report['passed'])
            self.assertEqual(report['tested_instances'], 1)
            self.assertEqual(report['devices'][0]['frames'], 0)
            self.assertEqual(report['devices'][0]['errors'], 1)
            self.assertIn('worker crashed', report['devices'][0]['last_error'])
            self.assertFalse(list(output.parent.glob('*.tmp')))

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
