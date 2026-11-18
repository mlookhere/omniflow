"""Strict Android raw screencap decoder, for opt-in capture profiling.

Android's uncompressed screencap emits width, height and pixel format as
little-endian uint32, followed by an optional dataspace uint32 (newer Android).
The remaining bytes are tightly packed rows, not padded framebuffer stride.
Reject unknown layouts instead of interpreting arbitrary bytes as a screen.
"""
from __future__ import annotations

import struct

import cv2
import numpy as np


_BYTES_PER_PIXEL = {1: 4, 2: 4, 3: 3, 4: 2, 5: 4}
_MAX_PIXELS = 24_000_000


def decode_raw_screencap(data: bytes) -> np.ndarray:
    """Decode supported Android raw RGB formats to OpenCV's BGR uint8 image."""
    if not isinstance(data, bytes) or len(data) < 12:
        raise ValueError('Raw screencap header is missing or truncated')
    width, height, pixel_format = struct.unpack_from('<III', data)
    if not (0 < width <= 16384 and 0 < height <= 16384 and
            width * height <= _MAX_PIXELS):
        raise ValueError(f'Raw screencap dimensions are invalid: {width}x{height}')
    bpp = _BYTES_PER_PIXEL.get(pixel_format)
    if bpp is None:
        raise ValueError(f'Unsupported raw screencap pixel format: {pixel_format}')
    pixel_bytes = width * height * bpp
    if len(data) == pixel_bytes + 12:
        offset = 12
    elif len(data) == pixel_bytes + 16:
        offset = 16
    else:
        raise ValueError(
            f'Raw screencap byte count mismatch: got {len(data)}, '
            f'expected {pixel_bytes + 12} or {pixel_bytes + 16}')

    if pixel_format == 4:  # RGB565: RRRRRGGG GGGBBBBB in uint16
        packed = np.frombuffer(data, dtype='<u2', count=width * height,
                               offset=offset).reshape(height, width).astype(np.uint32)
        red = ((packed >> 11 & 31) * 255 + 15) // 31
        green = ((packed >> 5 & 63) * 255 + 31) // 63
        blue = ((packed & 31) * 255 + 15) // 31
        return np.stack((blue, green, red), axis=-1).astype(np.uint8)

    channels = bpp
    pixels = np.frombuffer(data, dtype=np.uint8, count=width * height * channels,
                           offset=offset).reshape(height, width, channels)
    if pixel_format in (1, 2):  # RGBA, RGBX
        return cv2.cvtColor(pixels, cv2.COLOR_RGBA2BGR)
    if pixel_format == 3:  # RGB888
        return cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR)
    return cv2.cvtColor(pixels, cv2.COLOR_BGRA2BGR)  # BGRA8888
