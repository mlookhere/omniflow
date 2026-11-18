"""Per-state watchdog overrides must never alter existing action/delay semantics."""
import tempfile
import unittest
from pathlib import Path

from swordx.config import ProfileStore, Settings
from swordx.detection import ScreenDetector
from swordx.engine import KNOWN_STALL_SECONDS, Runner
from swordx.preview import (PREVIEW_SEED_INTERVAL, VIDEO_STALE_SECONDS,
                            should_seed_preview, video_is_stale)
from swordx.workflows import create_workflow, record_step
from test_engine import FakeDevice

ROOT = Path(__file__).resolve().parents[1]


class TimeoutTests(unittest.TestCase):
    def runner(self, overrides, initial='4'):
        device = FakeDevice(initial)
        if initial == '4':
            # Pretend Normal was selected: queue once, then Match becomes Cancel.
            from swordx.detection import to_device
            for x, y in ((198, 1297), (310, 1319), (351, 1299)):
                xx, yy = to_device(x, y, 540, 960)
                device.frame[yy-3:yy+4, xx-3:xx+4] = (98, 210, 205)
        settings = Settings(package='com.example.sword', diagnostics=False,
                            workflow='Built-in dungeon', state_timeouts=overrides)
        logs = []
        bot = Runner(device, ScreenDetector(ROOT / 'assets'), settings, logs.append)
        return device, bot, logs

    def test_old_profiles_still_restart_after_four_minutes(self):
        device, runner, _ = self.runner({})
        runner.step(100)
        runner.step(100 + KNOWN_STALL_SECONDS + 1)
        self.assertEqual(len(device.force_stops), 1)

    def test_waiting_matchmaking_can_have_ten_minute_timeout(self):
        device, runner, logs = self.runner({'Built-in dungeon': {'DETAILS': 600}})
        for now in (100, 200, 345, 510, 699, 700):
            runner.step(now)
        self.assertEqual(device.force_stops, [])
        # One tap: never inadvertently click Cancel while queued.
        self.assertEqual(len(device.taps), 1)
        runner.step(701)
        self.assertEqual(len(device.force_stops), 1)
        self.assertTrue(any('600 seconds' in x for x in logs))

    def test_never_restart_known_unchanged_state(self):
        device, runner, logs = self.runner({'Built-in dungeon': {'DETAILS': 0}})
        for now in (100, 200, 400, 1500, 5000):
            runner.step(now)
        self.assertEqual(device.force_stops, [])
        self.assertEqual(len(device.taps), 1)
        self.assertFalse(any('Stuck' in x for x in logs))

    def test_other_states_retain_original_timeout(self):
        device, runner, _ = self.runner({'Built-in dungeon': {'INVITE': 0}})
        runner.step(100)
        runner.step(345)
        self.assertEqual(len(device.force_stops), 1)

    def test_other_workflows_do_not_inherit_override(self):
        device, runner, _ = self.runner({'Unrelated workflow': {'DETAILS': 0}})
        runner.step(100)
        runner.step(345)
        self.assertEqual(len(device.force_stops), 1)

    def test_bad_stored_values_use_safe_existing_default(self):
        for invalid in (None, 'never', -10, 86401, True, 180.0):
            with self.subTest(invalid=invalid):
                device, runner, _ = self.runner({'Built-in dungeon': {'DETAILS': invalid}})
                runner.step(100)
                runner.step(345)
                self.assertEqual(len(device.force_stops), 1)

    def test_timeout_works_for_recorded_states_without_modifying_manifest(self):
        import cv2
        frame = cv2.imread(str(ROOT / 'tests/fixtures/2.jpg'))
        with tempfile.TemporaryDirectory() as td:
            folder = create_workflow('Custom dungeon', Path(td))
            state = record_step(folder, 'Queue', frame, [(17, 116, 158, 138)], taps=[(200, 240)])
            before = (folder / 'manifest.json').read_bytes()
            settings = Settings(package='com.example.sword', workflow='Custom dungeon',
                                state_timeouts={'Custom dungeon': {state: 0}})
            device = FakeDevice('2')
            runner = Runner(device, ScreenDetector(folder), settings, lambda _: None)
            for now in (100, 400, 800, 1400):
                runner.step(now)
            self.assertEqual(len(device.taps), 1)
            self.assertFalse(device.force_stops)
            self.assertEqual((folder / 'manifest.json').read_bytes(), before)

    def test_profile_persists_overrides_and_keeps_run_counts(self):
        with tempfile.TemporaryDirectory() as td:
            store = ProfileStore(Path(td))
            config = store.get('Default')
            config.state_timeouts = {'Built-in dungeon': {'DETAILS': 0, 'INVITE': 600}}
            store.save('Default', config)
            store.increment('Default')
            reopened = ProfileStore(Path(td))
            self.assertEqual(reopened.get('Default').state_timeouts, config.state_timeouts)
            self.assertEqual(reopened.runs('Default'), 1)

    def test_fresh_and_old_profiles_default_to_no_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            store = ProfileStore(Path(td))
            self.assertEqual(store.get('Default').state_timeouts, {})


class PreviewSeedTests(unittest.TestCase):
    def test_seed_shows_existing_emulator_frame_until_video_is_ready(self):
        self.assertFalse(should_seed_preview(True, False, PREVIEW_SEED_INTERVAL - 0.01))
        self.assertTrue(should_seed_preview(True, False, PREVIEW_SEED_INTERVAL))
        self.assertFalse(should_seed_preview(True, True, 50))
        self.assertFalse(should_seed_preview(False, False, 50))

    def test_stale_stream_reenables_seeded_emulator_frames(self):
        self.assertFalse(video_is_stale(10, 10 - VIDEO_STALE_SECONDS + .01))
        self.assertTrue(video_is_stale(10, 10 - VIDEO_STALE_SECONDS))
        self.assertTrue(should_seed_preview(True, False, PREVIEW_SEED_INTERVAL))

    def test_seed_does_not_start_second_screenshot_process(self):
        import inspect
        import swordx.preview as source
        code = inspect.getsource(source.should_seed_preview)
        self.assertNotIn('screenshot(', code)
        self.assertNotIn('screenrecord(', code)


if __name__ == '__main__':
    unittest.main()
