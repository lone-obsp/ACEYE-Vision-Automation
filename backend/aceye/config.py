from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "capture": {"fps": 30, "output": 0, "backend": "auto"},
    "detection": {
        "enabled": True,
        "weights": "models/yolo26n-seg.pt",
        "device": "cuda:0",
        "imgsz": [544, 960],
        "confidence": 0.25,
        "tracker": "bytetrack.yaml",
    },
    "ocr": {
        "enabled": True,
        "interval": 6,
        "device": "cuda:0",
        "weights": "models/ocr",
    },
    "sam": {
        "enabled": False,
        "weights": "models/sam2.1_t.pt",
        "device": "cuda:0",
        "max_objects": 3,
    },
    "reasoner": {
        "enabled": True,
        "backend": "local",
        "model": "models/Qwen3-VL-2B-Instruct",
        "model_dir": "models/Qwen3-VL-2B-Instruct",
        "endpoint": "http://127.0.0.1:1234/v1/chat/completions",
        "interval": 1.0,
        "goal": "理解当前屏幕状态，输出事件、风险和建议动作。",
        "max_new_tokens": 192,
        "image_max_side": 768,
    },
    "rhythm": {
        "enabled": False,
        "game": "muse_dash",
        "capture_fps": 120,
        "target_fps": 120,
        "roi_capture": True,
        "roi": None,
        "latency_compensation": True,
        "input_backend": "sendinput",
        "dry_run": False,
        "safety_stop_key": "f8",
        "profile": "config/profiles/muse_dash.json",
    },
    "profile": "desktop",
    "output": {"overlay": True, "print_text": False},
    "automation": {"enabled": False, "dry_run": True},
    "server": {"host": "127.0.0.1", "port": 8765},
}


def deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class ConfigStore:
    """JSON-backed configuration with atomic writes and editable relative paths."""

    def __init__(self, root: Path, path: str | Path = "config/aceye.json") -> None:
        self.root = root.resolve()
        self.path = (self.root / path).resolve() if not Path(path).is_absolute() else Path(path)
        self.data = copy.deepcopy(DEFAULT_CONFIG)

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("configuration root must be an object")
                self.data = deep_merge(DEFAULT_CONFIG, loaded)
            except (OSError, ValueError, json.JSONDecodeError):
                self.data = copy.deepcopy(DEFAULT_CONFIG)
        return copy.deepcopy(self.data)

    def update(self, partial: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
        if not isinstance(partial, dict):
            raise ValueError("configuration update must be an object")
        self.data = deep_merge(self.data, partial)
        self._normalize()
        if persist:
            self.save()
        return copy.deepcopy(self.data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="aceye-config-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(self.data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _normalize(self) -> None:
        capture = self.data["capture"]
        capture["fps"] = max(1, min(240, int(capture.get("fps", 30))))
        detection = self.data["detection"]
        detection["confidence"] = max(0.01, min(0.99, float(detection.get("confidence", 0.25))))
        detection["imgsz"] = [int(v) for v in detection.get("imgsz", [544, 960])]
        self.data["ocr"]["interval"] = max(1, int(self.data["ocr"].get("interval", 6)))
        self.data["reasoner"]["interval"] = max(0.1, float(self.data["reasoner"].get("interval", 1.0)))
        self.data["automation"]["enabled"] = bool(self.data["automation"].get("enabled", False))
        # Dry-run remains on unless the operator explicitly changes both flags.
        if not self.data["automation"]["enabled"]:
            self.data["automation"]["dry_run"] = True


def write_default_config(root: Path) -> Path:
    store = ConfigStore(root)
    if not store.path.exists():
        store.save()
    return store.path
