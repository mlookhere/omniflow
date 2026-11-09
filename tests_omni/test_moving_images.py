"""Movable image templates search large regions and tap only live matches."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from omniflow.engine import OmniDetector, OmniRunner, WorkflowError
from swordx.workflows import create_workflow, record_step, update_step_settings


class Device:
    def __init__(self, frame):
        self.frame = frame
        self.taps = []

    def screenshot(self):
        return self.frame.copy()

    def tap(self, x, y):
        self.taps.append((x, y))


class MovingImageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = create_workflow('Moving image', Path(temp.name))
        self.box = (65, 115, 125, 160)
        self.icon = np.random.default_rng(716).integers(
            30, 240, (45, 60, 3), dtype=np.uint8)
        self.original = np.full((960, 540, 3), 12, dtype=np.uint8)
        self.original[115:160, 65:125] = self.icon
        self.key = record_step(
            self.folder, 'Moving button', self.original, [self.box],
            action_chain=[{'kind': 'tap_match'}],
            search_rect=[0, 0, 540, 960])
        self.device = Device(self.at(340, 620))

    def at(self, x, y):
        frame = np.full((960, 540, 3), 12, dtype=np.uint8)
        frame[y:y + 45, x:x + 60] = self.icon
        return frame

    def test_detector_returns_actual_location_with_one_pass_and_no_ocr(self):
        detector = OmniDetector(self.folder)
        with (patch('omniflow.engine.OCREngine.read') as ocr,
              patch('omniflow.engine.cv2.matchTemplate',
                    wraps=cv2.matchTemplate) as matching):
            found = detector.detect(self.device.frame)
        self.assertEqual(found.key, self.key)
        self.assertEqual(found.location, (340, 620, 400, 665))
        self.assertGreater(found.confidence, .98)
        matching.assert_called_once()
        ocr.assert_not_called()
        inspected, states = detector.inspect(self.device.frame)
        self.assertEqual(inspected.location, found.location)
        self.assertEqual(states[0]['matched_bounds'], found.location)

    def test_live_tap_follows_image_not_old_recorded_coordinates(self):
        runner = OmniRunner(self.device, self.folder, 'com.example.game')
        runner.step(100)
        self.assertEqual(self.device.taps, [])
        runner.step(101)
        self.assertEqual(self.device.taps, [(370, 642)])
        self.assertEqual(runner.last_decision, 'Action 1/1: tap_match (command issued)')

    def test_change_of_location_needs_new_confirmation(self):
        runner = OmniRunner(self.device, self.folder, 'com.example.game')
        runner.step(100)
        self.device.frame = self.at(210, 440)
        runner.step(101)
        self.assertEqual(self.device.taps, [])
        self.assertIn('Confirming screen', runner.last_decision)
        runner.step(102)
        self.assertEqual(self.device.taps, [(240, 462)])
        # Moving again invalidates consumed action progress and requires a
        # second new frame before permitting the new target's first tap.
        self.device.frame = self.at(370, 700)
        runner.step(103)
        self.assertEqual(len(self.device.taps), 1)
        runner.step(104)
        self.assertEqual(self.device.taps[-1], (400, 722))

    def test_blank_or_outside_search_area_does_not_issue_touch(self):
        self.device.frame = self.at(340, 620)
        manifest = self.folder / 'manifest.json'
        data = json.loads(manifest.read_text(encoding='utf-8'))
        data['actions'][self.key]['search_rect'] = [0, 0, 190, 240]
        manifest.write_text(json.dumps(data), encoding='utf-8')
        runner = OmniRunner(self.device, self.folder, 'com.example.game')
        runner.step(100)
        runner.step(101)
        self.assertEqual(self.device.taps, [])
        self.assertEqual(runner.last_decision, 'Unknown screen; no input')

    def test_invalid_search_area_and_unsafely_configured_action_fail_closed(self):
        manifest = self.folder / 'manifest.json'
        original_bytes = manifest.read_bytes()
        for bad in ([0, 0, 5, 5], [-1, 0, 540, 960], [0, 0, 540.0, 960]):
            data = json.loads(original_bytes)
            data['actions'][self.key]['search_rect'] = bad
            manifest.write_text(json.dumps(data), encoding='utf-8')
            with self.subTest(search_rect=bad):
                with self.assertRaises(WorkflowError):
                    OmniRunner(self.device, self.folder, 'com.example.game')
                self.assertEqual(self.device.taps, [])
        data = json.loads(original_bytes)
        del data['actions'][self.key]['search_rect']
        manifest.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaisesRegex(WorkflowError, 'moving image search'):
            OmniRunner(self.device, self.folder, 'com.example.game')
        self.assertEqual(self.device.taps, [])

    def test_saved_settings_preserve_image_assets_and_custom_search_region(self):
        manifest = self.folder / 'manifest.json'
        before = json.loads(manifest.read_text(encoding='utf-8'))
        assets = [p.read_bytes() for p in self.folder.rglob('*.png')]
        studio = {'action_chain': [{'kind': 'tap_match'}], 'conditions': [],
                  'skip_template': False, 'condition_match': 'all',
                  'search_rect': [20, 45, 450, 840]}
        update_step_settings(self.folder, self.key, 'Moving button',
                             end=False, retry=False, studio=studio)
        after = json.loads(manifest.read_text(encoding='utf-8'))
        self.assertEqual(after['actions'][self.key]['search_rect'],
                         [20, 45, 450, 840])
        self.assertEqual(before['states'], after['states'])
        self.assertEqual(assets, [p.read_bytes() for p in self.folder.rglob('*.png')])
        bad = dict(studio, search_rect=[0, 0, 20, 20])
        saved = manifest.read_bytes()
        with self.assertRaises(ValueError):
            update_step_settings(self.folder, self.key, 'Moving button',
                                 end=False, retry=False, studio=bad)
        self.assertEqual(manifest.read_bytes(), saved)

    def test_dry_run_never_taps_dynamic_image(self):
        runner = OmniRunner(self.device, self.folder, 'com.example.game', dry_run=True)
        runner.step(100)
        runner.step(101)
        self.assertEqual(self.device.taps, [])
        self.assertEqual(runner.last_decision, 'Action 1/1: tap_match (no input)')


if __name__ == '__main__':
    unittest.main()
