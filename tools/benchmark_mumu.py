"""Read-only MuMu capture and optional workflow-recognition load test.

Examples (in the OmniFlow virtualenv, with emulators already running)::

    python tools/benchmark_mumu.py --instances 10 --seconds 60 --output mumu-load.json
    python tools/benchmark_mumu.py --instances 10 --seconds 60 --workflow path/to/workflow
    python tools/benchmark_mumu.py --instances 10 --seconds 60 --min-fps 0.8

Uses one ADB screenshot per device per poll, never starts a second encoder,
launches an app, or sends input. Video FPS requires diagnose_capture.py.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2

from omniflow.devices import Device, discover_mumu
from swordx.detection import normalize


@dataclass(frozen=True)
class DeviceResult:
    serial: str
    frames: int
    errors: int
    elapsed_seconds: float
    effective_fps: float
    capture_mean_ms: float | None
    capture_p95_ms: float | None
    detection_p95_ms: float | None
    last_error: str = ''


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank sample percentile without a statistics dependency."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def measure(serial: str, seconds: float, poll_seconds: float,
            barrier: threading.Barrier | None = None, *, device_factory=Device,
            detector_factory: Callable | None = None,
            clock: Callable[[], float] = time.monotonic,
            wait: Callable[[float], None] = time.sleep) -> DeviceResult:
    """Capture on one explicitly selected device; never issue Android input.

    All workers synchronize *after* connecting to avoid optimistic results
    from sequentially sampled emulators. Connection failure is reported as an
    error, but still reaches the barrier so healthy workers cannot deadlock.
    """
    device = None
    detector = None
    problem = ''
    try:
        device = device_factory(serial=serial)
        if hasattr(device, 'ensure_connected'):
            device.ensure_connected()
        detector = detector_factory() if detector_factory else None
    except Exception as exc:
        problem = f'{type(exc).__name__}: {exc}'
    if barrier is not None:
        try:
            barrier.wait(timeout=30)
        except threading.BrokenBarrierError:
            return DeviceResult(serial, 0, 1, 0.0, 0.0, None, None, None,
                                'Another emulator did not reach the start barrier')
    if problem:
        return DeviceResult(serial, 0, 1, 0.0, 0.0, None, None, None, problem)

    started = clock()
    deadline = started + seconds
    capture_times: list[float] = []
    detect_times: list[float] = []
    errors = 0
    consecutive_errors = 0
    last_error = ''
    next_poll = started
    while clock() < deadline:
        next_poll = max(next_poll, clock())
        sample_started = clock()
        try:
            frame = normalize(device.screenshot())
            if frame.shape != (960, 540, 3):
                raise ValueError(f'Unexpected normalized frame dimensions: {frame.shape}')
            acquired = clock()
            if detector is not None:
                detector.detect(frame)
                detect_times.append(clock() - acquired)
            capture_times.append(acquired - sample_started)
            consecutive_errors = 0
        except Exception as exc:
            errors += 1
            consecutive_errors += 1
            last_error = f'{type(exc).__name__}: {exc}'
            if consecutive_errors >= 5:
                break
        next_poll += poll_seconds
        remaining = min(deadline, next_poll) - clock()
        if remaining > 0:
            wait(remaining)
    elapsed = max(0.0, clock() - started)
    frames = len(capture_times)
    return DeviceResult(
        serial=serial, frames=frames, errors=errors,
        elapsed_seconds=round(elapsed, 3),
        effective_fps=round(frames / elapsed, 3) if elapsed > 0 else 0.0,
        capture_mean_ms=round(statistics.mean(capture_times) * 1000, 2) if frames else None,
        capture_p95_ms=round(percentile(capture_times, 0.95) * 1000, 2) if frames else None,
        detection_p95_ms=round(percentile(detect_times, 0.95) * 1000, 2) if detect_times else None,
        last_error=last_error,
    )


def evaluate(results: list[DeviceResult], requested: int, min_fps: float) -> list[str]:
    """Fail closed if requested emulator coverage or frame reliability is missing."""
    failures = []
    if len(results) != requested:
        failures.append(f'Expected {requested} emulator(s), tested {len(results)}')
    if len({result.serial for result in results}) != len(results):
        failures.append('Duplicate ADB serials detected')
    for result in results:
        if result.frames == 0:
            failures.append(f'{result.serial}: no valid screenshots')
        if result.errors:
            failures.append(f'{result.serial}: {result.errors} capture/detector error(s)')
        if min_fps and result.effective_fps < min_fps:
            failures.append(f'{result.serial}: {result.effective_fps:.2f} FPS below {min_fps:.2f} minimum')
    return failures


def write_json(path: Path, payload: dict) -> None:
    """Replace the report atomically so interrupted benchmarks leave no partial JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         prefix=f'.{path.name}.', suffix='.tmp', delete=False) as stream:
            temp_name = stream.name
            json.dump(payload, stream, indent=2)
            stream.write('\n')
        os.replace(temp_name, path)
    finally:
        if temp_name is not None and os.path.exists(temp_name):
            os.unlink(temp_name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instances', type=int, default=10, help='Exact number of required devices (1–10)')
    parser.add_argument('--seconds', type=int, default=30, help='Read-only test duration (3–300)')
    parser.add_argument('--poll', type=float, default=0.9, help='Per-device capture period in seconds (>= 0.35)')
    parser.add_argument('--workflow', type=Path, help='Optional workflow folder for actual recognition load')
    parser.add_argument('--min-fps', type=float, default=0.0, help='Optional per-device minimum effective FPS')
    parser.add_argument('--output', type=Path, help='Write detailed JSON report')
    args = parser.parse_args(argv)
    if not 1 <= args.instances <= 10 or not 3 <= args.seconds <= 300:
        parser.error('--instances must be 1–10; --seconds must be 3–300')
    if not math.isfinite(args.poll) or args.poll < 0.35:
        parser.error('--poll must be a finite number >= 0.35 seconds')
    if not math.isfinite(args.min_fps) or args.min_fps < 0:
        parser.error('--min-fps must be a nonnegative finite number')

    detector_factory = None
    if args.workflow:
        from omniflow.engine import OmniDetector
        folder = args.workflow
        if not (folder / 'manifest.json').is_file():
            parser.error(f'Workflow manifest not found: {folder}')
        try:
            OmniDetector(folder)  # Fail early on broken workflow assets.
        except Exception as exc:
            parser.error(f'Workflow cannot be loaded: {exc}')
        detector_factory = lambda: OmniDetector(folder)

    cv2.setNumThreads(1)
    try:
        discovered = discover_mumu()
    except Exception as exc:
        print(f'MuMu discovery failed: {exc}', file=sys.stderr)
        return 1
    serials = list(dict.fromkeys(discovered))[:args.instances]
    if len(serials) != args.instances:
        print(f'Found {len(serials)} of {args.instances} required MuMu devices; start all instances first.',
              file=sys.stderr)
        return 1
    print(f'Benchmarking {len(serials)} emulator(s) for {args.seconds}s; '
          f'poll={args.poll}s; workflow={args.workflow or "none"}; read-only.', flush=True)
    barrier = threading.Barrier(len(serials)) if len(serials) > 1 else None
    with ThreadPoolExecutor(max_workers=len(serials)) as pool:
        futures = [pool.submit(measure, serial, args.seconds, args.poll, barrier,
                               detector_factory=detector_factory) for serial in serials]
        results = [future.result() for future in futures]

    for result in results:
        print(f'{result.serial:<23} frames={result.frames:>4} '
              f'fps={result.effective_fps:>5.2f} '
              f'capture_p95={result.capture_p95_ms if result.capture_p95_ms is not None else "—"}ms '
              f'detect_p95={result.detection_p95_ms if result.detection_p95_ms is not None else "—"}ms '
              f'errors={result.errors} {result.last_error}')
    failures = evaluate(results, args.instances, args.min_fps)
    report = {
        'requested_instances': args.instances,
        'tested_instances': len(results),
        'duration_seconds': args.seconds,
        'poll_seconds': args.poll,
        'workflow': str(args.workflow) if args.workflow else None,
        'min_fps': args.min_fps,
        'passed': not failures,
        'failures': failures,
        'devices': [asdict(result) for result in results],
    }
    if args.output:
        write_json(args.output, report)
        print(f'JSON report: {args.output}')
    if failures:
        print('Benchmark NOT PASSED: ' + '; '.join(failures), file=sys.stderr)
        return 1
    print('Capture benchmark passed the requested coverage/error criteria. '
          'This does not measure H.264 preview FPS or GPU load.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
