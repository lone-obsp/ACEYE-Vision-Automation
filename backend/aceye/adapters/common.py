"""Shared helpers for ACEYE's optional model adapters.

The adapters deliberately keep third-party imports inside ``load``/``infer``.
The backend can therefore start and expose its UI even when a particular model
runtime is not installed.  A missing runtime or local weight is reported as an
``AdapterError`` with the exact next step instead of returning fabricated data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class AdapterError(RuntimeError):
    """Actionable error raised by a model adapter."""

    def __init__(self, adapter: str, message: str, *, hint: str | None = None) -> None:
        self.adapter = adapter
        self.hint = hint
        suffix = f" Hint: {hint}" if hint else ""
        super().__init__(f"[{adapter}] {message}{suffix}")


def resolve_local_path(root: Path, value: str | Path, *, setting: str) -> Path:
    """Resolve a model path and reject missing paths before library auto-downloads."""

    path = Path(value)
    if not path.is_absolute():
        path = root / path
    path = path.expanduser().resolve()
    if not path.exists():
        raise AdapterError(
            setting,
            f"local model path does not exist: {path}",
            hint="Place the model under the configured models directory or update the adapter path. Downloads are intentionally disabled.",
        )
    return path


def require_numpy(adapter: str):
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - exercised in deployment
        raise AdapterError(adapter, "numpy is required to infer a BGR frame", hint="Install requirements-models.txt") from exc
    return np


def empty_result() -> dict[str, Any]:
    return {"objects": [], "texts": [], "masks": [], "reasoning": {"text": "", "actions": []}}


def jsonable(value: Any) -> Any:
    """Convert tensors/arrays and nested model values into JSON-safe values."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    for method in ("tolist", "item"):
        fn = getattr(value, method, None)
        if callable(fn):
            try:
                return jsonable(fn())
            except (TypeError, ValueError, RuntimeError):
                pass
    return str(value)


def result_from_object(result: Any) -> Any:
    """Normalise Paddle/third-party result wrappers to a Python object."""

    if isinstance(result, (dict, list, tuple)):
        return result
    for attr in ("json", "to_json"):
        fn = getattr(result, attr, None)
        if callable(fn):
            value = fn()
            if isinstance(value, str):
                return json.loads(value)
            return value
    for attr in ("res", "result"):
        if hasattr(result, attr):
            return getattr(result, attr)
    return result


def extract_json(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from a model response, including fenced JSON."""

    import re

    candidates = [text]
    candidates.extend(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.I | re.S))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def parse_actions(value: Any) -> list[dict[str, Any]]:
    """Keep only bounded, well-shaped actions; execution is a separate sink."""

    if not isinstance(value, list):
        return []
    actions: list[dict[str, Any]] = []
    allowed = {"type", "x", "y", "button", "key", "text", "duration_ms", "reason"}
    for action in value:
        if not isinstance(action, dict) or not isinstance(action.get("type"), str):
            continue
        clean = {k: jsonable(v) for k, v in action.items() if k in allowed}
        if clean.get("type") in {
            "mouse_move",
            "mouse_button_down",
            "mouse_button_up",
            "key_down",
            "key_up",
            "text",
            "wait",
        }:
            actions.append(clean)
    return actions

