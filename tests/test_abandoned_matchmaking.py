"""Matchmaking acceptance can expire without showing another recorded state."""
import unittest
from pathlib import Path

import cv2

from swordx.config import Settings
from swordx.detection import Observation
from swordx.engine import Runner
from test_retry_modes import ControlledDevice

ROOT = Path(__file__).resolve().parents[1]


class MatchDetector:
    def __init__(self):
        self.state = 'MATCH'
        self.metadata = {'actions': {
            'MATCH': {'label': 'Start match', 'retry': True,
                      'retry_mode': 'on_return', 'retry_interval_seconds': 40,
                      'gestures': [{'kind': 'tap', 'point': [400, 820]}]},
        }}

    def detect(self, frame):
        return Observation(self.state, 1.0)


class ExpiredAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.match = cv2.imread(str(ROOT / 'tests/fixtures/4.jpg'))
        cls.invitation = cv2.imread(str(ROOT / 'tests/fixtures/1.jpg'))

    def setUp(self):
        self.device = ControlledDevice(self.match)
        self.detector = MatchDetector()
        self.logs = []
        self.runner = Runner(self.device, self.detector,
                             Settings(package='com.example.sword', diagnostics=False),
                             self.logs.append)

    def _expire_invitation(self):
        # The accept/countdown screen was never recorded or recognized.
        self.detector.state = 'UNKNOWN'
        self.device.frame = self.invitation.copy()
        self.runner.step(110)
        self.runner.step(113)
        self.detector.state = 'MATCH'

    def test_unrecognized_expired_acceptance_rearms_without_completed_run(self):
        self.runner.step(100)
        self.assertEqual(len(self.device.taps), 1)
        self._expire_invitation()
        self.assertTrue(self.runner.return_gates['start match'].departed)
        self.device.frame = self.match.copy()
        self.runner.step(141)
        self.assertEqual(len(self.device.taps), 2)
        self.assertEqual(self.runner.completed, 0)
        self.runner.step(200)
        self.assertEqual(len(self.device.taps), 2)  # Never cancels the new queue.
        self.assertFalse(self.device.restarts)

    def test_timer_is_only_a_minimum_not_a_forced_retry(self):
        self.runner.step(100)
        self._expire_invitation()
        self.device.frame = self.match.copy()
        self.runner.step(139)
        self.assertEqual(len(self.device.taps), 1)
        self.runner.step(140)
        self.assertEqual(len(self.device.taps), 2)

    def test_no_visible_departure_never_retries(self):
        self.runner.step(100)
        self.detector.state = 'UNKNOWN'
        for now in (110, 130, 160, 175):
            self.runner.step(now)
        self.detector.state = 'MATCH'
        self.runner.step(220)
        self.assertEqual(len(self.device.taps), 1)

    def test_unknown_overlay_and_cancel_button_must_not_tap(self):
        self.runner.step(100)
        self._expire_invitation()
        # Simulate the Cancel control occupying the original Match button.
        cancel = self.match.copy()
        cv2.rectangle(cancel, (360, 805), (445, 840), (45, 80, 100), -1)
        self.device.frame = cancel
        self.runner.step(160)
        self.runner.step(250)
        self.assertEqual(len(self.device.taps), 1)
        # Repeated recognitions cannot bypass target validation.
        self.device.frame = self.match.copy()
        self.runner.step(260)
        self.assertEqual(len(self.device.taps), 2)

    def test_difficulty_false_positive_cannot_tap_active_cancel(self):
        self.detector.metadata['actions']['DIFFICULTY'] = {
            'label': 'Choose hard', 'retry': True,
            'gestures': [{'kind': 'tap', 'point': [300, 920]}],
        }
        self.runner.actions = self.detector.metadata['actions']
        self.runner.labels['DIFFICULTY'] = 'Choose hard'
        self.runner.step(100)
        self._expire_invitation()
        cancel = self.match.copy()
        cv2.rectangle(cancel, (360, 805), (445, 840), (45, 80, 100), -1)
        self.device.frame = cancel
        self.detector.state = 'DIFFICULTY'
        self.runner.step(200)
        self.assertEqual(len(self.device.taps), 1)
        self.detector.state = 'MATCH'
        self.device.frame = self.match.copy()
        self.runner.step(210)
        self.assertEqual(len(self.device.taps), 2)

    def test_failed_acceptance_can_repeat_multiple_times(self):
        self.runner.step(100)
        for start in (150, 250, 350):
            self.detector.state = 'UNKNOWN'
            self.device.frame = self.invitation.copy()
            self.runner.step(start)
            self.runner.step(start + 3)
            self.device.frame = self.match.copy()
            self.detector.state = 'MATCH'
            self.runner.step(start + 15)
            self.runner.step(start + 20)
        self.assertEqual(len(self.device.taps), 4)
        self.assertEqual(self.runner.completed, 0)
        self.assertFalse(self.device.restarts)


if __name__ == '__main__':
    unittest.main()
