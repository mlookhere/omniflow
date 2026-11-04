"""Resilient screen-verified dungeon loop with progress and crash watchdogs."""
from __future__ import annotations

import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import cv2
import numpy as np

from .adb import ADBError
from .config import Settings, data_dir
from .detection import Observation, ScreenDetector, normalize, to_device


class Device(Protocol):
    def screenshot(self) -> np.ndarray: ...
    def size(self) -> tuple[int, int]: ...
    def tap(self, x: int, y: int) -> None: ...
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None: ...
    def running(self, package: str) -> bool | None: ...
    def foreground(self) -> str: ...
    def launch(self, package: str) -> None: ...
    def force_stop(self, package: str) -> None: ...
    def connect(self, endpoint: str) -> str: ...


# Coordinates from original screenshots, including the 40px emulator title bar.
TAPS: dict[str, tuple[int, int]] = {
    'open_journal': (234, 1311),
    'open_daily_dungeon': (169, 219),
    'open_shrine': (376, 691),
    'select_normal': (258, 1303),
    'find_match': (556, 1185),
    'accept_invite': (545, 1186),
    'ready': (371, 1029),
    'leave_results': (77, 1304),
}

STATES = {
    'HOME': 'Home', 'JOURNAL': 'Journal', 'DUNGEON_LIST': 'Dungeon list',
    'DETAILS': 'Shrine details', 'INVITE': 'Match invitation',
    'LOBBY': 'Waiting lobby', 'RESULTS': 'Battle results',
    'UNKNOWN': 'Unrecognized / battle in progress',
}

# Avoid tapping Match repeatedly: once queued it may become a Cancel button.
# Ready is a toggle, so it must never be pressed twice in one observed lobby.
ACTION_DELAY = {'find_match': float('inf'), 'ready': float('inf'), 'accept_invite': 5.0,
                'leave_results': 3.0, 'select_normal': 4.0}
# A queue is not a failed action. Require a visibly different, recognized
# game page before re-arming a return-only action (not just an OCR fluctuation).
RETURN_PAGE_DIFF = 20.0
RETURN_PAGE_CHANGED_FRACTION = 0.20
# An unrecorded acceptance/countdown overlay can be the only evidence that a
# queued match ended. Require repeated, substantially different frames, then
# verify the original tap target is back before re-arming.
UNKNOWN_DEPARTURE_FRAMES = 2
RETURN_TARGET_CORRELATION = 0.88
RETURN_TARGET_MAX_DIFFERENCE = 20.0
KNOWN_STALL_SECONDS = 240
UNKNOWN_STALL_SECONDS = 720
MAX_STALL_SECONDS = 86400
FROZEN_SECONDS = 75
RESTART_COOLDOWN = 90
MISSING_CONFIRM_SECONDS = 10
RECENT_GAME_GRACE_SECONDS = 20


@dataclass
class ReturnGate:
    tapped_at: float
    page: np.ndarray
    departed: bool = False
    unknown_frames: int = 0
    verify_target: bool = False
    target: np.ndarray | None = None
    target_xy: tuple[int, int] | None = None


@dataclass(frozen=True)
class Decision:
    action: str | None
    description: str


def decide(state: str, normal_selected: bool) -> Decision:
    return {
        'HOME': Decision('open_journal', 'Open journal'),
        'JOURNAL': Decision('open_daily_dungeon', 'Open daily dungeon'),
        'DUNGEON_LIST': Decision('open_shrine', 'Open Aquorigin Shrine'),
        'DETAILS': (Decision('find_match', 'Find a Normal match') if normal_selected
                    else Decision('select_normal', 'Select Normal difficulty')),
        'INVITE': Decision('accept_invite', 'Accept invitation'),
        'LOBBY': Decision('ready', 'Mark ready'),
        'RESULTS': Decision('leave_results', 'Return from results'),
    }.get(state, Decision(None, 'Wait for recognized game screen'))


class Runner:
    def __init__(self, device: Device, detector: ScreenDetector, settings: Settings,
                 log: Callable[[str], None] = print,
                 report: Callable[[Observation, np.ndarray], None] | None = None,
                 completed: Callable[[int], None] | None = None):
        self.device = device
        self.detector = detector
        self.settings = settings
        self.log = log
        self.report = report or (lambda *_: None)
        self.on_complete = completed or (lambda *_: None)
        self.actions = detector.metadata.get('actions') or {}
        self.custom = bool(self.actions)
        self.labels = dict(STATES)
        self.labels.update({key: value['label'] for key, value in self.actions.items()})
        self.last_state = ''
        self.last_logical_state = ''
        self.last_action = ''
        self.last_tap = float('-inf')
        self.wait_since: float | None = None
        self.wait_action = ''
        self.wait_remaining: float | None = None
        self.last_known = 0.0
        self.last_progress = 0.0
        self.last_recovery_check = float('-inf')
        self.last_diagnostic = float('-inf')
        self.last_restart = float('-inf')
        self.last_recovery_launch = float('-inf')
        self.missing_since: float | None = None
        self.retries: dict[str, int] = defaultdict(int)
        self.return_gates: dict[str, ReturnGate] = {}
        self.completed = 0
        self.pending_end = False
        self.device_size: tuple[int, int] | None = None
        self.last_emulator_launch = float('-inf')
        self.last_image: np.ndarray | None = None
        self.static_since = 0.0

    def action_for(self, state: str, frame: np.ndarray) -> Decision:
        if self.custom:
            info = self.actions.get(state)
            if not info or info.get('passive'):
                return Decision(None, 'Wait on recognized screen')
            return Decision(state, info['label'])
        return decide(state, self.detector.normal_selected(frame) if state == 'DETAILS' else False)

    def _scale_recorded_point(self, point: list[int] | tuple[int, int]) -> tuple[int, int]:
        width, height = self.device_size
        return (min(width - 1, max(0, round(point[0] * width / 540))),
                min(height - 1, max(0, round(point[1] * height / 960))))

    def _target(self, action: str, trigger: int = 0) -> tuple[int, int]:
        """Return the preview/interaction target for both saved action formats."""
        if not self.custom:
            return to_device(*TAPS[action], *self.device_size)
        info = self.actions[action]
        gestures = info.get('gestures')
        if gestures:
            gesture = gestures[min(trigger, len(gestures) - 1)]
            point = gesture['point'] if gesture['kind'] == 'tap' else gesture['start']
        else:
            taps = info.get('taps')
            point = taps[min(trigger, len(taps) - 1)] if taps else info.get('tap')
        if point is None:
            raise ValueError(f'Recorded state {info.get("label", action)!r} has no tap or drag target')
        return self._scale_recorded_point(point)

    def _perform_action(self, action: str, trigger: int) -> str:
        """Dispatch a screen-selected gesture in device coordinates."""
        gestures = self.actions.get(action, {}).get('gestures')
        if not gestures or gestures[min(trigger, len(gestures) - 1)]['kind'] == 'tap':
            x, y = self._target(action, trigger)
            self.device.tap(x, y)
            return f'tap ({x}, {y})'
        gesture = gestures[min(trigger, len(gestures) - 1)]
        start = self._target(action, trigger)
        end = self._scale_recorded_point(gesture['end'])
        duration = gesture['duration_ms']
        self.device.swipe(*start, *end, duration)
        return f'drag {start} → {end} ({duration} ms)'

    def _end_state(self, state: str) -> bool:
        return bool(self.actions[state].get('end', False)) if self.custom else state == 'RESULTS'

    @staticmethod
    def _different_page(current: np.ndarray, previous: np.ndarray) -> bool:
        delta = cv2.absdiff(current, previous)
        return (float(np.mean(delta)) > RETURN_PAGE_DIFF and
                float(np.mean(delta > 30)) > RETURN_PAGE_CHANGED_FRACTION)

    def _return_target(self, frame: np.ndarray, action: str, trigger: int) -> tuple[np.ndarray, tuple[int, int]]:
        """Capture the small control face around a recorded tap (not the entire page)."""
        x, y = self._target(action, trigger)
        width, height = self.device_size
        x = round(x * 540 / width)
        y = round(y * 960 / height)
        # A narrow crop centered on button text distinguishes Match vs Cancel.
        x1, x2 = max(0, x - 43), min(540, x + 43)
        y1, y2 = max(0, y - 18), min(960, y + 18)
        patch = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        return patch, (x, y)

    @staticmethod
    def _target_restored(frame: np.ndarray, gate: ReturnGate) -> bool:
        """Never infer that an active Cancel button became Match from page detection alone."""
        if gate.target is None or gate.target_xy is None:
            return False
        x, y = gate.target_xy
        height, width = gate.target.shape
        # Small spatial allowance for animation/registration differences.
        x1, y1 = max(0, x - width // 2 - 3), max(0, y - height // 2 - 3)
        x2, y2 = min(540, x1 + width + 6), min(960, y1 + height + 6)
        search = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        if search.shape[0] < height or search.shape[1] < width:
            return False
        scores = cv2.matchTemplate(search, gate.target, cv2.TM_CCOEFF_NORMED)
        _, best, _, location = cv2.minMaxLoc(scores)
        lx, ly = location
        current = search[ly:ly + height, lx:lx + width]
        return (best >= RETURN_TARGET_CORRELATION and
                float(np.mean(cv2.absdiff(current, gate.target))) <= RETURN_TARGET_MAX_DIFFERENCE)

    def _unknown_departure(self) -> None:
        """Observe missed/expired acceptance prompts without assuming a queue timeout."""
        if self.last_image is None:
            return
        for action, gate in self.return_gates.items():
            if gate.departed:
                continue
            if self._different_page(self.last_image, gate.page):
                gate.unknown_frames += 1
                if gate.unknown_frames >= UNKNOWN_DEPARTURE_FRAMES:
                    gate.departed = True
                    gate.verify_target = True
                    self.log(f'{action}: unrecognized screen transition; verifying Match control on return')
            else:
                gate.unknown_frames = 0

    def _progress(self, state: str, now: float) -> None:
        # Same-named action variants share a visit; a Wait state is a distinct
        # visit even if its label happens to be identical.
        logical = (('wait:' if self.actions[state].get('passive') else '') +
                   self.labels[state].casefold()) if self.custom else state
        if self.last_image is not None:
            for guarded_action, gate in self.return_gates.items():
                if (logical != guarded_action and not gate.departed and
                        self._different_page(self.last_image, gate.page)):
                    gate.departed = True
        # Multiple recordings with the same label are alternative ways to
        # recognize ONE logical visit. Don't tap again as variants oscillate.
        if logical == self.last_logical_state:
            self.last_state = state
            return
        if self.pending_end and not self._end_state(state):
            self.completed += 1
            self.pending_end = False
            # Completing a run proves the previous matchmaking visit ended,
            # even if its intermediate screenshots were never recognized or
            # failed the conservative image-difference test. Do not clear the
            # gates: the minimum retry interval still applies on return.
            rearmed = [action for action, gate in self.return_gates.items()
                       if not gate.departed]
            for gate in self.return_gates.values():
                gate.departed = True
            if rearmed:
                self.log(f'Run finished; return-only actions re-armed: {", ".join(rearmed)}')
            self.on_complete(self.completed)
            self.log(f'Completed loops this session: {self.completed}')
        if self._end_state(state):
            self.pending_end = True
        self.last_state = state
        self.last_logical_state = logical
        self.last_progress = now
        self.last_action = ''
        self.wait_since = None
        self.wait_action = ''
        self.retries.clear()
        self.log(f'Found: {self.labels[state]}')

    def _watch_frame(self, frame: np.ndarray, now: float) -> None:
        # Low-cost tiny luminance fingerprint, sampled for stalls only.
        sample = cv2.resize(cv2.cvtColor(normalize(frame), cv2.COLOR_BGR2GRAY),
                            (32, 56), interpolation=cv2.INTER_AREA)
        if self.last_image is None or np.mean(cv2.absdiff(sample, self.last_image)) > 1.8:
            self.static_since = now
        self.last_image = sample

    def step(self, now: float | None = None) -> Observation:
        now = time.monotonic() if now is None else now
        self.wait_remaining = None
        frame = normalize(self.device.screenshot())
        if self.device_size is None:
            self.device_size = self.device.size()
            width, height = self.device_size
            if abs(width / height - 9 / 16) > 0.025:
                raise ADBError(f'Unexpected display {width}x{height}; set MuMu portrait 2160x3840')
        self._watch_frame(frame, now)
        observation = self.detector.detect(frame)
        self.report(observation, normalize(frame))
        if not self.last_progress:
            self.last_progress = now
        if observation.state == 'UNKNOWN':
            self._unknown_departure()
            # A departed state cannot fire a stale, previously armed timer.
            self.wait_since = None
            self.wait_action = ''
            if now - self.last_diagnostic > 45:
                self._diagnostic(frame, now)
            # A queued match can temporarily be UNKNOWN while its status overlay
            # is visible. Verify process health, but do not restart for inactivity.
            self._recover_game(now, allow_stall=not any(
                not gate.departed for gate in self.return_gates.values()))
            return observation

        self.last_known = now
        self.missing_since = None
        self._progress(observation.state, now)
        if self.custom and self.actions[observation.state].get('passive'):
            # The recorder explicitly identified a no-tap screen. This is a
            # trustworthy departure for a return-only action, without relying
            # on the old full-page-difference / completed-run heuristics.
            for gate in self.return_gates.values():
                gate.departed = True
                gate.verify_target = False
        decision = self.action_for(observation.state, frame)
        if not decision.action:
            return observation
        # A region/tap pair is its own guarded action. Reordering the editor
        # list has no effect on dispatch, and alternatives stay independent.
        action_id = (self.labels[decision.action].casefold() if self.custom else decision.action)
        if action_id != self.last_action:
            self.last_action = action_id
            self.retries.clear()
        tries = self.retries[action_id]
        before_action = (self.actions[decision.action].get('pre_tap_delay_seconds', 0)
                         if self.custom else 0)
        if not isinstance(before_action, int) or not 0 <= before_action <= 86400:
            before_action = 0  # Malformed delay cannot leave the bot hung.
        if self.wait_since is None or self.wait_action != action_id:
            self.wait_action = action_id
            self.wait_since = now
            if before_action and tries == 0:
                self.log(f'Waiting {before_action} seconds before {self.labels[decision.action]}')
        # This timer is nonblocking. Screens continue to be captured and
        # matched while waiting. State departures cancel the pending action.
        # Same-named alternative captures share their initial visit timestamp.
        if tries == 0 and now - self.wait_since < before_action:
            self.wait_remaining = before_action - (now - self.wait_since)
            return observation
        retry_mode = (self.actions[decision.action].get('retry_mode', 'interval')
                      if self.custom else 'interval')
        retry_seconds = (self.actions[decision.action].get('retry_interval_seconds', 25)
                         if self.custom else 25)
        if type(retry_seconds) is not int or not 1 <= retry_seconds <= 86400:
            retry_seconds = 25
        gate = self.return_gates.get(action_id) if retry_mode == 'on_return' else None
        if gate and (not gate.departed or now - gate.tapped_at < retry_seconds):
            # No timed tap can cancel an active queue. Another recognized screen
            # on the SAME PAGE (e.g. difficulty tabs) cannot re-arm the action.
            return observation
        if gate and gate.verify_target and not self._target_restored(frame, gate):
            # An unknown overlay is not proof the queue ended. It may still
            # display Cancel, so require the original Match control itself.
            return observation
        # While we are still on the queue page, don't execute a competing state
        # (such as "choose difficulty") merely because its match score wins.
        if self.last_image is not None and any(
                not self._different_page(self.last_image, gate.page)
                and (not gate.departed or
                     (gate.verify_target and not self._target_restored(frame, gate)))
                for gate in self.return_gates.values()):
            return observation
        # An intentionally delayed first action isn't a stall, even when the
        # wait exceeds the normal four-minute recognized-screen watchdog.
        timeout = self._state_timeout(observation.state)
        # Waiting for the queue to finish is not a stuck page. An explicitly
        # configured per-state watchdog can still override this default.
        queue_wait = (retry_mode == 'on_return' and gate is not None)
        if (timeout and now - self.last_progress > timeout
                and not (tries == 0 and before_action)
                and not queue_wait):
            self._restart_game(now, f'Stuck on {self.labels[observation.state]} for over {timeout} seconds')
            return observation
        delay = ACTION_DELAY.get(decision.action, 5.0)
        if self.custom:
            # Some controls toggle; the safe recorder default is one tap per
            # recognized visit, with retries only if the author opts in.
            delay = float(retry_seconds) if self.actions[decision.action].get('retry', False) else float('inf')
        if tries and now - self.last_tap < delay:
            return observation
        # Optional missed-click cap, scoped to this visible-state visit.
        # Absent/zero preserves historical unlimited interval retries.
        cap = (self.actions[decision.action].get('max_extra_taps', 0)
               if self.custom and retry_mode == 'interval' else 0)
        cap = cap if type(cap) is int and 1 <= cap <= 20 else 0
        if cap and tries > cap:
            return observation
        # Safe persistent pages can be retried; no spamming toggles or queued Match.
        if tries >= 3 and not cap and now - self.last_tap < 45:
            return observation
        if tries >= 3 and not cap:
            self.retries[action_id] = 0
            self._diagnostic(frame, now)
            self.log(f'No transition from {self.labels[observation.state]}; retrying cautiously')
        executed = self._perform_action(decision.action, observation.trigger)
        self.last_tap = now
        if self.custom and before_action and tries == 0:
            # Start the post-tap stall clock only AFTER the intentional wait.
            self.last_progress = now
        self.retries[action_id] += 1
        if cap and self.retries[action_id] == cap + 1:
            self.log(f'{self.labels[observation.state]}: {cap} extra tap(s) used; waiting for screen change')
        if self.custom and retry_mode == 'on_return' and self.actions[decision.action].get('retry', False):
            target, target_xy = self._return_target(frame, decision.action, observation.trigger)
            self.return_gates[action_id] = ReturnGate(
                now, self.last_image.copy(), target=target, target_xy=target_xy)
            self.log(f'{decision.description}: awaiting departure and return before another tap')
        self.log(f'{decision.description} → {executed}')
        return observation

    def _state_timeout(self, state: str) -> int:
        """Return the selected state's watchdog duration. Zero disables it.

        Legacy profiles and every unconfigured state retain the four-minute
        default. Timers are profile/workflow scoped, not execution-order scoped.
        """
        overrides = self.settings.state_timeouts
        if not isinstance(overrides, dict):
            return KNOWN_STALL_SECONDS
        workflow = overrides.get(self.settings.workflow, {})
        value = workflow.get(state) if isinstance(workflow, dict) else None
        return value if type(value) is int and 0 <= value <= MAX_STALL_SECONDS else KNOWN_STALL_SECONDS

    def _diagnostic(self, frame: np.ndarray, now: float) -> None:
        self.last_diagnostic = now
        if not self.settings.diagnostics:
            return
        folder = data_dir() / 'diagnostics'
        folder.mkdir(exist_ok=True)
        name = time.strftime('%Y%m%d-%H%M%S')
        cv2.imwrite(str(folder / f'{name}.jpg'), cv2.resize(frame, (540, 960)),
                    [cv2.IMWRITE_JPEG_QUALITY, 80])
        for file in sorted(folder.glob('*.jpg'))[:-40]:
            file.unlink(missing_ok=True)
        self.log(f'Saved diagnostic: {name}.jpg')

    def _restart_game(self, now: float, reason: str) -> None:
        package = self.settings.package.strip()
        if not self.settings.auto_relaunch_game or not package or now - self.last_restart < RESTART_COOLDOWN:
            return
        self.last_restart = now
        self.log(f'{reason}. Restarting game…')
        self.device.force_stop(package)
        self.device.launch(package)
        self.last_progress = now
        self.last_known = now
        self.last_state = ''
        self.last_logical_state = ''
        self.last_action = ''
        self.wait_since = None
        self.wait_action = ''
        self.retries.clear()
        self.return_gates.clear()
        self.pending_end = False  # Never credit an interrupted run.
        self.last_image = None
        self.static_since = now
        self.missing_since = None

    def _recover_game(self, now: float, *, allow_stall: bool = True) -> None:
        # Only relaunch the Android app, never the emulator, when its process
        # disappears. A confirmed foreground game overrides unreliable PID checks.
        if not self.settings.auto_relaunch_game:
            return
        if now - self.last_recovery_check < 10:
            return
        self.last_recovery_check = now
        package = self.settings.package.strip()
        if not package:
            return

        # A visible foreground activity is stronger evidence than pidof. In
        # particular, combat/loading screens often have no recorded template.
        foreground = self.device.foreground()
        if foreground == package:
            self.missing_since = None
        else:
            running = self.device.running(package)
            if running is False:
                # Require *two* independent checks, separated in time, with no
                # freshly recognized game screen and no foreground game activity.
                if now - self.last_known <= RECENT_GAME_GRACE_SECONDS:
                    self.missing_since = None
                elif self.missing_since is None:
                    self.missing_since = now
                elif (now - self.missing_since >= MISSING_CONFIRM_SECONDS and
                      now - self.last_recovery_launch >= RESTART_COOLDOWN):
                    self.log('Game process confirmed stopped; relaunching Sword x Staff')
                    try:
                        self.device.launch(package)
                    except ADBError as exc:
                        # A temporary Android activity-manager failure must not
                        # kill the automation thread. Try again after confirmation.
                        self.missing_since = now
                        self.log(f'Game relaunch failed; will retry: {exc}')
                        return
                    self.last_recovery_launch = now
                    self.last_restart = now  # Share cooldown with the freeze watchdog.
                    self.last_progress = now
                    self.last_state = ''
                    self.last_logical_state = ''
                    self.last_action = ''
                    self.wait_since = None
                    self.wait_action = ''
                    self.pending_end = False
                    self.return_gates.clear()
                    self.missing_since = None
                    self.static_since = now
                    return
            else:
                # False negatives and unsupported pidof are not crashes.
                self.missing_since = None
                if running is True and foreground and foreground != package:
                    if now - self.last_recovery_launch >= RESTART_COOLDOWN:
                        self.log(f'Game is in background ({foreground}); bringing it forward')
                        self.device.launch(package)
                        self.last_recovery_launch = now
                        self.last_progress = now
                    return

        if not allow_stall:
            return
        if now - self.static_since >= FROZEN_SECONDS:
            self._restart_game(now, 'Unrecognized screen has been frozen')
        elif now - self.last_progress >= UNKNOWN_STALL_SECONDS:
            self._restart_game(now, 'No recognizable screen for over 12 minutes')

    def skip_paused_time(self, seconds: float) -> None:
        """Pausing the UI must not consume a deliberate pre-action countdown."""
        if seconds > 0 and self.wait_since is not None:
            self.wait_since += seconds

    def reconnect(self) -> None:
        if self.settings.endpoint:
            self.log('Reconnecting ADB…')
            self.device.connect(self.settings.endpoint)

    def recover_emulator(self) -> None:
        path = self.settings.emulator_exe.strip()
        now = time.monotonic()
        if not path or now - self.last_emulator_launch < 120:
            return
        if not Path(path).is_file():
            self.log('MuMu executable not found; retrying ADB connection')
            return
        self.last_emulator_launch = now
        subprocess.Popen([path], cwd=str(Path(path).parent),
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.log('Launched MuMu player; awaiting reconnection')
