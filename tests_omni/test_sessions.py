"""Ten workers, per-device ownership, pause, completion and cleanup."""
import tempfile
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
