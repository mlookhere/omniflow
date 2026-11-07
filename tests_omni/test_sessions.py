"""Ten workers, per-device ownership, pause, completion and cleanup."""
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

from omniflow.manager import InstanceManager
from omniflow.schedule import MAX_INSTANCES, ScheduleStore
from swordx.workflows import create_workflow, record_step


class MockADB:
    def __init__(self, serial=''):
        self.serial = serial
        self.frame = np.random.default_rng(17).integers(0,255,(960,540,3),dtype=np.uint8)
    def screenshot(self):return self.frame.copy()
    def size(self):return 2160,3840
    def tap(self,*args):pass
    def swipe(self,*args):pass
    def running(self,p):return True
    def foreground(self):return 'com.example.game'


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=ScheduleStore(self.root/'jobs.sqlite3')
        self.folder=create_workflow('Demo',self.root)
        self.frame=np.random.default_rng(17).integers(0,255,(960,540,3),dtype=np.uint8)
        record_step(self.folder,'Do nothing',self.frame,[(150,150,300,220)],passive=True)
        self.manager=InstanceManager(self.db,device_factory=MockADB,poll_seconds=0.35)
        self.addCleanup(self.cleanup_sessions)

    def cleanup_sessions(self):
        self.manager.stop_all()
        for s in self.manager.snapshot():
            if s.worker:s.worker.join(2)

    def wait_for(self,predicate,limit=3):
        end=time.monotonic()+limit
        while time.monotonic()<end:
            if predicate():return True
            time.sleep(0.01)
        return False

    def test_ten_sessions_are_isolated_and_eleventh_blocked(self):
        for i in range(MAX_INSTANCES):
            good,_=self.manager.start(f'emulator:{i}','com.example.game','Demo',self.folder)
            self.assertTrue(good)
        good,why=self.manager.start('emulator:extra','com.example.game','Demo',self.folder)
        self.assertFalse(good)
        self.assertIn('Maximum',why)
        self.assertTrue(self.wait_for(lambda:all(s.status=='Running' for s in self.manager.snapshot())))
        self.assertEqual(len(self.manager.snapshot()),10)
        self.assertTrue(all(s.worker.is_alive() for s in self.manager.snapshot()))
        self.manager.stop('emulator:0')
        self.assertTrue(self.wait_for(lambda:len(self.manager.snapshot())==9))
        good,_=self.manager.start('emulator:extra','com.example.game','Demo',self.folder)
        self.assertTrue(good)

    def test_prevents_duplicate_serial_even_in_second_manager(self):
        self.assertTrue(self.manager.start('emulator:1','com.example.game','Demo',self.folder)[0])
        other=InstanceManager(ScheduleStore(self.db.path),device_factory=MockADB)
        good,reason=other.start('emulator:1','com.example.game','Demo',self.folder)
        self.assertFalse(good)
        self.assertIn('another OmniFlow',reason)

    def test_pause_resume_and_release(self):
        self.assertTrue(self.manager.start('emulator:1','com.example.game','Demo',self.folder)[0])
        session=self.manager.snapshot()[0]
        self.manager.pause('emulator:1',True)
        self.assertTrue(self.wait_for(lambda:session.status=='Paused'))
        self.manager.pause('emulator:1',False)
        self.assertTrue(self.wait_for(lambda:session.status=='Running'))
        self.manager.stop('emulator:1')
        self.assertTrue(self.wait_for(lambda:not session.worker.is_alive()))
        self.assertEqual(session.stats.finished_at is not None,True)
        self.assertTrue(self.db.acquire('emulator:1','fresh'))

    def test_resume_reconfirms_state_in_live_worker(self):
        self.assertTrue(self.manager.start('emulator:1', 'com.example.game',
                                           'Demo', self.folder)[0])
        session = self.manager.snapshot()[0]
        self.assertTrue(self.wait_for(lambda: session.status == 'Running'))
        self.manager.pause('emulator:1', True)
        self.assertTrue(self.wait_for(lambda: session.status == 'Paused'))
        self.manager.pause('emulator:1', False)
        self.assertTrue(self.wait_for(lambda: any(
            'Session resumed; reconfirming state before input' in line
            for line in session.logs)))
        self.assertEqual(session.status, 'Running')

    def test_pause_during_in_flight_capture_prevents_touch_and_keeps_worker(self):
        folder = create_workflow('Tap after screen', self.root)
        record_step(folder, 'Tap', self.frame, [(150, 150, 300, 220)],
                    taps=[(250, 300)])

        class DelayedSecondCapture(MockADB):
            def __init__(self, serial=''):
                super().__init__(serial)
                self.frame = target_frame
                self.second_started = threading.Event()
                self.release_second = threading.Event()
                self.capture_count = 0
                self.taps = []

            def screenshot(self):
                self.capture_count += 1
                if self.capture_count == 2:
                    self.second_started.set()
                    if not self.release_second.wait(3):
                        raise OSError('Test capture synchronization timed out')
                return self.frame.copy()

            def tap(self, *args):
                self.taps.append(args)

        target_frame = self.frame
        device = DelayedSecondCapture()
        manager = InstanceManager(self.db,
                                  device_factory=lambda serial: device,
                                  poll_seconds=0.35)
        ok, reason = manager.start('emulator:pause', 'com.example.game',
                                   'Tap', folder)
        self.assertTrue(ok, reason)
        session = manager.snapshot()[0]

        def cleanup_worker():
            # Windows holds SQLite files open while worker/keepalive threads
            # still run. Stop first, then join before TemporaryDirectory cleanup.
            device.release_second.set()
            manager.stop_all()
            session.worker.join(3)
            self.assertFalse(session.worker.is_alive())

        self.addCleanup(cleanup_worker)
        self.assertTrue(device.second_started.wait(3))
        manager.pause(session.serial, True)
        device.release_second.set()

        self.assertTrue(self.wait_for(lambda: session.status == 'Paused'))
        self.assertFalse(device.taps)
        self.assertTrue(session.worker.is_alive())
        self.assertEqual(session.failures, 0)

        manager.pause(session.serial, False)
        self.assertTrue(self.wait_for(lambda: bool(device.taps), limit=3))
        self.assertEqual(len(device.taps), 1)
        self.assertTrue(session.worker.is_alive())
        self.assertTrue(any('reconfirming state before input' in line
                            for line in session.logs))

    def test_recent_session_image_is_copied_and_expires(self):
        from omniflow.manager import Session
        from pathlib import Path
        import numpy as np
        session = Session('127.0.0.1:16384', 'com.example.game',
                          'test', Path(self.folder), 'owner')
        frame = np.zeros((3, 4, 3), dtype=np.uint8)
        with session._image_lock:
            session.image = frame
            session.image_at = time.monotonic()
        copy = session.recent_image()
        self.assertIsNotNone(copy)
        copy[0, 0] = 255
        self.assertFalse(np.any(session.image[0, 0]))
        with session._image_lock:
            session.image_at = time.monotonic() - 5
        self.assertIsNone(session.recent_image())
        with self.assertRaises(ValueError):
            session.recent_image(0)

    def test_scheduler_busy_device_marks_skipped(self):
        self.assertTrue(self.manager.start('emulator:1','com.example.game','Demo',self.folder)[0])
        start=time.time()
        self.db.save('Booked','emulator:1','com.example.game','Demo','interval',
                     {'minutes':5,'anchor':start+1},'UTC',now=start)
        logs=self.manager.tick_schedules({'Demo':self.folder},now=start+2)
        self.assertEqual(len(logs),1)
        self.assertEqual(self.db.history()[0][3],'skipped_busy_or_invalid')
        self.assertEqual(len(self.manager.snapshot()),1)

if __name__=='__main__':unittest.main()
