"""Protect long-running game sessions from false process-death reports."""
import unittest
from unittest.mock import patch

import numpy as np

from swordx.adb import ADB, ADBError
from swordx.config import Settings
from swordx.detection import ScreenDetector
from swordx.engine import Runner
from test_engine import FakeDevice, ROOT


class ProcessProbeTests(unittest.TestCase):
    def setUp(self):
        self.adb = ADB(serial='127.0.0.1:7555')

    def test_pidof_live_process(self):
        with patch.object(self.adb, '_run', return_value=b'1012 1414\n') as probe:
            self.assertTrue(self.adb.running('com.example.sword'))
            probe.assert_called_once_with('shell', 'pidof', 'com.example.sword', timeout=7)

    def test_pidof_failure_does_not_hide_a_running_game(self):
        listing = b'USER PID PPID VSZ RSS WCHAN ADDR S NAME\nu0_a1 100 1 5 2 0 0 S com.example.sword\n'
        with patch.object(self.adb, '_run', side_effect=[ADBError('ADB exited 1'), listing]):
            self.assertTrue(self.adb.running('com.example.sword'))

    def test_pidof_missing_but_child_process_is_alive(self):
        listing = b'USER PID NAME\nu0_a1 100 com.example.sword:service\n'
        with patch.object(self.adb, '_run', side_effect=[ADBError('pidof: not found'), listing]):
            self.assertTrue(self.adb.running('com.example.sword'))

    def test_no_pid_and_valid_ps_confirms_absence(self):
        listing = b'USER PID NAME\nu0_a1 100 com.android.launcher\n'
        with patch.object(self.adb, '_run', side_effect=[ADBError('ADB exited 1'), listing]):
            self.assertFalse(self.adb.running('com.example.sword'))

    def test_unsupported_probes_are_unknown_not_false(self):
        with patch.object(self.adb, '_run', side_effect=[ADBError('ADB exited 127'),
                                                        b'unsupported', b'unsupported']):
            self.assertIsNone(self.adb.running('com.example.sword'))

    def test_device_offline_propagates(self):
        with patch.object(self.adb, '_run', side_effect=ADBError('device offline')):
            with self.assertRaisesRegex(ADBError, 'device offline'):
                self.adb.running('com.example.sword')


class CrashConfirmationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detector = ScreenDetector(ROOT / 'assets')

    def create(self):
        device = FakeDevice('9')
        device.frame = np.zeros((960, 540, 3), dtype=np.uint8)
        logs = []
        runner = Runner(device, self.detector,
                        Settings(package='com.example.sword', diagnostics=False), logs.append)
        return device, runner, logs

    def test_foreground_game_ignores_false_negative_process_check(self):
        device, runner, logs = self.create()
        device.process_running = False
        for now in (100, 110, 120, 130, 140):
            # Active game screen can be unknown in combat; avoid static watchdog.
            device.frame[:] = now % 240
            runner.step(now)
        self.assertEqual(device.launches, [])
        self.assertFalse(any('process' in message.lower() for message in logs))

    def test_recent_visual_match_overrides_process_false_negative(self):
        device, runner, logs = self.create()
        device.select('9')
        runner.step(100)
        device.frame = np.zeros((960, 540, 3), dtype=np.uint8)
        device.foreground_package = 'com.android.launcher'
        device.process_running = False
        runner.step(110)
        runner.step(121)
        self.assertEqual(device.launches, [])
        runner.step(132)
        self.assertEqual(device.launches, ['com.example.sword'])
        self.assertEqual(sum('confirmed stopped' in message for message in logs), 1)

    def test_process_probe_unavailable_never_claims_crash(self):
        device, runner, logs = self.create()
        device.foreground_package = ''
        device.running = lambda package: None
        for now in (100, 110, 120, 130):
            device.frame[:] = now % 240
            runner.step(now)
        self.assertEqual(device.launches, [])
        self.assertFalse(any('process' in line for line in logs))

    def test_missing_process_requires_repeated_checks_and_cools_down(self):
        device, runner, logs = self.create()
        device.foreground_package = 'com.android.launcher'
        device.process_running = False
        for now in (100, 105, 110, 120, 140):
            device.frame[:] = now % 240
            runner.step(now)
        self.assertEqual(device.launches, ['com.example.sword'])
        self.assertEqual(sum('confirmed stopped' in line for line in logs), 1)

    def test_running_in_background_is_not_a_crash(self):
        device, runner, logs = self.create()
        device.foreground_package = 'com.android.launcher'
        runner.step(100)
        self.assertEqual(device.launches, ['com.example.sword'])
        self.assertTrue(any('in background' in line for line in logs))
        self.assertFalse(any('confirmed stopped' in line for line in logs))

    def test_known_workflow_screen_never_checks_process(self):
        device, runner, logs = self.create()
        device.select('9')
        device.running = lambda package: self.fail('Do not probe processes on recognized game screen')
        device.foreground = lambda: self.fail('Do not probe foreground on recognized game screen')
        runner.step(100)
        self.assertEqual(len(device.taps), 1)
        self.assertFalse(any('stopped' in line for line in logs))


if __name__ == '__main__':
    unittest.main()
