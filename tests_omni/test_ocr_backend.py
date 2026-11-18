"""OCR installation contract, typed output and failed-inference safety."""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from unittest.mock import patch

import numpy as np

from omniflow.engine import OCREngine, WorkflowError


class OCRBackendTests(unittest.TestCase):
    def setUp(self):
        self.previous = OCREngine._instance, OCREngine._checked, OCREngine._error
        self.addCleanup(self.restore)
        OCREngine._instance = None
        OCREngine._checked = False
        OCREngine._error = ''
        self.frame = np.zeros((90, 320, 3), np.uint8)

    def restore(self):
        OCREngine._instance, OCREngine._checked, OCREngine._error = self.previous

    def test_modern_result_uses_typed_texts_and_reuses_engine(self):
        seen = []
        class FakeEngine:
            def __init__(self):
                seen.append('initialize')
            def __call__(self, image):
                self_frame = image  # actual ROI must reach OCR
                if self_frame.shape != (90, 320, 3):
                    raise AssertionError('wrong ROI')
                return types.SimpleNamespace(txts=('Start', 'Now'))
        with patch.dict(sys.modules, {'rapidocr': types.SimpleNamespace(RapidOCR=FakeEngine)}):
            self.assertEqual(OCREngine.read(self.frame), 'Start Now')
            self.assertEqual(OCREngine.read(self.frame), 'Start Now')
        self.assertEqual(seen, ['initialize'])

    def test_missing_backend_is_actionable_and_fails_closed(self):
        with patch.dict(sys.modules, {'rapidocr': None}):
            with self.assertRaisesRegex(WorkflowError, 'setup.bat'):
                OCREngine.read(self.frame)

    def test_inference_failure_is_descriptive_not_silent(self):
        class BrokenEngine:
            def __call__(self, _):
                raise RuntimeError('model corrupted')
        OCREngine._instance = BrokenEngine()
        OCREngine._checked = True
        with self.assertRaisesRegex(WorkflowError, 'model corrupted'):
            OCREngine.read(self.frame)

    def test_standard_requirements_install_ocr(self):
        root = __import__('pathlib').Path(__file__).resolve().parents[1]
        requirements = (root / 'requirements.txt').read_text(encoding='utf-8')
        self.assertIn('rapidocr>=3.10', requirements)
        self.assertIn('onnxruntime>=1.23', requirements)
        self.assertNotIn('rapidocr-onnxruntime', requirements)
        setup = (root / 'setup.bat').read_text(encoding='utf-8')
        self.assertIn('tools\\check_requirements.py', setup)


if __name__ == '__main__':
    unittest.main()
