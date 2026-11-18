"""ADB input must follow the current screen capture, not `wm size`.

Android may report a portrait base resolution after a game rotates to landscape.
The screenshots reflect the actual oriented display used by `adb input tap`.
"""
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from omniflow.engine import OmniRunner
from swordx.workflows import create_workflow, record_step


class RotatingDevice:
    def __init__(self, frame):
        self.frame = frame
        self.taps = []
        self.swipes = []
        self.size_queries = 0

    def screenshot(self):
        return self.frame.copy()

    def size(self):
        # wm size stays in portrait and should never be queried for input.
        self.size_queries += 1
        return 2160, 3840

    def tap(self, x, y):
        self.taps.append((x, y))

    def swipe(self, *args):
        self.swipes.append(args)

    def foreground(self):
        return 'com.example.game'

    def running(self, package):
        return True


class LiveScreenGeometryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = create_workflow('Rotate', Path(temp.name))
        # Deterministic nonconstant source with good template distinction.
        self.pattern = np.random.default_rng(520).integers(0, 256, (960, 540, 3), dtype=np.uint8)
        record_step(self.folder, 'Touch', self.pattern, [(110, 220, 360, 450)], taps=[(270, 480)])

    def runner(self, frame):
        device = RotatingDevice(frame)
        return device, OmniRunner(device, self.folder, 'com.example.game')

    def test_portrait_frame_is_the_input_coordinate_system(self):
        portrait = cv2.resize(self.pattern, (1080, 1920))
        device, runner = self.runner(portrait)
        runner.step(100)
        runner.step(101)
        self.assertEqual(device.taps, [(540, 960)])
        self.assertEqual(device.size_queries, 0)
        self.assertEqual(runner.device_size, (1080, 1920))

    def test_landscape_uses_current_screen_even_when_wm_size_is_portrait(self):
        landscape = cv2.resize(self.pattern, (1920, 1080))
        device, runner = self.runner(landscape)
        runner.step(100)
        runner.step(101)
        self.assertEqual(device.taps, [(960, 540)])
        self.assertEqual(device.size_queries, 0)
        self.assertEqual(runner.device_size, (1920, 1080))

    def test_swipe_endpoints_remain_inside_landscape_display(self):
        manifest_path = self.folder / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        action = next(iter(manifest['actions'].values()))
        action['action_chain'] = [
            {'kind': 'swipe', 'start': [0, 0], 'end': [540, 960], 'duration_ms': 700}
        ]
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        device, runner = self.runner(cv2.resize(self.pattern, (1920, 1080)))
        runner.step(100)
        runner.step(101)
        self.assertEqual(device.swipes, [(0, 0, 1919, 1079, 700)])
        self.assertEqual(device.size_queries, 0)

    def test_rotation_and_return_to_state_use_new_touch_geometry(self):
        wait_pattern = np.random.default_rng(521).integers(0, 256, (960, 540, 3), dtype=np.uint8)
        record_step(self.folder, 'Loading', wait_pattern, [(110, 220, 360, 450)], passive=True)
        portrait = cv2.resize(self.pattern, (1080, 1920))
        landscape = cv2.resize(self.pattern, (1920, 1080))
        device, runner = self.runner(portrait)
        runner.step(100)
        runner.step(101)
        self.assertEqual(device.taps, [(540, 960)])
        device.frame = cv2.resize(wait_pattern, (1920, 1080))
        runner.step(102)
        runner.step(103)
        self.assertEqual(runner.device_size, (1920, 1080))
        device.frame = landscape
        runner.step(104)
        runner.step(105)
        self.assertEqual(device.taps, [(540, 960), (960, 540)])
        self.assertEqual(device.size_queries, 0)



if __name__ == '__main__':
    unittest.main()
