"""Windows Qt smoke tests; skipped when PySide6 is unavailable."""
import os
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
try:
    from PySide6.QtWidgets import QApplication
    from omniflow.ui import OmniWindow, ScheduleDialog
    from omniflow.studio import OmniStudio
    from swordx.workflows import create_workflow
    QT=True
except ImportError:
    QT=False


@unittest.skipUnless(QT,'PySide6 is required for Windows GUI tests')
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_window_can_build_all_four_tabs_without_an_emulator(self):
        with patch('omniflow.ui.root_dir',return_value=self.root), \
             patch('omniflow.ui.DeviceScan.start'):
            window=OmniWindow()
        self.addCleanup(window.close)
        self.assertEqual(window.tabs.count(),4)
        self.assertIn('Scheduling',window.tabs.tabText(1))
        self.assertEqual(window.summary.text(),'0 / 10 SESSIONS')

    def test_schedule_dialog_values_and_weekday_fields(self):
        dlg=ScheduleDialog(['127.0.0.1:16384'],['Example workflow'])
        self.addCleanup(dlg.close)
        dlg.name.setText('Daily')
        dlg.package.setText('com.example.app')
        dlg.kind.setCurrentIndex(dlg.kind.findData('daily'))
        values=dlg.values()
        self.assertEqual(values['kind'],'daily')
        self.assertEqual(values['serial'],'127.0.0.1:16384')
        self.assertEqual(values['timezone_name'],'America/Los_Angeles')

    def test_studio_can_open_existing_custom_workflow(self):
        create_workflow('OldWorkflow',self.root)
        dialog=OmniStudio('', 'OldWorkflow',base=self.root)
        self.addCleanup(dialog.close)
        self.assertGreaterEqual(dialog.tabs.count(),4)
        self.assertEqual(dialog.workflows.currentText(),'OldWorkflow')

if __name__=='__main__':unittest.main()
