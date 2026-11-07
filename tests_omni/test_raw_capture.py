"""Android raw screencap decoding: strict size and pixel format contracts."""
from __future__ import annotations

import struct
import unittest

import numpy as np

from omniflow.raw_capture import decode_raw_screencap


def packet(width, height, fmt, pixels, *, dataspace=None):
    header = struct.pack('<III', width, height, fmt)
    if dataspace is not None:
        header += struct.pack('<I', dataspace)
    return header + pixels


class RawScreencapTests(unittest.TestCase):
    def test_rgba_12_byte_header_preserves_opencv_bgr(self):
        data = packet(2, 1, 1, bytes((10, 20, 30, 255, 40, 50, 60, 128)))
        result = decode_raw_screencap(data)
        self.assertEqual(result.shape, (1, 2, 3))
        self.assertEqual(result.dtype, np.uint8)
        self.assertEqual(result.tolist(), [[[30, 20, 10], [60, 50, 40]]])

    def test_rgba_16_byte_header_ignores_dataspace(self):
        data = packet(1, 1, 1, bytes((10, 20, 30, 255)), dataspace=142671872)
        self.assertEqual(decode_raw_screencap(data)[0, 0].tolist(), [30, 20, 10])

    def test_rgbx_rgb888_and_bgra_channels(self):
        cases = [
            (2, bytes((11, 22, 33, 0)), [33, 22, 11]),
            (3, bytes((11, 22, 33)), [33, 22, 11]),
            (5, bytes((33, 22, 11, 255)), [33, 22, 11]),
        ]
        for fmt, pixels, expected in cases:
            with self.subTest(format=fmt):
                self.assertEqual(decode_raw_screencap(packet(
                    1, 1, fmt, pixels))[0, 0].tolist(), expected)

    def test_rgb565_red_green_blue(self):
        pixels = struct.pack('<HHH', 0xF800, 0x07E0, 0x001F)
        result = decode_raw_screencap(packet(3, 1, 4, pixels))
        self.assertEqual(result.tolist(), [[[0, 0, 255], [0, 255, 0], [255, 0, 0]]])

    def test_malformed_frames_fail_instead_of_displaying_corruption(self):
        invalid = (
            b'',
            packet(0, 10, 1, b''),
            packet(20000, 1, 1, b''),
            packet(10000, 10000, 1, b''),
            packet(1, 1, 42, b'1234'),
            packet(2, 1, 1, b'1234'),
            packet(1, 1, 1, b'12345'),
            packet(1, 1, 1, b'1234') + b'\x00' * 10,
            struct.pack('>III', 1, 1, 1) + b'1234',
        )
        for blob in invalid:
            with self.subTest(blob=blob[:16]), self.assertRaises(ValueError):
                decode_raw_screencap(blob)


if __name__ == '__main__':
    unittest.main()
