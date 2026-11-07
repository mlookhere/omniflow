"""Fail-closed validation of real MuMu capture and ten-emulator benchmark evidence.

Both input reports come from read-only hardware diagnostics. This module never
starts an emulator or interprets a successful CI run as hardware acceptance.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from swordx.adb import ADB

from .devices import MAX_CAPTURE_AGE_SECONDS, canonical_mumu_serial
from .capture_health import evaluate_capture
MAX_FRAME_GAP_SECONDS = 6.0  # Same fixed gate as the read-only benchmark.


@dataclass(frozen=True)
class EvidenceCriteria:
    instances: int = 10
    seconds: float = 120.0
    min_video_fps: float = 20.0
    max_preview_gap: float = 3.0
    max_video_gap: float = 6.0
    min_capture_fps: float = 0.8
    max_error22_recovery: float = 6.0

    def __post_init__(self) -> None:
        if not 1 <= self.instances <= 10:
            raise ValueError('instances must be between 1 and 10')
        for name in ('seconds', 'min_video_fps', 'max_preview_gap',
                     'max_video_gap', 'min_capture_fps', 'max_error22_recovery'):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise ValueError(f'{name} must be finite')
            if (name == 'seconds' and value <= 0) or (name != 'seconds' and value < 0):
                raise ValueError(f'{name} must be positive' if name == 'seconds' else f'{name} must be nonnegative')


def _number(source: Mapping, key: str, label: str, failures: list[str]) -> float:
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        failures.append(f'{label}: missing or invalid nonnegative {key}')
        return 0.0
    return float(value)


def _count(source: Mapping, key: str, label: str, failures: list[str]) -> float:
    """Reject fractional observation totals even when a report claims success."""
    value = _number(source, key, label, failures)
    if not value.is_integer():
        failures.append(f'{label}: {key} must use whole numbers')
    return value


def evaluate_hardware_evidence(video: Mapping, benchmark: Mapping,
                               criteria: EvidenceCriteria = EvidenceCriteria()) -> dict:
    """Verify read-only evidence; never claim untested GPU/gesture behavior.

    The input reports' `passed` flags are *necessary, not sufficient*. Check
    independently measured duration and every emulator's effective FPS so a
    requested 120-second test that exited after 2 seconds cannot pass.
    """
    failures: list[str] = []
    outstanding = [
        'Manual live-game tap/swipe and rotation accuracy not measured',
        'Long-term host resource stability and per-emulator allocation not verified',
        'Same-host hardware identity and measurement timestamps not verified',
    ]
    if video.get('passed') is not True:
        failures.append('Video diagnostic did not report success')
    if benchmark.get('passed') is not True:
        failures.append(f'{criteria.instances}-instance capture benchmark did not report success')
    # Production automation still uses PNG. A successful raw benchmark is
    # performance evidence only, not proof of dynamic-game visual equivalence.
    if benchmark.get('capture_format') != 'png':
        failures.append('Hardware acceptance requires the production PNG capture format')
    timeout = _number(benchmark, 'adb_timeout_seconds', 'benchmark', failures)
    if not 0 < timeout <= MAX_CAPTURE_AGE_SECONDS:
        failures.append(
            'Benchmark PNG ADB timeout must not exceed the '
            f'{MAX_CAPTURE_AGE_SECONDS:g}s production limit')

    elapsed = _number(video, 'elapsed_seconds', 'video', failures)
    fps = _number(video, 'displayed_video_fps', 'video', failures)
    frames = _count(video, 'displayed_video_frames', 'video', failures)
    _count(video, 'fallback_snapshots', 'video', failures)
    preview_gap = _number(video, 'max_preview_gap_seconds', 'video', failures)
    video_gap = _number(video, 'max_video_gap_seconds', 'video', failures)
    error_events = _count(video, 'error22_events', 'video', failures)
    error_windows = _count(video, 'error22_windows', 'video', failures)
    error_recovered = _count(video, 'error22_recovered', 'video', failures)
    error_unresolved = _count(video, 'error22_unresolved', 'video', failures)
    if elapsed < criteria.seconds:
        failures.append(f'Video ran {elapsed:.1f}s, below the {criteria.seconds:g}s minimum')
    if elapsed and abs(frames / elapsed - fps) > 0.06:
        failures.append('Video frame total and reported FPS disagree')
    # Never permit screenshot-only fallback to satisfy the H.264 acceptance gate.
    if not frames:
        failures.append('No actual H.264 video frames observed')
    if fps < criteria.min_video_fps:
        failures.append(f'Video FPS {fps:.2f} below {criteria.min_video_fps:.2f}')
    if preview_gap > criteria.max_preview_gap:
        failures.append(f'Preview blackout {preview_gap:.2f}s exceeds {criteria.max_preview_gap:.2f}s')
    if video_gap > criteria.max_video_gap:
        failures.append(f'H.264 stream stalled {video_gap:.2f}s, exceeding {criteria.max_video_gap:.2f}s')
    # Multiple log messages from the same encoder failure form one recovery
    # window. The number of windows must equal recovered + still pending,
    # rather than comparing raw log counts with recovery counts.
    if any(not count.is_integer() for count in
           (error_events, error_windows, error_recovered, error_unresolved)):
        failures.append('Error 22 counters must be whole numbers')
    if error_windows > error_events:
        failures.append('Error 22 recovery windows exceed logged Error 22 events')
    if error_recovered + error_unresolved != error_windows:
        failures.append('Error 22 recovery windows are not fully reconciled')
    if error_unresolved > 1:
        failures.append('Error 22 can have at most one unresolved recovery window')
    if error_unresolved:
        failures.append('Unresolved Error 22 event remains')
    recovery_seconds = video.get('max_error22_recovery_seconds')
    if error_recovered:
        if recovery_seconds is None:
            failures.append('Recovered Error 22 lacks measured recovery duration')
        elif _number(video, 'max_error22_recovery_seconds', 'video', failures) > criteria.max_error22_recovery:
            failures.append(f'Error 22 recovery exceeded {criteria.max_error22_recovery:g}s')
    # Reuse the underlying diagnostic's safety rules; do not rely on its pass flag.
    if all(isinstance(video.get(k), (int, float)) for k in
           ('displayed_video_frames', 'fallback_snapshots', 'displayed_video_fps',
            'max_preview_gap_seconds', 'error22_unresolved')):
        failures.extend(evaluate_capture(video, min_video_fps=criteria.min_video_fps,
                                         max_preview_gap=criteria.max_preview_gap))
    recovery_observed = bool(error_events and error_recovered and not error_unresolved)
    if not recovery_observed:
        outstanding.append('Recovery from a real Error 22 event has not been demonstrated')

    requested = _count(benchmark, 'requested_instances', 'benchmark', failures)
    tested = _count(benchmark, 'tested_instances', 'benchmark', failures)
    benchmark_duration = _number(benchmark, 'duration_seconds', 'benchmark', failures)
    if requested != criteria.instances:
        failures.append(f'Benchmark requested {requested:g}, expected {criteria.instances}')
    if tested != criteria.instances:
        failures.append(f'Benchmark tested {tested:g}, expected {criteria.instances}')
    if benchmark_duration < criteria.seconds:
        failures.append(f'Benchmark requested duration {benchmark_duration:g}s is below {criteria.seconds:g}s')
    devices = benchmark.get('devices')
    if not isinstance(devices, list):
        failures.append('Benchmark device results missing')
        devices = []
    if len(devices) != criteria.instances:
        failures.append(f'Benchmark has {len(devices)} device results, expected {criteria.instances}')
    serials: set[str] = set()
    for i, device in enumerate(devices):
        label = f'device[{i}]'
        if not isinstance(device, dict):
            failures.append(f'{label}: invalid result')
            continue
        serial = device.get('serial')
        if not isinstance(serial, str) or not serial.strip():
            failures.append(f'{label}: missing serial')
            continue
        if not ADB.is_mumu_serial(serial):
            failures.append(f'{label}: {serial} is not a MuMu ADB device')
        serial = canonical_mumu_serial(serial)
        if serial in serials:
            failures.append(f'{label}: duplicate emulator {serial}')
        serials.add(serial)
        actual = _number(device, 'elapsed_seconds', label, failures)
        frames_captured = _count(device, 'frames', label, failures)
        errors = _count(device, 'errors', label, failures)
        capture_fps = _number(device, 'effective_fps', label, failures)
        _number(device, 'capture_p95_ms', label, failures)
        max_gap = _number(device, 'max_frame_gap_seconds', label, failures)
        if max_gap > MAX_FRAME_GAP_SECONDS:
            failures.append(
                f'{label}: screenshot gap {max_gap:.3f}s exceeds '
                f'{MAX_FRAME_GAP_SECONDS:.3f}s')
        if actual < criteria.seconds:
            failures.append(f'{label}: measured only {actual:.1f}s of required {criteria.seconds:g}s')
        if not frames_captured:
            failures.append(f'{label}: no captured frames')
        if errors:
            failures.append(f'{label}: {errors:g} capture/detection errors')
        if capture_fps < criteria.min_capture_fps:
            failures.append(f'{label}: screenshot FPS {capture_fps:.2f} below {criteria.min_capture_fps:.2f}')
        if actual and abs(frames_captured / actual - capture_fps) > 0.06:
            failures.append(f'{label}: frame count and FPS disagree')

    video_serial = video.get('device')
    if not isinstance(video_serial, str) or not video_serial:
        failures.append('Video device serial missing')
    else:
        if not ADB.is_mumu_serial(video_serial):
            failures.append('Video diagnostic serial is not a MuMu ADB device')
        if canonical_mumu_serial(video_serial) not in serials:
            failures.append('Video diagnostic device not present in benchmark')

    workflow_measured = bool(benchmark.get('workflow'))
    if workflow_measured:
        for i, device in enumerate(devices):
            if isinstance(device, dict):
                _number(device, 'detection_p95_ms', f'device[{i}]', failures)
    else:
        outstanding.append('Real workflow OCR/image recognition load not measured')

    # Optional whole-host resource evidence accompanies (but does not replace)
    # the capture benchmark. Missing NVIDIA telemetry is explicitly outstanding,
    # not fabricated as zero GPU load or a pass of a hardware acceptance gate.
    resources = benchmark.get('host_resources')
    resource_coverage = {'cpu': False, 'ram': False, 'gpu': False}
    if resources is None:
        outstanding.append('Windows CPU/RAM/GPU utilization not recorded by benchmark')
    elif not isinstance(resources, dict):
        failures.append('Host resource report must be an object')
    else:
        duration = _number(resources, 'duration_seconds', 'resources', failures)
        interval = _number(resources, 'interval_seconds', 'resources', failures)
        count = _number(resources, 'sample_count', 'resources', failures)
        if not interval:
            failures.append('Resource sampling interval is zero')
        if not count.is_integer():
            failures.append('Resource sample count must be a whole number')
        if duration < criteria.seconds:
            failures.append('Host resource coverage is shorter than benchmark duration')
        # Allow for bounded NVIDIA queries to take extra time, but reject a
        # brief snapshot presented as a sustained resource-monitoring session.
        minimum_samples = max(2, int(criteria.seconds / (interval * 3))) if interval else 2
        if count < minimum_samples:
            failures.append(f'Host resources contain only {count:g} samples; need {minimum_samples}')
        metrics = resources.get('metrics')
        if not isinstance(metrics, dict):
            failures.append('Host resource metric summaries are missing')
            metrics = {}
        valid = {}
        for name in ('cpu_percent', 'ram_percent', 'ram_used_mib',
                     'gpu_utilization_percent', 'gpu_memory_used_mib'):
            metric = metrics.get(name)
            if not isinstance(metric, dict):
                failures.append(f'Resource metric {name} is missing or invalid')
                continue
            label = f'resource.{name}'
            samples = _number(metric, 'samples', label, failures)
            if not samples.is_integer() or samples > count:
                failures.append(f'{label}: inconsistent sample count')
                continue
            if not samples:
                valid[name] = False
                continue
            average = _number(metric, 'average', label, failures)
            peak = _number(metric, 'peak', label, failures)
            if peak < average or (name.endswith('percent') and peak > 100):
                failures.append(f'{label}: impossible average/peak values')
                continue
            valid[name] = samples >= minimum_samples
        sufficient_duration = duration >= criteria.seconds
        resource_coverage = {
            'cpu': bool(valid.get('cpu_percent') and sufficient_duration),
            'ram': bool(valid.get('ram_percent') and valid.get('ram_used_mib') and sufficient_duration),
            'gpu': bool(valid.get('gpu_utilization_percent') and
                        valid.get('gpu_memory_used_mib') and sufficient_duration),
        }
        missing = [name.upper() for name, covered in resource_coverage.items() if not covered]
        if missing:
            outstanding.append('Host utilization telemetry unavailable/insufficient for: ' + ', '.join(missing))
        else:
            outstanding.append('Whole-host CPU/RAM/GPU observed; acceptable limits and per-emulator attribution not verified')
        if resources.get('errors'):
            outstanding.append('Host telemetry reported probe errors; inspect host_resources.errors')

    # These are read-only capture gates, not approval to merge. Resource load,
    # real gestures and recovery observability have separate acceptance steps.
    return {
        'passed': not failures,
        'failures': list(dict.fromkeys(failures)),
        'error22_recovery_observed': recovery_observed,
        'workflow_recognition_measured': workflow_measured,
        'host_resource_coverage': resource_coverage,
        'hardware_acceptance_complete': False,
        'outstanding': outstanding,
        'criteria': {
            'required_instances': criteria.instances,
            'minimum_seconds': criteria.seconds,
            'minimum_video_fps': criteria.min_video_fps,
            'maximum_preview_gap_seconds': criteria.max_preview_gap,
            'maximum_video_gap_seconds': criteria.max_video_gap,
            'minimum_capture_fps': criteria.min_capture_fps,
            'maximum_capture_gap_seconds': MAX_FRAME_GAP_SECONDS,
            'maximum_error22_recovery_seconds': criteria.max_error22_recovery,
        },
    }
