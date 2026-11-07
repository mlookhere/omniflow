"""PNG/raw comparison requires a stable screen, correct pixels and geometry."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from tools import compare_capture as tool


class Device:
    def __init__(self, serial):
        self.serial = serial

    def ensure_connected(self):
        pass


def frame(image, fmt):
    return image, {'payload_bytes': 100 if fmt == 'png' else 240,
                   'source_shape': [960, 540, 3]}


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((960, 540, 3), np.uint8)

    def test_stable_png_raw_png_triplets_verify_quality_only(self):
        samples = [frame(self.image, fmt) for fmt in ('png', 'raw', 'png') * 2]
        with patch.object(tool, 'capture_frame', side_effect=samples) as capture:
            result = tool.compare('127.0.0.1:16384', triplets=2,
                                  device_factory=Device)
        self.assertEqual([c.kwargs['capture_format'] for c in capture.call_args_list],
                         ['png', 'raw', 'png'] * 2)
        self.assertTrue(result['quality_equivalence_verified'])
        self.assertFalse(result['hardware_acceptance_complete'])
        self.assertEqual(result['results'][0]['raw_bytes'], 240)

    def test_moving_png_controls_fail_closed(self):
        changed = self.image.copy()
        changed[:150, :, 0] = 255
        samples = [frame(self.image, 'png'), frame(self.image, 'raw'),
                   frame(changed, 'png')]
        with patch.object(tool, 'capture_frame', side_effect=samples):
            result = tool.compare('127.0.0.1:16384', triplets=1,
                                  device_factory=Device)
        self.assertFalse(result['passed'])
        self.assertIn('stationary', ' '.join(result['failures']))

    def test_raw_pixel_mismatch_fails_when_controls_stable(self):
        wrong = self.image.copy()
        wrong[:100, :, 0] = 120
        samples = [frame(self.image, 'png'), frame(wrong, 'raw'),
                   frame(self.image, 'png')]
        with patch.object(tool, 'capture_frame', side_effect=samples):
            result = tool.compare('127.0.0.1:16384', triplets=1,
                                  device_factory=Device)
        self.assertFalse(result['passed'])
        self.assertTrue(result['results'][0]['stable_png_controls'])
        self.assertIn('differ', ' '.join(result['failures']))

    def test_orientation_changed_is_not_equivalent(self):
        samples = [frame(self.image, fmt) for fmt in ('png', 'raw', 'png')]
        samples[1][1]['source_shape'] = [540, 960, 3]
        with patch.object(tool, 'capture_frame', side_effect=samples):
            result = tool.compare('127.0.0.1:16384', triplets=1,
                                  device_factory=Device)
        self.assertFalse(result['passed'])
        self.assertIn('geometry', ' '.join(result['failures']))

    def test_bad_capture_is_reported_without_success(self):
        with patch.object(tool, 'capture_frame', side_effect=[
            frame(self.image, 'png'), OSError('ADB lost')]):
            result = tool.compare('127.0.0.1:16384', triplets=1,
                                  device_factory=Device)
        self.assertFalse(result['quality_equivalence_verified'])
        self.assertIn('ADB lost', ' '.join(result['failures']))

    def test_changed_fraction_checks_channels_shapes_and_threshold(self):
        altered = self.image.copy()
        altered[0, 0, 2] = 9
        self.assertAlmostEqual(tool.changed_fraction(self.image, altered, 8),
                               1 / (960 * 540))
        self.assertEqual(tool.changed_fraction(self.image, altered, 9), 0)
        with self.assertRaisesRegex(ValueError, 'dimensions'):
            tool.changed_fraction(self.image, altered[:20], 8)

    def test_device_failure_replaces_old_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'comparison.json'
            tool.write_json(output, {'passed': True})
            with patch.object(tool, 'discover_mumu', return_value=[]):
                status = tool.main(['--serial','127.0.0.1:16384',
                                    '--output',str(output)])
            result = json.loads(output.read_text())
            self.assertEqual(status, 1)
            self.assertFalse(result['passed'])
            self.assertFalse(result['quality_equivalence_verified'])


if __name__ == '__main__':
    unittest.main()
