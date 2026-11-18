"""Priority-ordered, completion-aware workflow chains for one leased device.

The scheduler never sends Android input itself. It scans saved trigger states
from one fresh screenshot, then delegates an uninterrupted run to OmniRunner.
"""
from __future__ import annotations

import json
import os
import time
from copy import copy
from dataclasses import dataclass
from inspect import getattr_static
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from swordx.detection import SIZE, normalize
from .devices import MAX_CAPTURE_AGE_SECONDS
from .engine import (Match, OmniDetector, OmniRunner, UNKNOWN, WorkflowError,
                     validate_saved_action_chains)
from swordx.adb import ADBError


@dataclass(frozen=True)
class ChainStep:
    workflow: str
    trigger: str
    complete: str
    mode: str
    limit: int = 0


@dataclass(frozen=True)
class ChainPlan:
    name: str
    steps: tuple[ChainStep, ...]
    repeat: bool = True


def validate_plan(source: dict, workflows: dict[str, Path]) -> ChainPlan:
    """Require existing workflow state IDs, never arbitrary filesystem paths."""
    if not isinstance(source, dict):
        raise ValueError('Chain definition must be an object')
    name = source.get('name')
    if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 48 or
            name != name.strip() or any(char in name for char in '\\/:*?"<>|\r\n')):
        raise ValueError('Chain name must be 1–48 safe characters')
    repeat = source.get('repeat', True)
    if type(repeat) is not bool:
        raise ValueError('Repeat chain must be true or false')
    rows = source.get('steps')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 40:
        raise ValueError('Add 1–40 prioritized workflows to the chain')
    steps = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f'Chain row {index} must be an object')
        workflow = row.get('workflow')
        if not isinstance(workflow, str) or workflow not in workflows:
            raise ValueError(f'Chain row {index}: select an existing workflow')
        trigger, complete, mode = (row.get('trigger'), row.get('complete'), row.get('mode'))
        if not isinstance(trigger, str) or not isinstance(complete, str) or trigger == complete:
            raise ValueError(f'Chain row {index}: select distinct trigger and completed states')
        if mode not in ('dynamic', 'timed', 'count'):
            raise ValueError(f'Chain row {index}: mode must be Dynamic, Timed or Run count')
        limit = row.get('limit', 0)
        if type(limit) is not int:
            raise ValueError(f'Chain row {index}: limit must be an integer')
        maximum = 10080 if mode == 'timed' else 100000 if mode == 'count' else 0
        if not (0 <= limit <= maximum and (mode == 'dynamic' or limit >= 1)):
            raise ValueError(f'Chain row {index}: choose a valid positive limit for this mode')
        try:
            manifest = json.loads((workflows[workflow] / 'manifest.json').read_text(
                encoding='utf-8'))
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f'Chain row {index}: workflow manifest is unreadable') from exc
        states = manifest.get('states')
        actions = manifest.get('actions')
        if (not isinstance(states, dict) or not isinstance(actions, dict) or
                trigger not in states or complete not in states or
                trigger not in actions or complete not in actions):
            raise ValueError(f'Chain row {index}: trigger or completed state was deleted')
        if actions[trigger].get('passive'):
            raise ValueError(f'Chain row {index}: trigger cannot be a passive WAIT state')
        steps.append(ChainStep(workflow, trigger, complete, mode, limit))
    return ChainPlan(name, tuple(steps), repeat)


def chain_file(root: Path) -> Path:
    return Path(root) / 'chains.json'


def read_chains(root: Path) -> dict[str, dict]:
    path = chain_file(root)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(data, dict) or data.get('version') != 1 or
            not isinstance(data.get('chains'), dict)):
        raise ValueError('Unsupported or malformed chain library')
    return data['chains']


def save_chain(root: Path, source: dict, workflows: dict[str, Path]) -> ChainPlan:
    plan = validate_plan(source, workflows)
    library = read_chains(root)
    library[plan.name] = {
        'name': plan.name,
        'repeat': plan.repeat,
        'steps': [
            {'workflow': s.workflow, 'trigger': s.trigger, 'complete': s.complete,
             'mode': s.mode, 'limit': s.limit}
            for s in plan.steps
        ],
    }
    _write_library(root, library)
    return plan


def delete_chain(root: Path, name: str) -> None:
    library = read_chains(root)
    if name not in library:
        raise ValueError('Saved chain no longer exists')
    del library[name]
    _write_library(root, library)


def _write_library(root: Path, library: dict[str, dict]) -> None:
    """Atomic update; cannot corrupt saved workflows when a write fails."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    target = chain_file(root)
    temporary = root / f'.chains.{uuid4().hex}.tmp'
    try:
        temporary.write_text(
            json.dumps({'version': 1, 'chains': library}, indent=2),
            encoding='utf-8')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


class ChainedRunner:
    """One device lease; dynamic fallback or bounded phases; no preemption.

    Dynamic rows yield to the next ready priority after completion. If no
    other trigger is present, a previously completed dynamic row may run again.
    Timed/count rows stay preferred until their quota, then release priority.
    A timer expiring during an active run NEVER aborts that run.
    """

    def __init__(self, device, plan: ChainPlan, workflows: dict[str, Path],
                 package: str, *, log=lambda _: None, completed=lambda _: None,
                 image=lambda *_: None, auto_relaunch=True, dry_run=False,
                 can_act=lambda: True, is_paused=lambda: False):
        self.device = device
        self.plan = plan
        self.package = package
        self.log, self.on_completed, self.on_image = log, completed, image
        self.auto_relaunch, self.dry_run = auto_relaunch, dry_run
        self.can_act, self.is_paused = can_act, is_paused
        self.detectors = []
        self.triggers = []
        # Fail before any screenshot/input if a referenced workflow is damaged.
        for row in plan.steps:
            detector = OmniDetector(workflows[row.workflow])
            validate_saved_action_chains(detector.manifest)
            probe = copy(detector)
            probe.actions = {row.trigger: detector.actions[row.trigger]}
            probe.templates = {row.trigger: detector.templates.get(row.trigger, [])}
            self.detectors.append(detector)
            self.triggers.append(probe)
        self.paths = tuple(Path(workflows[row.workflow]) for row in plan.steps)
        self.active: OmniRunner | None = None
        self.active_index: int | None = None
        self.locked: int | None = None
        self.preferred = 0
        self.completed_once: set[int] = set()
        self.exhausted: set[int] = set()
        self.counts = [0] * len(plan.steps)
        self.deadlines: dict[int, float] = {}
        self.done = False
        self._completed_during_step = False
        self.last_decision = 'Watching workflow triggers; no input'
        self.last_capture_started_at = 0.0
        self.last_capture_latency_ms: float | None = None
        self.last_capture_stages_ms: dict[str, float] | None = None
        self.wait_remaining = None

    def _advance(self, index: int) -> None:
        self.completed_once.add(index)
        self.exhausted.add(index)
        self.locked = None
        self.preferred = (index + 1) % len(self.plan.steps)
        self._maybe_cycle()

    def _maybe_cycle(self) -> None:
        if len(self.completed_once) < len(self.plan.steps):
            return
        if not self.plan.repeat:
            self.done = True
            self.last_decision = 'Workflow chain completed'
            return
        self.completed_once.clear()
        self.exhausted.clear()
        self.counts = [0] * len(self.plan.steps)
        self.deadlines.clear()
        self.locked = None
        self.preferred = 0
        self.log(f'Chain {self.plan.name}: completed cycle; ready for next triggers')

    def _eligible(self, now: float) -> list[int]:
        if self.locked is not None:
            index = self.locked
            row = self.plan.steps[index]
            if row.mode == 'timed' and now >= self.deadlines[index]:
                self.log(f'{row.workflow}: time elapsed between runs; advancing')
                self._advance(index)
            else:
                return [index]
        if self.done:
            return []
        count = len(self.plan.steps)
        return [index for index in
                ((*range(self.preferred, count), *range(self.preferred))
                 if count else ())
                if index not in self.exhausted]

    def _finished(self, index: int, now: float) -> None:
        row = self.plan.steps[index]
        self.counts[index] += 1
        self.on_completed(self.counts[index])
        self.log(f'{row.workflow}: completed run {self.counts[index]} [{row.mode}]')
        if row.mode == 'dynamic':
            self.completed_once.add(index)
            self.preferred = (index + 1) % len(self.plan.steps)
            self._maybe_cycle()
        elif row.mode == 'count' and self.counts[index] >= row.limit:
            self._advance(index)
        elif row.mode == 'timed' and now >= self.deadlines[index]:
            self._advance(index)
        else:
            self.locked = index
            self.preferred = index

    def _on_inner_complete(self, _runs: int) -> None:
        self._completed_during_step = True

    def _on_inner_image(self, match: Match, frame: np.ndarray) -> None:
        inner = self.active
        if inner is None:
            return
        self.last_capture_started_at = inner.last_capture_started_at
        self.last_capture_latency_ms = inner.last_capture_latency_ms
        self.last_capture_stages_ms = inner.last_capture_stages_ms
        row = self.plan.steps[self.active_index]
        self.on_image(Match(match.key, f'{row.workflow}: {match.label}',
                            match.confidence, match.passive, match.trigger,
                            match.location), frame)

    def _begin(self, index: int, now: float) -> None:
        row = self.plan.steps[index]
        self.active_index = index
        if row.mode == 'timed':
            self.deadlines.setdefault(index, now + row.limit * 60)
        if row.mode in ('timed', 'count'):
            self.locked = index
        self.active = OmniRunner(
            self.device, self.paths[index], self.package, log=self.log,
            completed=self._on_inner_complete, image=self._on_inner_image,
            auto_relaunch=self.auto_relaunch, dry_run=self.dry_run,
            can_act=self.can_act, is_paused=self.is_paused,
            terminal_key=row.complete, detector=self.detectors[index])
        self.log(f'Chain {self.plan.name}: selected {row.workflow} (priority {index + 1})')
        self.last_decision = f'{row.workflow}: trigger found; confirming workflow state'

    def _scan(self, now: float) -> None:
        if not self.can_act() or self.is_paused():
            self.last_decision = 'Paused or stopped; no trigger input'
            return
        started = time.monotonic()
        self.last_capture_started_at = started
        self.last_capture_stages_ms = None
        try:
            if callable(getattr_static(self.device, 'screenshot_timed', None)):
                raw, stages = self.device.screenshot_timed()
                self.last_capture_stages_ms = stages
            else:
                raw = self.device.screenshot()
            if (not isinstance(raw, np.ndarray) or raw.ndim != 3 or
                    raw.shape[2] != 3 or raw.dtype != np.uint8 or not raw.size):
                raise ADBError('Chain trigger screenshot was empty or unreadable')
            frame = normalize(raw)
            if frame.shape != (SIZE[1], SIZE[0], 3):
                raise ADBError('Chain trigger frame normalization failed')
            age = time.monotonic() - started
            if age > MAX_CAPTURE_AGE_SECONDS:
                raise ADBError('Chain trigger capture became stale')
            self.last_capture_latency_ms = age * 1000
            candidates = self._eligible(now)
            winner = None
            for index in candidates:
                observed = self.triggers[index].detect(frame)
                if observed.key == self.plan.steps[index].trigger:
                    winner = index
                    break
            if time.monotonic() - started > MAX_CAPTURE_AGE_SECONDS:
                raise ADBError('Chain trigger recognition became stale')
            self.on_image(Match('', f'Chain: {self.plan.name} · monitoring triggers', 0),
                          raw)
            if winner is None:
                self.last_decision = 'No eligible workflow trigger; no input'
            elif self.can_act() and not self.is_paused():
                self._begin(winner, now)
        except (cv2.error, ValueError) as exc:
            raise ADBError(f'Chain screenshot/recognition failed: {exc}') from exc

    def step(self, now: float | None = None):
        if self.done:
            return UNKNOWN
        now = time.monotonic() if now is None else now
        self.wait_remaining = None
        if self.active is None:
            self._scan(now)
            return UNKNOWN
        inner = self.active
        observation = inner.step(now)
        self.last_decision = inner.last_decision
        self.wait_remaining = inner.wait_remaining
        self.last_capture_started_at = inner.last_capture_started_at
        self.last_capture_latency_ms = inner.last_capture_latency_ms
        self.last_capture_stages_ms = inner.last_capture_stages_ms
        if self._completed_during_step:
            index = self.active_index
            self._completed_during_step = False
            self.active = None
            self.active_index = None
            self._finished(index, now)
            if not self.done:
                self.last_decision = (
                    f'{self.plan.steps[index].workflow}: completed; scanning next trigger')
        return observation

    def resume_after_pause(self, now: float) -> None:
        if self.active is not None:
            self.active.resume_after_pause(now)
        self.last_decision = 'Chain resumed; reconfirming trigger or workflow state'
