"""UI checks for removed duplicate indicators and visible number steppers."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication, QLabel, QSpinBox
    from swordx.recorder import RecorderDialog
    from app import Window, STYLE
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, 'PySide6 is unavailable')
class RefinedGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_no_pair_pills_in_workflow_editor(self):
        dialog = RecorderDialog(serial='', selected_workflow='')
        self.addCleanup(dialog.close)
        self.assertFalse(hasattr(dialog, 'pair_status'))
        self.assertFalse(any('PAIR 1' in label.text() or 'PAIR 2' in label.text()
                             for label in dialog.findChildren(QLabel)))
        self.assertEqual(len(dialog.buttons), 6)

    def test_number_steppers_have_real_bundled_arrow_images(self):
        self.assertIn('QSpinBox::up-arrow', STYLE)
        self.assertIn('QSpinBox::down-arrow', STYLE)
        self.assertNotIn('@@UP_ARROW@@', STYLE)
        for filename in ('spin-up.png', 'spin-down.png'):
            self.assertIn(filename, STYLE)
        dialog = RecorderDialog(serial='', selected_workflow='')
        self.addCleanup(dialog.close)
        self.assertGreaterEqual(len(dialog.findChildren(QSpinBox)), 5)

    def test_game_relaunch_checkbox_is_saved_with_profile(self):
        with patch('app.ADB.discover', return_value=[]):
            window = Window()
        self.addCleanup(window.close)
        self.assertTrue(window.game_recovery.isChecked() in (True, False))
        window.game_recovery.setChecked(False)
        self.assertFalse(window._capture_settings().auto_relaunch_game)
        window.game_recovery.setChecked(True)
        self.assertTrue(window._capture_settings().auto_relaunch_game)
        self.assertIn(window.game_recovery, window._settings_controls)


if __name__ == '__main__':
    unittest.main()
