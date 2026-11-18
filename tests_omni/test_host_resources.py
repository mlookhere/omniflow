"""Read-only benchmark telemetry must never invent absent GPU observations."""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from omniflow.resources import HostResourceMonitor, parse_nvidia_gpu, sample_host_resources
from tools import benchmark_mumu as benchmark


class HostResourceTests(unittest.TestCase):
    def test_nvidia_parser_aggregates_multiple_gpus_without_per_app_claims(self):
        values = parse_nvidia_gpu('40, 1200\n95, 3200\n')
        self.assertEqual(values, {'gpu_utilization_percent': 95.0,
                                  'gpu_memory_used_mib': 4400.0, 'gpu_count': 2})
        for bad in ('N/A, 1000', '20, nan', '-1, 400', '50, 20, 9', ''):
            with self.subTest(raw=bad), self.assertRaises(ValueError):
                parse_nvidia_gpu(bad)

    def test_gpu_missing_is_marked_unavailable_not_zero(self):
        with patch('omniflow.resources.shutil.which', return_value=None):
            sample, errors = sample_host_resources()
        self.assertNotIn('gpu_utilization_percent', sample)
        self.assertTrue(any('nvidia-smi not found' in error for error in errors))
        self.assertIn('cpu_percent', sample)

    def test_gpu_subprocess_is_read_only_and_time_limited(self):
        with patch('omniflow.resources.subprocess.run') as run:
            run.return_value = MagicMock(stdout='30, 2200\n')
            sample, errors = sample_host_resources(gpu_command='nvidia-smi')
        self.assertFalse(errors)
        self.assertEqual(sample['gpu_memory_used_mib'], 2200)
        args, kwargs = run.call_args
        self.assertEqual(args[0][:2], ['nvidia-smi', '--query-gpu=utilization.gpu,memory.used'])
        self.assertEqual(kwargs['timeout'], 2)
        self.assertTrue(kwargs['check'])

    def test_monitor_collects_and_stops_with_compact_summary(self):
        def probe():
            return ({'cpu_percent': 25.0, 'ram_percent': 45.0,
                     'ram_used_mib': 4000.0, 'gpu_utilization_percent': 60.0,
                     'gpu_memory_used_mib': 2000.0, 'gpu_count': 1}, [])
        monitor = HostResourceMonitor(interval_seconds=0.01, probe=probe)
        monitor.start()
        time.sleep(0.065)
        result = monitor.stop()
        self.assertGreaterEqual(result['sample_count'], 2)
        self.assertEqual(result['metrics']['gpu_utilization_percent']['peak'], 60)
        self.assertEqual(result['metrics']['cpu_percent']['samples'], result['sample_count'])
        self.assertFalse(result['errors'])

    def test_probe_errors_are_reported_without_stopping_captures(self):
        def fail():
            raise OSError('gpu query failed')
        monitor = HostResourceMonitor(interval_seconds=0.01, probe=fail)
        monitor.start()
        time.sleep(0.04)
        data = monitor.stop()
        self.assertEqual(data['metrics']['gpu_utilization_percent']['samples'], 0)
        self.assertTrue(any('gpu query failed' in msg for msg in data['errors']))

    def test_telemetry_start_failure_writes_failure_over_stale_success(self):
        class StartFailureMonitor:
            def start(self):
                raise OSError('host sampler cannot start')

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'result.json'
            benchmark.write_json(path, {'passed': True, 'devices': [{'serial': 'stale'}]})
            with patch.object(benchmark, 'discover_mumu', return_value=['127.0.0.1:16384']), \
                 patch.object(benchmark, 'HostResourceMonitor', return_value=StartFailureMonitor()), \
                 patch.object(benchmark, 'measure') as measure:
                code = benchmark.main(['--instances', '1', '--seconds', '3', '--output', str(path)])
            self.assertEqual(code, 1)
            measure.assert_not_called()
            data = json.loads(path.read_text())
            self.assertFalse(data['passed'])
            self.assertEqual(data['tested_instances'], 0)
            self.assertIsNone(data['host_resources'])
            self.assertIn('host sampler cannot start', ' '.join(data['failures']))

    def test_telemetry_stop_failure_preserves_capture_but_fails_report(self):
        class StopFailureMonitor:
            def start(self):
                pass
            def stop(self):
                raise RuntimeError('GPU sampler shutdown failed')

        serial = '127.0.0.1:16384'
        sample = benchmark.DeviceResult(serial, 3, 0, 3.0, 1.0, 20.0, 30.0, None)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'result.json'
            with patch.object(benchmark, 'discover_mumu', return_value=[serial]), \
                 patch.object(benchmark, 'HostResourceMonitor', return_value=StopFailureMonitor()), \
                 patch.object(benchmark, 'measure', return_value=sample):
                code = benchmark.main(['--instances', '1', '--seconds', '3', '--output', str(path)])
            self.assertEqual(code, 1)
            data = json.loads(path.read_text())
            self.assertFalse(data['passed'])
            self.assertEqual(data['tested_instances'], 1)
            self.assertEqual(data['devices'][0]['frames'], 3)
            self.assertIsNone(data['host_resources'])
            self.assertIn('GPU sampler shutdown failed', ' '.join(data['failures']))

    def test_benchmark_records_host_resources_without_affecting_capture_success(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / 'result.json'
            telemetry = {'duration_seconds': 3.0, 'interval_seconds': 2.0,
                         'sample_count': 0, 'metrics': {},
                         'errors': ['GPU unavailable: no NVIDIA card']}
            class FakeMonitor:
                def start(self):
                    pass
                def stop(self):
                    return telemetry
            result = benchmark.DeviceResult('127.0.0.1:16384', 3, 0, 3.0,
                                            1.0, 30.0, 40.0, None,
                                            max_frame_gap_seconds=1.0)
            with patch.object(benchmark, 'discover_mumu', return_value=[result.serial]), \
                 patch.object(benchmark, 'HostResourceMonitor', return_value=FakeMonitor()), \
                 patch.object(benchmark, 'measure', return_value=result):
                code = benchmark.main(['--instances', '1', '--seconds', '3', '--output', str(report)])
            self.assertEqual(code, 0)
            data = json.loads(report.read_text())
            self.assertTrue(data['passed'])  # Capture passed, but GPU is explicitly missing.
            self.assertEqual(data['host_resources'], telemetry)


if __name__ == '__main__':
    unittest.main()
