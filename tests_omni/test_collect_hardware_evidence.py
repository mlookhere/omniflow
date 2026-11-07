"""One-command hardware collection remains read-only and never approves a release."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import collect_hardware_evidence as collector


class CollectEvidenceTests(unittest.TestCase):
    def test_successful_subcommands_are_not_full_hardware_acceptance(self):
        seen = []

        def fake_run(command, **kwargs):
            seen.append((command, kwargs))
            report = Path(command[command.index('--output') + 1])
            report.write_text(json.dumps({'passed': True,
                                          'hardware_acceptance_complete': False}))
            return subprocess.CompletedProcess(command, 0, 'read-only simulated test\n', '')

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(collector.subprocess, 'run', side_effect=fake_run):
            self.assertEqual(collector.main(['--output-root', tmp, '--serial', '127.0.0.1:16384']), 0)
            folders = list(Path(tmp).glob('run-*'))
            self.assertEqual(len(folders), 1)
            manifest = json.loads((folders[0] / 'run-manifest.json').read_text())
            self.assertTrue(manifest['passed_read_only_checks'])
            self.assertFalse(manifest['hardware_acceptance_complete'])
            self.assertTrue(all(x['report_valid'] for x in manifest['steps'].values()))
            self.assertFalse(manifest['branch_protection_verified'])
            self.assertEqual([name for name in manifest['steps']], ['video', 'benchmark', 'verify'])
            self.assertEqual(len(seen), 3)
            self.assertEqual([Path(item[0][1]).name for item in seen],
                             ['diagnose_capture.py', 'benchmark_mumu.py', 'verify_hardware_evidence.py'])
            self.assertTrue(all(item[1]['cwd'] == collector.ROOT for item in seen))
            self.assertTrue(all(item[1].get('check') is False for item in seen))
            self.assertIn('--serial', seen[0][0])
            self.assertNotIn('--allow-snapshot-only', seen[0][0])
            self.assertIn('--instances', seen[1][0])
            self.assertEqual(seen[1][0][seen[1][0].index('--instances') + 1], '1')
            self.assertEqual(seen[2][0][seen[2][0].index('--instances') + 1], '1')
            self.assertFalse(manifest['ten_instance_operation_verified'])
            self.assertEqual(manifest['instances_requested'], 1)
            self.assertTrue(all((folders[0] / (name + '.log')).exists()
                                for name in ('video', 'benchmark', 'verify')))

    def test_explicit_serial_reaches_video_and_benchmark(self):
        seen = []
        def fake_run(command, **kwargs):
            seen.append(command)
            Path(command[command.index('--output') + 1]).write_text(json.dumps({
                'passed': True, 'hardware_acceptance_complete': False}))
            return subprocess.CompletedProcess(command, 0, '', '')
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(collector.subprocess, 'run', side_effect=fake_run):
            self.assertEqual(collector.main(['--serial', '127.0.0.1:16416',
                                             '--output-root', tmp]), 0)
        for command in seen[:2]:
            self.assertEqual(command[command.index('--serial') + 1], '127.0.0.1:16416')

    def test_capture_comparison_collects_static_diagnostics_without_hardware_approval(self):
        seen = []
        def fake_run(command, **kwargs):
            name = Path(command[1]).name
            seen.append(name)
            result = {'passed': True, 'hardware_acceptance_complete': False}
            if name == 'assess_capture.py':
                result.update(comparison_complete=True,
                              raw_candidate_for_further_validation=True)
            Path(command[command.index('--output') + 1]).write_text(json.dumps(result))
            return subprocess.CompletedProcess(command, 0, '', '')
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(collector.subprocess, 'run', side_effect=fake_run):
            status = collector.main(['--capture-comparison',
                                     '--serial', '127.0.0.1:16416',
                                     '--output-root', tmp])
            directory = next(Path(tmp).glob('run-*'))
            manifest = json.loads((directory / 'run-manifest.json').read_text())
            self.assertEqual(status, 0)
            self.assertEqual(seen, ['profile_capture.py', 'profile_capture.py',
                                    'compare_capture.py', 'assess_capture.py'])
            self.assertEqual(list(manifest['steps']), ['profile_png', 'profile_raw',
                                                       'fidelity', 'assess'])
            self.assertTrue(manifest['capture_comparison_complete'])
            self.assertTrue(manifest['raw_candidate_for_further_validation'])
            self.assertFalse(manifest['passed_read_only_checks'])
            self.assertFalse(manifest['hardware_acceptance_complete'])
            self.assertFalse(manifest['ten_instance_operation_verified'])
            self.assertTrue(all((directory / f'{name}.log').is_file() for name in
                                ('profile_png', 'profile_raw', 'fidelity', 'assess')))

    def test_png_below_fps_gate_remains_valid_diagnostic_but_not_hardware_acceptance(self):
        def fake_run(command, **kwargs):
            name = Path(command[1]).name
            result = {'passed': True, 'hardware_acceptance_complete': False}
            code = 0
            if name == 'profile_capture.py' and 'png' in command:
                code = 1
                result = {'passed': False, 'failures': ['Capture 0.719 FPS below 0.800 FPS target']}
            if name == 'assess_capture.py':
                result.update(comparison_complete=True,
                              raw_candidate_for_further_validation=False)
            Path(command[command.index('--output') + 1]).write_text(json.dumps(result))
            return subprocess.CompletedProcess(command, code, '', '')
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(collector.subprocess, 'run', side_effect=fake_run):
            self.assertEqual(collector.main(['--capture-comparison',
                                             '--serial', '127.0.0.1:16416',
                                             '--output-root', tmp]), 0)
            manifest = json.loads((next(Path(tmp).glob('run-*')) /
                                   'run-manifest.json').read_text())
            self.assertFalse(manifest['steps']['profile_png']['passed'])
            self.assertTrue(manifest['capture_comparison_complete'])
            self.assertFalse(manifest['raw_candidate_for_further_validation'])
            self.assertFalse(manifest['passed_read_only_checks'])

    def test_missing_comparison_report_never_approves_diagnostic(self):
        def fake_run(command, **kwargs):
            if Path(command[1]).name != 'compare_capture.py':
                payload = {'passed': True, 'hardware_acceptance_complete': False}
                if Path(command[1]).name == 'assess_capture.py':
                    payload['comparison_complete'] = True
                Path(command[command.index('--output') + 1]).write_text(
                    json.dumps(payload))
            return subprocess.CompletedProcess(command, 0, '', '')
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(collector.subprocess, 'run', side_effect=fake_run):
            self.assertEqual(collector.main(['--capture-comparison',
                                             '--serial', '127.0.0.1:16416',
                                             '--output-root', tmp]), 1)
            manifest = json.loads((next(Path(tmp).glob('run-*')) /
                                   'run-manifest.json').read_text())
            self.assertFalse(manifest['capture_comparison_complete'])
            self.assertFalse(manifest['passed_read_only_checks'])

    def test_comparison_requires_one_named_device(self):
        with patch.object(collector.subprocess, 'run') as run:
            for args in (['--capture-comparison'],
                         ['--capture-comparison', '--serial', '127.0.0.1:16384',
                          '--instances', '10']):
                with self.subTest(args=args), self.assertRaises(SystemExit):
                    collector.main(args)
            run.assert_not_called()

    def test_one_instance_projection_never_approves_ten(self):
        def fake_run(command, **kwargs):
            payload = {'passed': True, 'hardware_acceptance_complete': False}
            if Path(command[1]).name == 'benchmark_mumu.py':
                payload.update(
                    capture_format='png', requested_instances=1, tested_instances=1,
                    duration_seconds=120, min_fps=.8, failures=[],
                    devices=[{'serial': '127.0.0.1:16384', 'frames': 132,
                              'errors': 0, 'elapsed_seconds': 120,
                              'effective_fps': 1.1, 'max_frame_gap_seconds': 1.1,
                              'capture_p95_ms': 600}])
            Path(command[command.index('--output') + 1]).write_text(json.dumps(payload))
            return subprocess.CompletedProcess(command, 0, '', '')
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(collector.subprocess, 'run', side_effect=fake_run):
            self.assertEqual(collector.main(['--output-root', tmp]), 0)
            report = json.loads((next(Path(tmp).glob('run-*')) / 'run-manifest.json').read_text())
            forecast = report['capacity_estimate']
            self.assertEqual(forecast['estimated_aggregate_screenshot_fps_without_contention'], 11)
            self.assertIsNone(forecast['host_resource_projection'])
            self.assertTrue(forecast['linear_assumption_only'])
            self.assertFalse(report['ten_instance_operation_verified'])
            self.assertFalse(report['hardware_acceptance_complete'])

    def test_ten_instance_mode_keeps_real_ten_device_validation(self):
        seen = []
        def fake_run(command, **kwargs):
            seen.append(command)
            Path(command[command.index('--output') + 1]).write_text(
                json.dumps({'passed': True, 'hardware_acceptance_complete': False}))
            return subprocess.CompletedProcess(command, 0, '', '')
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(collector.subprocess, 'run', side_effect=fake_run):
            self.assertEqual(collector.main(['--instances', '10', '--output-root', tmp]), 0)
            report = json.loads((next(Path(tmp).glob('run-*')) / 'run-manifest.json').read_text())
            self.assertEqual(seen[1][seen[1].index('--instances') + 1], '10')
            self.assertEqual(seen[2][seen[2].index('--instances') + 1], '10')
            self.assertFalse(report['ten_instance_operation_verified'])
            self.assertTrue(report['ten_instance_capture_benchmark_passed'])
            self.assertIsNone(report['capacity_estimate'])
            self.assertFalse(report['hardware_acceptance_complete'])

    def test_projection_rejects_invalid_one_device_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'report.json'
            for sample in ({'passed': False, 'tested_instances': 1,
                            'devices': [{'effective_fps': 1}]},
                           {'passed': True, 'tested_instances': 1,
                            'devices': [{'effective_fps': float('nan')}]},
                           {'passed': True, 'tested_instances': 0,
                            'devices': [{'effective_fps': 1}]}):
                with self.subTest(sample=sample):
                    path.write_text(json.dumps(sample))
                    self.assertIsNone(collector.estimate_ten_instance_capture(path))

    def test_projection_requires_complete_measured_png_evidence(self):
        good = {
            'passed': True, 'hardware_acceptance_complete': False,
            'capture_format': 'png', 'requested_instances': 1,
            'tested_instances': 1, 'duration_seconds': 120, 'min_fps': .8,
            'failures': [],
            'devices': [{
                'serial': '127.0.0.1:16384', 'frames': 132, 'errors': 0,
                'elapsed_seconds': 120, 'effective_fps': 1.1,
                'max_frame_gap_seconds': 1.1, 'capture_p95_ms': 600,
            }],
        }
        cases = {
            'raw capture': ('capture_format', 'raw'),
            'missing duration': ('duration_seconds', None),
            'short run': ('duration_seconds', 30),
            'relaxed FPS gate': ('min_fps', 0),
            'different requested coverage': ('requested_instances', 10),
            'inconsistent tested count': ('tested_instances', 10),
            'claimed full acceptance': ('hardware_acceptance_complete', True),
            'unreported failures': ('failures', ['capture failed']),
            'missing devices': ('devices', []),
        }
        device_cases = {
            'short observation': ('elapsed_seconds', 30),
            'wrong frame count': ('frames', 1),
            'fractional frame count': ('frames', 132.5),
            'capture errors': ('errors', 1),
            'missing serial': ('serial', ''),
            'capture stall': ('max_frame_gap_seconds', 7),
            'nonfinite gap': ('max_frame_gap_seconds', float('nan')),
            'missing capture timing': ('capture_p95_ms', None),
            'subthreshold rate': ('effective_fps', .719),
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'benchmark.json'
            path.write_text(json.dumps(good))
            estimate = collector.estimate_ten_instance_capture(path)
            self.assertEqual(estimate['estimated_aggregate_screenshot_fps_without_contention'], 11)
            self.assertFalse(estimate['ten_instance_operation_verified'])
            for label, (field, value) in cases.items():
                with self.subTest(case=label):
                    report = json.loads(json.dumps(good))
                    report[field] = value
                    path.write_text(json.dumps(report))
                    self.assertIsNone(collector.estimate_ten_instance_capture(path))
            for label, (field, value) in device_cases.items():
                with self.subTest(case=label):
                    report = json.loads(json.dumps(good))
                    report['devices'][0][field] = value
                    path.write_text(json.dumps(report))
                    self.assertIsNone(collector.estimate_ten_instance_capture(path))

    def test_missing_verifier_report_never_looks_green(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(collector.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            self.assertEqual(collector.main(['--output-root', tmp]), 1)
            folder = next(Path(tmp).glob('run-*'))
            report = json.loads((folder / 'run-manifest.json').read_text())
            self.assertFalse(report['passed_read_only_checks'])
            self.assertTrue(all(not step['report_valid'] for step in report['steps'].values()))

    def test_multiple_runs_keep_evidence_separate(self):
        def fake_run(command, **kwargs):
            Path(command[command.index('--output') + 1]).write_text(
                json.dumps({'passed': True, 'hardware_acceptance_complete': False}))
            return subprocess.CompletedProcess(command, 0, '', '')

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(collector.subprocess, 'run', side_effect=fake_run):
            self.assertEqual(collector.main(['--output-root', tmp]), 0)
            self.assertEqual(collector.main(['--output-root', tmp]), 0)
            self.assertEqual(len(list(Path(tmp).glob('run-*'))), 2)

    def test_failures_continue_collection_and_preserve_nonzero_result(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(collector.subprocess, 'run', side_effect=[
                 subprocess.CompletedProcess([], 1, '', 'no video\n'),
                 subprocess.CompletedProcess([], 0, '', ''),
                 subprocess.CompletedProcess([], 1, '', 'rejected\n'),
             ]):
            self.assertEqual(collector.main(['--output-root', tmp]), 1)
            folder = next(Path(tmp).glob('run-*'))
            manifest = json.loads((folder / 'run-manifest.json').read_text())
            self.assertFalse(manifest['passed_read_only_checks'])
            self.assertEqual(manifest['steps']['video']['exit_code'], 1)
            self.assertEqual(manifest['steps']['benchmark']['exit_code'], 0)
            self.assertEqual(manifest['steps']['verify']['exit_code'], 1)
            self.assertIn('no video', (folder / 'video.log').read_text())

    def test_timeout_is_failed_and_next_diagnostics_still_run(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(collector.subprocess, 'run', side_effect=[
                 subprocess.TimeoutExpired('video', 175, output=b'trace'),
                 subprocess.CompletedProcess([], 0, '', ''),
                 subprocess.CompletedProcess([], 1, '', ''),
             ]):
            self.assertEqual(collector.main(['--output-root', tmp]), 1)
            folder = next(Path(tmp).glob('run-*'))
            report = json.loads((folder / 'run-manifest.json').read_text())
            self.assertIsNone(report['steps']['video']['exit_code'])
            self.assertFalse(report['passed_read_only_checks'])
            self.assertIn('timed out', (folder / 'video.log').read_text())


if __name__ == '__main__':
    unittest.main()
