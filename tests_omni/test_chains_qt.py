"""Offscreen GUI checks for persisted chain priority and dashboard handoff."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication
    from omniflow.chains import read_chains
    from omniflow.chains_ui import ChainLibraryDialog
    from omniflow.ui import OmniWindow
    from swordx.workflows import create_workflow, record_step
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, 'PySide6 required for chain GUI test')
class ChainGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workflows = {}
        for name, seed in [('A', 3), ('B', 6)]:
            folder = create_workflow(name, self.root)
            self.workflows[name] = folder
            frame = np.full((960, 540, 3), 30, np.uint8)
            frame[80:140, 100:180] = np.random.default_rng(seed).integers(
                20, 250, (60, 80, 3), dtype=np.uint8)
            record_step(folder, 'Start', frame, [(100, 80, 180, 140)],
                        taps=[(130, 95)])
            frame[80:140, 100:180] = np.random.default_rng(seed + 1).integers(
                20, 250, (60, 80, 3), dtype=np.uint8)
            record_step(folder, 'Completed', frame, [(100, 80, 180, 140)],
                        taps=[(140, 100)])

    def test_edit_priority_modes_and_save_then_reload(self):
        dialog = ChainLibraryDialog(self.root, self.workflows)
        self.addCleanup(dialog.close)
        dialog.name.setText('Daily loop')
        dialog.workflow.setCurrentText('A')
        dialog._add()
        dialog.workflow.setCurrentText('B')
        dialog.mode.setCurrentIndex(dialog.mode.findData('count'))
        dialog.limit.setValue(3)
        dialog._add()
        self.assertEqual(dialog.rows.count(), 2)
        dialog._move(-1)
        dialog.repeat.setChecked(False)
        with patch('omniflow.chains_ui.QMessageBox.information'):
            dialog._save()
        saved = read_chains(self.root)['Daily loop']
        self.assertEqual([row['workflow'] for row in saved['steps']], ['B', 'A'])
        self.assertEqual(saved['steps'][0]['mode'], 'count')
        self.assertEqual(saved['steps'][0]['limit'], 3)
        self.assertFalse(saved['repeat'])
        self.assertTrue((self.root / 'chains.json').exists())

    def test_dashboard_offers_saved_chain_without_starting_emulator(self):
        first = ChainLibraryDialog(self.root, self.workflows)
        first.name.setText('Routine')
        first.workflow.setCurrentText('A')
        first._add()
        with patch('omniflow.chains_ui.QMessageBox.information'):
            first._save()
        first.close()
        with (patch('omniflow.ui.root_dir', return_value=self.root),
              patch('omniflow.ui.DeviceScan.start')):
            window = OmniWindow()
        try:
            self.assertGreaterEqual(window.chain_combo.findText('Routine'), 0)
            window.chain_combo.setCurrentText('Routine')
            window.serial.setCurrentText('127.0.0.1:16384')
            window.package.setText('com.example')
            with (patch.object(window.manager, 'start_chain',
                               return_value=(True, 'Chain started')) as start,
                  patch('omniflow.ui.save_preferences') as persist):
                window._start_chain()
                self.assertEqual(start.call_count, 1)
                self.assertEqual(start.call_args.args[2].name, 'Routine')
                persist.assert_called_once()
        finally:
            window.close()


if __name__ == '__main__':
    unittest.main()
