"""Ultralytics SAM 2.1 Tiny promptable segmentation adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import AdapterError, empty_result, jsonable, require_numpy, resolve_local_path


class Sam21Adapter:
    name = "sam"

    def __init__(self, config: dict[str, Any] | None = None, root: Path | None = None) -> None:
        self.config = dict(config or {})
        self.root = Path(root or ".").resolve()
        self._model: Any = None

    def load(self) -> "Sam21Adapter":
        try:
            from ultralytics import SAM
        except ImportError as exc:
            raise AdapterError(self.name, "ultralytics is not installed", hint="Install requirements-models.txt") from exc
        weights = resolve_local_path(self.root, self.config.get("weights", "models/sam2.1_t.pt"), setting=self.name)
        try:
            self._model = SAM(str(weights))
        except Exception as exc:
            raise AdapterError(self.name, f"failed to load SAM 2.1 weights {weights}: {exc}") from exc
        return self

    def describe(self) -> str:
        return f"Ultralytics SAM 2.1 Tiny ({self.config.get('weights', 'unloaded')})"

    def infer(self, frame_bgr: Any, context: dict[str, Any] | None = None) -> dict[str, Any]:
        if frame_bgr is None:
            return empty_result()
        if self._model is None:
            raise AdapterError(self.name, "adapter is not loaded", hint="Call adapter.load() after constructing it")
        require_numpy(self.name)
        context = context or {}
        objects = context.get("objects") or context.get("vision", {}).get("objects", [])
        bboxes: list[list[float]] = []
        ids: list[Any] = []
        for obj in objects[: int(self.config.get("max_objects", 3))]:
            if isinstance(obj, dict) and isinstance(obj.get("bbox"), (list, tuple)) and len(obj["bbox"]) >= 4:
                bboxes.append([float(v) for v in obj["bbox"][:4]])
                ids.append(obj.get("id", len(ids)))
        if not bboxes:
            return empty_result()
        try:
            results = self._model.predict(
                frame_bgr,
                bboxes=bboxes,
                device=self.config.get("device", "cuda:0"),
                verbose=False,
            )
        except Exception as exc:
            raise AdapterError(self.name, f"SAM inference failed: {exc}") from exc
        result = results[0] if isinstance(results, (list, tuple)) and results else results
        masks = getattr(result, "masks", None)
        polygons = jsonable(getattr(masks, "xy", None)) if masks is not None else []
        if polygons is None:
            polygons = []
        if not isinstance(polygons, list):
            polygons = [polygons]
        output = empty_result()
        for index, polygon in enumerate(polygons):
            output["masks"].append({"id": ids[index] if index < len(ids) else index, "polygon": polygon})
        return output

    def close(self) -> None:
        self._model = None
