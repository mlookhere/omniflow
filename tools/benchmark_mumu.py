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

from omniflow.devices import (MAX_CAPTURE_AGE_SECONDS, Device,
                              canonical_mumu_serial, discover_mumu)
from swordx.adb import ADB
from omniflow.resources import HostResourceMonitor
from omniflow.raw_capture import decode_raw_screencap
from swordx.detection import normalize


MAX_FRAME_GAP_SECONDS = 6.0  # Do not accept silent screenshot stalls.


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
    max_frame_gap_seconds: float | None = None
    started_at_unix: float | None = None
    ended_at_unix: float | None = None
    valid_frame_times_unix: list[float] | None = None


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank sample percentile without a statistics dependency."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def measure(serial: str, seconds: float, poll_seconds: float,
            barrier: threading.Barrier | None = None, *, device_factory=Device,
            detector_factory: Callable | None = None,
            capture_format: str = 'png',
            clock: Callable[[], float] = time.monotonic,
            wait: Callable[[float], None] = time.sleep) -> DeviceResult:
    """Capture on one explicitly selected device; never issue Android input.

    All workers synchronize *after* connecting to avoid optimistic results
    from sequentially sampled emulators. Connection failure is reported as an
    error, but still reaches the barrier so healthy workers cannot deadlock.
    """
    if capture_format not in ('png', 'raw'):
        raise ValueError('Capture format must be png or raw')
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

    # UTC Unix time is comparable with the separate H.264 diagnostic; the
    # monotonic clock still owns duration, deadlines and throughput.
    started_at_unix = time.time()
    started = clock()
    deadline = started + seconds
    capture_times: list[float] = []
    valid_frame_times_unix: list[float] = []
    detect_times: list[float] = []
    source_shape: tuple[int, int, int] | None = None
    last_valid_at = started
    max_frame_gap = 0.0
    errors = 0
    consecutive_errors = 0
    last_error = ''
    next_poll = started
    while clock() < deadline:
        next_poll = max(next_poll, clock())
        sample_started = clock()
        try:
            if capture_format == 'raw':
                payload = device._run('exec-out', 'screencap', timeout=15)
                frame = decode_raw_screencap(payload)
            else:
                frame = device.screenshot()
            # Comparing normalized dimensions alone hides a live rotation:
            # portrait and landscape both normalize to 540x960.
            shape = tuple(frame.shape)
            if source_shape is None:
                source_shape = shape
            elif shape != source_shape:
                raise ValueError(
                    f'Screenshot source geometry changed: {source_shape} -> {shape}')
            frame = normalize(frame)
            if frame.shape != (960, 540, 3):
                raise ValueError(f'Unexpected normalized frame dimensions: {frame.shape}')
            acquired = clock()
            if detector is not None:
                detector.detect(frame)
                detect_times.append(clock() - acquired)
            # A frame is useful only after optional recognition succeeds.
            # Include OCR stalls, not just the ADB read time, in continuity.
            valid_at = clock()
            max_frame_gap = max(max_frame_gap, valid_at - last_valid_at)
            last_valid_at = valid_at
            capture_times.append(acquired - sample_started)
            # Use the same monotonic clock as FPS/continuity, anchored to
            # Unix time once. No extra ADB calls or per-frame wall-clock reads.
            valid_frame_times_unix.append(started_at_unix + valid_at - started)
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
    finished = clock()
    elapsed = max(0.0, finished - started)
    # FPS and duration must use the same reported precision. Otherwise,
    # short concurrent runs can disagree solely because elapsed is rounded.
    reported_elapsed = round(elapsed, 3)
    # Frame timestamps use this same monotonic delta. A second wall-clock
    # sample can lag behind a valid final frame on Windows (QPC vs system
    # wall time), producing impossible-looking evidence and flaky tests.
    ended_at_unix = started_at_unix + elapsed
    max_frame_gap = max(max_frame_gap, finished - last_valid_at)
    frames = len(capture_times)
    return DeviceResult(
        serial=serial, frames=frames, errors=errors,
        elapsed_seconds=reported_elapsed,
        effective_fps=(round(frames / reported_elapsed, 3)
                       if reported_elapsed > 0 else 0.0),
        capture_mean_ms=round(statistics.mean(capture_times) * 1000, 2) if frames else None,
        capture_p95_ms=round(percentile(capture_times, 0.95) * 1000, 2) if frames else None,
        detection_p95_ms=round(percentile(detect_times, 0.95) * 1000, 2) if detect_times else None,
        last_error=last_error,
        max_frame_gap_seconds=round(max_frame_gap, 3),
        started_at_unix=started_at_unix,
        ended_at_unix=ended_at_unix,
        valid_frame_times_unix=valid_frame_times_unix,
    )


def evaluate(results: list[DeviceResult], requested: int, min_fps: float,
             *, minimum_seconds: float = 0.0) -> list[str]:
    """Fail closed if requested emulator coverage or frame reliability is missing."""
    failures = []
    if len(results) != requested:
        failures.append(f'Expected {requested} emulator(s), tested {len(results)}')
    if len({result.serial for result in results}) != len(results):
        failures.append('Duplicate ADB serials detected')
    for result in results:
        elapsed, frames, errors, fps = (result.elapsed_seconds, result.frames,
                                       result.errors, result.effective_fps)
        if (type(elapsed) not in (int, float) or not math.isfinite(elapsed) or
                elapsed <= 0):
            failures.append(f'{result.serial}: invalid measured duration')
        elif elapsed < minimum_seconds:
            failures.append(
                f'{result.serial}: measured {elapsed:.3f}s; '
                f'required at least {minimum_seconds:.3f}s')
        if type(frames) is not int or frames < 0:
            failures.append(f'{result.serial}: invalid whole-number screenshot count')
        elif frames == 0:
            failures.append(f'{result.serial}: no valid screenshots')
        if type(errors) is not int or errors < 0:
            failures.append(f'{result.serial}: invalid whole-number error count')
        elif errors:
            failures.append(f'{result.serial}: {errors} capture/detector error(s)')
        if type(fps) not in (int, float) or not math.isfinite(fps) or fps < 0:
            failures.append(f'{result.serial}: invalid capture FPS')
        else:
            if (type(elapsed) in (int, float) and math.isfinite(elapsed) and
                    elapsed > 0 and type(frames) is int and frames >= 0 and
                    abs(frames / elapsed - fps) > 0.06):
                failures.append(f'{result.serial}: frame count and FPS disagree')
            if min_fps and fps < min_fps:
                failures.append(f'{result.serial}: {fps:.2f} FPS below {min_fps:.2f} minimum')
        gap = result.max_frame_gap_seconds
        if (type(gap) not in (int, float) or
                not math.isfinite(gap) or gap < 0):
            failures.append(f'{result.serial}: maximum valid-frame gap missing or invalid')
        elif gap > MAX_FRAME_GAP_SECONDS:
            failures.append(
                f'{result.serial}: screenshot gap {gap:.3f}s exceeds '
                f'{MAX_FRAME_GAP_SECONDS:.3f}s')
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
    parser.add_argument('--serial', default='', help='MuMu device to include first; fail if absent')
    parser.add_argument('--capture-format', choices=('png', 'raw'), default='png',
                        help='Read-only screenshot transport; raw is diagnostic-only')
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

    report = {
        'requested_instances': args.instances,
        'requested_serial': args.serial or None,
        'capture_format': args.capture_format,
        'adb_timeout_seconds': (MAX_CAPTURE_AGE_SECONDS if args.capture_format == 'png'
                                else 15.0),
        'hardware_acceptance_complete': False,
        'tested_instances': 0,
        'discovered_instances': 0,
        'duration_seconds': args.seconds,
        'poll_seconds': args.poll,
        'workflow': str(args.workflow) if args.workflow else None,
        'min_fps': args.min_fps,
        'passed': False,
        'failures': [],
        'devices': [],
        'host_resources': None,
    }

    def fail_before_measurement(message: str) -> int:
        report['failures'] = [message]
        if args.output:
            write_json(args.output, report)
            print(f'JSON report: {args.output}')
        print(message, file=sys.stderr)
        return 1

    if args.serial and not ADB.is_mumu_serial(args.serial):
        return fail_before_measurement('Requested serial is not a supported MuMu ADB address')

    cv2.setNumThreads(1)
    try:
        discovered = discover_mumu()
    except Exception as exc:
        return fail_before_measurement(f'MuMu discovery failed: {exc}')
    # ADB may report both localhost and 127.0.0.1 for the same MuMu port.
    # Preserve the actual -s spelling but never count/capture an alias twice.
    seen = {}
    for serial in discovered:
        seen.setdefault(canonical_mumu_serial(serial), serial)
    unique_serials = list(seen.values())
    report['discovered_instances'] = len(unique_serials)
    if args.serial:
        requested = canonical_mumu_serial(args.serial)
        selected = seen.get(requested)
        if selected is None:
            return fail_before_measurement(
                f'Requested MuMu device {args.serial} was not discovered; no substitutes accepted')
        unique_serials.remove(selected)
        unique_serials.insert(0, selected)
    serials = unique_serials[:args.instances]
    if len(serials) != args.instances:
        return fail_before_measurement(
            f'Found {len(serials)} of {args.instances} required MuMu devices; start all instances first.')
    print(f'Benchmarking {len(serials)} emulator(s) for {args.seconds}s; '
          f'poll={args.poll}s; format={args.capture_format}; '
          f'workflow={args.workflow or "none"}; read-only.', flush=True)
    barrier = threading.Barrier(len(serials)) if len(serials) > 1 else None
    try:
        monitor = HostResourceMonitor()
        monitor.start()
    except Exception as exc:
        return fail_before_measurement(
            f'Host telemetry could not start: {type(exc).__name__}: {exc}')

    results: list[DeviceResult] = []
    setup_error = ''
    resources = None
    try:
        with ThreadPoolExecutor(max_workers=len(serials)) as pool:
            futures = [pool.submit(measure, serial, args.seconds, args.poll, barrier,
                                   detector_factory=detector_factory,
                                   capture_format=args.capture_format) for serial in serials]
            for serial, future in zip(serials, futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    # A crashed worker still needs a failing device result so
                    # the completed workers and failure JSON are not lost.
                    results.append(DeviceResult(
                        serial, 0, 1, 0.0, 0.0, None, None, None,
                        f'Unexpected worker failure: {type(exc).__name__}: {exc}'))
    except Exception as exc:
        setup_error = f'Capture worker pool failed: {type(exc).__name__}: {exc}'
    finally:
        try:
            resources = monitor.stop()
        except Exception as exc:
            setup_error += (('; ' if setup_error else '') +
                            f'Host telemetry could not stop: {type(exc).__name__}: {exc}')

    for result in results:
        print(f'{result.serial:<23} frames={result.frames:>4} '
              f'fps={result.effective_fps:>5.2f} '
              f'capture_p95={result.capture_p95_ms if result.capture_p95_ms is not None else "—"}ms '
              f'detect_p95={result.detection_p95_ms if result.detection_p95_ms is not None else "—"}ms '
              f'max_gap={result.max_frame_gap_seconds if result.max_frame_gap_seconds is not None else "—"}s '
              f'errors={result.errors} {result.last_error}')
    failures = evaluate(results, args.instances, args.min_fps,
                        minimum_seconds=args.seconds)
    if setup_error:
        failures.append(setup_error)
    report.update(
        tested_instances=len(results),
        passed=not failures,
        failures=failures,
        devices=[asdict(result) for result in results],
        host_resources=resources,
    )
    if args.output:
        write_json(args.output, report)
        print(f'JSON report: {args.output}')
    if failures:
        print('Benchmark NOT PASSED: ' + '; '.join(failures), file=sys.stderr)
        return 1
    print('Capture benchmark passed its requested coverage/error criteria. '
          'Raw mode is diagnostic-only and is not admissible for the normal '
          'hardware acceptance verifier. This does not test H.264 or gestures.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
