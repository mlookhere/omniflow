"""Capture reports only support decisions when their measurements agree."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools import assess_capture as assessment


def profile(kind: str, fps: float, p95: float) -> dict:
    seconds = 120.0
    frames = round(seconds * fps)
    observed = frames / seconds
    return {
        'serial': '127.0.0.1:16640', 'read_only': True,
        'capture_format': kind,
        'capture_source': ('ADB exec-out screencap -p (PNG)' if kind == 'png'
                           else 'ADB exec-out screencap (raw framebuffer)'),
        'requested_seconds': seconds, 'elapsed_seconds': seconds,
        'min_fps': .8, 'frames': frames, 'errors': 0,
        'effective_fps': round(observed, 3),
        'payload_bytes_mean': 124000 if kind == 'png' else 4000000,
        'timings_ms': {name: {'mean': 150, 'p95': p95 if name == 'total_ms' else p95 * .8}
                       for name in ('adb_exec_ms', 'decode_ms', 'normalize_ms', 'total_ms')},
        'passed': observed >= .8,
        'failures': [] if observed >= .8 else [
            f'Capture {observed:.3f} FPS below 0.800 FPS target'],
    }


def fidelity() -> dict:
    return {
        'serial': 'localhost:16640', 'read_only': True,
        'passed': True, 'hardware_acceptance_complete': False,
        'quality_equivalence_verified': True,
        'capture_order': ['png', 'raw', 'png'],
        'triplets_requested': 3, 'triplets_checked': 3,
        'pixel_tolerance': 8, 'max_changed_fraction': .005,
        'results': [
            {'index': i, 'stable_png_controls': True, 'equivalent': True,
             'png_motion_fraction': 0.0, 'raw_delta_before_fraction': 0.0,
             'raw_delta_after_fraction': 0.0, 'source_shape': [960, 540, 3]}
            for i in (1, 2, 3)],
        'failures': [],
    }


class CaptureAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.png = profile('png', .725, 1719)
        self.raw = profile('raw', 1.125, 980)
        self.fidelity = fidelity()

    def test_valid_raw_candidate_is_not_production_or_hardware_acceptance(self):
        result = assessment.assess(self.png, self.raw, self.fidelity)
        self.assertTrue(result['passed'])
        self.assertTrue(result['comparison_complete'])
        self.assertTrue(result['raw_candidate_for_further_validation'])
        self.assertFalse(result['hardware_acceptance_complete'])
        self.assertFalse(result['production_capture_changed'])
        self.assertGreater(result['metrics']['raw_relative_fps_gain'], .1)
        self.assertEqual(result['metrics']['serial'], '127.0.0.1:16640')

    def test_slower_raw_is_a_valid_negative_result_not_a_candidate(self):
        raw = profile('raw', .7, 2000)
        result = assessment.assess(self.png, raw, self.fidelity)
        self.assertTrue(result['comparison_complete'])
        self.assertFalse(result['raw_candidate_for_further_validation'])

    def test_mismatched_identity_fails_closed(self):
        self.raw['serial'] = '127.0.0.1:16384'
        result = assessment.assess(self.png, self.raw, self.fidelity)
        self.assertFalse(result['passed'])
        self.assertIn('different devices', ' '.join(result['failures']))

    def test_short_duration_missing_frames_and_forged_fps_rejected(self):
        for field, value in [('elapsed_seconds', 19.0), ('frames', 0),
                             ('effective_fps', 30.0), ('errors', 1)]:
            with self.subTest(field=field):
                bad = copy.deepcopy(self.raw)
                bad[field] = value
                self.assertFalse(assessment.assess(self.png, bad, self.fidelity)['passed'])

    def test_raw_success_flag_cannot_override_fps_or_errors(self):
        raw = profile('raw', .6, 2000)
        raw['passed'] = True
        raw['failures'] = []
        self.assertFalse(assessment.assess(self.png, raw, self.fidelity)['passed'])

    def test_bad_fidelity_flags_and_loose_tolerance_rejected(self):
        for mutate in (lambda x: x.update(passed=False),
                       lambda x: x.update(pixel_tolerance=255),
                       lambda x: x['results'][1].update(stable_png_controls=False),
                       lambda x: x['results'][2].update(raw_delta_before_fraction=.1),
                       lambda x: x.update(triplets_checked=2),
                       lambda x: x['results'][1].update(source_shape=[540, 960, 3])):
            with self.subTest(mutate=mutate):
                bad = copy.deepcopy(self.fidelity)
                mutate(bad)
                self.assertFalse(assessment.assess(self.png, self.raw, bad)['passed'])

    def test_absent_json_overwrites_existing_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            destination = root / 'assessed.json'
            destination.write_text('{"passed": true}', encoding='utf-8')
            for name, payload in (('png', self.png), ('raw', self.raw)):
                (root / f'{name}.json').write_text(json.dumps(payload), encoding='utf-8')
            self.assertEqual(assessment.main([
                '--png', str(root / 'png.json'), '--raw', str(root / 'raw.json'),
                '--fidelity', str(root / 'missing.json'), '--output', str(destination)]), 1)
            data = json.loads(destination.read_text())
            self.assertFalse(data['passed'])
            self.assertFalse(data['raw_candidate_for_further_validation'])
            self.assertFalse(data['hardware_acceptance_complete'])
            self.assertIn('could not load', ' '.join(data['failures']))


if __name__ == '__main__':
    unittest.main()
