"""Game-agnostic, state-first OCR/image automation engine.

One normalized screenshot, one matching pass, one bounded action at a time.
There is no game-, dungeon-, or matchmaking-specific dispatch logic.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from inspect import getattr_static
from typing import Callable
from pathlib import Path
from threading import RLock

import cv2
import numpy as np

from swordx.adb import ADBError
from .devices import MAX_CAPTURE_AGE_SECONDS, android_text_argument
from swordx.detection import SIZE, ScreenDetector, capture_crop_top, normalize
from .visual import MAX_SAVED_IMAGE_BYTES


class WorkflowError(ValueError):
    pass


class InputPaused(WorkflowError):
    """An in-flight step attempted an action after pause was requested."""



class OCREngine:
    """Single shared RapidOCR engine for Studio autofill and workflow matching."""
    _instance = None
    _checked = False
    _error = ''
    _lock = RLock()

    @classmethod
    def read(cls, roi: np.ndarray) -> str:
        with cls._lock:
            if not cls._checked:
                cls._checked = True
                try:
                    from rapidocr import RapidOCR
                    cls._instance = RapidOCR()
                    cls._error = ''
                except (ImportError, OSError, RuntimeError) as exc:
                    cls._instance = None
                    cls._error = f'{type(exc).__name__}: {exc}'
            if cls._instance is None:
                raise WorkflowError(
                    'OCR is unavailable. Run setup.bat to install all requirements '
                    f'(rapidocr and onnxruntime). {cls._error}')
            try:
                result = cls._instance(roi)
            except Exception as exc:
                raise WorkflowError(f'OCR inference failed: {type(exc).__name__}: {exc}') from exc
            # Maintained rapidocr 3.x returns a typed object with .txts.
            return ' '.join(str(text) for text in (result.txts or ()))


@dataclass(frozen=True)
class Match:
    key: str
    label: str
    confidence: float
    passive: bool = False
    trigger: int = 0


UNKNOWN = Match('', 'Unknown screen', 0.0)

def _rect(frame: np.ndarray, bounds: list[int]) -> np.ndarray:
    if len(bounds) != 4:
        raise WorkflowError('Detector rectangle must have four coordinates')
    x1, y1, x2, y2 = map(int, bounds)
    if not 0 <= x1 < x2 <= SIZE[0] or not 0 <= y1 < y2 <= SIZE[1]:
        raise WorkflowError(f'Detector region is outside the normalized screen: {bounds}')
    return frame[y1:y2, x1:x2]


def validate_ocr_expression(text: str, regex: bool = False) -> str:
    """Reject invalid OCR rules before they reach the live state machine."""
    if not isinstance(text, str):
        raise WorkflowError('OCR condition text must be a string')
    expression = text
    if not expression.strip():
        raise WorkflowError('OCR condition must contain text')
    if regex:
        try:
            re.compile(expression, re.IGNORECASE)
        except re.error as exc:
            raise WorkflowError(f'Invalid OCR regular expression: {exc}') from exc
    return expression


def validate_condition(condition: dict) -> None:
    """Check a saved or draft recognition rule without OCR, pixels or input."""
    if not isinstance(condition, dict):
        raise WorkflowError('Recognition condition must be an object')
    kind = condition.get('type')
    for flag in ('regex', 'negate'):
        if flag in condition and type(condition[flag]) is not bool:
            raise WorkflowError(
                f'Recognition {flag} must be true or false')
    rect = condition.get('rect', [0, 0, *SIZE])
    if (not isinstance(rect, (list, tuple)) or len(rect) != 4 or
            any(type(v) is not int for v in rect)):
        raise WorkflowError('Recognition zone must have four integer coordinates')
    x1, y1, x2, y2 = rect
    if not (0 <= x1 < x2 <= SIZE[0] and 0 <= y1 < y2 <= SIZE[1]):
        raise WorkflowError('Recognition zone is outside the normalized screen')
    if kind == 'ocr':
        mode = condition.get('text_match', 'contains')
        if mode not in ('contains', 'equals', 'starts_with', 'ends_with'):
            raise WorkflowError('OCR text comparison must be contains, equals, starts_with or ends_with')
        if condition.get('regex') and mode != 'contains':
            raise WorkflowError('OCR regex cannot be combined with a text comparison')
        validate_ocr_expression(condition.get('text', ''), bool(condition.get('regex')))
    elif kind in ('color', 'pixel'):
        bgr = condition.get('bgr')
        if (not isinstance(bgr, (list, tuple)) or len(bgr) != 3 or
                any(type(v) is not int or not 0 <= v <= 255 for v in bgr)):
            raise WorkflowError('Color condition needs three BGR values from 0 to 255')
        tolerance = condition.get('tolerance', 25)
        if (type(tolerance) not in (int, float) or
                not math.isfinite(tolerance) or not 0 <= tolerance <= 255):
            raise WorkflowError('Color tolerance must be a finite number from 0 to 255')
        if kind == 'pixel':
            point = condition.get('point')
            if (not isinstance(point, (list, tuple)) or len(point) != 2 or
                    any(type(v) is not int for v in point) or
                    not (0 <= point[0] < SIZE[0] and 0 <= point[1] < SIZE[1])):
                raise WorkflowError('Selected pixel is outside the normalized screen')
    else:
        raise WorkflowError(f'Unknown detector type: {kind}')


def preview_conditions(frame: np.ndarray, conditions: list[dict]) -> list[bool]:
    """Test unsaved Studio rules against pixels without loading or executing actions."""
    return [item['passed'] for item in preview_condition_details(frame, conditions)]


def preview_condition_details(frame: np.ndarray, conditions: list[dict]) -> list[dict]:
    """Return input-free rule results and measurements; never include OCR contents."""
    if not conditions:
        raise WorkflowError('Add at least one recognition condition to test')
    if (not isinstance(frame, np.ndarray) or frame.ndim != 3 or
            frame.shape[2] != 3 or frame.dtype != np.uint8 or not frame.size):
        raise WorkflowError('Capture a valid color screenshot before testing conditions')
    for rule in conditions:
        validate_condition(rule)
    image = frame if frame.shape == (SIZE[1], SIZE[0], 3) else normalize(frame)
    cache: dict = {}
    results = []
    for rule in conditions:
        observation: dict = {}
        passed = bool(_condition(image, rule, cache, observation))
        results.append({'passed': passed, 'explanation': observation['explanation']})
    return results


def _condition(frame: np.ndarray, condition: dict, ocr_cache: dict,
               observation: dict | None = None) -> float:
    kind = condition.get('type')
    rect = condition.get('rect', [0, 0, *SIZE])
    roi = _rect(frame, rect)
    if kind == 'color':
        expected = np.asarray(condition['bgr'], dtype=np.float32)
        if expected.shape != (3,):
            raise WorkflowError('Color condition expects three BGR components')
        tolerance = float(condition.get('tolerance', 25))
        difference = float(np.max(np.abs(roi.mean(axis=(0, 1)) - expected)))
        passed = difference <= tolerance
        if observation is not None:
            observation['explanation'] = (
                f'Max color difference {difference:.1f}; tolerance {tolerance:g}')
    elif kind == 'ocr':
        expression = validate_ocr_expression(
            condition.get('text', ''), bool(condition.get('regex')))
        region = tuple(rect)
        if region not in ocr_cache:
            ocr_cache[region] = OCREngine.read(roi)
        found = ocr_cache[region]
        if condition.get('regex'):
            passed = re.search(expression, found, re.IGNORECASE) is not None
        else:
            expected, actual = expression.casefold(), found.casefold()
            mode = condition.get('text_match', 'contains')
            if mode == 'equals':
                passed = actual == expected
            elif mode == 'starts_with':
                passed = actual.startswith(expected)
            elif mode == 'ends_with':
                passed = actual.endswith(expected)
            else:
                passed = expected in actual
        if observation is not None:
            observation['explanation'] = (
                f'OCR recognized {len(found)} characters (contents hidden)'
                if found.strip() else 'OCR recognized no text')
    elif kind == 'pixel':
        x, y = map(int, condition['point'])
        if not 0 <= x < SIZE[0] or not 0 <= y < SIZE[1]:
            raise WorkflowError('Pixel point is outside the normalized screen')
        expect = np.asarray(condition['bgr'], dtype=np.float32)
        difference = float(np.max(np.abs(frame[y, x].astype(float) - expect)))
        tolerance = float(condition.get('tolerance', 25))
        passed = difference <= tolerance
        if observation is not None:
            observation['explanation'] = (
                f'Pixel max color difference {difference:.1f}; tolerance {tolerance:g}')
    else:
        raise WorkflowError(f'Unknown detector type: {kind}')
    if observation is not None:
        observation['explanation'] += (
            f'; base check {"passed" if passed else "failed"}'
            + ('; NOT reversed result' if condition.get('negate') else ''))
    if condition.get('negate'):
        passed = not passed
    return 1.0 if passed else 0.0


def validate_recognition_manifest(manifest: dict) -> None:
    """Reject unsafe state matching configuration once, before running OCR.

    A missing template must never silently turn an image-matching state into a
    condition-only state, and NaN thresholds must never bypass image checks.
    """
    states = manifest.get('states')
    actions = manifest.get('actions')
    if not isinstance(states, dict) or not isinstance(actions, dict):
        raise WorkflowError('Workflow must contain states and actions')
    if set(states) != set(actions):
        raise WorkflowError('Workflow state keys and action keys must match')

    def finite_number(value, description: str, *, unit_interval: bool = False):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise WorkflowError(f'{description} must be a finite number')
        if unit_interval and not 0 <= value <= 1:
            raise WorkflowError(f'{description} must be between 0 and 1')

    finite_number(manifest.get('threshold', 0.82),
                  'Workflow image threshold', unit_interval=True)
    for key, probes in states.items():
        action = actions[key]
        if not isinstance(action, dict) or not isinstance(probes, list):
            raise WorkflowError(f'State {key!r} must define action and image probe data')
        label = action.get('label', key)
        prefix = f'State {label!r}'
        for flag in ('passive', 'skip_template', 'retry', 'end'):
            if flag in action and type(action[flag]) is not bool:
                raise WorkflowError(f'{prefix}: {flag} must be true or false')
        timing_limits = (
            ('pre_tap_delay_seconds', 0, 86400),
            ('retry_interval_seconds', 1, 86400),
            ('max_extra_taps', 0, 20),
        )
        for field, minimum, maximum in timing_limits:
            if field in action:
                value = action[field]
                if type(value) is not int or not minimum <= value <= maximum:
                    raise WorkflowError(
                        f'{prefix}: {field} must be a whole number '
                        f'between {minimum} and {maximum}')
        if action.get('retry_mode', 'interval') not in ('interval', 'on_return'):
            raise WorkflowError(
                f'{prefix}: retry_mode must be interval or on_return')
        mode = action.get('match', 'all')
        if mode not in ('all', 'any'):
            raise WorkflowError(f'{prefix}: image match mode must be all or any')
        if action.get('condition_match', 'all') not in ('all', 'any'):
            raise WorkflowError(f'{prefix}: condition match mode must be all or any')
        finite_number(action.get('threshold', manifest.get('threshold', 0.82)),
                      f'{prefix} image threshold', unit_interval=True)
        finite_number(action.get('priority', 0), f'{prefix} priority')
        conditions = action.get('conditions', [])
        if not isinstance(conditions, list):
            raise WorkflowError(f'{prefix}: recognition conditions must be a list')
        for index, rule in enumerate(conditions, 1):
            try:
                validate_condition(rule)
            except WorkflowError as exc:
                raise WorkflowError(f'{prefix}, condition {index}: {exc}') from exc
        for index, probe in enumerate(probes, 1):
            description = f'{prefix}, template {index}'
            if not isinstance(probe, dict):
                raise WorkflowError(f'{description}: image probe must be an object')
            name = probe.get('file')
            if (not isinstance(name, str) or not name or
                    name in ('.', '..') or '/' in name or '\\' in name or
                    ':' in name or '\x00' in name):
                raise WorkflowError(f'{description}: unsafe anchor filename')
            rect = probe.get('rect')
            if (not isinstance(rect, (list, tuple)) or len(rect) != 4 or
                    any(type(v) is not int for v in rect)):
                raise WorkflowError(
                    f'{description}: image region needs four integer coordinates')
            x1, y1, x2, y2 = rect
            if not (0 <= x1 < x2 <= SIZE[0] and 0 <= y1 < y2 <= SIZE[1]):
                raise WorkflowError(
                    f'{description}: image region is outside the normalized screen')
        if action.get('skip_template'):
            if not conditions:
                raise WorkflowError(
                    f'{prefix}: condition-only recognition needs a condition')
        elif not probes:
            raise WorkflowError(
                f'{prefix}: image matching requires a captured template '
                '(enable condition-only recognition explicitly)')


class OmniDetector:
    """Fast legacy template matching + optional OCR/color/negative conditions.

    Templates are compiled once. OCR runs only for states whose image probes
    passed their threshold, avoiding an OCR call per state per emulator frame.
    """
    def __init__(self, folder: Path):
        import json
        self.folder = Path(folder)
        self.manifest = json.loads((self.folder / 'manifest.json').read_text(encoding='utf-8'))
        validate_recognition_manifest(self.manifest)
        self.actions = self.manifest['actions']
        self.threshold = float(self.manifest.get('threshold', 0.82))
        # Check path containment before invoking either the legacy or mixed
        # template loader. Neither can safely consume untrusted paths itself.
        anchors_root = (self.folder / 'anchors').resolve()
        if not anchors_root.is_relative_to(self.folder.resolve()):
            raise WorkflowError('Saved anchors directory resolves outside workflow')
        for key, probes in self.manifest['states'].items():
            for index, probe in enumerate(probes, 1):
                path = self.folder / 'anchors' / probe['file']
                label = self.actions[key].get('label', key)
                if not path.resolve().is_relative_to(anchors_root):
                    raise WorkflowError(
                        f'State {label!r}, template {index}: '
                        'anchor resolves outside workflow')
                try:
                    size = path.stat().st_size
                except OSError as exc:
                    raise WorkflowError(
                        f'State {label!r}, template {index}: '
                        'saved anchor is missing or inaccessible') from exc
                if not 0 < size <= MAX_SAVED_IMAGE_BYTES:
                    raise WorkflowError(
                        f'State {label!r}, template {index}: '
                        'saved anchor must be nonempty and at most 32 MiB')
        self.legacy = ScreenDetector(folder) if all(self.manifest['states'].values()) else None
        self.templates = {} if self.legacy is None else self.legacy.templates
        if self.legacy is None:
            # Allow OCR-only or pixel-only states while retaining image templates
            # for any other states in the same workflow.
            from swordx.detection import Anchor
            for key, probes in self.manifest['states'].items():
                self.templates[key] = []
                for probe in probes:
                    file = self.folder / 'anchors' / probe['file']
                    image = cv2.imread(str(file), cv2.IMREAD_GRAYSCALE)
                    if image is None:
                        raise WorkflowError(f'Missing template: {file}')
                    self.templates[key].append(Anchor(image, tuple(probe['rect'])))
        for key, anchors in self.templates.items():
            for index, anchor in enumerate(anchors, 1):
                x1, y1, x2, y2 = anchor.rect
                if (anchor.gray.ndim != 2 or anchor.gray.dtype != np.uint8 or
                        anchor.gray.shape != (y2 - y1, x2 - x1)):
                    raise WorkflowError(
                        f'State {self.actions[key].get("label", key)!r}, '
                        f'template {index}: saved image size differs from its region')
        if not self.actions:
            raise WorkflowError('The workflow contains no recorded states')
        self._previous = ''

    @staticmethod
    def _match_template(frame: np.ndarray, anchor) -> float:
        # TM_CCOEFF_NORMED gives 1.0 for a constant template regardless of
        # what is on screen. Use absolute error in that case instead.
        if float(anchor.gray.std()) >= 2.0:
            return ScreenDetector._match(frame, anchor)
        x1,y1,x2,y2 = anchor.rect
        m = 8
        crop = frame[max(0,y1-m):min(SIZE[1],y2+m),
                     max(0,x1-m):min(SIZE[0],x2+m)]
        if crop.shape[0] < anchor.gray.shape[0] or crop.shape[1] < anchor.gray.shape[1]:
            return 0.0
        # TM_SQDIFF_NORMED fails for an all-black template because its
        # denominator is zero (identical black regions score as a mismatch).
        # Raw squared error has no division by the template's energy.
        squared = cv2.matchTemplate(crop, anchor.gray, cv2.TM_SQDIFF)
        rmse = (max(0.0, float(cv2.minMaxLoc(squared)[0])) / anchor.gray.size) ** 0.5
        return max(0.0, 1.0 - rmse / 80.0)

    def detect(self, frame: np.ndarray) -> Match:
        """Fast production recognition; no trace allocation or extra OCR."""
        return self._evaluate(frame, explain=False)[0]

    def inspect(self, frame: np.ndarray) -> tuple[Match, list[dict]]:
        """Explain saved-state selection without executing actions or changing files.

        Uses the exact production matching and priority path. Conditions behind
        a failed image threshold are deliberately not evaluated or reported as
        passing; raw OCR output is not included in inspection results.
        """
        return self._evaluate(frame, explain=True)

    def _evaluate(self, frame: np.ndarray, *, explain: bool) -> tuple[Match, list[dict]]:
        # OmniRunner already normalized this screenshot. Preserve one resize
        # and one OCR cache for all states, also during read-only inspection.
        image = (frame if frame.shape == (SIZE[1], SIZE[0], 3)
                 else normalize(frame))
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        cache: dict = {}
        candidates: list[Match] = []
        details: list[dict] = []
        for key, action in self.actions.items():
            probes = () if action.get('skip_template') else self.templates.get(key, ())
            if probes:
                scores = [self._match_template(gray, anchor) for anchor in probes]
                if action.get('match', 'all') == 'any':
                    trigger = int(np.argmax(scores))
                    confidence = scores[trigger]
                else:
                    trigger = int(np.argmin(scores))
                    confidence = min(scores)
            else:
                confidence, trigger = 1.0, 0
            threshold = float(action.get('threshold', self.threshold))
            conditions = action.get('conditions', [])
            detail = {
                'key': key,
                'label': action.get('label', key),
                'passive': bool(action.get('passive', False)),
                'has_template': bool(probes),
                'template_confidence': float(confidence),
                'threshold': threshold,
                'condition_results': None,
                'condition_evidence': None,
                'match_confidence': None,
                'rank_reason': '',
                'status': 'template below threshold',
            } if explain else None
            if confidence < threshold:
                if explain:
                    details.append(detail)
                continue
            if not probes and not conditions:
                raise WorkflowError(f'State {key} needs a template or a condition')
            if conditions:
                if explain:
                    evidence = []
                    outcomes = []
                    for cond in conditions:
                        observation = {}
                        outcomes.append(_condition(image, cond, cache, observation))
                        evidence.append(observation['explanation'])
                    detail['condition_evidence'] = evidence
                else:
                    outcomes = [_condition(image, cond, cache) for cond in conditions]
                passed = (all(outcomes) if action.get('condition_match', 'all') == 'all'
                          else any(outcomes))
                confidence = min(confidence, float(passed))
                if explain:
                    detail['condition_results'] = [bool(value) for value in outcomes]
                if not passed:
                    if explain:
                        detail['status'] = 'conditions failed'
                        details.append(detail)
                    continue
            candidates.append(Match(key, action.get('label', key), confidence,
                                    bool(action.get('passive', False)), trigger))
            if explain:
                detail['status'] = 'matched'
                detail['match_confidence'] = float(confidence)
                details.append(detail)
        # A matching passive WAIT state overrides actionable states, then
        # confidence, priority and key break ties exactly as in production.
        winner = max(candidates, key=lambda m: (m.passive, m.confidence,
                                                 float(self.actions[m.key].get('priority', 0)), m.key)
                     ) if candidates else UNKNOWN
        if explain:
            winner_priority = float(self.actions[winner.key].get('priority', 0)) if winner.key else 0
            for detail in details:
                if detail['status'] != 'matched':
                    continue
                if detail['key'] == winner.key:
                    detail['status'] = 'selected'
                    continue
                if winner.passive and not detail['passive']:
                    detail['status'] = 'overridden by WAIT'
                    detail['rank_reason'] = 'passive WAIT takes precedence'
                elif winner.confidence > detail['match_confidence']:
                    detail['status'] = 'lower-ranked match'
                    detail['rank_reason'] = (
                        f'confidence {detail["match_confidence"]:.1%} '
                        f'below selected {winner.confidence:.1%}')
                else:
                    priority = float(self.actions[detail['key']].get('priority', 0))
                    detail['status'] = 'lower-ranked match'
                    detail['rank_reason'] = (
                        f'priority {priority:g} below selected {winner_priority:g}'
                        if priority < winner_priority
                        else 'stable state-key tie-break')
        return winner, details


def _target(point, device_size: tuple[int, int], crop_top: int = 0) -> tuple[int, int]:
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise WorkflowError('Action point must have two coordinates')
    if any(type(value) not in (int, float) or not math.isfinite(value)
           for value in point):
        raise WorkflowError('Action coordinates must be finite numbers')
    w, h = device_size
    x, y = point
    # Preserve legacy right/bottom edge coordinates. Device mapping clamps
    # inclusive edges to valid pixels, including on cropped/rotated screens.
    if not 0 <= x <= SIZE[0] or not 0 <= y <= SIZE[1]:
        raise WorkflowError(f'Point {point} outside 540×960 normalized screen')
    if not 0 <= crop_top < h:
        raise WorkflowError('Invalid screenshot crop geometry')
    return (max(0, min(w - 1, round(x * w / SIZE[0]))),
            max(0, min(h - 1, crop_top + round(y * (h - crop_top) / SIZE[1]))))


def resolve_action_chain(action: dict, region_count: int, trigger: int = 0) -> list[dict]:
    """Select the same action steps for live execution and read-only replay.

    Older paired gestures are alternatives selected by the winning image
    anchor, while modern action chains are always sequential.
    """
    if action.get('passive'):
        return []
    if 'action_chain' in action:
        return list(action['action_chain'])
    gestures = action.get('gestures')
    if gestures is None:
        taps = action.get('taps') or ([action['tap']] if 'tap' in action else [])
        gestures = [{'kind': 'tap', 'point': p} for p in taps if p is not None]
    if len(gestures) > 1 and region_count > 1:
        return [gestures[min(trigger, len(gestures) - 1)]]
    return list(gestures)


def validate_action(action: dict) -> str:
    """Fail closed on invalid action data before live or dry-run execution.

    Validate normalized targets without consulting a device or sending input.
    Real commands still perform their normal ownership/freshness checks.
    """
    if not isinstance(action, dict):
        raise WorkflowError('Action step must be an object')
    kind = action.get('kind')
    try:
        if kind in ('tap', 'double_tap'):
            _target(action['point'], SIZE)
        elif kind in ('hold', 'drag', 'swipe'):
            _target(action.get('start', action.get('point')), SIZE)
            _target(action.get('end', action.get('point')), SIZE)
            duration = action.get('duration_ms', 650)
            if type(duration) is not int or not 50 <= duration <= 10000:
                raise WorkflowError('Gesture duration must be a whole number from 50–10000 ms')
        elif kind == 'key':
            code = action.get('code', 4)
            if type(code) is not int or not 0 <= code <= 300:
                raise WorkflowError('Invalid Android key event (integer 0–300 required)')
        elif kind in ('text', 'field_text'):
            android_text_argument(action.get('text', ''))
            if kind == 'field_text':
                _target(action['point'], SIZE)
        elif kind == 'wait':
            seconds = action.get('seconds', 0)
            if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 <= seconds <= 86400:
                raise WorkflowError('Wait duration must be a finite number from 0–86400 seconds')
        elif kind not in ('launch', 'count_run'):
            raise WorkflowError(f'Unknown action: {kind}')
    except WorkflowError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowError(f'Invalid {kind} action: {exc}') from exc
    return kind


def validate_saved_action_chains(manifest: dict) -> None:
    """Preflight every executable step before any workflow touches the device.

    Validate every legacy alternative, not just the trigger selected on the
    first screenshot. Keep per-step checks during execution for defense in depth.
    """
    for key, action in manifest['actions'].items():
        if action.get('passive'):
            continue
        label = action.get('label', key)
        if 'action_chain' in action:
            configured = action['action_chain']
        elif action.get('gestures') is not None:
            configured = action['gestures']
        elif action.get('taps') is not None:
            configured = action['taps']
        else:
            configured = None
        if configured is not None and not isinstance(configured, list):
            raise WorkflowError(f'State {label!r}: action steps must be a list')
        # Supplying one region yields the complete legacy gesture list instead
        # of one selected alternative, while explicit chains remain sequential.
        try:
            steps = resolve_action_chain(action, 1)
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkflowError(f'State {label!r}: invalid action steps: {exc}') from exc
        if not steps:
            raise WorkflowError(
                f'State {label!r}: actionable state needs at least one action')
        for index, step in enumerate(steps, 1):
            try:
                validate_action(step)
            except WorkflowError as exc:
                raise WorkflowError(
                    f'State {label!r}, action {index}: {exc}') from exc


class OmniRunner:
    """One emulator, one state machine. Doesn't know about other sessions."""
    def __init__(self, device, folder: Path, package: str, *, log=lambda _: None,
                 completed=lambda _: None, image=lambda *_: None,
                 auto_relaunch: bool = True, dry_run: bool = False,
                 can_act: Callable[[], bool] = lambda: True,
                 is_paused: Callable[[], bool] = lambda: False):
        self.detector = OmniDetector(folder)
        validate_saved_action_chains(self.detector.manifest)
        self.device = device
        self.package = package
        self.log, self.on_completed, self.on_image = log, completed, image
        self.auto_relaunch = auto_relaunch
        self.dry_run = dry_run
        self.can_act = can_act
        self.is_paused = is_paused
        self.device_size = None
        self._capture_geometry: tuple[int, int] | None = None
        self._capture_crop_top = 0
        self.current = ''
        self.last_recognized = ''
        self.unknown_count = 0
        self.entered_at = 0.0
        self.action_at = 0.0
        self.attempts = 0
        self.chain_index = 0
        self.chain_wait_until = 0.0
        self.end_pending = False
        self.runs = 0
        self.last_game_check = 0.0
        self.missing_since: float | None = None
        self.last_launch = float('-inf')
        self.last_observation = UNKNOWN
        self.last_frame: np.ndarray | None = None
        # Conservative preview age starts before ADB acquisition, not when
        # recognition eventually publishes the captured pixels.
        self.last_capture_started_at = 0.0
        self.last_capture_latency_ms: float | None = None
        self.last_capture_stages_ms: dict[str, float] | None = None
        self._stable_key = ''
        self._streak = 0
        self._acted = False
        self.wait_remaining: float | None = None

    def _chain(self, key: str, trigger: int) -> list[dict]:
        return resolve_action_chain(
            self.detector.actions[key],
            len(self.detector.manifest['states'].get(key, [])),
            trigger)

    def _map_point(self, point) -> tuple[int, int]:
        """Map detector coordinates back through its screenshot viewport."""
        return _target(point, self.device_size, self._capture_crop_top)

    def _require_action_permission(self) -> None:
        if not self.can_act():
            raise WorkflowError('Device ownership lost or automation stopped; refusing input')
        if self.is_paused():
            raise InputPaused('Session paused; deferring input until state is reconfirmed')

    def _require_fresh_capture(self) -> None:
        """A blocked first command must not authorize a later command."""
        age = time.monotonic() - self.last_capture_started_at
        if age > MAX_CAPTURE_AGE_SECONDS:
            raise ADBError(
                f'Captured frame is stale during input ({age:.2f}s > '
                f'{MAX_CAPTURE_AGE_SECONDS:.0f}s); refusing further input')

    def _require_device_input(self) -> None:
        """Validate ownership and capture age immediately before Android I/O."""
        self._require_action_permission()
        self._require_fresh_capture()

    def _execute(self, action: dict, now: float) -> bool:
        # Every step must retain session ownership, including waits, dry runs
        # and run counters that do not send Android commands.
        self._require_action_permission()
        kind = validate_action(action)
        if kind == 'wait':
            seconds = float(action.get('seconds', 0))
            if self.dry_run:
                self.log(f'DRY RUN: step {self.chain_index + 1} on '
                         f'{self.last_observation.label}: wait {seconds:g}s (no input)')
            self.chain_wait_until = now + seconds
            self.chain_index += 1
            return False
        if self.dry_run:
            self.log(f'DRY RUN: step {self.chain_index + 1} on '
                     f'{self.last_observation.label}: {kind} (no input)')
            self.chain_index += 1
            return True
        if kind in ('tap', 'double_tap'):
            x, y = self._map_point(action['point'])
            self._require_device_input()
            self.device.tap(x, y)
            if kind == 'double_tap':
                # Recheck ownership and freshness before a follow-up touch.
                self._require_device_input()
                self.device.tap(x, y)
        elif kind in ('drag', 'swipe', 'hold'):
            start = self._map_point(action.get('start', action.get('point')))
            end = self._map_point(action.get('end', action.get('point')))
            duration = int(action.get('duration_ms', 650))
            self._require_device_input()
            self.device.swipe(*start, *end, duration)
        elif kind == 'key':
            code = int(action.get('code', 4))
            self._require_device_input()
            self.device.keyevent(code)
        elif kind in ('text', 'field_text'):
            text = str(action.get('text', ''))
            if kind == 'field_text':
                # Select the recorded input field; check ownership again before
                # the second command so lease revocation never types into it.
                x, y = self._map_point(action['point'])
                self._require_device_input()
                self.device.tap(x, y)
            self._require_device_input()
            self.device.input_text(text)
        elif kind == 'launch':
            self._require_device_input()
            self.device.launch(self.package)
        elif kind == 'count_run':
            self._finish_run()
        else:
            raise WorkflowError(f'Unknown action: {kind}')
        self.chain_index += 1
        self.log(f'{self.last_observation.label}: {kind}')
        return True

    def _finish_run(self) -> None:
        # End-of-state transitions can mark completion without a new touch.
        # Revoked ownership or Pause must never count an unobserved run.
        self._require_action_permission()
        self.runs += 1
        self.on_completed(self.runs)

    def _monitor_game(self, now: float) -> None:
        if self.dry_run or not self.auto_relaunch or not self.package or now - self.last_game_check < 10:
            return
        self.last_game_check = now
        foreground = self.device.foreground()
        if foreground == self.package:
            self.missing_since = None
            return
        alive = self.device.running(self.package)
        if alive is not False:
            self.missing_since = None
            return
        if self.missing_since is None:
            self.missing_since = now
            return
        if now - self.missing_since >= 10 and now - self.last_launch >= 25:
            # Foreground and process probes are ADB operations themselves.
            # Never relaunch using an observation that aged out during them.
            self._require_device_input()
            self.log(f'{self.package}: confirmed stopped; relaunching via ADB')
            self.device.launch(self.package)
            self.last_launch = now
            self.missing_since = None
            # The game may have changed screen while launching. Reconfirm
            # with two new captures before any subsequent workflow input.
            self._discard_capture(now)

    def _invalidate_visual_state(self, now: float) -> None:
        """A failed I/O attempt makes the previous state/action progress uncertain.

        Re-arm only after two new matching screenshots. In particular, never
        complete a run based on a screen transition that was not observed.
        """
        self._stable_key = ''
        self._streak = 0
        self.current = ''
        self.entered_at = now
        self.action_at = now
        self.chain_index = 0
        self.chain_wait_until = 0
        self.attempts = 0
        self.end_pending = False
        self._acted = False
        self.unknown_count = 0

    def _discard_capture(self, now: float) -> None:
        """Discard a frame that cannot safely authorize the next action."""
        self._invalidate_visual_state(now)
        self.last_frame = None
        self.last_observation = UNKNOWN
        self.last_capture_latency_ms = None
        self.last_capture_stages_ms = None

    def resume_after_pause(self, now: float) -> None:
        """Discard visual evidence and incomplete actions from before a pause."""
        self._invalidate_visual_state(now)
        self.last_frame = None
        self.last_observation = UNKNOWN
        self.wait_remaining = None
        self.log('Session resumed; reconfirming state before input')

    def step(self, now: float | None = None) -> Match:
        now = time.monotonic() if now is None else now
        self.wait_remaining = None
        capture_started = time.monotonic()
        self.last_capture_started_at = capture_started
        self.last_capture_latency_ms = None
        self.last_capture_stages_ms = None
        try:
            # Built-in MuMu devices expose per-stage PNG timings. Custom
            # adapters keep the unchanged screenshot() compatibility path.
            # Only use a method actually defined on the adapter. Dynamic
            # mocks/proxies can synthesize callable attributes on lookup.
            defined = getattr_static(self.device, 'screenshot_timed', None)
            if callable(defined):
                raw, self.last_capture_stages_ms = self.device.screenshot_timed()
            else:
                raw = self.device.screenshot()
            # Device adapters can return invalid frames without raising ADBError.
            # Reject them before OCR, geometry updates or any Android input.
            if (not isinstance(raw, np.ndarray) or raw.ndim != 3 or
                    raw.shape[2] != 3 or raw.dtype != np.uint8 or not raw.size):
                raise ADBError('ADB screenshot was empty or unreadable')
            try:
                frame = normalize(raw)
            except (cv2.error, ValueError) as exc:
                raise ADBError(f'ADB screenshot normalization failed: {exc}') from exc
            if frame.shape != (SIZE[1], SIZE[0], 3) or frame.dtype != np.uint8:
                raise ADBError('ADB screenshot normalization returned invalid pixels')
            capture_age = time.monotonic() - capture_started
            if capture_age > MAX_CAPTURE_AGE_SECONDS:
                raise ADBError(
                    f'Captured frame is stale ({capture_age:.2f}s > '
                    f'{MAX_CAPTURE_AGE_SECONDS:.0f}s); refusing input')
            # Acquisition + normalization measured by the same monotonic
            # clock used for input freshness; no extra device or clock call.
            self.last_capture_latency_ms = max(0.0, capture_age) * 1000
        except (ADBError, OSError):
            self._discard_capture(now)
            raise
        self.last_frame = frame
        # ADB `wm size` can report an old base orientation. Use screenshot
        # geometry, and never continue an action chain across a rotation or
        # resolution change on the strength of stale state recognition.
        geometry = (raw.shape[1], raw.shape[0])
        crop_top = capture_crop_top(*geometry)
        changed = (self._capture_geometry is not None and
                   (geometry != self._capture_geometry or
                    crop_top != self._capture_crop_top))
        self._capture_geometry = geometry
        self._capture_crop_top = crop_top
        self.device_size = geometry
        if changed:
            self.log(f'Display geometry changed to {geometry[0]}x{geometry[1]}; '
                     'reconfirming state before input')
            self._invalidate_visual_state(now)
        try:
            observation = self.detector.detect(frame)
        except cv2.error as exc:
            # OpenCV matching can fail on a transient or corrupt source.
            # Retry with fresh frames instead of killing the worker or
            # retaining an already-authorized action sequence.
            self._discard_capture(now)
            raise ADBError(f'Screenshot recognition failed: {exc}') from exc
        self.last_observation = observation

        def require_fresh_observation() -> None:
            age = time.monotonic() - capture_started
            if age > MAX_CAPTURE_AGE_SECONDS:
                self._discard_capture(now)
                raise ADBError(
                    f'Captured frame is stale after recognition ({age:.2f}s > '
                    f'{MAX_CAPTURE_AGE_SECONDS:.0f}s); refusing input')

        # A delayed OCR result must not inflate valid-frame telemetry.
        require_fresh_observation()
        # Preview displays original source geometry, not normalized OCR canvas.
        try:
            self.on_image(observation, raw)
        except cv2.error as exc:
            self._discard_capture(now)
            raise ADBError(f'Screenshot preview processing failed: {exc}') from exc
        # A slow preview callback must not authorize input from stale pixels.
        require_fresh_observation()
        if observation.key != self._stable_key:
            self._stable_key, self._streak = observation.key, 1
        else:
            self._streak += 1
        # Stabilize recognition before sending any input. A transient unknown
        # frame does not unlock a toggle/queue state.
        if self._streak < 2:
            return observation
        if observation.key != self.current:
            if self.current and self.end_pending and not self.dry_run:
                self._finish_run()
            self.current = observation.key
            self.entered_at = now
            self.attempts = 0
            self.chain_index = 0
            self.chain_wait_until = 0
            self.end_pending = False
            self._acted = False
            if observation.key:
                self.log(f'State: {observation.label} ({observation.confidence:.0%})')
        if not observation.key:
            self.unknown_count += 1
            if self.unknown_count >= 3:
                try:
                    self._monitor_game(now)
                except (ADBError, OSError):
                    # A failed app probe or stale relaunch invalidates the
                    # previous stopped-game evidence and capture state.
                    self._discard_capture(now)
                    self.missing_since = None
                    raise
            return observation
        self.unknown_count = 0
        action = self.detector.actions[observation.key]
        if observation.passive:
            return observation
        chain = self._chain(observation.key, observation.trigger)
        if not chain:
            return observation
        retry = bool(action.get('retry', False))
        retry_mode = action.get('retry_mode', 'interval')
        max_attempts = 1 + (int(action.get('max_extra_taps', 0)) if retry else 0)
        if retry and retry_mode == 'interval' and max_attempts == 1 and 'max_extra_taps' not in action:
            # Legacy unlimited retries remain bounded per visit as a safety
            # improvement. Use explicit cap if repeated taps are required.
            max_attempts = 4
        delay = max(0, int(action.get('pre_tap_delay_seconds', 0)))
        retry_delay = max(1, int(action.get('retry_interval_seconds', 25)))
        if self.chain_index >= len(chain):
            # Retry only an unchanged actionable state. A visible Wait state
            # takes precedence in the detector; returning to Match re-arms it.
            if (not retry or retry_mode != 'interval' or
                    self.attempts >= max_attempts):
                return observation
            if now < self.action_at + retry_delay:
                self.wait_remaining = self.action_at + retry_delay - now
                return observation
            self.chain_index = 0
            self.chain_wait_until = 0
            self.attempts += 1
        elif not self.attempts:
            if now < self.entered_at + delay:
                self.wait_remaining = self.entered_at + delay - now
                return observation
            self.attempts = 1
        elif now < self.chain_wait_until:
            self.wait_remaining = self.chain_wait_until - now
            return observation
        if self.chain_index == 0:
            self.action_at = now
        try:
            executed = self._execute(chain[self.chain_index], now)
        except (ADBError, OSError):
            # An interrupted touch/typing command may have partially reached
            # Android. Do not silently advance or reuse an old recognition
            # streak; treat the next screenshot as a new state observation.
            self._invalidate_visual_state(now)
            self.last_observation = UNKNOWN
            raise
        if executed:
            self._acted = True
        if self.chain_index >= len(chain):
            self.end_pending = bool(action.get('end') and not any(step.get('kind') == 'count_run' for step in chain))
            self.chain_wait_until = now + 0.1
        return observation
