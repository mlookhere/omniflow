"""Dashboard displays no-input and action decisions without running an emulator."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication
    from omniflow.manager import Session
    from omniflow.ui import OmniWindow
    QT = True
except ImportError:
    QT = False

@unittest.skipUnless(QT, 'PySide6 is needed for dashboard tests')
class LiveFeedbackQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dashboard_shows_selected_session_decision(self):
        with tempfile.TemporaryDirectory() as path:
            with patch('omniflow.ui.root_dir', return_value=Path(path)), \
                 patch('omniflow.ui.DeviceScan.start'):
                window = OmniWindow()
            try:
                session = Session('127.0.0.1:16384', 'com.example.game',
                                  'Workflow', Path(path), 'owner')
                session.status = 'Running'
                session.decision = 'Unknown screen; no input'
                with patch.object(window.manager, 'snapshot', return_value=[session]), \
                     patch.object(window.manager, 'tick_schedules', return_value=[]), \
                     patch.object(window, '_toggle_video'):
                    window._tick()
                self.assertIn('Unknown screen; no input', window.session_info.text())
                session.record_decision('Passive WAIT state; no input')
                with (patch.object(window.manager, 'snapshot', return_value=[session]),
                      patch.object(window.manager, 'tick_schedules', return_value=[]),
                      patch.object(window, '_toggle_video')):
                    window._tick()
                self.assertIn('Decision · Waiting for image: Passive WAIT state; no input',
                              window.logs.toPlainText())
            finally:
                window.close()

if __name__ == '__main__':
    unittest.main()
