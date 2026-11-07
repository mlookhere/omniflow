"""Read-only replay of recorded workflow screenshots for Studio debugging.

A one-frame recognition audit, not a simulated execution or hardware test.
No screenshots, manifest entries or emulator input are modified.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from swordx.workflows import step_order
from .engine import OmniDetector, WorkflowError
from .visual import condition_summary


def replay_saved_captures(folder: Path) -> list[dict]:
    """Check every recorded state against the same matcher used during runs.

    Unavailable, corrupt or unsafe capture paths are reported, never counted as
    successful matches. All candidates are inspected without sending actions.
    """
    detector = OmniDetector(folder)
    capture_root = (detector.folder / 'captures').resolve()
    results = []
    for key in step_order(detector.manifest):
        action = detector.actions[key]
        item = {
            'expected_key': key,
            'expected': action.get('label', key),
            'actual_key': '',
            'actual': '',
            'status': 'unavailable',
            'reason': '',
        }
        results.append(item)
        name = action.get('capture')
        if not name:
            item['reason'] = 'No saved screenshot (legacy or unrecorded state)'
            continue
        # Captures created by the recorder are filenames, never paths. Reject
        # traversal and symlinks so an edited manifest cannot read other files.
        if (not isinstance(name, str) or name in ('.', '..') or
                '/' in name or '\\' in name):
            item['reason'] = 'Unsafe screenshot reference'
            continue
        path = detector.folder / 'captures' / name
        if not path.resolve().is_relative_to(capture_root):
            item['reason'] = 'Screenshot resolves outside captures folder'
            continue
        if not path.is_file():
            item['reason'] = 'Saved screenshot is missing'
            continue
        try:
            frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
        except cv2.error:
            frame = None
        if (not isinstance(frame, np.ndarray) or frame.ndim != 3 or
                frame.shape[2] != 3 or frame.dtype != np.uint8 or not frame.size):
            item['reason'] = 'Saved screenshot is unreadable'
            continue
        try:
            winner, details = detector.inspect(frame)
        except (WorkflowError, cv2.error, ValueError, KeyError) as exc:
            item['status'] = 'error'
            item['reason'] = f'Recognition failed: {exc}'
            continue
        item['actual_key'] = winner.key
        item['actual'] = winner.label
        if winner.key == key:
            item['status'] = 'matched'
            item['reason'] = 'Recorded state selected'
        else:
            item['status'] = 'different' if winner.key else 'unknown'
            expected = next((d for d in details if d['key'] == key), None)
            item['reason'] = (expected['status'] if expected is not None
                              else 'Recorded state not present in matcher')
            if expected is not None and expected['condition_results'] is not None:
                failures = [
                    condition_summary(rule)
                    for passed, rule in zip(
                        expected['condition_results'],
                        action.get('conditions', []))
                    if not passed
                ]
                if failures:
                    item['reason'] += ': ' + '; '.join(failures)
    return results
