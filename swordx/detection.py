"""Fast, bounded-region template matching for a fixed portrait game layout."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

SIZE = (540, 960)
BASE_SIZE = (749, 1329)  # Game area inside the annotated 749x1369 references.
BASE_TOP = 40


@dataclass(frozen=True)
class Observation:
    state: str
    confidence: float
    anchors: tuple[float, ...] = ()
    trigger: int = 0


@dataclass(frozen=True)
class Anchor:
    gray: np.ndarray
    rect: tuple[int, int, int, int]


def normalize(image: np.ndarray) -> np.ndarray:
    """Strip emulator chrome if present and downscale for fast on-device matching."""
    height, width = image.shape[:2]
    expected = round(width * 16 / 9)
    if height > expected:
        # Original annotated references have 40px of desktop/emulator title bar.
        excess = height - expected
        offset = 40 if (width, height) == (749, 1369) else excess
        image = image[offset:]
    return cv2.resize(image, SIZE, interpolation=cv2.INTER_AREA)


def to_device(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    """Reference screenshot click -> ADB display coordinates (excluding titlebar)."""
    return (min(width - 1, max(0, round(x * width / BASE_SIZE[0]))),
            min(height - 1, max(0, round((y - BASE_TOP) * height / BASE_SIZE[1]))))


class ScreenDetector:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        data = json.loads((self.folder / 'manifest.json').read_text(encoding='utf-8'))
        self.threshold = float(data['threshold'])
        self.metadata = data
        if not data['states']:
            raise ValueError('Workflow has no recorded screen states')
        self.templates: dict[str, list[Anchor]] = {}
        for state, probes in data['states'].items():
            self.templates[state] = []
            for probe in probes:
                gray = cv2.imread(str(self.folder / 'anchors' / probe['file']), cv2.IMREAD_GRAYSCALE)
                if gray is None:
                    raise FileNotFoundError(probe['file'])
                self.templates[state].append(Anchor(gray, tuple(probe['rect'])))

    @staticmethod
    def _match(frame: np.ndarray, anchor: Anchor) -> float:
        x1, y1, x2, y2 = anchor.rect
        margin = 10  # Small registration shift, never a full-screen scan.
        search = frame[max(0, y1 - margin):min(SIZE[1], y2 + margin),
                       max(0, x1 - margin):min(SIZE[0], x2 + margin)]
        if search.shape[0] < anchor.gray.shape[0] or search.shape[1] < anchor.gray.shape[1]:
            return 0.0
        corr = cv2.matchTemplate(search, anchor.gray, cv2.TM_CCOEFF_NORMED)
        return max(0.0, cv2.minMaxLoc(corr)[1])

    def detect(self, frame: np.ndarray) -> Observation:
        gray = cv2.cvtColor(normalize(frame), cv2.COLOR_BGR2GRAY)
        ranked: list[Observation] = []
        for name, probes in self.templates.items():
            scores = tuple(self._match(gray, anchor) for anchor in probes)
            info = self.metadata.get('actions', {}).get(name, {})
            if info.get('match', 'all') == 'any':
                index = max(range(len(scores)), key=lambda i: (scores[i], -i))
                confidence = scores[index]
            else:  # Original built-in and legacy 2-probe states still require both.
                confidence, index = min(scores), 0
            ranked.append(Observation(name, confidence, scores, index))
        # Explicit wait states suppress actions when a known no-tap control
        # is visible (e.g. Cancel during matchmaking). List order is irrelevant.
        passive = [item for item in ranked if item.confidence >= self.threshold and
                   self.metadata.get('actions', {}).get(item.state, {}).get('passive')]
        best = max(passive or ranked, key=lambda x: (x.confidence, x.state))
        if best.confidence < self.threshold:
            return Observation('UNKNOWN', best.confidence, best.anchors)
        return best

    @staticmethod
    def normal_selected(frame: np.ndarray) -> bool:
        """Look only at the Normal difficulty pill, away from text/annotations."""
        sample = normalize(frame)
        # Interior of the Normal selector, offset from the label and border.
        points = [(198, 1297), (310, 1319), (351, 1299)]
        colors = []
        for x, y in points:
            xx, yy = to_device(x, y, SIZE[0], SIZE[1])
            colors.append(sample[yy, xx].astype(np.float32))
        bgr = np.mean(colors, axis=0)
        blue, green, red = bgr
        # Selected: saturated yellow-green (~RGB 205,212,100).
        # Unselected: charcoal grey (~RGB 35,35,35).
        return bool(green > 120 and red > 115 and blue < green * 0.88)
