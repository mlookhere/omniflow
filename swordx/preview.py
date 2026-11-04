"""Low-overhead Android screen stream, isolated from workflow OCR and input.

The video source is the selected ADB device, never a Windows desktop window.
Android's screenrecord H.264 output is decoded by PyAV in a separate thread.
"""
from __future__ import annotations

import errno
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
# Android MediaCodec may reject one encoder profile but accept a lower one.
# The selected emulator is the only device that runs this encoder.
STREAM_BITRATES = (STREAM_BITRATE, 650_000)
DISPLAY_SIZE = (360, 640)
TARGET_FPS = 30
PREVIEW_SEED_INTERVAL = 2.0
VIDEO_STALE_SECONDS = 4.5
VIDEO_STARTUP_SECONDS = 10.0


def video_is_stale(now: float, last_frame_at: float) -> bool:
    """A stream may stay connected without producing any new video frames."""
    return now - last_frame_at >= VIDEO_STALE_SECONDS


@dataclass
class StreamRetry:
    """Retry without a permanent failure latch; reduce encoder load if needed."""
    failures: int = 0

    def profile(self, landscape: bool = False) -> tuple[tuple[int, int], int]:
        index = min(self.failures, len(STREAM_SIZES) - 1)
        width, height = STREAM_SIZES[index]
        return ((height, width) if landscape else (width, height), STREAM_BITRATES[index])

    def complete(self, decoded_frames: int, elapsed: float) -> float:
        # A few decoding glitches must not permanently degrade a good source.
        if elapsed >= 3.0 and decoded_frames / elapsed >= 18.0:
            self.failures = 0
            return 0.2
        self.failures += 1
        return min(8.0, 0.5 * 2 ** min(self.failures - 1, 4))

    def should_log(self) -> bool:
        # Report first, second and exponentially spaced failures, not every loop.
        return self.failures in (1, 2) or self.failures & (self.failures - 1) == 0


def should_seed_preview(enabled: bool, video_active: bool, elapsed: float) -> bool:
    """Reuse an existing OCR frame until live video produces its first frame.

    Does not trigger an additional ADB screenshot or replace the H.264 stream.
    """
    return enabled and not video_active and elapsed >= PREVIEW_SEED_INTERVAL


def screenrecord_command(serial: str, size: tuple[int, int], adb_path: str = '',
                         bitrate: int = STREAM_BITRATE) -> list[str]:
    """Bind capture to one selected emulator; never launch an unscoped adb stream."""
    if not serial or not serial.strip():
        raise ValueError('Select a MuMu ADB device before starting video capture')
    width, height = size
    if min(width, height) < 64 or width % 16 or height % 16:
        raise ValueError('Android H.264 dimensions must be multiples of 16')
    if bitrate < 100_000 or bitrate > 10_000_000:
        raise ValueError('Android H.264 bitrate outside supported capture range')
    return [adb_path or bundled_adb(), '-s', serial, 'exec-out', 'screenrecord',
            '--output-format=h264', '--size', f'{width}x{height}',
            '--bit-rate', str(bitrate), '-']


def screen_is_unchanged(video_rgb: np.ndarray, screenshot_bgr: np.ndarray) -> bool:
    """Identify legitimately idle Android surfaces before restarting an encoder.

    Some Android surface encoders do not emit frames for unchanged screens. A
    single low-rate ADB screenshot distinguishes a static menu from a stuck
    stream; no extra H.264 encoder is started. Comparison is downscaled to the
    actual preview resolution for low overhead.
    """
    if video_rgb is None or screenshot_bgr is None:
        return False
    if video_rgb.ndim != 3 or video_rgb.shape[2] != 3 or screenshot_bgr.ndim != 3:
        return False
    # Letterboxed 4:3 and ultrawide game screens must be compared at the
    # same aspect ratio as the video. Stretching a screenshot causes false
    # stall detection and unnecessary H.264 encoder restarts.
    sampled = fit_preview(screenshot_bgr, video_rgb.shape[1], video_rgb.shape[0])
    difference = cv2.absdiff(video_rgb, cv2.cvtColor(sampled, cv2.COLOR_BGR2RGB))
    return (float(difference.mean()) < 8.0 and
            float(np.count_nonzero(np.max(difference, axis=2) > 35) / difference.shape[0] /
                  difference.shape[1]) < 0.01)


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
    bitrate: int = STREAM_BITRATE

    def __post_init__(self):
        self.process: subprocess.Popen | None = None
        self.stderr_file = None
        self._last_error = ''
        self._interrupted = False

    def __enter__(self):
        self.stderr_file = tempfile.TemporaryFile(mode='w+b')
        try:
            self.process = subprocess.Popen(screenrecord_command(self.serial, self.size, self.adb_path,
                                                              self.bitrate),
                                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                            stderr=self.stderr_file, bufsize=0,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return self
        except BaseException:
            self.close()
            raise

    def frames(self):
        """Decode an Android Annex-B H.264 byte stream without container probing.

        ``adb exec-out screenrecord`` produces raw video on a non-seekable Windows
        pipe, not an MP4 file. Feeding that pipe into ``av.open`` asks FFmpeg to
        probe/seek a container and can return EINVAL (error 22), especially when
        the stream is interrupted or its first SPS arrives in a short read.
        The codec parser accepts arbitrary byte chunks and reassembles packets.
        A stream restart creates a new decoder, so no old frame state leaks.
        """
        import av  # Lazy import: no decoder is allocated for idle instances.
        if self.process is None or self.process.stdout is None:
            raise RuntimeError('Android video stream was not started')
        decoder = av.CodecContext.create('h264', 'r')
        source = self.process.stdout
        while True:
            data = source.read(32768)
            if not data:
                break
            for packet in decoder.parse(data):
                for frame in decoder.decode(packet):
                    yield frame.to_ndarray(format='rgb24')
        # Killing the process truncates the current H.264 packet. Never flush
        # that partial access unit through libavcodec (it may raise EINVAL).
        if self._interrupted:
            return
        for packet in decoder.parse(b''):
            for frame in decoder.decode(packet):
                yield frame.to_ndarray(format='rgb24')
        for frame in decoder.decode(None):
            yield frame.to_ndarray(format='rgb24')

    @staticmethod
    def explain_failure(exc: Exception, source: str = '') -> str:
        """Retain the original error and identify common encoder/decoder faults."""
        code = getattr(exc, 'errno', None)
        if code in (errno.EINVAL, -errno.EINVAL) or 'error 22' in str(exc).lower():
            detail = 'Android H.264 encoder/decoder rejected the stream (error 22 / invalid argument)'
        else:
            detail = f'{type(exc).__name__}: {exc}'
        if source:
            detail += f' | ADB: {source.strip()[:280]}'
        return detail

    def error(self) -> str:
        if self.stderr_file:
            try:
                self.stderr_file.flush()
                self.stderr_file.seek(0)
                details = self.stderr_file.read(1600).decode(errors='replace').strip()
                if details:
                    return details
            except (OSError, ValueError):
                pass
        if self._last_error:
            return self._last_error
        if self.process and self.process.poll() is None:
            return 'No frames received from running Android encoder'
        code = self.process.returncode if self.process else 'not started'
        return f'Android screenrecord exited ({code})'

    def interrupt(self):
        """Terminate encoder immediately, including when PyAV is blocked reading."""
        self._interrupted = True
        proc = self.process
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass  # Process may have exited between poll() and kill().

    def close(self):
        # Retain Android's actual diagnostic across context-manager cleanup.
        if self.stderr_file:
            try:
                self.stderr_file.flush()
                self.stderr_file.seek(0)
                self._last_error = self.stderr_file.read(1600).decode(errors='replace').strip()
            except OSError:
                pass
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