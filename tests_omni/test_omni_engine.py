import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from swordx.adb import ADBError
from swordx.workflows import create_workflow,record_step
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

    def test_capture_age_within_limit_keeps_normal_recognition(self):
        device = Device(self.match)
        runner = OmniRunner(device, self.folder, 'com.game.demo')
        with patch('omniflow.engine.time.monotonic',
                   side_effect=[20.0, 21.8, 30.0, 31.2]):
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

    def test_field_text_taps_and_types_with_normalized_coordinates(self):
        device=Device(self.match)
        runner=OmniRunner(device,self.folder,'com.game.demo')
        runner.device_size=(2160,3840)
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
        checks=iter([True,False])
        runner.can_act=lambda: next(checks)
        with self.assertRaisesRegex(WorkflowError,'refusing input'):
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

    def test_condition_only_ocr_state_and_negation(self):
        self.change(self.key,skip_template=True,conditions=[{'type':'ocr','rect':[155,712,434,810], 'text':'PLAY'}])
        with patch('omniflow.engine.OCREngine.read', return_value='PLAY NOW'):
            detected=OmniDetector(self.folder).detect(self.cancel)
        self.assertEqual(detected.key,self.wait)  # Wait takes precedence.
        with patch('omniflow.engine.OCREngine.read', return_value='PLAY NOW'):
            detected=OmniDetector(self.folder).detect(np.zeros_like(self.match))
        self.assertEqual(detected.key,self.key)

    def test_condition_negation_when_ocr_is_absent(self):
        self.change(self.key,skip_template=True,conditions=[{'type':'ocr','rect':[0,0,100,100], 'text':'CANCEL','negate':True}])
        with patch('omniflow.engine.OCREngine.read', return_value='Ready'):
            self.assertEqual(OmniDetector(self.folder).detect(np.zeros_like(self.match)).key,self.key)
        with patch('omniflow.engine.OCREngine.read', return_value='Cancel'):
            self.assertEqual(OmniDetector(self.folder).detect(np.zeros_like(self.match)).key,'')

    def test_completion_marker_counts_on_exiting_screen_only(self):
        self.change(self.key,end=True)
        device=Device(self.match);runs=[]
        r=OmniRunner(device,self.folder,'com.game.demo',completed=runs.append)
        for t in (100,101,102):r.step(t)
        self.assertEqual(runs,[])
        for t in (103,104):device.frame=self.cancel;r.step(t)
        self.assertEqual(runs,[1])


if __name__=='__main__':unittest.main()
