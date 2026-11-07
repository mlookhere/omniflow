"""Collect read-only Windows/MuMu acceptance evidence in a fresh run folder.

    python tools/collect_hardware_evidence.py --workflow path/to/workflow

Runs existing diagnostics, never sends game input and never grants hardware
acceptance. Attach the resulting folder's JSON and logs to control issue #1.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_step(name: str, command: list[str], output: Path, timeout: int, report: Path) -> dict:
    """Preserve each command's exit code/log even when it fails or times out."""
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True,
                                text=True, errors='replace', timeout=timeout,
                                check=False)
        code, log = result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired as exc:
        code = None
        stdout = exc.stdout or b''
        stderr = exc.stderr or b''
        log = ''.join(x.decode(errors='replace') if isinstance(x, bytes) else x
                      for x in (stdout, stderr))
        log += f'\n{name} timed out after {timeout}s\n'
    except OSError as exc:
        code, log = None, f'{type(exc).__name__}: {exc}\n'
    (output / f'{name}.log').write_text(log, encoding='utf-8')
    try:
        evidence = json.loads(report.read_text(encoding='utf-8'))
        valid = isinstance(evidence, dict) and evidence.get('passed') is True
        if name == 'verify':
            valid = valid and evidence.get('hardware_acceptance_complete') is False
    except (OSError, ValueError):
        valid = False
    return {'exit_code': code, 'passed': code == 0 and valid,
            'report': report.name, 'log': f'{name}.log',
            'report_valid': valid}


def estimate_ten_instance_capture(benchmark: Path) -> dict | None:
    """Project ideal screenshot workload, not unmeasured 10-device capacity.

    Whole-host CPU/RAM/GPU includes the OS; extrapolating it needs a separate
    idle-host baseline, so never multiply its reported values by ten.
    """
    try:
        report = json.loads(benchmark.read_text(encoding='utf-8'))
        if (report.get('passed') is not True or report.get('tested_instances') != 1
                or len(report.get('devices', [])) != 1):
            return None
        rate = report['devices'][0]['effective_fps']
        if (isinstance(rate, bool) or not isinstance(rate, (int, float))
                or not math.isfinite(rate) or rate <= 0):
            return None
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None
    return {
        'measured_instances': 1,
        'projected_instances': 10,
        'linear_assumption_only': True,
        'estimated_aggregate_screenshot_fps_without_contention': round(rate * 10, 3),
        'host_resource_projection': None,
        'host_resource_note': ('Numerical CPU/RAM/GPU estimates require subtracting '
                               'a separately measured host idle baseline first.'),
        'ten_instance_operation_verified': False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instances', type=int, choices=(1, 10), default=1,
                        help='1 for single-device estimate (default); 10 for actual concurrency')
    parser.add_argument('--serial', default='', help='Target MuMu serial; default discovers first')
    parser.add_argument('--workflow', type=Path, help='Representative workflow for recognition benchmarking')
    parser.add_argument('--output-root', type=Path, default=ROOT / 'hardware-evidence',
                        help='Parent of unique per-run evidence folders (never reused)')
    args = parser.parse_args(argv)
    if args.workflow and not (args.workflow / 'manifest.json').is_file():
        parser.error('workflow must contain manifest.json')

    args.output_root = args.output_root.resolve()
    args.output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = Path(tempfile.mkdtemp(prefix=f'run-{stamp}-', dir=args.output_root))
    video = output / 'video-health.json'
    benchmark = output / f'capture-{args.instances}.json'
    verification = output / 'hardware-evidence.json'
    python = sys.executable
    commands = [
        ('video', [python, str(ROOT / 'tools/diagnose_capture.py'), '--seconds', '120',
                   '--min-video-fps', '20', '--max-preview-gap', '3', '--output', str(video)], 175),
        ('benchmark', [python, str(ROOT / 'tools/benchmark_mumu.py'), '--instances', str(args.instances),
                       '--seconds', '120', '--poll', '0.9', '--min-fps', '0.8',
                       '--output', str(benchmark)], 215),
        ('verify', [python, str(ROOT / 'tools/verify_hardware_evidence.py'),
                    '--video', str(video), '--benchmark', str(benchmark),
                    '--instances', str(args.instances), '--seconds', '120', '--min-video-fps', '20',
                    '--max-preview-gap', '3', '--max-video-gap', '6',
                    '--min-capture-fps', '0.8', '--output', str(verification)], 30),
    ]
    if args.serial:
        commands[0][1].extend(['--serial', args.serial])
    if args.workflow:
        commands[1][1].extend(['--workflow', str(args.workflow.resolve())])

    manifest = {
        'started_utc': datetime.now(timezone.utc).isoformat(),
        'read_only': True,
        'instances_requested': args.instances,
        'projection_target_instances': 10,
        'ten_instance_operation_verified': False,
        'hardware_acceptance_complete': False,
        'live_game_gestures_and_rotation_verified': False,
        'branch_protection_verified': False,
        'steps': {},
        'capacity_estimate': None,
    }
    try:
        for name, command, timeout in commands:
            print(f'Running {name} (read-only); logs: {output / (name + ".log")}', flush=True)
            report = {'video': video, 'benchmark': benchmark, 'verify': verification}[name]
            manifest['steps'][name] = run_step(name, command, output, timeout, report)
            (output / 'run-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n',
                                                      encoding='utf-8')
    except KeyboardInterrupt:
        manifest['interrupted'] = True
    finally:
        manifest['finished_utc'] = datetime.now(timezone.utc).isoformat()
        if args.instances == 1 and manifest['steps'].get('benchmark', {}).get('passed'):
            manifest['capacity_estimate'] = estimate_ten_instance_capture(benchmark)
        manifest['ten_instance_operation_verified'] = (
            args.instances == 10 and manifest['steps'].get('benchmark', {}).get('passed', False))
        manifest['passed_read_only_checks'] = (len(manifest['steps']) == 3 and
                                               all(s['passed'] for s in manifest['steps'].values()))
        (output / 'run-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n',
                                                  encoding='utf-8')
    print(f'Evidence folder: {output}', flush=True)
    return 0 if manifest['passed_read_only_checks'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
