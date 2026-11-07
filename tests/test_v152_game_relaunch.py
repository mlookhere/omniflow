"""Regression coverage: package relaunch is independent of desktop and MuMu recovery."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from swordx.adb import ADBError
from swordx.config import ProfileStore, Settings
from swordx.detection import ScreenDetector
from swordx.engine import Runner
from test_engine import FakeDevice, ROOT


class GameRelaunchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detector = ScreenDetector(ROOT / 'assets')

    def runner(self, enabled=True):
        device = FakeDevice('9')
        device.frame = np.zeros((960, 540, 3), dtype=np.uint8)
        device.foreground_package = 'com.android.settings'
        device.process_running = False
        messages = []
        settings = Settings(package='com.example.sword', diagnostics=False,
                            auto_relaunch_game=enabled)
        return device, Runner(device, self.detector, settings, messages.append), messages

    def test_closed_game_relaunches_only_after_confirmation(self):
        device, runner, messages = self.runner()
        for now in (100, 105, 110, 121):
            runner.step(now)
        self.assertEqual(device.launches, ['com.example.sword'])
        self.assertEqual(device.force_stops, [])
        self.assertEqual(sum('confirmed stopped' in line for line in messages), 1)

    def test_temporary_activity_manager_failure_does_not_stop_automation(self):
        device, runner, messages = self.runner()
        attempted = []
        def unavailable(package):
            attempted.append(package)
            if len(attempted) == 1:
                raise ADBError('Android activity manager temporarily unavailable')
        device.launch = unavailable
        for now in (100, 110, 120, 130, 140):
            runner.step(now)
        self.assertEqual(attempted, ['com.example.sword', 'com.example.sword'])
        self.assertTrue(any('will retry' in line for line in messages))

    def test_disabled_game_relaunch_does_not_start_package(self):
        device, runner, messages = self.runner(enabled=False)
        for now in (100, 120, 200, 500, 1000):
            runner.step(now)
        self.assertEqual(device.launches, [])
        self.assertEqual(device.force_stops, [])
        self.assertFalse(any('Restarting game' in line for line in messages))

    def test_disabled_game_relaunch_blocks_frozen_screen_watchdog(self):
        device, runner, _ = self.runner(enabled=False)
        runner.last_restart = float('-inf')
        runner._restart_game(1000, 'Unrecognized screen has been frozen')
        self.assertEqual(device.force_stops, [])
        self.assertEqual(device.launches, [])

    def test_momentary_process_probe_failure_does_not_relaunch(self):
        device, runner, _ = self.runner()
        runner.step(100)
        device.process_running = True
        device.foreground_package = 'com.example.sword'
        runner.step(110)
        device.process_running = False
        device.foreground_package = 'com.android.settings'
        runner.step(120)
        self.assertEqual(device.launches, [])

    def test_foreground_game_prevents_false_negative(self):
        device, runner, _ = self.runner()
        device.foreground_package = 'com.example.sword'
        for now in (100, 110, 120, 130):
            runner.step(now)
        self.assertEqual(device.launches, [])

    def test_legacy_profile_defaults_to_game_relaunch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = {'active': 'Original', 'profiles': {'Original': {
                'settings': {'package': 'com.example.sword', 'workflow': 'Demo'},
                'runs': 4}}}
            (root / 'profiles.json').write_text(json.dumps(old))
            store = ProfileStore(root)
            self.assertTrue(store.get('Original').auto_relaunch_game)
            setting = store.get('Original')
            setting.auto_relaunch_game = False
            store.save('Original', setting)
            self.assertFalse(ProfileStore(root).get('Original').auto_relaunch_game)
            self.assertEqual(ProfileStore(root).runs('Original'), 4)

    def test_settings_json_migration_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'LOCALAPPDATA': tmp}):
                folder = Path(tmp) / 'SwordXAuto'
                folder.mkdir()
                (folder / 'settings.json').write_text('{"package":"com.example.sword"}')
                self.assertTrue(Settings.load().auto_relaunch_game)

    def test_branded_spin_arrows_exist(self):
        for name in ('spin-up.png', 'spin-down.png', 'spin-up-disabled.png',
                     'spin-down-disabled.png'):
            self.assertGreater((ROOT / 'assets' / 'icons' / name).stat().st_size, 50)


if __name__ == '__main__':
    unittest.main()
