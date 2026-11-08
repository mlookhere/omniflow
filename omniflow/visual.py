"""Pure visual-capture helpers shared by the editor and the universal engine.

Coordinates are normalized to 540×960 internally, but users select them by
pointing at a screenshot. This module deliberately does not depend on Qt.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

from swordx.detection import SIZE, normalize


def load_screenshot_file(path: Path) -> np.ndarray:
    """Load an explicit user-selected portrait capture; never touch a device.

    A screenshot with a different aspect ratio would silently shift the
    visually chosen tap targets after normalization, so reject it first.
    """
    path = Path(path)
    if path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.bmp'):
        raise ValueError('Choose a PNG, JPG or BMP screenshot')
    if not 0 < path.stat().st_size <= 32 * 1024 * 1024:
        raise ValueError('Screenshot file must be smaller than 32 MiB')
    data = path.read_bytes()
    if not 0 < len(data) <= 32 * 1024 * 1024:
        raise ValueError('Screenshot file must be smaller than 32 MiB')
    frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if (frame is None or frame.ndim != 3 or frame.shape[2] != 3
            or frame.dtype != np.uint8):
        raise ValueError('Screenshot must contain a normal 8-bit color image')
    height, width = frame.shape[:2]
    if (width * height > 16_000_000 or
            ((width, height) != (749, 1369) and
             (height <= width or abs(height / width - 16 / 9) > 0.02 * 16 / 9))):
        raise ValueError(
            'Choose a 9:16 portrait screenshot (or legacy 749×1369); '
            'other proportions would misalign touch targets')
    return normalize(frame)


def require_frame(frame: np.ndarray) -> np.ndarray:
    if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError('Capture a color game screenshot before selecting a zone')
    return normalize(frame)


def validate_zone(rect: Sequence[int]) -> tuple[int, int, int, int]:
    if len(rect) != 4 or any(type(v) is not int for v in rect):
        raise ValueError('Draw a rectangular area directly on the game screen')
    x1, y1, x2, y2 = rect
    if not (0 <= x1 < x2 <= SIZE[0] and 0 <= y1 < y2 <= SIZE[1]):
        raise ValueError('Selected zone is outside the game screen')
    if x2 - x1 < 3 or y2 - y1 < 3:
        raise ValueError('Draw a slightly larger zone around the feature to detect')
    return x1, y1, x2, y2


def validate_point(point: Sequence[int]) -> tuple[int, int]:
    if len(point) != 2 or any(type(v) is not int for v in point):
        raise ValueError('Select a location by clicking on the game screen')
    x, y = point
    if not (0 <= x < SIZE[0] and 0 <= y < SIZE[1]):
        raise ValueError('Selected location is outside the game screen')
    return x, y


def sample_color(frame: np.ndarray, point: Sequence[int], radius: int = 2) -> list[int]:
    """Median across a few pixels resists compression artifacts and aliasing."""
    image = require_frame(frame)
    x, y = validate_point(point)
    bounds = image[max(0, y - radius):min(SIZE[1], y + radius + 1),
                   max(0, x - radius):min(SIZE[0], x + radius + 1)]
    return [int(v) for v in np.median(bounds.reshape(-1, 3), axis=0)]


def sample_average(frame: np.ndarray, rect: Sequence[int]) -> list[int]:
    image = require_frame(frame)
    x1, y1, x2, y2 = validate_zone(rect)
    return [int(round(v)) for v in image[y1:y2, x1:x2].mean(axis=(0, 1))]


def action_summary(action: dict) -> str:
    kind = action.get('kind', 'unknown')
    if kind == 'tap':
        return 'Tap location selected on screen'
    if kind == 'double_tap':
        return 'Double-tap location selected on screen'
    if kind == 'hold':
        return f'Press and hold for {int(action.get("duration_ms", 650))} ms'
    if kind in ('drag', 'swipe'):
        return f'Swipe along recorded path · {int(action.get("duration_ms", 650))} ms'
    if kind == 'wait':
        return f'Wait {action.get("seconds", 0)} seconds'
    if kind == 'key':
        return f'Android key {action.get("code", "?")}'
    if kind == 'text':
        return 'Type text (contents hidden)'
    if kind == 'field_text':
        return 'Tap selected field and type text (contents hidden)'
    if kind == 'launch':
        return 'Launch selected game'
    if kind == 'count_run':
        return 'Mark session run complete'
    return 'Unknown action — review required'


def condition_summary(rule: dict) -> str:
    kind = rule.get('type', '?')
    op = 'NOT ' if rule.get('negate') else ''
    if kind == 'ocr':
        comparisons = {
            'contains': 'contains', 'equals': 'equals',
            'starts_with': 'starts with', 'ends_with': 'ends with',
        }
        comparison = rule.get('text_match', 'contains')
        mode = ('matches pattern' if rule.get('regex') else
                comparisons.get(comparison, 'has invalid comparison')
                if isinstance(comparison, str) else 'has invalid comparison')
        return f'When zone text {op}{mode} {rule.get("text", "")!r}'
    if kind == 'color':
        return f'When zone average color {op}matches sampled color'
    if kind == 'pixel':
        return f'When selected pixel {op}matches sampled color'
    return 'Unknown condition — review required'
