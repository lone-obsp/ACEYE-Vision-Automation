from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..capture import CaptureError, SharedFrameHub
from ..input import InputError, NativeInput
from . import foreground as fg
from .calibration import load_profile
from .state import now_ns
from .controller import InputScheduler, RhythmController
from .muse_dash import MuseDashAdapter
from .osu import OsuAdapter


class LatestRoiFrameHub:
    """Rhythm consumer for the service-owned shared latest-frame hub.

    Rhythm never creates a NativeCapture/DXGI object. The general pipeline is
    the sole capture owner and publishes packets to ``SharedFrameHub``.
    """

    def __init__(self, root: Path, config: dict[str, Any], frame_hub: SharedFrameHub) -> None:
        self.root = root
        self.config = config
        self.frame_hub = frame_hub
        self.stop_event = threading.Event()
        self.wakeup = threading.Event()
        self.lock = threading.Lock()
        self.pending: dict[str, Any] | None = None
        self.thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.frames = 0
        self.dropped = 0
        self.last_error = ""
        # Capture publishes monotonic seconds; rhythm scheduling uses QPC ns.
        # Retain the capture instant rather than stamping the consumer's time.
        self._clock_offset_ns = now_ns() - int(time.monotonic() * 1_000_000_000)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.ready.clear()
        self.last_error = ""
        self.thread = threading.Thread(target=self._run, name="ACEYE-RhythmCapture", daemon=True)
        self.thread.start()
        self.ready.wait(3.0)
        if self.last_error:
            self.stop()
            raise CaptureError(self.last_error)

    def _run(self) -> None:
        self.ready.set()
        last_sequence = 0
        while not self.stop_event.is_set():
            packet = self.frame_hub.wait_next(last_sequence, timeout=0.1)
            if packet is None:
                continue
            last_sequence = int(packet.get("hub_sequence", last_sequence))
            captured_at = packet.get("captured_at")
            packet["rhythm_captured_ns"] = (
                int(float(captured_at) * 1_000_000_000) + self._clock_offset_ns
                if captured_at is not None else now_ns()
            )
            packet = self._apply_roi(packet)
            with self.lock:
                if self.pending is not None:
                    self.dropped += 1
                self.pending = packet
            self.frames += 1
            self.wakeup.set()

    def _apply_roi(self, packet: dict[str, Any]) -> dict[str, Any]:
        roi = self.config.get("roi")
        if not isinstance(roi, (list, tuple)) or len(roi) != 4:
            return packet
        x, y, width, height = (int(value) for value in roi)
        if width <= 0 or height <= 0:
            return packet
        frame = packet.get("frame")
        if frame is None or getattr(frame, "ndim", 0) < 2:
            return packet
        frame_height, frame_width = int(frame.shape[0]), int(frame.shape[1])
        x = max(0, min(x, frame_width))
        y = max(0, min(y, frame_height))
        right = max(x, min(frame_width, x + width))
        bottom = max(y, min(frame_height, y + height))
        packet = dict(packet)
        packet["frame"] = frame[y:bottom, x:right].copy()
        packet["width"] = max(0, right - x)
        packet["height"] = max(0, bottom - y)
        packet["origin_x"] = int(packet.get("origin_x", 0)) + x
        packet["origin_y"] = int(packet.get("origin_y", 0)) + y
        packet["roi"] = [x, y, right - x, bottom - y]
        return packet

    def latest(self) -> dict[str, Any] | None:
        with self.lock:
            packet = self.pending
            self.pending = None
        return packet

    def stop(self) -> None:
        self.stop_event.set()
        self.wakeup.set()
        if self.thread:
            self.thread.join(timeout=2.0)
        self.thread = None

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            depth = 1 if self.pending is not None else 0
        shared = self.frame_hub.snapshot()
        return {"frames": self.frames, "dropped_frames": self.dropped,
                "queue_depth": depth, "last_error": self.last_error,
                "backend": "shared_frame_hub", "shared_sequence": shared["sequence"]}


class RhythmRuntime:
    """Service-owned Rhythm worker; isolated from the general VisionPipeline."""

    def __init__(self, root: Path, config: dict[str, Any], frame_hub: SharedFrameHub,
                 log_callback: Callable[[str, str], None] | None = None) -> None:
        self.root = root.resolve()
        self.config = dict(config)
        self.log_callback = log_callback
        self.frame_hub = frame_hub
        self.game = str(self.config.get("game", "muse_dash")).lower()
        self.hub: LatestRoiFrameHub | None = None
        self.scheduler: InputScheduler | None = None
        self.controller: RhythmController | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.started_at = 0.0
        self.status_name = "stopped"
        self.last_error = ""
        self.frames_processed = 0
        self.detected_notes = 0
        self.confirmed_targets = 0
        self.last_action: dict[str, Any] | None = None
        self.predicted_hit_time_ns: int | None = None
        self.cv_latency_ms = 0.0
        self.timing_latency_ms = 0.0
        self.real_input_error = ""
        self._key_timers: dict[int, threading.Timer] = {}
        self._key_release_ns: dict[int, int] = {}
        self._key_generations: dict[int, int] = {}
        self._release_generation = 0
        self._active_keys: set[int] = set()
        self._input_lock = threading.RLock()
        self._keyboard_event_count = 0
        self._reset_requested = threading.Event()
        self.max_frame_age_ms = 150.0
        self.stale_frames = 0
        self._safety_thread: threading.Thread | None = None
        self._safety_stop = threading.Event()
        self._safety_triggered = False
        self.sendinput_last_status: int | None = None
        # Foreground-game guard state (input suppression + window cropping).
        fg_config = dict(self.config.get("foreground") or {})
        names = fg_config.get("process_names")
        self.fg_process_names: list[str] = ([str(n).lower() for n in names] if names
                                            else list(fg.DEFAULT_GAME_PROCESS_NAMES))
        self.pause_when_not_foreground = bool(fg_config.get("pause_when_not_foreground", True))
        self.capture_game_window = bool(fg_config.get("capture_game_window", True))
        self.game_foreground = True
        self.input_suppressed = 0
        self.frames_skipped_not_foreground = 0
        self.active_tracks = 0
        self.total_tracks = 0
        self.foreground_process = ""

    @property
    def is_running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self) -> None:
        with self.lock:
            if self.is_running:
                return
            if self.game not in {"muse_dash", "musedash", "osu", "osu!"}:
                raise ValueError(f"不支持的 Rhythm 游戏: {self.game}")
            self.stop_event.clear()
            self._reset_requested.clear()
            # Rhythm is an actual-input runtime. Simulation is reserved for
            # the offline benchmark and is not selectable from the service.
            self._native_input = NativeInput(self.root)
            self._native_input._load()
            self._log("info", "Rhythm: SendInput 已加载；F8 停止")
            self.scheduler = InputScheduler(sink=self._send_key, dry_run=False, logger=self._log,
                                            hold_extender=self._extend_hold)
            adapter_config = dict(self.config)
            # The service only merges a small whitelist of profile keys into
            # the rhythm config; merge the full profile here so the detector/
            # track/timing/foreground tuning in muse_dash.json actually
            # reaches the runtime and adapter. Explicit rhythm config values
            # still win.
            profile_path = adapter_config.get("profile")
            if self.game in {"muse_dash", "musedash"} and (not profile_path or "osu" in str(profile_path).lower()):
                profile_path = "config/profiles/muse_dash.json"
            if profile_path:
                profile_file = (self.root / str(profile_path)).resolve()
                if profile_file.exists():
                    try:
                        profile_data = json.loads(profile_file.read_text(encoding="utf-8"))
                        if isinstance(profile_data, dict):
                            for key, value in profile_data.items():
                                if key not in adapter_config or adapter_config.get(key) is None:
                                    adapter_config[key] = value
                    except Exception as exc:  # noqa: BLE001
                        self._log("warn", f"Rhythm profile 合并失败: {exc}")
            # Keep the merged result on self.config so runtime-level config
            # (foreground guard, capture_game_window) is live as well.
            self.config = dict(adapter_config)
            fg_config = dict(self.config.get("foreground") or {})
            names = fg_config.get("process_names")
            if names:
                self.fg_process_names = [str(n).lower() for n in names]
            self.pause_when_not_foreground = bool(fg_config.get("pause_when_not_foreground", True))
            self.capture_game_window = bool(self.config.get("capture_game_window", fg_config.get("capture_game_window", True)))
            timing_config = dict(self.config.get("timing") or {})
            self.max_frame_age_ms = max(1.0, float(timing_config.get("max_frame_age_ms", 150.0)))
            self.hub = LatestRoiFrameHub(self.root, dict(self.config), self.frame_hub)
            # LatestRoiFrameHub already crops a configured ROI. Do not crop
            # the same coordinates a second time inside the adapter.
            roi = adapter_config.get("roi")
            if isinstance(roi, (list, tuple)) and len(roi) == 4 and int(roi[2]) > 0 and int(roi[3]) > 0:
                adapter_config["roi"] = None
            calibration = None
            calibration_path = self.config.get("calibration_profile")
            if calibration_path and self.config.get("latency_compensation", True):
                calibration = load_profile((self.root / str(calibration_path)).resolve())
            adapter = (MuseDashAdapter(adapter_config, calibration=calibration)
                       if self.game in {"muse_dash", "musedash"}
                       else OsuAdapter(adapter_config, calibration=calibration))
            self.controller = RhythmController(adapter, self.scheduler, dry_run=False,
                                               max_frame_age_ms=self.max_frame_age_ms)
            # Start from a clean slate: no tracks, no scheduled actions.
            self.controller.reset()
            self.started_at = time.monotonic()
            self.status_name = "starting"
            self.last_error = ""
            self.hub.start()
            self._start_safety_monitor()
            self.thread = threading.Thread(target=self._run, name="ACEYE-Rhythm", daemon=True)
            self.thread.start()

    def _game_foreground(self) -> bool:
        """Foreground guard with a 100 ms cache; True when disabled."""
        self.foreground_process = fg.foreground_process_name()
        ok = fg.game_is_foreground(self.fg_process_names)
        if ok != self.game_foreground:
            self.game_foreground = ok
            self._log("info", f"Rhythm: 游戏前台状态 -> {ok} (前台进程: {self.foreground_process or '未知'})")
            if not ok:
                # The input thread may observe this first. Reset tracking on
                # the vision thread to avoid resetting during process().
                self._reset_requested.set()
                if self.scheduler:
                    self.scheduler.cancel_pending()
                self._release_active_keys()
        return ok

    def _crop_to_game_window(self, packet: dict[str, Any]) -> dict[str, Any]:
        """Crop the captured frame to the foreground game's client area.

        The capture packet covers the whole DXGI output (origin_x/origin_y in
        desktop coordinates). The judge centers in the profile are normalized
        against the *game picture*, so the frame must be cropped to the game
        client area before detection. Fullscreen/borderless games reduce to
        an identity crop.
        """
        if not self.capture_game_window or not self.game_foreground:
            return packet
        rect = fg.foreground_client_rect()
        frame = packet.get("frame")
        if rect is None or frame is None or getattr(frame, "ndim", 0) < 2:
            return packet
        frame_h, frame_w = int(frame.shape[0]), int(frame.shape[1])
        gx, gy, gw, gh = rect
        origin_x = int(packet.get("origin_x", 0) or 0)
        origin_y = int(packet.get("origin_y", 0) or 0)
        x0 = max(0, min(gx - origin_x, frame_w))
        y0 = max(0, min(gy - origin_y, frame_h))
        x1 = max(x0, min(gx - origin_x + gw, frame_w))
        y1 = max(y0, min(gy - origin_y + gh, frame_h))
        if x1 - x0 < 32 or y1 - y0 < 32:
            return packet
        cropped = dict(packet)
        cropped["frame"] = frame[y0:y1, x0:x1].copy()
        cropped["width"] = x1 - x0
        cropped["height"] = y1 - y0
        cropped["origin_x"] = origin_x + x0
        cropped["origin_y"] = origin_y + y0
        return cropped

    def _run(self) -> None:
        self.status_name = "running"
        while not self.stop_event.is_set():
            if self._reset_requested.is_set():
                self._reset_requested.clear()
                if self.controller:
                    self.controller.reset()
                self.last_action = None
                self.predicted_hit_time_ns = None
            packet = self.hub.latest() if self.hub else None
            if packet is None:
                if self.hub:
                    self.hub.wakeup.wait(0.01)
                    self.hub.wakeup.clear()
                continue
            try:
                if self.pause_when_not_foreground and not self._game_foreground():
                    # Game is not in the foreground: do not feed the detector
                    # (keeps browser/console frames out of the tracks) and do
                    # not let any input through (guard in _send_key).
                    self.frames_skipped_not_foreground += 1
                    self.detected_notes = 0
                    self.active_tracks = 0
                    self.total_tracks = 0
                    continue
                captured_ns = int(packet.get("rhythm_captured_ns", now_ns()))
                if (now_ns() - captured_ns) / 1_000_000.0 > self.max_frame_age_ms:
                    self.stale_frames += 1
                    continue
                packet = self._crop_to_game_window(packet)
                if self.stop_event.is_set():
                    break
                result = self.controller.process(packet["frame"], captured_ns)
                if result.get("dropped_frame_age"):
                    self.stale_frames += 1
                self.frames_processed += 1
                self.detected_notes = len(result.get("notes", []))
                self.confirmed_targets = int(result.get("confirmed_targets", 0))
                self.active_tracks = int(result.get("active_tracks", 0))
                self.total_tracks = int(result.get("total_tracks", 0))
                self.cv_latency_ms = float(result.get("cv_latency_ms", 0.0))
                self.timing_latency_ms = float(result.get("timing_latency_ms", 0.0))
                actions = result.get("actions", [])
                actions_by_track = {action.get("track_id"): action for action in actions}
                for note in result.get("notes", []):
                    action = actions_by_track.get(note.track_id, {})
                    target_ns = action.get("target_ns", getattr(note, "target_ns", None))
                    delay = f"{(int(target_ns) - now_ns()) / 1_000_000.0:.2f}" if target_ns else "none"
                    self._log(
                        "debug",
                        f"note: x={float(note.x):.1f} y={float(note.y):.1f} "
                        f"lane={action.get('key', note.lane)} "
                        f"estimated_hit_time={target_ns} "
                        f"in={delay} ms track_id={note.track_id}",
                    )
                self.last_action = dict(actions[-1]) if actions else self.last_action
                self.predicted_hit_time_ns = int(actions[-1]["target_ns"]) if actions else self.predicted_hit_time_ns
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                self.status_name = "error"
                time.sleep(0.05)
        if self.controller:
            self.controller.reset()

    def stop(self) -> None:
        with self.lock:
            self.stop_event.set()
            self._safety_stop.set()
            # Drop every track and pending action before tearing anything
            # down: no stale action may survive a start/stop cycle.
            if self.scheduler:
                self.scheduler.cancel_pending()
            if self.hub:
                self.hub.stop()
            if self.thread:
                self.thread.join(timeout=2.0)
            if self.scheduler:
                self.scheduler.close()
            if self.controller:
                self.controller.reset()
            self._release_active_keys()
            if self._safety_thread:
                self._safety_thread.join(timeout=0.5)
            self._safety_thread = None
            self.thread = None
            self.status_name = "safety-stopped" if self._safety_triggered else ("stopped" if not self.last_error else self.status_name)

    def _send_key(self, key: str, duration_ms: float) -> bool:
        """Send a bounded key press through the existing native SendInput bridge."""
        if self.fg_process_names and not self._game_foreground():
            # Never send game keys to whatever window is in the foreground
            # (historically: arrow keys reached the browser and flipped the
            # ACEYE mode select). Suppressed, not raised: the scheduler must
            # not treat this as an input failure.
            self.input_suppressed += 1
            self._log("info", f"input_suppressed: key={key}（游戏不在前台，前台={self.foreground_process or '未知'}）")
            return False
        vk = _virtual_key(key)
        if vk is None:
            raise InputError(f"不支持的 Muse Dash 按键: {key}")
        native = getattr(self, "_native_input", None)
        if native is None:
            native = NativeInput(self.root)
            native._load()
            self._native_input = native
        with self._input_lock:
            if self.stop_event.is_set() or self._safety_triggered:
                return False
            sent = vk not in self._active_keys
            if sent:
                try:
                    native.submit([{"type": "key_down", "key": vk}], dry_run=False)
                    self.sendinput_last_status = native.last_status
                    self._active_keys.add(vk)
                    self._keyboard_event_count += 1
                    self._log("info", f"real_input: key={key} timestamp={now_ns()} status={native.last_status}")
                except Exception as exc:  # noqa: BLE001
                    self.sendinput_last_status = native.last_status
                    self._log("error", f"real_input: key={key} timestamp={now_ns()} status={native.last_status} error={exc}")
                    raise
            release_ns = now_ns() + int(max(5.0, float(duration_ms)) * 1_000_000.0)
            self._arm_release(vk, max(release_ns, self._key_release_ns.get(vk, 0)))
            return sent

    def _arm_release(self, vk: int, release_ns: int) -> None:
        """Called under _input_lock; only the current timer owns key release."""
        previous = self._key_timers.pop(vk, None)
        if previous:
            previous.cancel()
        self._release_generation += 1
        generation = self._release_generation
        self._key_generations[vk] = generation
        self._key_release_ns[vk] = int(release_ns)
        timer = threading.Timer(max(0.001, (release_ns - now_ns()) / 1_000_000_000.0),
                                self._release_key, args=(vk, generation))
        timer.daemon = True
        self._key_timers[vk] = timer
        timer.start()

    def _extend_hold(self, key: str, end_ns: int) -> bool:
        if self.fg_process_names and not self._game_foreground():
            return False
        vk = _virtual_key(key)
        with self._input_lock:
            if vk is None or vk not in self._active_keys or self.stop_event.is_set():
                return False
            if int(end_ns) > self._key_release_ns.get(vk, 0):
                self._arm_release(vk, int(end_ns))
            return True

    def _release_key(self, vk: int, generation: int) -> None:
        with self._input_lock:
            if self._key_generations.get(vk) != generation or vk not in self._active_keys:
                return
            deadline = self._key_release_ns.get(vk, 0)
            if now_ns() < deadline:
                self._arm_release(vk, deadline)
                return
            native = getattr(self, "_native_input", None)
            try:
                if native is not None:
                    native.submit([{"type": "key_up", "key": vk}], dry_run=False)
                    self.sendinput_last_status = native.last_status
                    self._keyboard_event_count += 1
                    self._active_keys.discard(vk)
            except Exception as exc:  # noqa: BLE001
                self.sendinput_last_status = native.last_status
                self.real_input_error = str(exc)
                self._log("error", f"key_up_error: vk={vk} error={exc}")
            finally:
                self._key_timers.pop(vk, None)
                self._key_generations.pop(vk, None)
                self._key_release_ns.pop(vk, None)

    def _release_active_keys(self) -> None:
        native = getattr(self, "_native_input", None)
        if native is None:
            return
        with self._input_lock:
            for timer in self._key_timers.values():
                timer.cancel()
            self._key_timers.clear()
            self._key_generations.clear()
            self._key_release_ns.clear()
            for vk in list(self._active_keys):
                try:
                    native.submit([{"type": "key_up", "key": vk}], dry_run=False)
                    self.sendinput_last_status = native.last_status
                    self._keyboard_event_count += 1
                    self._active_keys.discard(vk)
                except Exception as exc:  # noqa: BLE001
                    self.sendinput_last_status = native.last_status
                    self.real_input_error = str(exc)

    def _start_safety_monitor(self) -> None:
        self._safety_stop.clear()
        self._safety_triggered = False
        self._safety_thread = threading.Thread(target=self._safety_loop, name="ACEYE-RhythmSafety", daemon=True)
        self._safety_thread.start()

    def _log(self, level: str, message: str) -> None:
        if self.log_callback:
            try:
                self.log_callback(level, message)
            except Exception:
                pass

    def _safety_loop(self) -> None:
        key = _virtual_key(self.config.get("safety_stop_key", "f8"))
        if key is None:
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
        except Exception:
            return
        while not self._safety_stop.is_set() and not self.stop_event.is_set():
            if user32.GetAsyncKeyState(key) & 0x8000:
                self._safety_triggered = True
                self.status_name = "safety-stopped"
                self.stop_event.set()
                if self.scheduler:
                    self.scheduler.cancel_pending()
                if self.hub:
                    self.hub.stop()
                self._release_active_keys()
                return
            time.sleep(0.01)

    def status(self) -> dict[str, Any]:
        with self.lock:
            hub = self.hub.snapshot() if self.hub else {"frames": 0, "dropped_frames": 0, "queue_depth": 0, "last_error": ""}
            scheduler = self.scheduler.metrics() if self.scheduler else {"scheduled": 0, "jitter_ms_mean": 0.0, "jitter_ms_p95": 0.0, "queue_depth": 0, "last_error": ""}
            elapsed = max(0.001, time.monotonic() - self.started_at) if self.started_at else 0.001
            current_ns = now_ns()
            predicted_ns = self.predicted_hit_time_ns
            if predicted_ns is not None and predicted_ns < current_ns:
                predicted_ns = None
            return {
                "status": self.status_name,
                "running": self.is_running,
                "game": self.game,
                "dry_run": False,
                "capture_fps": round(float(hub.get("frames", 0)) / elapsed, 2),
                "frames": self.frames_processed,
                "dropped_frames": hub.get("dropped_frames", 0) + self.stale_frames,
                "stale_frames": self.stale_frames,
                "queue_depth": hub.get("queue_depth", 0),
                "detected_notes": self.detected_notes,
                "confirmed_targets": self.confirmed_targets,
                "active_tracks": self.active_tracks,
                "total_tracks": self.total_tracks,
                "game_foreground": self.game_foreground,
                "foreground_process": self.foreground_process,
                "input_suppressed": self.input_suppressed,
                "frames_skipped_not_foreground": self.frames_skipped_not_foreground,
                "predicted_hit_time_ns": predicted_ns,
                "predicted_hit_time_ms": round((predicted_ns - current_ns) / 1_000_000.0, 2) if predicted_ns else None,
                "last_action": self.last_action,
                "cv_latency_ms": round(self.cv_latency_ms, 3),
                "timing_latency_ms": round(self.timing_latency_ms, 3),
                "input_scheduling_jitter_ms": scheduler.get("jitter_ms_mean", 0.0),
                "input_scheduling_jitter_p95_ms": scheduler.get("jitter_ms_p95", 0.0),
                "scheduled_actions": scheduler.get("scheduled", 0),
                "executed_actions": scheduler.get("executed_actions", 0),
                "real_input_count": scheduler.get("real_input_count", 0),
                "keyboard_event_count": self._keyboard_event_count,
                "sendinput_last_status": self.sendinput_last_status,
                "capture_backend": hub.get("backend", "shared_frame_hub"),
                "safety_stop_key": str(self.config.get("safety_stop_key", "f8")),
                "safety_triggered": self._safety_triggered,
                "error": self.last_error or scheduler.get("last_error", "") or self.real_input_error or hub.get("last_error", ""),
            }


def _virtual_key(key: Any) -> int | None:
    value = str(key).strip().lower()
    mapping = {
        "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
        "space": 0x20, "enter": 0x0D, "escape": 0x1B, "esc": 0x1B,
        "f8": 0x77, "z": 0x5A, "x": 0x58, "a": 0x41, "s": 0x53,
        "f": 0x46, "j": 0x4A,
    }
    if value.startswith("0x"):
        try:
            return int(value, 16)
        except ValueError:
            return None
    if value.isdigit():
        return int(value)
    return mapping.get(value)
