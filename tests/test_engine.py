import unittest
from pathlib import Path

import cv2
import numpy as np

from swordx.config import Settings
from swordx.detection import ScreenDetector, to_device
from swordx.engine import Runner, TAPS

ROOT = Path(__file__).resolve().parents[1]


class FakeDevice:
    def __init__(self, number: str):
        self.select(number)
        self.taps = []
        self.launches = []
        self.process_running = True
        self.foreground_package = 'com.example.sword'
        self.force_stops = []

    def select(self, number: str):
        self.frame = cv2.imread(str(ROOT / 'tests' / 'fixtures' / (number + '.jpg')))

    def screenshot(self):
        return self.frame.copy()

    def size(self):
        return (2160, 3840)

    def tap(self, x, y):
        self.taps.append((x, y))

    def running(self, package):
        return self.process_running

    def foreground(self):
        return self.foreground_package

    def launch(self, package):
        self.launches.append(package)

    def force_stop(self, package):
        self.force_stops.append(package)

    def connect(self, endpoint):
        return 'connected'


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detector = ScreenDetector(ROOT / 'assets')

    def create(self, screenshot):
        device = FakeDevice(screenshot)
        logs = []
        runner = Runner(device, self.detector,
                        Settings(package='com.example.sword', diagnostics=False), logs.append)
        return device, runner, logs

    def test_each_state_clicks_the_correct_target(self):
        pairs = [('1', 'open_journal'), ('2', 'open_daily_dungeon'),
                 ('3', 'open_shrine'), ('4', 'select_normal'),
                 ('5', 'select_normal'), ('6', 'accept_invite'),
                 ('8', 'accept_invite'), ('9', 'ready'),
                 ('10', 'leave_results')]
        for screenshot, expected in pairs:
            with self.subTest(screenshot=screenshot):
                device, runner, _ = self.create(screenshot)
                runner.step(now=100)
                self.assertEqual(device.taps, [to_device(*TAPS[expected], 2160, 3840)])

    def test_sequencing_is_from_observation_not_previous_step(self):
        device, runner, _ = self.create('9')
        runner.step(now=100)
        self.assertEqual(len(device.taps), 1)
        device.select('10')
        runner.step(now=102)
        self.assertEqual(len(device.taps), 2)
        self.assertEqual(runner.completed, 0)  # Wait for results to be exited.
        device.select('2')
        runner.step(now=104)
        self.assertEqual(device.taps[-1], to_device(*TAPS['open_daily_dungeon'], 2160, 3840))
        self.assertEqual(runner.completed, 1)

    def test_ready_never_toggles_again_during_same_lobby(self):
        device, runner, _ = self.create('9')
        for moment in (100, 103, 110, 150, 200):
            runner.step(now=moment)
        self.assertEqual(len(device.taps), 1)

    def test_unrecognized_screen_never_taps(self):
        device, runner, _ = self.create('9')
        device.frame = np.zeros((960, 540, 3), np.uint8)
        runner.step(now=100)
        self.assertEqual(device.taps, [])

    def test_relaunches_only_after_detected_process_death(self):
        device, runner, _ = self.create('9')
        device.frame = np.zeros((960, 540, 3), np.uint8)
        device.process_running = False
        device.foreground_package = 'com.android.launcher'
        runner.last_known = 0
        runner.step(now=30)
        self.assertEqual(device.launches, [])  # One missed PID is not a crash.
        runner.step(now=41)
        self.assertEqual(device.launches, ['com.example.sword'])
        self.assertEqual(device.taps, [])

    def test_unknown_active_combat_is_left_alone(self):
        device, runner, _ = self.create('9')
        device.frame = np.zeros((960, 540, 3), np.uint8)
        runner.last_known = 0
        runner.step(now=30)
        self.assertEqual(device.launches, [])
        self.assertEqual(device.taps, [])


if __name__ == '__main__':
    unittest.main()
