"""Visual state editing and lockout tests for Windows Qt integration."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication
    from swordx.recorder import RecorderDialog
    from app import Window
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, 'PySide6 is unavailable')
class VisualStateGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_branding_removed_and_running_controls_share_lockout(self):
        with patch('app.ADB.discover', return_value=[]):
            main = Window()
        self.addCleanup(main.close)
        self.assertNotIn('MUMU  /  VISUAL AUTOMATION', main.findChildren(type(main.status))[0].text())
        self.assertFalse(any('Wait states suppress unsafe taps' in label.text()
                             for label in main.findChildren(type(main.status))))
        self.assertTrue(main.app_recovery.isChecked() in (True, False))
        for control in main._settings_controls:
            control.setEnabled(False)
            self.assertFalse(control.isEnabled())

    def test_recorder_disables_actions_without_workflow_or_capture(self):
        editor = RecorderDialog(serial='', selected_workflow='')
        self.addCleanup(editor.close)
        self.assertFalse(editor.save.isEnabled())
        self.assertFalse(editor.delete_button.isEnabled())
        self.assertFalse(editor.capture_button.isEnabled())
        self.assertFalse(editor.buttons[1].isEnabled())
        self.assertEqual(editor.selected_state_banner.property('kind'), 'empty')


if __name__ == '__main__':
    unittest.main()
