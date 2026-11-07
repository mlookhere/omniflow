"""Read-only PNG/raw image fidelity test with screen-motion controls.

Use a stationary game menu. PNG->RAW->PNG triplets detect motion so different
game frames cannot be mistaken for equivalent or non-equivalent capture codecs.
No screenshots are persisted, no Android input or H.264 encoders are used.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from omniflow.devices import Device, canonical_mumu_serial, discover_mumu
from swordx.adb import ADB
from tools.benchmark_mumu import write_json
from tools.profile_capture import capture_frame


def changed_fraction(a: np.ndarray, b: np.ndarray, tolerance: int) -> float:
    if a.shape != b.shape or a.ndim != 3 or a.shape[2] != 3 or a.size == 0:
        raise ValueError('Capture image dimensions or channels differ')
    difference = np.abs(a.astype(np.int16) - b.astype(np.int16))
    return float(np.count_nonzero(np.any(difference > tolerance, axis=2)) /
                 (a.shape[0] * a.shape[1]))


def compare(serial: str, triplets: int = 3, pixel_tolerance: int = 8,
            max_changed_fraction: float = .005, *, device_factory=Device) -> dict:
    if not 1 <= triplets <= 10 or not 0 <= pixel_tolerance <= 255:
        raise ValueError('Invalid comparison count or pixel tolerance')
    if not math.isfinite(max_changed_fraction) or not 0 <= max_changed_fraction <= 1:
        raise ValueError('Maximum changed fraction must be finite and between 0 and 1')
    device = device_factory(serial=serial)
    device.ensure_connected()
    results, failures = [], []
    for index in range(triplets):
        try:
            before, meta_before = capture_frame(device, capture_format='png',
                                                native_pixels=True)
            raw, meta_raw = capture_frame(device, capture_format='raw',
                                          native_pixels=True)
            after, meta_after = capture_frame(device, capture_format='png',
                                              native_pixels=True)
            if (meta_before['source_shape'] != meta_raw['source_shape'] or
                    meta_raw['source_shape'] != meta_after['source_shape']):
                raise ValueError('Source screenshot geometry changed')
            source_shape = tuple(meta_before['source_shape'])
            if any(image.shape != source_shape for image in (before, raw, after)):
                raise ValueError('Fidelity samples are not in native source geometry')
            motion = changed_fraction(before, after, pixel_tolerance)
            delta_before = changed_fraction(before, raw, pixel_tolerance)
            delta_after = changed_fraction(after, raw, pixel_tolerance)
            stable = motion <= max_changed_fraction
            equivalent = (stable and delta_before <= max_changed_fraction and
                          delta_after <= max_changed_fraction)
            results.append({
                'index': index + 1,
                'png_motion_fraction': round(motion, 6),
                'raw_delta_before_fraction': round(delta_before, 6),
                'raw_delta_after_fraction': round(delta_after, 6),
                'stable_png_controls': stable,
                'equivalent': equivalent,
                'source_shape': meta_before['source_shape'],
                'png_before_bytes': meta_before['payload_bytes'],
                'raw_bytes': meta_raw['payload_bytes'],
                'png_after_bytes': meta_after['payload_bytes'],
            })
            if not stable:
                failures.append(f'Triplet {index+1}: PNG controls moved; use a stationary screen')
            elif not equivalent:
                failures.append(f'Triplet {index+1}: raw pixels differ from stable PNG controls')
        except Exception as exc:
            failures.append(f'Triplet {index+1}: {type(exc).__name__}: {exc}')
            results.append({'index': index+1, 'error': f'{type(exc).__name__}: {exc}'})
    passed = len(results) == triplets and not failures
    return {
        'serial': serial, 'read_only': True,
        'capture_order': ['png', 'raw', 'png'],
        'triplets_requested': triplets, 'triplets_checked': len(results),
        'pixel_tolerance': pixel_tolerance,
        'max_changed_fraction': max_changed_fraction,
        'results': results,
        'quality_equivalence_verified': passed,
        'hardware_acceptance_complete': False,
        'passed': passed, 'failures': failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', default='', help='MuMu serial, or first discovered')
    parser.add_argument('--triplets', type=int, default=3)
    parser.add_argument('--pixel-tolerance', type=int, default=8)
    parser.add_argument('--max-changed-fraction', type=float, default=.005)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if not 1 <= args.triplets <= 10 or not 0 <= args.pixel_tolerance <= 255:
        parser.error('triplets must be 1–10 and pixel tolerance 0–255')
    if not math.isfinite(args.max_changed_fraction) or not 0 <= args.max_changed_fraction <= 1:
        parser.error('max changed fraction must be finite and 0–1')

    def fail(message: str) -> int:
        result = {'passed': False, 'quality_equivalence_verified': False,
                  'hardware_acceptance_complete': False, 'read_only': True,
                  'serial': args.serial, 'results': [], 'failures': [message]}
        if args.output:
            write_json(args.output, result)
        print(message, file=sys.stderr)
        return 1

    if args.serial and not ADB.is_mumu_serial(args.serial):
        return fail('Requested serial is not a MuMu device')
    try:
        available = discover_mumu()
    except Exception as exc:
        return fail(f'MuMu discovery failed: {type(exc).__name__}: {exc}')
    serial = next((d for d in available if not args.serial or
                   canonical_mumu_serial(d) == canonical_mumu_serial(args.serial)), '')
    if not serial:
        return fail('Requested MuMu device not connected')
    try:
        result = compare(serial, args.triplets, args.pixel_tolerance,
                         args.max_changed_fraction)
    except Exception as exc:
        return fail(f'Capture comparison failed: {type(exc).__name__}: {exc}')
    if args.output:
        write_json(args.output, result)
        print(f'JSON report: {args.output}')
    print(json.dumps({'passed': result['passed'],
                      'quality_equivalence_verified': result['quality_equivalence_verified'],
                      'failures': result['failures']}, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
