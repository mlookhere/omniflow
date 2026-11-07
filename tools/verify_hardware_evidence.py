"""Evaluate two *real* read-only MuMu reports; never start or control a game.

    python tools/verify_hardware_evidence.py --video video-health.json \
        --benchmark capture-ten.json --output hardware-evidence.json

A passing read-only report is not final approval for real-game gestures or GPU load.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omniflow.hardware_evidence import EvidenceCriteria, evaluate_hardware_evidence
from tools.benchmark_mumu import write_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', type=Path, required=True, help='Actual diagnose_capture.py JSON')
    parser.add_argument('--benchmark', type=Path, required=True, help='Actual benchmark_mumu.py JSON')
    parser.add_argument('--output', type=Path, required=True, help='Atomic report destination')
    parser.add_argument('--instances', type=int, default=10)
    parser.add_argument('--seconds', type=float, default=120)
    parser.add_argument('--min-video-fps', type=float, default=20)
    parser.add_argument('--max-preview-gap', type=float, default=3)
    parser.add_argument('--max-video-gap', type=float, default=6)
    parser.add_argument('--min-capture-fps', type=float, default=0.8)
    parser.add_argument('--max-error22-recovery', type=float, default=6)
    args = parser.parse_args(argv)
    try:
        criteria = EvidenceCriteria(args.instances, args.seconds, args.min_video_fps,
                                    args.max_preview_gap, args.max_video_gap, args.min_capture_fps,
                                    args.max_error22_recovery)
    except ValueError as exc:
        parser.error(str(exc))
    reports = []
    errors = []
    for label, path in [('video', args.video), ('benchmark', args.benchmark)]:
        try:
            report = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(report, dict):
                raise ValueError('root must be a JSON object')
        except (OSError, ValueError) as exc:
            errors.append(f'{label}: unable to load {path}: {exc}')
            report = {}
        reports.append(report)
    result = evaluate_hardware_evidence(*reports, criteria)
    result['failures'] = errors + result['failures']
    result['passed'] = not result['failures']
    result['source_files'] = {'video': str(args.video), 'benchmark': str(args.benchmark)}
    write_json(args.output, result)
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
