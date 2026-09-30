"""Small ctypes wrapper for the editable ACEYECapture bridge.

The wrapper deliberately leaves model inference and policy decisions in Python.
It only owns the native capture buffer, optional transparent overlay and the
low-level SendInput bridge.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path


class FrameInfo(ctypes.Structure):
    _fields_ = [
        ("sequence", ctypes.c_uint64),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("desktop_origin_x", ctypes.c_int32),
        ("desktop_origin_y", ctypes.c_int32),
        ("payload_bytes", ctypes.c_uint32),
        ("status_code", ctypes.c_int32),
        ("native_code", ctypes.c_int32),
    ]


class OverlayConfig(ctypes.Structure):
    _fields_ = [
        ("desktop_origin_x", ctypes.c_int32),
        ("desktop_origin_y", ctypes.c_int32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("opacity", ctypes.c_uint8),
        ("reserved", ctypes.c_uint8 * 3),
    ]


class OverlayBox(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_float),
        ("y", ctypes.c_float),
        ("width", ctypes.c_float),
        ("height", ctypes.c_float),
        ("argb", ctypes.c_uint32),
        ("stroke_width", ctypes.c_uint32),
        ("label", ctypes.c_wchar * 128),
    ]


class InputAction(ctypes.Structure):
    _fields_ = [
        ("kind", ctypes.c_uint32),
        ("x", ctypes.c_int32),
        ("y", ctypes.c_int32),
        ("virtual_key", ctypes.c_uint16),
        ("reserved", ctypes.c_uint16),
        ("text", ctypes.c_wchar * 128),
    ]


class NativeError(RuntimeError):
    pass


def load_library(path: str | Path):
    library = ctypes.WinDLL(str(path))
    library.ACEYE_CreateCapture.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_int32)]
    library.ACEYE_CreateCapture.restype = ctypes.c_void_p
    library.ACEYE_Grab.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t, ctypes.c_uint32, ctypes.POINTER(FrameInfo)]
    library.ACEYE_Grab.restype = ctypes.c_int32
    library.ACEYE_DestroyCapture.argtypes = [ctypes.c_void_p]
    library.ACEYE_DestroyCapture.restype = None
    library.ACEYE_OverlayStart.argtypes = [ctypes.POINTER(OverlayConfig)]
    library.ACEYE_OverlayStart.restype = ctypes.c_void_p
    library.ACEYE_OverlayUpdate.argtypes = [ctypes.c_void_p, ctypes.POINTER(OverlayBox), ctypes.c_uint32]
    library.ACEYE_OverlayUpdate.restype = ctypes.c_int32
    library.ACEYE_OverlayClear.argtypes = [ctypes.c_void_p]
    library.ACEYE_OverlayClear.restype = None
    library.ACEYE_OverlayStop.argtypes = [ctypes.c_void_p]
    library.ACEYE_OverlayStop.restype = None
    library.ACEYE_SendInput.argtypes = [ctypes.POINTER(InputAction), ctypes.c_uint32]
    library.ACEYE_SendInput.restype = ctypes.c_int32
    return library


class NativeCapture:
    def __init__(self, library, output_index: int = 0, buffer_bytes: int = 16 * 1024 * 1024):
        self._library = library
        status = ctypes.c_int32()
        self._handle = library.ACEYE_CreateCapture(output_index, ctypes.byref(status))
        if not self._handle:
            raise NativeError(f"capture initialization failed: {status.value}")
        self._buffer = (ctypes.c_uint8 * buffer_bytes)()

    def close(self):
        if self._handle:
            self._library.ACEYE_DestroyCapture(self._handle)
            self._handle = None

    def grab(self, timeout_ms: int = 16):
        info = FrameInfo()
        status = self._library.ACEYE_Grab(self._handle, self._buffer, ctypes.sizeof(self._buffer), timeout_ms, ctypes.byref(info))
        if status == 1:  # normal quiet-desktop timeout
            return None
        if status == -3:
            raise NativeError(f"capture buffer too small; need {info.payload_bytes} bytes")
        if status != 0:
            raise NativeError(f"capture failed: {status}, native={info.native_code}")
        payload = bytes(self._buffer[: info.payload_bytes])
        return info, payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class NativeOverlay:
    def __init__(self, library, config: OverlayConfig):
        self._library = library
        self._handle = library.ACEYE_OverlayStart(ctypes.byref(config))
        if not self._handle:
            raise NativeError("overlay initialization failed")

    def update(self, boxes: list[OverlayBox]):
        values = (OverlayBox * len(boxes))(*boxes)
        status = self._library.ACEYE_OverlayUpdate(self._handle, values, len(boxes))
        if status != 0:
            raise NativeError(f"overlay update failed: {status}")

    def clear(self):
        self._library.ACEYE_OverlayClear(self._handle)

    def close(self):
        if self._handle:
            self._library.ACEYE_OverlayStop(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
