"""Database or thread-start failure must not strand an emulator session."""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from omniflow.manager import InstanceManager
from omniflow.schedule import ScheduleStore


class FailingStore(ScheduleStore):
    fail_release = False
    fail_mark = False

    def release(self, serial: str, owner: str) -> None:
        if self.fail_release:
            raise OSError('SQLite release unavailable')
        super().release(serial, owner)

    def mark(self, job_id: str, intended_at: float, outcome: str) -> None:
        if self.fail_mark:
            raise OSError('SQLite mark unavailable')
        super().mark(job_id, intended_at, outcome)


class IdleRunner:
    entered = threading.Event()

    def __init__(self, *args, **kwargs):
        pass

    def step(self, now):
        self.entered.set()


class WorkerCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = FailingStore(Path(self.tmp.name) / 'jobs.sqlite3')
        self.folder = Path(self.tmp.name) / 'workflow'
        self.folder.mkdir()
        (self.folder / 'manifest.json').write_text('{}', encoding='utf-8')
        self.manager = InstanceManager(self.db, poll_seconds=0.35)
        self.addCleanup(self.manager.stop_all)
        IdleRunner.entered = threading.Event()

    def run_worker(self, *, scheduled=False):
        kwargs = {'job_id': 'job1', 'intended_at': 10.0} if scheduled else {}
        with patch('omniflow.manager.OmniRunner', IdleRunner):
            ok, detail = self.manager.start('127.0.0.1:16384', 'com.test.game', 'Demo', self.folder, **kwargs)
            self.assertTrue(ok, detail)
            session = self.manager.snapshot()[0]
            self.addCleanup(lambda: session.worker.join(3))
            self.assertTrue(IdleRunner.entered.wait(3))
            self.manager.stop(session.serial)
            session.worker.join(3)
        self.assertFalse(session.worker.is_alive())
        self.assertEqual(self.manager.snapshot(), [])
        self.assertIn(session, self.manager.completed_sessions)
        self.assertTrue(session.stop_event.is_set())
        return session

    def test_release_error_does_not_strand_session(self):
        self.db.fail_release = True
        session = self.run_worker()
        self.assertIn('lease release failed', ' '.join(session.logs))
        self.assertIn('lease release failed', ' '.join(self.manager.drain_events()))
        # A failed release remains leased in SQLite until expiry: fail closed.
        self.assertFalse(self.db.acquire(session.serial, 'other'))

    def test_mark_error_does_not_strand_session(self):
        self.db.fail_mark = True
        session = self.run_worker(scheduled=True)
        self.assertIn('outcome persistence failed', ' '.join(session.logs))
        self.assertIn('outcome persistence failed', ' '.join(self.manager.drain_events()))
        self.assertTrue(self.db.acquire(session.serial, 'other'))

    def test_both_errors_are_independently_reported(self):
        self.db.fail_mark = self.db.fail_release = True
        session = self.run_worker(scheduled=True)
        messages = ' '.join(session.logs)
        self.assertIn('lease release failed', messages)
        self.assertIn('outcome persistence failed', messages)

    def test_thread_start_exception_releases_slot_and_lease(self):
        with patch.object(threading.Thread, 'start', side_effect=RuntimeError('no threads')):
            ok, message = self.manager.start('127.0.0.1:16384', 'com.test.game', 'Demo', self.folder)
        self.assertFalse(ok)
        self.assertIn('no threads', message)
        self.assertEqual(self.manager.snapshot(), [])
        self.assertTrue(self.db.acquire('127.0.0.1:16384', 'new-owner'))

    def test_thread_start_and_release_failure_still_releases_slot(self):
        self.db.fail_release = True
        with patch.object(threading.Thread, 'start', side_effect=RuntimeError('no threads')):
            ok, _ = self.manager.start('127.0.0.1:16384', 'com.test.game', 'Demo', self.folder)
        self.assertFalse(ok)
        self.assertEqual(self.manager.snapshot(), [])
        self.assertIn('startup lease cleanup failed', ' '.join(self.manager.drain_events()))
        self.assertFalse(self.db.acquire('127.0.0.1:16384', 'new-owner'))


if __name__ == '__main__':
    unittest.main()
