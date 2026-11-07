"""Offscreen Qt checks for visual-only zone, OCR and gesture authoring."""
from __future__ import annotations

import os
import unittest

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLabel, QSpinBox
    from omniflow.studio import ActionEditor, RuleEditor, ZoneCaptureDialog
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, 'PySide6 unavailable')
class VisualStudioGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.frame = np.full((960, 540, 3), 75, dtype=np.uint8)

    def test_clicking_screenshot_yields_normalized_touch_point(self):
        dialog = ZoneCaptureDialog(self.frame, 'point')
        dialog.show()
        self.app.processEvents()
        rect = dialog.canvas._rect()
        QTest.mouseClick(dialog.canvas, Qt.LeftButton, Qt.NoModifier, rect.center())
        selected = dialog.result_selection()
        self.assertLess(abs(selected[0] - 270), 5)
        self.assertLess(abs(selected[1] - 480), 5)
        dialog.close()

    def test_actions_offer_no_manual_xy_fields(self):
        dialog = ActionEditor(frame=self.frame)
        self.assertEqual(dialog.kind.currentData(), 'tap')
        self.assertEqual(len(dialog.findChildren(QSpinBox)), 3)  # timing and key only
        with self.assertRaisesRegex(ValueError, 'Select the tap'):
            dialog.result_action()
        dialog.selection = [210, 330]
        self.assertEqual(dialog.result_action(), {'kind': 'tap', 'point': [210, 330]})
        dialog.kind.setCurrentIndex(dialog.kind.findData('swipe'))
        with self.assertRaisesRegex(ValueError, 'Draw the swipe'):
            dialog.result_action()
        dialog.selection = {'start': [200, 400], 'end': [150, 160]}
        self.assertEqual(dialog.result_action()['end'], [150, 160])
        dialog.close()

    def test_ocr_autofill_populates_the_selected_zone(self):
        from unittest.mock import patch
        dialog = RuleEditor(frame=self.frame)
        dialog.bounds = [110, 220, 350, 290]
        with patch('omniflow.engine.OCREngine.read', return_value='Claim now') as read:
            dialog._read_ocr()
        read.assert_called_once()
        self.assertEqual(read.call_args.args[0].shape, (70, 240, 3))
        self.assertEqual(dialog.text.text(), 'Claim now')
        self.assertEqual(dialog.result_rule()['text'], 'Claim now')
        dialog.close()

    def test_ocr_and_color_rules_have_no_coordinate_entry(self):
        dialog = RuleEditor(frame=self.frame)
        self.assertEqual(len(dialog.findChildren(QSpinBox)), 1)  # tolerance only
        with self.assertRaisesRegex(ValueError, 'Draw the detection zone'):
            dialog.result_rule()
        dialog.bounds = [100, 200, 230, 300]
        dialog.text.setText('Ready')
        self.assertEqual(dialog.result_rule()['text'], 'Ready')
        dialog.kind.setCurrentIndex(dialog.kind.findData('color'))
        result = dialog.result_rule()
        self.assertEqual(result['bgr'], [75, 75, 75])
        dialog.close()


if __name__ == '__main__':
    unittest.main()