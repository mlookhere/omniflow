"""Workflow chain selection, terminal boundaries, quotas and persistence."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from omniflow.chains import (ChainedRunner, delete_chain, read_chains,
                             save_chain, validate_plan)
from omniflow.engine import OmniRunner
from swordx.workflows import create_workflow, record_step


class FakeDevice:
    def __init__(self):
        self.frame = None
        self.taps = []

    def screenshot(self):
        return self.frame.copy()

    def tap(self, x, y):
        self.taps.append((x, y))


class ChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workflows = {}
        self.screens = {}
        self.keys = {}
        for name, seed in [('A', 12), ('B', 42)]:
            folder = create_workflow(name, self.root)
            self.workflows[name] = folder
            start = self._frame(seed, 75, 115)
            complete = self._frame(seed + 1, 285, 660)
            first = record_step(
                folder, 'Trigger', start, [(75, 115, 135, 160)],
                taps=[(95, 138)])
            last = record_step(
                folder, 'Completed', complete, [(285, 660, 345, 705)],
                taps=[(310, 681)])
            self.keys[name] = (first, last)
            self.screens[name] = (start, complete)
        self.device = FakeDevice()
        self.finished = []

    @staticmethod
    def _frame(seed, x, y):
        pixels = np.full((960, 540, 3), 9, dtype=np.uint8)
        pixels[y:y + 45, x:x + 60] = np.random.default_rng(seed).integers(
            35, 245, (45, 60, 3), dtype=np.uint8)
        return pixels

    def row(self, name, mode='dynamic', limit=0):
        start, complete = self.keys[name]
        return {'workflow': name, 'trigger': start, 'complete': complete,
                'mode': mode, 'limit': limit}

    def chain(self, rows, *, repeat=False):
        source = {'name': 'Routine', 'repeat': repeat, 'steps': rows}
        plan = validate_plan(source, self.workflows)
        return ChainedRunner(
            self.device, plan, self.workflows, 'com.example',
            completed=lambda value: self.finished.append(value))

    def complete(self, runner, name, base):
        self.device.frame = self.screens[name][0]
        runner.step(base)
        self.assertEqual(runner.active_index, ['A', 'B'].index(name))
        runner.step(base + 1)
        runner.step(base + 2)
        self.device.frame = self.screens[name][1]
        runner.step(base + 3)
        runner.step(base + 4)
        self.assertIsNone(runner.active)
        self.assertEqual(self.finished[-1] >= 1, True)

    def test_dynamic_priority_falls_back_to_previous_trigger(self):
        runner = self.chain([self.row('A'), self.row('B')])
        self.complete(runner, 'A', 100)
        self.assertEqual(self.finished, [1])
        # B is not ready; A may be repeated without waiting for B.
        self.complete(runner, 'A', 200)
        self.assertEqual(self.finished, [1, 2])
        self.complete(runner, 'B', 300)
        self.assertEqual(self.finished, [1, 2, 1])
        self.assertTrue(runner.done)
        # Completion-screen recorded taps are NEVER dispatched.
        self.assertEqual(self.device.taps, [(95, 138)] * 2 + [(95, 138)])

    def test_count_mode_requires_full_terminal_screen_for_each_run(self):
        runner = self.chain([self.row('A', 'count', 2), self.row('B')])
        self.device.frame = self.screens['A'][0]
        runner.step(100)
        runner.step(101)
        runner.step(102)
        self.assertEqual(self.finished, [])
        # Screen must actually show the completion state twice.
        self.device.frame = self.screens['A'][1]
        runner.step(103)
        self.assertEqual(self.finished, [])
        runner.step(104)
        self.assertEqual(self.finished, [1])
        self.assertEqual(runner.locked, 0)
        self.complete(runner, 'A', 110)
        self.assertEqual(self.finished, [1, 2])
        self.assertEqual(runner.preferred, 1)
        self.complete(runner, 'B', 120)
        self.assertTrue(runner.done)
        self.assertEqual(self.finished, [1, 2, 1])

    def test_completed_dynamic_can_run_while_count_stage_is_unready(self):
        runner = self.chain([self.row('A'), self.row('B', 'count', 2)])
        self.complete(runner, 'A', 100)
        self.complete(runner, 'B', 110)
        self.assertEqual(runner.locked, 1)  # B still owes one full run.
        self.complete(runner, 'A', 120)      # B unavailable, A re-triggered.
        self.assertEqual(runner.locked, 1)  # Do not abandon B's remaining quota.
        self.complete(runner, 'B', 130)
        self.assertTrue(runner.done)
        self.assertEqual(self.finished, [1, 1, 2, 2])

    def test_timed_expiry_finishes_inflight_run_then_handoffs(self):
        runner = self.chain([self.row('A', 'timed', 1), self.row('B')])
        self.complete(runner, 'A', 100)
        self.assertEqual(runner.locked, 0)
        self.device.frame = self.screens['A'][0]
        runner.step(159)  # Start final run before the minute is over.
        self.assertEqual(runner.active_index, 0)
        runner.step(161)
        runner.step(162)
        self.device.frame = self.screens['A'][1]
        runner.step(170)
        self.assertEqual(self.finished, [1])
        runner.step(171)
        self.assertEqual(self.finished, [1, 2])
        self.assertEqual(runner.preferred, 1)
        self.complete(runner, 'B', 180)
        self.assertTrue(runner.done)

    def test_timed_expiry_while_waiting_can_skip_to_next_ready_trigger(self):
        runner = self.chain([self.row('A', 'timed', 1), self.row('B')])
        self.complete(runner, 'A', 100)
        self.device.frame = self.screens['B'][0]
        runner.step(161)
        self.assertEqual(runner.active_index, 1)
        self.assertEqual(self.finished, [1])
        self.complete_selected(runner, 'B', 162)
        self.assertTrue(runner.done)

    def complete_selected(self, runner, name, at):
        runner.step(at)
        runner.step(at + 1)
        self.device.frame = self.screens[name][1]
        runner.step(at + 2)
        runner.step(at + 3)

    def test_idle_never_taps_without_a_trigger_and_pause_requires_recheck(self):
        runner = self.chain([self.row('A')])
        self.device.frame = np.full((960, 540, 3), 255, np.uint8)
        runner.step(1)
        runner.step(2)
        self.assertIsNone(runner.active)
        self.assertEqual(self.device.taps, [])
        self.device.frame = self.screens['A'][0]
        runner.step(3)
        runner.step(4)
        runner.resume_after_pause(5)
        runner.step(5)
        self.assertEqual(self.device.taps, [])
        runner.step(6)
        self.assertEqual(self.device.taps, [(95, 138)])

    def test_legacy_single_runner_still_counts_original_completion(self):
        folder = self.workflows['A']
        manifest = folder / 'manifest.json'
        data = json.loads(manifest.read_text(encoding='utf-8'))
        data['actions'][self.keys['A'][0]]['end'] = True
        manifest.write_text(json.dumps(data), encoding='utf-8')
        self.device.frame = self.screens['A'][0]
        single = OmniRunner(self.device, folder, 'com.example',
                            completed=lambda count: self.finished.append(count))
        single.step(1)
        single.step(2)
        self.device.frame = self.screens['A'][1]
        single.step(3)
        single.step(4)
        self.assertEqual(self.finished, [1])

    def test_atomic_saved_chain_and_validation_fail_closed(self):
        source = {'name': 'Routine', 'repeat': False,
                  'steps': [self.row('A'), self.row('B', 'count', 3)]}
        save_chain(self.root, source, self.workflows)
        before = (self.root / 'chains.json').read_bytes()
        self.assertEqual(read_chains(self.root)['Routine'], source)
        for invalid in [
            dict(source, steps=[dict(self.row('A'), complete=self.keys['A'][0])]),
            dict(source, steps=[dict(self.row('A'), mode='count', limit=-2)]),
            dict(source, steps=[dict(self.row('A'), workflow='../test')]),
            dict(source, repeat='false'),
        ]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    save_chain(self.root, invalid, self.workflows)
                self.assertEqual((self.root / 'chains.json').read_bytes(), before)
        delete_chain(self.root, 'Routine')
        self.assertEqual(read_chains(self.root), {})


if __name__ == '__main__':
    unittest.main()
