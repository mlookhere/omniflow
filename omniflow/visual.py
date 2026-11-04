"""Pure visual-capture helpers; editors select screen zones instead of typing XY."""
from __future__ import annotations

from typing import Sequence
import cv2
import numpy as np

SIZE = (540, 960)

def require_frame(frame: np.ndarray) -> np.ndarray:
    if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("Capture a color game screenshot first")
    return cv2.resize(frame, SIZE, interpolation=cv2.INTER_AREA) if frame.shape[:2] != (960, 540) else frame

def validate_zone(rect: Sequence[int]) -> tuple[int,int,int,int]:
    if len(rect) != 4 or any(type(v) is not int for v in rect):
        raise ValueError("Draw a rectangle on the game screenshot")
    x1,y1,x2,y2 = rect
    if not (0 <= x1 < x2 <= SIZE[0] and 0 <= y1 < y2 <= SIZE[1]) or x2-x1 < 3 or y2-y1 < 3:
        raise ValueError("Selected zone is too small or outside the game screen")
    return x1,y1,x2,y2

def validate_point(point: Sequence[int]) -> tuple[int,int]:
    if len(point) != 2 or any(type(v) is not int for v in point):
        raise ValueError("Click the target on the game screenshot")
    x,y = point
    if not (0 <= x < SIZE[0] and 0 <= y < SIZE[1]):
        raise ValueError("Target outside game screen")
    return x,y

def sample_color(frame: np.ndarray, point: Sequence[int], radius: int = 2) -> list[int]:
    frame = require_frame(frame)
    x,y = validate_point(point)
    values = frame[max(0,y-radius):min(SIZE[1],y+radius+1),
                   max(0,x-radius):min(SIZE[0],x+radius+1)]
    return [int(v) for v in np.median(values.reshape(-1,3),axis=0)]

def sample_average(frame: np.ndarray, rect: Sequence[int]) -> list[int]:
    frame = require_frame(frame)
    x1,y1,x2,y2 = validate_zone(rect)
    return [int(round(v)) for v in frame[y1:y2,x1:x2].mean(axis=(0,1))]

def action_summary(action: dict) -> str:
    kind = action.get("kind")
    if kind == "tap": return "Tap selected screen location"
    if kind == "double_tap": return "Double tap selected location"
    if kind == "hold": return f'Hold {int(action.get("duration_ms",650))} ms'
    if kind in ("drag","swipe"): return f'Swipe recorded path · {int(action.get("duration_ms",650))} ms'
    if kind == "wait": return f'Wait {action.get("seconds",0)} seconds'
    if kind == "key": return f'Android key {action.get("code","?")}'
    if kind == "text": return f'Type {str(action.get("text",""))[:35]!r}'
    if kind == "launch": return "Launch selected game"
    if kind == "count_run": return "Mark completed run"
    return "Unknown action"

def condition_summary(rule: dict) -> str:
    kind = rule.get("type")
    op = "NOT " if rule.get("negate") else ""
    if kind == "ocr":
        mode = "matches pattern" if rule.get("regex") else "contains"
        return f'When zone text {op}{mode} {rule.get("text","")!r}'
    if kind == "color": return f'When zone color {op}matches sample'
    if kind == "pixel": return f'When selected pixel {op}matches sample'
    return "Unknown condition"
