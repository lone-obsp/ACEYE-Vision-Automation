"""Editable, lazy-loaded model adapters used by the ACEYE backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import AdapterError
from .detection import YoloByteTrackAdapter
from .ocr import PaddleOcrAdapter
from .reasoner import Qwen3VlReasonerAdapter
from .sam import Sam21Adapter


def create_adapter(name: str, config: dict[str, Any] | None = None, root: Path | None = None):
    """Create an unloaded adapter; callers control when GPU memory is allocated."""

    key = name.strip().lower()
    classes = {
        "detection": YoloByteTrackAdapter,
        "detector": YoloByteTrackAdapter,
        "yolo": YoloByteTrackAdapter,
        "ocr": PaddleOcrAdapter,
        "sam": Sam21Adapter,
        "segmentation": Sam21Adapter,
        "reasoner": Qwen3VlReasonerAdapter,
        "vlm": Qwen3VlReasonerAdapter,
        "llm": Qwen3VlReasonerAdapter,
    }
    try:
        cls = classes[key]
    except KeyError as exc:
        raise AdapterError("factory", f"unknown adapter '{name}'", hint="Use detection, ocr, sam, or reasoner") from exc
    return cls(config=config, root=root)


__all__ = [
    "AdapterError",
    "PaddleOcrAdapter",
    "Qwen3VlReasonerAdapter",
    "Sam21Adapter",
    "YoloByteTrackAdapter",
    "create_adapter",
]

