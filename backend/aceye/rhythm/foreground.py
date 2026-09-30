from __future__ import annotations

"""Foreground-window guard for the Rhythm real-input path.

All Win32 calls here are unprivileged: GetForegroundWindow, OpenProcess with
PROCESS_QUERY_LIMITED_INFORMATION and QueryFullProcessImageNameW work for
same-user processes without any UAC/permission prompt.

Muse Dash common process names (matched case-insensitively by substring):
MuseDash.exe (Steam), muse_dash.exe, MuseDashUnity.exe.
"""

DEFAULT_GAME_PROCESS_NAMES: tuple[str, ...] = (
    "musedash",
    "muse_dash",
    "musedashunity",
)

_CACHE_TTL_NS = 100_000_000  # 100 ms; GetForegroundWindow is cheap but
# QueryFullProcessImageNameW opens a process handle, so cache briefly.

try:  # pragma: no cover - platform guard
    import ctypes
    import ctypes.wintypes as _wt

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = _wt.HWND
    _user32.GetWindowThreadProcessId.argtypes = [_wt.HWND, ctypes.POINTER(_wt.DWORD)]
    _user32.GetWindowThreadProcessId.restype = _wt.DWORD
    _user32.GetClientRect.argtypes = [_wt.HWND, ctypes.POINTER(_wt.RECT)]
    _user32.GetClientRect.restype = _wt.BOOL
    _user32.ClientToScreen.argtypes = [_wt.HWND, ctypes.POINTER(_wt.POINT)]
    _user32.ClientToScreen.restype = _wt.BOOL
    _kernel32.OpenProcess.argtypes = [_wt.DWORD, _wt.BOOL, _wt.DWORD]
    _kernel32.OpenProcess.restype = _wt.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [_wt.HANDLE, _wt.DWORD, _wt.LPWSTR, ctypes.POINTER(_wt.DWORD)]
    _kernel32.QueryFullProcessImageNameW.restype = _wt.BOOL
    _kernel32.CloseHandle.argtypes = [_wt.HANDLE]
    _kernel32.CloseHandle.restype = _wt.BOOL
except Exception:  # noqa: BLE001 - non-Windows or restricted environment
    _user32 = None
    _kernel32 = None

_cached_name = ""
_cached_ns = 0
_cached_hwnd = None


def _now_ns() -> int:
    import time

    return time.perf_counter_ns()


def foreground_process_name() -> str:
    """Lowercase exe name of the current foreground-window process ("" on failure)."""
    global _cached_name, _cached_ns, _cached_hwnd
    if _user32 is None or _kernel32 is None:
        return ""
    now = _now_ns()
    name = ""
    hwnd = None
    try:
        hwnd = _user32.GetForegroundWindow()
        # A window switch invalidates the cached process immediately; key
        # dispatch must never reuse the game's identity for another window.
        if hwnd == _cached_hwnd and _cached_name and now - _cached_ns < _CACHE_TTL_NS:
            return _cached_name
        if hwnd:
            pid = _wt.DWORD()
            if _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) and pid.value:
                handle = _kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
                if handle:
                    try:
                        size = _wt.DWORD(1024)
                        buf = ctypes.create_unicode_buffer(1024)
                        if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                            name = buf.value.rsplit("\\", 1)[-1].rsplit("/", 1)[-1].lower()
                    finally:
                        _kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        name = ""
    _cached_name, _cached_ns, _cached_hwnd = name, now, hwnd
    return name


def game_is_foreground(process_names: list[str] | tuple[str, ...] | None) -> bool:
    """True when the foreground process matches one of ``process_names``.

    An empty ``process_names`` disables the guard (always True).
    """
    if not process_names:
        return True
    current = foreground_process_name()
    return bool(current) and any(str(name).lower() in current for name in process_names)


def foreground_client_rect() -> tuple[int, int, int, int] | None:
    """Client-area rect of the foreground window in desktop coordinates.

    Returns (x, y, width, height) or None. The client area is the rendered
    game surface - the only coordinate frame the normalized judge centers
    are valid against.
    """
    if _user32 is None:
        return None
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return None
        rect = _wt.RECT()
        if not _user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        point = _wt.POINT(0, 0)
        if not _user32.ClientToScreen(hwnd, ctypes.byref(point)):
            return None
        return (int(point.x), int(point.y), int(rect.right), int(rect.bottom))
    except Exception:  # noqa: BLE001
        return None
