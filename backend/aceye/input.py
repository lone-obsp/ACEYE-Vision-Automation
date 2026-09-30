from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Any


class InputError(RuntimeError):
    pass


class NativeInput:
    """Validated Windows SendInput bridge used by the Rhythm scheduler."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.library: Any = None
        self.Action: Any = None
        self.last_status: int | None = None
        self.last_result = ""

    def _load(self) -> None:
        candidates = (
            self.root / "native" / "ACEYECapture.dll",
            self.root / "ACEYECapture.dll",
            self.root / "native" / "out" / "bin" / "Release" / "ACEYECapture.dll",
            self.root / "native" / "out" / "bin" / "Debug" / "ACEYECapture.dll",
            self.root / "native" / "build" / "Release" / "ACEYCapture.dll",
            self.root / "native" / "build" / "Debug" / "ACEYCapture.dll",
        )
        path = next((candidate for candidate in candidates if candidate.exists()), None)
        if path is None:
            raise InputError("未找到原生输入桥")
        self.library = ctypes.WinDLL(str(path))
        class Action(ctypes.Structure):
            _fields_ = [("kind", ctypes.c_uint32), ("x", ctypes.c_int32), ("y", ctypes.c_int32),
                        ("virtual_key", ctypes.c_uint16), ("reserved", ctypes.c_uint16),
                        ("text", ctypes.c_wchar * 128)]
        self.Action = Action
        self.library.ACEYE_SendInput.argtypes = [ctypes.POINTER(Action), ctypes.c_uint32]
        self.library.ACEYE_SendInput.restype = ctypes.c_int32

    def submit(self, actions: list[dict[str, Any]], *, dry_run: bool) -> str:
        if dry_run:
            self.last_status = None
            self.last_result = f"dry-run: {len(actions)} actions recorded"
            return self.last_result
        if len(actions) > 16:
            raise InputError("单次输入动作超过 16 个，已拒绝")
        if self.library is None:
            self._load()
        values = (self.Action * len(actions))()
        kinds = {"mouse_move": 1, "mouse_button_down": 2, "mouse_button_up": 3,
                 "key_down": 6, "key_up": 7, "text": 8, "wait": 0}
        for idx, action in enumerate(actions):
            kind = action.get("type")
            if kind not in kinds or kind == "wait":
                raise InputError(f"不允许的输入动作: {kind}")
            values[idx].kind = kinds[kind]
            values[idx].x = max(-32768, min(32767, int(action.get("x", 0))))
            values[idx].y = max(-32768, min(32767, int(action.get("y", 0))))
            values[idx].virtual_key = max(0, min(0xFF, int(action.get("key", 0))))
            values[idx].text = str(action.get("text", ""))[:127]
        status = self.library.ACEYE_SendInput(values, len(actions))
        self.last_status = int(status)
        if status != 0:
            raise InputError(f"SendInput 失败: {status}")
        self.last_result = f"executed: {len(actions)} actions; status={status}"
        return self.last_result
