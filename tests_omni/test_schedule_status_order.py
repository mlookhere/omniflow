"""Out-of-order scheduled session completions must not misreport job status."""
import tempfile
import unittest
from pathlib import Path

from omniflow.schedule import ScheduleStore


class LatestOccurrenceStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = ScheduleStore(Path(self.tmp.name) / 'jobs.sqlite')
        self.clock = 1_800_000_000.0
        self.job_id = self.db.save(
            'Repeat', '127.0.0.1:16384', 'com.example', 'Demo',
            'interval', {'minutes': 1, 'anchor': self.clock + 60},
            'UTC', now=self.clock,
        )
        self.first = self.clock + 60
        self.second = self.clock + 120

    def status(self):
        return self.db.list_jobs()[0].last_status

    def test_late_first_completion_preserves_newer_claim(self):
        self.assertEqual(len(self.db.claim_due(self.first + 1)), 1)
        self.assertEqual(len(self.db.claim_due(self.second + 1)), 1)
        self.assertEqual(self.status(), 'claimed')

        # The first task's runtime might exceed the minute between claims.
        self.db.mark(self.job_id, self.first, 'duration_limit')
        self.assertEqual(self.status(), 'claimed')
        self.assertEqual(self.db.history()[1][3], 'duration_limit')

        self.db.mark(self.job_id, self.second, 'runs_limit')
        self.assertEqual(self.status(), 'runs_limit')
        self.assertEqual(self.db.history()[0][3], 'runs_limit')

        # Duplicate old completion cannot overwrite the newest result.
        self.db.mark(self.job_id, self.first, 'stopped')
        self.assertEqual(self.status(), 'runs_limit')

    def test_missing_occurrence_cannot_overwrite_schedule_status(self):
        self.db.claim_due(self.first + 1)
        self.db.mark(self.job_id, self.first - 1, 'error')
        self.assertEqual(self.status(), 'claimed')
        self.assertEqual(len(self.db.history()), 1)

    def test_multiple_process_stores_preserve_newest_outcome(self):
        other = ScheduleStore(self.db.path)
        self.db.claim_due(self.first + 1)
        other.claim_due(self.second + 1)
        self.db.mark(self.job_id, self.first, 'stopped')
        self.assertEqual(other.list_jobs()[0].last_status, 'claimed')
        other.mark(self.job_id, self.second, 'skipped_busy_or_invalid')
        self.assertEqual(self.status(), 'skipped_busy_or_invalid')

    def test_single_occurrence_still_updates_job_status(self):
        self.db.claim_due(self.first + 1)
        self.db.mark(self.job_id, self.first, 'runs_limit')
        self.assertEqual(self.status(), 'runs_limit')


if __name__ == '__main__':
    unittest.main()
