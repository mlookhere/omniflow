"""Read-only replay of recorded workflow screenshots for Studio debugging.

A one-frame recognition audit, not a simulated execution or hardware test.
No screenshots, manifest entries or emulator input are modified.
"""
from __future__ import annotations

from copy import copy
from pathlib import Path

import cv2
import numpy as np

from swordx.workflows import _safe_saved_image, step_order
from .engine import (OmniDetector, WorkflowError, resolve_action_chain,
                     validate_action, validate_recognition_manifest,
                     validate_saved_action_chains)
from .visual import MAX_SAVED_IMAGE_BYTES, action_summary, condition_summary


def draft_recognition_inspector(
        detector: OmniDetector, key: str, *, conditions: list[dict],
        condition_match: str, condition_only: bool,
        threshold: float | None, search_rect=...) -> OmniDetector:
    """Build an in-memory single-state inspector using the production matcher.

    Fail closed on invalid unsaved rules before requesting a screenshot.
    Saved templates are shared read-only; the detector and manifest on disk
    are never edited. Do not use this as a claim about other saved states.
    """
    if key not in detector.actions:
        raise WorkflowError('Save or select a recorded state to test its image')
    action = dict(detector.actions[key])
    action['conditions'] = conditions
    action['condition_match'] = condition_match
    action['skip_template'] = condition_only
    if search_rect is not ...:
        if search_rect is None:
            action.pop('search_rect', None)
        else:
            action['search_rect'] = list(search_rect)
    # Removing a saved override means use the workflow default, matching the
    # unchecked custom-confidence control in Studio.
    action.pop('threshold', None)
    if threshold is not None:
        action['threshold'] = threshold
    manifest = {
        'states': {key: detector.manifest['states'][key]},
        'actions': {key: action},
        'threshold': detector.threshold,
    }
    validate_recognition_manifest(manifest)
    draft = copy(detector)
    draft.manifest = manifest
    draft.actions = manifest['actions']
    return draft


def describe_action_plan(action: dict, chain: list[dict]) -> list[str]:
    """Validate and describe a draft or saved action sequence without input."""
    if action.get('passive'):
        return ['WAIT — no input will be sent']
    steps = []
    delay = int(action.get('pre_tap_delay_seconds', 0))
    if delay:
        steps.append(f'Initial delay {delay}s after stable recognition')
    if not chain:
        raise WorkflowError('Actionable state needs at least one action')
    for item in chain:
        validate_action(item)
        steps.append(action_summary(item))
    return steps


def describe_action_trace(chain: list[dict], *, passive: bool = False,
                          initial_delay_seconds: int = 0) -> list[str]:
    """Explain validated runner steps in order without executing any of them.

    Coordinates are normalized 540×960 authoring positions, not Android
    device pixels. Never include text payloads in returned trace lines.
    """
    if passive:
        return ['WAIT — no commands or Android input']
    lines = []
    if initial_delay_seconds:
        lines.append(
            f'Before step 1: delay {initial_delay_seconds}s after stable '
            'recognition (no input)')
    for index, action in enumerate(chain, 1):
        kind = validate_action(action)
        label = action_summary(action)
        if kind == 'tap_match':
            effect = ('Tap center of the currently matched image after two '
                      'consistent fresh screenshots; no fixed target')
        elif kind in ('tap', 'double_tap'):
            point = action['point']
            count = 2 if kind == 'double_tap' else 1
            effect = (f'{count} Android tap(s) at normalized '
                      f'({point[0]}, {point[1]})')
        elif kind in ('hold', 'drag', 'swipe'):
            start = action.get('start', action.get('point'))
            end = action.get('end', action.get('point'))
            effect = (
                f'Android swipe from ({start[0]}, {start[1]}) to '
                f'({end[0]}, {end[1]}) over '
                f'{action.get("duration_ms", 650)} ms')
        elif kind == 'field_text':
            point = action['point']
            effect = (
                f'Android tap at normalized ({point[0]}, {point[1]}), '
                'then type [contents hidden]')
        elif kind == 'text':
            effect = 'Android text input [contents hidden]'
        elif kind == 'key':
            effect = f'Android keyevent {action.get("code", 4)}'
        elif kind == 'wait':
            effect = f'Delay {action.get("seconds", 0)}s; no Android input'
        elif kind == 'launch':
            effect = 'Launch configured game; requires fresh session permission'
        else:
            effect = 'Mark session run complete; no Android input'
        lines.append(f'{index}. {label}\n   DRY RUN · {effect}')
    return lines


def describe_selected_actions(detector: OmniDetector, key: str,
                              trigger: int) -> list[str]:
    """Preview the actual winning state using the live runner's chain resolver."""
    if not key:
        return []
    action = detector.actions[key]
    chain = resolve_action_chain(
        action, len(detector.manifest['states'].get(key, [])), trigger)
    return describe_action_plan(action, chain)


def describe_recognition_evidence(detector: OmniDetector,
                                  details: list[dict]) -> list[str]:
    """Summarize every state decision without raw OCR output or device input."""
    lines = []
    for state in details:
        kind = 'WAIT' if state['passive'] else 'ACTION'
        image = (f"image {state['template_confidence']:.0%} "
                 f"(minimum {state['threshold']:.0%})"
                 if state['has_template'] else 'condition-only')
        priority = float(detector.actions[state['key']].get('priority', 0))
        reason = state.get('rank_reason', '')
        suffix = (f' · priority {priority:g}' if state['status'] in (
                  'selected', 'overridden by WAIT', 'lower-ranked match') else '')
        if reason:
            suffix += f' · {reason}'
        lines.append(f"{state['label']} [{kind}]: {state['status']} — {image}{suffix}")
        rules = detector.actions[state['key']].get('conditions', [])
        outcomes = state['condition_results']
        if rules:
            mode = detector.actions[state['key']].get('condition_match', 'all')
            lines.append('  WHEN rules: all must pass (AND)' if mode == 'all'
                         else '  WHEN rules: any one may pass (OR)')
        if rules and outcomes is None:
            lines.append('  Conditions not evaluated: image threshold failed')
        elif outcomes is not None:
            evidence = state.get('condition_evidence') or []
            for index, (passed, rule) in enumerate(zip(outcomes, rules)):
                explanation = (f' ({evidence[index]})'
                               if index < len(evidence) else '')
                lines.append(
                    f"  {'PASS' if passed else 'FAIL'} — "
                    f"{condition_summary(rule)}{explanation}")
    return lines


def audit_draft_saved_captures(
        folder: Path, key: str | None, *, conditions: list[dict],
        condition_match: str, condition_only: bool,
        threshold: float | None, search_rect=...) -> list[dict]:
    """Cross-check unsaved WHEN recognition against recorded screenshot examples.

    This tests only the draft state against each image, not the final winning
    state in the saved workflow. Matches on another state's image are
    potential collisions, not proof that runtime would select this draft.
    """
    detector = OmniDetector(folder)
    if key:
        draft = draft_recognition_inspector(
            detector, key, conditions=conditions,
            condition_match=condition_match, condition_only=condition_only,
            threshold=threshold, search_rect=search_rect)
        draft_key = key
    else:
        if not condition_only:
            raise WorkflowError('Save the new image-template state before replay')
        draft_key = '__unsaved_draft__'
        while draft_key in detector.actions:
            draft_key += '_'
        manifest = {
            'states': {draft_key: []},
            'actions': {draft_key: {
                'label': 'Unsaved draft', 'skip_template': True,
                'conditions': conditions, 'condition_match': condition_match}},
            'threshold': detector.threshold,
        }
        # Even when an image gate is disabled, malformed numeric draft
        # settings must not be interpreted as valid by offline diagnostics.
        if threshold is not None:
            manifest['actions'][draft_key]['threshold'] = threshold
        validate_recognition_manifest(manifest)
        draft = copy(detector)
        draft.manifest = manifest
        draft.actions = manifest['actions']
        draft.templates = {}

    results = []
    for source_key in step_order(detector.manifest):
        action = detector.actions[source_key]
        result = {
            'source_key': source_key,
            'source': action.get('label', source_key),
            'status': 'unavailable',
            'reason': '',
            'evidence': [],
        }
        results.append(result)
        frame, reason = _read_saved_capture(detector.folder, action.get('capture'))
        if frame is None:
            result['reason'] = reason
            continue
        try:
            winner, details = draft.inspect(frame)
        except (WorkflowError, cv2.error, ValueError, KeyError, TypeError) as exc:
            result['status'] = 'error'
            result['reason'] = f'Draft inspection failed: {exc}'
            continue
        result['evidence'] = describe_recognition_evidence(draft, details)
        result['reason'] = details[0]['status']
        if winner.key:
            result['status'] = 'matched' if source_key == key else 'collision'
        else:
            result['status'] = 'not matched'
    return results


def _read_saved_capture(folder: Path, name: str | None) -> tuple[np.ndarray | None, str]:
    """Reuse the exact read-only path/geometry checks for every replay."""
    if not name:
        return None, 'No saved screenshot (legacy or unrecorded state)'
    try:
        path = _safe_saved_image(folder, 'captures', name)
    except ValueError:
        return None, 'Unsafe screenshot reference'
    if not path.is_file():
        return None, 'Saved screenshot is missing'
    try:
        size = path.stat().st_size
    except OSError:
        return None, 'Saved screenshot is inaccessible'
    # Match the offline image-import limit before allowing OpenCV to decode
    # an externally modified capture. Recorded 540×960 frames are far smaller.
    if not 0 < size <= MAX_SAVED_IMAGE_BYTES:
        return None, 'Saved screenshot must be nonempty and at most 32 MiB'
    try:
        frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    except cv2.error:
        frame = None
    if (not isinstance(frame, np.ndarray) or frame.ndim != 3 or
            frame.shape[2] != 3 or frame.dtype != np.uint8 or not frame.size):
        return None, 'Saved screenshot is unreadable'
    if frame.shape != (960, 540, 3):
        return None, 'Saved screenshot must be a normalized 540×960 color image'
    return frame, ''


def replay_saved_action_map(
        folder: Path, source_key: str
        ) -> tuple[np.ndarray, list[dict], bool, str, int]:
    """Resolve a recorded screen's current winning actions for a visual dry-run.

    The replay row is only a navigation hint. Reload and validate the actual
    saved workflow and screen on each click, preventing a stale result from
    drawing targets after a state or capture has changed.
    """
    detector = OmniDetector(folder)
    validate_saved_action_chains(detector.manifest)
    if source_key not in detector.actions:
        raise WorkflowError('Recorded source state no longer exists')
    frame, reason = _read_saved_capture(
        detector.folder, detector.actions[source_key].get('capture'))
    if frame is None:
        raise WorkflowError(reason)
    winner, _details = detector.inspect(frame)
    if not winner.key:
        raise WorkflowError('No saved state recognizes this screenshot')
    action = detector.actions[winner.key]
    chain = resolve_action_chain(
        action, len(detector.manifest['states'].get(winner.key, [])),
        winner.trigger)
    describe_action_plan(action, chain)  # Revalidate the selected commands.
    if any(step.get('kind') == 'tap_match' for step in chain):
        if winner.location is None:
            raise WorkflowError('Moving image position unavailable on this screenshot')
        # Only the read-only overlay uses a point; persisted steps and live
        # execution retain the dynamic target and fresh-frame confirmation.
        x1, y1, x2, y2 = winner.location
        preview_point = [round((x1 + x2) / 2), round((y1 + y2) / 2)]
        chain = [({'kind': 'tap', 'point': preview_point}
                  if step.get('kind') == 'tap_match' else step)
                 for step in chain]
    return (frame, chain, bool(action.get('passive')), winner.label,
            int(action.get('pre_tap_delay_seconds', 0)))


def replay_saved_captures(folder: Path) -> list[dict]:
    """Check every recorded state against the same matcher used during runs.

    Unavailable, corrupt or unsafe capture paths are reported, never counted as
    successful matches. All candidates are inspected without sending actions.
    """
    detector = OmniDetector(folder)
    validate_saved_action_chains(detector.manifest)
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
            'evidence': [],
        }
        results.append(item)
        frame, reason = _read_saved_capture(detector.folder, action.get('capture'))
        if frame is None:
            item['reason'] = reason
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
        item['evidence'] = describe_recognition_evidence(detector, details)
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
            if expected is not None and expected['status'] == 'lower-ranked match':
                item['reason'] += f" ({expected['rank_reason']})"
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
