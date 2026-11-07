"""Offscreen Qt checks for visual-only zone, OCR and gesture authoring."""
from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QSpinBox
    from omniflow.studio import (ActionEditor, RuleEditor, ZoneCaptureDialog,
                                 OmniStudio, ReplayResultsDialog)
    from swordx.workflows import create_workflow, record_step
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

    def test_action_kind_switch_preserves_only_compatible_visual_targets(self):
        dialog = ActionEditor(frame=self.frame)
        self.addCleanup(dialog.close)
        dialog.selection = [210, 330]
        dialog.kind.setCurrentIndex(dialog.kind.findData('wait'))
        self.assertIsNone(dialog.selection)
        self.assertEqual(dialog.result_action()['kind'], 'wait')
        dialog.kind.setCurrentIndex(dialog.kind.findData('swipe'))
        self.assertIsNone(dialog.selection)  # Tap must not become a swipe.
        dialog.selection = {'start': [200, 400], 'end': [150, 160]}
        dialog.kind.setCurrentIndex(dialog.kind.findData('tap'))
        self.assertEqual(dialog.result_action()['point'], [210, 330])
        dialog.kind.setCurrentIndex(dialog.kind.findData('swipe'))
        self.assertEqual(dialog.result_action()['end'], [150, 160])
        dialog.kind.setCurrentIndex(dialog.kind.findData('back'))
        self.assertIsNone(dialog.selection)
        self.assertEqual(dialog.result_action(), {'kind': 'key', 'code': 4})
        dialog.kind.setCurrentIndex(dialog.kind.findData('hold'))
        self.assertEqual(dialog.result_action()['point'], [210, 330])
        dialog.kind.setCurrentIndex(dialog.kind.findData('field_text'))
        dialog.text.setText('Ready')
        self.assertEqual(dialog.result_action()['point'], [210, 330])

    def test_text_field_action_has_visual_target_and_validates_text(self):
        dialog = ActionEditor(frame=self.frame)
        dialog.kind.setCurrentIndex(dialog.kind.findData('field_text'))
        dialog.text.setText('Test 123')
        with self.assertRaisesRegex(ValueError, 'Select the input field'):
            dialog.result_action()
        dialog.selection = [210, 330]
        self.assertEqual(dialog.result_action(), {
            'kind': 'field_text', 'point': [210, 330], 'text': 'Test 123'
        })
        dialog.text.setText('unsafe;command')
        with self.assertRaisesRegex(ValueError, 'IME adapter'):
            dialog.result_action()
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

    def test_invalid_ocr_regex_is_rejected_before_saving(self):
        dialog = RuleEditor(frame=self.frame)
        self.addCleanup(dialog.close)
        dialog.bounds = [100, 200, 230, 300]
        dialog.text.setText('(')
        dialog.regex.setChecked(True)
        with self.assertRaisesRegex(ValueError, 'Invalid OCR regular expression'):
            dialog.result_rule()
        dialog.text.setText(r'CLAIM\\s+NOW')
        self.assertTrue(dialog.result_rule()['regex'])

    def test_switching_color_to_pixel_requires_fresh_target_and_color(self):
        frame = self.frame.copy()
        frame[210:235, 110:135] = (10, 20, 30)
        dialog = RuleEditor(
            initial={'type': 'color', 'rect': [100, 200, 150, 250],
                     'bgr': [1, 2, 3]}, frame=frame)
        self.addCleanup(dialog.close)
        # Opening an existing rule must not silently alter its saved color.
        self.assertEqual(dialog.result_rule()['bgr'], [1, 2, 3])
        dialog.kind.setCurrentIndex(dialog.kind.findData('pixel'))
        self.assertIsNone(dialog.point)
        self.assertIsNone(dialog.bounds)
        self.assertIn('Select a pixel', dialog.zone_status.text())
        with self.assertRaisesRegex(ValueError, 'Draw the detection zone'):
            dialog.result_rule()
        with patch('omniflow.studio.ZoneCaptureDialog') as picker:
            picker.return_value.exec.return_value = QDialog.Accepted
            picker.return_value.result_selection.return_value = [120, 220]
            dialog._pick()
        self.assertEqual(dialog.result_rule()['point'], [120, 220])
        self.assertEqual(dialog.result_rule()['bgr'], [10, 20, 30])
        self.assertEqual(dialog.zone_status.text(), 'Pixel selected')

        dialog.kind.setCurrentIndex(dialog.kind.findData('color'))
        with self.assertRaisesRegex(ValueError, 'Draw the detection zone'):
            dialog.result_rule()
        with patch('omniflow.studio.ZoneCaptureDialog') as picker:
            picker.return_value.exec.return_value = QDialog.Accepted
            picker.return_value.result_selection.return_value = [200, 300, 250, 350]
            dialog._pick()
        self.assertEqual(dialog.result_rule()['bgr'], [75, 75, 75])
        self.assertNotIn('point', dialog.result_rule())
        self.assertEqual(dialog.zone_status.text(), 'Zone selected')

    def test_ocr_to_color_uses_same_zone_but_samples_its_own_color(self):
        frame = self.frame.copy()
        frame[210:240, 110:140] = (10, 20, 30)
        dialog = RuleEditor(
            initial={'type': 'ocr', 'rect': [110, 210, 140, 240],
                     'text': 'Ready'}, frame=frame)
        self.addCleanup(dialog.close)
        dialog.kind.setCurrentIndex(dialog.kind.findData('color'))
        self.assertEqual(dialog.result_rule()['rect'], [110, 210, 140, 240])
        self.assertEqual(dialog.result_rule()['bgr'], [10, 20, 30])
        dialog.kind.setCurrentIndex(dialog.kind.findData('ocr'))
        self.assertEqual(dialog.result_rule()['text'], 'Ready')
        dialog.kind.setCurrentIndex(dialog.kind.findData('color'))
        self.assertEqual(dialog.result_rule()['bgr'], [10, 20, 30])

    def test_unsaved_studio_rule_preview_does_not_change_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('PreviewDraft', root)
            manifest = (folder / 'manifest.json').read_bytes()
            studio = OmniStudio('', 'PreviewDraft', base=root)
            try:
                studio.canvas.load(self.frame)
                studio.needs_recapture = False
                studio.extra_conditions = [
                    {'type': 'color', 'rect': [100, 200, 230, 300],
                     'bgr': [75, 75, 75], 'tolerance': 0},
                    {'type': 'pixel', 'rect': [100, 200, 230, 300],
                     'point': [110, 220], 'bgr': [75, 75, 75],
                     'tolerance': 0, 'negate': True},
                ]
                studio._refresh_extra()
                with patch('omniflow.devices.Device.screenshot') as capture:
                    studio._test_draft_conditions()
                    capture.assert_not_called()
                message = studio.rule_test_status.text()
                self.assertIn('1/2 conditions match', message)
                self.assertIn('PASS', message)
                self.assertIn('FAIL', message)
                self.assertIn('nothing was saved or tapped', message)
                self.assertEqual((folder / 'manifest.json').read_bytes(), manifest)
            finally:
                studio.close()

    def test_saved_studio_inspection_explains_states_without_input_or_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('SavedInspect', root)
            # Recorded image anchors require distinctive artwork; a flat
            # screenshot is intentionally rejected by the real recorder.
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            record_step(folder, 'Recognized state', image,
                        [(100, 200, 230, 300)], taps=[(150, 250)])
            manifest = (folder / 'manifest.json').read_bytes()
            studio = OmniStudio('', 'SavedInspect', base=root)
            try:
                studio.canvas.load(image)
                with patch('omniflow.studio.QMessageBox') as box, \
                     patch('omniflow.devices.Device.screenshot') as capture:
                    studio._test_detection()
                    capture.assert_not_called()
                info = box.return_value
                self.assertIn('No actions were executed', info.setInformativeText.call_args.args[0])
                details = info.setDetailedText.call_args.args[0]
                self.assertIn('Recognized state: selected', details)
                self.assertIn('Image ', details)
                info.exec.assert_called_once()
                self.assertEqual((folder / 'manifest.json').read_bytes(), manifest)
            finally:
                studio.close()

    def test_offline_replay_uses_saved_images_not_emulator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('ReplayOffline', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            record_step(folder, 'Recorded screen', image,
                        [(100, 200, 230, 300)], taps=[(150, 250)])
            before = {p.relative_to(folder): p.read_bytes() for p in
                      folder.rglob('*') if p.is_file()}
            studio = OmniStudio('127.0.0.1:16384', 'ReplayOffline', base=root)
            try:
                with patch.object(ReplayResultsDialog, 'exec') as shown, \
                     patch('omniflow.devices.Device.screenshot') as capture:
                    studio._replay_saved_screenshots()
                    capture.assert_not_called()
                    shown.assert_called_once()
                self.assertEqual(
                    before, {p.relative_to(folder): p.read_bytes()
                             for p in folder.rglob('*') if p.is_file()})
            finally:
                studio.close()

    def test_replay_dialog_shows_overlaps_without_hiding_failing_states(self):
        rows = [
            {'expected': 'Waiting for match', 'status': 'matched',
             'actual': 'Waiting for match', 'reason': 'Recorded state selected',
             'overlaps': [{'key': 'tap', 'label': 'Press Match', 'passive': False}],
             'action_plan': ['WAIT — no input will be sent']},
            {'expected': 'Press Match', 'status': 'different',
             'actual': 'Waiting for match', 'reason': 'overridden by WAIT',
             'overlaps': [{'key': 'tap', 'label': 'Press Match', 'passive': False}]},
            {'expected': 'Legacy state', 'status': 'unavailable',
             'actual': '', 'reason': 'No saved screenshot',
             'overlaps': []},
        ]
        dialog = ReplayResultsDialog(rows)
        self.addCleanup(dialog.close)
        self.assertIn('1/2 readable screenshots', dialog.summary.text())
        self.assertIn('2 with overlapping matches', dialog.counts.text())
        self.assertIn('1 unavailable', dialog.counts.text())
        self.assertEqual(dialog.table.rowCount(), 3)
        self.assertEqual(dialog.table.columnCount(), 6)
        self.assertEqual(dialog.table.item(0, 5).text(),
                         'WAIT — no input will be sent')
        self.assertEqual(dialog.table.item(1, 5).text(), '—')
        self.assertEqual(dialog.table.item(0, 1).text(), 'MATCHED')
        self.assertEqual(dialog.table.item(0, 3).text(), 'Press Match')
        self.assertEqual(dialog.table.item(1, 1).text(), 'DIFFERENT')
        self.assertEqual(dialog.table.item(1, 4).text(), 'overridden by WAIT')
        self.assertEqual(dialog.table.item(2, 1).text(), 'UNAVAILABLE')

    def test_draft_condition_undo_redo_restores_selection_and_never_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('DraftHistory', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            record_step(folder, 'Ready', image, [(100, 200, 230, 300)],
                        taps=[(150, 250)])
            before = (folder / 'manifest.json').read_bytes()
            studio = OmniStudio('', 'DraftHistory', base=root)
            try:
                studio.steps.setCurrentRow(0)
                rule = {'type': 'ocr', 'rect': [100, 200, 230, 300],
                        'text': 'READY'}
                with patch('omniflow.studio.RuleEditor') as editor:
                    editor.return_value.exec.return_value = QDialog.Accepted
                    editor.return_value.result_rule.return_value = rule
                    studio._add_rule()
                self.assertEqual(studio.extra_conditions, [rule])
                self.assertEqual(studio.rules_list.currentRow(), 0)
                self.assertTrue(studio._undo_buttons[0].isEnabled())
                studio._undo_edit()
                self.assertEqual(studio.extra_conditions, [])
                self.assertTrue(studio._redo_buttons[0].isEnabled())
                studio._redo_edit()
                self.assertEqual(studio.extra_conditions, [rule])
                self.assertEqual(studio.rules_list.currentRow(), 0)

                studio.rules_only.setChecked(True)
                studio._undo_edit()
                self.assertFalse(studio.rules_only.isChecked())
                studio._redo_edit()
                self.assertTrue(studio.rules_only.isChecked())

                studio.rules_list.setCurrentRow(0)
                studio._remove_rule()
                self.assertFalse(studio.extra_conditions)
                studio._undo_edit()
                self.assertEqual(studio.extra_conditions, [rule])
                self.assertEqual(studio.rules_list.currentRow(), 0)
                self.assertEqual((folder / 'manifest.json').read_bytes(), before)
                self.assertTrue(studio._dirty)
            finally:
                with patch('swordx.recorder.QMessageBox.question',
                           return_value=QMessageBox.Yes):
                    studio.close()

    def test_action_undo_redo_reorder_reset_and_save_history_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('ActionHistory', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            record_step(folder, 'Ready', image, [(100, 200, 230, 300)],
                        taps=[(150, 250)])
            before = (folder / 'manifest.json').read_bytes()
            studio = OmniStudio('', 'ActionHistory', base=root)
            try:
                studio.steps.setCurrentRow(0)
                self.assertIsNone(studio.extra_chain)
                with patch('omniflow.studio.ActionEditor') as editor:
                    editor.return_value.exec.return_value = QDialog.Accepted
                    editor.return_value.result_action.return_value = {
                        'kind': 'wait', 'seconds': 2}
                    studio._add_action()
                self.assertEqual(len(studio.extra_chain), 2)
                self.assertEqual(studio.actions_list.currentRow(), 1)
                studio._move_action(-1)
                self.assertEqual(studio.extra_chain[0]['kind'], 'wait')
                self.assertEqual(studio.actions_list.currentRow(), 0)
                studio._reset_chain()
                self.assertIsNone(studio.extra_chain)
                studio._undo_edit()
                self.assertEqual(studio.extra_chain[0]['kind'], 'wait')
                studio._undo_edit()
                self.assertEqual(studio.extra_chain[1]['kind'], 'wait')
                studio._undo_edit()
                self.assertIsNone(studio.extra_chain)
                studio._redo_edit()
                self.assertEqual(studio.extra_chain[1]['kind'], 'wait')
                studio._redo_edit()
                self.assertEqual(studio.extra_chain[0]['kind'], 'wait')
                self.assertEqual((folder / 'manifest.json').read_bytes(), before)

                studio._save()
                self.assertFalse(studio._dirty)
                self.assertFalse(studio._undo_buttons[0].isEnabled())
                self.assertFalse(studio._redo_buttons[0].isEnabled())
                saved = json.loads((folder / 'manifest.json').read_text())
                key = studio.editing_key
                self.assertEqual(saved['actions'][key]['action_chain'][0]['kind'], 'wait')
                studio._undo_edit()  # History must not leak across saves.
                self.assertEqual(studio.extra_chain[0]['kind'], 'wait')
            finally:
                with patch('swordx.recorder.QMessageBox.question',
                           return_value=QMessageBox.Yes):
                    studio.close()

    def test_settings_only_studio_save_is_atomic_and_preserves_templates(self):
        from swordx import workflows
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('SingleSave', root)
            image = self.frame.copy()
            image[215:275, 115:200] = (5, 230, 25)
            key = record_step(folder, 'Ready', image, [(100, 200, 230, 300)],
                              taps=[(150, 250)])
            captures_before = {p.name: p.read_bytes() for p in
                               (folder / 'captures').iterdir()}
            anchors_before = {p.name: p.read_bytes() for p in
                              (folder / 'anchors').iterdir()}
            studio = OmniStudio('', 'SingleSave', base=root)
            try:
                studio.steps.setCurrentRow(0)
                self.assertEqual(studio.editing_key, key)
                self.assertFalse(studio._visual_dirty)
                studio.extra_conditions = [
                    {'type': 'color', 'rect': [100, 200, 230, 300],
                     'bgr': [75, 75, 75], 'tolerance': 200}]
                studio.extra_chain = [{'kind': 'tap', 'point': [150, 250]}]
                studio._refresh_extra()
                studio._field_changed()
                with patch('swordx.workflows._write_manifest',
                           wraps=workflows._write_manifest) as save:
                    studio._save()
                    save.assert_called_once()
                data = json.loads((folder / 'manifest.json').read_text())
                self.assertEqual(data['actions'][key]['conditions'],
                                 studio.extra_conditions)
                self.assertEqual(data['actions'][key]['action_chain'],
                                 studio.extra_chain)
                self.assertEqual({p.name: p.read_bytes() for p in
                                  (folder / 'captures').iterdir()}, captures_before)
                self.assertEqual({p.name: p.read_bytes() for p in
                                  (folder / 'anchors').iterdir()}, anchors_before)
                self.assertFalse(studio._dirty)
            finally:
                studio.close()

    def test_cancel_new_state_preserves_unsaved_rules_and_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('CancelEdit', root)
            image = self.frame.copy()
            image[215:275, 115:200] = (5, 230, 25)
            key = record_step(folder, 'Ready', image, [(100, 200, 230, 300)],
                              taps=[(150, 250)])
            original = (folder / 'manifest.json').read_bytes()
            studio = OmniStudio('', 'CancelEdit', base=root)
            try:
                studio.steps.setCurrentRow(0)
                studio.extra_conditions = [
                    {'type': 'ocr', 'rect': [100, 200, 230, 300], 'text': 'READY'}]
                studio.extra_chain = [{'kind': 'tap', 'point': [120, 240]}]
                studio._refresh_extra()
                studio._field_changed()
                with patch('swordx.recorder.QMessageBox.question',
                           return_value=QMessageBox.No) as prompt:
                    studio._new_state()
                    prompt.assert_called_once()
                self.assertEqual(studio.editing_key, key)
                self.assertTrue(studio._dirty)
                self.assertEqual(studio.extra_conditions[0]['text'], 'READY')
                self.assertEqual(studio.extra_chain[0]['point'], [120, 240])
                self.assertEqual((folder / 'manifest.json').read_bytes(), original)
            finally:
                with patch('swordx.recorder.QMessageBox.question',
                           return_value=QMessageBox.Yes):
                    studio.close()

    def test_disabling_condition_only_without_template_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('ConditionOnly', root)
            key = record_step(
                folder, 'Ready', self.frame, [],
                conditions=[{'type': 'color', 'rect': [100, 200, 230, 300],
                             'bgr': [75, 75, 75]}], condition_only=True,
                action_chain=[{'kind': 'tap', 'point': [120, 240]}])
            original = (folder / 'manifest.json').read_bytes()
            studio = OmniStudio('', 'ConditionOnly', base=root)
            try:
                studio.steps.setCurrentRow(0)
                self.assertEqual(studio.editing_key, key)
                self.assertTrue(studio.rules_only.isChecked())
                studio.rules_only.setChecked(False)
                with patch('omniflow.studio.QMessageBox.warning') as warn:
                    studio._save()
                    self.assertIn('Draw a detection region', warn.call_args.args[2])
                self.assertEqual((folder / 'manifest.json').read_bytes(), original)
                self.assertTrue(studio._dirty)
            finally:
                with patch('swordx.recorder.QMessageBox.question',
                           return_value=QMessageBox.Yes):
                    studio.close()

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