"""Regression tests for long-running sessions, freezes, profiles and recorder."""
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from swordx.config import ProfileStore, Settings
from swordx.detection import ScreenDetector
from swordx.engine import Runner
from swordx.workflows import create_workflow, record_step, list_workflows, remove_step
from test_engine import FakeDevice

ROOT = Path(__file__).resolve().parents[1]


class ReliabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detector = ScreenDetector(ROOT / 'assets')

    def runner(self, initial='9'):
        device = FakeDevice(initial)
        logs, counts = [], []
        runner = Runner(device, self.detector, Settings(package='com.example.sword', diagnostics=False),
                        logs.append, completed=counts.append)
        return device, runner, logs, counts

    def test_many_loops_count_once_only_after_confirmed_exit(self):
        device, runner, _, counts = self.runner()
        clock = 100
        for i in range(20):
            device.select('9')
            runner.step(clock)
            device.select('10')
            runner.step(clock + 3)
            runner.step(clock + 4)  # Same results, not a second completion.
            self.assertEqual(runner.completed, i)
            device.select('1')
            runner.step(clock + 6)
            self.assertEqual(runner.completed, i + 1)
            clock += 12
        self.assertEqual(counts, list(range(1, 21)))

    def test_no_duplicate_match_taps_in_queued_state(self):
        device, runner, _, _ = self.runner('4')
        for point in ((198,1297), (310,1319), (351,1299)):
            from swordx.detection import to_device
            x,y = to_device(*point, 540,960)
            device.frame[y-3:y+4,x-3:x+4] = (98,210,205)
        for moment in (100, 110, 175, 220):
            runner.step(moment)
        self.assertEqual(len(device.taps), 1)
        self.assertEqual(device.force_stops, [])

    def test_stalled_recognized_page_restarts_and_resets_action(self):
        device, runner, logs, _ = self.runner('4')
        runner.step(100)
        runner.step(345)
        self.assertEqual(device.force_stops, ['com.example.sword'])
        self.assertEqual(device.launches, ['com.example.sword'])
        self.assertEqual(runner.last_state, '')
        self.assertIn('Stuck', ' '.join(logs))

    def test_frozen_unknown_screen_forces_recovery(self):
        device, runner, _, _ = self.runner()
        device.frame = np.zeros((960,540,3), dtype=np.uint8)
        for now in (100,110,120,150,180):
            runner.step(now)
        self.assertEqual(device.force_stops, ['com.example.sword'])
        self.assertEqual(device.launches, ['com.example.sword'])

    def test_different_combat_frames_do_not_force_restart(self):
        device, runner, _, _ = self.runner()
        for now in (100,110,130,150,180,220,260):
            device.frame = np.full((960,540,3), (now % 110)*2, dtype=np.uint8)
            runner.step(now)
        self.assertFalse(device.force_stops)

    def test_backgrounded_game_recovers(self):
        device, runner, logs, _ = self.runner()
        device.frame = np.zeros((960,540,3), dtype=np.uint8)
        device.foreground_package = 'com.android.launcher'
        runner.step(100)
        self.assertEqual(device.launches, ['com.example.sword'])

    def test_interrupted_results_does_not_count(self):
        device, runner, _, counts = self.runner('10')
        runner.step(100)
        device.frame = np.zeros((960,540,3), dtype=np.uint8)
        device.process_running = False
        device.foreground_package = 'com.android.launcher'
        runner.step(130)
        runner.step(141)  # Confirm game closed; pending completion is discarded.
        device.process_running = True
        device.foreground_package = 'com.example.sword'
        device.select('1')
        runner.step(160)
        self.assertEqual(counts, [])


class ProfileTests(unittest.TestCase):
    def test_profiles_keep_separate_settings_and_atomic_run_totals(self):
        with tempfile.TemporaryDirectory() as temp:
            a = ProfileStore(Path(temp))
            a.create('Farm Main', Settings(package='com.main'))
            a.create('Farm Alt', Settings(package='com.alt'))
            for _ in range(12):
                a.increment('Farm Main')
            a.increment('Farm Alt')
            b = ProfileStore(Path(temp))
            self.assertEqual(b.get('Farm Main').package, 'com.main')
            self.assertEqual(b.get('Farm Alt').package, 'com.alt')
            self.assertEqual(b.runs('Farm Main'), 12)
            self.assertEqual(b.runs('Farm Alt'), 1)
            self.assertEqual(b.active(), 'Farm Alt')

    def test_profile_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ProfileStore(Path(temp))
            with self.assertRaises(ValueError):
                store.create('../escape')
            store.create('Night Runs')
            with self.assertRaises(ValueError):
                store.create('night runs')


class RecorderTests(unittest.TestCase):
    def test_new_workflow_detects_saved_screen_and_uses_custom_target(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            folder = create_workflow('My Training', base)
            self.assertIn('My Training', list_workflows(base))
            frame = cv2.imread(str(ROOT / 'tests/fixtures/2.jpg'))
            boxes = [(17,116,158,138), (72,186,202,282)]
            key = record_step(folder, 'Open dungeon', frame, boxes, (200,240), end=True)
            detector = ScreenDetector(folder)
            self.assertEqual(detector.detect(frame).state, key)
            device = FakeDevice('2')
            runner = Runner(device, detector, Settings(package='com.example.sword', diagnostics=False), lambda _:None)
            runner.step(100)
            self.assertEqual(device.taps, [(800, 960)])
            runner.step(200)
            self.assertEqual(len(device.taps), 1)  # A recorded tap is one-shot by default.
            remove_step(folder, key)
            self.assertFalse(json.loads((folder/'manifest.json').read_text())['states'])

    def test_recorder_allows_duplicate_names_and_overlapping_regions(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = create_workflow('test', Path(temp))
            frame = cv2.imread(str(ROOT / 'tests/fixtures/2.jpg'))
            boxes = [(17,116,158,138), (17,116,158,138)]
            first = record_step(folder, 'First', frame, boxes, taps=[(200,240),(210,260)])
            second = record_step(folder, 'first', frame, [boxes[0]], taps=[(201,241)])
            self.assertNotEqual(first, second)
            self.assertEqual(len(json.loads((folder/'manifest.json').read_text())['states']), 2)
            with self.assertRaises(ValueError):
                record_step(folder, 'blank', np.zeros((960,540,3), dtype=np.uint8),
                            [boxes[0]], taps=[(200,240)])
            with self.assertRaises(ValueError):
                record_step(folder, 'Incomplete', frame, boxes, taps=[(200,240)])


if __name__ == '__main__':
    unittest.main()
