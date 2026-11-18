"""Windows desktop integration smoke tests (skipped if Qt is unavailable)."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication
    from app import Window
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, 'PySide6 is not installed in the test environment')
class DashboardGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fake_devices = patch('app.ADB.discover', return_value=[])
        self.fake_devices.start()
        self.addCleanup(self.fake_devices.stop)
        self.window = Window()
        self.addCleanup(self.window.close)

    def test_session_metrics_not_total(self):
        self.assertEqual(self.window.run_count.text(), '0')
        self.assertEqual(self.window.average_run.text(), '—')
        self.assertFalse(hasattr(self.window, 'session_count'))

    def test_advanced_recovery_collapses(self):
        self.assertTrue(self.window.advanced.isHidden())
        self.window.advanced_toggle.click()
        self.assertFalse(self.window.advanced.isHidden())
        self.window.advanced_toggle.click()
        self.assertTrue(self.window.advanced.isHidden())

    def test_recognition_badge_colors_follow_state(self):
        self.window._state_seen('Start match', .99)
        self.assertEqual(self.window.state_badge.property('level'), 'ok')
        self.window._state_seen('Unrecognized / battle in progress', .1)
        self.assertEqual(self.window.state_badge.property('level'), 'unknown')


if __name__ == '__main__':
    unittest.main()
