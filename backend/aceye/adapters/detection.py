"""Ultralytics YOLO26 segmentation + persistent ByteTrack adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import AdapterError, empty_result, jsonable, require_numpy, resolve_local_path


class YoloByteTrackAdapter:
    name = "detection"

    def __init__(self, config: dict[str, Any] | None = None, root: Path | None = None) -> None:
        self.config = dict(config or {})
        self.root = Path(root or ".").resolve()
        self._model: Any = None
        self._weights: Path | None = None
        self._backend = "pytorch"

    def load(self) -> "YoloByteTrackAdapter":
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise AdapterError(self.name, "ultralytics is not installed", hint="Install requirements-models.txt in the backend environment") from exc
        requested = resolve_local_path(
            self.root,
            self.config.get("weights", "models/yolo26n-seg.pt"),
            setting=self.name,
        )
        backend = str(self.config.get("backend", "auto")).lower()
        engine = requested.with_suffix(".engine")
        if backend in {"auto", "tensorrt", "engine"} and requested.suffix.lower() != ".engine" and engine.exists():
            requested = engine
        self._weights = requested
        self._backend = "tensorrt" if requested.suffix.lower() == ".engine" else "pytorch"
        try:
            self._model = YOLO(str(self._weights))
        except Exception as exc:  # the library includes useful CUDA/model details
            raise AdapterError(self.name, f"failed to load YOLO weights {self._weights}: {exc}") from exc
        return self

    def describe(self) -> str:
        precision = "FP16" if bool(self.config.get("half", True)) else "FP32"
        return f"Ultralytics YOLO26-seg + ByteTrack | {self._backend} {precision} ({self._weights or self.config.get('weights', 'unloaded')})"

    def infer(self, frame_bgr: Any, context: dict[str, Any] | None = None) -> dict[str, Any]:
        if frame_bgr is None:
            return empty_result()
        if self._model is None:
            raise AdapterError(self.name, "adapter is not loaded", hint="Call adapter.load() after constructing it")
        require_numpy(self.name)
        context = context or {}
        kwargs: dict[str, Any] = {
            "persist": True,
            "tracker": self.config.get("tracker", "bytetrack.yaml"),
            "conf": float(self.config.get("confidence", 0.25)),
            "iou": float(self.config.get("iou", 0.7)),
            "imgsz": self.config.get("imgsz", [544, 960]),
            "device": self.config.get("device", "cuda:0"),
            "verbose": False,
        }
        if "half" in self.config:
            # Ultralytics 8.4 unified precision under ``quantize``. Using the
            # new key avoids a deprecation warning on every frame.
            kwargs["quantize"] = 16 if bool(self.config["half"]) else None
        if "max_det" in self.config:
            kwargs["max_det"] = int(self.config["max_det"])
        try:
            results = self._model.track(frame_bgr, **kwargs)
        except Exception as exc:
            raise AdapterError(self.name, f"YOLO inference failed: {exc}") from exc
        result = results[0] if isinstance(results, (list, tuple)) and results else results
        return self._parse_result(result, context)

    @staticmethod
    def _parse_result(result: Any, context: dict[str, Any] | None = None) -> dict[str, Any]:
        output = empty_result()
        if result is None:
            return output
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return output
        xyxy = getattr(boxes, "xyxy", None)
        conf = getattr(boxes, "conf", None)
        cls = getattr(boxes, "cls", None)
        ids = getattr(boxes, "id", None)
        names = getattr(result, "names", {}) or {}
        xyxy = jsonable(xyxy) or []
        conf = jsonable(conf) or []
        cls = jsonable(cls) or []
        ids = jsonable(ids)
        if not isinstance(xyxy, list):
            xyxy = [xyxy]
        if xyxy and not isinstance(xyxy[0], (list, tuple)):
            xyxy = [xyxy]
        if not isinstance(conf, list):
            conf = [conf]
        if not isinstance(cls, list):
            cls = [cls]
        if ids is not None and not isinstance(ids, list):
            ids = [ids]
        masks = getattr(result, "masks", None)
        polygons = jsonable(getattr(masks, "xy", None)) if masks is not None else None
        if polygons is not None and not isinstance(polygons, list):
            polygons = [polygons]
        for index, bbox in enumerate(xyxy):
            if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                continue
            class_id = int(cls[index]) if index < len(cls) else -1
            label = names.get(class_id, str(class_id)) if isinstance(names, dict) else str(class_id)
            item: dict[str, Any] = {
                "id": int(ids[index]) if ids is not None and index < len(ids) else index,
                "label": str(label),
                "confidence": float(conf[index]) if index < len(conf) else 0.0,
                "bbox": [float(v) for v in bbox[:4]],
            }
            if polygons is not None and index < len(polygons):
                item["polygon"] = polygons[index]
            output["objects"].append(item)
        return output

    def close(self) -> None:
        self._model = None
