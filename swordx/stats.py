"""Session-only, monotonic run analytics; never modifies saved profiles."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SessionSnapshot:
    runs: int
    elapsed: float
    active: float
    average: float | None
    fastest: float | None
    latest: float | None
    per_hour: float


@dataclass
class SessionStats:
    started: float = field(default_factory=time.monotonic)
    completed: list[float] = field(default_factory=list)
    paused_at: float | None = None
    paused_total: float = 0.0
    last_finish_active: float = 0.0
    finished_at: float | None = None

    def _active(self, now: float) -> float:
        paused = self.paused_total + (max(0.0, now - self.paused_at) if self.paused_at is not None else 0)
        return max(0.0, now - self.started - paused)

    def pause(self, now: float | None = None) -> None:
        if self.paused_at is None:
            self.paused_at = time.monotonic() if now is None else now

    def resume(self, now: float | None = None) -> None:
        if self.paused_at is not None:
            self.paused_total += max(0.0, (time.monotonic() if now is None else now) - self.paused_at)
            self.paused_at = None

    def finish(self, now: float | None = None) -> None:
        if self.finished_at is None:
            self.finished_at = time.monotonic() if now is None else now

    def complete(self, now: float | None = None) -> float:
        if self.finished_at is not None:
            raise ValueError('The session has already stopped')
        active = self._active(time.monotonic() if now is None else now)
        duration = max(0.0, active - self.last_finish_active)
        self.completed.append(duration)
        self.last_finish_active = active
        return duration

    def snapshot(self, now: float | None = None) -> SessionSnapshot:
        now = time.monotonic() if now is None else now
        at = self.finished_at if self.finished_at is not None else now
        active = self._active(at)
        runs = len(self.completed)
        return SessionSnapshot(
            runs=runs, elapsed=max(0.0, at - self.started), active=active,
            average=sum(self.completed) / runs if runs else None,
            fastest=min(self.completed) if runs else None,
            latest=self.completed[-1] if runs else None,
            per_hour=runs * 3600 / active if active > 0 else 0.0,
        )


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return '—'
    total = int(max(0.0, seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f'{hours}:{minutes:02d}:{secs:02d}' if hours else f'{minutes:02d}:{secs:02d}'
