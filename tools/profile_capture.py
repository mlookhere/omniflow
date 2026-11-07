"""Read-only MuMu screenshot profile: split ADB transport, PNG decode and resize."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np

from omniflow.devices import Device, canonical_mumu_serial, discover_mumu
from omniflow.raw_capture import decode_raw_screencap
from swordx.adb import ADB
from swordx.detection import normalize
from tools.benchmark_mumu import percentile, write_json


def capture_frame(device: Device, *, capture_format: str = 'png',
                  clock: Callable[[], float] = time.monotonic) -> tuple[np.ndarray, dict]:
    """Return a normalized screenshot and its capture timing metadata."""
    if capture_format not in ('png', 'raw'):
        raise ValueError(f'Unsupported capture format: {capture_format}')
    started = clock()
    args = ('exec-out', 'screencap', '-p') if capture_format == 'png' else ('exec-out', 'screencap')
    payload = device._run(*args, timeout=15)
    transferred = clock()
    frame = (cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
             if capture_format == 'png' else decode_raw_screencap(payload))
    decoded = clock()
    if frame is None:
        raise ValueError('ADB returned an empty or unreadable screenshot')
    image = normalize(frame)
    finished = clock()
    if image.shape != (960, 540, 3):
        raise ValueError(f'Unexpected normalized dimensions: {image.shape}')
    sample = {
        'adb_exec_ms': (transferred - started) * 1000,
        'decode_ms': (decoded - transferred) * 1000,
        f'{capture_format}_decode_ms': (decoded - transferred) * 1000,
        'normalize_ms': (finished - decoded) * 1000,
        'total_ms': (finished - started) * 1000,
        'payload_bytes': len(payload),
        'source_shape': list(frame.shape),
    }
    if capture_format == 'png':
        sample['png_bytes'] = len(payload)  # Preserve existing profiler sample schema.
    return image, sample


def capture_sample(device: Device, *, capture_format: str = 'png',
                   clock: Callable[[], float] = time.monotonic) -> dict:
    """Retain the existing timing-only API for profiling callers."""
    _, sample = capture_frame(device, capture_format=capture_format, clock=clock)
    return sample


def profile(serial: str, seconds: float, poll: float, min_fps: float,
            *, capture_format: str = 'png', device_factory=Device,
            clock: Callable[[], float] = time.monotonic,
            wait: Callable[[float], None] = time.sleep) -> dict:
    if capture_format not in ('png', 'raw'):
        raise ValueError(f'Unsupported capture format: {capture_format}')
    device = device_factory(serial=serial)
    device.ensure_connected()
    started = clock()
    deadline = started + seconds
    next_poll = started
    samples: list[dict] = []
    errors = consecutive = 0
    last_error = ''
    while clock() < deadline:
        try:
            samples.append(capture_sample(device, capture_format=capture_format, clock=clock))
            consecutive = 0
        except Exception as exc:
            errors += 1
            consecutive += 1
            last_error = f'{type(exc).__name__}: {exc}'
            if consecutive >= 5:
                break
        next_poll += poll
        remaining = min(deadline, next_poll) - clock()
        if remaining > 0:
            wait(remaining)
    elapsed = max(0.0, clock() - started)
    fps = len(samples) / elapsed if elapsed else 0.0
    timings = {}
    for key in ('adb_exec_ms', 'decode_ms', f'{capture_format}_decode_ms',
                'normalize_ms', 'total_ms'):
        values = [sample[key] for sample in samples]
        timings[key] = {
            'mean': round(statistics.mean(values), 2) if values else None,
            'p95': round(percentile(values, .95), 2) if values else None,
        }
    failures = []
    if not samples:
        failures.append('No valid PNG screenshots received')
    if errors:
        failures.append(f'{errors} screenshot/decode errors: {last_error}')
    if elapsed < seconds * .95:
        failures.append(f'Measured {elapsed:.1f}s, expected at least {seconds * .95:.1f}s')
    if fps < min_fps:
        failures.append(f'Capture {fps:.3f} FPS below {min_fps:.3f} FPS target')
    return {
        'serial': serial,
        'read_only': True,
        'capture_format': capture_format,
        'capture_source': ('ADB exec-out screencap -p (PNG)' if capture_format == 'png'
                           else 'ADB exec-out screencap (raw framebuffer)'),
        'requested_seconds': seconds,
        'elapsed_seconds': round(elapsed, 3),
        'poll_seconds': poll,
        'min_fps': min_fps,
        'frames': len(samples),
        'errors': errors,
        'effective_fps': round(fps, 3),
        'payload_bytes_mean': round(statistics.mean(s['payload_bytes'] for s in samples))
                              if samples else None,
        'png_bytes_mean': (round(statistics.mean(s['payload_bytes'] for s in samples))
                           if samples and capture_format == 'png' else None),
        'timings_ms': timings,
        'passed': not failures,
        'failures': failures,
        'last_error': last_error,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', default='', help='MuMu serial or first discovered')
    parser.add_argument('--seconds', type=float, default=120)
    parser.add_argument('--poll', type=float, default=.9)
    parser.add_argument('--min-fps', type=float, default=.8)
    parser.add_argument('--capture-format', choices=('png', 'raw'), default='png',
                        help='PNG baseline or opt-in raw framebuffer measurement')
    parser.add_argument('--output', type=Path, help='Optional atomic JSON output')
    args = parser.parse_args(argv)
    if not math.isfinite(args.seconds) or not 3 <= args.seconds <= 300:
        parser.error('--seconds must be finite between 3 and 300')
    if not math.isfinite(args.poll) or args.poll < .35:
        parser.error('--poll must be finite and >=0.35')
    if not math.isfinite(args.min_fps) or args.min_fps < 0:
        parser.error('--min-fps must be finite and >=0')
    failure = {'passed': False, 'read_only': True, 'serial': args.serial,
               'capture_format': args.capture_format,
               'frames': 0, 'errors': 0, 'failures': []}

    def fail(message: str) -> int:
        failure['failures'] = [message]
        if args.output:
            write_json(args.output, failure)
        print(message, file=sys.stderr)
        return 1

    if args.serial and not ADB.is_mumu_serial(args.serial):
        return fail('Requested serial is not a MuMu address')
    try:
        available = discover_mumu()
    except Exception as exc:
        return fail(f'MuMu discovery failed: {type(exc).__name__}: {exc}')
    selected = next((serial for serial in available if not args.serial or
                     canonical_mumu_serial(serial) ==
                     canonical_mumu_serial(args.serial)), '')
    if not selected:
        return fail('Requested MuMu is not connected')
    cv2.setNumThreads(1)
    try:
        report = profile(selected, args.seconds, args.poll, args.min_fps,
                         capture_format=args.capture_format)
    except Exception as exc:
        return fail(f'Profiler failed: {type(exc).__name__}: {exc}')
    if args.output:
        write_json(args.output, report)
        print(f'JSON report: {args.output}')
    print(json.dumps({'capture_format': args.capture_format,
                      'passed': report['passed'],
                      'effective_fps': report['effective_fps'],
                      'timings_ms': report['timings_ms'],
                      'failures': report['failures']}, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
