"""Dependency-free contract tests for parsing and factory behavior.

Run with ``python -m unittest ACEYE/backend/aceye/adapters/test_adapters.py``.
No GPU, model weights, or third-party runtime is imported by these tests.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from . import AdapterError, create_adapter
from .common import extract_json, parse_actions
from .detection import YoloByteTrackAdapter
from .ocr import PaddleOcrAdapter


class _Value:
    def __init__(self, value):
        self.value = value

    def tolist(self):
        return self.value


class _Boxes:
    xyxy = _Value([[1, 2, 30, 40]])
    conf = _Value([0.91])
    cls = _Value([2])
    id = _Value([7])


class _Masks:
    xy = _Value([[[1, 2], [3, 4], [5, 6]]])


class _DetectionResult:
    boxes = _Boxes()
    masks = _Masks()
    names = {2: "enemy"}


class AdapterContractTests(unittest.TestCase):
    def test_factory_is_lazy(self):
        adapter = create_adapter("detection", {}, Path("."))
        self.assertIsInstance(adapter, YoloByteTrackAdapter)
        self.assertIsNone(adapter._model)

    def test_yolo_parser_contract(self):
        result = YoloByteTrackAdapter._parse_result(_DetectionResult())
        self.assertEqual(result["objects"][0]["id"], 7)
        self.assertEqual(result["objects"][0]["label"], "enemy")
        self.assertEqual(result["objects"][0]["bbox"], [1.0, 2.0, 30.0, 40.0])
        self.assertEqual(len(result["objects"][0]["polygon"]), 3)

    def test_ocr_parser_contract(self):
        result = PaddleOcrAdapter._parse_result({"rec_texts": ["HP 72"], "rec_scores": [0.98], "rec_boxes": [[1, 2, 30, 40]]})
        self.assertEqual(result["texts"][0]["text"], "HP 72")
        self.assertEqual(result["texts"][0]["bbox"], [1.0, 2.0, 30.0, 40.0])

    def test_reasoner_json_action_filter(self):
        parsed = extract_json('```json{"actions":[{"type":"key_down","key":"F1"},{"type":"shell"}]} ```')
        self.assertEqual(len(parse_actions(parsed["actions"])), 1)

    def test_unknown_adapter_is_actionable(self):
        with self.assertRaises(AdapterError):
            create_adapter("missing")


if __name__ == "__main__":
    unittest.main()

