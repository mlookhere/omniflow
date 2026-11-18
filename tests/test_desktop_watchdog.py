"""Desktop recovery is independent of OCR and preserves clean-exit semantics."""
import json
import tempfile
import unittest
from pathlib import Path

from swordx.watchdog import (MAX_CRASHES, restart_enabled, run_watchdog,
                             set_restart_enabled)


class FakeChild:
    def __init__(self, code):
        self.code = code

    def wait(self):
        return self.code


class DesktopWatchdogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.app = self.root / 'app.py'
        self.app.write_text('pass', encoding='utf-8')

    def supervisor(self, exit_codes, *, step=3):
        calls = []
        times = iter(range(0, 1000, step))
        codes = iter(exit_codes)
        slept = []

        def fake_launch(argv, **kwargs):
            calls.append((argv, kwargs))
            return FakeChild(next(codes))

        code = run_watchdog(self.app, root=self.root, launch=fake_launch,
                            clock=lambda: next(times), sleep=slept.append)
        return code, calls, slept

    def test_new_install_defaults_to_recovery_enabled(self):
        self.assertTrue(restart_enabled(self.root))

    def test_setting_is_persisted_outside_profiles(self):
        set_restart_enabled(False, self.root)
        self.assertFalse(restart_enabled(self.root))
        self.assertFalse(json.loads((self.root / 'desktop_recovery.json').read_text())['restart_on_crash'])
        set_restart_enabled(True, self.root)
        self.assertTrue(restart_enabled(self.root))
        self.assertFalse((self.root / 'profiles.json').exists())

    def test_clean_exit_never_restarts(self):
        code, calls, waits = self.supervisor([0])
        self.assertEqual((code, len(calls), waits), (0, 1, []))

    def test_crash_restarts_gui_then_clean_exit_stops(self):
        code, calls, waits = self.supervisor([1, 0])
        self.assertEqual((code, len(calls), waits), (0, 2, [2]))
        self.assertTrue(all(args[0][-1] == str(self.app) for args in calls))
        self.assertIn('Restarting GUI', (self.root / 'desktop_crashes.log').read_text())

    def test_disabled_recovery_never_restarts(self):
        set_restart_enabled(False, self.root)
        code, calls, waits = self.supervisor([1])
        self.assertEqual((code, len(calls), waits), (1, 1, []))

    def test_crash_loop_is_bounded(self):
        code, calls, waits = self.supervisor([7] * (MAX_CRASHES + 1))
        self.assertEqual(code, 7)
        self.assertEqual(len(calls), MAX_CRASHES + 1)
        self.assertEqual(len(waits), MAX_CRASHES)
        self.assertIn('Restart limit reached', (self.root / 'desktop_crashes.log').read_text())

    def test_crash_count_resets_after_long_stable_run(self):
        # A stable run longer than the rolling window no longer counts toward the limit.
        code, calls, waits = self.supervisor([1, 1, 1, 0], step=200)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(waits), 3)


if __name__ == '__main__':
    unittest.main()
