"""A delayed first ADB touch must not authorize a stale second command."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from omniflow.engine import Match, OmniRunner
from swordx.adb import ADBError
from swordx.workflows import create_workflow, record_step


class TimedDevice:
    def __init__(self, frame: np.ndarray, clock: list[float], tap_delay: float):
        self.frame, self.clock, self.tap_delay = frame, clock, tap_delay
        self.taps: list[tuple[int, int]] = []
        self.texts: list[str] = []

    def screenshot(self) -> np.ndarray:
        return self.frame.copy()

    def tap(self, x: int, y: int) -> None:
        self.taps.append((x, y))
        self.clock[0] += self.tap_delay

    def input_text(self, value: str) -> None:
        self.texts.append(value)


class InFlightFreshnessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = create_workflow('Input freshness', Path(temp.name))
        self.frame = np.full((960, 540, 3), (51, 58, 69), dtype=np.uint8)
        cv2.rectangle(self.frame, (150, 700), (440, 830), (101, 161, 198), -1)
        cv2.putText(self.frame, 'MATCH', (185, 773),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.65, (250, 250, 250), 4)
        self.key = record_step(self.folder, 'Match', self.frame,
                               [(155, 712, 434, 810)], taps=[(290, 770)])

    def runner(self, action: dict, tap_delay: float):
        manifest_path = self.folder / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['actions'][self.key]['action_chain'] = [action]
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        clock = [100.0]
        device = TimedDevice(self.frame, clock, tap_delay)
        runner = OmniRunner(device, self.folder, 'com.game')
        return runner, device, clock

    def test_stalled_first_double_tap_rearms_before_second_touch(self):
        runner, device, clock = self.runner(
            {'kind': 'double_tap', 'point': [290, 770]}, tap_delay=7.0)
        with patch('omniflow.engine.time.monotonic', side_effect=lambda: clock[0]), \
             patch.object(runner.detector, 'detect',
                          return_value=Match(self.key, 'Match', 1.0)):
            runner.step(100)
            with self.assertRaisesRegex(ADBError, 'stale during input'):
                runner.step(101)
            self.assertEqual(len(device.taps), 1)
            self.assertEqual(runner.current, '')
            self.assertEqual(runner._streak, 0)
            device.tap_delay = 0
            runner.step(102)
            self.assertEqual(len(device.taps), 1)
            runner.step(103)
        self.assertEqual(len(device.taps), 3)

    def test_stalled_field_focus_does_not_type_into_unknown_screen(self):
        runner, device, clock = self.runner(
            {'kind': 'field_text', 'point': [290, 770], 'text': 'Hello world'},
            tap_delay=7.0)
        with patch('omniflow.engine.time.monotonic', side_effect=lambda: clock[0]), \
             patch.object(runner.detector, 'detect',
                          return_value=Match(self.key, 'Match', 1.0)):
            runner.step(100)
            with self.assertRaisesRegex(ADBError, 'stale during input'):
                runner.step(101)
            self.assertEqual(len(device.taps), 1)
            self.assertEqual(device.texts, [])
            device.tap_delay = 0
            runner.step(102)
            self.assertEqual(device.texts, [])
            runner.step(103)
        self.assertEqual(device.texts, ['Hello world'])

    def test_follow_up_commands_are_allowed_with_fresh_capture(self):
        for action, expected_taps, expected_text in (
            ({'kind': 'double_tap', 'point': [290, 770]}, 2, []),
            ({'kind': 'field_text', 'point': [290, 770], 'text': 'Hello world'},
             1, ['Hello world']),
        ):
            with self.subTest(kind=action['kind']):
                runner, device, clock = self.runner(action, tap_delay=5.0)
                with patch('omniflow.engine.time.monotonic',
                           side_effect=lambda: clock[0]), \
                     patch.object(runner.detector, 'detect',
                                  return_value=Match(self.key, 'Match', 1.0)):
                    runner.step(100)
                    runner.step(101)
                self.assertEqual(len(device.taps), expected_taps)
                self.assertEqual(device.texts, expected_text)


if __name__ == '__main__':
    unittest.main()
