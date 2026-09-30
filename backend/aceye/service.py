from __future__ import annotations

import copy
import json
import secrets
import threading
from pathlib import Path
from typing import Any

from .commands import HELP, parse_command
from .config import ConfigStore
from .capture import SharedFrameHub
from .model_manager import ModelManager
from .pipeline import VisionPipeline
from .state import RuntimeState
from .rhythm.runtime import RhythmRuntime


class AceyeService:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.config_store = ConfigStore(self.root)
        config = self.config_store.load()
        self.state = RuntimeState(config)
        self.model_manager = ModelManager(self.root, config)
        self.frame_hub = SharedFrameHub()
        self.token = secrets.token_urlsafe(24)
        self.pipeline: VisionPipeline | None = None
        self.rhythm: RhythmRuntime | None = None
        self._rhythm_owned_pipeline = False
        self._lock = threading.RLock()
        self.state.log("info", "ACEYE 后端已就绪；模型按模块懒加载")

    def health(self) -> dict[str, Any]:
        return {"ok": True, "version": "0.2.0", "token_required": True, "token": self.token}

    def snapshot(self) -> dict[str, Any]:
        snapshot = self.state.snapshot()
        snapshot["rhythm"] = self.rhythm.status() if self.rhythm else {"status": "stopped", "game": self.config_store.data.get("rhythm", {}).get("game", "osu")}
        return snapshot

    def models(self) -> dict[str, Any]:
        self.model_manager.config = copy.deepcopy(self.config_store.data)
        return {"ok": True, "models": self.model_manager.inspect()}

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.pipeline and self.pipeline.thread and self.pipeline.thread.is_alive():
                return {"ok": True, "message": "流水线已经在运行", "state": self.snapshot()}
            if self.pipeline:
                self.pipeline.stop()
            self.frame_hub.clear()
            self.pipeline = VisionPipeline(self.root, copy.deepcopy(self.config_store.data), self.state, self.frame_hub)
            self._rhythm_owned_pipeline = False
            self.pipeline.start()
            return {"ok": True, "message": "正在启动流水线", "state": self.snapshot()}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self.stop_rhythm()
            if self.pipeline:
                self.pipeline.stop()
                self.pipeline = None
            else:
                self.state.set_phase("stopped")
            return {"ok": True, "message": "流水线已停止", "state": self.snapshot()}

    def emergency(self) -> dict[str, Any]:
        with self._lock:
            config = self.config_store.update({"automation": {"enabled": False, "dry_run": True}})
            self.state.set_config(config)
            self.state.log("warn", "紧急停止：流水线停止，自动化输入已关闭")
            self.stop_rhythm()
            if self.pipeline:
                self.pipeline.stop()
                self.pipeline = None
            self.state.set_phase("stopped")
            return {"ok": True, "message": "已紧急停止并关闭自动化", "state": self.snapshot()}

    def update_config(self, partial: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            changed = self._config_differs(partial)
            config = self.config_store.update(partial)
            self.state.set_config(config)
            self.model_manager.config = copy.deepcopy(config)
            if self.pipeline:
                self.pipeline.config = copy.deepcopy(config)
            if not changed:
                # No real change (profile already active, same values): do not
                # log or announce a save. The frontend polls state once per
                # second and used to trigger this path repeatedly.
                return {"ok": True, "changed": False, "message": "配置未变化",
                        "state": self.snapshot()}
            self.state.log("info", "配置已保存；需要时请重启流水线使模型设置生效")
            return {"ok": True, "changed": True, "message": "配置已保存", "state": self.snapshot()}

    def _config_differs(self, partial: dict[str, Any]) -> bool:
        """True when ``partial`` would actually change the stored config."""
        if not isinstance(partial, dict) or not partial:
            return False
        current = self.config_store.data

        def compare(base: Any, incoming: Any) -> bool:
            if isinstance(incoming, dict):
                if not isinstance(base, dict):
                    return True
                return any(compare(base.get(key), value) for key, value in incoming.items())
            return base != incoming

        return compare(current, partial)

    def command(self, raw: str) -> dict[str, Any]:
        try:
            command = parse_command(raw)
            if not command.name:
                raise ValueError("请输入命令，/help 查看帮助")
            result = self._dispatch(command.name, command.args)
            self.state.set_command(raw, True, result["message"])
            return result
        except Exception as exc:  # noqa: BLE001
            self.state.set_command(raw, False, str(exc))
            self.state.log("warn", str(exc))
            return {"ok": False, "message": str(exc), "state": self.snapshot()}

    def prepare(self, name: str) -> dict[str, Any]:
        name = {"vision": "detection", "reasoner": "reasoner", "vlm": "reasoner"}.get(name, name)
        with self._lock:
            inventory = self.model_manager.inspect().get(name)
            if inventory is None:
                return {"ok": False, "message": f"未知模型模块: {name}", "state": self.snapshot()}
            if inventory["status"] in {"missing", "disabled"}:
                message = f"{name} 模型状态为 {inventory['status']}：{inventory['path'] or '未配置'}；不会自动下载"
                self.state.set_module(name, "missing-model" if inventory["status"] == "missing" else "disabled", message)
                self.state.set_job(False, message)
                self.state.log("warn", message)
                return {"ok": False, "message": message, "state": self.snapshot(), "model": inventory}
            if self.pipeline is None:
                self.pipeline = VisionPipeline(self.root, copy.deepcopy(self.config_store.data), self.state)
            pipeline = self.pipeline
            self.state.set_job(True, f"正在准备 {name} 模型；本地权重检查可能需要较长时间")

            def worker() -> None:
                ok, message = pipeline.prepare(name)
                self.state.set_job(False, message)
                self.state.log("info" if ok else "error", message)

            threading.Thread(target=worker, name=f"ACEYE-prepare-{name}", daemon=True).start()
            return {"ok": True, "message": f"已开始准备 {name}", "state": self.snapshot(), "model": inventory}

    def model_status(self) -> dict[str, Any]:
        """Return the explicit local model inventory used by the UI."""
        return {"ok": True, "models": self.model_manager.inspect(), "state": self.snapshot()}

    def start_rhythm(self, game: str | None = None) -> dict[str, Any]:
        with self._lock:
            if self.rhythm and self.rhythm.is_running:
                return {"ok": True, "message": "Rhythm 已经在运行", "rhythm": self.rhythm.status(), "state": self.snapshot()}
            if not (self.pipeline and self.pipeline.thread and self.pipeline.thread.is_alive()):
                capture_config = copy.deepcopy(self.config_store.data)
                for name in ("detection", "ocr", "sam", "reasoner"):
                    capture_config.setdefault(name, {})["enabled"] = False
                capture_config.setdefault("output", {})["overlay"] = False
                self.frame_hub.clear()
                self.pipeline = VisionPipeline(self.root, capture_config, self.state, self.frame_hub)
                self._rhythm_owned_pipeline = True
                self.pipeline.start()
                self.state.log("info", "Rhythm 请求自动启动通用流水线，并复用其共享 FrameHub")
            if self.rhythm:
                self.rhythm.stop()
            config = copy.deepcopy(self.config_store.data.get("rhythm", {}))
            if game:
                config["game"] = game
            config.setdefault("game", "muse_dash")
            if config["game"] in {"muse_dash", "musedash"}:
                # Coherence guard: a running Muse Dash rhythm must never be
                # reported next to a different top-level mode selection.
                stored_profile = str(self.config_store.data.get("profile", ""))
                if "musedash" not in stored_profile.replace("_", "").lower() and "muse" not in stored_profile.lower():
                    self.config_store.update({"profile": "config/profiles/muse_dash.json"})
            # Merge the selected game profile without requiring adapters to
            # know about the profile store. Explicit rhythm values win.
            profile_path = config.get("profile")
            if config.get("game") in {"muse_dash", "musedash"} and (not profile_path or "osu" in str(profile_path).lower()):
                profile_path = "config/profiles/muse_dash.json"
            if profile_path:
                path = (self.root / str(profile_path)).resolve()
                if path.exists():
                    try:
                        profile = json.loads(path.read_text(encoding="utf-8"))
                        if isinstance(profile, dict):
                            for key in ("roi", "lanes", "input_mapping", "threshold", "hit_y", "note_velocity_px_s",
                                        "key_duration_ms", "judge", "lane_bands", "hue_ranges", "lane_hues",
                                        "detector", "track", "timing", "actions", "foreground",
                                        "capture_game_window", "profile"):
                                if (key not in config or config.get(key) is None) and key in profile:
                                    config[key] = profile[key]
                            capture = profile.get("capture", {})
                            if ("roi" not in config or config.get("roi") is None) and isinstance(capture, dict):
                                config["roi"] = capture.get("roi")
                    except Exception as exc:  # noqa: BLE001
                        self.state.log("warn", f"Rhythm profile 加载失败: {exc}")
            self.rhythm = RhythmRuntime(self.root, config, self.frame_hub, self.state.log)
            try:
                self.rhythm.start()
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "message": str(exc), "rhythm": self.rhythm.status(), "state": self.snapshot()}
            return {"ok": True, "message": "Rhythm 已启动（真实 SendInput，共享 FrameHub）", "rhythm": self.rhythm.status(), "state": self.snapshot()}

    def stop_rhythm(self) -> dict[str, Any]:
        if self.rhythm:
            self.rhythm.stop()
        if self._rhythm_owned_pipeline and self.pipeline:
            self.pipeline.stop()
            self.pipeline = None
            self._rhythm_owned_pipeline = False
        return {"ok": True, "message": "Rhythm 已停止", "rhythm": self.rhythm.status() if self.rhythm else {"status": "stopped"}, "state": self.snapshot()}

    def rhythm_status(self) -> dict[str, Any]:
        return {"ok": True, "rhythm": self.rhythm.status() if self.rhythm else {"status": "stopped", "game": self.config_store.data.get("rhythm", {}).get("game", "osu")}}

    def reason(self) -> dict[str, Any]:
        if not self.pipeline:
            return {"ok": False, "message": "请先启动流水线", "state": self.snapshot()}
        self.pipeline.request_reason()
        return {"ok": True, "message": "已请求下一帧理解", "state": self.snapshot()}

    def segment(self) -> dict[str, Any]:
        if not self.pipeline:
            return {"ok": False, "message": "请先启动流水线", "state": self.snapshot()}
        self.pipeline.request_sam()
        return {"ok": True, "message": "已请求下一帧精细分割", "state": self.snapshot()}

    def _dispatch(self, name: str, args: list[str]) -> dict[str, Any]:
        if name == "help":
            return {"ok": True, "message": HELP, "state": self.snapshot()}
        if name == "start":
            return self.start()
        if name == "stop":
            return self.stop()
        if name == "emergency":
            return self.emergency()
        if name == "status":
            return {"ok": True, "message": "状态已刷新", "state": self.snapshot()}
        if name == "reason":
            return self.reason()
        if name == "sam":
            return self.segment()
        if name in {"fps", "conf", "goal", "ocr", "overlay", "dryrun"}:
            if not args:
                raise ValueError(f"/{name} 需要一个参数")
            if name == "fps":
                return self.update_config({"capture": {"fps": int(args[0])}})
            if name == "conf":
                return self.update_config({"detection": {"confidence": float(args[0])}})
            if name == "goal":
                return self.update_config({"reasoner": {"goal": " ".join(args)}})
            if name == "ocr":
                return self.update_config({"ocr": {"enabled": self._on_off(args[0])}})
            if name == "overlay":
                return self.update_config({"output": {"overlay": self._on_off(args[0])}})
            if name == "dryrun":
                return self.update_config({"automation": {"dry_run": self._on_off(args[0])}})
        raise ValueError(f"未知指令 /{name}；/help 查看帮助")

    @staticmethod
    def _on_off(value: str) -> bool:
        value = value.lower()
        if value in {"on", "true", "1", "yes"}:
            return True
        if value in {"off", "false", "0", "no"}:
            return False
        raise ValueError("参数只能是 on 或 off")
