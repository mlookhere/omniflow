"""Regression coverage for MuMu-specific Android process/foreground checks."""
import unittest
from unittest.mock import patch

from swordx.adb import ADB, ADBError


class GameStatusTests(unittest.TestCase):
    def setUp(self):
        self.adb = ADB(serial='127.0.0.1:16384')

    def test_android_window_format(self):
        dumpsys = b'mCurrentFocus=Window{c1a002 u0 com.example.sword/.MainActivity}\n'
        with patch.object(self.adb, '_run', return_value=dumpsys) as run:
            self.assertEqual(self.adb.foreground(), 'com.example.sword')
            run.assert_called_once_with('shell', 'dumpsys', 'window', timeout=12)

    def test_activity_record_format(self):
        window = b'mCurrentFocus=null\nmFocusedApp=null'
        activity = b'topResumedActivity=ActivityRecord{ac23 u0 com.example.sword/com.example.sword.MainActivity t9}'
        with patch.object(self.adb, '_run', side_effect=[window, activity]):
            self.assertEqual(self.adb.foreground(), 'com.example.sword')

    def test_mumu_window_dump_unavailable_falls_back_to_activity(self):
        activity = b'mResumedActivity: ActivityRecord{faa u0 com.example.sword/.MainActivity}'
        with patch.object(self.adb, '_run', side_effect=[ADBError('unsupported'), activity]):
            self.assertEqual(self.adb.foreground(), 'com.example.sword')

    def test_non_u0_display_activity(self):
        window = b'mCurrentFocus=Window{ac4 u10 com.example.sword/.Main}'
        with patch.object(self.adb, '_run', return_value=window):
            self.assertEqual(self.adb.foreground(), 'com.example.sword')

    def test_unknown_foreground_not_guessed(self):
        with patch.object(self.adb, '_run', side_effect=[b'ignored', b'nothing useful']):
            self.assertEqual(self.adb.foreground(), '')

    def test_both_activity_queries_fail_is_connection_error(self):
        with patch.object(self.adb, '_run', side_effect=ADBError('device offline')):
            with self.assertRaises(ADBError):
                self.adb.foreground()

    def test_installed_uses_exact_android_package(self):
        with patch.object(self.adb, '_run', return_value=b'package:/data/app/com.example.sword/base.apk\n') as run:
            self.assertTrue(self.adb.installed('com.example.sword'))
            run.assert_called_once_with('shell', 'pm', 'path', 'com.example.sword', timeout=10)

    def test_wrong_package_not_installed(self):
        with patch.object(self.adb, '_run', return_value=b''):
            self.assertFalse(self.adb.installed('com.example.wrong'))

    def test_missing_package_manager_exit_one(self):
        with patch.object(self.adb, '_run', side_effect=ADBError('ADB exited 1')):
            self.assertFalse(self.adb.installed('com.example.wrong'))

    def test_monkey_launch_failure_is_not_silently_success(self):
        with patch.object(self.adb, '_run', return_value=b'** No activities found to run, monkey aborted.\n'):
            with self.assertRaisesRegex(ADBError, 'Cannot launch'):
                self.adb.launch('com.example.sword')

    def test_monkey_success_unchanged(self):
        with patch.object(self.adb, '_run', return_value=b'Events injected: 1\n'):
            self.adb.launch('com.example.sword')


if __name__ == '__main__':
    unittest.main()
