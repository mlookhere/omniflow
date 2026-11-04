import unittest
import numpy as np
from omniflow.visual import sample_color, validate_zone, validate_point, action_summary

class VisualCaptureTests(unittest.TestCase):
    def test_zone_and_point(self):
        self.assertEqual(validate_zone((0,0,540,960)), (0,0,540,960))
        with self.assertRaises(ValueError):
            validate_zone((0,0,1,1))
        with self.assertRaises(ValueError):
            validate_point((540,3))
    def test_auto_sample_color(self):
        frame=np.full((960,540,3),(30,60,120),dtype=np.uint8)
        self.assertEqual(sample_color(frame,[90,300]),[30,60,120])
    def test_readable_action(self):
        self.assertNotIn('240',action_summary({'kind':'tap','point':[240,500]}))
if __name__ == '__main__':
    unittest.main()
