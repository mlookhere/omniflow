"""Read-only preview health accounting, independent of MuMu and Qt.

Track *displayed* frames, not nominal encoder output. This intentionally does
not infer smooth video from a working low-FPS screenshot fallback.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


ERROR_22 = re.compile(r'(?i)(?:\berror\s*22\b|\berrno\s*22\b|\beINVAL\b|\(22\))')


@dataclass
class CaptureHealth:
    started: float
    video_frames: int = 0
    snapshot_frames: int = 0
    last_preview_at: float | None = None
    last_video_at: float | None = None
    max_preview_gap: float = 0.0
    max_video_gap: float = 0.0
    errors: list[str] = field(default_factory=list)
    error22_events: int = 0
    error22_recovered: int = 0
    recovery_seconds: list[float] = field(default_factory=list)
    _error22_pending_at: float | None = None

    def observe(self, now: float, kind: str | None = None) -> None:
        if now < self.started:
            raise ValueError('Sample time precedes diagnostic start')
        self.max_preview_gap = max(self.max_preview_gap, now - (self.last_preview_at or self.started))
        self.max_video_gap = max(self.max_video_gap, now - (self.last_video_at or self.started))
        if kind == 'video':
            self.video_frames += 1
            self.last_video_at = now
            self.last_preview_at = now
            if self._error22_pending_at is not None:
                self.error22_recovered += 1
                self.recovery_seconds.append(max(0.0, now - self._error22_pending_at))
                self._error22_pending_at = None
        elif kind == 'snapshot':
            self.snapshot_frames += 1
            self.last_preview_at = now
        elif kind is not None:
            raise ValueError(f'Unknown preview frame type: {kind}')

    def record_error(self, now: float, message: str) -> None:
        self.errors.append(str(message))
        if ERROR_22.search(message):
            self.error22_events += 1
            # Consecutive Error 22 logs are one unresolved recovery window.
            if self._error22_pending_at is None:
                self._error22_pending_at = now

    def summary(self, now: float) -> dict:
        self.observe(now)
        elapsed = max(0.001, now - self.started)
        return {
            'elapsed_seconds': round(elapsed, 2),
            'displayed_video_frames': self.video_frames,
            'displayed_video_fps': round(self.video_frames / elapsed, 2),
            'fallback_snapshots': self.snapshot_frames,
            'max_preview_gap_seconds': round(self.max_preview_gap, 2),
            'max_video_gap_seconds': round(self.max_video_gap, 2),
            'error22_events': self.error22_events,
            'error22_recovered': self.error22_recovered,
            'error22_unresolved': int(self._error22_pending_at is not None),
            'max_error22_recovery_seconds': (round(max(self.recovery_seconds), 2)
                                              if self.recovery_seconds else None),
            'reported_stream_errors': len(self.errors),
            'recent_errors': self.errors[-10:],
        }


def evaluate_capture(report: dict, *, allow_snapshot_only: bool = False,
                     min_video_fps: float = 0.0,
                     max_preview_gap: float = 0.0) -> list[str]:
    """Only pass criteria the diagnostic can actually establish."""
    failures = []
    video, fallback = report['displayed_video_frames'], report['fallback_snapshots']
    if not video and not fallback:
        failures.append('No preview frames received')
    if not video and not allow_snapshot_only:
        failures.append('H.264 video never delivered a frame (snapshot-only fallback)')
    if min_video_fps and report['displayed_video_fps'] < min_video_fps:
        failures.append(f'Video FPS {report["displayed_video_fps"]:.2f} below {min_video_fps:.2f}')
    if max_preview_gap and report['max_preview_gap_seconds'] > max_preview_gap:
        failures.append(f'Longest preview blackout {report["max_preview_gap_seconds"]:.2f}s '
                        f'exceeds {max_preview_gap:.2f}s')
    if report['error22_unresolved']:
        failures.append('Error 22 recovery not confirmed by a subsequent video frame')
    return failures
