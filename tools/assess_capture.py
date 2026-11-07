"""Assess measured PNG vs raw MuMu reports without controlling Android.

This is a read-only decision *for more hardware testing*, not authorization to
switch production capture or a ten-emulator acceptance result.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omniflow.devices import MAX_CAPTURE_AGE_SECONDS, canonical_mumu_serial
from swordx.adb import ADB
from tools.benchmark_mumu import write_json


MIN_SECONDS = 120.0
MIN_FPS = 0.8
MIN_RAW_FPS_GAIN = 0.10
MIN_TRIPLETS = 3
MAX_CAPTURE_GAP_SECONDS = 6.0


def _number(value, label: str, *, positive: bool = False) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f'{label}: missing or nonfinite number')
    if positive and value <= 0:
        raise ValueError(f'{label}: must be positive')
    return float(value)


def _profile(report: dict, capture_format: str) -> tuple[str, float, float, float, tuple[int, ...], float]:
    if not isinstance(report, dict):
        raise ValueError(f'{capture_format}: profile is not an object')
    if report.get('read_only') is not True or report.get('capture_format') != capture_format:
        raise ValueError(f'{capture_format}: incorrect source or missing read-only provenance')
    timeout = _number(report.get('adb_timeout_seconds'),
                      f'{capture_format} ADB timeout', positive=True)
    limit = MAX_CAPTURE_AGE_SECONDS if capture_format == 'png' else 15.0
    if timeout > limit:
        raise ValueError(
            f'{capture_format}: ADB screenshot timeout exceeds '
            f'{limit:.0f}-second source policy')
    serial = report.get('serial')
    if not isinstance(serial, str) or not ADB.is_mumu_serial(serial):
        raise ValueError(f'{capture_format}: missing or invalid MuMu ADB serial')
    requested = _number(report.get('requested_seconds'), f'{capture_format} requested duration')
    elapsed = _number(report.get('elapsed_seconds'), f'{capture_format} observed duration', positive=True)
    if requested < MIN_SECONDS or elapsed < MIN_SECONDS or elapsed < requested * .95:
        raise ValueError(f'{capture_format}: need >=120 seconds and >=95% of requested capture time')
    minimum = _number(report.get('min_fps'), f'{capture_format} configured minimum')
    if minimum < MIN_FPS:
        raise ValueError(f'{capture_format}: FPS gate below 0.8 is not accepted')
    frames = _number(report.get('frames'), f'{capture_format} frames', positive=True)
    if not frames.is_integer():
        raise ValueError(f'{capture_format}: captured frame count must be a whole number')
    errors = report.get('errors')
    if type(errors) is not int or errors != 0:
        raise ValueError(f'{capture_format}: capture/decode errors present or unknown')
    fps = _number(report.get('effective_fps'), f'{capture_format} FPS')
    if abs(frames / elapsed - fps) > 0.015:
        raise ValueError(f'{capture_format}: FPS inconsistent with observed frames/duration')
    gap_limit = _number(report.get('max_capture_gap_limit_seconds'),
                        f'{capture_format} configured gap limit', positive=True)
    gap = _number(report.get('max_capture_gap_seconds'),
                  f'{capture_format} longest capture gap')
    if gap_limit > MAX_CAPTURE_GAP_SECONDS or not 0 <= gap <= elapsed + .001:
        raise ValueError(f'{capture_format}: invalid or weakened capture gap constraint')
    shape = report.get('source_shape')
    shapes = report.get('source_shapes')
    if (not isinstance(shape, list) or len(shape) != 3 or
            any(type(n) is not int or n <= 0 for n in shape) or shape[2] != 3 or
            shapes != [shape]):
        raise ValueError(f'{capture_format}: missing or changed source geometry')
    bytes_mean = _number(report.get('payload_bytes_mean'), f'{capture_format} transfer size',
                         positive=True)
    if bytes_mean <= 0:
        raise ValueError(f'{capture_format}: invalid transfer size')
    timings = report.get('timings_ms')
    if not isinstance(timings, dict):
        raise ValueError(f'{capture_format}: missing stage timing')
    for stage in ('adb_exec_ms', 'decode_ms', 'normalize_ms', 'total_ms'):
        record = timings.get(stage)
        if not isinstance(record, dict):
            raise ValueError(f'{capture_format}: missing {stage} timing')
        for key in ('mean', 'p95'):
            value = _number(record.get(key), f'{capture_format} {stage} {key}')
            if value < 0:
                raise ValueError(f'{capture_format}: negative {stage} timing')
    total_p95 = _number(timings['total_ms']['p95'], f'{capture_format} total p95', positive=True)
    source = report.get('capture_source')
    expected = ('ADB exec-out screencap -p (PNG)' if capture_format == 'png'
                else 'ADB exec-out screencap (raw framebuffer)')
    if source != expected:
        raise ValueError(f'{capture_format}: unexpected screenshot source')
    failures = report.get('failures')
    if not isinstance(failures, list) or any(not isinstance(x, str) for x in failures):
        raise ValueError(f'{capture_format}: invalid failure list')
    expected_prefixes = set()
    if frames / elapsed < minimum:
        expected_prefixes.add('Capture ')
    if gap > gap_limit:
        expected_prefixes.add('Capture gap ')
    if not expected_prefixes:
        if report.get('passed') is not True or failures:
            raise ValueError(f'{capture_format}: inconsistent successful source report')
    else:
        # A subthreshold baseline is useful diagnostic evidence, but errors,
        # rotations and unrelated failures must never be accepted as such.
        if report.get('passed') is not False or len(failures) != len(expected_prefixes):
            raise ValueError(f'{capture_format}: unaccounted source failure')
        labels = set()
        for failure in failures:
            if failure.startswith('Capture gap ') and failure.endswith('s limit'):
                labels.add('Capture gap ')
            elif failure.startswith('Capture ') and failure.endswith(' FPS target'):
                labels.add('Capture ')
            else:
                raise ValueError(f'{capture_format}: unrelated capture failure')
        if labels != expected_prefixes:
            raise ValueError(f'{capture_format}: inconsistent FPS/gap failure report')
    return canonical_mumu_serial(serial), fps, total_p95, bytes_mean, tuple(shape), gap


def _fidelity(report: dict) -> str:
    if not isinstance(report, dict):
        raise ValueError('fidelity: missing report')
    if (report.get('read_only') is not True or
            report.get('passed') is not True or
            report.get('quality_equivalence_verified') is not True or
            report.get('hardware_acceptance_complete') is not False or
            report.get('capture_order') != ['png', 'raw', 'png'] or
            report.get('comparison_pixel_space') != 'native_source' or
            report.get('failures') != []):
        raise ValueError('fidelity: source did not verify read-only PNG/raw equivalence')
    count = report.get('triplets_requested')
    if type(count) is not int or count < MIN_TRIPLETS or report.get('triplets_checked') != count:
        raise ValueError('fidelity: need at least three complete PNG/raw/PNG triplets')
    results = report.get('results')
    if not isinstance(results, list) or len(results) != count:
        raise ValueError('fidelity: incomplete triplet evidence')
    tolerance = _number(report.get('pixel_tolerance'), 'fidelity channel tolerance')
    permitted = _number(report.get('max_changed_fraction'), 'fidelity changed fraction')
    if not 0 <= tolerance <= 8 or not 0 <= permitted <= .005:
        raise ValueError('fidelity: looser than the documented pixel thresholds')
    shapes = []
    for index, row in enumerate(results):
        if (not isinstance(row, dict) or row.get('index') != index + 1 or
                row.get('stable_png_controls') is not True or
                row.get('equivalent') is not True):
            raise ValueError(f'fidelity: triplet {index + 1} not verified')
        for key in ('png_motion_fraction', 'raw_delta_before_fraction',
                    'raw_delta_after_fraction'):
            value = _number(row.get(key), f'fidelity {key}')
            if not 0 <= value <= permitted:
                raise ValueError(f'fidelity: triplet {index + 1} exceeds pixel threshold')
        shape = row.get('source_shape')
        if (not isinstance(shape, list) or len(shape) != 3 or
                any(type(n) is not int or n <= 0 for n in shape) or shape[2] != 3):
            raise ValueError('fidelity: invalid source geometry')
        shapes.append(shape)
    if any(shape != shapes[0] for shape in shapes):
        raise ValueError('fidelity: orientation/geometry changed between triplets')
    serial = report.get('serial')
    if not isinstance(serial, str) or not ADB.is_mumu_serial(serial):
        raise ValueError('fidelity: missing or invalid MuMu device serial')
    return canonical_mumu_serial(serial), tuple(shapes[0])


def assess(png: dict, raw: dict, fidelity: dict) -> dict:
    result = {
        'read_only': True, 'comparison_complete': False,
        'raw_candidate_for_further_validation': False,
        'hardware_acceptance_complete': False,
        'production_capture_changed': False,
        'minimum_fps': MIN_FPS,
        'minimum_observed_seconds': MIN_SECONDS,
        'minimum_raw_fps_gain': MIN_RAW_FPS_GAIN,
        'metrics': None, 'failures': [],
        'passed': False,
    }
    try:
        png_id, png_fps, png_p95, png_bytes, png_shape, png_gap = _profile(png, 'png')
        raw_id, raw_fps, raw_p95, raw_bytes, raw_shape, raw_gap = _profile(raw, 'raw')
        quality_id, quality_shape = _fidelity(fidelity)
        if png_id != raw_id or png_id != quality_id:
            raise ValueError('PNG, raw and fidelity evidence are for different devices')
        if png_shape != raw_shape or png_shape != quality_shape:
            raise ValueError('PNG, raw and fidelity source geometry differs')
    except (ValueError, TypeError, KeyError) as exc:
        result['failures'] = [str(exc)]
        return result
    gain = (raw_fps - png_fps) / png_fps
    candidate = (raw.get('passed') is True and raw_fps >= MIN_FPS and
                 gain >= MIN_RAW_FPS_GAIN and raw_p95 < png_p95)
    result.update(
        comparison_complete=True,
        passed=True,  # Complete diagnostic only, NOT production acceptance.
        raw_candidate_for_further_validation=candidate,
        metrics={
            'serial': png_id,
            'png_fps': png_fps, 'raw_fps': raw_fps,
            'raw_relative_fps_gain': round(gain, 4),
            'png_capture_p95_ms': png_p95, 'raw_capture_p95_ms': raw_p95,
            'png_payload_bytes_mean': png_bytes, 'raw_payload_bytes_mean': raw_bytes,
            'source_shape': list(png_shape),
            'png_max_capture_gap_seconds': png_gap,
            'raw_max_capture_gap_seconds': raw_gap,
        },
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--png', type=Path, required=True)
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--fidelity', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    reports = []
    problems = []
    for name, path in [('PNG', args.png), ('raw', args.raw), ('fidelity', args.fidelity)]:
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(payload, dict):
                raise ValueError('root must be a JSON object')
            reports.append(payload)
        except (OSError, ValueError) as exc:
            problems.append(f'{name}: could not load {path}: {exc}')
            reports.append({})
    result = assess(*reports)
    if problems:
        result['comparison_complete'] = result['passed'] = False
        result['raw_candidate_for_further_validation'] = False
        result['failures'] = problems + result['failures']
    result['source_files'] = {
        'png': str(args.png), 'raw': str(args.raw), 'fidelity': str(args.fidelity)}
    write_json(args.output, result)
    print(json.dumps(result, indent=2))
    return 0 if result['comparison_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
