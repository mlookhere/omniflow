"""Failed MuMu discovery must not leave a previously green video report."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import diagnose_capture
from tools.benchmark_mumu import write_json


class DiagnosticPreflightTests(unittest.TestCase):
    def test_missing_mumu_overwrites_stale_success(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / 'video.json'
            write_json(output, {'passed': True, 'displayed_video_frames': 3000})
            with patch('omniflow.devices.discover_mumu', return_value=[]):
                status = diagnose_capture.main(['--seconds', '10', '--output', str(output)])
            self.assertEqual(status, 2)
            report = json.loads(output.read_text())
            self.assertFalse(report['passed'])
            self.assertIsNone(report['device'])
            self.assertEqual(report['elapsed_seconds'], 0.0)
            self.assertEqual(report['displayed_video_frames'], 0)
            self.assertEqual(report['fallback_snapshots'], 0)
            self.assertIn('No MuMu', ' '.join(report['failures']))
            self.assertFalse(list(output.parent.glob('*.tmp')))

    def test_discovery_error_writes_failing_report(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / 'results' / 'video.json'
            with patch('omniflow.devices.discover_mumu',
                       side_effect=OSError('adb disconnected')):
                status = diagnose_capture.main(['--seconds', '10', '--output', str(output)])
            self.assertEqual(status, 2)
            report = json.loads(output.read_text())
            self.assertFalse(report['passed'])
            self.assertEqual(report['displayed_video_frames'], 0)
            self.assertIn('adb disconnected', ' '.join(report['failures']))
            self.assertFalse(list(output.parent.glob('*.tmp')))


if __name__ == '__main__':
    unittest.main()
