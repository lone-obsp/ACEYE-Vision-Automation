from __future__ import annotations

import copy
import threading
import time
from typing import Any


class RuntimeState:
    def __init__(self, config: dict[str, Any]) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {
            "phase": "stopped",
            "config": copy.deepcopy(config),
            "modules": {
                name: {"status": "idle", "message": "未启动"}
                for name in ("capture", "detection", "ocr", "sam", "reasoner", "overlay", "input")
            },
            "metrics": {
                "capture_fps": 0.0,
                "vision_fps": 0.0,
                "detection_fps": 0.0,
                "latency_ms": 0.0,
                "latest_age_ms": 0.0,
                "yolo_latency_ms": 0.0,
                "ocr_latency_ms": 0.0,
                "sam_latency_ms": 0.0,
                "reasoner_latency_ms": 0.0,
                "gpu_memory_mb": 0.0,
                "frames": 0,
                "capture_frames": 0,
                "detection_frames": 0,
                "dropped": 0,
                "dropped_capture": 0,
                "dropped_vision": 0,
                "queue_depth": 0,
            },
            "vision": {"frame_id": 0, "objects": [], "texts": [], "events": []},
            "reasoning": {"text": "", "actions": []},
            "logs": [],
            "job": {"running": False, "message": ""},
            "last_command": {"text": "", "ok": True, "message": ""},
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, **fields: Any) -> None:
        with self._lock:
            self._data.update(copy.deepcopy(fields))

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self._data["phase"] = phase

    def set_config(self, config: dict[str, Any]) -> None:
        with self._lock:
            self._data["config"] = copy.deepcopy(config)

    def set_module(self, name: str, status: str, message: str = "") -> None:
        with self._lock:
            self._data["modules"].setdefault(name, {})
            self._data["modules"][name] = {"status": status, "message": message}

    def set_metric(self, name: str, value: float | int) -> None:
        with self._lock:
            self._data["metrics"][name] = value

    def set_vision(self, value: dict[str, Any]) -> None:
        with self._lock:
            self._data["vision"] = copy.deepcopy(value)

    def merge_vision(self, fields: dict[str, Any]) -> None:
        """Merge asynchronous OCR/SAM results without replacing newer objects."""
        with self._lock:
            self._data["vision"].update(copy.deepcopy(fields))

    def set_reasoning(self, value: dict[str, Any]) -> None:
        with self._lock:
            self._data["reasoning"] = copy.deepcopy(value)

    def set_job(self, running: bool, message: str) -> None:
        with self._lock:
            self._data["job"] = {"running": running, "message": message}

    def set_command(self, text: str, ok: bool, message: str) -> None:
        with self._lock:
            self._data["last_command"] = {"text": text, "ok": ok, "message": message}

    def log(self, level: str, message: str) -> None:
        entry = {"time": time.strftime("%H:%M:%S"), "level": level, "message": message}
        with self._lock:
            self._data["logs"].append(entry)
            self._data["logs"] = self._data["logs"][-200:]
