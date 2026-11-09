"""Studio moving image search is visually configurable and persisted."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication
    from omniflow.studio import OmniStudio, ActionEditor
    from swordx.workflows import create_workflow, record_step
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, 'PySide6 is required for Studio UI tests')
class MovingImageStudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.folder = create_workflow('MovingButtons', self.base)
        self.frame = np.full((960, 540, 3), 10, dtype=np.uint8)
        self.frame[170:220, 90:150] = np.random.default_rng(21).integers(
            0, 255, (50, 60, 3), dtype=np.uint8)
        self.key = record_step(
            self.folder, 'Move me', self.frame, [(90, 170, 150, 220)],
            action_chain=[{'kind': 'tap_match'}],
            search_rect=[0, 0, 540, 960])

    def test_action_editor_requires_no_fixed_coordinates(self):
        dialog = ActionEditor({'kind': 'tap_match'})
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.kind.currentData(), 'tap_match')
        self.assertFalse(dialog.pick.isEnabled())
        self.assertEqual(dialog.result_action(), {'kind': 'tap_match'})

    def test_studio_load_edit_undo_and_atomic_save_search_area(self):
        studio = OmniStudio('', 'MovingButtons', base=self.base)
        self.addCleanup(studio.close)
        studio.steps.setCurrentRow(0)
        self.assertEqual(studio.editing_key, self.key)
        self.assertTrue(studio.search_enabled.isChecked())
        self.assertEqual(studio._search_rect_option(), [0, 0, 540, 960])
        self.assertEqual(studio.extra_chain, [{'kind': 'tap_match'}])
        studio.search_enabled.setChecked(False)
        self.assertIsNone(studio._search_rect_option())
        studio._undo_edit()
        self.assertTrue(studio.search_enabled.isChecked())
        studio._search_rect = [20, 80, 420, 820]
        studio._field_changed()
        before = {p.name: p.read_bytes() for p in
                  (self.folder / 'anchors').iterdir()}
        studio._save()
        saved = json.loads((self.folder / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['actions'][self.key]['search_rect'], [20, 80, 420, 820])
        self.assertEqual(
            before, {p.name: p.read_bytes() for p in
                     (self.folder / 'anchors').iterdir()})


if __name__ == '__main__':
    unittest.main()
