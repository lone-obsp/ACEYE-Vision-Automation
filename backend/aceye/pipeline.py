from __future__ import annotations

import copy
import threading
import time
from pathlib import Path
from typing import Any

from .capture import CaptureError, SharedFrameHub, make_capture
from .input import InputError, NativeInput
from .overlay import NullOverlayController, make_overlay
from .state import RuntimeState


class VisionPipeline:
    """Editable Python orchestration layer around the model adapters.

    Adapters are intentionally loaded on this worker, so optional CUDA/model
    imports never prevent the command UI from starting.
    """

    def __init__(self, root: Path, config: dict[str, Any], state: RuntimeState,
                 frame_hub: SharedFrameHub | None = None) -> None:
        self.root = root
        self.config = config
        self.state = state
        self.frame_hub = frame_hub
        self.stop_event = threading.Event()
        self.reason_event = threading.Event()
        self.sam_event = threading.Event()
        self.reason_wakeup = threading.Event()
        self.reason_lock = threading.Lock()
        self.reason_pending: tuple[Any, dict[str, Any]] | None = None
        self.frame_wakeup = threading.Event()
        self.frame_lock = threading.Lock()
        self.frame_pending: dict[str, Any] | None = None
        self.ocr_wakeup = threading.Event()
        self.ocr_lock = threading.Lock()
        self.ocr_pending: tuple[Any, dict[str, Any]] | None = None
        self.sam_wakeup = threading.Event()
        self.sam_lock = threading.Lock()
        self.sam_pending: tuple[Any, dict[str, Any]] | None = None
        self.thread: threading.Thread | None = None
        self.vision_thread: threading.Thread | None = None
        self.ocr_thread: threading.Thread | None = None
        self.sam_thread: threading.Thread | None = None
        self.reason_thread: threading.Thread | None = None
        self.capture: Any = None
        self.overlay: Any = None
        self.input: NativeInput | None = None
        self.adapters: dict[str, Any] = {}
        self.adapter_errors: dict[str, str] = {}
        self.adapter_lock = threading.RLock()

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.reason_wakeup.clear()
        self.frame_wakeup.clear()
        self.ocr_wakeup.clear()
        self.sam_wakeup.clear()
        self.reason_thread = threading.Thread(target=self._reason_worker, name="ACEYE-Reasoner", daemon=True)
        self.reason_thread.start()
        self.thread = threading.Thread(target=self._run, name="ACEYE-Pipeline", daemon=True)
        self.thread.start()

    def stop(self, timeout: float = 8.0) -> None:
        self.stop_event.set()
        self.reason_wakeup.set()
        self.frame_wakeup.set()
        self.ocr_wakeup.set()
        self.sam_wakeup.set()
        if self.thread:
            self.thread.join(timeout=timeout)
        for worker in (self.vision_thread, self.ocr_thread, self.sam_thread):
            if worker:
                worker.join(timeout=timeout)
        self.vision_thread = None
        self.ocr_thread = None
        self.sam_thread = None
        if self.reason_thread:
            self.reason_thread.join(timeout=timeout)
        self.reason_thread = None
        self.thread = None
        self._close_adapters()
        if self.overlay is not None:
            try:
                self.overlay.close()
            except Exception:
                pass
            self.overlay = None
        self.input = None
        if self.capture is not None:
            try:
                self.capture.close()
            except Exception:
                pass
            self.capture = None

    def request_reason(self) -> None:
        self.reason_event.set()

    def request_sam(self) -> None:
        self.sam_event.set()

    def prepare(self, name: str) -> tuple[bool, str]:
        try:
            self.adapter_errors.pop(name, None)
            adapter = self._load_adapter(name)
            if adapter is None:
                return False, self.adapter_errors.get(name, f"未知模块 {name}")
            return True, f"{name} adapter ready"
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            return False, str(exc)

    def _run(self) -> None:
        self.state.set_phase("starting")
        self.state.set_job(True, "正在初始化捕获和模型模块")
        try:
            self.capture = make_capture(self.root, self.config)
            self.capture.start()
            self.state.set_module("capture", "ready", type(self.capture).__name__)
        except Exception as exc:  # noqa: BLE001
            self.state.set_module("capture", "error", str(exc))
            self.state.set_phase("error")
            self.state.set_job(False, str(exc))
            self.state.log("error", f"捕获初始化失败: {exc}")
            return

        for name in ("detection", "ocr", "sam", "reasoner"):
            section = self.config.get(name, {})
            if name == "sam" and not section.get("enabled", False):
                self.state.set_module(name, "disabled", "按需启用")
                continue
            if name != "sam" and not section.get("enabled", True):
                self.state.set_module(name, "disabled", "配置已关闭")
                continue
            self.state.set_module(name, "deferred", "首次需要时在后台加载")
        self.overlay = make_overlay(self.root, bool(self.config.get("output", {}).get("overlay", True)))
        if isinstance(self.overlay, NullOverlayController):
            self.state.set_module("overlay", "degraded", self.overlay.reason)
        else:
            self.state.set_module("overlay", "ready", "透明、置顶、点击穿透 Overlay")
        automation = self.config.get("automation", {})
        if automation.get("enabled", False) and not automation.get("dry_run", True):
            try:
                self.input = NativeInput(self.root)
                self.state.set_module("input", "armed", "SendInput 已接入；动作仍受适配器校验")
            except Exception as exc:  # noqa: BLE001
                self.state.set_module("input", "error", str(exc))
        else:
            self.state.set_module("input", "dry-run", "默认不注入鼠标键盘")
        self.state.set_phase("running")
        self.state.set_job(False, "流水线运行中")
        self.state.log("info", "ACEYE 流水线已启动")
        self.vision_thread = threading.Thread(target=self._vision_worker, name="ACEYE-Vision", daemon=True)
        self.ocr_thread = threading.Thread(target=self._ocr_worker, name="ACEYE-OCR", daemon=True)
        self.sam_thread = threading.Thread(target=self._sam_worker, name="ACEYE-SAM", daemon=True)
        self.vision_thread.start()
        self.ocr_thread.start()
        self.sam_thread.start()

        capture_count = 0
        started = time.monotonic()
        target_period = 1.0 / max(1, int(self.config.get("capture", {}).get("fps", 30)))
        while not self.stop_event.is_set():
            loop_started = time.monotonic()
            try:
                packet = self.capture.grab()
            except CaptureError as exc:
                self.state.set_module("capture", "error", str(exc))
                snapshot = self.state.snapshot()
                dropped = int(snapshot["metrics"].get("dropped", 0)) + 1
                self.state.set_metric("dropped", dropped)
                self.state.set_metric("dropped_capture", dropped)
                time.sleep(0.1)
                continue
            except Exception as exc:  # noqa: BLE001
                self.state.set_module("capture", "error", f"捕获异常: {exc}")
                time.sleep(0.1)
                continue
            if packet is None:
                time.sleep(0.001)
                continue
            capture_count += 1
            if self.frame_hub is not None:
                self.frame_hub.publish(packet)
            self._submit_frame(packet)
            now = time.monotonic()
            elapsed = max(0.001, now - started)
            self.state.set_metric("frames", capture_count)
            self.state.set_metric("capture_frames", capture_count)
            self.state.set_metric("capture_fps", round(capture_count / elapsed, 2))
            with self.frame_lock:
                queue_depth = 1 if self.frame_pending is not None else 0
            self.state.set_metric("queue_depth", queue_depth)
            time.sleep(max(0.0, target_period - (time.monotonic() - loop_started)))

        self.state.set_phase("stopping")
        self.state.set_job(False, "正在关闭模块")
        if self.overlay is not None:
            try:
                self.overlay.close()
            except Exception:
                pass
            self.overlay = None
        self.input = None
        try:
            self.capture.close()
        except Exception:
            pass
        self.capture = None
        self.state.set_phase("stopped")
        self.state.set_job(False, "已停止")
        self.state.log("info", "ACEYE 流水线已停止")

    def _submit_frame(self, packet: dict[str, Any]) -> None:
        with self.frame_lock:
            if self.frame_pending is not None:
                current = int(self.state.snapshot()["metrics"].get("dropped_vision", 0)) + 1
                self.state.set_metric("dropped_vision", current)
            self.frame_pending = packet
        self.frame_wakeup.set()

    def _take_frame(self) -> dict[str, Any] | None:
        with self.frame_lock:
            packet = self.frame_pending
            self.frame_pending = None
        return packet

    def _vision_worker(self) -> None:
        processed = 0
        started = time.monotonic()
        last_reason = 0.0
        while not self.stop_event.is_set():
            self.frame_wakeup.wait(0.25)
            self.frame_wakeup.clear()
            while not self.stop_event.is_set():
                packet = self._take_frame()
                if packet is None:
                    break
                processed += 1
                frame = packet["frame"]
                context: dict[str, Any] = {
                    "frame_id": int(packet["sequence"]),
                    "width": int(packet["width"]),
                    "height": int(packet["height"]),
                    "origin_x": int(packet.get("origin_x", 0)),
                    "origin_y": int(packet.get("origin_y", 0)),
                    "objects": [], "texts": [], "masks": [], "events": [],
                }
                detection_started = time.monotonic()
                self._infer_detection(frame, context)
                detection_ms = (time.monotonic() - detection_started) * 1000.0
                self.state.set_metric("yolo_latency_ms", round(detection_ms, 2))
                if processed == 1 or processed % 30 == 0:
                    self._update_gpu_memory()
                if self.config.get("ocr", {}).get("enabled", True) and processed % max(1, int(self.config.get("ocr", {}).get("interval", 6))) == 0:
                    self._submit_ocr(frame, context)
                if self.sam_event.is_set():
                    self.sam_event.clear()
                    self._submit_sam(frame, context)
                if self.overlay is not None:
                    try:
                        self.overlay.update(context)
                    except Exception as exc:  # noqa: BLE001
                        self.state.set_module("overlay", "error", str(exc))
                now = time.monotonic()
                if self.config.get("reasoner", {}).get("enabled", True):
                    reason_interval = float(self.config.get("reasoner", {}).get("interval", 1.0))
                    if self.reason_event.is_set() or now - last_reason >= reason_interval:
                        self.reason_event.clear()
                        last_reason = now
                        self._submit_reasoner(frame, context)
                self.state.set_vision(context)
                elapsed = max(0.001, now - started)
                self.state.set_metric("detection_frames", processed)
                self.state.set_metric("vision_fps", round(processed / elapsed, 2))
                self.state.set_metric("detection_fps", round(processed / elapsed, 2))
                age_ms = max(0.0, (now - float(packet.get("captured_at", now))) * 1000.0)
                self.state.set_metric("latency_ms", round(age_ms, 2))
                self.state.set_metric("latest_age_ms", round(age_ms, 2))

    def _submit_ocr(self, frame: Any, context: dict[str, Any]) -> None:
        with self.ocr_lock:
            self.ocr_pending = (frame.copy() if hasattr(frame, "copy") else frame, copy.deepcopy(context))
        self.ocr_wakeup.set()

    def _update_gpu_memory(self) -> None:
        try:
            import torch
            if torch.cuda.is_available():
                self.state.set_metric("gpu_memory_mb", round(float(torch.cuda.memory_allocated()) / 1024.0 / 1024.0, 1))
        except Exception:
            return

    def _ocr_worker(self) -> None:
        while not self.stop_event.is_set():
            self.ocr_wakeup.wait(0.25)
            self.ocr_wakeup.clear()
            with self.ocr_lock:
                pending = self.ocr_pending
                self.ocr_pending = None
            if pending is None:
                continue
            frame, context = pending
            started = time.monotonic()
            self._infer_ocr(frame, context)
            self.state.set_metric("ocr_latency_ms", round((time.monotonic() - started) * 1000.0, 2))
            self.state.merge_vision({"texts": context.get("texts", []), "ocr_frame_id": context.get("frame_id", 0)})

    def _submit_sam(self, frame: Any, context: dict[str, Any]) -> None:
        with self.sam_lock:
            self.sam_pending = (frame.copy() if hasattr(frame, "copy") else frame, copy.deepcopy(context))
        self.sam_wakeup.set()

    def _sam_worker(self) -> None:
        while not self.stop_event.is_set():
            self.sam_wakeup.wait(0.25)
            self.sam_wakeup.clear()
            with self.sam_lock:
                pending = self.sam_pending
                self.sam_pending = None
            if pending is None:
                continue
            frame, context = pending
            started = time.monotonic()
            self._infer_sam(frame, context)
            self.state.set_metric("sam_latency_ms", round((time.monotonic() - started) * 1000.0, 2))
            self.state.merge_vision({"masks": context.get("masks", []), "sam_frame_id": context.get("frame_id", 0)})

    def _load_adapter(self, name: str) -> Any | None:
        with self.adapter_lock:
            if name in self.adapters:
                return self.adapters[name]
            if name in self.adapter_errors:
                return None
            try:
                from .adapters import create_adapter

                self.state.set_module(name, "loading", "正在加载本地模型")
                adapter = create_adapter(name, self.config.get(name, {}), self.root)
                adapter.load()
                self.adapters[name] = adapter
                self.state.set_module(name, "ready", adapter.describe())
                self.adapter_errors.pop(name, None)
                self.state.log("info", f"{name} 模型已加载")
                return adapter
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                self.adapter_errors[name] = message
                status = "missing-model" if "local model path does not exist" in message else "error"
                self.state.set_module(name, status, message)
                self.state.log("warn", f"{name} 未加载: {message}")
                return None

    def _infer_detection(self, frame: Any, context: dict[str, Any]) -> None:
        adapter = self.adapters.get("detection") or self._load_adapter("detection")
        if not adapter:
            return
        try:
            result = adapter.infer(frame, context)
            context["objects"] = result.get("objects", [])
            context["masks"] = result.get("masks", [])
            context["events"].extend(result.get("events", []))
        except Exception as exc:  # noqa: BLE001
            self.state.set_module("detection", "error", str(exc))

    def _infer_ocr(self, frame: Any, context: dict[str, Any]) -> None:
        adapter = self.adapters.get("ocr") or self._load_adapter("ocr")
        if not adapter:
            return
        try:
            result = adapter.infer(frame, context)
            context["texts"] = result.get("texts", [])
        except Exception as exc:  # noqa: BLE001
            self.state.set_module("ocr", "error", str(exc))

    def _infer_sam(self, frame: Any, context: dict[str, Any]) -> None:
        adapter = self.adapters.get("sam") or self._load_adapter("sam")
        if not adapter:
            return
        try:
            result = adapter.infer(frame, context)
            context["masks"] = result.get("masks", [])
        except Exception as exc:  # noqa: BLE001
            self.state.set_module("sam", "error", str(exc))

    def _infer_reasoner(self, frame: Any, context: dict[str, Any]) -> None:
        adapter = self.adapters.get("reasoner") or self._load_adapter("reasoner")
        if not adapter:
            return
        prompt = self._prompt(context)
        started = time.monotonic()
        try:
            result = adapter.infer(frame, {"prompt": prompt, "goal": self.config["reasoner"].get("goal", ""), "vision": context})
            reasoning = result.get("reasoning", {})
            actions = reasoning.get("actions", result.get("actions", []))
            self.state.set_reasoning({"text": reasoning.get("text", result.get("text", "")), "actions": actions})
            context["events"].extend(result.get("events", []))
            if actions:
                self._handle_actions(actions)
        except Exception as exc:  # noqa: BLE001
            self.state.set_module("reasoner", "error", str(exc))
        finally:
            self.state.set_metric("reasoner_latency_ms", round((time.monotonic() - started) * 1000.0, 2))

    def _submit_reasoner(self, frame: Any, context: dict[str, Any]) -> None:
        """Keep only the newest frame for the slower understanding layer."""
        try:
            pending_frame = frame.copy() if hasattr(frame, "copy") else frame
        except Exception:
            pending_frame = frame
        with self.reason_lock:
            self.reason_pending = (pending_frame, copy.deepcopy(context))
        self.reason_wakeup.set()

    def _reason_worker(self) -> None:
        while not self.stop_event.is_set():
            self.reason_wakeup.wait(0.25)
            self.reason_wakeup.clear()
            with self.reason_lock:
                pending = self.reason_pending
                self.reason_pending = None
            if pending is None:
                continue
            frame, context = pending
            self._infer_reasoner(frame, context)

    def _handle_actions(self, actions: list[dict[str, Any]]) -> None:
        policy = self.config.get("automation", {})
        if not policy.get("enabled", False) or policy.get("dry_run", True):
            self.state.log("info", f"输入动作已记录（dry-run）: {len(actions)} 个")
            return
        if self.input is None:
            self.state.log("error", "输入动作被拒绝：SendInput 适配器未就绪")
            return
        try:
            message = self.input.submit(actions, dry_run=False)
            self.state.log("info", message)
        except InputError as exc:
            self.state.log("error", str(exc))

    @staticmethod
    def _prompt(context: dict[str, Any]) -> str:
        return (
            "根据当前屏幕视觉结果和 OCR 输出 JSON。\n"
            f"frame={context['frame_id']} screen={context['width']}x{context['height']}\n"
            f"objects={context['objects']}\ntexts={context['texts']}\n"
            "输出 scene、event、risk、recommended_action；任何输入动作必须单独经过策略校验。"
        )

    def _close_adapters(self) -> None:
        for name, adapter in list(self.adapters.items()):
            try:
                adapter.close()
            except Exception as exc:  # noqa: BLE001
                self.state.log("warn", f"关闭 {name} 失败: {exc}")
        self.adapters.clear()
