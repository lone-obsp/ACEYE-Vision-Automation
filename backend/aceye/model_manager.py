"""Explicit, local-only model inventory for the editable ACEYE backend.

The manager never downloads weights as a side effect.  UI callers can inspect
which paths are ready and then explicitly start a module preparation job.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class ModelManager:
    def __init__(self, root: Path, config: dict[str, Any]) -> None:
        self.root = root.resolve()
        self.config = config

    def _path(self, value: Any) -> Path | None:
        if not value:
            return None
        path = Path(str(value))
        return path.resolve() if path.is_absolute() else (self.root / path).resolve()

    def inspect(self) -> dict[str, dict[str, Any]]:
        definitions = {
            "detection": self.config.get("detection", {}),
            "ocr": self.config.get("ocr", {}),
            "sam": self.config.get("sam", {}),
            "reasoner": self.config.get("reasoner", {}),
        }
        result: dict[str, dict[str, Any]] = {}
        for name, section in definitions.items():
            enabled = bool(section.get("enabled", name != "sam"))
            value = section.get("weights") if name != "reasoner" else section.get("model_dir", section.get("model"))
            path = self._path(value)
            if not enabled:
                status = "disabled"
            elif path is None or not path.exists():
                status = "missing"
            else:
                status = "ready"
            size = 0
            if path and path.is_file():
                size = path.stat().st_size
            elif path and path.is_dir():
                size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
            result[name] = {
                "status": status,
                "path": str(path) if path else "",
                "size_bytes": size,
                "source": "local path; downloads disabled",
            }
        return result

