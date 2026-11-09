"""Unified editor save preserves user recordings and existing behavior."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from swordx.detection import ScreenDetector
from swordx.workflows import (create_workflow, load_step, record_step,
                              update_step_settings)

A = (40, 80, 100, 140)
B = (260, 560, 340, 620)


def screenshot():
    frame = np.full((960, 540, 3), 30, dtype=np.uint8)
    rng = np.random.default_rng(19)
    for x1, y1, x2, y2 in (A, B):
        frame[y1:y2, x1:x2] = rng.integers(0, 255, (y2-y1, x2-x1, 3), dtype=np.uint8)
    return frame


class UnifiedSaveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = create_workflow('Editable', Path(self.temp.name))
        self.frame = screenshot()

    def manifest(self):
        return json.loads((self.folder/'manifest.json').read_text(encoding='utf-8'))

    def images(self):
        return {str(p.relative_to(self.folder)): p.read_bytes()
                for category in ('captures', 'anchors')
                for p in (self.folder/category).glob('*')}

    def test_one_save_updates_all_settings_without_touching_templates(self):
        key = record_step(self.folder, 'Match', self.frame, [A], taps=[(100, 200)])
        old_data, old_images = self.manifest(), self.images()
        update_step_settings(self.folder, key, 'Start match', end=True, retry=True,
                             retry_mode='on_return', retry_interval_seconds=40,
                             pre_tap_delay_seconds=300)
        data = self.manifest()
        action = data['actions'][key]
        self.assertEqual(data['states'], old_data['states'])
        self.assertEqual(self.images(), old_images)
        self.assertEqual(action['taps'], [[100, 200]])
        self.assertEqual(action['label'], 'Start match')
        self.assertTrue(action['end'])
        self.assertTrue(action['retry'])
        self.assertEqual(action['retry_mode'], 'on_return')
        self.assertEqual(action['retry_interval_seconds'], 40)
        self.assertEqual(action['pre_tap_delay_seconds'], 300)
        self.assertEqual(ScreenDetector(self.folder).detect(self.frame).state, key)

    def test_preserves_legacy_and_tap_format_without_recapture(self):
        key = record_step(self.folder, 'Old match', self.frame, [A, B], (100, 200))
        capture = self.folder/'captures'/self.manifest()['actions'][key]['capture']
        capture.unlink()
        old_images = self.images()
        self.assertTrue(load_step(self.folder, key)[0]['_needs_recapture'])
        update_step_settings(self.folder, key, 'Renamed', end=False, retry=True,
                             retry_mode='on_return', retry_interval_seconds=40,
                             pre_tap_delay_seconds=600)
        self.assertEqual(self.images(), old_images)
        self.assertEqual(self.manifest()['actions'][key]['match'], 'all')
        self.assertEqual(self.manifest()['actions'][key]['tap'], [100, 200])
        self.assertEqual(ScreenDetector(self.folder).detect(self.frame).state, key)

    def test_preserves_two_region_gestures_and_unknown_action_fields(self):
        gestures = [{'kind': 'tap', 'point': [130, 220]},
                    {'kind': 'drag', 'start': [200, 600], 'end': [200, 300], 'duration_ms': 800}]
        key = record_step(self.folder, 'Scroll', self.frame, [A, B], gestures=gestures)
        data = self.manifest()
        data['actions'][key]['extra_field_from_future'] = {'retain': True}
        (self.folder/'manifest.json').write_text(json.dumps(data), encoding='utf-8')
        before = self.images()
        update_step_settings(self.folder, key, 'Scroll again', end=False, retry=False,
                             pre_tap_delay_seconds=25)
        action = self.manifest()['actions'][key]
        self.assertEqual(action['gestures'], gestures)
        self.assertEqual(action['extra_field_from_future'], {'retain': True})
        self.assertEqual(before, self.images())

    def test_reset_to_defaults_removes_optional_fields_only(self):
        key = record_step(self.folder, 'Match', self.frame, [A], taps=[(100, 200)],
                          retry=True, retry_mode='on_return', retry_interval_seconds=40,
                          pre_tap_delay_seconds=300)
        old = self.images()
        update_step_settings(self.folder, key, 'Match', end=False, retry=False)
        action = self.manifest()['actions'][key]
        for key_name in ('retry_mode', 'retry_interval_seconds', 'pre_tap_delay_seconds'):
            self.assertNotIn(key_name, action)
        self.assertFalse(action['retry'])
        self.assertEqual(old, self.images())

    def test_invalid_settings_do_not_change_any_files(self):
        key = record_step(self.folder, 'Match', self.frame, [A], taps=[(100, 200)])
        before, assets = (self.folder/'manifest.json').read_bytes(), self.images()
        bad_inputs = [{'label': ''}, {'pre_tap_delay_seconds': -1},
                      {'retry_mode': 'unknown'}, {'retry_interval_seconds': 0},
                      {'pre_tap_delay_seconds': True}, {'retry_interval_seconds': 86401}]
        for changed in bad_inputs:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                args = dict(label='Match', end=False, retry=False)
                args.update(changed)
                update_step_settings(self.folder, key, **args)
            self.assertEqual((self.folder/'manifest.json').read_bytes(), before)
            self.assertEqual(self.images(), assets)

    def test_unchanged_state_order_and_duplicate_names(self):
        first = record_step(self.folder, 'Same', self.frame, [A], taps=[(100, 200)])
        second = record_step(self.folder, 'Same', self.frame, [B], taps=[(300, 400)])
        before = self.manifest()['order']
        update_step_settings(self.folder, first, 'Same', end=True, retry=False)
        self.assertEqual(self.manifest()['order'], before)
        self.assertIn(second, self.manifest()['states'])


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication, QPushButton
    from swordx.recorder import RecorderDialog
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False


@unittest.skipUnless(GUI_AVAILABLE, 'PySide6 unavailable')
class UnifiedSaveGuiTests(unittest.TestCase):
    setUp = UnifiedSaveTests.setUp
    manifest = UnifiedSaveTests.manifest
    images = UnifiedSaveTests.images

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_editor(self):
        # Isolate the UI from real user files.
        self.list_patch = patch('swordx.recorder.list_workflows', return_value={'Editable': self.folder})
        self.list_patch.start()
        self.addCleanup(self.list_patch.stop)
        editor = RecorderDialog('', 'Editable')
        self.addCleanup(editor.close)
        return editor

    def test_editor_exposes_one_save_button(self):
        editor = self.make_editor()
        saving = [button.text() for button in editor.findChildren(QPushButton)
                  if 'save' in button.text().casefold()]
        self.assertEqual(saving, ['Save State'])

    def test_legacy_recording_edit_settings_from_single_button(self):
        key = record_step(self.folder, 'Old', self.frame, [A, B], (100, 200))
        capture = self.folder/'captures'/self.manifest()['actions'][key]['capture']
        capture.unlink()
        original = self.images()
        editor = self.make_editor()
        editor.steps.setCurrentRow(0)
        self.assertTrue(editor.needs_recapture)
        editor.step_name.setText('Old edited')
        editor.wait_minutes.setValue(5)
        editor.retry.setChecked(True)
        editor.retry_mode.setCurrentIndex(1)
        editor.retry_interval.setValue(40)
        editor.save.click()
        action = self.manifest()['actions'][key]
        self.assertEqual(action['pre_tap_delay_seconds'], 300)
        self.assertEqual(action['retry_mode'], 'on_return')
        self.assertEqual(action['label'], 'Old edited')
        self.assertEqual(self.images(), original)
