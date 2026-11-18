"""Per-state missed-click limits, legacy behavior, and saved image preservation."""
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from swordx.config import Settings
from swordx.detection import Observation, ScreenDetector
from swordx.engine import Runner
from swordx.workflows import create_workflow, record_step, update_step_settings


class Device:
    def __init__(self, frame):
        self.frame = frame
        self.taps = []
        self.restarts = []

    def screenshot(self):
        return self.frame.copy()

    def size(self):
        return 2160, 3840

    def tap(self, x, y):
        self.taps.append((x, y))

    def launch(self, package):
        pass

    def force_stop(self, package):
        self.restarts.append(package)

    def running(self, package):
        return True

    def foreground(self):
        return 'sword.game'


class ScriptedDetector:
    def __init__(self, actions, state):
        self.metadata = {'actions': actions}
        self.state = state

    def detect(self, frame):
        return Observation(self.state, 1.0)


class MissedClickRetryTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((960, 540, 3), np.uint8)
        self.device = Device(self.frame)
        self.actions = {
            'ACTION': {'label': 'Tap', 'retry': True, 'retry_mode': 'interval',
                       'retry_interval_seconds': 10, 'max_extra_taps': 2,
                       'gestures': [{'kind': 'tap', 'point': [300, 700]}]},
            'WAIT': {'label': 'Waiting', 'passive': True},
            'OTHER': {'label': 'Other', 'retry': False,
                      'gestures': [{'kind': 'tap', 'point': [100, 200]}]},
        }
        self.detector = ScriptedDetector(self.actions, 'ACTION')
        self.runner = Runner(self.device, self.detector,
                             Settings(package='sword.game', diagnostics=False), lambda _: None)

    def test_limits_extra_taps_until_state_leaves(self):
        for t in (100, 111, 122, 140, 160, 220):
            self.runner.step(t)
        self.assertEqual(len(self.device.taps), 3)  # first + two extra
        self.detector.state = 'WAIT'
        self.runner.step(225)
        self.detector.state = 'ACTION'
        self.runner.step(240)
        self.assertEqual(len(self.device.taps), 4)

    def test_negative_state_suppresses_retries(self):
        self.runner.step(100)
        self.detector.state = 'WAIT'
        for t in (110, 130, 160):
            self.runner.step(t)
        self.assertEqual(len(self.device.taps), 1)

    def test_default_without_cap_keeps_legacy_retries(self):
        self.actions['ACTION'].pop('max_extra_taps')
        for t in (100, 115, 130, 200, 260):
            self.runner.step(t)
        self.assertGreater(len(self.device.taps), 3)

    def test_on_return_ignores_interval_retry_cap(self):
        self.actions['ACTION']['retry_mode'] = 'on_return'
        self.runner.step(100)
        self.runner.step(200)
        self.assertEqual(len(self.device.taps), 1)

    def test_saved_cap_is_optional_and_templates_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = create_workflow('Retries', Path(temp))
            frame = np.full((960, 540, 3), 100, np.uint8)
            cv2.putText(frame, 'TAP', (200, 600), cv2.FONT_HERSHEY_SIMPLEX,
                        3, (255, 255, 255), 6)
            box = (170, 520, 390, 640)
            key = record_step(folder, 'Tap', frame, [box], taps=[(220, 600)],
                              retry=True, retry_interval_seconds=12, max_extra_taps=2)
            anchors = {p.name: p.read_bytes() for p in (folder / 'anchors').glob('*')}
            update_step_settings(folder, key, 'Tap again', end=False, retry=True,
                                 retry_interval_seconds=15, max_extra_taps=4)
            manifest = json.loads((folder / 'manifest.json').read_text())
            self.assertEqual(manifest['actions'][key]['max_extra_taps'], 4)
            self.assertEqual(anchors, {p.name: p.read_bytes() for p in (folder/'anchors').glob('*')})
            self.assertEqual(ScreenDetector(folder).detect(frame).state, key)
            update_step_settings(folder, key, 'Tap again', end=False, retry=True)
            manifest = json.loads((folder / 'manifest.json').read_text())
            self.assertNotIn('max_extra_taps', manifest['actions'][key])

    def test_invalid_caps_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = create_workflow('Retry test', Path(temp))
            frame = np.random.default_rng(42).integers(0, 255, (960, 540, 3), dtype=np.uint8)
            key = record_step(folder, 'Tap', frame, [(100, 100, 200, 200)], taps=[(100, 300)])
            before = (folder / 'manifest.json').read_bytes()
            for cap in (-1, True, 21, 1.5):
                with self.subTest(cap=cap), self.assertRaises(ValueError):
                    update_step_settings(folder, key, 'Tap', end=False, retry=True,
                                         max_extra_taps=cap)
            self.assertEqual((folder / 'manifest.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
