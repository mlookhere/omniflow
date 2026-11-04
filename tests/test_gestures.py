"""Swipe/hold recording and dispatch regressions (no emulator needed)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from swordx.adb import ADB
from swordx.config import Settings
from swordx.detection import ScreenDetector
from swordx.engine import Runner
from swordx.workflows import create_workflow, load_step, record_step, reorder_steps
from test_engine import FakeDevice

BOX_A = (60, 100, 120, 150)
BOX_B = (200, 300, 270, 350)


def screen(seed=10):
    rng = np.random.default_rng(seed)
    out = np.full((960, 540, 3), 36, dtype=np.uint8)
    for x1, y1, x2, y2 in (BOX_A, BOX_B):
        out[y1:y2, x1:x2] = rng.integers(0, 256, (y2-y1, x2-x1, 3), dtype=np.uint8)
    return out


class SwipeDevice(FakeDevice):
    def __init__(self, initial):
        super().__init__('1')
        self.frame = initial
        self.swipes = []

    def swipe(self, x1, y1, x2, y2, duration_ms):
        self.swipes.append((x1, y1, x2, y2, duration_ms))


class GestureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = create_workflow('Gesture Recording', Path(directory.name))
        self.settings = Settings(package='com.example.sword', diagnostics=False)

    def runner(self, frame):
        device = SwipeDevice(frame)
        runner = Runner(device, ScreenDetector(self.folder), self.settings, lambda _: None)
        return device, runner

    def test_single_region_drag_is_saved_detected_and_swiped_once(self):
        shot = screen()
        gesture = {'kind': 'drag', 'start': [80, 700], 'end': [80, 260], 'duration_ms': 850}
        key = record_step(self.folder, 'Scroll down', shot, [BOX_A], gestures=[gesture])
        action, boxes, taps, screenshot = load_step(self.folder, key)
        self.assertEqual(action['gestures'], [gesture])
        self.assertEqual(boxes, [BOX_A])
        self.assertEqual(taps, [None])
        self.assertEqual(screenshot.shape, shot.shape)
        device, runner = self.runner(shot)
        self.assertEqual(runner.step(now=10).state, key)
        runner.step(now=20)
        self.assertEqual(device.swipes, [(320, 2800, 320, 1040, 850)])
        self.assertEqual(device.taps, [])

    def test_second_region_routes_to_its_own_drag_not_tap(self):
        shot = screen()
        gestures = [{'kind': 'tap', 'point': [105, 205]},
                    {'kind': 'drag', 'start': [400, 800], 'end': [400, 360], 'duration_ms': 1200}]
        key = record_step(self.folder, 'Two behaviors', shot, [BOX_A, BOX_B], gestures=gestures)
        metadata = json.loads((self.folder/'manifest.json').read_text())
        self.assertEqual(metadata['actions'][key]['match'], 'any')
        # Destroy first template only; strongest remaining match must drive drag 2.
        altered = shot.copy()
        x1,y1,x2,y2 = BOX_A
        altered[y1:y2,x1:x2] = 36
        device, runner = self.runner(altered)
        observed = runner.step(now=100)
        self.assertEqual((observed.state, observed.trigger), (key, 1))
        self.assertEqual(device.swipes, [(1600, 3200, 1600, 1440, 1200)])
        self.assertEqual(device.taps, [])
        # And conversely, the first region triggers just its tap.
        altered = shot.copy()
        x1,y1,x2,y2 = BOX_B
        altered[y1:y2,x1:x2] = 36
        device, runner = self.runner(altered)
        observed = runner.step(now=100)
        self.assertEqual((observed.state, observed.trigger), (key, 0))
        self.assertEqual(device.taps, [(420, 820)])
        self.assertEqual(device.swipes, [])

    def test_same_start_end_is_supported_for_long_press(self):
        shot = screen()
        record_step(self.folder, 'Hold button', shot, [BOX_A],
                    gestures=[{'kind':'drag', 'start':[100,450], 'end':[100,450],
                               'duration_ms':1750}])
        device, runner = self.runner(shot)
        runner.step(now=100)
        self.assertEqual(device.swipes, [(400, 1800, 400, 1800, 1750)])

    def test_editing_drag_duration_does_not_duplicate_state_or_change_order(self):
        shot = screen()
        key = record_step(self.folder, 'Old', shot, [BOX_A], gestures=[
            {'kind':'drag','start':[200,800], 'end':[200,200], 'duration_ms':500}])
        updated = {'kind':'drag','start':[250,790], 'end':[250,150], 'duration_ms':950}
        record_step(self.folder, 'Updated', shot, [BOX_A], gestures=[updated], key=key)
        metadata = json.loads((self.folder/'manifest.json').read_text())
        self.assertEqual(list(metadata['states']), [key])
        self.assertEqual(load_step(self.folder, key)[0]['gestures'], [updated])
        reorder_steps(self.folder, [key])
        device, runner = self.runner(shot)
        runner.step(now=100)
        self.assertEqual(device.swipes, [(1000, 3160, 1000, 600, 950)])

    def test_invalid_drag_rejected_without_mutating_existing_state(self):
        shot = screen()
        key = record_step(self.folder, 'Untouched', shot, [BOX_A], gestures=[
            {'kind':'tap','point':[120,250]}])
        before = (self.folder / 'manifest.json').read_bytes()
        valid = {'kind':'drag', 'start':[100,100], 'end':[100,250], 'duration_ms':750}
        invalid = [
            {**valid, 'duration_ms':0}, {**valid, 'duration_ms':10001},
            {**valid, 'duration_ms':'850'}, {**valid, 'end':[100,960]},
            {**valid, 'start':[-1,100]}, {**valid, 'start':[True,100]},
            {'kind':'drag','start':[100,100],'duration_ms':500},
            {'kind':'unknown','point':[100,100]},
        ]
        for gesture in invalid:
            with self.subTest(gesture=gesture), self.assertRaises(ValueError):
                record_step(self.folder, 'Invalid', shot, [BOX_A], gestures=[gesture], key=key)
        with self.assertRaises(ValueError):
            record_step(self.folder, 'Invalid', shot, [BOX_A,BOX_B], gestures=[valid], key=key)
        self.assertEqual((self.folder / 'manifest.json').read_bytes(), before)

    def test_legacy_saved_taps_remain_compatible(self):
        shot = screen()
        key = record_step(self.folder, 'Legacy', shot, [BOX_A], taps=[(70, 80)])
        info, _, taps, _ = load_step(self.folder, key)
        self.assertNotIn('gestures', info)
        self.assertEqual(taps, [(70,80)])
        device, runner = self.runner(shot)
        runner.step(now=100)
        self.assertEqual(device.taps, [(280,320)])

    def test_adb_sends_android_swipe_with_duration(self):
        adb = ADB(serial='127.0.0.1:7555')
        with patch.object(adb, '_run', return_value=b'') as run:
            adb.swipe(100,200,100,900,1750)
        self.assertEqual(run.call_args.args,
                         ('shell','input','swipe','100','200','100','900','1750'))
        self.assertGreaterEqual(run.call_args.kwargs['timeout'], 10)


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from swordx.recorder import CaptureCanvas
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False


@unittest.skipUnless(GUI_AVAILABLE, 'PySide6 is not installed in this environment')
class GestureCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_drag_draw_edit_duration_remove_and_promote(self):
        canvas = CaptureCanvas()
        canvas.resize(400,711)
        canvas.show()
        canvas.load(screen(), boxes=[BOX_A,BOX_B])
        canvas.mode = 2
        start = canvas._screen_point(100,800)
        end = canvas._screen_point(100,300)
        QTest.mousePress(canvas, Qt.LeftButton, pos=start)
        QTest.mouseMove(canvas, end)
        QTest.mouseRelease(canvas, Qt.LeftButton, pos=end)
        self.assertEqual(canvas.gestures[0]['kind'], 'drag')
        self.assertEqual(canvas.gestures[0]['end'], [100,300])
        canvas.set_drag_duration(0, 1300)
        self.assertEqual(canvas.gestures[0]['duration_ms'], 1300)
        # Removing a drag that crosses a region must remove the drag first.
        self.assertTrue(canvas.remove_at(start))
        self.assertIsNone(canvas.gestures[0])
        self.assertEqual(canvas.boxes[0], BOX_A)
        canvas._set_gesture(1, {'kind':'tap', 'point':[250,800]})
        canvas.remove_at(canvas._screen_point(70,120))
        self.assertEqual(canvas.boxes[0], BOX_B)
        self.assertEqual(canvas.taps[0], (250,800))
        canvas.close()
