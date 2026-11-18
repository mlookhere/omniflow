"""Worker polling accounts for in-flight capture; error backoff remains separate."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from omniflow.manager import InstanceManager, Session
from omniflow.schedule import ScheduleStore
from swordx.adb import ADBError


class SimulatedClock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now


class RecordingStop:
    def __init__(self, clock, *, stop_after):
        self.clock = clock
        self.stop_after = stop_after
        self.stopped = False
        self.waits = []

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        self.waits.append(seconds)
        self.clock.now += seconds
        if len(self.waits) >= self.stop_after:
            self.stopped = True
        return self.stopped


class PollCadenceTests(unittest.TestCase):
    def exercise(self, steps):
        clock = SimulatedClock()
        with tempfile.TemporaryDirectory() as tmp:
            db = ScheduleStore(Path(tmp) / 'jobs.sqlite3')
            folder = Path(tmp) / 'workflow'
            folder.mkdir()
            (folder / 'manifest.json').write_text('{}', encoding='utf-8')
            manager = InstanceManager(
                db, device_factory=lambda serial: object(), poll_seconds=.35)
            session = Session('127.0.0.1:16384', 'com.game', 'demo',
                              folder, 'owner')
            stop = RecordingStop(clock, stop_after=len(steps))
            session.stop_event = stop
            starts = []
            upcoming = iter(steps)

            class TimedRunner:
                def __init__(self, *args, **kwargs):
                    pass

                def step(self, started):
                    starts.append(started)
                    duration, fails = next(upcoming)
                    clock.now += duration
                    if fails:
                        raise ADBError('simulated screenshot transport error')

            with patch('omniflow.manager.OmniRunner', TimedRunner), \
                 patch('omniflow.manager.time',
                       SimpleNamespace(monotonic=clock.monotonic,
                                       strftime=time.strftime)):
                manager._run(session)

        return starts, stop.waits, session

    def test_slow_capture_does_not_add_another_full_poll_delay(self):
        starts, waits, session = self.exercise([
            (.2, False), (.8, False), (.1, False)])
        for actual, expected in zip(starts, (100.0, 100.35, 101.15), strict=True):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(waits, (.15, 0, .25), strict=True):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(session.failures, 0)
        self.assertIn('Stopped', session.status)
        self.assertFalse(session.lease_lost)

    def test_capture_error_keeps_existing_full_backoff(self):
        starts, waits, session = self.exercise([
            (.2, True), (.1, False)])
        for actual, expected in zip(starts, (100.0, 101.2), strict=True):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(waits, (1.0, .25), strict=True):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(session.failures, 0)
        self.assertTrue(any('simulated screenshot transport error' in item
                            for item in session.logs))


if __name__ == '__main__':
    unittest.main()
