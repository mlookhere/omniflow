"""Offscreen Qt checks for visual-only zone, OCR and gesture authoring."""
from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
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

    def test_draft_action_preview_redacts_text_and_never_uses_device_or_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('PreviewActions', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            record_step(folder, 'Ready', image, [(100, 200, 230, 300)],
                        taps=[(150, 250)])
            before = {p.relative_to(folder): p.read_bytes()
                      for p in folder.rglob('*') if p.is_file()}
            studio = OmniStudio('127.0.0.1:16384', 'PreviewActions', base=root)
            try:
                studio.steps.setCurrentRow(0)
                studio.extra_chain = [
                    {'kind': 'tap', 'point': [150, 250]},
                    {'kind': 'wait', 'seconds': 2},
                    {'kind': 'field_text', 'point': [150, 250],
                     'text': 'secret-987'},
                ]
                with (patch('omniflow.devices.Device.screenshot') as capture,
                      patch('swordx.workflows._write_manifest') as write):
                    studio._preview_draft_actions()
                    capture.assert_not_called()
                    write.assert_not_called()
                output = studio.action_preview_status.text()
                self.assertIn('1. Tap location selected on screen', output)
                self.assertIn('2. Wait 2 seconds', output)
                self.assertIn('3. Tap selected field and type text (contents hidden)', output)
                self.assertNotIn('secret-987', output)
                self.assertIn('no taps or saves', output)
                self.assertEqual(
                    before, {p.relative_to(folder): p.read_bytes()
                             for p in folder.rglob('*') if p.is_file()})
            finally:
                studio.close()

    def test_draft_action_preview_rejects_invalid_steps_and_honors_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('DraftSafety', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            record_step(folder, 'Ready', image, [(100, 200, 230, 300)],
                        taps=[(150, 250)])
            before = (folder / 'manifest.json').read_bytes()
            studio = OmniStudio('', 'DraftSafety', base=root)
            try:
                studio.steps.setCurrentRow(0)
                studio._preview_draft_actions()
                self.assertIn('Tap location selected on screen',
                              studio.action_preview_status.text())

                studio.extra_chain = [{'kind': 'tap', 'point': [700, 200]}]
                studio._preview_draft_actions()
                self.assertIn('Draft action invalid:',
                              studio.action_preview_status.text())
                self.assertIn('Nothing saved or tapped',
                              studio.action_preview_status.text())

                studio.passive.setChecked(True)
                studio._preview_draft_actions()
                self.assertIn('WAIT — no input will be sent',
                              studio.action_preview_status.text())
                self.assertNotIn('Draft action invalid',
                                 studio.action_preview_status.text())

                studio.passive.setChecked(False)
                studio.extra_chain = [{'kind': 'wait', 'seconds': 5}]
                studio._refresh_extra()
                self.assertIn('Unsaved actions can be checked',
                              studio.action_preview_status.text())
                self.assertEqual((folder / 'manifest.json').read_bytes(), before)
            finally:
                with patch('swordx.recorder.QMessageBox.question',
                           return_value=QMessageBox.Yes):
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

    def test_legacy_rule_or_action_editor_recapture_keeps_existing_overlays(self):
        for edit_kind in ('rule', 'action'):
            with self.subTest(edit_kind=edit_kind):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    folder = create_workflow('LegacyEditorCapture', root)
                    image = self.frame.copy()
                    image[220:270, 120:200] = (10, 230, 30)
                    image[415:450, 275:350] = (12, 210, 25)
                    boxes = [(100, 200, 230, 300), (250, 400, 390, 490)]
                    gestures = [
                        {'kind': 'tap', 'point': [150, 250]},
                        {'kind': 'drag', 'start': [290, 430],
                         'end': [340, 460], 'duration_ms': 900},
                    ]
                    key = record_step(folder, 'Legacy', image, boxes,
                                      gestures=gestures)
                    manifest = folder / 'manifest.json'
                    saved = json.loads(manifest.read_text(encoding='utf-8'))
                    (folder / 'captures' / saved['actions'][key]['capture']).unlink()
                    before = manifest.read_bytes()
                    studio = OmniStudio('127.0.0.1:16384',
                                       'LegacyEditorCapture', base=root)
                    try:
                        studio.steps.setCurrentRow(0)
                        self.assertEqual(studio.editing_key, key)
                        self.assertTrue(studio.needs_recapture)
                        self.assertEqual(studio.canvas.boxes, boxes)
                        self.assertEqual(studio.canvas.gestures, gestures)

                        updated_image = image.copy()
                        updated_image[230:260, 130:170] = (15, 210, 35)
                        editor_name = ('RuleEditor' if edit_kind == 'rule'
                                       else 'ActionEditor')
                        edit = studio._add_rule if edit_kind == 'rule' else studio._add_action
                        with (patch('omniflow.devices.Device.screenshot',
                                    return_value=updated_image) as capture,
                              patch(f'omniflow.studio.{editor_name}') as dialog,
                              patch('swordx.workflows._write_manifest') as write):
                            dialog.return_value.exec.return_value = QDialog.Rejected
                            edit()
                            capture.assert_called_once()
                            write.assert_not_called()
                        self.assertFalse(studio.needs_recapture)
                        self.assertEqual(studio.canvas.boxes, boxes)
                        self.assertEqual(studio.canvas.gestures, gestures)
                        self.assertEqual(studio.canvas.drag_durations, [700, 900])
                        np.testing.assert_array_equal(studio.canvas.frame, updated_image)
                        self.assertTrue(studio._visual_dirty)
                        self.assertTrue(studio._dirty)
                        self.assertEqual(manifest.read_bytes(), before)

                        studio._save()
                        updated = json.loads(manifest.read_text(encoding='utf-8'))
                        self.assertEqual(updated['states'][key][0]['rect'], list(boxes[0]))
                        self.assertEqual(updated['states'][key][1]['rect'], list(boxes[1]))
                        self.assertEqual(updated['actions'][key]['gestures'], gestures)
                        self.assertTrue((folder / 'captures' /
                                         updated['actions'][key]['capture']).is_file())
                        self.assertFalse(studio._dirty)
                    finally:
                        with patch('swordx.recorder.QMessageBox.question',
                                   return_value=QMessageBox.Yes):
                            studio.close()

    def test_studio_refuses_unsafe_state_capture_on_selection_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('UnsafeCaptureEdit', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            key = record_step(folder, 'Recorded screen', image,
                              [(100, 200, 230, 300)], taps=[(150, 250)])
            studio = OmniStudio('', 'UnsafeCaptureEdit', base=root)
            try:
                manifest = folder / 'manifest.json'
                data = json.loads(manifest.read_text(encoding='utf-8'))
                data['actions'][key]['capture'] = '../outside.png'
                manifest.write_text(json.dumps(data), encoding='utf-8')
                before = {p.relative_to(folder): p.read_bytes()
                          for p in folder.rglob('*') if p.is_file()}
                studio.steps.setCurrentRow(-1)
                with (patch('swordx.recorder.QMessageBox.warning') as warning,
                      patch('swordx.workflows.cv2.imread') as read,
                      patch('swordx.workflows._write_manifest') as write):
                    studio.steps.setCurrentRow(0)
                    warning.assert_called_once()
                    read.assert_not_called()
                    write.assert_not_called()
                self.assertIn('Unsafe saved captures',
                              warning.call_args.args[2])
                self.assertEqual(before, {
                    p.relative_to(folder): p.read_bytes()
                    for p in folder.rglob('*') if p.is_file()})
            finally:
                studio.close()

    def test_failed_saved_state_switch_restores_editor_and_list_selection(self):
        for from_saved in (False, True):
            with self.subTest(from_saved=from_saved):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    folder = create_workflow('InvalidSelection', root)
                    image = self.frame.copy()
                    image[220:270, 120:200] = (10, 230, 30)
                    first = record_step(folder, 'First', image,
                                        [(100, 200, 230, 300)],
                                        taps=[(150, 250)])
                    second = record_step(folder, 'Second', image,
                                         [(100, 200, 230, 300)],
                                         taps=[(150, 250)])
                    studio = OmniStudio('', 'InvalidSelection', base=root)
                    try:
                        if from_saved:
                            studio.steps.setCurrentRow(0)
                            self.assertEqual(studio.editing_key, first)
                            self.assertEqual(studio.step_name.text(), 'First')
                        else:
                            self.assertIsNone(studio.editing_key)
                            self.assertEqual(studio.steps.currentRow(), -1)
                        manifest = folder / 'manifest.json'
                        data = json.loads(manifest.read_text(encoding='utf-8'))
                        data['actions'][second]['capture'] = '../../outside.png'
                        manifest.write_text(json.dumps(data), encoding='utf-8')
                        before = manifest.read_bytes()
                        with (patch('swordx.recorder.QMessageBox.warning') as warning,
                              patch('swordx.workflows.cv2.imread') as read,
                              patch('swordx.workflows._write_manifest') as write):
                            studio.steps.setCurrentRow(1)
                            warning.assert_called_once()
                            read.assert_not_called()
                            write.assert_not_called()
                        self.assertIn('Unsafe saved captures', warning.call_args.args[2])
                        self.assertEqual(studio.steps.currentRow(),
                                         0 if from_saved else -1)
                        self.assertEqual(studio.editing_key,
                                         first if from_saved else None)
                        self.assertEqual(studio.step_name.text(),
                                         'First' if from_saved else '')
                        self.assertFalse(studio._dirty)
                        self.assertEqual(manifest.read_bytes(), before)
                    finally:
                        studio.close()

    def test_saved_capture_geometry_error_keeps_editor_target_and_replay_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('BadCaptureGeometry', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            first = record_step(folder, 'First', image,
                                [(100, 200, 230, 300)], taps=[(150, 250)])
            second = record_step(folder, 'Second', image,
                                 [(100, 200, 230, 300)], taps=[(150, 250)])
            studio = OmniStudio('', 'BadCaptureGeometry', base=root)
            try:
                studio.steps.setCurrentRow(0)
                self.assertEqual(studio.editing_key, first)
                manifest = folder / 'manifest.json'
                data = json.loads(manifest.read_text(encoding='utf-8'))
                capture = folder / 'captures' / data['actions'][second]['capture']
                self.assertTrue(cv2.imwrite(str(capture), image[:700, :500]))
                before = manifest.read_bytes()

                with patch('swordx.recorder.QMessageBox.warning') as warning:
                    studio.steps.setCurrentRow(1)
                    warning.assert_called_once()
                self.assertIn('normalized 540×960', warning.call_args.args[2])
                self.assertEqual(studio.steps.currentRow(), 0)
                self.assertEqual(studio.editing_key, first)
                self.assertEqual(studio.step_name.text(), 'First')
                self.assertFalse(studio._dirty)

                dialogs = []
                with (patch.object(ReplayResultsDialog, 'exec',
                                   new=lambda dlg: dialogs.append(dlg)),
                      patch('omniflow.devices.Device.screenshot') as device,
                      patch('swordx.workflows._write_manifest') as write):
                    studio._replay_saved_screenshots()
                    device.assert_not_called()
                    write.assert_not_called()
                self.assertEqual(len(dialogs), 1)
                self.assertEqual(dialogs[0].table.item(1, 1).text(), 'UNAVAILABLE')
                self.assertIn('normalized 540×960',
                              dialogs[0].table.item(1, 4).text())
                self.assertEqual(manifest.read_bytes(), before)
            finally:
                studio.close()

    def test_studio_failed_manifest_replace_warns_without_losing_saved_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('AtomicStudioFailure', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            key = record_step(folder, 'Before', image,
                              [(100, 200, 230, 300)], taps=[(150, 250)])
            manifest = folder / 'manifest.json'
            previous = manifest.read_bytes()
            studio = OmniStudio('', 'AtomicStudioFailure', base=root)
            try:
                studio.steps.setCurrentRow(0)
                studio.step_name.setText('After')
                with (patch('swordx.workflows.os.replace',
                            side_effect=OSError('simulated replacement failure')),
                      patch('swordx.recorder.QMessageBox.warning') as warning):
                    studio._save()
                    warning.assert_called_once()
                self.assertIn('simulated replacement failure',
                              warning.call_args.args[2])
                self.assertEqual(manifest.read_bytes(), previous)
                self.assertEqual(json.loads(manifest.read_text())['actions'][key]['label'],
                                 'Before')
                self.assertEqual(list(folder.glob('.manifest.json.*.tmp')), [])
            finally:
                with patch('swordx.recorder.QMessageBox.question',
                           return_value=QMessageBox.Yes):
                    studio.close()

    def test_studio_failed_capture_encoding_leaves_no_partial_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('PartialCaptureStudio', root)
            manifest = folder / 'manifest.json'
            before = manifest.read_bytes()
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            studio = OmniStudio('', 'PartialCaptureStudio', base=root)
            try:
                studio.canvas.load(image)
                studio.canvas.boxes[0] = (100, 200, 230, 300)
                studio.canvas.gestures[0] = {'kind': 'tap', 'point': [150, 250]}
                studio.step_name.setText('Ready')
                writes = []

                def interrupted_encoder(path, *_args):
                    target = Path(path)
                    writes.append(target)
                    target.write_bytes(b'partial encoded image')
                    return target.parent.name != 'captures'

                with (patch('swordx.workflows.cv2.imwrite',
                            side_effect=interrupted_encoder),
                      patch('swordx.workflows._write_manifest') as save,
                      patch('swordx.recorder.QMessageBox.warning') as warning):
                    studio._save()
                    warning.assert_called_once()
                    save.assert_not_called()
                self.assertIn('Cannot save state screenshot',
                              warning.call_args.args[2])
                self.assertEqual(len(writes), 2)
                self.assertTrue(all(not path.exists() for path in writes))
                self.assertFalse(list((folder / 'anchors').iterdir()))
                self.assertFalse(list((folder / 'captures').iterdir()))
                self.assertEqual(manifest.read_bytes(), before)
                self.assertEqual(studio.steps.count(), 0)
                self.assertTrue(studio._dirty)
            finally:
                with patch('swordx.recorder.QMessageBox.question',
                           return_value=QMessageBox.Yes):
                    studio.close()

    def test_studio_new_state_refuses_unsafe_screenshot_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('UnsafeStudioWrite', root)
            manifest = folder / 'manifest.json'
            before = manifest.read_bytes()
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            studio = OmniStudio('', 'UnsafeStudioWrite', base=root)
            try:
                studio.canvas.load(image)
                studio.canvas.boxes[0] = (100, 200, 230, 300)
                studio.canvas.gestures[0] = {'kind': 'tap', 'point': [150, 250]}
                studio.step_name.setText('Ready')
                with (patch('swordx.workflows._safe_saved_image',
                            side_effect=ValueError('Saved captures directory resolves outside workflow')),
                      patch('swordx.workflows.cv2.imwrite') as write,
                      patch('swordx.workflows._write_manifest') as save,
                      patch('swordx.recorder.QMessageBox.warning') as warning):
                    studio._save()
                    warning.assert_called_once()
                    write.assert_not_called()
                    save.assert_not_called()
                self.assertIn('Saved captures directory resolves outside workflow',
                              warning.call_args.args[2])
                self.assertEqual(manifest.read_bytes(), before)
            finally:
                studio.close()

    def test_studio_delete_requires_discard_of_unsaved_rules_and_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('GuardDeleteDraft', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            first = record_step(folder, 'First', image,
                                [(100, 200, 230, 300)], taps=[(150, 250)])
            second = record_step(folder, 'Second', image,
                                 [(100, 200, 230, 300)], taps=[(150, 250)])
            manifest = folder / 'manifest.json'
            before = {p.relative_to(folder): p.read_bytes()
                      for p in folder.rglob('*') if p.is_file()}
            studio = OmniStudio('', 'GuardDeleteDraft', base=root)
            try:
                studio.steps.setCurrentRow(0)
                studio.step_name.setText('Draft name')
                rule = {'type': 'ocr', 'rect': [100, 200, 230, 300],
                        'text': 'READY'}
                studio.extra_conditions = [rule]
                studio.extra_chain = [{'kind': 'tap', 'point': [150, 250]}]
                studio._refresh_extra()
                studio._field_changed()
                self.assertTrue(studio._dirty)
                with (patch('swordx.recorder.QMessageBox.question',
                            side_effect=[QMessageBox.Yes, QMessageBox.No]) as ask,
                      patch('swordx.recorder.remove_step') as remove):
                    studio._delete()
                    self.assertEqual(ask.call_count, 2)
                    self.assertEqual(ask.call_args_list[0].args[1], 'Delete state')
                    self.assertEqual(ask.call_args_list[1].args[1], 'Unsaved state')
                    remove.assert_not_called()
                self.assertEqual(
                    {p.relative_to(folder): p.read_bytes()
                     for p in folder.rglob('*') if p.is_file()}, before)
                self.assertEqual(studio.editing_key, first)
                self.assertEqual(studio.steps.currentRow(), 0)
                self.assertEqual(studio.step_name.text(), 'Draft name')
                self.assertEqual(studio.extra_conditions, [rule])
                self.assertEqual(studio.extra_chain[0]['point'], [150, 250])
                self.assertTrue(studio._dirty)

                # A failed deletion must also retain the unsaved draft.
                with (patch('swordx.recorder.QMessageBox.question',
                            side_effect=[QMessageBox.Yes, QMessageBox.Yes]),
                      patch('swordx.recorder.remove_step',
                            side_effect=OSError('simulated deletion failure')),
                      patch('swordx.recorder.QMessageBox.warning') as warning):
                    studio._delete()
                    warning.assert_called_once()
                self.assertEqual(
                    {p.relative_to(folder): p.read_bytes()
                     for p in folder.rglob('*') if p.is_file()}, before)
                self.assertEqual(studio.extra_conditions, [rule])
                self.assertTrue(studio._dirty)

                with patch('swordx.recorder.QMessageBox.question',
                           side_effect=[QMessageBox.Yes, QMessageBox.Yes]) as ask:
                    studio._delete()
                    self.assertEqual(ask.call_count, 2)
                content = json.loads(manifest.read_text(encoding='utf-8'))
                self.assertNotIn(first, content['states'])
                self.assertEqual(list(content['states']), [second])
                self.assertEqual(studio.steps.count(), 1)
                self.assertIsNone(studio.editing_key)
                self.assertFalse(studio._dirty)
                self.assertFalse(studio.extra_conditions)
            finally:
                with patch('swordx.recorder.QMessageBox.question',
                           return_value=QMessageBox.Yes):
                    studio.close()

    def test_studio_delete_retains_images_used_by_remaining_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('SharedStudioDelete', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            first = record_step(folder, 'First', image,
                                [(100, 200, 230, 300)], taps=[(150, 250)])
            second = record_step(folder, 'Second', image,
                                 [(100, 200, 230, 300)], taps=[(150, 250)])
            manifest = folder / 'manifest.json'
            data = json.loads(manifest.read_text(encoding='utf-8'))
            anchor_name = data['states'][first][0]['file']
            capture_name = data['actions'][first]['capture']
            data['states'][second][0]['file'] = anchor_name
            data['actions'][second]['capture'] = capture_name
            manifest.write_text(json.dumps(data), encoding='utf-8')
            shared_anchor = folder / 'anchors' / anchor_name
            shared_capture = folder / 'captures' / capture_name
            original_anchor = shared_anchor.read_bytes()
            original_capture = shared_capture.read_bytes()
            studio = OmniStudio('', 'SharedStudioDelete', base=root)
            try:
                studio.steps.setCurrentRow(0)
                with (patch('swordx.recorder.QMessageBox.question',
                            return_value=QMessageBox.Yes),
                      patch('swordx.recorder.QMessageBox.warning') as warning):
                    studio._delete()
                    warning.assert_not_called()
                saved = json.loads(manifest.read_text(encoding='utf-8'))
                self.assertNotIn(first, saved['states'])
                self.assertEqual(saved['states'][second][0]['file'], anchor_name)
                self.assertEqual(saved['actions'][second]['capture'], capture_name)
                self.assertEqual(shared_anchor.read_bytes(), original_anchor)
                self.assertEqual(shared_capture.read_bytes(), original_capture)
            finally:
                studio.close()

    def test_studio_delete_succeeds_after_old_image_unlink_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('LockedOldImage', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            key = record_step(folder, 'Before', image,
                              [(100, 200, 230, 300)], taps=[(150, 250)])
            manifest = folder / 'manifest.json'
            saved = json.loads(manifest.read_text(encoding='utf-8'))
            old_anchor = folder / 'anchors' / saved['states'][key][0]['file']
            old_capture = folder / 'captures' / saved['actions'][key]['capture']
            old_assets = {old_anchor, old_capture}
            real_unlink = Path.unlink

            def blocked_unlink(path, *args, **kwargs):
                if path in old_assets:
                    raise PermissionError('simulated locked image')
                return real_unlink(path, *args, **kwargs)

            studio = OmniStudio('', 'LockedOldImage', base=root)
            try:
                studio.steps.setCurrentRow(0)
                with (patch('swordx.recorder.QMessageBox.question',
                            return_value=QMessageBox.Yes),
                      patch('swordx.recorder.QMessageBox.warning') as warning,
                      patch('swordx.workflows.Path.unlink', autospec=True,
                            side_effect=blocked_unlink),
                      self.assertLogs(level='WARNING') as logs):
                    studio._delete()
                    warning.assert_not_called()
                self.assertIn('simulated locked image', '\n'.join(logs.output))
                self.assertNotIn(key,
                                 json.loads(manifest.read_text())['states'])
                self.assertEqual(studio.steps.count(), 0)
                self.assertTrue(old_anchor.exists())
                self.assertTrue(old_capture.exists())
            finally:
                studio.close()

    def test_studio_delete_rejects_corrupt_image_cleanup_without_filesystem_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('UnsafeDelete', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            key = record_step(folder, 'Recorded screen', image,
                              [(100, 200, 230, 300)], taps=[(150, 250)])
            studio = OmniStudio('', 'UnsafeDelete', base=root)
            try:
                studio.steps.setCurrentRow(0)
                manifest = folder / 'manifest.json'
                data = json.loads(manifest.read_text(encoding='utf-8'))
                data['actions'][key]['capture'] = '../../outside.txt'
                manifest.write_text(json.dumps(data), encoding='utf-8')
                outside = folder.parent / 'outside.txt'
                outside.write_text('preserve me', encoding='utf-8')
                before = {p.relative_to(folder): p.read_bytes()
                          for p in folder.rglob('*') if p.is_file()}
                with (patch('swordx.recorder.QMessageBox.question',
                            return_value=QMessageBox.Yes),
                      patch('swordx.recorder.QMessageBox.warning') as warning,
                      patch('swordx.workflows._write_manifest') as write):
                    studio._delete()
                    warning.assert_called_once()
                    write.assert_not_called()
                self.assertIn('Unsafe saved captures', warning.call_args.args[2])
                self.assertEqual(outside.read_text(encoding='utf-8'), 'preserve me')
                self.assertEqual(before, {
                    p.relative_to(folder): p.read_bytes()
                    for p in folder.rglob('*') if p.is_file()})
            finally:
                studio.close()

    def test_saved_inspection_validates_flags_before_live_emulator_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('UnsafeOCRFlags', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            key = record_step(folder, 'Ready', image,
                              [(100, 200, 230, 300)], taps=[(150, 250)])
            studio = OmniStudio('', 'UnsafeOCRFlags', base=root)
            try:
                studio.canvas.load(image)
                studio.serial = '127.0.0.1:16384'
                manifest = folder / 'manifest.json'
                data = json.loads(manifest.read_text(encoding='utf-8'))
                data['actions'][key]['conditions'] = [
                    {'type': 'ocr', 'rect': [100, 200, 230, 300],
                     'text': 'READY', 'regex': 'false'}]
                manifest.write_text(json.dumps(data), encoding='utf-8')
                before = manifest.read_bytes()
                with (patch('omniflow.studio.QMessageBox.warning') as warning,
                      patch('omniflow.devices.Device') as device,
                      patch('swordx.workflows._write_manifest') as write):
                    studio._test_detection()
                    warning.assert_called_once()
                    device.assert_not_called()
                    write.assert_not_called()
                self.assertIn('Recognition regex must be true or false',
                              warning.call_args.args[2])
                self.assertEqual(manifest.read_bytes(), before)
            finally:
                studio.close()

    def test_saved_inspection_rejects_unsafe_matcher_configuration_without_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('UnsafeMatcher', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            key = record_step(folder, 'Recorded screen', image,
                              [(100, 200, 230, 300)], taps=[(150, 250)])
            studio = OmniStudio('', 'UnsafeMatcher', base=root)
            try:
                studio.canvas.load(image)
                manifest = folder / 'manifest.json'
                data = json.loads(manifest.read_text(encoding='utf-8'))
                data['actions'][key]['threshold'] = float('nan')
                manifest.write_text(json.dumps(data), encoding='utf-8')
                before = manifest.read_bytes()
                with (patch('omniflow.studio.QMessageBox.warning') as warning,
                      patch('omniflow.devices.Device.screenshot') as screenshot,
                      patch('swordx.workflows._write_manifest') as write):
                    studio._test_detection()
                    screenshot.assert_not_called()
                    write.assert_not_called()
                    warning.assert_called_once()
                self.assertIn(
                    'finite number', warning.call_args.args[2])
                self.assertEqual(manifest.read_bytes(), before)
            finally:
                studio.close()

    def test_saved_inspection_rejects_unsafe_retry_without_device_or_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('UnsafeRetry', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            key = record_step(folder, 'Recorded screen', image,
                              [(100, 200, 230, 300)], taps=[(150, 250)])
            studio = OmniStudio('', 'UnsafeRetry', base=root)
            try:
                studio.canvas.load(image)
                manifest = folder / 'manifest.json'
                data = json.loads(manifest.read_text(encoding='utf-8'))
                data['actions'][key]['retry'] = 'false'
                manifest.write_text(json.dumps(data), encoding='utf-8')
                before = {p.relative_to(folder): p.read_bytes()
                          for p in folder.rglob('*') if p.is_file()}
                with (patch('omniflow.studio.QMessageBox.warning') as warning,
                      patch('omniflow.devices.Device.screenshot') as screenshot,
                      patch('swordx.workflows._write_manifest') as write):
                    studio._test_detection()
                    screenshot.assert_not_called()
                    write.assert_not_called()
                    warning.assert_called_once()
                self.assertIn('retry must be true or false',
                              warning.call_args.args[2])
                self.assertEqual(before, {
                    p.relative_to(folder): p.read_bytes()
                    for p in folder.rglob('*') if p.is_file()})
            finally:
                studio.close()

    def test_saved_studio_inspection_rejects_unsafe_template_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('UnsafeTemplate', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            key = record_step(folder, 'Recorded screen', image,
                              [(100, 200, 230, 300)], taps=[(150, 250)])
            studio = OmniStudio('', 'UnsafeTemplate', base=root)
            try:
                studio.canvas.load(image)
                manifest = folder / 'manifest.json'
                data = json.loads(manifest.read_text(encoding='utf-8'))
                data['states'][key][0]['file'] = '../outside.png'
                manifest.write_text(json.dumps(data), encoding='utf-8')
                before = {p.relative_to(folder): p.read_bytes()
                          for p in folder.rglob('*') if p.is_file()}
                with (patch('omniflow.studio.QMessageBox.warning') as warning,
                      patch('omniflow.devices.Device.screenshot') as capture,
                      patch('swordx.workflows._write_manifest') as write):
                    studio._test_detection()
                    warning.assert_called_once()
                    capture.assert_not_called()
                    write.assert_not_called()
                self.assertIn('unsafe anchor filename',
                              warning.call_args.args[2])
                self.assertEqual(before, {
                    p.relative_to(folder): p.read_bytes()
                    for p in folder.rglob('*') if p.is_file()})
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

    def test_replay_shows_unavailable_when_capture_directory_redirected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('RedirectedReplay', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            record_step(folder, 'Ready', image, [(100, 200, 230, 300)],
                        taps=[(150, 250)])
            studio = OmniStudio('127.0.0.1:16384', 'RedirectedReplay', base=root)
            try:
                captures = folder / 'captures'
                outside = root / 'external_captures'
                captures.rename(outside)
                try:
                    captures.symlink_to(outside, target_is_directory=True)
                except (OSError, NotImplementedError):
                    outside.rename(captures)
                    self.skipTest('Directory symlinks not available')
                before = (folder / 'manifest.json').read_bytes()
                dialogs = []
                with (patch.object(ReplayResultsDialog, 'exec',
                                   new=lambda dialog: dialogs.append(dialog)),
                      patch('omniflow.devices.Device.screenshot') as capture,
                      patch('swordx.workflows._write_manifest') as write):
                    studio._replay_saved_screenshots()
                    capture.assert_not_called()
                    write.assert_not_called()
                self.assertEqual(len(dialogs), 1)
                dialog = dialogs[0]
                self.assertEqual(dialog.table.item(0, 1).text(), 'UNAVAILABLE')
                self.assertEqual(dialog.table.item(0, 4).text(),
                                 'Unsafe screenshot reference')
                self.assertEqual(dialog.table.item(0, 5).text(), '—')
                self.assertIn('Unsafe screenshot reference',
                              dialog.evidence.toPlainText())
                self.assertEqual((folder / 'manifest.json').read_bytes(), before)
            finally:
                studio.close()

    def test_replay_warns_on_later_invalid_action_without_emulator_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('UnsafeReplay', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            key = record_step(folder, 'Ready', image,
                              [(100, 200, 230, 300)], taps=[(150, 250)])
            path = folder / 'manifest.json'
            manifest = json.loads(path.read_text(encoding='utf-8'))
            manifest['actions'][key]['action_chain'] = [
                {'kind': 'tap', 'point': [150, 250]},
                {'kind': 'unknown'},
            ]
            path.write_text(json.dumps(manifest), encoding='utf-8')
            before = path.read_bytes()
            studio = OmniStudio('', 'UnsafeReplay', base=root)
            try:
                with (patch('omniflow.studio.QMessageBox.warning') as warning,
                      patch('omniflow.devices.Device.screenshot') as capture,
                      patch('swordx.workflows._write_manifest') as write):
                    studio._replay_saved_screenshots()
                    warning.assert_called_once()
                    capture.assert_not_called()
                    write.assert_not_called()
                self.assertIn('action 2: Unknown action',
                              warning.call_args.args[2])
                self.assertEqual(path.read_bytes(), before)
            finally:
                studio.close()

    def test_replay_dialog_shows_overlaps_without_hiding_failing_states(self):
        rows = [
            {'expected': 'Waiting for match', 'status': 'matched',
             'actual': 'Waiting for match', 'reason': 'Recorded state selected',
             'overlaps': [{'key': 'tap', 'label': 'Press Match', 'passive': False}],
             'action_plan': ['WAIT — no input will be sent'],
             'evidence': ['Waiting for match [WAIT]: selected — image 96% (minimum 82%)',
                          'Press Match [ACTION]: overridden by WAIT — image 94% (minimum 82%)']},
            {'expected': 'Press Match', 'status': 'different',
             'actual': 'Waiting for match', 'reason': 'overridden by WAIT',
             'overlaps': [{'key': 'tap', 'label': 'Press Match', 'passive': False}],
             'evidence': ['Press Match [ACTION]: conditions failed — condition-only',
                          '  FAIL — zone average color']},
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
        self.assertTrue(dialog.evidence.isReadOnly())
        self.assertIn('overridden by WAIT', dialog.evidence.toPlainText())
        dialog.table.setCurrentCell(1, 0)
        self.assertIn('FAIL — zone average color', dialog.evidence.toPlainText())
        dialog.table.setCurrentCell(2, 0)
        self.assertIn('No saved screenshot', dialog.evidence.toPlainText())
        dialog.table.setCurrentCell(-1, -1)
        self.assertEqual(dialog.evidence.toPlainText(), '')

    def test_empty_replay_dialog_keeps_evidence_panel_empty(self):
        dialog = ReplayResultsDialog([])
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertEqual(dialog.evidence.toPlainText(), '')

    def test_failed_state_reorder_preserves_unsaved_draft_and_prior_good_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('SafeReorder', root)
            image = self.frame.copy()
            image[215:275, 115:200] = (5, 230, 25)
            first = record_step(folder, 'First', image,
                                [(100, 200, 230, 300)], taps=[(150, 250)])
            second = record_step(folder, 'Second', image,
                                 [(100, 200, 230, 300)], taps=[(150, 250)])
            manifest = folder / 'manifest.json'
            studio = OmniStudio('', 'SafeReorder', base=root)
            try:
                studio.steps.setCurrentRow(0)
                studio.step_name.setText('First edited but unsaved')
                rule = {'type': 'ocr', 'rect': [100, 200, 230, 300],
                        'text': 'READY'}
                with patch('omniflow.studio.RuleEditor') as editor:
                    editor.return_value.exec.return_value = QDialog.Accepted
                    editor.return_value.result_rule.return_value = rule
                    studio._add_rule()
                history = len(studio._undo_edits)
                self.assertGreater(history, 0)
                self.assertTrue(studio._dirty)

                def reverse_visible_order():
                    blocked = studio.steps.blockSignals(True)
                    try:
                        item = studio.steps.takeItem(0)
                        studio.steps.addItem(item)
                        studio.steps.setCurrentItem(item)
                    finally:
                        studio.steps.blockSignals(blocked)

                # A successful drag updates the baseline. Neither it nor a
                # later failed drag may commit or discard draft fields.
                reverse_visible_order()
                studio._reordered(studio.steps.keys())
                self.assertEqual(studio.steps.keys(), [second, first])
                # Qt may select the dragged item even though the editor still
                # targets a different state; a successful reorder must repair it.
                self.assertEqual(studio.steps.currentItem().data(Qt.UserRole), first)
                self.assertEqual(studio.editing_key, first)
                self.assertEqual(studio.step_name.text(), 'First edited but unsaved')
                self.assertEqual(studio.extra_conditions, [rule])
                self.assertEqual(len(studio._undo_edits), history)
                self.assertTrue(studio._dirty)
                self.assertEqual(studio._persisted_step_order, [second, first])
                self.assertEqual(json.loads(manifest.read_text())['order'],
                                 [second, first])
                persisted = manifest.read_bytes()
                reverse_visible_order()
                self.assertEqual(studio.steps.keys(), [first, second])
                with (patch('swordx.workflows.os.replace',
                            side_effect=OSError('simulated reorder write failure')),
                      patch('swordx.recorder.QMessageBox.warning') as warning,
                      patch.object(studio, '_load_steps') as reload):
                    studio._reordered(studio.steps.keys())
                    warning.assert_called_once()
                    reload.assert_not_called()
                self.assertIn('simulated reorder write failure',
                              warning.call_args.args[2])
                self.assertEqual(studio.steps.keys(), [second, first])
                self.assertEqual(studio.steps.currentItem().data(Qt.UserRole), first)
                self.assertEqual(studio.editing_key, first)
                self.assertEqual(studio.step_name.text(),
                                 'First edited but unsaved')
                self.assertEqual(studio.extra_conditions, [rule])
                self.assertEqual(len(studio._undo_edits), history)
                self.assertTrue(studio._dirty)
                self.assertEqual(manifest.read_bytes(), persisted)

                studio._save()
                saved = json.loads(manifest.read_text(encoding='utf-8'))
                self.assertEqual(saved['order'], [second, first])
                self.assertEqual(saved['actions'][first]['label'],
                                 'First edited but unsaved')
                self.assertEqual(saved['actions'][first]['conditions'], [rule])
                self.assertFalse(studio._dirty)
            finally:
                with patch('swordx.recorder.QMessageBox.question',
                           return_value=QMessageBox.Yes):
                    studio.close()

    def test_reorder_without_selected_state_keeps_new_draft_unselected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('NewStateReorder', root)
            image = self.frame.copy()
            image[215:275, 115:200] = (5, 230, 25)
            first = record_step(folder, 'First', image,
                                [(100, 200, 230, 300)], taps=[(150, 250)])
            second = record_step(folder, 'Second', image,
                                 [(100, 200, 230, 300)], taps=[(150, 250)])
            studio = OmniStudio('', 'NewStateReorder', base=root)
            try:
                studio.step_name.setText('Unsaved new state')
                self.assertIsNone(studio.editing_key)
                self.assertTrue(studio._dirty)
                previous = (folder / 'manifest.json').read_bytes()
                blocked = studio.steps.blockSignals(True)
                try:
                    moved = studio.steps.takeItem(0)
                    studio.steps.addItem(moved)
                    studio.steps.setCurrentRow(0)
                finally:
                    studio.steps.blockSignals(blocked)
                studio._reordered(studio.steps.keys())
                self.assertEqual(studio.steps.keys(), [second, first])
                self.assertEqual(studio.steps.currentRow(), -1)
                self.assertIsNone(studio.editing_key)
                self.assertEqual(studio.step_name.text(), 'Unsaved new state')
                self.assertTrue(studio._dirty)
                saved = json.loads((folder / 'manifest.json').read_text())
                self.assertEqual(saved['order'], [second, first])
                self.assertEqual(saved['actions'][first]['label'], 'First')
                self.assertEqual(saved['actions'][second]['label'], 'Second')
                self.assertNotEqual((folder / 'manifest.json').read_bytes(),
                                    previous)
            finally:
                with patch('swordx.recorder.QMessageBox.question',
                           return_value=QMessageBox.Yes):
                    studio.close()

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

    def test_draft_history_survives_canceled_switch_but_not_completed_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('ScopedHistory', root)
            image = self.frame.copy()
            image[220:270, 120:200] = (10, 230, 30)
            first = record_step(folder, 'First', image,
                                [(100, 200, 230, 300)], taps=[(150, 250)])
            second = record_step(folder, 'Second', image,
                                 [(100, 200, 230, 300)], taps=[(150, 250)])
            before = (folder / 'manifest.json').read_bytes()
            studio = OmniStudio('', 'ScopedHistory', base=root)
            try:
                studio.steps.setCurrentRow(0)
                self.assertEqual(studio.editing_key, first)
                studio.rules_only.setChecked(True)
                self.assertTrue(studio._undo_buttons[0].isEnabled())
                with patch('swordx.recorder.QMessageBox.question',
                           return_value=QMessageBox.No):
                    studio.steps.setCurrentRow(1)
                self.assertEqual(studio.editing_key, first)
                self.assertEqual(studio.steps.currentRow(), 0)
                self.assertTrue(studio.rules_only.isChecked())
                self.assertTrue(studio._undo_buttons[0].isEnabled())

                with patch('swordx.recorder.QMessageBox.question',
                           return_value=QMessageBox.Yes):
                    studio.steps.setCurrentRow(1)
                self.assertEqual(studio.editing_key, second)
                self.assertFalse(studio.rules_only.isChecked())
                self.assertFalse(studio._undo_buttons[0].isEnabled())
                self.assertFalse(studio._redo_buttons[0].isEnabled())
                self.assertEqual((folder / 'manifest.json').read_bytes(), before)
            finally:
                with patch('swordx.recorder.QMessageBox.question',
                           return_value=QMessageBox.Yes):
                    studio.close()

    def test_rule_copy_reorder_and_disabled_controls_are_undoable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('RuleCopy', root)
            image = self.frame.copy()
            image[215:275, 115:200] = (5, 230, 25)
            record_step(folder, 'Ready', image, [(100, 200, 230, 300)],
                        taps=[(150, 250)])
            original = (folder / 'manifest.json').read_bytes()
            studio = OmniStudio('', 'RuleCopy', base=root)
            try:
                studio.steps.setCurrentRow(0)
                controls = studio._selection_buttons['rule']
                self.assertFalse(controls['duplicate'].isEnabled())
                self.assertFalse(controls['edit'].isEnabled())
                self.assertFalse(controls['up'].isEnabled())
                studio.extra_conditions = [
                    {'type': 'ocr', 'rect': [100, 200, 230, 300], 'text': 'READY'},
                    {'type': 'color', 'rect': [100, 200, 230, 300],
                     'bgr': [75, 75, 75], 'tolerance': 15}]
                studio._refresh_extra()
                studio.rules_list.setCurrentRow(0)
                self.assertTrue(controls['duplicate'].isEnabled())
                self.assertFalse(controls['up'].isEnabled())
                self.assertTrue(controls['down'].isEnabled())
                studio._duplicate_rule()
                self.assertEqual(len(studio.extra_conditions), 3)
                self.assertEqual(studio.rules_list.currentRow(), 1)
                self.assertIsNot(studio.extra_conditions[0],
                                 studio.extra_conditions[1])
                self.assertIsNot(studio.extra_conditions[0]['rect'],
                                 studio.extra_conditions[1]['rect'])
                studio.extra_conditions[1]['rect'][0] = 105
                self.assertEqual(studio.extra_conditions[0]['rect'][0], 100)
                # A duplicate is a distinct draft value, not an alias.
                studio._move_rule(1)
                self.assertEqual(studio.rules_list.currentRow(), 2)
                self.assertFalse(controls['down'].isEnabled())
                studio._move_rule(1)  # Out-of-range must be a no-op.
                self.assertEqual(len(studio._undo_edits), 2)
                studio._undo_edit()
                self.assertEqual(studio.rules_list.currentRow(), 1)
                studio._undo_edit()
                self.assertEqual(len(studio.extra_conditions), 2)
                studio._redo_edit()
                self.assertEqual(len(studio.extra_conditions), 3)
                self.assertEqual(studio.rules_list.currentRow(), 1)
                self.assertEqual((folder / 'manifest.json').read_bytes(),
                                 original)
            finally:
                with patch('swordx.recorder.QMessageBox.question',
                           return_value=QMessageBox.Yes):
                    studio.close()

    def test_action_copy_reorder_and_save_preserve_chain_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('ActionCopy', root)
            image = self.frame.copy()
            image[215:275, 115:200] = (5, 230, 25)
            key = record_step(folder, 'Ready', image,
                              [(100, 200, 230, 300)], taps=[(150, 250)])
            original = (folder / 'manifest.json').read_bytes()
            studio = OmniStudio('', 'ActionCopy', base=root)
            try:
                studio.steps.setCurrentRow(0)
                controls = studio._selection_buttons['action']
                self.assertFalse(controls['duplicate'].isEnabled())
                self.assertFalse(controls['remove'].isEnabled())
                studio.extra_chain = [
                    {'kind': 'tap', 'point': [150, 250]},
                    {'kind': 'wait', 'seconds': 2}]
                studio._refresh_extra()
                studio.actions_list.setCurrentRow(0)
                self.assertTrue(controls['duplicate'].isEnabled())
                self.assertFalse(controls['up'].isEnabled())
                studio._duplicate_action()
                self.assertEqual(studio.actions_list.currentRow(), 1)
                self.assertEqual([a['kind'] for a in studio.extra_chain],
                                 ['tap', 'tap', 'wait'])
                self.assertIsNot(studio.extra_chain[0]['point'],
                                 studio.extra_chain[1]['point'])
                studio._move_action(1)
                self.assertEqual(studio.actions_list.currentRow(), 2)
                self.assertFalse(controls['down'].isEnabled())
                self.assertEqual([a['kind'] for a in studio.extra_chain],
                                 ['tap', 'wait', 'tap'])
                studio._undo_edit()
                self.assertEqual([a['kind'] for a in studio.extra_chain],
                                 ['tap', 'tap', 'wait'])
                studio._redo_edit()
                self.assertEqual([a['kind'] for a in studio.extra_chain],
                                 ['tap', 'wait', 'tap'])
                self.assertEqual((folder / 'manifest.json').read_bytes(),
                                 original)
                studio._save()
                saved = json.loads((folder / 'manifest.json').read_text())
                self.assertEqual([a['kind'] for a in
                                  saved['actions'][key]['action_chain']],
                                 ['tap', 'wait', 'tap'])
                self.assertFalse(studio._undo_buttons[0].isEnabled())
            finally:
                with patch('swordx.recorder.QMessageBox.question',
                           return_value=QMessageBox.Yes):
                    studio.close()

    def test_studio_rejects_invalid_draft_recognition_rules_without_saving(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('InvalidWhen', root)
            image = self.frame.copy()
            image[215:275, 115:200] = (5, 230, 25)
            record_step(folder, 'Ready', image,
                        [(100, 200, 230, 300)], taps=[(150, 250)])
            before = {p.relative_to(folder): p.read_bytes()
                      for p in folder.rglob('*') if p.is_file()}
            studio = OmniStudio('', 'InvalidWhen', base=root)
            try:
                studio.steps.setCurrentRow(0)
                invalid = [
                    {'type': 'ocr', 'rect': [100, 200, 230, 300],
                     'text': '[', 'regex': True},
                    {'type': 'pixel', 'rect': [100, 200, 230, 300],
                     'point': [540, 250], 'bgr': [50, 50, 50]},
                    {'type': 'color', 'rect': [100, 200, 230, 300],
                     'bgr': [50, 50, 50], 'tolerance': -1,
                     'negate': True},
                    {'type': 'ocr', 'rect': [100, 200, 230, 300],
                     'text': 'READY', 'regex': 'false'},
                    {'type': 'color', 'rect': [100, 200, 230, 300],
                     'bgr': [50, 50, 50], 'negate': 'false'},
                ]
                for bad_rule in invalid:
                    with self.subTest(rule=bad_rule):
                        studio.extra_conditions = [bad_rule]
                        studio._field_changed()
                        with (patch('omniflow.studio.QMessageBox.warning') as warn,
                              patch('swordx.workflows._write_manifest') as write):
                            studio._save()
                            warn.assert_called_once()
                            write.assert_not_called()
                        self.assertEqual(warn.call_args.args[1], 'Rules')
                        self.assertIn('Condition 1:', warn.call_args.args[2])
                        self.assertEqual(
                            {p.relative_to(folder): p.read_bytes()
                             for p in folder.rglob('*') if p.is_file()}, before)
                        self.assertTrue(studio._dirty)
            finally:
                with patch('swordx.recorder.QMessageBox.question',
                           return_value=QMessageBox.Yes):
                    studio.close()

    def test_studio_rejects_invalid_draft_actions_without_saving(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = create_workflow('InvalidDraft', root)
            image = self.frame.copy()
            image[215:275, 115:200] = (5, 230, 25)
            record_step(folder, 'Ready', image,
                        [(100, 200, 230, 300)], taps=[(150, 250)])
            before = (folder / 'manifest.json').read_bytes()
            studio = OmniStudio('', 'InvalidDraft', base=root)
            try:
                studio.steps.setCurrentRow(0)
                for invalid in (
                        {'kind': 'unknown'},
                        {'kind': 'tap', 'point': [700, 200]},
                        {'kind': 'tap', 'point': [541, 200]},
                        {'kind': 'double_tap', 'point': [150, 961]},
                        {'kind': 'swipe', 'start': [120, 200],
                         'end': [180, 240], 'duration_ms': 1},
                        {'kind': 'swipe', 'start': [541, 120],
                         'end': [180, 240], 'duration_ms': 600},
                        {'kind': 'field_text', 'point': [150, 961],
                         'text': 'READY'},
                        {'kind': 'field_text', 'point': [150, 250],
                         'text': 'unsafe;command'}):
                    with self.subTest(action=invalid):
                        studio.extra_chain = [invalid]
                        studio._field_changed()
                        with patch('omniflow.studio.QMessageBox.warning') as warn:
                            studio._save()
                            warn.assert_called_once()
                            self.assertEqual(warn.call_args.args[1], 'Actions')
                        self.assertEqual(
                            (folder / 'manifest.json').read_bytes(), before)
                        self.assertTrue(studio._dirty)
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