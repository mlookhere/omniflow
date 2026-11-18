"""No-tap states replace queue lifecycle guesses with visible-state truth."""
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from swordx.config import Settings
from swordx.detection import Observation, ScreenDetector
from swordx.engine import Runner
from swordx.workflows import (create_workflow, load_step, record_step,
                              update_step_settings)


class Device:
    def __init__(self, frame):
        self.frame = frame
        self.taps = []
        self.restarts = 0

    def screenshot(self):
        return self.frame.copy()

    def size(self):
        return 2160, 3840

    def tap(self, x, y):
        self.taps.append((x, y))

    def force_stop(self, package):
        self.restarts += 1

    def launch(self, package):
        pass

    def foreground(self):
        return 'com.example.sword'

    def running(self, package):
        return True


class PassiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = create_workflow('Queue', Path(self.tmp.name))
        self.match = self._frame('Match')
        self.cancel = self._frame('Cancel')
        self.button = (160, 720, 395, 815)
        self.match_key = record_step(self.folder, 'Start match', self.match,
                                     [self.button], taps=[(300, 773)],
                                     retry=True, retry_mode='on_return',
                                     retry_interval_seconds=40)
        self.wait_key = record_step(self.folder, 'Queue active — Cancel', self.cancel,
                                    [self.button], passive=True)

    @staticmethod
    def _frame(button):
        frame = np.full((960, 540, 3), (49, 62, 74), dtype=np.uint8)
        cv2.putText(frame, 'DIFFICULTY', (22, 82), cv2.FONT_HERSHEY_SIMPLEX,
                    0.9, (210, 245, 95), 2, cv2.LINE_AA)
        cv2.rectangle(frame, (160, 720), (395, 815), (92, 180, 205), -1)
        cv2.rectangle(frame, (162, 722), (393, 813), (61, 145, 178), 4)
        cv2.putText(frame, button, (190, 780), cv2.FONT_HERSHEY_SIMPLEX,
                    1.25, (245, 255, 255), 3, cv2.LINE_AA)
        return frame

    def _runner(self):
        detector = ScreenDetector(self.folder)
        device = Device(self.match)
        logs = []
        runner = Runner(device, detector,
                        Settings(package='com.example.sword', diagnostics=False), logs.append)
        return device, runner, logs

    def test_passive_wins_on_detection_and_has_no_tap(self):
        detector = ScreenDetector(self.folder)
        self.assertEqual(detector.detect(self.match).state, self.match_key)
        self.assertEqual(detector.detect(self.cancel).state, self.wait_key)
        info, boxes, taps, screen = load_step(self.folder, self.wait_key)
        self.assertTrue(info['passive'])
        self.assertEqual(boxes, [self.button])
        self.assertEqual(taps, [])
        self.assertIsNotNone(screen)
        device, runner, _ = self._runner()
        device.frame = self.cancel
        for moment in (1, 60, 300, 1000):
            runner.step(moment)
        self.assertEqual(device.taps, [])
        self.assertEqual(device.restarts, 0)

    def test_repeat_after_failed_acceptance_without_completed_run(self):
        device, runner, logs = self._runner()
        for start in (100, 200, 300, 400, 500):
            device.frame = self.match
            runner.step(start)
            self.assertEqual(len(device.taps), (start - 100)//100 + 1)
            device.frame = self.cancel
            for moment in (start+3, start+32, start+78):
                runner.step(moment)
                self.assertEqual(len(device.taps), (start - 100)//100 + 1)
        self.assertEqual(runner.completed, 0)
        self.assertEqual(device.restarts, 0)

    def test_same_action_with_animation_does_not_unlock_return_gate(self):
        device, runner, _ = self._runner()
        runner.step(100)
        animated = self.match.copy()
        animated[:500, :, :] = 230  # Animated screen, identical Match control.
        device.frame = animated
        runner.step(145)
        runner.step(190)
        self.assertFalse(runner.return_gates['start match'].departed)
        self.assertEqual(len(device.taps), 1)

    def test_minimum_retry_interval_not_a_forced_tap(self):
        device, runner, _ = self._runner()
        runner.step(100)
        device.frame = self.cancel
        runner.step(120)
        runner.step(159)
        self.assertEqual(len(device.taps), 1)
        device.frame = self.match
        runner.step(161)
        self.assertEqual(len(device.taps), 2)
        runner.step(300)
        self.assertEqual(len(device.taps), 2)

    def test_difficulty_state_cannot_override_cancel_even_with_higher_score(self):
        # A competing image matches a static decoration that is also visible
        # while the Cancel button is on screen; the negative still wins.
        key = record_step(self.folder, 'Choose difficulty', self.cancel,
                          [(18, 44, 165, 119)], taps=[(300, 910)], retry=True)
        detector = ScreenDetector(self.folder)
        self.assertEqual(detector.detect(self.cancel).state, self.wait_key)
        device, runner, _ = self._runner()
        device.frame = self.cancel
        runner.step(100)
        self.assertFalse(device.taps)

    def test_legacy_saved_action_can_be_marked_passive_without_recapture(self):
        before = json.loads((self.folder/'manifest.json').read_text())
        path = self.folder/'anchors'/before['states'][self.match_key][0]['file']
        crop = path.read_bytes()
        update_step_settings(self.folder, self.match_key, 'Match disabled', end=False,
                             retry=False, passive=True)
        self.assertEqual(crop, path.read_bytes())
        detector = ScreenDetector(self.folder)
        self.assertEqual(detector.detect(self.match).state, self.match_key)
        device = Device(self.match)
        Runner(device, detector, Settings(package='com.example.sword')).step(100)
        self.assertEqual(device.taps, [])
        update_step_settings(self.folder, self.match_key, 'Match enabled', end=False,
                             retry=True, passive=False)
        self.assertEqual(crop, path.read_bytes())
        device = Device(self.match)
        Runner(device, ScreenDetector(self.folder), Settings(package='com.example.sword')).step(100)
        self.assertEqual(len(device.taps), 1)

    def test_wait_state_without_saved_gesture_must_not_be_reenabled_as_action(self):
        with self.assertRaisesRegex(ValueError, 'Add a tap or drag'):
            update_step_settings(self.folder, self.wait_key, 'Tap now', end=False,
                                 retry=False, passive=False)

    def test_wait_state_rejects_attached_gesture(self):
        with self.assertRaisesRegex(ValueError, 'cannot have any taps'):
            record_step(self.folder, 'Bad', self.cancel, [self.button],
                        passive=True, gestures=[{'kind':'tap','point':[300, 773]}])


if __name__ == '__main__':
    unittest.main()


# Optional UI coverage: skipped in the headless build environment.
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication
    from unittest.mock import patch
    from swordx.recorder import RecorderDialog
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, 'PySide6 unavailable')
class PassiveGuiTests(unittest.TestCase):
    setUp = PassiveTests.setUp
    _frame = staticmethod(PassiveTests._frame)  # Reuse the real test fixture.
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_wait_state_single_save_no_tap(self):
        with patch('swordx.recorder.list_workflows', return_value={'Queue': self.folder}):
            editor = RecorderDialog('', 'Queue')
            try:
                editor._new_state(confirm=False)
                editor.step_name.setText('Cancel - wait')
                editor.canvas.load(self.cancel)
                editor.canvas.boxes[0] = self.button
                editor.passive.setChecked(True)
                self.assertEqual(editor._folder(), self.folder)
                expected_label = editor.step_name.text()  # Qt-provided text is what Save must persist.
                self.assertTrue(editor.save.isEnabled(), editor.save_status.text())
                with patch('swordx.recorder.QMessageBox.warning') as warning, \
                     patch('swordx.recorder.QMessageBox.information') as information:
                    editor.save.click()
                self.assertFalse(warning.called, str(warning.call_args_list))
                self.assertFalse(information.called, str(information.call_args_list))
                data = json.loads((self.folder/'manifest.json').read_text())
                saved = [v for v in data['actions'].values() if v['label'] == expected_label]
                self.assertEqual(len(saved), 1, f'saved labels={[v["label"] for v in data["actions"].values()]} expected={expected_label!r}')
                self.assertTrue(saved[0]['passive'])
                self.assertFalse(saved[0]['gestures'])
            finally:
                editor.close()