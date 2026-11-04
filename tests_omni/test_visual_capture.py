"""Zone/gesture authoring is coordinate-free in the UI, normalized in storage."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from omniflow.engine import OmniDetector, OmniRunner
from omniflow.visual import (action_summary, condition_summary, require_frame,
                             sample_average, sample_color, validate_point, validate_zone)
from swordx.workflows import create_workflow, record_step


class VisualCaptureTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.full((960, 540, 3), (5, 50, 180), dtype=np.uint8)
        cv2.rectangle(self.frame, (80, 100), (220, 170), (200, 100, 10), -1)
        cv2.putText(self.frame, 'START', (85, 142), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8, (245, 245, 245), 2)

    def test_zone_and_point_validate_borders(self):
        self.assertEqual(validate_zone((0, 0, 540, 960)), (0, 0, 540, 960))
        self.assertEqual(validate_point([539, 959]), (539, 959))
        for bad in ((-1, 0, 20, 40), (100, 100, 99, 140), (0, 0, 1, 1)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_zone(bad)
        for bad in ((540, 300), (5.5, 60)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_point(bad)

    def test_color_sampling_uses_selected_image_without_manual_bgr(self):
        self.assertEqual(sample_color(self.frame, [120, 160]), [200, 100, 10])
        self.assertEqual(sample_average(self.frame, [110, 150, 140, 168]), [200, 100, 10])

    def test_no_manual_coordinates_required_to_author_condition_only_ocr(self):
        with tempfile.TemporaryDirectory() as root:
            folder = create_workflow('OCR-only', Path(root))
            rule = {'type': 'ocr', 'rect': [80, 100, 220, 170], 'text': 'Start'}
            action = {'kind': 'tap', 'point': [140, 155]}
            key = record_step(folder, 'Tap when text appears', self.frame, [],
                              gestures=[], conditions=[rule], condition_only=True,
                              action_chain=[action])
            manifest = json.loads((folder / 'manifest.json').read_text())
            self.assertEqual(manifest['states'][key], [])
            self.assertTrue(manifest['actions'][key]['skip_template'])
            with patch('omniflow.engine.OCREngine.read', return_value='START GAME'):
                match = OmniDetector(folder).detect(self.frame)
            self.assertEqual(match.key, key)
            self.assertEqual(match.label, 'Tap when text appears')

    def test_condition_only_requires_proof_and_valid_action(self):
        with tempfile.TemporaryDirectory() as root:
            folder = create_workflow('Invalid condition', Path(root))
            with self.assertRaisesRegex(ValueError, 'recognition condition'):
                record_step(folder, 'Unsafe', self.frame, [], gestures=[],
                            condition_only=True, action_chain=[{'kind':'tap','point':[100,200]}])
            with self.assertRaisesRegex(ValueError, 'action sequence'):
                record_step(folder, 'No action', self.frame, [], gestures=[],
                            condition_only=True, conditions=[{'type':'ocr','rect':[80,100,220,170],'text':'GO'}])
            self.assertEqual(len(json.loads((folder/'manifest.json').read_text())['states']), 0)

    def test_preexisting_capture_with_region_and_tap_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            folder = create_workflow('Traditional', Path(root))
            key = record_step(folder,'Classic', self.frame, [(80,100,220,170)],
                              taps=[(130,140)])
            manifest = json.loads((folder/'manifest.json').read_text())
            self.assertEqual(len(manifest['states'][key]), 1)
            self.assertEqual(manifest['actions'][key]['taps'], [[130, 140]])

    def test_readable_editor_summaries_do_not_expose_coordinates(self):
        self.assertIn('Swipe',action_summary({'kind':'swipe','start':[20,20],
                                               'end':[120,180],'duration_ms':450}))
        self.assertNotIn('120',action_summary({'kind':'tap','point':[120,180]}))
        self.assertIn('NOT',condition_summary({'type':'ocr','rect':[1,1,20,20],
                                               'text':'Cancel','negate':True}))


if __name__ == '__main__':
    unittest.main()
