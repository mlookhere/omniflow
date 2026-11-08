import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from swordx.adb import ADBError
from swordx.workflows import create_workflow, record_step, update_step_retry
from omniflow.engine import OmniDetector, OmniRunner,WorkflowError, Match


class Device:
    def __init__(self,frame):
        self.frame=frame
        self.taps=[]
        self.swipes=[]
        self.keys=[]
        self.texts=[]
        self.launches=[]
    def screenshot(self):return self.frame.copy()
    def size(self):return 2160,3840
    def tap(self,*args):self.taps.append(args)
    def swipe(self,*args):self.swipes.append(args)
    def keyevent(self,code):self.keys.append(code)
    def input_text(self,t):self.texts.append(t)
    def launch(self,package):self.launches.append(package)
    def foreground(self):return 'com.game.demo'
    def running(self,package):return True


def image(word):
    frame=np.full((960,540,3),(51,58,69),dtype=np.uint8)
    cv2.rectangle(frame,(150,700),(440,830),(101,161,198),-1)
    cv2.putText(frame,word,(185,773),cv2.FONT_HERSHEY_SIMPLEX,1.65,(250,250,250),4)
    return frame


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder=create_workflow('Game automation',Path(self.temp.name))
        self.match=image('MATCH');self.cancel=image('CANCEL')
        self.button=(155,712,434,810)
        self.key=record_step(self.folder,'Match',self.match,[self.button],taps=[(290,770)],retry=True,
                             retry_mode='on_return')
        self.wait=record_step(self.folder,'Cancel',self.cancel,[self.button],passive=True)

    def change(self,key,**changes):
        f=self.folder/'manifest.json';d=json.loads(f.read_text())
        d['actions'][key].update(changes);f.write_text(json.dumps(d))

    def test_constant_black_image_anchor_is_not_rejected(self):
        from swordx.detection import Anchor
        anchor = Anchor(np.zeros((20, 20), np.uint8), (100, 100, 120, 120))
        screen = np.full((960, 540), 255, np.uint8)
        screen[100:120, 100:120] = 0
        self.assertGreater(OmniDetector._match_template(screen, anchor), .98)
        screen[100:120, 100:120] = 120
        self.assertLess(OmniDetector._match_template(screen, anchor), .82)

    def test_already_normalized_frame_skips_duplicate_resize(self):
        from swordx.detection import normalize
        detector = OmniDetector(self.folder)
        # The pre-normalized input is exactly what OmniRunner passes.
        with patch('omniflow.engine.normalize', wraps=normalize) as resize:
            expected = detector.detect(self.match)
            resize.assert_not_called()
        self.assertEqual(expected.key, self.key)

        # Direct callers still normalize full-resolution captures once.
        enlarged = cv2.resize(self.match, (1080, 1920),
                              interpolation=cv2.INTER_NEAREST)
        with patch('omniflow.engine.normalize', wraps=normalize) as resize:
            actual = detector.detect(enlarged)
            resize.assert_called_once()
        self.assertEqual(actual.key, expected.key)
        self.assertAlmostEqual(actual.confidence, expected.confidence, places=3)

    def test_runner_normalizes_only_once_per_capture(self):
        from swordx.detection import normalize
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        with patch('omniflow.engine.normalize', wraps=normalize) as resize:
            runner.step(100)
            resize.assert_called_once()

    def test_malformed_device_frames_recover_without_leaking_queued_input(self):
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        found = Match(self.key, 'Match', 1.0)
        with patch.object(runner.detector, 'detect', return_value=found):
            runner.step(100)
            runner.step(101)
            self.assertEqual(len(device.taps), 1)
            for bad in (None, np.zeros((960, 540), np.uint8),
                        np.zeros((960, 540, 4), np.uint8),
                        np.zeros((960, 540, 3), np.float32),
                        np.zeros((0, 0, 3), np.uint8)):
                with self.subTest(frame=repr(getattr(bad, 'shape', None))), \
                     patch.object(device, 'screenshot', return_value=bad):
                    with self.assertRaisesRegex(ADBError, 'empty or unreadable'):
                        runner.step(102)
                self.assertEqual(len(device.taps), 1)
                self.assertEqual(runner.current, '')
                self.assertEqual(runner._streak, 0)
                self.assertIsNone(runner.last_frame)
                self.assertIsNone(runner.last_capture_latency_ms)
            runner.step(103)
            self.assertEqual(len(device.taps), 1)
            runner.step(104)
        self.assertEqual(len(device.taps), 2)

    def test_opencv_normalization_failure_rearms_capture_instead_of_stopping_worker(self):
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        found = Match(self.key, 'Match', 1.0)
        with patch.object(runner.detector, 'detect', return_value=found):
            runner.step(100)
            runner.step(101)
            with patch('omniflow.engine.normalize',
                       side_effect=cv2.error('resize failed')):
                with self.assertRaisesRegex(ADBError, 'normalization failed'):
                    runner.step(102)
            self.assertEqual(runner.current, '')
            self.assertEqual(runner._streak, 0)
            self.assertIsNone(runner.last_capture_latency_ms)
            runner.step(103)
            self.assertEqual(len(device.taps), 1)
            runner.step(104)
        self.assertEqual(len(device.taps), 2)

    def test_recognition_opencv_error_rearms_without_input_or_stale_telemetry(self):
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        found = Match(self.key, 'Match', 1.0)
        with patch.object(runner.detector, 'detect', return_value=found):
            runner.step(100)
            runner.step(101)
        self.assertEqual(len(device.taps), 1)

        with patch.object(runner.detector, 'detect',
                          side_effect=cv2.error('matchTemplate failed')):
            with self.assertRaisesRegex(ADBError, 'recognition failed'):
                runner.step(102)
        self.assertEqual(len(device.taps), 1)
        self.assertEqual(runner._streak, 0)
        self.assertEqual(runner.current, '')
        self.assertIsNone(runner.last_frame)
        self.assertEqual(runner.last_observation.key, '')
        self.assertIsNone(runner.last_capture_latency_ms)
        self.assertIsNone(runner.last_capture_stages_ms)
        with patch.object(runner.detector, 'detect', return_value=found):
            runner.step(103)
            self.assertEqual(len(device.taps), 1)
            runner.step(104)
        self.assertEqual(len(device.taps), 2)

    def test_preview_opencv_error_rearms_without_second_touch(self):
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        found = Match(self.key, 'Match', 1.0)
        with patch.object(runner.detector, 'detect', return_value=found):
            runner.step(100)
            runner.step(101)
            self.assertEqual(len(device.taps), 1)
            with patch.object(runner, 'on_image',
                              side_effect=cv2.error('resize failed')):
                with self.assertRaisesRegex(ADBError, 'preview processing failed'):
                    runner.step(102)
            self.assertIsNone(runner.last_frame)
            self.assertIsNone(runner.last_capture_latency_ms)
            self.assertEqual(runner._streak, 0)
            self.assertEqual(runner.current, '')
            runner.step(103)
            self.assertEqual(len(device.taps), 1)
            runner.step(104)
        self.assertEqual(len(device.taps), 2)

    def test_non_opencv_recognition_programming_errors_are_not_hidden(self):
        runner = OmniRunner(Device(self.match), self.folder, 'com.game.demo')
        with patch.object(runner.detector, 'detect',
                          side_effect=RuntimeError('invalid workflow code')):
            with self.assertRaisesRegex(RuntimeError, 'invalid workflow code'):
                runner.step(100)

    def test_stale_game_status_probe_rearms_before_relaunch(self):
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        clock = [100.0]
        unknown = Match('', 'Unknown', 0.0)
        def running(package):
            if clock[0] == 114.0:
                clock[0] += 7.0  # ADB app-status probe exceeds freshness.
            return False
        with patch.object(device, 'foreground', return_value='com.other.app'), \
             patch.object(device, 'running', side_effect=running), \
             patch.object(runner.detector, 'detect', return_value=unknown), \
             patch('omniflow.engine.time.monotonic',
                   side_effect=lambda: clock[0]):
            for now in (100, 101, 102, 103):
                runner.step(now)
            self.assertEqual(runner.missing_since, 103)
            clock[0] = 114.0
            with self.assertRaisesRegex(ADBError, 'stale during input'):
                runner.step(114)
            self.assertEqual(device.launches, [])
            self.assertIsNone(runner.missing_since)
            self.assertEqual(runner._streak, 0)
            self.assertEqual(runner.unknown_count, 0)
            self.assertIsNone(runner.last_frame)
            self.assertIsNone(runner.last_capture_latency_ms)
            for now in (125, 126, 127, 128):
                clock[0] = float(now)
                runner.step(now)
            self.assertEqual(runner.missing_since, 128)
            self.assertEqual(device.launches, [])
            clock[0] = 139.0
            runner.step(139)
        self.assertEqual(device.launches, ['com.game.demo'])
        self.assertIsNone(runner.missing_since)
        self.assertEqual(runner._streak, 0)
        self.assertIsNone(runner.last_frame)
        self.assertIsNone(runner.last_capture_latency_ms)

    def test_game_relaunch_still_requires_fresh_proof_and_respects_disable(self):
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        runner.last_capture_started_at = 125.0
        with patch.object(device, 'foreground', return_value='com.other.app'), \
             patch.object(device, 'running', return_value=False), \
             patch('omniflow.engine.time.monotonic', return_value=125.0):
            runner._monitor_game(100)
            runner._monitor_game(125)
        self.assertEqual(device.launches, ['com.game.demo'])
        self.assertIsNone(runner.missing_since)
        self.assertEqual(runner._streak, 0)
        other = OmniRunner(Device(self.match), self.folder, 'com.game.demo',
                           auto_relaunch=False)
        other._monitor_game(100)
        other._monitor_game(125)
        self.assertEqual(other.device.launches, [])

    def test_titlebar_cropped_screenshot_maps_touch_swipe_and_field_focus(self):
        from swordx.detection import capture_crop_top, normalize
        screenshot = np.zeros((1369, 749, 3), np.uint8)
        screenshot[:40] = 255
        screenshot[40:] = cv2.resize(self.match, (749, 1329))
        self.assertEqual(capture_crop_top(749, 1369), 40)
        self.assertEqual(normalize(screenshot).shape, (960, 540, 3))
        self.change(self.key, action_chain=[
            {'kind': 'tap', 'point': [290, 770]},
            {'kind': 'swipe', 'start': [100, 100], 'end': [100, 300],
             'duration_ms': 650},
            {'kind': 'field_text', 'point': [200, 400], 'text': 'Ready'},
        ])
        device = Device(screenshot)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        with patch.object(runner.detector, 'detect',
                          return_value=Match(self.key, 'Match', 1.0)):
            for now in range(100, 104):
                runner.step(now)
        def expected(x, y):
            return (min(748, round(x * 749 / 540)),
                    min(1368, 40 + round(y * 1329 / 960)))
        self.assertEqual(runner._capture_crop_top, 40)
        self.assertEqual(device.taps, [expected(290, 770), expected(200, 400)])
        self.assertEqual(device.swipes,
                         [(*expected(100, 100), *expected(100, 300), 650)])
        self.assertEqual(device.texts, ['Ready'])
        self.assertEqual(runner._map_point([0, 0]), (0, 40))
        self.assertEqual(runner._map_point([540, 960]), (748, 1368))

    def test_generic_crop_and_rotation_rearm_action_geometry(self):
        from swordx.detection import capture_crop_top
        self.assertEqual(capture_crop_top(1080, 1940), 20)
        self.assertEqual(capture_crop_top(1080, 1920), 0)
        self.assertEqual(capture_crop_top(1920, 1080), 0)
        device = Device(np.zeros((1940, 1080, 3), np.uint8))
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        found = Match(self.key, 'Match', 1.0)
        with patch.object(runner.detector, 'detect', return_value=found):
            runner.step(100)
            runner.step(101)
            self.assertEqual(runner._capture_crop_top, 20)
            self.assertEqual(device.taps[-1],
                             (round(290 * 1080 / 540),
                              20 + round(770 * 1920 / 960)))
            device.frame = np.zeros((1080, 1940, 3), np.uint8)
            runner.step(102)
            self.assertEqual(runner._capture_crop_top, 0)
            self.assertEqual(len(device.taps), 1)
            self.assertEqual(runner.current, '')
            runner.step(103)
        self.assertEqual(len(device.taps), 2)
        self.assertEqual(device.taps[-1],
                         (round(290 * 1940 / 540), round(770 * 1080 / 960)))

    def test_optional_stage_telemetry_preserves_custom_adapter_and_failure_reset(self):
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        found = Match(self.key, 'Match', 1.0)
        self.assertFalse(hasattr(device, 'screenshot_timed'))
        with patch.object(runner.detector, 'detect', return_value=found):
            runner.step(100)
            self.assertIsNone(runner.last_capture_stages_ms)
            with patch.object(device, 'screenshot_timed',
                              return_value=(self.match.copy(),
                                            {'adb_exec_ms': 1400.0,
                                             'png_decode_ms': 24.0}), create=True) as timed:
                runner.step(101)
            timed.assert_called_once_with()
            self.assertEqual(runner.last_capture_stages_ms,
                             {'adb_exec_ms': 1400.0, 'png_decode_ms': 24.0})
            self.assertEqual(len(device.taps), 1)
            with patch.object(device, 'screenshot_timed',
                              side_effect=ADBError('offline'), create=True):
                with self.assertRaises(ADBError):
                    runner.step(102)
            self.assertIsNone(runner.last_capture_stages_ms)
            self.assertEqual(runner.current, '')
            runner.step(103)
            self.assertEqual(len(device.taps), 1)
            runner.step(104)
        self.assertEqual(len(device.taps), 2)

    def test_geometry_change_reconfirms_state_before_new_input(self):
        device = Device(self.match)
        logs = []
        runner = OmniRunner(device, self.folder, 'com.game.demo', log=logs.append)
        found = Match(self.key, 'Match', 1.0)
        with patch.object(runner.detector, 'detect', return_value=found):
            runner.step(100)
            runner.step(101)
            self.assertEqual(len(device.taps), 1)

            # Same state still detected, but source screenshot is now landscape.
            device.frame = cv2.rotate(self.match, cv2.ROTATE_90_CLOCKWISE)
            runner.step(102)
            self.assertEqual(len(device.taps), 1)
            self.assertEqual(runner.device_size, (960, 540))
            self.assertEqual(runner.current, '')
            runner.step(103)
        self.assertEqual(len(device.taps), 2)
        self.assertTrue(any('Display geometry changed' in line for line in logs))

    def test_geometry_change_drops_pending_action_chain_and_completion(self):
        self.change(self.key, action_chain=[
            {'kind': 'tap', 'point': [290, 770]},
            {'kind': 'key', 'code': 4}], end=True)
        device = Device(self.match)
        completions = []
        runner = OmniRunner(device, self.folder, 'com.game.demo',
                            completed=completions.append)
        found = Match(self.key, 'Match', 1.0)
        with patch.object(runner.detector, 'detect', return_value=found):
            runner.step(100)
            runner.step(101)
            self.assertEqual(runner.chain_index, 1)
            self.assertEqual(len(device.taps), 1)

            # The queued keypress must not leak into the new display geometry.
            device.frame = cv2.rotate(self.match, cv2.ROTATE_90_CLOCKWISE)
            runner.step(102)
            self.assertEqual(runner.chain_index, 0)
            self.assertEqual(device.keys, [])
            self.assertEqual(completions, [])
            runner.step(103)
            self.assertEqual(runner.chain_index, 1)
            self.assertEqual(device.keys, [])
        self.assertEqual(len(device.taps), 2)

    def test_resolution_change_without_rotation_also_reconfirms(self):
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        found = Match(self.key, 'Match', 1.0)
        with patch.object(runner.detector, 'detect', return_value=found):
            runner.step(100)
            runner.step(101)
            device.frame = cv2.resize(self.match, (1080, 1920))
            runner.step(102)
            self.assertEqual(len(device.taps), 1)
            self.assertEqual(runner.device_size, (1080, 1920))
            runner.step(103)
        self.assertEqual(len(device.taps), 2)

    def test_slow_recognition_rejects_frame_before_input(self):
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        found = Match(self.key, 'Match', 1.0)
        with patch.object(runner.detector, 'detect', return_value=found), \
             patch('omniflow.engine.time.monotonic',
                   side_effect=[20.0, 20.4, 27.0]):
            with self.assertRaisesRegex(ADBError, 'stale after recognition'):
                runner.step(100)
        self.assertEqual(device.taps, [])
        self.assertIsNone(runner.last_frame)
        self.assertEqual(runner.last_observation.key, '')
        self.assertEqual(runner._streak, 0)

    def test_slow_recognition_never_publishes_stale_frame(self):
        device = Device(self.match)
        published = []
        runner = OmniRunner(device, self.folder, 'com.game.demo',
                            image=lambda *args: published.append(args))
        found = Match(self.key, 'Match', 1.0)
        with patch.object(runner.detector, 'detect', return_value=found), \
             patch('omniflow.engine.time.monotonic',
                   side_effect=[20.0, 20.4, 27.0]):
            with self.assertRaisesRegex(ADBError, 'stale after recognition'):
                runner.step(100)
        self.assertEqual(published, [])
        self.assertEqual(device.taps, [])
        self.assertIsNone(runner.last_capture_latency_ms)
        self.assertIsNone(runner.last_capture_stages_ms)
        self.assertEqual(runner._streak, 0)

    def test_stalled_preview_callback_cannot_authorize_tap(self):
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo',
                            image=lambda *_: None)
        found = Match(self.key, 'Match', 1.0)
        with patch.object(runner.detector, 'detect', return_value=found), \
             patch('omniflow.engine.time.monotonic',
                   side_effect=[20.0, 20.1, 20.2, 26.2]):
            with self.assertRaisesRegex(ADBError, 'stale after recognition'):
                runner.step(100)
        self.assertEqual(device.taps, [])

    def test_resume_after_pause_restarts_pending_chain_and_confirmation(self):
        self.change(self.key, action_chain=[
            {'kind': 'tap', 'point': [290, 770]},
            {'kind': 'key', 'code': 4}], end=True)
        device = Device(self.match)
        completed = []
        runner = OmniRunner(device, self.folder, 'com.game.demo',
                            completed=completed.append)
        found = Match(self.key, 'Match', 1.0)
        with patch.object(runner.detector, 'detect', return_value=found):
            runner.step(100)
            runner.step(101)
            self.assertEqual(runner.chain_index, 1)
            runner.resume_after_pause(200)
            self.assertEqual(runner.current, '')
            self.assertEqual(runner.chain_index, 0)
            self.assertFalse(runner.end_pending)
            runner.step(201)
            self.assertEqual(device.keys, [])
            runner.step(202)
        self.assertEqual(len(device.taps), 2)
        self.assertEqual(device.keys, [])
        self.assertEqual(completed, [])

    def test_stalled_capture_cannot_trigger_stale_input(self):
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        found = Match(self.key, 'Match', 1.0)
        with patch.object(runner.detector, 'detect', return_value=found):
            runner.step(100)
            runner.step(101)
            self.assertEqual(len(device.taps), 1)
            # No ADB exception was raised, but the decoded screenshot is
            # already too old to authorize a new action or chain progress.
            with patch('omniflow.engine.time.monotonic',
                       side_effect=[20.0, 27.0]):
                with self.assertRaisesRegex(ADBError, 'stale'):
                    runner.step(102)
            self.assertEqual(runner.current, '')
            self.assertIsNone(runner.last_frame)
            runner.step(103)
            self.assertEqual(len(device.taps), 1)
            runner.step(104)
        self.assertEqual(len(device.taps), 2)

    def test_runner_exposes_real_capture_latency_without_extra_clock_reads(self):
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        with patch('omniflow.engine.time.monotonic',
                   side_effect=[100.0, 101.5, 101.6, 101.7]):
            runner.step(100)
        self.assertAlmostEqual(runner.last_capture_latency_ms, 1500, places=3)
        # A subsequent failed screenshot cannot repeat the previous measurement.
        with patch.object(device, 'screenshot', side_effect=ADBError('offline')):
            with self.assertRaises(ADBError):
                runner.step(102)
        self.assertIsNone(runner.last_capture_latency_ms)

    def test_capture_age_within_limit_keeps_normal_recognition(self):
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        with patch('omniflow.engine.time.monotonic',
                   side_effect=[20.0, 21.8, 22.0, 22.1,
                                30.0, 31.2, 31.3, 31.4, 31.5]):
            runner.step(100)
            runner.step(101)
        self.assertEqual(len(device.taps), 1)

    def test_capture_failure_drops_pending_action_and_requires_fresh_frames(self):
        self.change(self.key, action_chain=[
            {'kind': 'tap', 'point': [290, 770]},
            {'kind': 'key', 'code': 4}], end=True)
        device = Device(self.match)
        completed = []
        runner = OmniRunner(device, self.folder, 'com.game.demo',
                            completed=completed.append)
        found = Match(self.key, 'Match', 1.0)
        with patch.object(runner.detector, 'detect', return_value=found):
            runner.step(100)
            runner.step(101)
            self.assertEqual(runner.chain_index, 1)
            self.assertEqual(len(device.taps), 1)
            with patch.object(device, 'screenshot', side_effect=ADBError('offline')):
                with self.assertRaisesRegex(ADBError, 'offline'):
                    runner.step(102)
            self.assertEqual(runner.chain_index, 0)
            self.assertEqual(runner.current, '')
            self.assertIsNone(runner.last_frame)
            self.assertEqual(runner.last_observation.key, '')
            self.assertFalse(runner.end_pending)
            runner.step(103)
            self.assertEqual(device.keys, [])
            self.assertEqual(len(device.taps), 1)
            runner.step(104)
        # The resumed workflow starts from the first action, not the old key.
        self.assertEqual(device.keys, [])
        self.assertEqual(len(device.taps), 2)
        self.assertEqual(completed, [])

    def test_failed_adb_input_reconfirms_before_retrying(self):
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        found = Match(self.key, 'Match', 1.0)
        with patch.object(runner.detector, 'detect', return_value=found):
            runner.step(100)
            with patch.object(device, 'tap', side_effect=ADBError('transport lost')):
                with self.assertRaisesRegex(ADBError, 'transport lost'):
                    runner.step(101)
            self.assertEqual(runner.current, '')
            self.assertEqual(runner.chain_index, 0)
            runner.step(102)
            self.assertEqual(device.taps, [])
            runner.step(103)
        self.assertEqual(len(device.taps), 1)

    def test_capture_error_clears_completion_that_cannot_be_observed(self):
        self.change(self.key, end=True)
        device = Device(self.match)
        runs = []
        runner = OmniRunner(device, self.folder, 'com.game.demo',
                            completed=runs.append)
        runner.step(100)
        runner.step(101)
        self.assertTrue(runner.end_pending)
        with patch.object(device, 'screenshot', side_effect=OSError('device disconnected')):
            with self.assertRaisesRegex(OSError, 'device disconnected'):
                runner.step(102)
        device.frame = self.cancel
        runner.step(103)
        runner.step(104)
        self.assertEqual(runs, [])

    def test_negative_wait_overrides_queued_action_and_rearms_on_return(self):
        device=Device(self.match)
        runner=OmniRunner(device,self.folder,'com.game.demo')
        for t in (100,101,150):runner.step(t)
        self.assertEqual(len(device.taps),1)
        for t in (151,152,200,300):device.frame=self.cancel;runner.step(t)
        self.assertEqual(len(device.taps),1)
        for t in (301,302,450):device.frame=self.match;runner.step(t)
        self.assertEqual(len(device.taps),2)

    def test_no_duplicate_tap_on_unchanged_state_and_no_run_without_end(self):
        device=Device(self.match);runs=[]
        r=OmniRunner(device,self.folder,'com.game.demo',completed=runs.append)
        for t in range(100,180):r.step(float(t))
        self.assertEqual(len(device.taps),1)
        self.assertEqual(runs,[])

    def test_bounded_retries(self):
        self.change(self.key,retry=True,retry_mode='interval',retry_interval_seconds=10,max_extra_taps=2)
        device=Device(self.match)
        r=OmniRunner(device,self.folder,'com.game.demo')
        for t in (100,101,102,111,112,113,121,122,144,200):r.step(t)
        self.assertEqual(len(device.taps),3)

    def test_saved_timed_retry_defaults_roundtrip_through_workflow_writer(self):
        # The writer omits retry_mode for 'interval'; the runner must
        # interpret the absent key using the *same* default.
        update_step_retry(self.folder, self.key, True, 'interval', 10)
        data = json.loads((self.folder / 'manifest.json').read_text())
        action = data['actions'][self.key]
        self.assertNotIn('retry_mode', action)
        self.assertEqual(action['retry_interval_seconds'], 10)
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        for t in (100, 101, 102, 110, 111, 112, 121):
            runner.step(float(t))
        self.assertEqual(len(device.taps), 3)
        self.assertEqual(runner.attempts, 3)

        # Explicit 'on_return' must never re-tap an unchanged screen.
        update_step_retry(self.folder, self.key, True, 'on_return', 10)
        action = json.loads((self.folder / 'manifest.json').read_text())[
            'actions'][self.key]
        self.assertEqual(action['retry_mode'], 'on_return')
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        for t in (100, 101, 102, 110, 111, 112, 121, 200):
            runner.step(float(t))
        self.assertEqual(device.taps, [(290, 770)])

    def test_action_chain_tap_swipe_hold_key_text_and_count(self):
        chain=[{'kind':'tap','point':[200,400]}, {'kind':'swipe','start':[100,100],'end':[100,300],'duration_ms':650},
               {'kind':'hold','point':[200,400],'duration_ms':700},
               {'kind':'key','code':4}, {'kind':'text','text':'hi world'}, {'kind':'count_run'}]
        self.change(self.key,action_chain=chain)
        device=Device(self.match);runs=[]
        r=OmniRunner(device,self.folder,'com.game.demo',completed=runs.append)
        for t in range(100,120):r.step(float(t))
        self.assertEqual(device.taps,[(200,400)])
        self.assertEqual(device.swipes,[(100,100,100,300,650),(200,400,200,400,700)])
        self.assertEqual(device.keys,[4]);self.assertEqual(device.texts,['hi world']);self.assertEqual(runs,[1])

    def test_saved_chain_fails_startup_before_an_earlier_valid_tap(self):
        manifest = self.folder / 'manifest.json'
        original = json.loads(manifest.read_text(encoding='utf-8'))
        bad_chains = [
            ([{'kind': 'tap', 'point': [200, 400]},
              {'kind': 'swipe', 'start': [200, 400],
               'end': [200, 600], 'duration_ms': 0}], 'action 2'),
            ([{'kind': 'tap', 'point': [200, 400]},
              {'kind': 'field_text', 'point': [200, 400],
               'text': 'unsafe;command'}], 'action 2'),
            ([{'kind': 'tap', 'point': [200, 400]},
              {'kind': 'teleport'}], 'action 2'),
            ({'kind': 'tap', 'point': [200, 400]}, 'action steps must be a list'),
        ]
        for chain, expected in bad_chains:
            with self.subTest(chain=chain):
                data = json.loads(json.dumps(original))
                data['actions'][self.key]['action_chain'] = chain
                manifest.write_text(json.dumps(data), encoding='utf-8')
                device = Device(self.match)
                with (patch.object(device, 'screenshot') as screenshot,
                      patch('omniflow.engine.OCREngine.read') as ocr):
                    with self.assertRaisesRegex(WorkflowError, expected):
                        OmniRunner(device, self.folder, 'com.game.demo')
                    screenshot.assert_not_called()
                    ocr.assert_not_called()
                self.assertFalse(device.taps)
                self.assertFalse(device.swipes)
                self.assertFalse(device.texts)
        manifest.write_text(json.dumps(original), encoding='utf-8')
        self.assertEqual(OmniRunner(
            Device(self.match), self.folder, 'com.game.demo').runs, 0)

    def test_all_legacy_gesture_alternatives_preflight_before_input(self):
        manifest = self.folder / 'manifest.json'
        data = json.loads(manifest.read_text(encoding='utf-8'))
        # The second alternative may not be chosen by the first frame.
        data['states'][self.key].append(dict(data['states'][self.key][0]))
        action = data['actions'][self.key]
        action.pop('action_chain', None)
        action['gestures'] = [
            {'kind': 'tap', 'point': [200, 400]},
            {'kind': 'tap', 'point': [750, 400]},
        ]
        manifest.write_text(json.dumps(data), encoding='utf-8')
        device = Device(self.match)
        with patch.object(device, 'screenshot') as capture:
            with self.assertRaisesRegex(
                    WorkflowError, 'action 2: Point'):
                OmniRunner(device, self.folder, 'com.game.demo')
            capture.assert_not_called()
        self.assertFalse(device.taps)

    def test_passive_wait_with_old_dormant_tap_data_stays_compatible(self):
        # A WAIT state never executes its stored gestures.
        self.change(self.wait, action_chain=[{'kind': 'unused-old-command'}])
        runner = OmniRunner(Device(self.match), self.folder, 'com.game.demo')
        self.assertEqual(runner._chain(self.wait, 0), [])

    def test_dry_run_rejects_unsupported_and_malformed_actions_without_io(self):
        device = Device(self.match)
        messages = []
        runner = OmniRunner(device, self.folder, 'com.game.demo',
                            dry_run=True, log=messages.append)
        runner.last_observation = Match(self.key, 'Match', 1.0)
        invalid = [
            {'kind': 'teleport', 'point': [100, 200]},
            {'kind': 'tap'},
            {'kind': 'tap', 'point': [800, 200]},
            {'kind': 'swipe', 'start': [100, 200], 'end': [200, 300],
             'duration_ms': 10001},
            {'kind': 'hold', 'point': [100, 200], 'duration_ms': 20},
            {'kind': 'key', 'code': 999},
            {'kind': 'field_text', 'text': 'hello', 'point': [900, 200]},
            {'kind': 'field_text', 'text': 'not;safe', 'point': [200, 300]},
            {'kind': 'wait', 'seconds': -1},
            {'kind': 'wait', 'seconds': float('nan')},
            {'kind': 'wait', 'seconds': True},
            {'kind': 'wait', 'seconds': '2'},
            {'kind': 'tap', 'point': [True, 200]},
            {'kind': 'tap', 'point': ['100', 200]},
            {'kind': 'tap', 'point': [float('inf'), 200]},
            {'kind': 'tap', 'point': [float('nan'), 200]},
            {'kind': 'swipe', 'start': [100, 200], 'end': [200, 300],
             'duration_ms': 650.5},
            {'kind': 'swipe', 'start': [100, 200], 'end': [200, 300],
             'duration_ms': '650'},
            {'kind': 'key', 'code': True},
            {'kind': 'key', 'code': 4.7},
            {'kind': 'text', 'text': None},
            {'kind': 'text', 'text': 123},
            {'kind': 'field_text', 'point': [200, 300], 'text': False},
        ]
        for action in invalid:
            with self.subTest(action=action):
                with self.assertRaises(WorkflowError):
                    runner._execute(action, 100)
                self.assertEqual(runner.chain_index, 0)
        self.assertEqual(messages, [])
        self.assertEqual(device.taps, [])
        self.assertEqual(device.swipes, [])
        self.assertEqual(device.keys, [])
        self.assertEqual(device.texts, [])
        self.assertEqual(device.launches, [])

    def test_valid_action_numeric_defaults_and_fractional_wait_remain_supported(self):
        from omniflow.engine import validate_action
        for action in (
            {'kind': 'tap', 'point': [100.5, 200]},
            {'kind': 'hold', 'point': [100, 200]},
            {'kind': 'swipe', 'start': [100, 200], 'end': [200, 300],
             'duration_ms': 650},
            {'kind': 'key', 'code': 4},
            {'kind': 'wait', 'seconds': 0.25},
            {'kind': 'text', 'text': 'Ready Player_1'},
            {'kind': 'field_text', 'point': [200, 300], 'text': 'Ready'},
        ):
            with self.subTest(action=action):
                self.assertEqual(validate_action(action), action['kind'])

    def test_dry_run_traces_each_step_waits_and_redacts_text(self):
        secret = 'account-private-42'
        self.change(self.key, action_chain=[
            {'kind': 'tap', 'point': [200, 400]},
            {'kind': 'wait', 'seconds': 2},
            {'kind': 'field_text', 'point': [200, 400], 'text': secret},
            {'kind': 'count_run'},
        ])
        device = Device(self.match)
        logs, runs = [], []
        runner = OmniRunner(device, self.folder, 'com.game.demo',
                            log=logs.append, completed=runs.append,
                            dry_run=True)
        runner.step(100)
        runner.step(101)
        runner.step(102)
        runner.step(103)
        self.assertEqual(runner.chain_index, 2)
        runner.step(104)
        runner.step(105)
        traces = [message for message in logs if message.startswith('DRY RUN:')]
        self.assertEqual(len(traces), 4)
        for index, kind in enumerate(('tap', 'wait', 'field_text', 'count_run'), 1):
            self.assertIn(f'step {index} on Match: {kind}', traces[index - 1])
            self.assertIn('no input', traces[index - 1])
        self.assertNotIn(secret, repr(logs))
        self.assertEqual(device.taps, [])
        self.assertEqual(device.swipes, [])
        self.assertEqual(device.keys, [])
        self.assertEqual(device.texts, [])
        self.assertEqual(device.launches, [])
        self.assertEqual(runner.runs, 0)
        self.assertEqual(runs, [])

    def test_live_run_validates_action_before_any_device_input(self):
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        runner.device_size = (2160, 3840)
        runner.last_capture_started_at = 100.0
        with patch('omniflow.engine.time.monotonic', return_value=100.0):
            with self.assertRaisesRegex(WorkflowError, 'outside 540'):
                runner._execute(
                    {'kind': 'field_text', 'point': [900, 200], 'text': 'valid'}, 100)
        self.assertEqual(device.taps, [])
        self.assertEqual(device.texts, [])

    def test_field_text_taps_and_types_with_normalized_coordinates(self):
        device=Device(self.match)
        runner=OmniRunner(device,self.folder,'com.game.demo')
        runner.device_size=(2160,3840)
        # Direct _execute() tests bypass step(), so supply a fresh frame clock.
        runner.last_capture_started_at=100.0
        with patch('omniflow.engine.time.monotonic', return_value=100.0):
            runner._execute({'kind':'field_text','point':[200,400],'text':'Agent 21'},100)
        self.assertEqual(device.taps,[(800,1600)])
        self.assertEqual(device.texts,['Agent 21'])

    def test_field_text_validates_before_tap_even_in_dry_run(self):
        device=Device(self.match)
        runner=OmniRunner(device,self.folder,'com.game.demo',dry_run=True)
        with self.assertRaisesRegex(WorkflowError,'IME adapter'):
            runner._execute({'kind':'field_text','point':[200,400],'text':'hello;world'},100)
        self.assertFalse(device.taps)
        self.assertFalse(device.texts)

    def test_field_text_rechecks_lease_after_focus_tap(self):
        device=Device(self.match)
        runner=OmniRunner(device,self.folder,'com.game.demo')
        runner.device_size=(2160,3840)
        # Entry and focus-tap checks pass; typing loses lease.
        checks=iter([True,True,False])
        runner.can_act=lambda: next(checks)
        runner.last_capture_started_at=100.0
        with patch('omniflow.engine.time.monotonic', return_value=100.0), \
             self.assertRaisesRegex(WorkflowError,'refusing input'):
            runner._execute({'kind':'field_text','point':[200,400],'text':'Ready'},100)
        self.assertEqual(device.taps,[(800,1600)])
        self.assertFalse(device.texts)

    def test_pre_action_delay_stays_nonblocking(self):
        self.change(self.key,pre_tap_delay_seconds=300)
        device=Device(self.match);r=OmniRunner(device,self.folder,'com.game.demo')
        for t in (100,101,120,200,400):r.step(t)
        self.assertFalse(device.taps)
        r.step(401)
        self.assertEqual(len(device.taps),1)

    def test_inspect_explains_wait_priority_and_caches_shared_ocr(self):
        region = [155, 712, 434, 810]
        self.change(self.key, skip_template=True, conditions=[
            {'type': 'ocr', 'rect': region, 'text': 'READY'}])
        self.change(self.wait, skip_template=True, conditions=[
            {'type': 'ocr', 'rect': region, 'text': 'CANCEL', 'negate': True}])
        detector = OmniDetector(self.folder)
        manifest_before = (self.folder / 'manifest.json').read_bytes()
        with patch('omniflow.engine.OCREngine.read', return_value='READY') as ocr:
            winner, states = detector.inspect(self.match)
            ocr.assert_called_once()  # Both rules reuse the same OCR zone.
            self.assertEqual(detector.detect(self.match), winner)
            self.assertEqual(ocr.call_count, 2)  # One more call for detect(), no extra probes.
        self.assertEqual(winner.key, self.wait)  # Passive WAIT prevents the tap.
        self.assertEqual(states[0]['key'], self.key)
        self.assertEqual(states[0]['status'], 'overridden by WAIT')
        self.assertEqual(states[0]['condition_results'], [True])
        self.assertFalse(states[0]['has_template'])
        self.assertEqual(states[1]['status'], 'selected')
        self.assertEqual(states[1]['condition_results'], [True])
        self.assertTrue(states[1]['passive'])
        self.assertEqual((self.folder / 'manifest.json').read_bytes(), manifest_before)

    def test_inspect_shows_rejected_conditions_without_false_positive(self):
        self.change(self.key, skip_template=True, conditions=[
            {'type': 'ocr', 'rect': [155, 712, 434, 810], 'text': 'MATCH'}])
        self.change(self.wait, skip_template=True, conditions=[
            {'type': 'ocr', 'rect': [155, 712, 434, 810], 'text': 'CANCEL'}])
        detector = OmniDetector(self.folder)
        with patch('omniflow.engine.OCREngine.read', return_value='MATCH'):
            winner, states = detector.inspect(self.match)
        self.assertEqual(winner.key, self.key)
        self.assertEqual(states[0]['status'], 'selected')
        self.assertEqual(states[1]['status'], 'conditions failed')
        self.assertEqual(states[1]['condition_results'], [False])

    def test_inspect_does_not_evaluate_ocr_when_template_fails(self):
        self.change(self.key, conditions=[
            {'type': 'ocr', 'rect': [155, 712, 434, 810], 'text': 'READY'}])
        detector = OmniDetector(self.folder)
        with patch.object(detector, '_match_template', return_value=0.0), \
             patch('omniflow.engine.OCREngine.read') as ocr:
            winner, states = detector.inspect(self.match)
            ocr.assert_not_called()
            self.assertEqual(detector.detect(self.match), winner)
        self.assertFalse(winner.key)
        self.assertTrue(all(s['status'] == 'template below threshold' for s in states))
        self.assertIsNone(states[0]['condition_results'])
        self.assertTrue(states[0]['has_template'])

    def test_anchor_references_are_validated_before_template_files_are_read(self):
        manifest = self.folder / 'manifest.json'
        base = json.loads(manifest.read_text(encoding='utf-8'))
        original = base['states'][self.key][0]
        cases = [
            ({'file': '../outside.png', 'rect': original['rect']},
             'unsafe anchor filename'),
            ({'file': r'..\\outside.png', 'rect': original['rect']},
             'unsafe anchor filename'),
            ({'file': '/tmp/outside.png', 'rect': original['rect']},
             'unsafe anchor filename'),
            ({'file': 'C:outside.png', 'rect': original['rect']},
             'unsafe anchor filename'),
            ({'file': '.', 'rect': original['rect']},
             'unsafe anchor filename'),
            ({'file': original['file'], 'rect': [100, 200, 100, 300]},
             'image region is outside'),
            ({'file': original['file'], 'rect': [0, 0, 541, 30]},
             'image region is outside'),
            ({'file': original['file'], 'rect': [0, 0, '150', 200]},
             'four integer coordinates'),
            ({'file': original['file'], 'rect': [0, 0, 12]},
             'four integer coordinates'),
            ({'file': original['file']}, 'four integer coordinates'),
            ('bad probe', 'image probe must be an object'),
        ]
        for probe, expected in cases:
            with self.subTest(probe=probe):
                data = json.loads(json.dumps(base))
                data['states'][self.key] = [probe]
                manifest.write_text(json.dumps(data), encoding='utf-8')
                device = Device(self.match)
                with (patch('swordx.detection.ScreenDetector.__init__') as legacy,
                      patch.object(device, 'screenshot') as screenshot,
                      patch('omniflow.engine.OCREngine.read') as ocr):
                    with self.assertRaisesRegex(WorkflowError, expected):
                        OmniRunner(device, self.folder, 'com.game.demo')
                    legacy.assert_not_called()
                    screenshot.assert_not_called()
                    ocr.assert_not_called()
                self.assertFalse(device.taps)
        manifest.write_text(json.dumps(base), encoding='utf-8')
        self.assertEqual(OmniDetector(self.folder).detect(self.match).key, self.key)

    def test_anchor_symlink_escape_and_mismatched_image_size_fail_closed(self):
        manifest = self.folder / 'manifest.json'
        base = json.loads(manifest.read_text(encoding='utf-8'))
        name = base['states'][self.key][0]['file']
        original = self.folder / 'anchors' / name
        with tempfile.TemporaryDirectory() as escape_root:
            outside = Path(escape_root) / 'outside.png'
            outside.write_bytes(original.read_bytes())
            link = self.folder / 'anchors' / 'escaped.png'
            try:
                link.symlink_to(outside)
            except (NotImplementedError, OSError):
                # Some Windows runners disallow symlinks without developer mode.
                pass
            else:
                data = json.loads(json.dumps(base))
                data['states'][self.key][0]['file'] = link.name
                manifest.write_text(json.dumps(data), encoding='utf-8')
                with self.assertRaisesRegex(WorkflowError, 'anchor resolves outside workflow'):
                    OmniDetector(self.folder)
        manifest.write_text(json.dumps(base), encoding='utf-8')
        # An image with a different width cannot silently act as an anchor
        # with the recorded region's original coordinates.
        image = cv2.imread(str(original), cv2.IMREAD_GRAYSCALE)
        self.assertIsNotNone(image)
        self.assertTrue(cv2.imwrite(str(original), image[:, :-1]))
        with self.assertRaisesRegex(WorkflowError, 'saved image size differs'):
            OmniDetector(self.folder)

    def test_matcher_metadata_is_checked_before_screenshots_or_ocr(self):
        manifest = self.folder / 'manifest.json'
        base = json.loads(manifest.read_text(encoding='utf-8'))
        cases = [
            ('NaN global threshold', lambda d: d.update(threshold=float('nan')),
             'Workflow image threshold must be a finite number'),
            ('infinite global threshold', lambda d: d.update(threshold=float('inf')),
             'Workflow image threshold must be a finite number'),
            ('global threshold above one', lambda d: d.update(threshold=1.1),
             'Workflow image threshold must be between 0 and 1'),
            ('per-state NaN threshold', lambda d: d['actions'][self.key].update(
                threshold=float('nan')), 'image threshold must be a finite number'),
            ('negative state threshold', lambda d: d['actions'][self.key].update(
                threshold=-0.1), 'image threshold must be between 0 and 1'),
            ('NaN priority', lambda d: d['actions'][self.key].update(
                priority=float('nan')), 'priority must be a finite number'),
            ('unknown match mode', lambda d: d['actions'][self.key].update(
                match='sometimes'), 'image match mode must be all or any'),
            ('truthy passive string', lambda d: d['actions'][self.key].update(
                passive='false'), 'passive must be true or false'),
            ('truthy skip flag', lambda d: d['actions'][self.key].update(
                skip_template='false'), 'skip_template must be true or false'),
            ('truthy retry string', lambda d: d['actions'][self.key].update(
                retry='false'), 'retry must be true or false'),
            ('truthy end string', lambda d: d['actions'][self.key].update(
                end='false'), 'end must be true or false'),
            ('negative action delay', lambda d: d['actions'][self.key].update(
                pre_tap_delay_seconds=-1), 'pre_tap_delay_seconds must be a whole number'),
            ('fractional action delay', lambda d: d['actions'][self.key].update(
                pre_tap_delay_seconds=0.5), 'pre_tap_delay_seconds must be a whole number'),
            ('invalid retry interval', lambda d: d['actions'][self.key].update(
                retry_interval_seconds=0), 'retry_interval_seconds must be a whole number'),
            ('nan retry interval', lambda d: d['actions'][self.key].update(
                retry_interval_seconds=float('nan')), 'retry_interval_seconds must be a whole number'),
            ('retry limit too high', lambda d: d['actions'][self.key].update(
                max_extra_taps=21), 'max_extra_taps must be a whole number'),
            ('retry limit boolean', lambda d: d['actions'][self.key].update(
                max_extra_taps=True), 'max_extra_taps must be a whole number'),
            ('unsupported retry mode', lambda d: d['actions'][self.key].update(
                retry_mode='immediate'), 'retry_mode must be interval or on_return'),
            ('non-list conditions', lambda d: d['actions'][self.key].update(
                conditions={}), 'recognition conditions must be a list'),
            ('unrecorded image template', lambda d: d['states'].update(
                {self.key: []}), 'image matching requires a captured template'),
            ('condition-only without rules', lambda d: d['actions'][self.key].update(
                skip_template=True), 'condition-only recognition needs a condition'),
            ('action without corresponding state', lambda d: d['states'].pop(
                self.key), 'state keys and action keys must match'),
        ]
        for name, corrupt, message in cases:
            with self.subTest(case=name):
                data = json.loads(json.dumps(base))
                corrupt(data)
                manifest.write_text(json.dumps(data), encoding='utf-8')
                device = Device(self.match)
                with (patch.object(device, 'screenshot') as capture,
                      patch('omniflow.engine.OCREngine.read') as ocr):
                    with self.assertRaisesRegex(WorkflowError, message):
                        OmniRunner(device, self.folder, 'com.game.demo')
                    capture.assert_not_called()
                    ocr.assert_not_called()
                self.assertEqual(device.taps, [])
        manifest.write_text(json.dumps(base), encoding='utf-8')
        self.assertEqual(OmniDetector(self.folder).detect(self.match).key, self.key)

    def test_saved_retry_timing_boundaries_keep_workflows_compatible(self):
        manifest = self.folder / 'manifest.json'
        data = json.loads(manifest.read_text(encoding='utf-8'))
        data['actions'][self.key].update(
            retry=True, retry_mode='interval', retry_interval_seconds=86400,
            pre_tap_delay_seconds=86400, max_extra_taps=20, end=True)
        manifest.write_text(json.dumps(data), encoding='utf-8')
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        self.assertEqual(runner.detector.actions[self.key]['max_extra_taps'], 20)
        self.assertEqual(device.taps, [])
        # Legacy absent timing fields are valid; no migration is required.
        data['actions'][self.key].update(retry=False, end=False)
        for field in ('pre_tap_delay_seconds', 'retry_interval_seconds',
                      'max_extra_taps', 'retry_mode'):
            data['actions'][self.key].pop(field, None)
        manifest.write_text(json.dumps(data), encoding='utf-8')
        self.assertIsNotNone(OmniRunner(device, self.folder, 'com.game.demo'))

    def test_condition_only_requires_explicit_mode_and_keeps_valid_mixed_states(self):
        manifest = self.folder / 'manifest.json'
        data = json.loads(manifest.read_text(encoding='utf-8'))
        data['states'][self.key] = []
        data['actions'][self.key]['conditions'] = [
            {'type': 'color', 'rect': [155, 712, 434, 810],
             'bgr': [51, 58, 69], 'tolerance': 255}]
        manifest.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaisesRegex(
                WorkflowError, 'image matching requires a captured template'):
            OmniDetector(self.folder)
        data['actions'][self.key]['skip_template'] = True
        manifest.write_text(json.dumps(data), encoding='utf-8')
        detector = OmniDetector(self.folder)
        self.assertIn(self.key, detector.actions)
        self.assertTrue(detector.actions[self.key]['skip_template'])

    def test_invalid_recognition_rules_fail_before_ocr_or_saved_runtime(self):
        from omniflow.engine import preview_conditions, validate_condition
        valid_ocr = {'type': 'ocr', 'rect': [155, 712, 434, 810],
                     'text': 'READY'}
        invalid = [
            ({'type': 'ocr', 'rect': [155, 712, 434, 810],
              'text': '[', 'regex': True}, 'regular expression'),
            ({'type': 'ocr', 'rect': [155, 712, 434, 810],
              'text': 'READY', 'regex': 'false'}, 'Recognition regex must be true or false'),
            ({'type': 'ocr', 'rect': [155, 712, 434, 810],
              'text': 'READY', 'regex': 1}, 'Recognition regex must be true or false'),
            ({'type': 'ocr', 'rect': [155, 712, 434, 810],
              'text': 'READY', 'regex': None}, 'Recognition regex must be true or false'),
            ({'type': 'color', 'bgr': [51, 58, 69],
              'negate': 'false'}, 'Recognition negate must be true or false'),
            ({'type': 'pixel', 'point': [200, 400],
              'bgr': [51, 58, 69], 'negate': 0},
             'Recognition negate must be true or false'),
            ({'type': 'ocr', 'rect': [0, 0, 0, 20],
              'text': 'READY'}, 'outside the normalized screen'),
            ({'type': 'pixel', 'point': [540, 200],
              'bgr': [50, 60, 70]}, 'Selected pixel'),
            ({'type': 'color', 'bgr': [50, 60]}, 'three BGR'),
            ({'type': 'color', 'bgr': [50, 60, 256]}, 'three BGR'),
            ({'type': 'color', 'bgr': [50, 60, 70],
              'tolerance': float('nan')}, 'finite number'),
            ({'type': 'color', 'bgr': [50, 60, 70],
              'tolerance': -1, 'negate': True}, 'finite number'),
            ({'type': 'unknown', 'rect': [1, 1, 30, 30]},
             'Unknown detector type'),
        ]
        for rule, error in invalid:
            with self.subTest(rule=rule):
                with self.assertRaisesRegex(WorkflowError, error):
                    validate_condition(rule)
                # Validate the whole draft first: a previous OCR rule cannot
                # trigger any inference before a later broken rule is rejected.
                with patch('omniflow.engine.OCREngine.read') as ocr:
                    with self.assertRaisesRegex(WorkflowError, error):
                        preview_conditions(self.match, [valid_ocr, rule])
                    ocr.assert_not_called()
                self.change(self.key, conditions=[rule])
                with self.assertRaisesRegex(
                        WorkflowError, 'State .Match., condition 1'):
                    OmniDetector(self.folder)
        self.change(self.key, conditions=[valid_ocr])
        detector = OmniDetector(self.folder)
        self.assertIn(self.key, detector.actions)

    def test_explicit_boolean_ocr_flags_keep_normal_matching(self):
        from omniflow.engine import preview_conditions
        rect = [155, 712, 434, 810]
        literal = {'type': 'ocr', 'rect': rect, 'text': 'READY',
                   'regex': False, 'negate': False}
        regex = {'type': 'ocr', 'rect': rect, 'text': '^READY',
                 'regex': True, 'negate': False}
        inverted = {'type': 'ocr', 'rect': rect, 'text': 'READY',
                    'regex': False, 'negate': True}
        with patch('omniflow.engine.OCREngine.read', return_value='READY') as ocr:
            self.assertEqual(
                preview_conditions(self.match, [literal, regex, inverted]),
                [True, True, False])
            ocr.assert_called_once()

    def test_condition_only_ocr_state_and_negation(self):
        self.change(self.key, skip_template=True, conditions=[
            {'type': 'ocr', 'rect': [155, 712, 434, 810], 'text': 'PLAY'}])
        with patch('omniflow.engine.OCREngine.read', return_value='PLAY NOW'):
            detected = OmniDetector(self.folder).detect(self.cancel)
        self.assertEqual(detected.key, self.wait)  # WAIT takes precedence.
        with patch('omniflow.engine.OCREngine.read', return_value='PLAY NOW'):
            detected = OmniDetector(self.folder).detect(np.zeros_like(self.match))
        self.assertEqual(detected.key, self.key)

    def test_condition_negation_when_ocr_is_absent(self):
        self.change(self.key, skip_template=True, conditions=[
            {'type': 'ocr', 'rect': [0, 0, 100, 100],
             'text': 'CANCEL', 'negate': True}])
        with patch('omniflow.engine.OCREngine.read', return_value='Ready'):
            self.assertEqual(
                OmniDetector(self.folder).detect(np.zeros_like(self.match)).key,
                self.key)
        with patch('omniflow.engine.OCREngine.read', return_value='Cancel'):
            self.assertEqual(
                OmniDetector(self.folder).detect(np.zeros_like(self.match)).key,
                '')

    def test_completion_marker_counts_on_exiting_screen_only(self):
        self.change(self.key, end=True)
        device = Device(self.match)
        runs = []
        runner = OmniRunner(device, self.folder, 'com.game.demo',
                            completed=runs.append)
        for t in (100, 101, 102):
            runner.step(t)
        self.assertEqual(runs, [])
        for t in (103, 104):
            device.frame = self.cancel
            runner.step(t)
        self.assertEqual(runs, [1])


if __name__ == '__main__':
    unittest.main()
