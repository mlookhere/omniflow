import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from omniflow.schedule import ScheduleStore, next_occurrence, MAX_INSTANCES


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.db = ScheduleStore(Path(self.t.name)/'jobs.db')
        self.epoch = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc).timestamp()

    def create(self, kind='interval', spec=None, serial='127.0.0.1:16384', **extra):
        return self.db.save('Daily tasks', serial, 'com.example.game', 'My workflow',kind,
                            spec or {'minutes':5,'anchor':self.epoch+60},'America/Los_Angeles',
                            now=self.epoch,**extra)

    def test_timezones_daily_and_weekly(self):
        after=datetime(2026,11,3,12,tzinfo=timezone.utc).timestamp()
        timestamp=next_occurrence('daily',{'time':'07:30'},'America/Los_Angeles',after)
        self.assertEqual(datetime.fromtimestamp(timestamp,ZoneInfo('America/Los_Angeles')).strftime('%Y-%m-%d %H:%M'),
                         '2026-11-03 07:30')
        weekly=next_occurrence('weekly',{'time':'07:30','weekdays':[2]},'America/Los_Angeles',after)
        self.assertEqual(datetime.fromtimestamp(weekly,ZoneInfo('America/Los_Angeles')).strftime('%a'), 'Wed')

    def test_during_spring_forward_nonexistent_hour_is_skipped(self):
        after=datetime(2027,3,13,10,tzinfo=timezone.utc).timestamp()
        when=next_occurrence('daily',{'time':'02:30'},'America/Los_Angeles',after)
        self.assertEqual(datetime.fromtimestamp(when,ZoneInfo('America/Los_Angeles')).date().isoformat(), '2027-03-13')
        again=next_occurrence('daily',{'time':'02:30'},'America/Los_Angeles',when)
        self.assertEqual(datetime.fromtimestamp(again,ZoneInfo('America/Los_Angeles')).date().isoformat(), '2027-03-15')

    def test_atomically_claim_once_across_two_instances(self):
        job=self.create()
        other=ScheduleStore(self.db.path)
        self.assertEqual(self.db.claim_due(self.epoch),[])
        one=self.db.claim_due(self.epoch+65)
        two=other.claim_due(self.epoch+65)
        self.assertEqual(len(one),1)
        self.assertEqual(two,[])
        self.assertEqual(one[0][0].id,job)
        self.assertTrue(one[0][1])
        self.assertEqual(len(self.db.history()),1)

    def test_old_job_skipped_not_replayed(self):
        job=self.create(grace_seconds=5)
        result=self.db.claim_due(self.epoch+600)
        self.assertEqual(len(result),1)
        self.assertFalse(result[0][1])
        self.assertEqual(self.db.history()[0][3],'skipped_late')
        self.assertGreater(self.db.list_jobs()[0].next_at,self.epoch+600)

    def test_once_disables_after_claim_and_keeps_history(self):
        at=datetime.fromtimestamp(self.epoch+90,timezone.utc).isoformat()
        job=self.create('once',{'at':at})
        self.assertEqual(len(self.db.claim_due(self.epoch+91)),1)
        self.assertFalse(self.db.list_jobs()[0].enabled)
        self.assertIsNone(self.db.list_jobs()[0].next_at)
        self.db.mark(job,self.epoch+90,'stopped')
        self.assertEqual(self.db.history()[0][3],'stopped')

    def test_lease_prevents_duplicate_device_control(self):
        self.assertTrue(self.db.acquire('device1','controller-a',now=100))
        self.assertFalse(self.db.acquire('device1','controller-b',now=101))
        self.assertFalse(self.db.heartbeat('device1','controller-b',now=102))
        self.assertTrue(self.db.heartbeat('device1','controller-a',now=102))
        self.db.release('device1','controller-b')
        self.assertFalse(self.db.acquire('device1','controller-b',now=103))
        self.db.release('device1','controller-a')
        self.assertTrue(self.db.acquire('device1','controller-b',now=104))

    def test_global_limit_ten_and_expired_lease_reclaimed(self):
        for i in range(MAX_INSTANCES):
            self.assertTrue(self.db.acquire(f'device-{i}',str(i),now=100))
        self.assertFalse(self.db.acquire('device-extra','x',now=100))
        self.assertTrue(self.db.acquire('device-extra','x',now=10000))
        self.assertEqual(len(self.db.history()),0)

    def test_run_and_duration_limits_support_unlimited(self):
        job=self.create(max_runs=5,max_minutes=0)
        saved=self.db.list_jobs()[0]
        self.assertEqual((saved.max_runs,saved.max_minutes),(5,0))
        with self.assertRaises(ValueError):
            self.create(max_runs=-1)
        with self.assertRaises(ValueError):
            self.create(max_minutes=10081)

    def test_old_schedule_database_migrates_without_losing_existing_jobs(self):
        import sqlite3
        old=Path(self.t.name)/'old.sqlite'
        db=sqlite3.connect(old)
        db.executescript('''CREATE TABLE jobs (
            id TEXT PRIMARY KEY, name TEXT, serial TEXT, package TEXT,
            workflow TEXT, kind TEXT, spec TEXT, timezone TEXT,
            next_at REAL, enabled INTEGER, max_minutes INTEGER,
            grace_seconds INTEGER, last_status TEXT
        );''')
        db.execute('''INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
            'legacy','Old','127.0.0.1:16384','com.test','Workflow','daily',
            '{"time":"10:00"}','UTC',self.epoch+50,1,60,300,''))
        db.commit();db.close()
        restored=ScheduleStore(old)
        self.assertEqual(restored.list_jobs()[0].name,'Old')
        self.assertEqual(restored.list_jobs()[0].max_runs,0)
        restored.set_enabled('legacy',False)
        self.assertFalse(restored.list_jobs()[0].enabled)

    def test_invalid_weekdays_and_duplicate_names(self):
        with self.assertRaises(ValueError):
            next_occurrence('weekly',{'time':'10:30','weekdays':[]},'UTC',self.epoch)
        with self.assertRaises(ValueError):
            self.db.save('','device','com.example','wf','daily',{'time':'12:00'},'UTC',now=self.epoch)


if __name__=='__main__':unittest.main()
