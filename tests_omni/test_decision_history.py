"""Live decision history is bounded, observable and read-only."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from omniflow.manager import Session


class DecisionHistoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.session = Session('127.0.0.1:16384', 'com.game', 'Example',
                               Path(temp.name), 'owner')

    def test_transition_only_logging_with_live_countdown(self):
        session = self.session
        session.current = 'Start'
        session.record_decision('Confirming screen (1/2); no input')
        session.record_decision('Confirming screen (1/2); no input')
        self.assertEqual(len(session.log_snapshot()), 1)
        session.record_decision('Initial action delay: 3.0s; no input')
        session.record_decision('Initial action delay: 2.0s; no input')
        self.assertEqual(len(session.log_snapshot()), 2)
        self.assertEqual(session.decision, 'Initial action delay: 2.0s; no input')
        session.record_decision('Action 1/1: tap (command issued)')
        self.assertEqual(len(session.log_snapshot()), 3)
        self.assertIn('command issued', session.log_snapshot()[-1])

    def test_state_change_retry_and_between_action_wait(self):
        session = self.session
        session.current = 'Home'
        session.record_decision('Bounded retry in 6.0s; no input')
        session.record_decision('Bounded retry in 5.0s; no input')
        self.assertEqual(len(session.log_snapshot()), 1)
        session.current = 'Settings'
        session.record_decision('Bounded retry in 4.0s; no input')
        session.record_decision('Between-action wait: 1.0s; no input')
        session.record_decision('Between-action wait: 0.1s; no input')
        self.assertEqual(len(session.log_snapshot()), 3)
        self.assertIn('Settings', session.log_snapshot()[-1])
        self.assertEqual(session.decision, 'Between-action wait: 0.1s; no input')

    def test_bounded_concurrent_log_snapshot(self):
        session = self.session
        def writer():
            for i in range(500):
                session.log(f'worker event {i}')
        thread = threading.Thread(target=writer)
        thread.start()
        while thread.is_alive():
            self.assertLessEqual(len(session.log_snapshot()), 200)
        thread.join()
        snapshot = session.log_snapshot()
        self.assertEqual(len(snapshot), 200)
        self.assertTrue(snapshot[-1].endswith('worker event 499'))
        snapshot.clear()
        self.assertEqual(len(session.log_snapshot()), 200)

    def test_invalid_decision_does_not_change_latest(self):
        for item in ('', None, 42):
            self.session.record_decision(item)
        self.assertEqual(self.session.decision, 'Waiting for first screenshot; no input')
        self.assertEqual(self.session.log_snapshot(), [])


if __name__ == '__main__':
    unittest.main()
