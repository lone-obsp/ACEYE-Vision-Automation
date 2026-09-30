"""PaddleOCR 3.x adapter with explicit local model directories."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import AdapterError, empty_result, jsonable, require_numpy, resolve_local_path, result_from_object


class PaddleOcrAdapter:
    name = "ocr"

    def __init__(self, config: dict[str, Any] | None = None, root: Path | None = None) -> None:
        self.config = dict(config or {})
        self.root = Path(root or ".").resolve()
        self._pipeline: Any = None

    def load(self) -> "PaddleOcrAdapter":
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise AdapterError(self.name, "paddleocr 3.x is not installed", hint="Install PaddlePaddle GPU and paddleocr from requirements-models.txt") from exc
        # Supplying local directories prevents PaddleX from silently downloading a
        # default model. Both names are PaddleOCR 3.x constructor parameters.
        base = self.config.get("weights")
        det_value = self.config.get("det_model_dir")
        rec_value = self.config.get("rec_model_dir")
        if base and not det_value and not rec_value:
            # Existing ACEYE configs use weights=models/ocr with the standard
            # det/rec subdirectories; retain that editable shorthand.
            det_value, rec_value = str(Path(base) / "det"), str(Path(base) / "rec")
        det = resolve_local_path(self.root, det_value or "models/ocr/det", setting=self.name)
        rec = resolve_local_path(self.root, rec_value or "models/ocr/rec", setting=self.name)
        kwargs: dict[str, Any] = {
            "text_detection_model_dir": str(det),
            "text_recognition_model_dir": str(rec),
            "device": ("gpu:0" if str(self.config.get("device", "gpu:0")).startswith("cuda") else self.config.get("device", "cpu")),
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": bool(self.config.get("use_textline_orientation", False)),
        }
        if self.config.get("lang"):
            kwargs["lang"] = self.config["lang"]
        try:
            self._pipeline = PaddleOCR(**kwargs)
        except Exception as exc:
            raise AdapterError(self.name, f"failed to load PaddleOCR models: {exc}") from exc
        return self

    def describe(self) -> str:
        return "PaddleOCR 3.x (local detection + recognition models)"

    def infer(self, frame_bgr: Any, context: dict[str, Any] | None = None) -> dict[str, Any]:
        if frame_bgr is None:
            return empty_result()
        if self._pipeline is None:
            raise AdapterError(self.name, "adapter is not loaded", hint="Call adapter.load() after constructing it")
        require_numpy(self.name)
        try:
            prediction = self._pipeline.predict(frame_bgr)
            if not isinstance(prediction, (list, tuple)):
                prediction = list(prediction)
            item = result_from_object(prediction[0]) if prediction else {}
        except Exception as exc:
            raise AdapterError(self.name, f"PaddleOCR inference failed: {exc}") from exc
        return self._parse_result(item)

    @staticmethod
    def _parse_result(result: Any) -> dict[str, Any]:
        output = empty_result()
        result = result_from_object(result)
        if not isinstance(result, dict):
            return output
        # Paddle returns numpy arrays for rec_boxes/rec_polys; do not use
        # ``a or b`` on them because numpy intentionally rejects truth tests.
        if isinstance(result.get("res"), dict):
            result = result["res"]
        texts = result.get("rec_texts") if result.get("rec_texts") is not None else result.get("texts", [])
        scores = result.get("rec_scores") if result.get("rec_scores") is not None else result.get("scores", [])
        if result.get("rec_boxes") is not None:
            boxes = result["rec_boxes"]
        elif result.get("dt_polys") is not None:
            boxes = result["dt_polys"]
        else:
            boxes = result.get("rec_polys", [])
        texts, scores, boxes = jsonable(texts), jsonable(scores), jsonable(boxes)
        if not isinstance(texts, list):
            texts = [texts]
        if not isinstance(scores, list):
            scores = [scores]
        if not isinstance(boxes, list):
            boxes = [boxes]
        for index, text in enumerate(texts):
            if not isinstance(text, str) or not text.strip():
                continue
            box = boxes[index] if index < len(boxes) else []
            polygon = None
            if isinstance(box, list) and box and isinstance(box[0], (list, tuple)):
                polygon = box
                flat = [point for point in box if isinstance(point, (list, tuple)) and len(point) >= 2 for point in [point]]
                if flat:
                    xs, ys = [float(point[0]) for point in flat], [float(point[1]) for point in flat]
                    box = [min(xs), min(ys), max(xs), max(ys)]
            if not isinstance(box, list) or len(box) < 4:
                box = []
            entry: dict[str, Any] = {
                "text": text,
                "confidence": float(scores[index]) if index < len(scores) else 0.0,
                "bbox": [float(v) for v in box[:4]],
            }
            if polygon is not None:
                entry["polygon"] = polygon
            output["texts"].append(entry)
        return output

    def close(self) -> None:
        self._pipeline = None
