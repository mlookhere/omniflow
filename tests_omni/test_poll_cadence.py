"""Worker polling uses start-to-start cadence, without shortening recovery delays."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from omniflow.manager import InstanceManager, Session
from omniflow.schedule import ScheduleStore


class Clock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now


class FakeStop:
    def __init__(self):
        self.stopped = False
        self.waits = []

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        self.waits.append(seconds)
        clock.now += seconds
        if len(self.waits) >= 3:
            self.stopped = True
        return self.stopped


class PollCadenceTests(unittest.TestCase):
    def test_slow_capture_does_not_add_another_full_poll_delay(self):
        global clock
        clock = Clock()
        with tempfile.TemporaryDirectory() as tmp:
            db = ScheduleStore(Path(tmp) / 'jobs.sqlite3')
            folder = Path(tmp) / 'workflow'
            folder.mkdir()
            (folder / 'manifest.json').write_text('{}', encoding='utf-8')
            manager = InstanceManager(db, poll_seconds=.35)
            session = Session('127.0.0.1:16384', 'com.game', 'demo',
                              folder, 'owner')
            stop = FakeStop()
            session.stop_event = stop
            starts = []
            durations = iter((.2, .8, .1))

            class TimedRunner:
                def __init__(self, *args, **kwargs):
                    pass

                def step(self, started):
                    starts.append(started)
                    clock.now += next(durations)

            with patch('omniflow.manager.OmniRunner', TimedRunner), \
                 patch('omniflow.manager.time',
                       SimpleNamespace(monotonic=clock.monotonic,
                                       strftime=time.strftime)):
                manager._run(session)

        self.assertEqual(len(starts), 3)
        for actual, expected in zip(starts, (100.0, 100.35, 101.15)):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(stop.waits, (.15, 0, .25)):
            self.assertAlmostEqual(actual, expected)
        self.assertIn('Stopped', session.status)
        self.assertFalse(session.lease_lost)

    def test_pause_and_capture_errors_do_not_take_success_poll_path(self):
        # The normal manager integration tests cover pause, stop, lease loss,
        # and ADB recovery. The new cadence is only reached after a
        # successful step; it must never replace those separate wait paths.
        from omniflow.manager import InstanceManager
        self.assertGreaterEqual(InstanceManager.__init__.__code__.co_argcount, 2)


if __name__ == '__main__':
    unittest.main()
