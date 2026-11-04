"""Low-overhead Android screen stream, isolated from workflow OCR and input.

The video source is the selected ADB device, never a Windows desktop window.
Android's screenrecord H.264 output is decoded by PyAV in a separate thread.
"""
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass

import cv2
import numpy as np

from swordx.adb import bundled_adb


# Keep dimensions encoder-friendly (multiples of 16), 9:16 and close to the UI's
# 360x640 preview. Reducing encoder size matters when using two emulators.
STREAM_SIZES = ((432, 768), (288, 512))
STREAM_BITRATE = 1_500_000
DISPLAY_SIZE = (360, 640)
TARGET_FPS = 30
PREVIEW_SEED_INTERVAL = 2.0
VIDEO_STALE_SECONDS = 3.5


def video_is_stale(now: float, last_frame_at: float) -> bool:
    """A stream may stay connected without producing any new video frames."""
    return now - last_frame_at >= VIDEO_STALE_SECONDS


def should_seed_preview(enabled: bool, video_active: bool, elapsed: float) -> bool:
    """Reuse an existing OCR frame until live video produces its first frame.

    Does not trigger an additional ADB screenshot or replace the H.264 stream.
    """
    return enabled and not video_active and elapsed >= PREVIEW_SEED_INTERVAL


def screenrecord_command(serial: str, size: tuple[int, int], adb_path: str = '') -> list[str]:
    """Bind capture to one selected emulator; never launch an unscoped adb stream."""
    if not serial or not serial.strip():
        raise ValueError('Select a MuMu ADB device before starting video capture')
    width, height = size
    if min(width, height) < 64 or width % 16 or height % 16:
        raise ValueError('Android H.264 dimensions must be multiples of 16')
    return [adb_path or bundled_adb(), '-s', serial, 'exec-out', 'screenrecord',
            '--output-format=h264', '--size', f'{width}x{height}',
            '--bit-rate', str(STREAM_BITRATE), '-']


def fit_preview(frame: np.ndarray, width: int = 360, height: int = 640) -> np.ndarray:
    """Resize video without distorting the emulator display."""
    h, w = frame.shape[:2]
    scale = min(width / w, height / h)
    resized = cv2.resize(frame, (max(1, round(w * scale)), max(1, round(h * scale))),
                         interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    canvas = np.zeros((height, width, frame.shape[2]), dtype=frame.dtype)
    x = (width - resized.shape[1]) // 2
    y = (height - resized.shape[0]) // 2
    canvas[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    return canvas


@dataclass
class AndroidVideoStream:
    """One ADB H.264 video subprocess with bounded, cancellable lifetime."""
    serial: str
    size: tuple[int, int] = STREAM_SIZES[0]
    adb_path: str = ''

    def __post_init__(self):
        self.process: subprocess.Popen | None = None
        self.stderr_file = None

    def __enter__(self):
        self.stderr_file = tempfile.TemporaryFile(mode='w+b')
        try:
            self.process = subprocess.Popen(screenrecord_command(self.serial, self.size, self.adb_path),
                                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                            stderr=self.stderr_file, bufsize=0,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return self
        except BaseException:
            self.close()
            raise

    def frames(self):
        """Yield decoded RGB frames; source is direct emulator video, not screenshots."""
        import av  # Optional until setup.bat installs the streaming dependency.
        if self.process is None or self.process.stdout is None:
            raise RuntimeError('Android video stream was not started')
        # Low latency: don't accumulate an unnecessarily large demux buffer.
        # Keep the initial H.264 access units: 'nobuffer' can discard the
        # first keyframe on some emulators and delay video until the next one.
        with av.open(self.process.stdout, mode='r', format='h264',
                     options={'flags': 'low_delay', 'probesize': '32768',
                              'analyzeduration': '0'}) as video:
            for frame in video.decode(video=0):
                yield frame.to_ndarray(format='rgb24')

    def error(self) -> str:
        if self.process and self.process.poll() is None:
            return 'No frames received from running Android encoder'
        if self.stderr_file:
            self.stderr_file.flush()
            self.stderr_file.seek(0)
            details = self.stderr_file.read(1600).decode(errors='replace').strip()
            if details:
                return details
        code = self.process.returncode if self.process else 'not started'
        return f'Android screenrecord exited ({code})'

    def interrupt(self):
        """Terminate encoder immediately, including when PyAV is blocked reading."""
        proc = self.process
        if proc and proc.poll() is None:
            proc.kill()

    def close(self):
        self.interrupt()
        if self.process:
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
            if self.process.stdout:
                self.process.stdout.close()
            self.process = None
        if self.stderr_file:
            self.stderr_file.close()
            self.stderr_file = None

    def __exit__(self, *_):
        self.close()
