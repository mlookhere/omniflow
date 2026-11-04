"""Game-agnostic, state-first OCR/image automation engine.

One normalized screenshot, one matching pass, one bounded action at a time.
There is no game-, dungeon-, or matchmaking-specific dispatch logic.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

import cv2
import numpy as np

from swordx.adb import ADBError
from swordx.detection import SIZE, ScreenDetector, normalize


class WorkflowError(ValueError):
    pass


class OCREngine:
    """Lazy optional OCR; shared across workers, avoiding ten model loads."""
    _instance = None
    _checked = False
    _lock = RLock()

    @classmethod
    def read(cls, roi: np.ndarray) -> str:
        with cls._lock:
            if not cls._checked:
                cls._checked = True
                try:
                    from rapidocr_onnxruntime import RapidOCR
                    cls._instance = RapidOCR()
                except ImportError:
                    cls._instance = None
            if cls._instance is None:
                raise WorkflowError('OCR detector needs optional rapidocr-onnxruntime; install requirements-ocr.txt')
            result, _ = cls._instance(roi)
            return ' '.join(str(row[1]) for row in (result or []))


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
        image = normalize(frame)
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
                 auto_relaunch: bool = True, dry_run: bool = False):
        self.device = device
        self.detector = OmniDetector(folder)
        self.package = package
        self.log, self.on_completed, self.on_image = log, completed, image
        self.auto_relaunch = auto_relaunch
        self.dry_run = dry_run
        self.device_size = None
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

    def _execute(self, action: dict, now: float) -> bool:
        kind = action.get('kind')
        size = self.device_size
        if kind == 'wait':
            seconds = float(action.get('seconds', 0))
            if not 0 <= seconds <= 86400:
                raise WorkflowError('Wait action outside 0–86400 seconds')
            self.chain_wait_until = now + seconds
            self.chain_index += 1
            return False
        if self.dry_run and kind != 'wait':
            self.chain_index += 1
            self.log(f'DRY RUN: would execute {kind} on {self.last_observation.label}')
            return True
        if kind in ('tap', 'double_tap'):
            x, y = _target(action['point'], size)
            self.device.tap(x, y)
            if kind == 'double_tap':
                # Two taps are part of one recorded action; no full-screen poll.
                self.device.tap(x, y)
        elif kind in ('drag', 'swipe', 'hold'):
            start = _target(action.get('start', action.get('point')), size)
            end = _target(action.get('end', action.get('point')), size)
            duration = int(action.get('duration_ms', 650))
            if not 50 <= duration <= 10000:
                raise WorkflowError('Gesture duration must be 50–10000 ms')
            self.device.swipe(*start, *end, duration)
        elif kind == 'key':
            code = int(action.get('code', 4))
            if not 0 <= code <= 300:
                raise WorkflowError('Invalid Android key event')
            self.device.keyevent(code)
        elif kind == 'text':
            text = str(action.get('text', ''))
            if not text or len(text) > 250:
                raise WorkflowError('Text input must be 1–250 characters')
            self.device.input_text(text)
        elif kind == 'launch':
            self.device.launch(self.package)
        elif kind == 'count_run':
            self._finish_run()
        else:
            raise WorkflowError(f'Unknown action: {kind}')
        self.chain_index += 1
        self.log(f'{self.last_observation.label}: {kind}')
        return True

    def _finish_run(self) -> None:
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
            self.log(f'{self.package}: confirmed stopped; relaunching via ADB')
            self.device.launch(self.package)
            self.last_launch = now
            self.missing_since = None

    def step(self, now: float | None = None) -> Match:
        now = time.monotonic() if now is None else now
        self.wait_remaining = None
        raw = self.device.screenshot()
        frame = normalize(raw)
        self.last_frame = frame
        # ADB `wm size` may report the portrait base resolution even when
        # Android has rotated the display. Input coordinates follow the
        # screenshot's current orientation, not that base resolution.
        # Refresh on *every* capture so rotations mid-workflow stay aligned.
        self.device_size = (raw.shape[1], raw.shape[0])
        observation = self.detector.detect(frame)
        self.last_observation = observation
        # Recognition intentionally uses a fixed coordinate canvas for saved
        # workflows. The dashboard must display the *original* screen aspect
        # ratio instead of the stretched recognition image.
        self.on_image(observation, raw)
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
        executed = self._execute(chain[self.chain_index], now)
        if executed:
            self._acted = True
        if self.chain_index >= len(chain):
            self.end_pending = bool(action.get('end') and not any(step.get('kind') == 'count_run' for step in chain))
            self.chain_wait_until = now + 0.1
        return observation
