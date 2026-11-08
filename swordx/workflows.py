"""Persistent visual workflows with independent region/gesture pairs."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from .config import data_dir
from .detection import SIZE

BUILTIN = 'Built-in dungeon'


def workflow_root(base: Path | None = None) -> Path:
    path = (base or data_dir()) / 'workflows'
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_workflows(base: Path | None = None) -> dict[str, Path]:
    found = {BUILTIN: Path(__file__).resolve().parents[1] / 'assets'}
    for folder in sorted(workflow_root(base).iterdir()):
        if folder.is_dir() and (folder / 'manifest.json').is_file():
            try:
                meta = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
                found[meta['name']] = folder
            except (OSError, KeyError, ValueError, TypeError):
                continue
    return found


def _write_manifest(path: Path, content: dict) -> None:
    # Each save stages its own file so overlapping writes never share a temp.
    # A failed replacement must leave the previous manifest intact and not
    # leave a stale staging file in the workflow folder.
    temp = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        temp.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def create_workflow(name: str, base: Path | None = None) -> Path:
    name = name.strip()
    if not (1 <= len(name) <= 48) or any(ch in name for ch in '\\/:*?"<>|\r\n'):
        raise ValueError('Enter a workflow name up to 48 characters')
    if any(existing.casefold() == name.casefold() for existing in list_workflows(base)):
        raise ValueError('A workflow with that name already exists')
    path = workflow_root(base) / uuid4().hex
    path.mkdir()
    (path / 'anchors').mkdir()
    (path / 'captures').mkdir()
    _write_manifest(path / 'manifest.json', {'name': name, 'threshold': .82,
                   'states': {}, 'actions': {}, 'order': []})
    return path


def step_order(content: dict) -> list[str]:
    """Append legacy entries missing from order. Never affect detection priority."""
    keys = content.get('states', {})
    ordered = [key for key in content.get('order', []) if key in keys]
    return list(dict.fromkeys(ordered + list(keys)))


def reorder_steps(folder: Path, keys: list[str]) -> None:
    path = Path(folder) / 'manifest.json'
    content = json.loads(path.read_text(encoding='utf-8'))
    if len(keys) != len(set(keys)) or set(keys) != set(content['states']):
        raise ValueError('Reordering must include every state exactly once')
    content['order'] = list(keys)
    _write_manifest(path, content)


def _safe_saved_image(folder: Path, kind: str, name: str) -> Path:
    """Resolve a recorded image without trusting externally edited manifests."""
    if (not isinstance(name, str) or not name or name in ('.', '..') or
            '/' in name or '\\' in name or ':' in name or '\x00' in name):
        raise ValueError(f'Unsafe saved {kind} filename')
    workflow = folder.resolve()
    root = (folder / kind).resolve()
    if not root.is_relative_to(workflow):
        raise ValueError(f'Saved {kind} directory resolves outside workflow')
    image = folder / kind / name
    if not image.resolve().is_relative_to(root):
        raise ValueError(f'Saved {kind} image resolves outside workflow')
    return image


def _saved_step_images(folder: Path, probes: list[dict], capture: str | None) -> list[Path]:
    """Validate all recorded image references before mutating a saved state."""
    if not isinstance(probes, list):
        raise ValueError('Invalid saved templates')
    paths = []
    for index, probe in enumerate(probes, 1):
        if not isinstance(probe, dict):
            raise ValueError(f'Invalid saved template {index}')
        paths.append(_safe_saved_image(folder, 'anchors', probe.get('file')))
    # A missing or blank screenshot is valid for legacy template-only states.
    if capture is not None and capture != '':
        paths.append(_safe_saved_image(folder, 'captures', capture))
    return paths


def _unshared_step_images(folder: Path, content: dict, key: str) -> list[Path]:
    """Only reclaim images that no other saved state still references."""
    if key not in content['states'] or key not in content['actions']:
        raise ValueError('Saved state no longer exists; reload the workflow')
    images = _saved_step_images(folder, content['states'][key],
                                content['actions'][key].get('capture'))
    shared = set()
    for other, probes in content['states'].items():
        if other == key:
            continue
        if other not in content['actions']:
            raise ValueError('Saved state actions are incomplete; reload the workflow')
        for path in _saved_step_images(folder, probes,
                                       content['actions'][other].get('capture')):
            shared.add(path.resolve())
    return [path for path in images if path.resolve() not in shared]


def _prune_old_images(images: list[Path]) -> None:
    """Post-commit cleanup must not turn a persisted edit into a false failure."""
    for image in images:
        try:
            image.unlink(missing_ok=True)
        except OSError as exc:
            # A leftover orphan is recoverable; the committed state is not
            # rolled back by an unrelated file-permission failure.
            logging.warning('Could not remove unused workflow image: %s', exc)


def load_step(folder: Path, key: str) -> tuple[dict, list[tuple[int, int, int, int]],
                                                list[tuple[int, int] | None], np.ndarray | None]:
    """Load editable region/gesture pairs and saved screenshot (if available)."""
    folder = Path(folder)
    content = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    action = content['actions'][key].copy()
    # Check *all* image references before reading even the primary screenshot.
    # Old captures may be missing, but unsafe references must never be opened.
    probes = content['states'][key]
    boxes = []
    anchors = []
    for index, probe in enumerate(probes, 1):
        if not isinstance(probe, dict):
            raise ValueError(f'Invalid saved template {index}')
        rect = probe.get('rect')
        if (not isinstance(rect, (list, tuple)) or len(rect) != 4 or
                any(type(value) is not int for value in rect)):
            raise ValueError(f'Invalid saved template {index} region')
        x1, y1, x2, y2 = rect
        if not (0 <= x1 < x2 <= SIZE[0] and 0 <= y1 < y2 <= SIZE[1]):
            raise ValueError(f'Invalid saved template {index} region')
        boxes.append(tuple(rect))
        anchors.append(_safe_saved_image(folder, 'anchors', probe.get('file')))
    capture = action.get('capture')
    capture_path = (_safe_saved_image(folder, 'captures', capture)
                    if capture else None)
    taps = [tuple(tap) if tap is not None else None for tap in action.get('taps', [])]
    if 'gestures' in action:
        taps = [tuple(gesture['point']) if gesture['kind'] == 'tap' else None
                for gesture in action['gestures']]
    if not taps and not action.get('passive') and not action.get('action_chain'):  # v1.1 compatibility.
        taps = [tuple(action['tap'])] + [None] * (len(boxes) - 1)
    frame = None
    if capture_path and capture_path.is_file():
        try:
            frame = cv2.imread(str(capture_path), cv2.IMREAD_COLOR)
        except cv2.error as exc:
            raise ValueError('Saved screenshot is unreadable; recapture this state') from exc
        if frame is None:
            raise ValueError('Saved screenshot is unreadable; recapture this state')
        if frame.dtype != np.uint8 or frame.shape != (SIZE[1], SIZE[0], 3):
            raise ValueError('Saved screenshot must be a normalized 540×960 color image')
    if frame is None:
        # Legacy/missing full-screen captures can still be edited through a
        # reconstructed preview, but must be recaptured before visual edits.
        frame = np.full((SIZE[1], SIZE[0], 3), 28, dtype=np.uint8)
        for (x1, y1, x2, y2), anchor in zip(boxes, anchors):
            crop = cv2.imread(str(anchor))
            if crop is not None:
                frame[y1:y2, x1:x2] = cv2.resize(crop, (x2-x1, y2-y1))
        action['_needs_recapture'] = True
    return action, boxes, taps, frame


def _validate_pre_tap_delay(seconds: int) -> None:
    if type(seconds) is not int or not 0 <= seconds <= 86400:
        raise ValueError('Delay before action must be 0–86400 whole seconds')


def update_step_delay(folder: Path, key: str, seconds: int) -> None:
    """Edit a state's wait without touching its screenshot, detection crops or gestures."""
    _validate_pre_tap_delay(seconds)
    path = Path(folder) / 'manifest.json'
    content = json.loads(path.read_text(encoding='utf-8'))
    if key not in content['states'] or key not in content['actions']:
        raise ValueError('Saved state no longer exists; reload the workflow')
    # A missing field means zero delay, preserving all existing recordings.
    if seconds:
        content['actions'][key]['pre_tap_delay_seconds'] = seconds
    else:
        content['actions'][key].pop('pre_tap_delay_seconds', None)
    _write_manifest(path, content)


def _validate_retry(retry_mode: str, retry_interval_seconds: int) -> None:
    if retry_mode not in ('interval', 'on_return'):
        raise ValueError('Retry mode must be timed or after leaving and returning')
    if type(retry_interval_seconds) is not int or not 1 <= retry_interval_seconds <= 86400:
        raise ValueError('Retry interval must be 1–86400 whole seconds')


def update_step_retry(folder: Path, key: str, enabled: bool, mode: str, seconds: int) -> None:
    """Change retry rules in-place without altering detection crops or screenshots."""
    _validate_retry(mode, seconds)
    path = Path(folder) / 'manifest.json'
    content = json.loads(path.read_text(encoding='utf-8'))
    if key not in content['states'] or key not in content['actions']:
        raise ValueError('Saved state no longer exists; reload the workflow')
    action = content['actions'][key]
    action['retry'] = bool(enabled)
    if mode == 'interval':
        action.pop('retry_mode', None)
    else:
        action['retry_mode'] = mode
    if seconds == 25:
        action.pop('retry_interval_seconds', None)
    else:
        action['retry_interval_seconds'] = seconds
    _write_manifest(path, content)


def _validate_max_extra_taps(count: int) -> None:
    if type(count) is not int or not 0 <= count <= 20:
        raise ValueError('Maximum extra taps must be between 0 (unlimited) and 20')


def update_step_settings(folder: Path, key: str, label: str, *, end: bool,
                         retry: bool, retry_mode: str = 'interval',
                         retry_interval_seconds: int = 25,
                         pre_tap_delay_seconds: int = 0,
                         passive: bool = False, max_extra_taps: int = 0,
                         studio: dict | None = None) -> None:
    """Save all editor settings atomically; preserve screenshots and region data.

    Works with every historical action format, including legacy AND templates
    that cannot be safely re-recorded without capturing a new full screen.
    """
    label = label.strip()
    if not 1 <= len(label) <= 48:
        raise ValueError('Give this state a name (1–48 characters)')
    _validate_pre_tap_delay(pre_tap_delay_seconds)
    _validate_retry(retry_mode, retry_interval_seconds)
    _validate_max_extra_taps(max_extra_taps)
    path = Path(folder) / 'manifest.json'
    content = json.loads(path.read_text(encoding='utf-8'))
    if key not in content['states'] or key not in content['actions']:
        raise ValueError('Saved state no longer exists; reload the workflow')
    action = content['actions'][key]
    chain = studio['action_chain'] if studio is not None else action.get('action_chain')
    if studio is not None:
        if studio['skip_template'] and not studio['conditions']:
            raise ValueError('Condition-only states require a recognition condition')
        if not studio['skip_template'] and not content['states'][key]:
            raise ValueError('Image matching requires a saved template; recapture a detection region')
    if not passive and action.get('passive') and not (action.get('gestures') or
                                                    action.get('taps') or action.get('tap') or chain):
        raise ValueError('Add a tap or drag to convert this Wait state into an action')
    action.update(label=label, end=bool(end), retry=bool(retry), passive=bool(passive))
    if studio is not None:
        # Save Studio's conditions and action chain in this *same* manifest
        # replacement. Legacy recorder callers omit studio and retain the
        # original fields unchanged.
        if chain is None:
            action.pop('action_chain', None)
        else:
            action['action_chain'] = [dict(item) for item in chain]
        if studio['conditions']:
            action['conditions'] = [dict(item) for item in studio['conditions']]
        else:
            action.pop('conditions', None)
        if studio['skip_template']:
            action['skip_template'] = True
        else:
            action.pop('skip_template', None)
    if max_extra_taps:
        action['max_extra_taps'] = max_extra_taps
    else:
        action.pop('max_extra_taps', None)
    if pre_tap_delay_seconds:
        action['pre_tap_delay_seconds'] = pre_tap_delay_seconds
    else:
        action.pop('pre_tap_delay_seconds', None)
    if retry_mode == 'on_return':
        action['retry_mode'] = retry_mode
    else:
        action.pop('retry_mode', None)
    if retry_interval_seconds != 25:
        action['retry_interval_seconds'] = retry_interval_seconds
    else:
        action.pop('retry_interval_seconds', None)
    _write_manifest(path, content)


def record_step(folder: Path, label: str, frame: np.ndarray,
                boxes: list[tuple[int, int, int, int]],
                click: tuple[int, int] | None = None,
                end: bool = False, retry: bool = False, *,
                taps: list[tuple[int, int]] | None = None,
                key: str | None = None,
                gestures: list[dict] | None = None,
                pre_tap_delay_seconds: int = 0,
                retry_mode: str = 'interval', retry_interval_seconds: int = 25,
                passive: bool = False, max_extra_taps: int = 0,
                action_chain: list[dict] | None = None,
                conditions: list[dict] | None = None, condition_only: bool = False) -> str:
    """Save 1–2 region/gesture pairs. Gesture can be tap or timed drag.

    ``click`` and ``taps`` retain the v1.2 API for old workflow clients.
    Legacy two-anchor/one-tap states remain readable without conversion.
    """
    _validate_pre_tap_delay(pre_tap_delay_seconds)
    _validate_retry(retry_mode, retry_interval_seconds)
    _validate_max_extra_taps(max_extra_taps)
    label = label.strip()
    if not 1 <= len(label) <= 48:
        raise ValueError('Give this state a name (1–48 characters)')
    if frame is None or frame.shape[:2] != (SIZE[1], SIZE[0]):
        raise ValueError('Screen capture must be normalized to 540×960')
    if not (0 <= len(boxes) <= 2 if condition_only else 1 <= len(boxes) <= 2):
        raise ValueError('Select one or two detection regions')
    if condition_only and not conditions:
        raise ValueError('Condition-only states require a recognition condition')
    if condition_only and not passive and not action_chain and not gestures and not taps and not click:
        raise ValueError('An action state needs a gesture or an action sequence')
    if passive:
        if gestures or taps or click:
            raise ValueError('A wait state cannot have any taps or drags')
        gestures = []
        taps = []
        legacy_and = False
    elif gestures is not None:
        if taps is not None or click is not None:
            raise ValueError('Supply gestures OR tap coordinates, not both')
        taps = []
        if not passive and not action_chain and len(gestures) != len(boxes):
            raise ValueError('Each detection region needs its own tap or drag')
        for gesture in gestures:
            if not isinstance(gesture, dict):
                raise ValueError('Gesture must be a tap or drag')
            kind = gesture.get('kind')
            if kind == 'tap':
                points = [gesture.get('point')]
            elif kind == 'drag':
                points = [gesture.get('start'), gesture.get('end')]
                duration = gesture.get('duration_ms')
                if type(duration) is not int or not 100 <= duration <= 10000:
                    raise ValueError('Drag duration must be 100–10000 milliseconds')
            else:
                raise ValueError('Gesture must be a tap or drag')
            for point in points:
                if (not isinstance(point, (tuple, list)) or len(point) != 2
                        or any(type(value) is not int for value in point)
                        or not (0 <= point[0] < SIZE[0] and 0 <= point[1] < SIZE[1])):
                    raise ValueError('Gesture position is outside the game screen')
            taps.append(tuple(points[0]) if kind == 'tap' else None)
        legacy_and = False
    elif taps is None:
        if action_chain:
            taps = []
            gestures = []
            legacy_and = False
        elif click is None:
            raise ValueError('Add a tap or drag for each region')
        # For backward API compatibility, legacy two-region callers get their
        # previous AND matching behavior with a single tap.
        taps = [click] if len(boxes) == 1 else [click, click]
        legacy_and = len(boxes) == 2
    else:
        legacy_and = False
    if not passive and not action_chain and (len(taps) != len(boxes) or
                        (gestures is None and any(tap is None for tap in taps))):
        raise ValueError('Each detection region needs its own tap target')
    for tap in taps:
        if tap is not None and not (0 <= tap[0] < SIZE[0] and 0 <= tap[1] < SIZE[1]):
            raise ValueError('Tap target is outside the game screen')
    for x1, y1, x2, y2 in boxes:
        if not (0 <= x1 < x2 <= SIZE[0] and 0 <= y1 < y2 <= SIZE[1]):
            raise ValueError('Invalid detection region')
        if x2-x1 < 18 or y2-y1 < 15:
            raise ValueError('Detection regions should be at least 18×15 pixels')
        roi = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        if float(np.std(roi)) < 12:
            raise ValueError('Detection region is too plain; choose distinctive artwork or text')
    folder = Path(folder)
    manifest = folder / 'manifest.json'
    content = json.loads(manifest.read_text(encoding='utf-8'))
    if key is not None and (key not in content['states'] or key not in content['actions']):
        raise ValueError('Saved state no longer exists; reload the workflow')
    state_key = key or ('STEP_' + uuid4().hex[:12].upper())
    old_images = (_unshared_step_images(folder, content, key)
                  if key is not None else [])
    (folder / 'anchors').mkdir(exist_ok=True)
    (folder / 'captures').mkdir(exist_ok=True)
    # File names must never contain a manifest-supplied state key: imported
    # workflows can have arbitrary keys, including traversal characters.
    stamp = uuid4().hex
    capture_name = f'capture_{stamp}.jpg'
    anchor_names = [f'anchor_{stamp}_{index}.png' for index in range(len(boxes))]
    # Check both destination roots before the first write. An externally
    # replaced anchors/captures directory must not redirect new image output.
    capture_path = _safe_saved_image(folder, 'captures', capture_name)
    anchor_paths = [_safe_saved_image(folder, 'anchors', name)
                    for name in anchor_names]
    probes = []
    created = []
    try:
        for index, (x1, y1, x2, y2) in enumerate(boxes):
            filename = anchor_names[index]
            image = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
            path = anchor_paths[index]
            if not cv2.imwrite(str(path), image):
                raise OSError(f'Cannot save region image: {filename}')
            created.append(path)
            probes.append({'file': filename, 'rect': [x1, y1, x2, y2]})
        path = capture_path
        if not cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 89]):
            raise OSError('Cannot save state screenshot')
        created.append(path)
        content['states'][state_key] = probes
        action = {'label': label, 'match': 'all' if legacy_and else 'any',
                  'capture': capture_name, 'end': bool(end), 'retry': bool(retry),
                  'passive': bool(passive)}
        if pre_tap_delay_seconds:
            action['pre_tap_delay_seconds'] = pre_tap_delay_seconds
        if retry_mode == 'on_return':
            action['retry_mode'] = retry_mode
        if retry_interval_seconds != 25:
            action['retry_interval_seconds'] = retry_interval_seconds
        if max_extra_taps:
            action['max_extra_taps'] = max_extra_taps
        if action_chain is not None:
            action['action_chain'] = [dict(step) for step in action_chain]
        if conditions:
            action['conditions'] = [dict(condition) for condition in conditions]
        if condition_only:
            action['skip_template'] = True
        if gestures is not None:
            # Passive states have no gestures; others use the modern format.
            action['gestures'] = [dict(gesture) for gesture in gestures]
        else:
            action['tap'] = list(taps[0])
            action['taps'] = [list(tap) for tap in taps]
        content['actions'][state_key] = action
        content['order'] = step_order(content)
        _write_manifest(manifest, content)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    _prune_old_images(old_images)
    return state_key


def remove_step(folder: Path, key: str) -> None:
    folder = Path(folder)
    file = folder / 'manifest.json'
    content = json.loads(file.read_text(encoding='utf-8'))
    # Check references before the manifest changes, not after a file is deleted.
    images = _unshared_step_images(folder, content, key)
    content['states'].pop(key)
    content['actions'].pop(key)
    content['order'] = step_order(content)
    _write_manifest(file, content)
    _prune_old_images(images)
