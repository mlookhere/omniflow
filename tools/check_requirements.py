"""Fail-fast dependency and OCR inference self-test for Windows setup and CI.

No ADB, network, emulator, user workflow, or input actions are performed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def check() -> None:
    import av  # noqa: F401 - video codec
    import cv2
    import numpy as np
    import onnxruntime  # noqa: F401 - RapidOCR inference backend
    import psutil  # noqa: F401 - hardware telemetry
    from PySide6.QtCore import Qt  # noqa: F401 - application GUI
    from omniflow.engine import OCREngine

    frame = np.full((100, 350, 3), 255, dtype=np.uint8)
    cv2.putText(frame, 'READY', (12, 70), cv2.FONT_HERSHEY_SIMPLEX,
                1.5, (0, 0, 0), 3)
    text = OCREngine.read(frame)
    if not isinstance(text, str):
        raise RuntimeError('OCR did not return recognized text')
    print(f'Dependency and OCR inference check passed (recognized: {text!r}).')


if __name__ == '__main__':
    try:
        check()
    except Exception as exc:
        print(f'OmniFlow dependency check FAILED: {type(exc).__name__}: {exc}',
              file=sys.stderr)
        raise SystemExit(1) from exc
