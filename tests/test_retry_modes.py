"""Workflow-level retry safeguards for stateful Match/Cancel buttons."""
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from swordx.config import Settings
from swordx.detection import Observation, ScreenDetector
from swordx.engine import Runner
from swordx.workflows import create_workflow, load_step, record_step, update_step_retry

ROOT = Path(__file__).resolve().parents[1]
BOX = (17, 116, 158, 138)


class ControlledDevice:
    def __init__(self, frame):
        self.frame = frame.copy()
        self.taps = []
        self.restarts = 0

    def screenshot(self):
        return self.frame.copy()

    def size(self):
        return (2160, 3840)

    def tap(self, x, y):
        self.taps.append((x, y))

    def force_stop(self, package):
        self.restarts += 1

    def launch(self, package):
        pass

    def running(self, package):
        return True

    def foreground(self):
        return 'com.example.sword'


class ScriptedDetector:
    def __init__(self):
        self.state = 'MATCH'
        self.metadata = {'actions': {
            'MATCH': {'label': 'Start match', 'retry': True, 'retry_mode': 'on_return',
                      'retry_interval_seconds': 40, 'gestures': [{'kind':'tap', 'point':[400, 820]}]},
            'DIFFICULTY': {'label': 'Choose hard', 'retry': True,
                           'gestures': [{'kind':'tap', 'point':[310, 917]}]},
            'LOBBY': {'label':'Ready', 'retry': False,
                      'gestures': [{'kind':'tap', 'point':[300, 600]}]}
        }}

    def detect(self, frame):
        return Observation(self.state, 1.0)


class RetryModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.match_frame = cv2.imread(str(ROOT/'tests/fixtures/4.jpg'))
        cls.other_frame = cv2.imread(str(ROOT/'tests/fixtures/2.jpg'))

    def setup_runner(self):
        device = ControlledDevice(self.match_frame)
        detector = ScriptedDetector()
        logs = []
        runner = Runner(device, detector, Settings(package='com.example.sword', diagnostics=False), logs.append)
        return device, detector, runner, logs

    def test_queue_is_never_canceled_even_after_long_matchmaking(self):
        device, detector, runner, logs = self.setup_runner()
        for moment in (100, 125, 140, 141, 180, 360, 660, 1800):
            runner.step(moment)
        self.assertEqual(len(device.taps), 1)
        self.assertEqual(device.restarts, 0)
        self.assertIn('awaiting departure and return', ' '.join(logs))

    def test_same_page_alternative_detection_does_not_rearm_or_tap(self):
        device, detector, runner, _ = self.setup_runner()
        runner.step(100)
        detector.state = 'DIFFICULTY'  # Shared details screen, different detection region.
        runner.step(145)
        runner.step(165)
        detector.state = 'MATCH'
        runner.step(240)
        self.assertEqual(len(device.taps), 1)

    def test_unknown_during_queue_does_not_rearm_or_restart(self):
        device, detector, runner, _ = self.setup_runner()
        runner.step(100)
        detector.state = 'UNKNOWN'
        runner.step(180)
        runner.step(900)
        detector.state = 'MATCH'
        runner.step(999)
        self.assertEqual(len(device.taps), 1)
        self.assertFalse(device.restarts)

    def test_genuine_page_transition_then_return_rearms_once(self):
        device, detector, runner, _ = self.setup_runner()
        runner.step(100)
        detector.state = 'LOBBY'
        device.frame = self.other_frame.copy()
        runner.step(160)  # Recognized, visibly different page.
        self.assertTrue(runner.return_gates['start match'].departed)
        detector.state = 'MATCH'
        device.frame = self.match_frame.copy()
        runner.step(200)
        self.assertEqual(len(device.taps), 3)  # Match, Ready, Match again.
        runner.step(255)
        self.assertEqual(len(device.taps), 3)

    def test_min_interval_is_not_a_forced_retry(self):
        device, detector, runner, _ = self.setup_runner()
        runner.step(100)
        detector.state = 'LOBBY'
        device.frame = self.other_frame.copy()
        runner.step(110)
        detector.state = 'MATCH'
        device.frame = self.match_frame.copy()
        runner.step(120)
        self.assertEqual(len(device.taps), 2)
        runner.step(140)  # Minimum 40 seconds since first action.
        self.assertEqual(len(device.taps), 3)

    def test_completed_run_rearms_match_without_visual_departure(self):
        """A confirmed end-of-run must release a stale queue guard."""
        device, detector, runner, logs = self.setup_runner()
        detector.metadata['actions'].update({
            'RESULTS': {'label': 'Finish dungeon', 'end': True, 'retry': False,
                        'gestures': [{'kind': 'tap', 'point': [200, 800]}]},
            'AFTER_RESULTS': {'label': 'Leave results', 'retry': False,
                              'gestures': [{'kind': 'tap', 'point': [300, 800]}]},
        })
        runner.actions = detector.metadata['actions']
        runner.labels.update({key: value['label'] for key, value in runner.actions.items()})
        runner.step(100)  # Match starts; subsequent attempts must not cancel it.
        self.assertFalse(runner.return_gates['start match'].departed)
        detector.state = 'UNKNOWN'
        runner.step(170)
        detector.state = 'MATCH'
        runner.step(180)
        self.assertEqual(len(device.taps), 1)

        # Screens are deliberately identical to reproduce a missed visual
        # departure, although the recorded end marker is recognized.
        detector.state = 'RESULTS'
        runner.step(200)
        self.assertFalse(runner.return_gates['start match'].departed)
        detector.state = 'AFTER_RESULTS'
        runner.step(210)
        self.assertEqual(runner.completed, 1)
        self.assertTrue(runner.return_gates['start match'].departed)
        detector.state = 'MATCH'
        runner.step(220)
        self.assertEqual(len(device.taps), 3)  # Match, post-result, Match (same-frame END is guarded).
        for moment in (260, 320, 500):
            runner.step(moment)
        self.assertEqual(len(device.taps), 3)
        self.assertIn('Completed loops this session: 1', logs)

    def test_return_directly_to_match_after_end_marker(self):
        device, detector, runner, logs = self.setup_runner()
        detector.metadata['actions']['END'] = {
            'label': 'Finish', 'end': True, 'retry': False,
            'gestures': [{'kind': 'tap', 'point': [200, 800]}],
        }
        runner.actions = detector.metadata['actions']
        runner.labels['END'] = 'Finish'
        runner.step(100)
        detector.state = 'END'
        runner.step(200)
        detector.state = 'MATCH'
        runner.step(210)  # Also confirms completion (no intermediate page).
        self.assertEqual(runner.completed, 1)
        self.assertEqual(len(device.taps), 2)
        self.assertIn('Run finished; return-only actions re-armed: start match', logs)
        runner.step(300)
        self.assertEqual(len(device.taps), 2)

    def test_each_completed_run_rearms_match_once(self):
        device, detector, runner, _ = self.setup_runner()
        detector.metadata['actions'].update({
            'END': {'label': 'Finish dungeon', 'end': True, 'retry': False,
                    'gestures': [{'kind': 'tap', 'point': [200, 800]}]},
            'RETURN': {'label': 'Post-run menu', 'retry': False,
                       'gestures': [{'kind': 'tap', 'point': [250, 800]}]},
        })
        runner.actions = detector.metadata['actions']
        runner.labels.update({key: value['label'] for key, value in runner.actions.items()})
        for cycle in range(3):
            start = 100 + cycle * 200
            detector.state = 'MATCH'
            runner.step(start)
            runner.step(start + 60)
            detector.state = 'END'
            runner.step(start + 80)
            detector.state = 'RETURN'
            runner.step(start + 90)
        self.assertEqual(runner.completed, 3)
        self.assertEqual(len(device.taps), 6)  # Simulated END shares queue pixels; guarded.
        detector.state = 'MATCH'
        runner.step(710)
        self.assertEqual(len(device.taps), 7)
        runner.step(810)
        self.assertEqual(len(device.taps), 7)  # Never cancel the new queue.

    def test_completed_run_still_respects_minimum_retry_interval(self):
        device, detector, runner, _ = self.setup_runner()
        detector.metadata['actions'].update({
            'END': {'label': 'Finish', 'end': True, 'retry': False,
                    'gestures': [{'kind': 'tap', 'point': [200, 800]}]},
            'RETURN': {'label': 'Return', 'retry': False,
                       'gestures': [{'kind': 'tap', 'point': [220, 800]}]},
        })
        runner.actions = detector.metadata['actions']
        runner.labels.update({key: value['label'] for key, value in runner.actions.items()})
        runner.step(100)
        detector.state = 'END'
        runner.step(110)
        detector.state = 'RETURN'
        runner.step(115)
        detector.state = 'MATCH'
        runner.step(120)
        self.assertEqual(len(device.taps), 2)
        runner.step(141)
        self.assertEqual(len(device.taps), 3)

    def test_confirmed_process_failure_still_recovers_during_queue(self):
        device, detector, runner, _ = self.setup_runner()
        runner.step(100)
        detector.state = 'UNKNOWN'
        device.foreground = lambda: 'com.android.launcher'
        device.running = lambda package: False
        launches = []
        device.launch = launches.append
        runner.step(150)
        runner.step(165)
        self.assertEqual(launches, ['com.example.sword'])
        self.assertFalse(runner.return_gates)

    def test_legacy_interval_behavior_and_custom_interval(self):
        device, detector, runner, _ = self.setup_runner()
        detector.metadata['actions']['MATCH']['retry_mode'] = 'interval'
        detector.metadata['actions']['MATCH']['retry_interval_seconds'] = 60
        runner.actions = detector.metadata['actions']
        runner.step(100)
        runner.step(125)
        self.assertEqual(len(device.taps), 1)
        runner.step(160)
        self.assertEqual(len(device.taps), 2)

    def test_saved_retry_settings_do_not_modify_screenshots_or_templates(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = create_workflow('Safeguards', Path(temp))
            frame = cv2.imread(str(ROOT/'tests/fixtures/2.jpg'))
            key = record_step(folder, 'Match', frame, [BOX], (200, 240), retry=True)
            data = json.loads((folder/'manifest.json').read_text())
            image = folder/'captures'/data['actions'][key]['capture']
            crop = folder/'anchors'/data['states'][key][0]['file']
            before_image, before_crop = image.read_bytes(), crop.read_bytes()
            update_step_retry(folder, key, True, 'on_return', 40)
            modified = load_step(folder, key)[0]
            self.assertEqual(modified['retry_mode'], 'on_return')
            self.assertEqual(modified['retry_interval_seconds'], 40)
            self.assertEqual(image.read_bytes(), before_image)
            self.assertEqual(crop.read_bytes(), before_crop)
            self.assertEqual(ScreenDetector(folder).detect(frame).state, key)
            update_step_retry(folder, key, False, 'interval', 25)
            restored = load_step(folder, key)[0]
            self.assertFalse(restored['retry'])
            self.assertNotIn('retry_mode', restored)
            self.assertNotIn('retry_interval_seconds', restored)

    def test_invalid_retry_settings_do_not_mutate_files(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = create_workflow('Safeguards', Path(temp))
            frame = cv2.imread(str(ROOT/'tests/fixtures/2.jpg'))
            key = record_step(folder, 'Match', frame, [BOX], (200, 240), retry=True)
            path = folder/'manifest.json'
            before = path.read_bytes()
            for mode, seconds in [('none',25), ('on_return',0), ('interval',86401),
                                  ('interval',True), ('on_return', 2.5)]:
                with self.subTest(mode=mode,seconds=seconds), self.assertRaises(ValueError):
                    update_step_retry(folder, key, True, mode, seconds)
                self.assertEqual(before, path.read_bytes())


if __name__ == '__main__':
    unittest.main()
