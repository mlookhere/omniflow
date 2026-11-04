"""Up to ten independent, device-exclusive worker sessions.

Only the selected session's already-captured OCR image is displayed in the GUI;
there is no need for ten continuously encoded H.264 preview streams.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from swordx.adb import ADBError
from swordx.stats import SessionStats
from swordx.detection import normalize
from .devices import Device
from .engine import OmniRunner, Match
from .schedule import MAX_INSTANCES, ScheduleStore


@dataclass
class Session:
    serial: str
    package: str
    workflow: str
    folder: Path
    owner: str
    stats: SessionStats = field(default_factory=SessionStats)
    stop_event: threading.Event = field(default_factory=threading.Event)
    pause_event: threading.Event = field(default_factory=threading.Event)
    worker: threading.Thread | None = None
    status: str = 'Connecting'
    current: str = 'Waiting for image'
    confidence: float = 0
    image: np.ndarray | None = None
    logs: deque = field(default_factory=lambda: deque(maxlen=200))
    failures: int = 0
    job_id: str | None = None
    intended_at: float | None = None
    max_minutes: int | None = None
    max_runs: int = 0
    _image_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def latest(self) -> np.ndarray | None:
        with self._image_lock:
            return None if self.image is None else self.image.copy()

    def log(self, message: str) -> None:
        self.logs.append(f'{time.strftime("%H:%M:%S")}  {message}')


class InstanceManager:
    def __init__(self, db: ScheduleStore, *, device_factory=Device,
                 poll_seconds: float = 0.9, auto_relaunch: bool = True, dry_run: bool = False):
        self.db = db
        self.device_factory = device_factory
        self.poll_seconds = max(0.35, float(poll_seconds))
        self.auto_relaunch = auto_relaunch
        self.dry_run = dry_run
        self.sessions: dict[str, Session] = {}
        self.completed_sessions: deque[Session] = deque(maxlen=50)
        self._lock = threading.RLock()
        cv2.setNumThreads(1)  # prevent per-emulator OpenCV thread oversubscription
        self.events: deque[str] = deque(maxlen=200)

    def start(self, serial: str, package: str, workflow: str, folder: Path,
              *, job_id: str | None = None, intended_at: float | None = None,
              max_minutes: int | None = None, max_runs: int = 0) -> tuple[bool, str]:
        if not serial or not package or not workflow or not Path(folder, 'manifest.json').is_file():
            return False, 'Select an available device, installed package, and valid workflow'
        with self._lock:
            if serial in self.sessions:
                return False, f'{serial} already has an active session'
            if len(self.sessions) >= MAX_INSTANCES:
                return False, f'Maximum of {MAX_INSTANCES} active emulators reached'
            owner = uuid4().hex
            if not self.db.acquire(serial, owner):
                return False, f'{serial} is already controlled by another OmniFlow process'
            session = Session(serial, package, workflow, Path(folder), owner,
                              job_id=job_id, intended_at=intended_at, max_minutes=max_minutes,
                              max_runs=max_runs)
            self.sessions[serial] = session
            session.worker = threading.Thread(target=self._run, args=(session,),
                                              name=f'OmniFlow-{serial}', daemon=True)
            session.worker.start()
        return True, f'Started {workflow} on {serial}'

    def _run(self, session: Session) -> None:
        reason = 'stopped'
        try:
            device = self.device_factory(serial=session.serial)
            if hasattr(device, 'ensure_connected'):
                try:
                    device.ensure_connected()
                except ADBError as exc:
                    session.log(f'Initial device connect failed, retrying: {exc}')
            runner = OmniRunner(device, session.folder, session.package,
                                log=session.log, completed=lambda _: session.stats.complete(),
                                image=lambda match, frame: self._image(session, match, frame),
                                auto_relaunch=self.auto_relaunch, dry_run=self.dry_run and session.job_id is None)
            session.status = 'Running'
            last_heartbeat = 0.0
            paused_at = None
            while not session.stop_event.is_set():
                now = time.monotonic()
                if session.max_minutes and now - session.stats.started >= session.max_minutes * 60:
                    reason = 'duration_limit'
                    break
                if session.max_runs and len(session.stats.completed) >= session.max_runs:
                    reason = 'runs_limit'
                    break
                if now - last_heartbeat >= 8:
                    if not self.db.heartbeat(session.serial, session.owner):
                        reason = 'lost_device_lease'
                        session.log('Device lease lost; stopping to avoid duplicate input')
                        break
                    last_heartbeat = now
                if session.pause_event.is_set():
                    if paused_at is None:
                        paused_at = now
                        session.stats.pause(now)
                    session.status = 'Paused'
                    session.stop_event.wait(0.15)
                    continue
                if paused_at is not None:
                    delta = now - paused_at
                    runner.entered_at += delta
                    runner.action_at += delta
                    runner.chain_wait_until += delta
                    session.stats.resume(now)
                    paused_at = None
                    session.status = 'Running'
                try:
                    runner.step(now)
                    session.failures = 0
                except (ADBError, OSError) as exc:
                    session.failures += 1
                    session.log(f'ADB/capture issue ({session.failures}): {exc}')
                    if session.failures % 3 == 0 and hasattr(device,'ensure_connected'):
                        try:device.ensure_connected()
                        except ADBError:pass
                    if session.failures >= 20:
                        reason = 'connection_unavailable'
                        break
                    session.stop_event.wait(min(8, session.failures * 1.0))
                    continue
                session.stop_event.wait(self.poll_seconds)
        except Exception as exc:
            reason = 'error'
            session.log(f'Worker error: {type(exc).__name__}: {exc}')
        finally:
            session.stats.finish()
            session.status = f'Stopped · {reason}'
            self.events.append(f'{session.serial}: {session.workflow} finished ({reason}); '
                               f'{len(session.stats.completed)} run(s)')
            self.db.release(session.serial, session.owner)
            if session.job_id is not None and session.intended_at is not None:
                self.db.mark(session.job_id, session.intended_at, reason)
            # Keep finished session stats visible until a new run overwrites it.
            with self._lock:
                if self.sessions.get(session.serial) is session:
                    del self.sessions[session.serial]
                self.completed_sessions.append(session)

    def _image(self, session: Session, match: Match, frame: np.ndarray) -> None:
        session.current, session.confidence = match.label, match.confidence
        # Reuse the runner's captured screenshot rather than requesting another
        # ADB frame. Preserve its real orientation and aspect ratio, keeping
        # just a bounded thumbnail per session (not ten full-resolution frames).
        height, width = frame.shape[:2]
        scale = min(1.0, 480 / max(height, width))
        target = (max(1, round(width * scale)), max(1, round(height * scale)))
        thumbnail = cv2.resize(frame, target, interpolation=cv2.INTER_AREA)
        with session._image_lock:
            session.image = thumbnail

    def stop(self, serial: str) -> None:
        with self._lock:
            if serial in self.sessions:
                self.sessions[serial].stop_event.set()

    def pause(self, serial: str, paused: bool) -> None:
        with self._lock:
            if serial in self.sessions:
                (self.sessions[serial].pause_event.set if paused else
                 self.sessions[serial].pause_event.clear)()

    def stop_all(self) -> None:
        with self._lock:
            for session in self.sessions.values():
                session.stop_event.set()

    def snapshot(self) -> list[Session]:
        with self._lock:
            return list(self.sessions.values())

    def drain_events(self) -> list[str]:
        with self._lock:
            events = list(self.events)
            self.events.clear()
            return events

    def tick_schedules(self, workflows: dict[str, Path], now: float | None = None) -> list[str]:
        statuses = []
        for job, valid in self.db.claim_due(now):
            if not valid:
                statuses.append(f'{job.name}: skipped stale scheduled time')
                continue
            folder = workflows.get(job.workflow)
            if folder is None:
                result, message = False, 'workflow not found'
            else:
                result, message = self.start(job.serial, job.package, job.workflow, folder,
                                             job_id=job.id, intended_at=job.next_at,
                                             max_minutes=job.max_minutes, max_runs=job.max_runs)
            if not result:
                self.db.mark(job.id, job.next_at, 'skipped_busy_or_invalid')
            statuses.append(f'{job.name}: {message}')
        return statuses
