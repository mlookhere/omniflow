"""Workflow duplication must preserve every recording without touching its source."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from swordx.config import ProfileStore, Settings
from swordx.detection import ScreenDetector
from swordx.storage import duplicate_workflow
from swordx.workflows import (BUILTIN, create_workflow, list_workflows, record_step,
                               remove_step, reorder_steps, update_step_settings)


class DuplicateWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = create_workflow('Original', self.root)
        self.store = ProfileStore(self.root)
        self.store.save('Default', Settings(workflow='Original'))
        self.frame = np.random.default_rng(39).integers(0, 256, (960, 540, 3), dtype=np.uint8)
        self.match = record_step(self.source, 'Start match', self.frame,
                                 [(50, 70, 180, 120), (310, 700, 420, 750)],
                                 gestures=[{'kind': 'tap', 'point': [100, 300]},
                                           {'kind': 'drag', 'start': [400, 700], 'end': [400, 200],
                                            'duration_ms': 1400}],
                                 retry=True, retry_mode='on_return',
                                 retry_interval_seconds=40, pre_tap_delay_seconds=300)
        self.wait = record_step(self.source, 'Wait on Cancel', self.frame,
                                [(50, 70, 180, 120)], passive=True, end=True)
        reorder_steps(self.source, [self.wait, self.match])

    @staticmethod
    def snapshot(folder):
        return {p.relative_to(folder).as_posix(): p.read_bytes()
                for p in folder.rglob('*') if p.is_file()}

    def test_copy_is_independent_and_retains_all_visual_actions_and_settings(self):
        source_before = self.snapshot(self.source)
        copied = duplicate_workflow('Original', 'New Copy', base=self.root)
        self.assertNotEqual(copied, self.source)
        original_manifest = json.loads(source_before['manifest.json'])
        copy_manifest = json.loads((copied / 'manifest.json').read_text())
        self.assertEqual({**copy_manifest, 'name': 'Original'}, original_manifest)
        for relative, content in source_before.items():
            if relative != 'manifest.json':
                self.assertEqual((copied / relative).read_bytes(), content, relative)
        self.assertEqual(list_workflows(self.root)['New Copy'], copied)
        self.assertEqual(self.store.get('Default').workflow, 'Original')
        self.assertEqual(ScreenDetector(copied).detect(self.frame).state, self.wait)

        update_step_settings(copied, self.match, 'Different action', end=False,
                             retry=False, pre_tap_delay_seconds=15)
        remove_step(copied, self.wait)
        self.assertEqual(self.snapshot(self.source), source_before)
        self.assertEqual(len(json.loads((copied/'manifest.json').read_text())['states']), 1)
        self.assertEqual(len(list_workflows(self.root)), 3)  # includes built-in

    def test_reject_names_and_builtin_without_affecting_existing_workflows(self):
        original = self.snapshot(self.source)
        for name in ('Original', 'oRiGiNaL', BUILTIN, '   ', 'a/b', 'x'*49):
            with self.subTest(name=name), self.assertRaises(ValueError):
                duplicate_workflow('Original', name, base=self.root)
        with self.assertRaisesRegex(ValueError, 'recorded workflow'):
            duplicate_workflow(BUILTIN, 'Built-in copy', base=self.root)
        with self.assertRaisesRegex(ValueError, 'recorded workflow'):
            duplicate_workflow('Missing', 'Missing copy', base=self.root)
        self.assertEqual(self.snapshot(self.source), original)
        self.assertEqual(len(list_workflows(self.root)), 2)

    def test_missing_template_fails_without_publishing_partial_copy(self):
        meta = json.loads((self.source/'manifest.json').read_text())
        file = meta['states'][self.match][0]['file']
        (self.source/'anchors'/file).unlink()
        with self.assertRaisesRegex(ValueError, 'Missing or unsafe'):
            duplicate_workflow('Original', 'Invalid copy', base=self.root)
        self.assertNotIn('Invalid copy', list_workflows(self.root))
        self.assertEqual(list(self.root.glob('.swordx-duplicate-*')), [])

    def test_legacy_template_without_capture_is_supported(self):
        content = json.loads((self.source/'manifest.json').read_text())
        cap = content['actions'][self.match].pop('capture')
        (self.source/'captures'/cap).unlink()
        (self.source/'manifest.json').write_text(json.dumps(content))
        clone = duplicate_workflow('Original', 'Legacy copy', base=self.root)
        self.assertFalse((clone/'captures'/cap).exists())
        self.assertEqual(json.loads((clone/'manifest.json').read_text())['actions'],
                         content['actions'])
        self.assertEqual(self.snapshot(self.source)['manifest.json'],
                         (self.source/'manifest.json').read_bytes())


try:
    from PySide6.QtWidgets import QApplication, QInputDialog
    from swordx.recorder import RecorderDialog
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False


@unittest.skipUnless(GUI_AVAILABLE, 'PySide6 unavailable')
class DuplicateWorkflowGuiTests(unittest.TestCase):
    setUp = DuplicateWorkflowTests.setUp

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_duplicate_button_opens_new_editable_copy(self):
        with patch('swordx.workflows.data_dir', return_value=self.root), \
             patch.object(QInputDialog, 'getText', return_value=('Original (copy)', True)):
            editor = RecorderDialog('', 'Original')
            try:
                self.assertTrue(editor.duplicate.isEnabled())
                editor.duplicate.click()
                self.assertEqual(editor.workflows.currentText(), 'Original (copy)')
                self.assertEqual(editor.steps.count(), 2)
                self.assertIn('Original', list_workflows(self.root))
                self.assertIn('Original (copy)', list_workflows(self.root))
            finally:
                editor.close()


if __name__ == '__main__':
    unittest.main()
