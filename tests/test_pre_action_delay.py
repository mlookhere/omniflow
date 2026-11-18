"""Deliberate per-state waits: nonblocking, cancellable and backward compatible."""
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from swordx.config import Settings
from swordx.detection import ScreenDetector
from swordx.engine import Runner
from swordx.workflows import create_workflow, load_step, record_step, update_step_delay
from test_engine import FakeDevice

ROOT = Path(__file__).resolve().parents[1]
BOX = (17, 116, 158, 138)


class PreActionDelayTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = create_workflow('Existing recording', Path(tmp.name))
        self.frame = cv2.imread(str(ROOT / 'tests/fixtures/2.jpg'))

    def record(self, delay=0, retry=False):
        return record_step(self.folder, 'Open dungeon', self.frame, [BOX],
                           taps=[(200, 240)], retry=retry, pre_tap_delay_seconds=delay)

    def runner(self, shot=None):
        device = FakeDevice('2')
        if shot is not None:
            device.frame = shot.copy()
        log = []
        runner = Runner(device, ScreenDetector(self.folder),
                        Settings(package='com.example.sword', diagnostics=False), log.append)
        return device, runner, log

    def test_zero_default_is_old_immediate_behavior(self):
        key = self.record()
        self.assertNotIn('pre_tap_delay_seconds', load_step(self.folder, key)[0])
        device, runner, _ = self.runner()
        runner.step(100)
        self.assertEqual(device.taps, [(800, 960)])

    def test_wait_starts_on_recognition_and_fires_once(self):
        self.record(125)
        device, runner, log = self.runner()
        for instant in [100, 135, 224.9]:
            runner.step(instant)
            self.assertEqual(device.taps, [])
        self.assertEqual(len([s for s in log if s.startswith('Waiting')]), 1)
        runner.step(225)
        self.assertEqual(device.taps, [(800, 960)])
        runner.step(226)
        self.assertEqual(len(device.taps), 1)

    def test_multi_minute_delay_never_triggers_recognized_stall(self):
        self.record(600)
        device, runner, _ = self.runner()
        for instant in [100, 200, 350, 500, 699]:
            runner.step(instant)
        self.assertEqual(device.force_stops, [])
        self.assertEqual(device.taps, [])
        runner.step(700)
        self.assertEqual(device.taps, [(800, 960)])
        self.assertEqual(device.force_stops, [])
        runner.step(939)
        self.assertEqual(device.force_stops, [])
        runner.step(941)
        self.assertEqual(device.force_stops, ['com.example.sword'])

    def test_wait_cancels_after_screen_departure_and_restarts_on_return(self):
        self.record(90)
        device, runner, _ = self.runner()
        runner.step(100)
        device.frame = np.zeros((960, 540, 3), dtype=np.uint8)
        runner.step(130)
        device.frame = self.frame.copy()
        runner.step(180)
        runner.step(220)  # Not 90 seconds after the ORIGINAL first sighting.
        self.assertEqual(device.taps, [])
        runner.step(270)
        self.assertEqual(device.taps, [(800, 960)])

    def test_pausing_does_not_consume_wait(self):
        self.record(120)
        device, runner, _ = self.runner()
        runner.step(100)
        runner.skip_paused_time(300)
        runner.step(400)
        self.assertEqual(device.taps, [])
        runner.step(519)
        self.assertEqual(device.taps, [])
        runner.step(520)
        self.assertEqual(device.taps, [(800, 960)])

    def test_delay_applies_to_swipes_as_well_as_taps(self):
        record_step(self.folder, 'Scroll', self.frame, [BOX],
                    gestures=[{'kind': 'drag', 'start': [50, 60], 'end': [50, 200],
                               'duration_ms': 900}], pre_tap_delay_seconds=60)
        device, runner, _ = self.runner()
        swipes = []
        device.swipe = lambda *args: swipes.append(args)
        runner.step(100)
        self.assertFalse(swipes)
        runner.step(160)
        self.assertEqual(swipes, [(200, 240, 200, 800, 900)])

    def test_live_preview_of_new_format_tap_does_not_stop_five_minute_wait(self):
        """Regression: the preview called _target(), which required obsolete action['tap']."""
        record_step(self.folder, 'Phone handoff', self.frame, [BOX],
                    gestures=[{'kind': 'tap', 'point': [200, 240]}],
                    pre_tap_delay_seconds=300)
        device, runner, log = self.runner()
        targets = []

        def render_overlay(observation, frame):
            if observation.state != 'UNKNOWN':
                targets.append(runner._target(observation.state, observation.trigger))

        runner.report = render_overlay
        for now in (100, 160, 300, 399):
            runner.step(now)
            self.assertEqual(device.taps, [])
        self.assertEqual(runner.wait_remaining, 1)
        self.assertEqual(targets, [(800, 960)] * 4)
        runner.step(400)
        self.assertEqual(device.taps, [(800, 960)])
        self.assertEqual(len([entry for entry in log if entry.startswith('Waiting')]), 1)
        runner.step(401)
        self.assertEqual(device.taps, [(800, 960)])

    def test_preview_marks_drag_start_while_delayed_then_executes_drag(self):
        record_step(self.folder, 'Scroll after handoff', self.frame, [BOX],
                    gestures=[{'kind': 'drag', 'start': [120, 300],
                               'end': [120, 600], 'duration_ms': 850}],
                    pre_tap_delay_seconds=90)
        device, runner, _ = self.runner()
        swipes = []
        device.swipe = lambda *args: swipes.append(args)
        overlays = []
        runner.report = lambda obs, frame: overlays.append(runner._target(obs.state, obs.trigger))
        runner.step(200)
        runner.step(289)
        self.assertEqual(overlays, [(480, 1200), (480, 1200)])
        self.assertEqual(swipes, [])
        runner.step(290)
        self.assertEqual(swipes, [(480, 1200, 480, 2400, 850)])
        self.assertEqual(device.taps, [])

    def test_preview_marks_second_pair_gesture_target(self):
        frame = self.frame.copy()
        first = BOX
        second = (200, 350, 270, 400)
        rng = np.random.default_rng(98)
        x1, y1, x2, y2 = second
        frame[y1:y2, x1:x2] = rng.integers(0, 256, (y2-y1, x2-x1, 3), dtype=np.uint8)
        record_step(self.folder, 'Choose best region', frame, [first, second],
                    gestures=[{'kind': 'tap', 'point': [170, 240]},
                              {'kind': 'drag', 'start': [410, 630],
                               'end': [410, 140], 'duration_ms': 800}],
                    pre_tap_delay_seconds=60)
        # First region no longer matches, so preview must show Drag 2's START.
        frame[first[1]:first[3], first[0]:first[2]] = 0
        device, runner, _ = self.runner(frame)
        swipes = []
        device.swipe = lambda *args: swipes.append(args)
        overlay = []
        runner.report = lambda obs, shot: overlay.append(runner._target(obs.state, obs.trigger))
        self.assertEqual(runner.step(100).trigger, 1)
        self.assertEqual(overlay, [(1640, 2520)])
        self.assertFalse(swipes)
        runner.step(160)
        self.assertEqual(swipes, [(1640, 2520, 1640, 560, 800)])

    def test_preview_of_legacy_tap_recording_remains_supported(self):
        self.record(60)
        device, runner, _ = self.runner()
        overlay = []
        runner.report = lambda obs, shot: overlay.append(runner._target(obs.state, obs.trigger))
        runner.step(100)
        self.assertEqual(overlay, [(800, 960)])
        self.assertFalse(device.taps)
        runner.step(160)
        self.assertEqual(device.taps, [(800, 960)])

    def test_save_only_delay_keeps_screenshot_and_template_byte_identical(self):
        key = self.record(0)
        original = json.loads((self.folder / 'manifest.json').read_text())
        action = original['actions'][key]
        screenshot = self.folder / 'captures' / action['capture']
        template = self.folder / 'anchors' / original['states'][key][0]['file']
        before = (screenshot.read_bytes(), template.read_bytes(), action.copy())
        update_step_delay(self.folder, key, 450)
        changed = json.loads((self.folder / 'manifest.json').read_text())
        self.assertEqual(changed['actions'][key]['pre_tap_delay_seconds'], 450)
        self.assertEqual(screenshot.read_bytes(), before[0])
        self.assertEqual(template.read_bytes(), before[1])
        self.assertEqual({k: v for k, v in changed['actions'][key].items()
                          if k != 'pre_tap_delay_seconds'}, before[2])
        self.assertEqual(ScreenDetector(self.folder).detect(self.frame).state, key)
        update_step_delay(self.folder, key, 0)
        self.assertNotIn('pre_tap_delay_seconds', load_step(self.folder, key)[0])

    def test_full_edit_of_existing_recording_preserves_delay_when_passed(self):
        key = self.record(0)
        update_step_delay(self.folder, key, 610)
        existing = load_step(self.folder, key)[0]
        record_step(self.folder, 'Edited', self.frame, [BOX], taps=[(200, 240)],
                    key=key, pre_tap_delay_seconds=existing['pre_tap_delay_seconds'])
        self.assertEqual(load_step(self.folder, key)[0]['pre_tap_delay_seconds'], 610)

    def test_nonrecaptured_legacy_recordings_support_delay_only(self):
        key = self.record()
        content = json.loads((self.folder / 'manifest.json').read_text())
        capture = content['actions'][key].pop('capture')
        (self.folder / 'manifest.json').write_text(json.dumps(content))
        (self.folder / 'captures' / capture).unlink()
        self.assertTrue(load_step(self.folder, key)[0]['_needs_recapture'])
        update_step_delay(self.folder, key, 300)
        self.assertEqual(load_step(self.folder, key)[0]['pre_tap_delay_seconds'], 300)

    def test_invalid_values_do_not_change_manifest(self):
        key = self.record()
        path = self.folder / 'manifest.json'
        original = path.read_bytes()
        for invalid in [-1, 86401, 4.5, True, None, '60']:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                update_step_delay(self.folder, key, invalid)
            self.assertEqual(path.read_bytes(), original)
            with self.assertRaises(ValueError):
                record_step(self.folder, 'Bad', self.frame, [BOX], taps=[(200, 240)],
                            pre_tap_delay_seconds=invalid)
        with self.assertRaises(ValueError):
            update_step_delay(self.folder, 'NO_SUCH_STATE', 60)
        self.assertEqual(path.read_bytes(), original)

    def test_two_same_named_variants_share_timer_and_retain_one_tap_guard(self):
        frame2 = self.frame.copy()
        rng = np.random.default_rng(53)
        x1, y1, x2, y2 = BOX
        frame2[y1:y2, x1:x2] = rng.integers(0, 255, (y2-y1, x2-x1, 3), dtype=np.uint8)
        self.record(120)
        record_step(self.folder, 'Open dungeon', frame2, [BOX],
                    taps=[(310, 320)], pre_tap_delay_seconds=120)
        device, runner, _ = self.runner()
        runner.step(100)
        device.frame = frame2
        runner.step(160)
        self.assertEqual(device.taps, [])
        runner.step(220)
        self.assertEqual(device.taps, [(1240, 1280)])
        device.frame = self.frame
        runner.step(221)
        self.assertEqual(len(device.taps), 1)


if __name__ == '__main__':
    unittest.main()
