"""One-command hardware collection remains read-only and never approves a release."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import collect_hardware_evidence as collector


class CollectEvidenceTests(unittest.TestCase):
    def test_successful_subcommands_are_not_full_hardware_acceptance(self):
        seen = []

        def fake_run(command, **kwargs):
            seen.append((command, kwargs))
            report = Path(command[command.index('--output') + 1])
            report.write_text(json.dumps({'passed': True,
                                          'hardware_acceptance_complete': False}))
            return subprocess.CompletedProcess(command, 0, 'read-only simulated test\n', '')

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(collector.subprocess, 'run', side_effect=fake_run):
            self.assertEqual(collector.main(['--output-root', tmp, '--serial', '127.0.0.1:16384']), 0)
            folders = list(Path(tmp).glob('run-*'))
            self.assertEqual(len(folders), 1)
            manifest = json.loads((folders[0] / 'run-manifest.json').read_text())
            self.assertTrue(manifest['passed_read_only_checks'])
            self.assertFalse(manifest['hardware_acceptance_complete'])
            self.assertTrue(all(x['report_valid'] for x in manifest['steps'].values()))
            self.assertFalse(manifest['branch_protection_verified'])
            self.assertEqual([name for name in manifest['steps']], ['video', 'benchmark', 'verify'])
            self.assertEqual(len(seen), 3)
            self.assertEqual([Path(item[0][1]).name for item in seen],
                             ['diagnose_capture.py', 'benchmark_mumu.py', 'verify_hardware_evidence.py'])
            self.assertTrue(all(item[1]['cwd'] == collector.ROOT for item in seen))
            self.assertTrue(all(item[1].get('check') is False for item in seen))
            self.assertIn('--serial', seen[0][0])
            self.assertNotIn('--allow-snapshot-only', seen[0][0])
            self.assertIn('--instances', seen[1][0])
            self.assertIn('10', seen[1][0])
            self.assertTrue(all((folders[0] / (name + '.log')).exists()
                                for name in ('video', 'benchmark', 'verify')))

    def test_missing_verifier_report_never_looks_green(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(collector.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            self.assertEqual(collector.main(['--output-root', tmp]), 1)
            folder = next(Path(tmp).glob('run-*'))
            report = json.loads((folder / 'run-manifest.json').read_text())
            self.assertFalse(report['passed_read_only_checks'])
            self.assertTrue(all(not step['report_valid'] for step in report['steps'].values()))

    def test_multiple_runs_keep_evidence_separate(self):
        def fake_run(command, **kwargs):
            Path(command[command.index('--output') + 1]).write_text(
                json.dumps({'passed': True, 'hardware_acceptance_complete': False}))
            return subprocess.CompletedProcess(command, 0, '', '')

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(collector.subprocess, 'run', side_effect=fake_run):
            self.assertEqual(collector.main(['--output-root', tmp]), 0)
            self.assertEqual(collector.main(['--output-root', tmp]), 0)
            self.assertEqual(len(list(Path(tmp).glob('run-*'))), 2)

    def test_failures_continue_collection_and_preserve_nonzero_result(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(collector.subprocess, 'run', side_effect=[
                 subprocess.CompletedProcess([], 1, '', 'no video\n'),
                 subprocess.CompletedProcess([], 0, '', ''),
                 subprocess.CompletedProcess([], 1, '', 'rejected\n'),
             ]):
            self.assertEqual(collector.main(['--output-root', tmp]), 1)
            folder = next(Path(tmp).glob('run-*'))
            manifest = json.loads((folder / 'run-manifest.json').read_text())
            self.assertFalse(manifest['passed_read_only_checks'])
            self.assertEqual(manifest['steps']['video']['exit_code'], 1)
            self.assertEqual(manifest['steps']['benchmark']['exit_code'], 0)
            self.assertEqual(manifest['steps']['verify']['exit_code'], 1)
            self.assertIn('no video', (folder / 'video.log').read_text())

    def test_timeout_is_failed_and_next_diagnostics_still_run(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(collector.subprocess, 'run', side_effect=[
                 subprocess.TimeoutExpired('video', 175, output=b'trace'),
                 subprocess.CompletedProcess([], 0, '', ''),
                 subprocess.CompletedProcess([], 1, '', ''),
             ]):
            self.assertEqual(collector.main(['--output-root', tmp]), 1)
            folder = next(Path(tmp).glob('run-*'))
            report = json.loads((folder / 'run-manifest.json').read_text())
            self.assertIsNone(report['steps']['video']['exit_code'])
            self.assertFalse(report['passed_read_only_checks'])
            self.assertIn('timed out', (folder / 'video.log').read_text())


if __name__ == '__main__':
    unittest.main()
