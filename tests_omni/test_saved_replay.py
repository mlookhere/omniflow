"""Offline replay must be read-only, label mismatches and reject unsafe files."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from omniflow.engine import WorkflowError, resolve_action_chain
from omniflow.replay import describe_action_plan, replay_saved_captures
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
        self.assertEqual(results[0]['action_plan'], ['Tap location selected on screen'])
        self.assertTrue(any('Ready [ACTION]: selected' in line
                            for line in results[0]['evidence']))
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
        evidence = '\n'.join(results[0]['evidence'])
        self.assertIn('Wait [WAIT]: selected', evidence)
        self.assertIn('Tap [ACTION]: overridden by WAIT', evidence)
        self.assertEqual(results[1]['status'], 'matched')
        self.assertEqual(results[0]['action_plan'], ['WAIT — no input will be sent'])
        self.assertEqual(results[1]['action_plan'], ['WAIT — no input will be sent'])
        self.assertEqual(results[1]['overlaps'], [
            {'key': action_key, 'label': 'Tap', 'passive': False}])
        self.assertEqual(results[0]['overlaps'], [
            {'key': action_key, 'label': 'Tap', 'passive': False}])

    def test_replay_previews_chain_order_and_hides_text_contents(self):
        secret = 'secret123'
        record_step(
            self.folder, 'Enter code', self.frame, [],
            condition_only=True,
            conditions=[{'type': 'color', 'rect': [90, 130, 240, 255],
                         'bgr': [15, 225, 20], 'tolerance': 150}],
            pre_tap_delay_seconds=3,
            action_chain=[
                {'kind': 'tap', 'point': [150, 210]},
                {'kind': 'wait', 'seconds': 2},
                {'kind': 'field_text', 'point': [150, 210], 'text': secret},
                {'kind': 'count_run'},
            ])
        # Use a known passing condition to inspect the saved chain.
        data = json.loads(self.manifest().read_text())
        action = next(iter(data['actions'].values()))
        action['conditions'] = [
            {'type': 'pixel', 'rect': [95, 125, 150, 175],
             'point': [110, 140], 'bgr': [15, 225, 20], 'tolerance': 255}]
        self.manifest().write_text(json.dumps(data), encoding='utf-8')
        result = replay_saved_captures(self.folder)[0]
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['action_plan'], [
            'Initial delay 3s after stable recognition',
            'Tap location selected on screen',
            'Wait 2 seconds',
            'Tap selected field and type text (contents hidden)',
            'Mark session run complete'])
        self.assertNotIn(secret, repr(result))

    def test_legacy_paired_anchors_resolve_one_alternative(self):
        action = {'gestures': [
            {'kind': 'tap', 'point': [100, 200]},
            {'kind': 'tap', 'point': [250, 300]}]}
        self.assertEqual(
            resolve_action_chain(action, 2, trigger=1),
            [action['gestures'][1]])
        self.assertEqual(
            resolve_action_chain(action, 1, trigger=1),
            action['gestures'])
        self.assertEqual(
            resolve_action_chain({'passive': True, **action}, 2, trigger=1), [])
        explicit = {'action_chain': [
            {'kind': 'wait', 'seconds': 1},
            {'kind': 'tap', 'point': [250, 300]}],
            **action}
        self.assertEqual(
            resolve_action_chain(explicit, 2, trigger=1),
            explicit['action_chain'])

    def test_image_threshold_failure_includes_actual_score(self):
        self.record('Unrecognized')
        with patch('omniflow.engine.OmniDetector._match_template',
                   return_value=0.13):
            result = replay_saved_captures(self.folder)[0]
        self.assertEqual(result['status'], 'unknown')
        self.assertIn('image 13%', result['reason'])
        self.assertIn('minimum 82%', result['reason'])
        self.assertIn('image 13% (minimum 82%)', '\n'.join(result['evidence']))
        self.assertIn('template below threshold', '\n'.join(result['evidence']))
        self.assertEqual(result['overlaps'], [])

    def test_unsaved_action_plan_is_validated_and_redacted(self):
        action = {'pre_tap_delay_seconds': 3}
        chain = [
            {'kind': 'tap', 'point': [150, 210]},
            {'kind': 'wait', 'seconds': 2},
            {'kind': 'text', 'text': 'private-42'},
            {'kind': 'count_run'},
        ]
        steps = describe_action_plan(action, chain)
        self.assertEqual(steps, [
            'Initial delay 3s after stable recognition',
            'Tap location selected on screen',
            'Wait 2 seconds',
            'Type text (contents hidden)',
            'Mark session run complete',
        ])
        self.assertNotIn('private-42', repr(steps))
        with self.assertRaisesRegex(WorkflowError, 'outside 540'):
            describe_action_plan(action, [
                {'kind': 'tap', 'point': [999, 210]}])
        self.assertEqual(
            describe_action_plan({'passive': True}, chain),
            ['WAIT — no input will be sent'])

    def test_replay_rejects_saved_invalid_action_plan_instead_of_claiming_success(self):
        key = self.record('Unsafe')
        manifest = self.manifest()
        data = json.loads(manifest.read_text())
        data['actions'][key]['action_chain'] = [
            {'kind': 'tap', 'point': [700, 210]}]
        manifest.write_text(json.dumps(data), encoding='utf-8')
        before = manifest.read_bytes()
        with patch('omniflow.engine.OCREngine.read') as ocr:
            with self.assertRaisesRegex(WorkflowError, 'action 1: Point'):
                replay_saved_captures(self.folder)
            ocr.assert_not_called()
        self.assertEqual(manifest.read_bytes(), before)

    def test_replay_checks_nonwinning_states_before_inspecting_screenshots(self):
        self.record('Expected')
        other = self.record('Secondary')
        manifest = self.manifest()
        data = json.loads(manifest.read_text(encoding='utf-8'))
        data['actions'][other]['action_chain'] = [
            {'kind': 'tap', 'point': [150, 210]},
            {'kind': 'unsupported'},
        ]
        manifest.write_text(json.dumps(data), encoding='utf-8')
        before = manifest.read_bytes()
        with patch('omniflow.engine.OCREngine.read') as ocr:
            with self.assertRaisesRegex(
                    WorkflowError, "Secondary.*, action 2: Unknown action"):
                replay_saved_captures(self.folder)
            ocr.assert_not_called()
        self.assertEqual(manifest.read_bytes(), before)

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

    def test_nonnormalized_capture_is_unavailable_without_detector_inspection(self):
        key = self.record('Wrong capture geometry')
        manifest = self.manifest()
        data = json.loads(manifest.read_text(encoding='utf-8'))
        capture = self.folder / 'captures' / data['actions'][key]['capture']
        self.assertTrue(cv2.imwrite(str(capture), self.frame[:700, :500]))
        before = manifest.read_bytes()
        with patch('omniflow.replay.OmniDetector.inspect') as inspect:
            result = replay_saved_captures(self.folder)[0]
            inspect.assert_not_called()
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('normalized 540×960', result['reason'])
        self.assertEqual(result['actual_key'], '')
        self.assertEqual(result['action_plan'], [])
        self.assertEqual(result['evidence'], [])
        self.assertEqual(manifest.read_bytes(), before)

    def test_redirected_capture_directory_is_unavailable_and_never_read(self):
        key = self.record('External capture')
        captures = self.folder / 'captures'
        outside = Path(self.temp.name) / 'outside_captures'
        captures.rename(outside)
        try:
            captures.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            outside.rename(captures)
            self.skipTest('Directory symlinks not available')

        before = self.manifest().read_bytes()
        with patch('omniflow.replay.cv2.imread', wraps=cv2.imread) as read:
            result = replay_saved_captures(self.folder)[0]
        self.assertEqual(result['expected_key'], key)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['reason'], 'Unsafe screenshot reference')
        self.assertEqual(result['evidence'], [])
        self.assertEqual(result['action_plan'], [])
        self.assertFalse(any(str(outside) in str(call.args[0])
                             for call in read.call_args_list))
        self.assertEqual(self.manifest().read_bytes(), before)

    def test_redirected_anchor_directory_fails_before_image_reads(self):
        self.record('External anchor')
        anchors = self.folder / 'anchors'
        outside = Path(self.temp.name) / 'outside_anchors'
        anchors.rename(outside)
        try:
            anchors.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            outside.rename(anchors)
            self.skipTest('Directory symlinks not available')

        before = self.manifest().read_bytes()
        with patch('omniflow.engine.cv2.imread') as read:
            with self.assertRaisesRegex(WorkflowError,
                                        'anchors directory resolves outside workflow'):
                replay_saved_captures(self.folder)
            read.assert_not_called()
        self.assertEqual(self.manifest().read_bytes(), before)

    def test_colon_in_imported_capture_name_is_not_a_safe_filename(self):
        key = self.record('Imported screenshot')
        content = json.loads(self.manifest().read_text(encoding='utf-8'))
        content['actions'][key]['capture'] = 'external:C.jpg'
        self.manifest().write_text(json.dumps(content), encoding='utf-8')
        with patch('omniflow.replay.cv2.imread', wraps=cv2.imread) as read:
            result = replay_saved_captures(self.folder)[0]
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['reason'], 'Unsafe screenshot reference')
        self.assertFalse(any('external:C.jpg' in str(call.args[0])
                             for call in read.call_args_list))

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
        self.assertIn('FAIL — When zone average color', '\n'.join(result['evidence']))

    def test_replay_explains_pass_and_fail_rules_without_raw_ocr_text(self):
        record_step(
            self.folder, 'OCR expected', self.frame, [], condition_only=True,
            conditions=[
                {'type': 'ocr', 'rect': [90, 130, 240, 255],
                 'text': 'READY'},
                {'type': 'ocr', 'rect': [90, 130, 240, 255],
                 'text': 'CANCEL', 'negate': True},
                {'type': 'color', 'rect': [90, 130, 240, 255],
                 'bgr': [0, 0, 0], 'tolerance': 0},
            ],
            action_chain=[{'kind': 'tap', 'point': [150, 210]}])
        with patch('omniflow.engine.OCREngine.read',
                   return_value='READY private-ocr-output') as ocr:
            result = replay_saved_captures(self.folder)[0]
        self.assertEqual(result['status'], 'unknown')
        evidence = '\n'.join(result['evidence'])
        self.assertIn('OCR expected [ACTION]: conditions failed', evidence)
        self.assertIn('PASS', evidence)
        self.assertIn('FAIL — When zone average color', evidence)
        self.assertNotIn('private-ocr-output', repr(result))
        self.assertEqual(ocr.call_count, 1)

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
