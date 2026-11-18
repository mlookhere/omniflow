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
from concurrent.futures import ThreadPoolExecutor
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from omniflow.devices import MAX_CAPTURE_AGE_SECONDS


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
        if name == 'assess':
            valid = (valid and evidence.get('comparison_complete') is True and
                     evidence.get('hardware_acceptance_complete') is False)
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
        # A reported pass is necessary but not enough even for an idealized
        # capacity estimate. Validate actual PNG provenance and measurements.
        if (report.get('passed') is not True or
                report.get('hardware_acceptance_complete') is not False or
                report.get('capture_format') != 'png' or
                type(report.get('adb_timeout_seconds')) not in (int, float) or
                not 0 < report['adb_timeout_seconds'] <= MAX_CAPTURE_AGE_SECONDS or
                report.get('requested_instances') != 1 or
                report.get('tested_instances') != 1 or
                report.get('failures') != []):
            return None
        devices = report.get('devices')
        if not isinstance(devices, list) or len(devices) != 1 or not isinstance(devices[0], dict):
            return None
        device = devices[0]
        duration, minimum = report.get('duration_seconds'), report.get('min_fps')
        elapsed, rate = device.get('elapsed_seconds'), device.get('effective_fps')
        gap, p95 = device.get('max_frame_gap_seconds'), device.get('capture_p95_ms')
        frames, errors = device.get('frames'), device.get('errors')
        if not all(type(value) in (int, float) and math.isfinite(value)
                   for value in (duration, minimum, elapsed, rate, gap, p95)):
            return None
        if (duration < 120 or minimum < .8 or elapsed < duration or
                rate < minimum or gap < 0 or gap > 6 or gap > elapsed or
                p95 < 0 or type(frames) is not int or frames <= 0 or
                type(errors) is not int or errors != 0 or
                not isinstance(device.get('serial'), str) or
                not device['serial'].strip() or
                abs(frames / elapsed - rate) > .015):
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
    parser.add_argument('--serial', default='', help='Target MuMu serial for video and benchmark')
    parser.add_argument('--capture-comparison', action='store_true',
                        help='Instead collect PNG/raw profiles and stationary image fidelity (one device)')
    parser.add_argument('--workflow', type=Path, help='Representative workflow for recognition benchmarking')
    parser.add_argument('--output-root', type=Path, default=ROOT / 'hardware-evidence',
                        help='Parent of unique per-run evidence folders (never reused)')
    args = parser.parse_args(argv)
    if args.workflow and not (args.workflow / 'manifest.json').is_file():
        parser.error('workflow must contain manifest.json')
    if args.capture_comparison and (not args.serial or args.instances != 1):
        parser.error('--capture-comparison requires --serial and --instances 1')
    if args.capture_comparison and args.workflow:
        parser.error('--capture-comparison measures capture only; omit --workflow')

    args.output_root = args.output_root.resolve()
    args.output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = Path(tempfile.mkdtemp(prefix=f'run-{stamp}-', dir=args.output_root))
    video = output / 'video-health.json'
    benchmark = output / f'capture-{args.instances}.json'
    verification = output / 'hardware-evidence.json'
    python = sys.executable
    if args.capture_comparison:
        png = output / 'capture-profile.png.json'
        raw = output / 'capture-profile.raw.json'
        fidelity = output / 'capture-fidelity.json'
        assessment = output / 'capture-assessment.json'
        commands = [
            ('profile_png', [python, str(ROOT / 'tools/profile_capture.py'),
                             '--serial', args.serial, '--capture-format', 'png',
                             '--seconds', '120', '--poll', '0.9', '--min-fps', '0.8',
                             '--output', str(png)], 215),
            ('profile_raw', [python, str(ROOT / 'tools/profile_capture.py'),
                             '--serial', args.serial, '--capture-format', 'raw',
                             '--seconds', '120', '--poll', '0.9', '--min-fps', '0.8',
                             '--output', str(raw)], 215),
            ('fidelity', [python, str(ROOT / 'tools/compare_capture.py'),
                          '--serial', args.serial, '--triplets', '3',
                          '--output', str(fidelity)], 180),
            ('assess', [python, str(ROOT / 'tools/assess_capture.py'),
                        '--png', str(png), '--raw', str(raw), '--fidelity', str(fidelity),
                        '--output', str(assessment)], 30),
        ]
    else:
        commands = [
            # Run both real diagnostics at the same time on THIS host. The
            # video starts first, but both subprocesses launch concurrently;
            # 150s and 135s allow <=15s startup skew while preserving >=120s
            # of overlapping measured evidence.
            ('video', [python, str(ROOT / 'tools/diagnose_capture.py'), '--seconds', '150',
                       '--min-video-fps', '20', '--max-preview-gap', '3',
                       '--capture-worker-active',
                       '--output', str(video)], 210),
            ('benchmark', [python, str(ROOT / 'tools/benchmark_mumu.py'),
                           '--instances', str(args.instances), '--seconds', '135',
                           '--poll', '0.9', '--min-fps', '0.8',
                           '--output', str(benchmark)], 215),
            ('verify', [python, str(ROOT / 'tools/verify_hardware_evidence.py'),
                        '--video', str(video), '--benchmark', str(benchmark),
                        '--instances', str(args.instances), '--seconds', '120',
                        '--min-video-fps', '20', '--max-preview-gap', '3',
                        '--max-video-gap', '6', '--min-capture-fps', '0.8',
                        '--output', str(verification)], 30),
        ]
        if args.serial:
            commands[0][1].extend(['--serial', args.serial])
            commands[1][1].extend(['--serial', args.serial])
        if args.workflow:
            commands[1][1].extend(['--workflow', str(args.workflow.resolve())])

    manifest = {
        'started_utc': datetime.now(timezone.utc).isoformat(),
        'read_only': True,
        'instances_requested': args.instances,
        'requested_serial': args.serial or None,
        'capture_comparison_requested': args.capture_comparison,
        'capture_comparison_complete': False,
        'raw_candidate_for_further_validation': False,
        'projection_target_instances': 10,
        'ten_instance_operation_verified': False,
        'ten_instance_capture_benchmark_passed': False,
        'hardware_acceptance_complete': False,
        'live_game_gestures_and_rotation_verified': False,
        'branch_protection_verified': False,
        'steps': {},
        'capacity_estimate': None,
    }
    try:
        if not args.capture_comparison:
            # The independent verifier now requires 120s of *common*
            # video/PNG overlap. Sequential execution can never satisfy it.
            # Threads only supervise subprocesses; each diagnostic retains
            # its independent timeout, stdout, report and failure accounting.
            with ThreadPoolExecutor(max_workers=2) as workers:
                pending = []
                for name, command, timeout in commands[:2]:
                    print(f'Running {name} (read-only); logs: '
                          f'{output / (name + ".log")}', flush=True)
                    report = Path(command[command.index('--output') + 1])
                    pending.append((name, workers.submit(
                        run_step, name, command, output, timeout, report)))
                for name, task in pending:
                    manifest['steps'][name] = task.result()
                    (output / 'run-manifest.json').write_text(
                        json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
            commands = commands[2:]  # Verify only after both sources finish.
        for name, command, timeout in commands:
            print(f'Running {name} (read-only); logs: {output / (name + ".log")}', flush=True)
            report = Path(command[command.index('--output') + 1])
            manifest['steps'][name] = run_step(name, command, output, timeout, report)
            (output / 'run-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n',
                                                      encoding='utf-8')
    except KeyboardInterrupt:
        manifest['interrupted'] = True
    finally:
        manifest['finished_utc'] = datetime.now(timezone.utc).isoformat()
        if args.instances == 1 and manifest['steps'].get('benchmark', {}).get('passed'):
            manifest['capacity_estimate'] = estimate_ten_instance_capture(benchmark)
        manifest['ten_instance_capture_benchmark_passed'] = (
            args.instances == 10 and manifest['steps'].get('benchmark', {}).get('passed', False))
        if args.capture_comparison:
            # Sub-threshold PNG is meaningful baseline evidence. Only the
            # independent assessor can determine whether those failed profiles
            # still form a complete and internally consistent comparison.
            assess_step = manifest['steps'].get('assess', {})
            sources_present = all((output / name).is_file() for name in (
                'capture-profile.png.json', 'capture-profile.raw.json',
                'capture-fidelity.json'))
            manifest['capture_comparison_complete'] = (
                len(manifest['steps']) == 4 and sources_present and
                assess_step.get('passed') is True and not manifest.get('interrupted', False))
            if manifest['capture_comparison_complete']:
                try:
                    result = json.loads((output / 'capture-assessment.json').read_text(
                        encoding='utf-8'))
                    manifest['raw_candidate_for_further_validation'] = (
                        result.get('raw_candidate_for_further_validation') is True)
                except (OSError, ValueError, AttributeError):
                    manifest['capture_comparison_complete'] = False
            # A standalone capture-format comparison NEVER validates the
            # normal video/benchmark hardware acceptance workflow.
            manifest['passed_read_only_checks'] = False
        else:
            manifest['passed_read_only_checks'] = (
                len(manifest['steps']) == 3 and
                all(s['passed'] for s in manifest['steps'].values()) and
                not manifest.get('interrupted', False))
        (output / 'run-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n',
                                                  encoding='utf-8')
    print(f'Evidence folder: {output}', flush=True)
    return 0 if (manifest['capture_comparison_complete'] if args.capture_comparison
                 else manifest['passed_read_only_checks']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
