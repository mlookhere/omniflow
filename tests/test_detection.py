import unittest
from pathlib import Path

import cv2
import numpy as np

from swordx.detection import ScreenDetector, to_device
from swordx.engine import decide

ROOT = Path(__file__).resolve().parents[1]
IMAGES = {'1': 'HOME', '2': 'JOURNAL', '3': 'DUNGEON_LIST',
          '4': 'DETAILS', '5': 'DETAILS', '6': 'INVITE',
          '8': 'INVITE', '9': 'LOBBY', '10': 'RESULTS'}


class DetectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detector = ScreenDetector(ROOT / 'assets')

    def test_all_uploaded_screens(self):
        for image, state in IMAGES.items():
            with self.subTest(image=image):
                shot = cv2.imread(str(ROOT / 'tests' / 'fixtures' / (image + '.jpg')))
                found = self.detector.detect(shot)
                self.assertEqual(found.state, state)
                self.assertGreaterEqual(found.confidence, .80)
                self.assertEqual(len(found.anchors), 2)

    def test_screenshot_scaled_from_android_resolution(self):
        frame = cv2.imread(str(ROOT / 'tests' / 'fixtures' / '6.jpg'))
        expanded = cv2.resize(frame, (2160, 3840))
        self.assertEqual(self.detector.detect(expanded).state, 'INVITE')

    def test_uncertain_screen_does_not_trigger(self):
        for image in (np.zeros((960, 540, 3), np.uint8),
                      np.full((960, 540, 3), 235, np.uint8),
                      np.random.default_rng(123).integers(0, 255, (960, 540, 3), dtype=np.uint8)):
            self.assertEqual(self.detector.detect(image).state, 'UNKNOWN')

    def test_reference_detail_starts_on_nightmare(self):
        for number in ('4', '5'):
            frame = cv2.imread(str(ROOT / 'tests' / 'fixtures' / (number + '.jpg')))
            self.assertFalse(self.detector.normal_selected(frame))
            self.assertEqual(decide('DETAILS', False).action, 'select_normal')
            self.assertEqual(decide('DETAILS', True).action, 'find_match')

    def test_home_badges_are_not_required(self):
        frame = cv2.imread(str(ROOT / 'tests' / 'fixtures' / '1.jpg'))
        # Hide the two optional red notification badges without altering the book itself.
        cv2.rectangle(frame, (186, 891), (207, 921), (27, 30, 33), -1)
        cv2.rectangle(frame, (288, 867), (311, 899), (90, 122, 146), -1)
        self.assertEqual(self.detector.detect(frame).state, 'HOME')

    def test_small_frame_translation_is_tolerated(self):
        frame = cv2.imread(str(ROOT / 'tests' / 'fixtures' / '3.jpg'))
        matrix = np.float32([[1, 0, 3], [0, 1, -4]])
        shifted = cv2.warpAffine(frame, matrix, (540, 960))
        self.assertEqual(self.detector.detect(shifted).state, 'DUNGEON_LIST')

    def test_normal_pill_switch_changes_action_without_step_history(self):
        frame = cv2.imread(str(ROOT / 'tests' / 'fixtures' / '4.jpg'))
        for x, y in ((198, 1297), (310, 1319), (351, 1299)):
            x, y = to_device(x, y, 540, 960)
            frame[max(0, y-3):y+4, x-3:x+4] = (98, 210, 205)
        self.assertEqual(self.detector.detect(frame).state, 'DETAILS')
        self.assertTrue(self.detector.normal_selected(frame))
        self.assertEqual(decide('DETAILS', self.detector.normal_selected(frame)).action, 'find_match')

    def test_device_tap_coordinates(self):
        x, y = to_device(545, 1186, 2160, 3840)
        self.assertTrue(1300 < x < 1700)
        self.assertTrue(3000 < y < 3700)


if __name__ == '__main__':
    unittest.main()
