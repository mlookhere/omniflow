"""Emulator ownership must persist even when a game step blocks."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from omniflow.manager import InstanceManager
from omniflow.schedule import ScheduleStore


class ObservedStore(ScheduleStore):
    def __init__(self, path, *, fail_after=None, error=False):
        super().__init__(path)
        self.calls = 0
        self.fail_after = fail_after
        self.error = error
        self.call_lock = threading.Lock()
        self.calls_changed = threading.Condition(self.call_lock)

    def wait_for_calls(self, count, timeout):
        with self.calls_changed:
            return self.calls_changed.wait_for(lambda: self.calls >= count, timeout=timeout)

    def heartbeat(self, serial, owner, now=None):
        with self.calls_changed:
            self.calls += 1
            count = self.calls
            failing = self.fail_after
            raise_error = self.error
            self.calls_changed.notify_all()
        if raise_error:
            raise OSError('SQLite inaccessible')
        if failing is not None and count >= failing:
            return False
        return super().heartbeat(serial, owner, now)


class BlockingRunner:
    arrived = threading.Event()
    continue_step = threading.Event()

    def __init__(self, *args, **kwargs):
        pass

    def step(self, now):
        self.arrived.set()
        self.continue_step.wait(2)


class LeaseKeepaliveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'workflow'
        self.folder.mkdir()
        (self.folder / 'manifest.json').write_text('{}', encoding='utf-8')
        BlockingRunner.arrived = threading.Event()
        BlockingRunner.continue_step = threading.Event()
        self.addCleanup(BlockingRunner.continue_step.set)

    def start(self, store):
        manager = InstanceManager(store, poll_seconds=0.35, lease_interval=0.05)
        self.addCleanup(manager.stop_all)
        ok, message = manager.start('127.0.0.1:16384', 'com.example.game',
                                    'workflow', self.folder)
        self.assertTrue(ok, message)
        session = manager.snapshot()[0]
        self.addCleanup(lambda: session.worker.join(2))
        self.assertTrue(BlockingRunner.arrived.wait(3))
        return manager, session

    def test_renewal_continues_during_a_blocked_workflow_step(self):
        store = ObservedStore(Path(self.temp.name) / 'jobs.sqlite')
        with patch('omniflow.manager.OmniRunner', BlockingRunner):
            manager, session = self.start(store)
            # Observe three *additional* heartbeats after the workflow step
            # is already blocked. A Condition avoids polling/scheduler races.
            with store.call_lock:
                baseline = store.calls
            self.assertTrue(store.wait_for_calls(baseline + 3, timeout=2.0))
            with store.call_lock:
                self.assertGreaterEqual(store.calls - baseline, 3)
            self.assertTrue(session.worker.is_alive())
            self.assertFalse(store.acquire(session.serial, 'other'))
            manager.stop(session.serial)
            BlockingRunner.continue_step.set()
            session.worker.join(2)
            self.assertFalse(session.worker.is_alive())
            self.assertTrue(store.acquire(session.serial, 'new-owner'))

    def test_lost_lease_ends_session_fail_closed(self):
        store = ObservedStore(Path(self.temp.name) / 'jobs.sqlite')
        with patch('omniflow.manager.OmniRunner', BlockingRunner):
            _, session = self.start(store)
            with store.call_lock:
                store.fail_after = store.calls + 1
            self.assertTrue(session.stop_event.wait(2))
            BlockingRunner.continue_step.set()
            session.worker.join(2)
            self.assertFalse(session.worker.is_alive())
            self.assertTrue(session.lease_lost)
            self.assertIn('lost_device_lease', session.status)

    def test_renewal_exception_is_not_silently_ignored(self):
        store = ObservedStore(Path(self.temp.name) / 'jobs.sqlite')
        with patch('omniflow.manager.OmniRunner', BlockingRunner):
            _, session = self.start(store)
            with store.call_lock:
                store.error = True
            self.assertTrue(session.stop_event.wait(2))
            BlockingRunner.continue_step.set()
            session.worker.join(2)
            self.assertTrue(session.lease_lost)
            self.assertIn('renewal failed', ' '.join(session.logs))


if __name__ == '__main__':
    unittest.main()


class ActionLeaseGuards(unittest.TestCase):
    def setUp(self):
        from swordx.workflows import create_workflow, record_step
        import numpy as np
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = create_workflow('Permission check', Path(self.tmp.name))
        self.frame = np.random.default_rng(31).integers(0,256,(960,540,3), dtype=np.uint8)
        record_step(self.folder, 'Tap', self.frame, [(140,200,390,400)], taps=[(250,300)])

    def test_stop_before_action_rejects_touch_but_keeps_recognition(self):
        from omniflow.engine import OmniRunner, WorkflowError
        from unittest.mock import MagicMock
        device = MagicMock()
        device.screenshot.return_value = self.frame.copy()
        runner = OmniRunner(device, self.folder, 'com.example.game', can_act=lambda: False)
        runner.step(100)
        with self.assertRaisesRegex(WorkflowError, 'refusing input'):
            runner.step(101)
        device.tap.assert_not_called()

    def test_double_tap_rechecks_ownership_before_second_touch(self):
        from omniflow.engine import OmniRunner, WorkflowError
        from unittest.mock import MagicMock
        device = MagicMock()
        device.screenshot.return_value = self.frame.copy()
        # Entry check, first touch check, then revoke before second touch.
        allowed = iter((True, True, False))
        runner = OmniRunner(device, self.folder, 'com.example.game',
                            can_act=lambda: next(allowed))
        runner.device_size = (540,960)
        with self.assertRaisesRegex(WorkflowError, 'refusing input'):
            runner._execute({'kind':'double_tap','point':[200,200]}, 100)
        device.tap.assert_called_once()

    def test_revoked_ownership_rejects_wait_count_and_dry_run(self):
        from omniflow.engine import OmniRunner, WorkflowError
        from unittest.mock import MagicMock
        for dry_run in (False, True):
            for action in ({'kind': 'wait', 'seconds': 0},
                           {'kind': 'count_run'},
                           {'kind': 'tap', 'point': [200, 200]}):
                with self.subTest(dry_run=dry_run, action=action['kind']):
                    device = MagicMock()
                    runner = OmniRunner(device, self.folder, 'com.example.game',
                                        can_act=lambda: False, dry_run=dry_run)
                    runner.device_size = (540, 960)
                    with self.assertRaisesRegex(WorkflowError, 'ownership lost'):
                        runner._execute(action, 100)
                    self.assertEqual(runner.chain_index, 0)
                    self.assertEqual(runner.runs, 0)
                    device.tap.assert_not_called()

    def test_completion_transition_fails_after_losing_ownership(self):
        from omniflow.engine import OmniRunner, WorkflowError
        from unittest.mock import MagicMock
        from swordx.detection import normalize
        device = MagicMock()
        device.screenshot.return_value = self.frame.copy()
        allowed = True
        completed = []
        runner = OmniRunner(device, self.folder, 'com.example.game',
                            can_act=lambda: allowed,
                            completed=completed.append)
        runner.device_size = (540, 960)
        runner.end_pending = True
        runner.current = 'previous-state'
        # A second confirmed state transition can finish a run without
        # executing an Android command. Revoking ownership prevents this.
        runner._stable_key = 'new-state'
        runner._streak = 1
        from omniflow.engine import Match
        runner.detector.detect = lambda *_: Match('new-state', 'New state', 1.0)
        allowed = False
        with self.assertRaisesRegex(WorkflowError, 'ownership lost'):
            runner.step(100)
        self.assertEqual(runner.runs, 0)
        self.assertEqual(completed, [])
        device.tap.assert_not_called()

    def test_auto_relaunch_rejects_input_after_ownership_loss(self):
        from omniflow.engine import OmniRunner, WorkflowError
        from unittest.mock import MagicMock
        device = MagicMock()
        device.foreground.return_value = 'com.other.app'
        device.running.return_value = False
        runner = OmniRunner(device, self.folder, 'com.example.game', can_act=lambda: False)
        runner._monitor_game(100)
        with self.assertRaisesRegex(WorkflowError, 'refusing input'):
            runner._monitor_game(125)
        device.launch.assert_not_called()
