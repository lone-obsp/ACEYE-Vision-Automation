from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from typing import Any


class OverlayError(RuntimeError):
    pass


class NativeOverlayController:
    """Thin adapter around native/ACEYECapture.dll's transparent overlay."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._library: Any = None
        self._handle: Any = None
        self._width = 0
        self._height = 0
        self._origin = (0, 0)

    def _load(self) -> None:
        paths = [
            self.root / "native" / "ACEYECapture.dll",
            self.root / "ACEYECapture.dll",
            self.root / "native" / "out" / "bin" / "Release" / "ACEYECapture.dll",
            self.root / "native" / "out" / "bin" / "Debug" / "ACEYECapture.dll",
        ]
        path = next((item for item in paths if item.exists()), None)
        if path is None:
            raise OverlayError("未找到 native/ACEYECapture.dll；Overlay 契约仍可用，但没有桌面绘制后端")
        try:
            self._library = ctypes.WinDLL(str(path))
            class OverlayConfig(ctypes.Structure):
                _fields_ = [("desktop_origin_x", ctypes.c_int32), ("desktop_origin_y", ctypes.c_int32),
                            ("width", ctypes.c_uint32), ("height", ctypes.c_uint32),
                            ("opacity", ctypes.c_uint8), ("reserved", ctypes.c_uint8 * 3)]
            class OverlayBox(ctypes.Structure):
                _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float), ("width", ctypes.c_float),
                            ("height", ctypes.c_float), ("argb", ctypes.c_uint32),
                            ("stroke_width", ctypes.c_uint32), ("label", ctypes.c_wchar * 128)]
            self._Config = OverlayConfig
            self._Box = OverlayBox
            self._library.ACEYE_OverlayStart.argtypes = [ctypes.POINTER(OverlayConfig)]
            self._library.ACEYE_OverlayStart.restype = ctypes.c_void_p
            self._library.ACEYE_OverlayUpdate.argtypes = [ctypes.c_void_p, ctypes.POINTER(OverlayBox), ctypes.c_uint32]
            self._library.ACEYE_OverlayUpdate.restype = ctypes.c_int32
            self._library.ACEYE_OverlayClear.argtypes = [ctypes.c_void_p]
            self._library.ACEYE_OverlayStop.argtypes = [ctypes.c_void_p]
        except OSError as exc:
            raise OverlayError(f"加载 Overlay DLL 失败: {exc}") from exc

    def ensure(self, width: int, height: int, origin_x: int, origin_y: int) -> None:
        if self._handle and (width, height, origin_x, origin_y) == (self._width, self._height, *self._origin):
            return
        if self._handle:
            self.close()
        if self._library is None:
            self._load()
        config = self._Config(origin_x, origin_y, width, height, 210, (0, 0, 0))
        self._handle = self._library.ACEYE_OverlayStart(ctypes.byref(config))
        if not self._handle:
            raise OverlayError("透明 Overlay 初始化失败")
        self._width, self._height, self._origin = width, height, (origin_x, origin_y)

    def update(self, context: dict[str, Any]) -> None:
        self.ensure(int(context["width"]), int(context["height"]), int(context.get("origin_x", 0)), int(context.get("origin_y", 0)))
        objects = context.get("objects", [])
        boxes = (self._Box * len(objects))()
        for index, item in enumerate(objects):
            bbox = item.get("bbox", [0, 0, 0, 0])
            x1, y1, x2, y2 = [float(value) for value in bbox]
            boxes[index].x = x1
            boxes[index].y = y1
            boxes[index].width = max(0.0, x2 - x1)
            boxes[index].height = max(0.0, y2 - y1)
            boxes[index].argb = 0xFF00FF00
            boxes[index].stroke_width = 2
            boxes[index].label = str(item.get("label", "object"))[:127]
        status = self._library.ACEYE_OverlayUpdate(self._handle, boxes, len(objects))
        if status != 0:
            raise OverlayError(f"Overlay 绘制失败: {status}")

    def close(self) -> None:
        if self._handle and self._library is not None:
            self._library.ACEYE_OverlayClear(self._handle)
            self._library.ACEYE_OverlayStop(self._handle)
        self._handle = None


class NullOverlayController:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def update(self, context: dict[str, Any]) -> None:
        return None

    def close(self) -> None:
        return None


def make_overlay(root: Path, enabled: bool) -> NativeOverlayController | NullOverlayController:
    if not enabled:
        return NullOverlayController("配置已关闭")
    try:
        controller = NativeOverlayController(root)
        controller._load()
        return controller
    except Exception as exc:  # noqa: BLE001
        return NullOverlayController(str(exc))
