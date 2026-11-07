"""Offline replay must be read-only, label mismatches and reject unsafe files."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from omniflow.engine import WorkflowError
from omniflow.replay import replay_saved_captures
from swordx.workflows import create_workflow, record_step


def screenshot():
    frame = np.full((960, 540, 3), (35, 45, 60), dtype=np.uint8)
    cv2.rectangle(frame, (95, 125), (245, 260), (15, 225, 20), -1)
    cv2.putText(frame, 'READY', (101, 208), cv2.FONT_HERSHEY_SIMPLEX,
                1.3, (255, 255, 255), 3)
    return frame


class SavedReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = create_workflow('Replay test', Path(self.temp.name))
        self.frame = screenshot()
        self.rect = (85, 115, 250, 270)

    def record(self, name='Ready', *, passive=False):
        return record_step(
            self.folder, name, self.frame, [self.rect],
            passive=passive, taps=None if passive else [(150, 210)])

    def manifest(self):
        return self.folder / 'manifest.json'

    def test_saved_capture_matches_without_writing_any_files(self):
        key = self.record()
        before = {file.relative_to(self.folder): file.read_bytes()
                  for file in self.folder.rglob('*') if file.is_file()}
        results = replay_saved_captures(self.folder)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['expected_key'], key)
        self.assertEqual(results[0]['actual_key'], key)
        self.assertEqual(results[0]['status'], 'matched')
        self.assertEqual(
            before, {file.relative_to(self.folder): file.read_bytes()
                     for file in self.folder.rglob('*') if file.is_file()})

    def test_passive_wait_overrides_actionable_state_on_saved_capture(self):
        action_key = self.record('Tap')
        wait_key = self.record('Wait', passive=True)
        results = replay_saved_captures(self.folder)
        self.assertEqual([item['expected_key'] for item in results],
                         [action_key, wait_key])
        self.assertEqual(results[0]['status'], 'different')
        self.assertEqual(results[0]['actual_key'], wait_key)
        self.assertEqual(results[0]['reason'], 'overridden by WAIT')
        self.assertEqual(results[1]['status'], 'matched')

    def test_no_capture_does_not_count_as_success(self):
        key = self.record()
        data = json.loads(self.manifest().read_text())
        data['actions'][key].pop('capture')
        self.manifest().write_text(json.dumps(data), encoding='utf-8')
        results = replay_saved_captures(self.folder)
        self.assertEqual(results[0]['status'], 'unavailable')
        self.assertIn('No saved screenshot', results[0]['reason'])

    def test_missing_corrupt_and_unsafe_captures_fail_closed(self):
        key = self.record()
        manifest = self.manifest()
        data = json.loads(manifest.read_text())
        for name, reason in [
            ('../../outside.jpg', 'Unsafe screenshot reference'),
            ('missing.jpg', 'Saved screenshot is missing'),
        ]:
            with self.subTest(name=name):
                data['actions'][key]['capture'] = name
                manifest.write_text(json.dumps(data), encoding='utf-8')
                result = replay_saved_captures(self.folder)[0]
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(result['reason'], reason)
        name = 'broken.jpg'
        (self.folder / 'captures' / name).write_bytes(b'not a JPEG')
        data['actions'][key]['capture'] = name
        manifest.write_text(json.dumps(data), encoding='utf-8')
        result = replay_saved_captures(self.folder)[0]
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['reason'], 'Saved screenshot is unreadable')

    def test_replay_explains_which_condition_rejected_the_capture(self):
        record_step(
            self.folder, 'Wrong color', self.frame, [],
            condition_only=True,
            conditions=[{'type': 'color', 'rect': [90, 130, 240, 255],
                         'bgr': [0, 0, 0], 'tolerance': 0}],
            action_chain=[{'kind': 'tap', 'point': [150, 210]}])
        result = replay_saved_captures(self.folder)[0]
        self.assertEqual(result['status'], 'unknown')
        self.assertIn('conditions failed', result['reason'])
        self.assertIn('zone average color', result['reason'])

    def test_ocr_error_is_reported_not_as_a_match(self):
        record_step(
            self.folder, 'OCR screen', self.frame, [],
            condition_only=True,
            conditions=[{'type': 'ocr', 'rect': [90, 130, 240, 255],
                         'text': 'READY'}],
            action_chain=[{'kind': 'tap', 'point': [150, 210]}])
        with patch('omniflow.engine.OCREngine.read',
                   side_effect=WorkflowError('OCR backend unavailable')):
            result = replay_saved_captures(self.folder)[0]
        self.assertEqual(result['status'], 'error')
        self.assertIn('OCR backend unavailable', result['reason'])


if __name__ == '__main__':
    unittest.main()
