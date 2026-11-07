"""Compatibility, alternatives, screen-first dispatch and persisted editor data."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from swordx.config import Settings
from swordx.detection import ScreenDetector
from swordx.engine import Runner
from swordx.workflows import (create_workflow, load_step, record_step,
                               reorder_steps, remove_step, step_order)
from test_engine import FakeDevice

BOX_A = (40, 80, 100, 140)
BOX_B = (270, 560, 350, 620)


def frame(seed=1):
    shot = np.full((960, 540, 3), 32, dtype=np.uint8)
    rng = np.random.default_rng(seed)
    for x1, y1, x2, y2 in (BOX_A, BOX_B):
        shot[y1:y2, x1:x2] = rng.integers(0, 256, (y2-y1, x2-x1, 3), dtype=np.uint8)
    return shot


class WorkflowEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = create_workflow('My workflow', Path(self.temp.name))

    def metadata(self):
        return json.loads((self.folder / 'manifest.json').read_text(encoding='utf-8'))

    def test_one_region_and_one_tap_saved_detected_and_dispatched(self):
        source = frame()
        key = record_step(self.folder, 'Open menu', source, [BOX_A], taps=[(110, 200)])
        info, boxes, taps, capture = load_step(self.folder, key)
        self.assertEqual(boxes, [BOX_A])
        self.assertEqual(taps, [(110, 200)])
        self.assertFalse(info.get('_needs_recapture', False))
        self.assertLess(float(np.mean(cv2.absdiff(capture, source))), 4)
        detector = ScreenDetector(self.folder)
        match = detector.detect(source)
        self.assertEqual((match.state, match.trigger), (key, 0))
        device = FakeDevice('1')
        device.frame = source.copy()
        runner = Runner(device, detector, Settings(package='com.example.sword', diagnostics=False), lambda _: None)
        runner.step(100)
        self.assertEqual(device.taps, [(440, 800)])

    def test_two_region_pairs_alternate_and_route_the_correct_tap(self):
        source = frame()
        key = record_step(self.folder, 'Alternate buttons', source, [BOX_A, BOX_B],
                          taps=[(100, 200), (400, 700)])
        detector = ScreenDetector(self.folder)
        self.assertEqual(self.metadata()['actions'][key]['match'], 'any')
        for trigger in (0, 1):
            altered = source.copy()
            x1, y1, x2, y2 = (BOX_B if trigger == 0 else BOX_A)
            altered[y1:y2, x1:x2] = 32
            observed = detector.detect(altered)
            self.assertEqual(observed.state, key)
            self.assertEqual(observed.trigger, trigger)
            device = FakeDevice('1')
            device.frame = altered
            runner = Runner(device, detector, Settings(package='com.example.sword', diagnostics=False), lambda _: None)
            runner.step(100)
            self.assertEqual(device.taps, [(400, 800)] if trigger == 0 else [(1600, 2800)])

    def test_two_regions_can_fully_overlap_but_each_needs_a_tap(self):
        same = [BOX_A, BOX_A]
        record_step(self.folder, 'Overlay', frame(), same, taps=[(20, 30), (40, 50)])
        with self.assertRaisesRegex(ValueError, 'Each detection region'):
            record_step(self.folder, 'No second tap', frame(), same, taps=[(20, 30)])

    def test_edit_in_place_updates_templates_capture_and_actions(self):
        key = record_step(self.folder, 'Original', frame(5), [BOX_A], taps=[(20, 30)])
        old_info = self.metadata()['actions'][key]
        old_probe = self.metadata()['states'][key][0]['file']
        result = record_step(self.folder, 'Changed', frame(8), [BOX_B],
                             taps=[(333, 444)], retry=True, end=True, key=key)
        self.assertEqual(result, key)
        meta = self.metadata()
        self.assertEqual(len(meta['states']), 1)
        self.assertEqual(meta['actions'][key]['label'], 'Changed')
        self.assertTrue(meta['actions'][key]['end'])
        self.assertTrue(meta['actions'][key]['retry'])
        self.assertFalse((self.folder/'anchors'/old_probe).exists())
        self.assertFalse((self.folder/'captures'/old_info['capture']).exists())
        self.assertEqual(ScreenDetector(self.folder).detect(frame(8)).state, key)
        self.assertEqual(load_step(self.folder, key)[2], [(333, 444)])

    def test_reorder_changes_display_only_and_survives_reload(self):
        a = record_step(self.folder, 'A', frame(11), [BOX_A], taps=[(1, 2)])
        b = record_step(self.folder, 'B', frame(22), [BOX_A], taps=[(3, 4)])
        detector_before = ScreenDetector(self.folder)
        self.assertEqual(detector_before.detect(frame(11)).state, a)
        reorder_steps(self.folder, [b, a])
        self.assertEqual(step_order(self.metadata()), [b, a])
        self.assertEqual(ScreenDetector(self.folder).detect(frame(11)).state, a)
        with self.assertRaises(ValueError):
            reorder_steps(self.folder, [a, a])
        remove_step(self.folder, b)
        self.assertEqual(step_order(self.metadata()), [a])

    def test_duplicate_label_variants_are_one_logical_visit(self):
        a = record_step(self.folder, 'Open inventory', frame(101), [BOX_A], taps=[(10, 20)])
        b = record_step(self.folder, 'Open inventory', frame(102), [BOX_A], taps=[(30, 40)])
        another = record_step(self.folder, 'Next page', frame(103), [BOX_A], taps=[(50, 60)])
        detector = ScreenDetector(self.folder)
        self.assertEqual(detector.detect(frame(101)).state, a)
        self.assertEqual(detector.detect(frame(102)).state, b)
        self.assertEqual(detector.detect(frame(103)).state, another)
        device = FakeDevice('1')
        device.frame = frame(101)
        runner = Runner(device, detector, Settings(package='com.example.sword', diagnostics=False), lambda _:None)
        runner.step(100)
        self.assertEqual(device.taps, [(40, 80)])
        device.frame = frame(102)
        runner.step(101)
        self.assertEqual(len(device.taps), 1, 'Alternating variants must not tap again on same visit')
        device.frame = frame(103)
        runner.step(102)
        self.assertEqual(len(device.taps), 2)
        device.frame = frame(102)
        runner.step(103)
        self.assertEqual(device.taps[-1], (120, 160))
        self.assertEqual(len(device.taps), 3)

    def test_legacy_pair_is_still_and_matched_and_needs_recapture_for_edit(self):
        source = frame()
        key = record_step(self.folder, 'Old', source, [BOX_A, BOX_B], click=(110, 200))
        meta = self.metadata()
        meta['actions'][key].pop('taps')
        meta['actions'][key].pop('match')
        name = meta['actions'][key].pop('capture')
        (self.folder / 'manifest.json').write_text(json.dumps(meta), encoding='utf-8')
        (self.folder / 'captures' / name).unlink()
        info, boxes, taps, rendered = load_step(self.folder, key)
        self.assertTrue(info['_needs_recapture'])
        self.assertEqual(boxes, [BOX_A, BOX_B])
        self.assertEqual(taps, [(110, 200), None])
        self.assertEqual(rendered.shape, (960, 540, 3))
        detector = ScreenDetector(self.folder)
        altered = source.copy()
        x1,y1,x2,y2 = BOX_B
        altered[y1:y2, x1:x2] = 32
        self.assertEqual(detector.detect(altered).state, 'UNKNOWN')
        self.assertEqual(detector.detect(source).state, key)

    def test_identical_alternatives_have_order_independent_tie_break(self):
        source = frame(88)
        first = record_step(self.folder, 'Same page', source, [BOX_A], taps=[(111,222)])
        second = record_step(self.folder, 'Same page', source, [BOX_A], taps=[(333,444)])
        before = ScreenDetector(self.folder).detect(source).state
        reorder_steps(self.folder, [second, first])
        after = ScreenDetector(self.folder).detect(source).state
        self.assertEqual(before, after)
        self.assertIn(after, (first, second))

    def test_edit_two_pairs_down_to_one_removes_old_anchor(self):
        source = frame(42)
        key = record_step(self.folder, 'Two', source, [BOX_A, BOX_B],
                          taps=[(111,222),(333,444)])
        original = self.metadata()['states'][key]
        record_step(self.folder, 'One', source, [BOX_A], taps=[(111,222)], key=key)
        self.assertEqual(len(self.metadata()['states'][key]), 1)
        self.assertEqual(load_step(self.folder, key)[2], [(111,222)])
        for item in original:
            self.assertFalse((self.folder/'anchors'/item['file']).exists())

    def test_unknown_and_invalid_update_leave_manifest_unchanged(self):
        old = record_step(self.folder, 'Safe', frame(1), [BOX_A], taps=[(1, 2)])
        before = (self.folder / 'manifest.json').read_bytes()
        with self.assertRaises(ValueError):
            record_step(self.folder, 'Broken', np.zeros((960,540,3),np.uint8), [BOX_B],
                        taps=[(1,2)], key=old)
        with self.assertRaises(ValueError):
            record_step(self.folder, 'Wrong key', frame(2), [BOX_A],
                        taps=[(1,2)], key='NOT_REAL')
        self.assertEqual((self.folder / 'manifest.json').read_bytes(), before)


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication
    from swordx.recorder import CaptureCanvas
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False


@unittest.skipUnless(GUI_AVAILABLE, 'PySide6 is not installed in this environment')
class CanvasHitTestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_right_click_prefers_tap_when_region_overlaps(self):
        canvas = CaptureCanvas()
        canvas.resize(400, 711)
        canvas.load(frame(), boxes=[BOX_A], taps=[(55, 100)])
        tap_pt = canvas._screen_point(55, 100)
        self.assertTrue(canvas.remove_at(tap_pt))
        self.assertIsNone(canvas.taps[0])
        self.assertEqual(canvas.boxes[0], BOX_A)
        self.assertTrue(canvas.remove_at(canvas._screen_point(85, 125)))
        self.assertIsNone(canvas.boxes[0])

    def test_deleting_first_region_promotes_second_pair(self):
        canvas = CaptureCanvas()
        canvas.resize(400, 711)
        canvas.load(frame(), boxes=[BOX_A, BOX_B], taps=[(200,200), (400,700)])
        canvas.remove_at(canvas._screen_point(50, 100))
        self.assertEqual(canvas.boxes, [BOX_B, None])
        self.assertEqual(canvas.taps, [(400,700), None])
