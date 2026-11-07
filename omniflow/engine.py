"""Game-agnostic, state-first OCR/image automation engine.

One normalized screenshot, one matching pass, one bounded action at a time.
There is no game-, dungeon-, or matchmaking-specific dispatch logic.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable
from pathlib import Path
from threading import RLock

import cv2
import numpy as np

from swordx.adb import ADBError
from .devices import android_text_argument
from swordx.detection import SIZE, ScreenDetector, normalize


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

# Do not send input based on screenshots delayed by a stalled ADB transport.
MAX_CAPTURE_AGE_SECONDS = 6.0


def _rect(frame: np.ndarray, bounds: list[int]) -> np.ndarray:
    if len(bounds) != 4:
        raise WorkflowError('Detector rectangle must have four coordinates')
    x1, y1, x2, y2 = map(int, bounds)
    if not 0 <= x1 < x2 <= SIZE[0] or not 0 <= y1 < y2 <= SIZE[1]:
        raise WorkflowError(f'Detector region is outside the normalized screen: {bounds}')
    return frame[y1:y2, x1:x2]


def _condition(frame: np.ndarray, condition: dict, ocr_cache: dict) -> float:
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
    elif kind == 'ocr':
        region = tuple(rect)
        if region not in ocr_cache:
            ocr_cache[region] = OCREngine.read(roi)
        found = ocr_cache[region]
        expression = str(condition.get('text', ''))
        if not expression:
            raise WorkflowError('OCR condition must contain text')
        if condition.get('regex'):
            passed = re.search(expression, found, re.IGNORECASE) is not None
        else:
            passed = expression.casefold() in found.casefold()
    elif kind == 'pixel':
        x, y = map(int, condition['point'])
        if not 0 <= x < SIZE[0] or not 0 <= y < SIZE[1]:
            raise WorkflowError('Pixel point is outside the normalized screen')
        expect = np.asarray(condition['bgr'], dtype=np.float32)
        passed = np.max(np.abs(frame[y, x].astype(float) - expect)) <= float(condition.get('tolerance', 25))
    else:
        raise WorkflowError(f'Unknown detector type: {kind}')
    if condition.get('negate'):
        passed = not passed
    return 1.0 if passed else 0.0


class OmniDetector:
    """Fast legacy template matching + optional OCR/color/negative conditions.

    Templates are compiled once. OCR runs only for states whose image probes
    passed their threshold, avoiding an OCR call per state per emulator frame.
    """
    def __init__(self, folder: Path):
        import json
        self.folder = Path(folder)
        self.manifest = json.loads((self.folder / 'manifest.json').read_text(encoding='utf-8'))
        self.actions = self.manifest.get('actions', {})
        self.threshold = float(self.manifest.get('threshold', 0.82))
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
        # OmniRunner already normalized this screenshot. Avoid an identical
        # second OpenCV resize on every emulator tick, while preserving support
        # for full-resolution frames passed directly to the detector.
        image = (frame if frame.shape == (SIZE[1], SIZE[0], 3)
                 else normalize(frame))
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        cache: dict = {}
        candidates = []
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
            if confidence < threshold:
                continue
            conditions = action.get('conditions', [])
            if not probes and not conditions:
                raise WorkflowError(f'State {key} needs a template or a condition')
            if conditions:
                try:
                    outcomes = [_condition(image, cond, cache) for cond in conditions]
                except WorkflowError:
                    raise
                confidence = min(confidence, min(outcomes))
                if min(outcomes) == 0:
                    continue
            candidates.append(Match(key, action.get('label', key), confidence,
                                    bool(action.get('passive', False)), trigger))
        if not candidates:
            return UNKNOWN
        # A strong explicit no-tap state wins against overlapping action probes.
        # Priority is stable and independent of visual list order.
        return max(candidates, key=lambda m: (m.passive, m.confidence,
                                               float(self.actions[m.key].get('priority', 0)), m.key))


def _target(point, device_size: tuple[int, int]) -> tuple[int, int]:
    if len(point) != 2:
        raise WorkflowError('Action point must have two coordinates')
    w, h = device_size
    x, y = map(float, point)
    if not 0 <= x <= SIZE[0] or not 0 <= y <= SIZE[1]:
        raise WorkflowError(f'Point {point} outside 540×960 normalized screen')
    return max(0, min(w - 1, round(x * w / SIZE[0]))), max(0, min(h - 1, round(y * h / SIZE[1])))


class OmniRunner:
    """One emulator, one state machine. Doesn't know about other sessions."""
    def __init__(self, device, folder: Path, package: str, *, log=lambda _: None,
                 completed=lambda _: None, image=lambda *_: None,
                 auto_relaunch: bool = True, dry_run: bool = False,
                 can_act: Callable[[], bool] = lambda: True,
                 is_paused: Callable[[], bool] = lambda: False):
        self.device = device
        self.detector = OmniDetector(folder)
        self.package = package
        self.log, self.on_completed, self.on_image = log, completed, image
        self.auto_relaunch = auto_relaunch
        self.dry_run = dry_run
        self.can_act = can_act
        self.is_paused = is_paused
        self.device_size = None
        self._capture_geometry: tuple[int, int] | None = None
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
        self._stable_key = ''
        self._streak = 0
        self._acted = False
        self.wait_remaining: float | None = None

    def _chain(self, key: str, trigger: int) -> list[dict]:
        action = self.detector.actions[key]
        if action.get('passive'):
            return []
        if 'action_chain' in action:
            return list(action['action_chain'])
        gestures = action.get('gestures')
        if gestures is None:
            taps = action.get('taps') or ([action['tap']] if 'tap' in action else [])
            gestures = [{'kind': 'tap', 'point': p} for p in taps if p is not None]
        # Legacy paired region actions were alternatives, not a sequential chain.
        if len(gestures) > 1 and len(self.detector.manifest['states'].get(key, [])) > 1:
            return [gestures[min(trigger, len(gestures) - 1)]]
        return list(gestures)

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

    def _execute(self, action: dict, now: float) -> bool:
        # Every step must retain session ownership, including waits, dry runs
        # and run counters that do not send Android commands.
        self._require_action_permission()
        kind = action.get('kind')
        size = self.device_size
        if kind == 'wait':
            seconds = float(action.get('seconds', 0))
            if not 0 <= seconds <= 86400:
                raise WorkflowError('Wait action outside 0–86400 seconds')
            self.chain_wait_until = now + seconds
            self.chain_index += 1
            return False
        if kind in ('text', 'field_text'):
            try:
                android_text_argument(str(action.get('text', '')))
            except ValueError as exc:
                raise WorkflowError(str(exc)) from exc
        if self.dry_run and kind != 'wait':
            self.chain_index += 1
            self.log(f'DRY RUN: would execute {kind} on {self.last_observation.label}')
            return True
        if kind in ('tap', 'double_tap'):
            x, y = _target(action['point'], size)
            self._require_action_permission()
            self.device.tap(x, y)
            if kind == 'double_tap':
                # Device ownership may be revoked between the two touches.
                self._require_action_permission()
                self._require_fresh_capture()
                self.device.tap(x, y)
        elif kind in ('drag', 'swipe', 'hold'):
            start = _target(action.get('start', action.get('point')), size)
            end = _target(action.get('end', action.get('point')), size)
            duration = int(action.get('duration_ms', 650))
            if not 50 <= duration <= 10000:
                raise WorkflowError('Gesture duration must be 50–10000 ms')
            self._require_action_permission()
            self.device.swipe(*start, *end, duration)
        elif kind == 'key':
            code = int(action.get('code', 4))
            if not 0 <= code <= 300:
                raise WorkflowError('Invalid Android key event')
            self._require_action_permission()
            self.device.keyevent(code)
        elif kind in ('text', 'field_text'):
            text = str(action.get('text', ''))
            if kind == 'field_text':
                # Select the recorded input field; check ownership again before
                # the second command so lease revocation never types into it.
                x, y = _target(action['point'], size)
                self._require_action_permission()
                self.device.tap(x, y)
            self._require_action_permission()
            if kind == 'field_text':
                self._require_fresh_capture()
            self.device.input_text(text)
        elif kind == 'launch':
            self._require_action_permission()
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
            self._require_action_permission()
            self.log(f'{self.package}: confirmed stopped; relaunching via ADB')
            self.device.launch(self.package)
            self.last_launch = now
            self.missing_since = None

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
        try:
            raw = self.device.screenshot()
            frame = normalize(raw)
            capture_age = time.monotonic() - capture_started
            if capture_age > MAX_CAPTURE_AGE_SECONDS:
                raise ADBError(
                    f'Captured frame is stale ({capture_age:.2f}s > '
                    f'{MAX_CAPTURE_AGE_SECONDS:.0f}s); refusing input')
        except (ADBError, OSError):
            self._invalidate_visual_state(now)
            self.last_frame = None
            self.last_observation = UNKNOWN
            raise
        self.last_frame = frame
        # ADB `wm size` can report an old base orientation. Use screenshot
        # geometry, and never continue an action chain across a rotation or
        # resolution change on the strength of stale state recognition.
        geometry = (raw.shape[1], raw.shape[0])
        changed = self._capture_geometry is not None and geometry != self._capture_geometry
        self._capture_geometry = geometry
        self.device_size = geometry
        if changed:
            self.log(f'Display geometry changed to {geometry[0]}x{geometry[1]}; '
                     'reconfirming state before input')
            self._invalidate_visual_state(now)
        observation = self.detector.detect(frame)
        self.last_observation = observation
        # Recognition intentionally uses a fixed coordinate canvas for saved
        # workflows. The dashboard must display the *original* screen aspect
        # ratio instead of the stretched recognition image.
        self.on_image(observation, raw)
        # OCR and callbacks can block after ADB returns. Freshness must hold
        # when the screenshot authorizes input, not only after PNG decoding.
        age = time.monotonic() - capture_started
        if age > MAX_CAPTURE_AGE_SECONDS:
            self._invalidate_visual_state(now)
            self.last_frame = None
            self.last_observation = UNKNOWN
            raise ADBError(
                f'Captured frame is stale after recognition ({age:.2f}s > '
                f'{MAX_CAPTURE_AGE_SECONDS:.0f}s); refusing input')
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
                self._monitor_game(now)
            return observation
        self.unknown_count = 0
        action = self.detector.actions[observation.key]
        if observation.passive:
            return observation
        chain = self._chain(observation.key, observation.trigger)
        if not chain:
            return observation
        retry = bool(action.get('retry', False))
        retry_mode = action.get('retry_mode', 'on_return')
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
