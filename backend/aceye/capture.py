from __future__ import annotations

import ctypes
import os
import threading
import time
from pathlib import Path
from typing import Any


class CaptureError(RuntimeError):
    pass


class SharedFrameHub:
    """Latest-frame fan-out owned by the single capture service.

    The capture owner publishes packets once. Consumers keep their own
    sequence cursor, so reading a packet never removes it from another
    consumer (for example the YOLO worker). Replacing an unread packet is
    intentional: real-time consumers always receive the newest frame.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._latest: dict[str, Any] | None = None
        self._sequence = 0
        self._published = 0
        self._dropped = 0

    def clear(self) -> None:
        with self._condition:
            self._latest = None
            self._condition.notify_all()

    def publish(self, packet: dict[str, Any]) -> int:
        with self._condition:
            if self._latest is not None:
                self._dropped += 1
            self._sequence += 1
            value = dict(packet)
            value["hub_sequence"] = self._sequence
            self._latest = value
            self._published += 1
            self._condition.notify_all()
            return self._sequence

    def wait_next(self, last_sequence: int = 0, timeout: float = 0.1) -> dict[str, Any] | None:
        with self._condition:
            self._condition.wait_for(
                lambda: self._latest is not None
                and int(self._latest.get("hub_sequence", 0)) != int(last_sequence),
                timeout=max(0.0, float(timeout)),
            )
            if self._latest is None:
                return None
            if int(self._latest.get("hub_sequence", 0)) == int(last_sequence):
                return None
            return dict(self._latest)

    def snapshot(self) -> dict[str, int]:
        with self._condition:
            return {
                "sequence": self._sequence,
                "published": self._published,
                "dropped": self._dropped,
                "has_frame": int(self._latest is not None),
            }


class MssCapture:
    """Portable fallback capture; DXGI remains preferred when the bridge DLL exists."""

    def __init__(self, output: int = 0, region: Any = None) -> None:
        self.output = output
        self.region = region
        self._sct: Any = None
        self._monitor: dict[str, int] | None = None
        self.sequence = 0

    def start(self) -> None:
        try:
            import mss  # type: ignore
        except ImportError as exc:
            raise CaptureError("缺少 mss；请安装 backend/requirements.txt 或构建 ACEYE 原生捕获桥") from exc
        self._sct = mss.mss()
        monitors = self._sct.monitors
        index = int(self.output) + 1
        if index >= len(monitors):
            raise CaptureError(f"屏幕输出 {self.output} 不存在，可用数量为 {len(monitors) - 1}")
        self._monitor = dict(monitors[index])
        if isinstance(self.region, (list, tuple)) and len(self.region) == 4:
            x, y, width, height = (int(value) for value in self.region)
            if width > 0 and height > 0:
                self._monitor = {
                    "left": self._monitor.get("left", 0) + x,
                    "top": self._monitor.get("top", 0) + y,
                    "width": width,
                    "height": height,
                }

    def grab(self) -> dict[str, Any] | None:
        if self._sct is None or self._monitor is None:
            raise CaptureError("capture source is not started")
        import numpy as np  # type: ignore

        image = np.asarray(self._sct.grab(self._monitor), dtype=np.uint8)
        self.sequence += 1
        return {
            "frame": image[:, :, :3].copy(),
            "sequence": self.sequence,
            "width": int(image.shape[1]),
            "height": int(image.shape[0]),
            "origin_x": int(self._monitor.get("left", 0)),
            "origin_y": int(self._monitor.get("top", 0)),
            "captured_at": time.monotonic(),
        }

    def close(self) -> None:
        if self._sct is not None:
            self._sct.close()
        self._sct = None


class NativeCapture:
    """ctypes bridge for the C++ DXGI capture DLL.

    ABI: ac_capture_create(uint32)->handle, ac_capture_grab(handle, buffer,
    capacity, timeout_ms, *width,*height,*origin_x,*origin_y,*sequence)->int,
    ac_capture_destroy(handle). The bridge is optional and loaded lazily.
    """

    def __init__(self, dll_path: Path, output: int = 0) -> None:
        self.dll_path = dll_path
        self.output = output
        self.dll: Any = None
        self.handle: Any = None
        self.buffer: Any = None
        self.capacity = 0
        self.sequence = 0

    def start(self) -> None:
        try:
            self.dll = ctypes.WinDLL(str(self.dll_path))
            self.dll.ac_capture_create.argtypes = [ctypes.c_uint32]
            self.dll.ac_capture_create.restype = ctypes.c_void_p
            self.dll.ac_capture_grab.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_ubyte),
                ctypes.c_size_t,
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_int32),
                ctypes.POINTER(ctypes.c_int32),
                ctypes.POINTER(ctypes.c_uint64),
            ]
            self.dll.ac_capture_grab.restype = ctypes.c_int
            self.dll.ac_capture_destroy.argtypes = [ctypes.c_void_p]
            self.dll.ac_capture_destroy.restype = None
            self.handle = self.dll.ac_capture_create(self.output)
            if not self.handle:
                raise CaptureError("DXGI capture bridge failed to initialize")
        except (AttributeError, OSError) as exc:
            raise CaptureError(f"无法加载 DXGI 捕获桥: {self.dll_path}: {exc}") from exc

    def grab(self) -> dict[str, Any] | None:
        if self.dll is None or not self.handle:
            raise CaptureError("native capture source is not started")
        import numpy as np  # type: ignore

        # Desktop duplication dimensions are discovered on the first call.
        width = ctypes.c_uint32()
        height = ctypes.c_uint32()
        origin_x = ctypes.c_int32()
        origin_y = ctypes.c_int32()
        sequence = ctypes.c_uint64()
        if self.capacity == 0:
            self.capacity = 3840 * 2160 * 4
            self.buffer = (ctypes.c_ubyte * self.capacity)()
        code = self.dll.ac_capture_grab(
            self.handle,
            self.buffer,
            self.capacity,
            16,
            ctypes.byref(width),
            ctypes.byref(height),
            ctypes.byref(origin_x),
            ctypes.byref(origin_y),
            ctypes.byref(sequence),
        )
        if code == 1:  # normal DXGI timeout
            return None
        if code != 0:
            raise CaptureError(f"DXGI capture bridge returned status {code}")
        required = int(width.value) * int(height.value) * 4
        if required > self.capacity:
            self.capacity = required
            self.buffer = (ctypes.c_ubyte * self.capacity)()
            return self.grab()
        # The bridge buffer is intentionally over-allocated for 4K.  Slice the
        # valid payload before reshaping so smaller desktop modes work too.
        bgra = np.ctypeslib.as_array(self.buffer)[:required].copy()
        frame = bgra.reshape((height.value, width.value, 4))[:, :, :3]
        self.sequence = int(sequence.value)
        return {
            "frame": frame,
            "sequence": self.sequence,
            "width": int(width.value),
            "height": int(height.value),
            "origin_x": int(origin_x.value),
            "origin_y": int(origin_y.value),
            "captured_at": time.monotonic(),
        }

    def close(self) -> None:
        if self.dll is not None and self.handle:
            self.dll.ac_capture_destroy(self.handle)
        self.handle = None
        self.dll = None


def make_capture(root: Path, config: dict[str, Any]) -> Any:
    capture = config.get("capture", config)
    backend = str(capture.get("backend", "auto")).lower()
    output = int(capture.get("output", 0))
    candidates = [
        # Portable package layout: ACEYECapture.dll is copied beside the
        # editable backend's `native/` directory.
        root / "native" / "ACEYECapture.dll",
        root / "ACEYECapture.dll",
        root / "native" / "out" / "bin" / "Release" / "ACEYECapture.dll",
        root / "native" / "out" / "bin" / "Debug" / "ACEYECapture.dll",
        root / "native" / "build" / "Release" / "ACEYCapture.dll",
        root / "native" / "build" / "Debug" / "ACEYCapture.dll",
        root / "runtime" / "ACEYCapture.dll",
    ]
    dll = next((path for path in candidates if path.exists()), None)
    if backend in {"auto", "native", "dxgi"} and dll is not None:
        return NativeCapture(dll, output)
    if backend == "native":
        raise CaptureError("未找到 native/ACEYCapture.dll")
    return MssCapture(output, capture.get("roi"))
