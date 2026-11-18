"""Live decision feedback must never authorize an extra command."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from omniflow.engine import Match, OmniRunner, UNKNOWN
from swordx.workflows import create_workflow, record_step


class FakeDevice:
    def __init__(self, frame):
        self.frame = frame
        self.taps = []
    def screenshot(self):
        return self.frame.copy()
    def tap(self, *args):
        self.taps.append(args)


class DecisionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = create_workflow('Decision feedback', Path(tmp.name))
        self.frame = np.random.default_rng(18).integers(0, 255, (960, 540, 3), dtype=np.uint8)
        self.key = record_step(self.folder, 'Tap', self.frame,
                               [(100, 100, 220, 180)], taps=[(160, 140)])
        self.device = FakeDevice(self.frame)
        self.runner = OmniRunner(self.device, self.folder, 'com.example.game')

    def test_confirmation_action_and_completion_are_reported(self):
        found = Match(self.key, 'Tap', 1.0)
        with patch.object(self.runner.detector, 'detect', return_value=found):
            self.runner.step(100)
            self.assertIn('Confirming screen', self.runner.last_decision)
            self.assertEqual(self.device.taps, [])
            self.runner.step(101)
            self.assertEqual(self.runner.last_decision, 'Action 1/1: tap (command issued)')
            self.assertEqual(len(self.device.taps), 1)
            self.runner.step(102)
        self.assertEqual(self.runner.last_decision,
                         'Action sequence complete; awaiting screen change')
        self.assertEqual(len(self.device.taps), 1)

    def test_unknown_and_passive_never_issue_input(self):
        with patch.object(self.runner.detector, 'detect', return_value=UNKNOWN):
            self.runner.step(100)
            self.runner.step(101)
        self.assertEqual(self.runner.last_decision, 'Unknown screen; no input')
        self.assertEqual(self.device.taps, [])
        passive = Match(self.key, 'Wait', 1.0, passive=True)
        with patch.object(self.runner.detector, 'detect', return_value=passive):
            self.runner.step(102)
            self.runner.step(103)
        self.assertEqual(self.runner.last_decision, 'Passive WAIT state; no input')
        self.assertEqual(self.device.taps, [])

    def test_initial_delay_and_dry_run_redaction(self):
        import json
        manifest = self.folder / 'manifest.json'
        value = json.loads(manifest.read_text(encoding='utf-8'))
        value['actions'][self.key]['pre_tap_delay_seconds'] = 10
        manifest.write_text(json.dumps(value), encoding='utf-8')
        dry = OmniRunner(self.device, self.folder, 'com.example.game', dry_run=True)
        found = Match(self.key, 'Tap', 1.0)
        with patch.object(dry.detector, 'detect', return_value=found):
            dry.step(100)
            dry.step(101)
            self.assertIn('Initial action delay:', dry.last_decision)
            dry.step(111)
        self.assertEqual(dry.last_decision, 'Action 1/1: tap (no input)')
        self.assertEqual(self.device.taps, [])


if __name__ == '__main__':
    unittest.main()
