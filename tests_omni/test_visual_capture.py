"""Zone/gesture authoring is coordinate-free in the UI, normalized in storage."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from omniflow.engine import OmniDetector, OmniRunner
from omniflow.visual import (action_summary, condition_summary, require_frame,
                             sample_average, sample_color, validate_point, validate_zone)
from swordx.workflows import create_workflow, record_step, remove_step


class VisualCaptureTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.full((960, 540, 3), (5, 50, 180), dtype=np.uint8)
        cv2.rectangle(self.frame, (80, 100), (220, 170), (200, 100, 10), -1)
        cv2.putText(self.frame, 'START', (85, 142), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8, (245, 245, 245), 2)

    def test_zone_and_point_validate_borders(self):
        self.assertEqual(validate_zone((0, 0, 540, 960)), (0, 0, 540, 960))
        self.assertEqual(validate_point([539, 959]), (539, 959))
        for bad in ((-1, 0, 20, 40), (100, 100, 99, 140), (0, 0, 1, 1)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_zone(bad)
        for bad in ((540, 300), (5.5, 60)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_point(bad)

    def test_color_sampling_uses_selected_image_without_manual_bgr(self):
        self.assertEqual(sample_color(self.frame, [120, 160]), [200, 100, 10])
        self.assertEqual(sample_average(self.frame, [110, 150, 140, 168]), [200, 100, 10])

    def test_no_manual_coordinates_required_to_author_condition_only_ocr(self):
        with tempfile.TemporaryDirectory() as root:
            folder = create_workflow('OCR-only', Path(root))
            rule = {'type': 'ocr', 'rect': [80, 100, 220, 170], 'text': 'Start'}
            action = {'kind': 'tap', 'point': [140, 155]}
            key = record_step(folder, 'Tap when text appears', self.frame, [],
                              gestures=[], conditions=[rule], condition_only=True,
                              action_chain=[action])
            manifest = json.loads((folder / 'manifest.json').read_text())
            self.assertEqual(manifest['states'][key], [])
            self.assertTrue(manifest['actions'][key]['skip_template'])
            with patch('omniflow.engine.OCREngine.read', return_value='START GAME'):
                match = OmniDetector(folder).detect(self.frame)
            self.assertEqual(match.key, key)
            self.assertEqual(match.label, 'Tap when text appears')

    def test_condition_only_requires_proof_and_valid_action(self):
        with tempfile.TemporaryDirectory() as root:
            folder = create_workflow('Invalid condition', Path(root))
            with self.assertRaisesRegex(ValueError, 'recognition condition'):
                record_step(folder, 'Unsafe', self.frame, [], gestures=[],
                            condition_only=True, action_chain=[{'kind':'tap','point':[100,200]}])
            with self.assertRaisesRegex(ValueError, 'action sequence'):
                record_step(folder, 'No action', self.frame, [], gestures=[],
                            condition_only=True, conditions=[{'type':'ocr','rect':[80,100,220,170],'text':'GO'}])
            self.assertEqual(len(json.loads((folder/'manifest.json').read_text())['states']), 0)

    def test_unsaved_conditions_preview_is_read_only_and_shares_ocr_results(self):
        from omniflow.engine import preview_conditions, WorkflowError
        region = [80, 100, 220, 170]
        rules = [
            {'type': 'ocr', 'rect': region, 'text': 'START'},
            {'type': 'ocr', 'rect': region, 'text': 'STOP', 'negate': True},
            {'type': 'color', 'rect': [300, 300, 320, 320],
             'bgr': [5, 50, 180], 'tolerance': 0},
            {'type': 'pixel', 'rect': [80, 100, 220, 170],
             'point': [120, 110], 'bgr': [200, 100, 10], 'tolerance': 0},
        ]
        with patch('omniflow.engine.OCREngine.read', return_value='START GAME') as ocr:
            outcomes = preview_conditions(self.frame, rules)
        self.assertEqual(outcomes, [True, True, True, True])
        ocr.assert_called_once()
        self.assertFalse(preview_conditions(self.frame, [
            {'type': 'pixel', 'rect': region,
             'point': [120, 110], 'bgr': [1, 2, 3], 'tolerance': 0}])[0])
        for invalid in (None, np.zeros((0, 0, 3), np.uint8),
                        np.zeros((960, 540), np.uint8)):
            with self.subTest(invalid=repr(getattr(invalid, 'shape', None))):
                with self.assertRaises(WorkflowError):
                    preview_conditions(invalid, rules)
        with self.assertRaisesRegex(WorkflowError, 'at least one'):
            preview_conditions(self.frame, [])

    def test_bad_ocr_regex_is_actionable_workflow_error(self):
        from omniflow.engine import WorkflowError, preview_conditions
        rule = {'type': 'ocr', 'rect': [80, 100, 220, 170],
                'text': '[invalid', 'regex': True}
        with patch('omniflow.engine.OCREngine.read', return_value='START') as ocr:
            with self.assertRaisesRegex(WorkflowError, 'Invalid OCR regular expression'):
                preview_conditions(self.frame, [rule])
            ocr.assert_not_called()
        with self.assertRaisesRegex(WorkflowError, 'Invalid OCR regular expression'):
            from omniflow.engine import validate_ocr_expression
            validate_ocr_expression('(', True)

    def test_preexisting_capture_with_region_and_tap_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            folder = create_workflow('Traditional', Path(root))
            key = record_step(folder,'Classic', self.frame, [(80,100,220,170)],
                              taps=[(130,140)])
            manifest = json.loads((folder/'manifest.json').read_text())
            self.assertEqual(len(manifest['states'][key]), 1)
            self.assertEqual(manifest['actions'][key]['taps'], [[130, 140]])

    def test_legacy_settings_save_preserves_studio_fields_by_default(self):
        from swordx.workflows import update_step_settings
        with tempfile.TemporaryDirectory() as root:
            folder = create_workflow('LegacySave', Path(root))
            key = record_step(
                folder, 'Before', self.frame, [],
                action_chain=[{'kind': 'tap', 'point': [140, 155]}],
                conditions=[{'type': 'color', 'rect': [80, 100, 220, 170],
                             'bgr': [200, 100, 10]}],
                condition_only=True)
            path = folder / 'manifest.json'
            original = json.loads(path.read_text())['actions'][key]
            update_step_settings(folder, key, 'After', end=False, retry=False)
            saved = json.loads(path.read_text())['actions'][key]
            self.assertEqual(saved['label'], 'After')
            for field in ('action_chain', 'conditions', 'skip_template'):
                self.assertEqual(saved[field], original[field])

    def test_settings_extra_validation_never_writes_partial_manifest(self):
        from swordx.workflows import update_step_settings
        with tempfile.TemporaryDirectory() as root:
            folder = create_workflow('SafeSave', Path(root))
            key = record_step(folder, 'Original', self.frame,
                              [(80, 100, 220, 170)], taps=[(140, 155)])
            path = folder / 'manifest.json'
            original = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'recognition condition'):
                update_step_settings(
                    folder, key, 'Changed', end=True, retry=True,
                    studio={'action_chain': [], 'conditions': [],
                            'skip_template': True})
            self.assertEqual(path.read_bytes(), original)

    def test_reorder_is_presentation_only_and_rejects_incomplete_keys(self):
        from swordx.workflows import reorder_steps
        with tempfile.TemporaryDirectory() as root:
            folder = create_workflow('Presentation order', Path(root))
            first = record_step(folder, 'First', self.frame,
                                [(80, 100, 220, 170)], taps=[(130, 140)])
            second = record_step(folder, 'Second', self.frame,
                                 [(80, 100, 220, 170)], taps=[(130, 140)])
            manifest = folder / 'manifest.json'
            original = json.loads(manifest.read_text(encoding='utf-8'))
            images = {p.relative_to(folder): p.read_bytes()
                      for p in folder.rglob('*')
                      if p.is_file() and p != manifest}
            reorder_steps(folder, [second, first])
            saved = json.loads(manifest.read_text(encoding='utf-8'))
            self.assertEqual(saved['order'], [second, first])
            for field in ('states', 'actions', 'threshold', 'name'):
                self.assertEqual(saved[field], original[field])
            self.assertEqual(
                {p.relative_to(folder): p.read_bytes()
                 for p in folder.rglob('*') if p.is_file() and p != manifest},
                images)
            before_invalid = manifest.read_bytes()
            with self.assertRaisesRegex(ValueError,
                                        'every state exactly once'):
                reorder_steps(folder, [second, second])
            self.assertEqual(manifest.read_bytes(), before_invalid)

    def test_failed_manifest_replace_preserves_previous_save_and_cleans_staging(self):
        from swordx.workflows import update_step_settings
        with tempfile.TemporaryDirectory() as root:
            folder = create_workflow('Atomic settings', Path(root))
            key = record_step(folder, 'Before', self.frame,
                              [(80, 100, 220, 170)], taps=[(130, 140)])
            manifest = folder / 'manifest.json'
            original = manifest.read_bytes()
            previous_temp = folder / 'manifest.tmp'
            previous_temp.write_text('existing unrelated file', encoding='utf-8')
            with patch('swordx.workflows.os.replace',
                       side_effect=OSError('simulated replace failure')) as replace:
                with self.assertRaisesRegex(OSError, 'simulated replace failure'):
                    update_step_settings(folder, key, 'After', end=True, retry=True)
                replace.assert_called_once()
            self.assertEqual(manifest.read_bytes(), original)
            self.assertEqual(previous_temp.read_text(encoding='utf-8'),
                             'existing unrelated file')
            self.assertEqual(list(folder.glob('.manifest.json.*.tmp')), [])

    def test_repeated_manifest_saves_use_distinct_staging_paths(self):
        import os
        from swordx.workflows import update_step_settings
        with tempfile.TemporaryDirectory() as root:
            folder = create_workflow('Distinct staging', Path(root))
            key = record_step(folder, 'Before', self.frame,
                              [(80, 100, 220, 170)], taps=[(130, 140)])
            originals = []
            real_replace = os.replace

            def observe_replace(source, destination):
                originals.append(Path(source))
                real_replace(source, destination)

            with patch('swordx.workflows.os.replace', side_effect=observe_replace):
                update_step_settings(folder, key, 'First', end=False, retry=False)
                update_step_settings(folder, key, 'Second', end=True, retry=False)
            self.assertEqual(len(originals), 2)
            self.assertNotEqual(originals[0], originals[1])
            self.assertTrue(all(path.parent == folder for path in originals))
            self.assertFalse(any(path.exists() for path in originals))
            self.assertEqual(json.loads((folder / 'manifest.json').read_text())
                             ['actions'][key]['label'], 'Second')

    def test_template_mode_needs_a_real_saved_image_region(self):
        from swordx.workflows import update_step_settings
        with tempfile.TemporaryDirectory() as root:
            folder = create_workflow('NoTemplate', Path(root))
            conditions = [{'type': 'ocr', 'rect': [80, 100, 220, 170],
                           'text': 'READY'}]
            chain = [{'kind': 'tap', 'point': [140, 155]}]
            key = record_step(folder, 'Ready', self.frame, [],
                              condition_only=True, conditions=conditions,
                              action_chain=chain)
            path = folder / 'manifest.json'
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'requires a saved template'):
                update_step_settings(
                    folder, key, 'Wrong mode', end=False, retry=False,
                    studio={'action_chain': chain, 'conditions': conditions,
                            'skip_template': False})
            self.assertEqual(path.read_bytes(), before)

    def test_recapture_and_delete_clean_up_original_images(self):
        with tempfile.TemporaryDirectory() as root:
            folder = create_workflow('Clean image replacement', Path(root))
            rect = (80, 100, 220, 170)
            key = record_step(folder, 'Original', self.frame, [rect], taps=[(130, 140)])
            manifest = folder / 'manifest.json'
            before = json.loads(manifest.read_text())
            old_anchor = folder / 'anchors' / before['states'][key][0]['file']
            old_capture = folder / 'captures' / before['actions'][key]['capture']
            self.assertTrue(old_anchor.exists())
            self.assertTrue(old_capture.exists())
            self.assertEqual(record_step(folder, 'Updated', self.frame, [rect],
                                         taps=[(130, 140)], key=key), key)
            updated = json.loads(manifest.read_text())
            new_anchor = folder / 'anchors' / updated['states'][key][0]['file']
            new_capture = folder / 'captures' / updated['actions'][key]['capture']
            self.assertFalse(old_anchor.exists())
            self.assertFalse(old_capture.exists())
            self.assertTrue(new_anchor.exists())
            self.assertTrue(new_capture.exists())
            remove_step(folder, key)
            self.assertNotIn(key, json.loads(manifest.read_text())['states'])
            self.assertFalse(new_anchor.exists())
            self.assertFalse(new_capture.exists())

    def test_load_step_rejects_bad_screenshot_geometry_without_editing_manifest(self):
        from swordx.workflows import load_step
        with tempfile.TemporaryDirectory() as root:
            folder = create_workflow('Capture geometry', Path(root))
            key = record_step(folder, 'Ready', self.frame,
                              [(80, 100, 220, 170)], taps=[(130, 140)])
            manifest = folder / 'manifest.json'
            saved = json.loads(manifest.read_text(encoding='utf-8'))
            capture = folder / 'captures' / saved['actions'][key]['capture']
            original_manifest = manifest.read_bytes()
            self.assertTrue(cv2.imwrite(str(capture), self.frame[:700, :500]))
            with self.assertRaisesRegex(ValueError, 'normalized 540×960'):
                load_step(folder, key)
            self.assertEqual(manifest.read_bytes(), original_manifest)

            capture.write_bytes(b'corrupt screenshot')
            with self.assertRaisesRegex(ValueError, 'unreadable'):
                load_step(folder, key)
            self.assertEqual(manifest.read_bytes(), original_manifest)

            # Preserve support for old workflows without a full screenshot.
            capture.unlink()
            action, boxes, taps, preview = load_step(folder, key)
            self.assertTrue(action['_needs_recapture'])
            self.assertEqual(preview.shape, (960, 540, 3))
            self.assertEqual(boxes, [(80, 100, 220, 170)])
            self.assertEqual(taps, [(130, 140)])
            self.assertEqual(manifest.read_bytes(), original_manifest)

    def test_load_step_rejects_unsafe_imported_images_before_reading(self):
        from swordx.workflows import load_step
        for kind in ('captures', 'anchors'):
            with self.subTest(kind=kind):
                with tempfile.TemporaryDirectory() as root:
                    folder = create_workflow('Unsafe selection', Path(root))
                    key = record_step(folder, 'Recorded', self.frame,
                                      [(80, 100, 220, 170)], taps=[(130, 140)])
                    manifest = folder / 'manifest.json'
                    data = json.loads(manifest.read_text(encoding='utf-8'))
                    if kind == 'captures':
                        data['actions'][key]['capture'] = '../../outside.jpg'
                    else:
                        data['states'][key][0]['file'] = '../../outside.png'
                    manifest.write_text(json.dumps(data), encoding='utf-8')
                    before = manifest.read_bytes()
                    with patch('swordx.workflows.cv2.imread') as read:
                        with self.assertRaisesRegex(ValueError,
                                                    f'Unsafe saved {kind}'):
                            load_step(folder, key)
                        read.assert_not_called()
                    self.assertEqual(manifest.read_bytes(), before)

    def test_partial_image_write_failure_rolls_back_new_and_recaptured_assets(self):
        rect = (80, 100, 220, 170)
        for editing in (False, True):
            for failed_asset in ('anchor', 'capture'):
                for failure in ('false', 'exception'):
                    with self.subTest(editing=editing, asset=failed_asset,
                                      failure=failure):
                        with tempfile.TemporaryDirectory() as root:
                            folder = create_workflow('Partial image rollback',
                                                     Path(root))
                            key = (record_step(folder, 'Before', self.frame,
                                               [rect], taps=[(130, 140)])
                                   if editing else None)
                            manifest = folder / 'manifest.json'
                            before = {p.relative_to(folder): p.read_bytes()
                                      for p in folder.rglob('*') if p.is_file()}
                            attempted = []

                            def partial_write(name, *_args):
                                target = Path(name)
                                attempted.append(target)
                                # Model an encoder that creates output before
                                # reporting failure or throwing an exception.
                                target.write_bytes(b'incomplete image bytes')
                                if failed_asset in target.parent.name:
                                    if failure == 'false':
                                        return False
                                    raise RuntimeError('simulated interrupted encoder')
                                return True

                            error = OSError if failure == 'false' else RuntimeError
                            message = ('Cannot save' if failure == 'false'
                                       else 'simulated interrupted encoder')
                            with (patch('swordx.workflows.cv2.imwrite',
                                        side_effect=partial_write),
                                  patch('swordx.workflows._write_manifest') as save):
                                with self.assertRaisesRegex(error, message):
                                    record_step(folder, 'After', self.frame,
                                                [rect], taps=[(130, 140)], key=key)
                                save.assert_not_called()
                            self.assertEqual(len(attempted),
                                             1 if failed_asset == 'anchor' else 2)
                            self.assertTrue(all(not p.exists() for p in attempted))
                            self.assertEqual(
                                {p.relative_to(folder): p.read_bytes()
                                 for p in folder.rglob('*') if p.is_file()},
                                before)
                            if key is not None:
                                self.assertEqual(
                                    json.loads(manifest.read_text())['actions'][key]['label'],
                                    'Before')

    def test_failed_obsolete_image_cleanup_keeps_committed_state_successful(self):
        # An unlink failure happens after the manifest transaction, so it
        # cannot safely make either operation appear rolled back.
        real_unlink = Path.unlink
        for operation in ('recapture', 'remove'):
            with self.subTest(operation=operation):
                with tempfile.TemporaryDirectory() as root:
                    folder = create_workflow('Postcommit cleanup', Path(root))
                    rect = (80, 100, 220, 170)
                    key = record_step(folder, 'Before', self.frame, [rect],
                                      taps=[(130, 140)])
                    manifest = folder / 'manifest.json'
                    saved = json.loads(manifest.read_text(encoding='utf-8'))
                    old_anchor = folder / 'anchors' / saved['states'][key][0]['file']
                    old_capture = folder / 'captures' / saved['actions'][key]['capture']
                    old_assets = {old_anchor, old_capture}

                    def blocked_unlink(path, *args, **kwargs):
                        if path in old_assets:
                            raise PermissionError('simulated locked image')
                        return real_unlink(path, *args, **kwargs)

                    with (patch('swordx.workflows.Path.unlink',
                                autospec=True, side_effect=blocked_unlink),
                          self.assertLogs(level='WARNING') as logs):
                        if operation == 'recapture':
                            self.assertEqual(
                                record_step(folder, 'After', self.frame, [rect],
                                            taps=[(130, 140)], key=key), key)
                        else:
                            remove_step(folder, key)
                    self.assertIn('simulated locked image', '\n'.join(logs.output))
                    self.assertTrue(old_anchor.is_file())
                    self.assertTrue(old_capture.is_file())
                    current = json.loads(manifest.read_text(encoding='utf-8'))
                    if operation == 'recapture':
                        self.assertEqual(current['actions'][key]['label'], 'After')
                        self.assertTrue(
                            (folder / 'captures' /
                             current['actions'][key]['capture']).is_file())
                        self.assertNotEqual(
                            current['actions'][key]['capture'],
                            saved['actions'][key]['capture'])
                    else:
                        self.assertNotIn(key, current['states'])
                        self.assertNotIn(key, current['actions'])

    def test_delete_manifest_replace_failure_preserves_state_and_images(self):
        with tempfile.TemporaryDirectory() as root:
            folder = create_workflow('Atomic delete failure', Path(root))
            key = record_step(folder, 'Saved', self.frame,
                              [(80, 100, 220, 170)], taps=[(130, 140)])
            before = {p.relative_to(folder): p.read_bytes()
                      for p in folder.rglob('*') if p.is_file()}
            with patch('swordx.workflows.os.replace',
                       side_effect=OSError('simulated manifest write failure')):
                with self.assertRaisesRegex(OSError, 'simulated manifest write failure'):
                    remove_step(folder, key)
            self.assertEqual(
                {p.relative_to(folder): p.read_bytes()
                 for p in folder.rglob('*') if p.is_file()}, before)
            self.assertEqual(list(folder.glob('.manifest.json.*.tmp')), [])
            data = json.loads((folder / 'manifest.json').read_text())
            self.assertIn(key, data['states'])
            self.assertIn(key, data['actions'])

    def test_unsafe_cleanup_references_cannot_recapture_or_delete(self):
        for operation in ('recapture', 'remove'):
            for asset in ('anchor', 'capture'):
                with self.subTest(operation=operation, asset=asset):
                    with tempfile.TemporaryDirectory() as root:
                        folder = create_workflow('Unsafe cleanup', Path(root))
                        rect = (80, 100, 220, 170)
                        key = record_step(folder, 'Saved', self.frame, [rect],
                                          taps=[(130, 140)])
                        outside = folder.parent / 'outside.txt'
                        outside.write_text('untouched', encoding='utf-8')
                        manifest = folder / 'manifest.json'
                        data = json.loads(manifest.read_text())
                        if asset == 'anchor':
                            data['states'][key][0]['file'] = '../../outside.txt'
                        else:
                            data['actions'][key]['capture'] = '../../outside.txt'
                        manifest.write_text(json.dumps(data), encoding='utf-8')
                        before = {item.relative_to(folder): item.read_bytes()
                                  for item in folder.rglob('*') if item.is_file()}
                        with patch('swordx.workflows.cv2.imwrite') as write:
                            with self.assertRaisesRegex(ValueError, 'Unsafe saved'):
                                if operation == 'recapture':
                                    record_step(folder, 'Replacement', self.frame,
                                                [rect], taps=[(130, 140)], key=key)
                                else:
                                    remove_step(folder, key)
                            write.assert_not_called()
                        self.assertEqual(outside.read_text(encoding='utf-8'), 'untouched')
                        self.assertEqual(before, {
                            item.relative_to(folder): item.read_bytes()
                            for item in folder.rglob('*') if item.is_file()})

    def test_cleanup_preserves_image_references_shared_by_another_state(self):
        # Imported manifests may legitimately reuse the same saved image.
        for operation in ('recapture', 'remove'):
            with self.subTest(operation=operation):
                with tempfile.TemporaryDirectory() as root:
                    folder = create_workflow('Shared images', Path(root))
                    rect = (80, 100, 220, 170)
                    first = record_step(folder, 'First', self.frame, [rect],
                                        taps=[(130, 140)])
                    second = record_step(folder, 'Second', self.frame, [rect],
                                         taps=[(130, 140)])
                    manifest = folder / 'manifest.json'
                    data = json.loads(manifest.read_text(encoding='utf-8'))
                    anchor_name = data['states'][first][0]['file']
                    capture_name = data['actions'][first]['capture']
                    data['states'][second][0]['file'] = anchor_name
                    data['actions'][second]['capture'] = capture_name
                    manifest.write_text(json.dumps(data), encoding='utf-8')
                    shared_anchor = folder / 'anchors' / anchor_name
                    shared_capture = folder / 'captures' / capture_name
                    expected_anchor = shared_anchor.read_bytes()
                    expected_capture = shared_capture.read_bytes()
                    if operation == 'recapture':
                        record_step(folder, 'First updated', self.frame, [rect],
                                    taps=[(130, 140)], key=first)
                    else:
                        remove_step(folder, first)
                    saved = json.loads(manifest.read_text(encoding='utf-8'))
                    self.assertEqual(saved['states'][second][0]['file'], anchor_name)
                    self.assertEqual(saved['actions'][second]['capture'], capture_name)
                    self.assertEqual(shared_anchor.read_bytes(), expected_anchor)
                    self.assertEqual(shared_capture.read_bytes(), expected_capture)

    def test_other_state_with_unsafe_image_reference_blocks_cleanup(self):
        with tempfile.TemporaryDirectory() as root:
            folder = create_workflow('Bad sibling', Path(root))
            rect = (80, 100, 220, 170)
            first = record_step(folder, 'First', self.frame, [rect],
                                taps=[(130, 140)])
            second = record_step(folder, 'Second', self.frame, [rect],
                                 taps=[(130, 140)])
            manifest = folder / 'manifest.json'
            data = json.loads(manifest.read_text(encoding='utf-8'))
            data['states'][second][0]['file'] = '../../outside.png'
            manifest.write_text(json.dumps(data), encoding='utf-8')
            before = {p.relative_to(folder): p.read_bytes()
                      for p in folder.rglob('*') if p.is_file()}
            with patch('swordx.workflows.cv2.imwrite') as write:
                with self.assertRaisesRegex(ValueError, 'Unsafe saved anchors'):
                    record_step(folder, 'First updated', self.frame, [rect],
                                taps=[(130, 140)], key=first)
                write.assert_not_called()
            self.assertEqual(before, {p.relative_to(folder): p.read_bytes()
                                      for p in folder.rglob('*') if p.is_file()})

    def test_recapture_with_imported_traversal_key_writes_only_generated_assets(self):
        with tempfile.TemporaryDirectory() as root:
            folder = create_workflow('Imported keys', Path(root))
            rect = (80, 100, 220, 170)
            original = record_step(folder, 'Saved', self.frame, [rect],
                                   taps=[(130, 140)])
            manifest = folder / 'manifest.json'
            content = json.loads(manifest.read_text(encoding='utf-8'))
            imported = '../../escaped'
            content['states'][imported] = content['states'].pop(original)
            content['actions'][imported] = content['actions'].pop(original)
            content['order'] = [imported]
            manifest.write_text(json.dumps(content), encoding='utf-8')
            self.assertEqual(record_step(folder, 'Updated', self.frame, [rect],
                                         taps=[(130, 140)], key=imported), imported)
            saved = json.loads(manifest.read_text(encoding='utf-8'))
            self.assertEqual(saved['order'], [imported])
            for kind, name in (('captures', saved['actions'][imported]['capture']),
                               ('anchors', saved['states'][imported][0]['file'])):
                self.assertNotIn('/', name)
                self.assertNotIn('..', name)
                self.assertTrue((folder / kind / name).is_file())
            self.assertFalse(list(folder.parent.glob('escaped*')))

    def test_symlinked_output_directories_fail_before_writing_images(self):
        for kind in ('captures', 'anchors'):
            with self.subTest(kind=kind):
                with tempfile.TemporaryDirectory() as root:
                    folder = create_workflow('Unsafe output', Path(root))
                    manifest = folder / 'manifest.json'
                    before = manifest.read_bytes()
                    outside = Path(root) / 'outside'
                    outside.mkdir()
                    (folder / kind).rmdir()
                    try:
                        (folder / kind).symlink_to(outside, target_is_directory=True)
                    except (OSError, NotImplementedError):
                        # Windows environments can disallow creating symlinks.
                        continue
                    with patch('swordx.workflows.cv2.imwrite') as write:
                        with self.assertRaisesRegex(ValueError,
                                                    'directory resolves outside workflow'):
                            record_step(folder, 'New', self.frame,
                                        [(80, 100, 220, 170)], taps=[(130, 140)])
                        write.assert_not_called()
                    self.assertEqual(manifest.read_bytes(), before)
                    self.assertEqual(list(outside.iterdir()), [])

    def test_readable_editor_summaries_do_not_expose_coordinates(self):
        self.assertIn('Swipe',action_summary({'kind':'swipe','start':[20,20],
                                               'end':[120,180],'duration_ms':450}))
        self.assertNotIn('120',action_summary({'kind':'tap','point':[120,180]}))
        self.assertIn('NOT',condition_summary({'type':'ocr','rect':[1,1,20,20],
                                               'text':'Cancel','negate':True}))


if __name__ == '__main__':
    unittest.main()
