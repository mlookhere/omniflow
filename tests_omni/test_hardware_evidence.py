"""Hardware acceptance must not be inferred from a green CI or report flag."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from omniflow.hardware_evidence import EvidenceCriteria, evaluate_hardware_evidence
from tools.verify_hardware_evidence import main


def samples(*, devices=10, workflow='MyWorkflow'):
    video = {
        'device': 'localhost:16384', 'passed': True,
        'elapsed_seconds': 120.02, 'displayed_video_frames': 3000,
        'displayed_video_fps': 25.0, 'fallback_snapshots': 2,
        'max_preview_gap_seconds': 1.1, 'max_video_gap_seconds': 3.4,
        'error22_events': 2, 'error22_windows': 1,
        'error22_recovered': 1, 'error22_unresolved': 0,
        'max_error22_recovery_seconds': 2.8,
    }
    benchmark = {
        'passed': True, 'requested_instances': devices,
        'tested_instances': devices, 'duration_seconds': 120,
        'capture_format': 'png', 'adb_timeout_seconds': 6.0,
        'workflow': workflow,
        'devices': [{
            'serial': f'127.0.0.1:{16384 + i * 32}',
            'frames': 120, 'errors': 0, 'elapsed_seconds': 120.0,
            'effective_fps': 1.0, 'capture_p95_ms': 180,
            'max_frame_gap_seconds': 1.8,
            'detection_p95_ms': 40 if workflow else None,
        } for i in range(devices)],
    }
    return video, benchmark


class HardwareEvidenceTests(unittest.TestCase):
    def test_valid_read_only_evidence_is_not_live_game_approval(self):
        video, bench = samples()
        result = evaluate_hardware_evidence(video, bench)
        self.assertTrue(result['passed'], result['failures'])
        self.assertTrue(result['error22_recovery_observed'])
        self.assertTrue(result['workflow_recognition_measured'])
        self.assertFalse(result['hardware_acceptance_complete'])
        self.assertTrue(any('GPU' in x for x in result['outstanding']))

    def test_single_device_failure_uses_actual_requested_count(self):
        video, bench = samples(devices=1)
        bench['passed'] = False
        result = evaluate_hardware_evidence(video, bench, EvidenceCriteria(instances=1))
        self.assertIn('1-instance capture benchmark did not report success', result['failures'])
        self.assertFalse(result['hardware_acceptance_complete'])

    def test_snapshot_only_must_fail_even_with_source_pass_flag(self):
        video, bench = samples()
        video.update(displayed_video_frames=0, displayed_video_fps=0, fallback_snapshots=100)
        self.assertFalse(evaluate_hardware_evidence(video, bench)['passed'])

    def test_tenth_emulator_missing_or_repeated_alias_fails(self):
        video, bench = samples()
        bench['devices'].pop()
        self.assertFalse(evaluate_hardware_evidence(video, bench)['passed'])
        video, bench = samples()
        bench['devices'][1]['serial'] = 'localhost:16384'
        self.assertIn('duplicate', ' '.join(evaluate_hardware_evidence(video, bench)['failures']))

    def test_raw_capture_benchmark_cannot_be_promoted_to_hardware_acceptance(self):
        video, bench = samples()
        bench['capture_format'] = 'raw'
        bench['passed'] = True
        result = evaluate_hardware_evidence(video, bench)
        self.assertFalse(result['passed'])
        self.assertFalse(result['hardware_acceptance_complete'])
        self.assertIn('production PNG', ' '.join(result['failures']))

    def test_png_screenshot_timeout_provenance_must_be_bounded(self):
        for timeout in (None, 0, -1.0, 6.01, 15.0, float('nan'), True):
            with self.subTest(timeout=timeout):
                video, bench = samples()
                if timeout is None:
                    bench.pop('adb_timeout_seconds')
                else:
                    bench['adb_timeout_seconds'] = timeout
                failures = evaluate_hardware_evidence(video, bench)['failures']
                self.assertTrue(any('timeout' in issue for issue in failures),
                                failures)

    def test_missing_capture_format_provenance_fails_closed(self):
        video, bench = samples()
        bench.pop('capture_format')
        result = evaluate_hardware_evidence(video, bench)
        self.assertFalse(result['passed'])
        self.assertIn('production PNG', ' '.join(result['failures']))

    def test_capture_blackout_fails_despite_good_average_fps(self):
        video, bench = samples()
        bench['devices'][2]['max_frame_gap_seconds'] = 9.0
        bench['passed'] = True  # A forged top-level pass cannot bypass evidence checks.
        result = evaluate_hardware_evidence(video, bench)
        self.assertFalse(result['passed'])
        self.assertIn('screenshot gap', ' '.join(result['failures']))

    def test_missing_capture_gap_is_not_interpreted_as_zero(self):
        video, bench = samples()
        del bench['devices'][3]['max_frame_gap_seconds']
        result = evaluate_hardware_evidence(video, bench)
        self.assertFalse(result['passed'])
        self.assertIn('max_frame_gap_seconds', ' '.join(result['failures']))

    def test_early_shutdown_cannot_pass_a_requested_long_benchmark(self):
        video, bench = samples()
        bench['devices'][3].update(elapsed_seconds=3, frames=3, effective_fps=1)
        self.assertIn('measured only', ' '.join(evaluate_hardware_evidence(video, bench)['failures']))

    def test_unreconciled_error22_windows_fail_even_with_pass_flag(self):
        video, bench = samples()
        video.update(error22_windows=2, error22_recovered=1, error22_unresolved=0)
        self.assertIn('not fully reconciled',
                      ' '.join(evaluate_hardware_evidence(video, bench)['failures']))
        video.update(error22_windows=1, error22_recovered=1, error22_unresolved=0)
        self.assertTrue(evaluate_hardware_evidence(video, bench)['passed'])

    def test_missing_or_invalid_window_counters_fail_closed(self):
        video, bench = samples()
        video.pop('error22_windows')
        self.assertFalse(evaluate_hardware_evidence(video, bench)['passed'])
        video['error22_windows'] = 1.5
        self.assertIn('whole numbers',
                      ' '.join(evaluate_hardware_evidence(video, bench)['failures']))
        video['error22_windows'] = 3
        self.assertIn('exceed logged',
                      ' '.join(evaluate_hardware_evidence(video, bench)['failures']))

    def test_fractional_counts_never_pass_hardware_evidence(self):
        for report, key in (
            ('video', 'displayed_video_frames'),
            ('video', 'fallback_snapshots'),
            ('video', 'error22_events'),
            ('video', 'error22_windows'),
            ('video', 'error22_recovered'),
            ('video', 'error22_unresolved'),
            ('benchmark', 'requested_instances'),
            ('benchmark', 'tested_instances'),
            ('device', 'frames'),
            ('device', 'errors'),
        ):
            with self.subTest(report=report, key=key):
                video, bench = samples()
                target = (video if report == 'video' else bench if report == 'benchmark'
                          else bench['devices'][0])
                target[key] = 1.5
                failures = evaluate_hardware_evidence(video, bench)['failures']
                self.assertTrue(any(key in failure and 'whole number' in failure
                                    for failure in failures), failures)

    def test_non_mumu_adb_devices_cannot_substitute_for_mumu(self):
        video, bench = samples()
        bench['devices'][0]['serial'] = 'emulator-5554'
        failures = evaluate_hardware_evidence(video, bench)['failures']
        self.assertTrue(any('not a MuMu' in failure for failure in failures), failures)
        video, bench = samples()
        video['device'] = 'emulator-5554'
        failures = evaluate_hardware_evidence(video, bench)['failures']
        self.assertTrue(any('not a MuMu' in failure for failure in failures), failures)

    def test_video_stall_fails_even_if_snapshots_hide_blackout(self):
        video, bench = samples()
        video['max_video_gap_seconds'] = 24
        video['max_preview_gap_seconds'] = 1
        self.assertIn('stream stalled', ' '.join(evaluate_hardware_evidence(video, bench)['failures']))

    def test_error22_unresolved_and_slow_recovery_fail(self):
        video, bench = samples()
        video.update(error22_unresolved=1)
        self.assertFalse(evaluate_hardware_evidence(video, bench)['passed'])
        video['error22_unresolved'] = 0
        video['max_error22_recovery_seconds'] = 30
        self.assertFalse(evaluate_hardware_evidence(video, bench)['passed'])

    def test_video_serial_mismatch_and_fps_inconsistency_fail(self):
        video, bench = samples()
        video['device'] = 'localhost:16673'
        video['displayed_video_fps'] = 100
        errors = evaluate_hardware_evidence(video, bench)['failures']
        self.assertTrue(any('not present' in e for e in errors))
        self.assertTrue(any('disagree' in e for e in errors))

    def test_absent_workflow_is_outstanding_and_not_fabricated(self):
        video, bench = samples(workflow=None)
        result = evaluate_hardware_evidence(video, bench)
        self.assertTrue(result['passed'])
        self.assertFalse(result['workflow_recognition_measured'])
        self.assertTrue(any('recognition' in note for note in result['outstanding']))
        video['error22_events'] = video['error22_recovered'] = 0
        video['max_error22_recovery_seconds'] = None
        result = evaluate_hardware_evidence(video, bench)
        self.assertFalse(result['error22_recovery_observed'])
        self.assertTrue(any('Error 22' in note for note in result['outstanding']))

    def test_missing_or_invalid_fields_cannot_pass(self):
        video, bench = samples()
        video.pop('max_preview_gap_seconds')
        bench['devices'][0]['capture_p95_ms'] = float('nan')
        self.assertFalse(evaluate_hardware_evidence(video, bench)['passed'])
        with self.assertRaises(ValueError):
            EvidenceCriteria(min_video_fps=float('nan'))

    def test_sustained_host_resources_are_reported_without_approving_hardware(self):
        video, bench = samples()
        metrics = {}
        for name, value in [('cpu_percent', 35), ('ram_percent', 55),
                            ('ram_used_mib', 16384), ('gpu_utilization_percent', 70),
                            ('gpu_memory_used_mib', 7500)]:
            metrics[name] = {'samples': 40, 'average': value * .8, 'peak': value}
        bench['host_resources'] = {'duration_seconds': 122, 'interval_seconds': 2,
                                   'sample_count': 40, 'metrics': metrics, 'errors': []}
        result = evaluate_hardware_evidence(video, bench)
        self.assertTrue(result['passed'], result['failures'])
        self.assertEqual(result['host_resource_coverage'],
                         {'cpu': True, 'ram': True, 'gpu': True})
        self.assertFalse(result['hardware_acceptance_complete'])
        self.assertTrue(any('per-emulator' in x for x in result['outstanding']))

    def test_missing_gpu_telemetry_remains_outstanding_not_fabricated(self):
        video, bench = samples()
        metrics = {}
        for name in ('cpu_percent', 'ram_percent', 'ram_used_mib'):
            metrics[name] = {'samples': 40, 'average': 20, 'peak': 40}
        for name in ('gpu_utilization_percent', 'gpu_memory_used_mib'):
            metrics[name] = {'samples': 0, 'average': None, 'peak': None}
        bench['host_resources'] = {'duration_seconds': 122, 'interval_seconds': 2,
                                   'sample_count': 40, 'metrics': metrics,
                                   'errors': ['GPU unavailable: nvidia-smi not found']}
        result = evaluate_hardware_evidence(video, bench)
        self.assertTrue(result['passed'], result['failures'])
        self.assertFalse(result['host_resource_coverage']['gpu'])
        self.assertIn('GPU', ' '.join(result['outstanding']))

    def test_short_or_impossible_host_resource_report_fails_closed(self):
        video, bench = samples()
        bench['host_resources'] = {'duration_seconds': 12, 'interval_seconds': 2,
                                   'sample_count': 1, 'metrics': {}, 'errors': []}
        result = evaluate_hardware_evidence(video, bench)
        self.assertFalse(result['passed'])
        self.assertIn('shorter', ' '.join(result['failures']))
        video, bench = samples()
        metrics = {name: {'samples': 50, 'average': 30, 'peak': 40}
                   for name in ('cpu_percent', 'ram_percent', 'ram_used_mib',
                                'gpu_utilization_percent', 'gpu_memory_used_mib')}
        metrics['gpu_utilization_percent']['peak'] = 150
        bench['host_resources'] = {'duration_seconds': 125, 'interval_seconds': 2,
                                   'sample_count': 50, 'metrics': metrics, 'errors': []}
        result = evaluate_hardware_evidence(video, bench)
        self.assertFalse(result['passed'])
        self.assertTrue(any('impossible' in x for x in result['failures']))

    def test_cli_always_writes_failure_evidence_for_missing_input(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'result.json'
            status = main(['--video', str(Path(d) / 'absent.json'), '--benchmark',
                           str(Path(d) / 'missing.json'), '--output', str(path)])
            self.assertEqual(status, 1)
            self.assertFalse(json.loads(path.read_text())['passed'])

    def test_cli_writes_passing_read_only_evidence(self):
        video, bench = samples()
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / 'video.json').write_text(json.dumps(video))
            (folder / 'bench.json').write_text(json.dumps(bench))
            result = folder / 'result.json'
            status = main(['--video', str(folder / 'video.json'), '--benchmark',
                           str(folder / 'bench.json'), '--output', str(result)])
            self.assertEqual(status, 0)
            self.assertFalse(json.loads(result.read_text())['hardware_acceptance_complete'])


if __name__ == '__main__':
    unittest.main()
