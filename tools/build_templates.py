"""One-time asset generator; not required to run the automation."""
from pathlib import Path
import argparse
import json
import cv2

parser = argparse.ArgumentParser(description='Rebuild anchors from original numbered 749x1369 PNG screenshots')
parser.add_argument('screenshots', type=Path, help='Folder containing 1.png ... 10.png')
args = parser.parse_args()
ROOT = Path(__file__).resolve().parents[1]
REFERENCES = args.screenshots
AREAS = {
    'HOME': ('1', [(209,1290,251,1334),(335,1290,396,1350)]),
    'JOURNAL': ('2', [(23,200,219,231),(100,298,280,430)]),
    'DUNGEON_LIST': ('3', [(44,78,241,120),(260,1018,399,1051)]),
    'DETAILS': ('4', [(113,1164,338,1209),(530,1170,600,1200)]),
    'INVITE': ('6', [(122,94,634,141),(160,1169,262,1207)]),
    'LOBBY': ('9', [(227,270,454,308),(326,1013,424,1051)]),
    'RESULTS': ('10', [(34,941,112,978),(92,77,411,122)]),
}
MANIFEST = {'threshold': 0.80, 'states': {}}
for state, (number, boxes) in AREAS.items():
    raw = cv2.imread(str(REFERENCES / (number + '.png')))
    screenshot = cv2.resize(raw[40:], (540, 960), interpolation=cv2.INTER_AREA)
    results = []
    for idx, (x1, y1, x2, y2) in enumerate(boxes):
        x1, x2 = [round(x * 540 / 749) for x in (x1, x2)]
        y1, y2 = [round((y - 40) * 960 / 1329) for y in (y1, y2)]
        target = screenshot[y1:y2, x1:x2]
        filename = f'{state.lower()}_{idx}.png'
        cv2.imwrite(str(ROOT / 'assets' / 'anchors' / filename), target)
        results.append({'file': filename, 'rect': [x1, y1, x2, y2]})
    MANIFEST['states'][state] = results
(ROOT / 'assets' / 'manifest.json').write_text(json.dumps(MANIFEST, indent=2), encoding='utf-8')
for number in ['1','2','3','4','5','6','8','9','10']:
    raw=cv2.imread(str(REFERENCES / f'{number}.png'))
    cv2.imwrite(str(ROOT / 'tests' / 'fixtures' / f'{number}.jpg'), cv2.resize(raw[40:], (540, 960)),
               [cv2.IMWRITE_JPEG_QUALITY, 95])
