"""Read-only replay of recorded workflow screenshots for Studio debugging.

A one-frame recognition audit, not a simulated execution or hardware test.
No screenshots, manifest entries or emulator input are modified.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from swordx.workflows import step_order
from .engine import OmniDetector, WorkflowError, resolve_action_chain, validate_action
from .visual import action_summary, condition_summary


def describe_action_plan(action: dict, chain: list[dict]) -> list[str]:
    """Validate and describe a draft or saved action sequence without input."""
    if action.get('passive'):
        return ['WAIT — no input will be sent']
    steps = []
    delay = int(action.get('pre_tap_delay_seconds', 0))
    if delay:
        steps.append(f'Initial delay {delay}s after stable recognition')
    for item in chain:
        kind = validate_action(item)
        if kind == 'text':
            steps.append('Type text (contents hidden)')
        elif kind == 'field_text':
            steps.append('Tap selected field and type text (contents hidden)')
        else:
            steps.append(action_summary(item))
    return steps or ['No configured action']


def describe_selected_actions(detector: OmniDetector, key: str,
                              trigger: int) -> list[str]:
    """Preview the actual winning state using the live runner's chain resolver."""
    if not key:
        return []
    action = detector.actions[key]
    chain = resolve_action_chain(
        action, len(detector.manifest['states'].get(key, [])), trigger)
    return describe_action_plan(action, chain)


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
            'overlaps': [],
            'action_plan': [],
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
            plan = describe_selected_actions(detector, winner.key, winner.trigger)
        except (WorkflowError, cv2.error, ValueError, KeyError, TypeError) as exc:
            item['status'] = 'error'
            item['reason'] = f'Replay inspection failed: {exc}'
            continue
        item['actual_key'] = winner.key
        item['actual'] = winner.label
        item['action_plan'] = plan
        # A selected state can still be ambiguous. Expose other candidates
        # that passed recognition, even when the expected state won.
        item['overlaps'] = [
            {'key': candidate['key'], 'label': candidate['label'],
             'passive': candidate['passive']}
            for candidate in details
            if candidate['key'] != winner.key and candidate['status'] in
            ('lower-ranked match', 'overridden by WAIT')
        ]
        if winner.key == key:
            item['status'] = 'matched'
            item['reason'] = 'Recorded state selected'
        else:
            item['status'] = 'different' if winner.key else 'unknown'
            expected = next((d for d in details if d['key'] == key), None)
            item['reason'] = (expected['status'] if expected is not None
                              else 'Recorded state not present in matcher')
            if expected is not None and expected['status'] == 'template below threshold':
                item['reason'] += (
                    f" (image {expected['template_confidence']:.0%}, "
                    f"minimum {expected['threshold']:.0%})")
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
