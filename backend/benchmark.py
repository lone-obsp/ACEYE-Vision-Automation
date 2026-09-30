"""ACEYE repeatable performance benchmarks.

The commands intentionally exercise the real capture bridge and adapters used by
the service.  They emit one JSON document so results can be archived or fed to
the Web UI without scraping human log output::

    python backend/benchmark.py --benchmark-capture --seconds 5
    python backend/benchmark.py --benchmark-yolo --seconds 5
    python backend/benchmark.py --benchmark-pipeline --seconds 10

No model is downloaded by this tool.  Missing optional models are reported in
the JSON result and do not make capture benchmarking fail.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any


# Running ``python backend/benchmark.py`` does not automatically put the
# backend directory on sys.path.  Keep this script usable from the source tree
# and from a packaged ACEYE directory.
BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from aceye.capture import make_capture  # noqa: E402
from aceye.config import ConfigStore  # noqa: E402
from aceye.pipeline import VisionPipeline  # noqa: E402
from aceye.state import RuntimeState  # noqa: E402
from aceye.adapters.detection import YoloByteTrackAdapter  # noqa: E402


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((percentile / 100.0) * (len(ordered) - 1)))))
    return float(ordered[index])


def _gpu_metrics() -> dict[str, Any]:
    result: dict[str, Any] = {"cuda_available": False, "gpu_memory_mb": 0.0}
    try:
        import torch  # type: ignore

        result["cuda_available"] = bool(torch.cuda.is_available())
        result["torch"] = str(torch.__version__)
        result["torch_cuda"] = str(getattr(torch.version, "cuda", None))
        if result["cuda_available"]:
            result["gpu"] = str(torch.cuda.get_device_name(0))
            result["gpu_memory_mb"] = round(float(torch.cuda.memory_allocated()) / (1024 * 1024), 1)
    except Exception as exc:  # optional dependency
        result["error"] = str(exc)
    return result


def _load_config(root: Path) -> dict[str, Any]:
    return ConfigStore(root).load()


def benchmark_capture(root: Path, config: dict[str, Any], seconds: float) -> dict[str, Any]:
    capture = make_capture(root, config)
    timings: list[float] = []
    count = 0
    started = time.perf_counter()
    shape: list[int] | None = None
    try:
        capture.start()
        started = time.perf_counter()
        deadline = started + max(0.2, seconds)
        while time.perf_counter() < deadline:
            sample_started = time.perf_counter()
            packet = capture.grab()
            elapsed_ms = (time.perf_counter() - sample_started) * 1000.0
            if packet is None:
                continue
            timings.append(elapsed_ms)
            count += 1
            frame = packet.get("frame")
            if frame is not None and hasattr(frame, "shape"):
                shape = [int(v) for v in frame.shape]
        total = max(0.001, time.perf_counter() - started)
        return {
            "ok": True,
            "benchmark": "capture",
            "seconds": round(total, 3),
            "frames": count,
            "capture_fps": round(count / total, 2),
            "grab_ms_avg": round(statistics.fmean(timings), 3) if timings else 0.0,
            "grab_ms_p50": round(_percentile(timings, 50), 3),
            "grab_ms_p95": round(_percentile(timings, 95), 3),
            "frame_shape": shape,
            **_gpu_metrics(),
        }
    finally:
        try:
            capture.close()
        except Exception:
            pass


def benchmark_yolo(root: Path, config: dict[str, Any], seconds: float, warmup: int = 3) -> dict[str, Any]:
    capture = make_capture(root, config)
    adapter = YoloByteTrackAdapter(config.get("detection", {}), root)
    timings: list[float] = []
    count = 0
    shape: list[int] | None = None
    started = time.perf_counter()
    try:
        capture.start()
        # Grab one frame on the same thread as the capture bridge.  DXGI
        # desktop duplication is thread-affine on Windows.
        packet = None
        while packet is None:
            packet = capture.grab()
        frame = packet["frame"]
        if hasattr(frame, "shape"):
            shape = [int(v) for v in frame.shape]
        adapter.load()
        context = {"frame_id": 0, "width": packet["width"], "height": packet["height"]}
        for index in range(max(0, int(warmup))):
            adapter.infer(frame, context)
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                torch.cuda.synchronize()
        except Exception:
            pass
        started = time.perf_counter()
        deadline = started + max(0.2, seconds)
        while time.perf_counter() < deadline:
            # Keep capture on this thread; use the latest available frame and
            # avoid adding an unbounded queue to the benchmark itself.
            packet = capture.grab()
            if packet is None:
                continue
            frame = packet["frame"]
            context["frame_id"] = int(packet["sequence"])
            sample_started = time.perf_counter()
            adapter.infer(frame, context)
            try:
                import torch  # type: ignore

                if torch.cuda.is_available():
                    torch.cuda.synchronize()
            except Exception:
                pass
            timings.append((time.perf_counter() - sample_started) * 1000.0)
            count += 1
        total = max(0.001, time.perf_counter() - started)
        return {
            "ok": True,
            "benchmark": "yolo",
            "seconds": round(total, 3),
            "frames": count,
            "yolo_fps": round(count / total, 2),
            "inference_ms_avg": round(statistics.fmean(timings), 3) if timings else 0.0,
            "inference_ms_p50": round(_percentile(timings, 50), 3),
            "inference_ms_p95": round(_percentile(timings, 95), 3),
            "input_shape": shape,
            "weights": str(adapter._weights) if adapter._weights else None,
            "backend": getattr(adapter, "_backend", "pytorch"),
            "precision": "FP16" if bool(config.get("detection", {}).get("half", True)) else "FP32",
            **_gpu_metrics(),
        }
    finally:
        try:
            adapter.close()
        except Exception:
            pass
        try:
            capture.close()
        except Exception:
            pass


def benchmark_pipeline(root: Path, config: dict[str, Any], seconds: float) -> dict[str, Any]:
    state = RuntimeState(config)
    pipeline = VisionPipeline(root, config, state)
    started = time.perf_counter()
    try:
        pipeline.start()
        # Wait for the requested interval while workers collect metrics.
        time.sleep(max(0.2, seconds))
        snapshot = state.snapshot()
        metrics = snapshot.get("metrics", {})
        modules = snapshot.get("modules", {})
        total = max(0.001, time.perf_counter() - started)
        return {
            "ok": True,
            "benchmark": "pipeline",
            "seconds": round(total, 3),
            "phase": snapshot.get("phase"),
            "capture_fps": metrics.get("capture_fps", 0.0),
            "detection_fps": metrics.get("detection_fps", metrics.get("vision_fps", 0.0)),
            "vision_fps": metrics.get("vision_fps", 0.0),
            "capture_frames": metrics.get("capture_frames", metrics.get("frames", 0)),
            "detection_frames": metrics.get("detection_frames", 0),
            "dropped_capture": metrics.get("dropped_capture", 0),
            "dropped_vision": metrics.get("dropped_vision", metrics.get("dropped", 0)),
            "queue_depth": metrics.get("queue_depth", 0),
            "latest_age_ms": metrics.get("latest_age_ms", metrics.get("latency_ms", 0.0)),
            "yolo_latency_ms": metrics.get("yolo_latency_ms", 0.0),
            "ocr_latency_ms": metrics.get("ocr_latency_ms", 0.0),
            "sam_latency_ms": metrics.get("sam_latency_ms", 0.0),
            "reasoner_latency_ms": metrics.get("reasoner_latency_ms", 0.0),
            "gpu_memory_mb": metrics.get("gpu_memory_mb", 0.0),
            "modules": modules,
            "logs": snapshot.get("logs", [])[-20:],
        }
    finally:
        pipeline.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ACEYE capture/YOLO/pipeline benchmark")
    parser.add_argument("--root", type=Path, default=BACKEND_DIR.parent)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--benchmark-capture", action="store_true")
    parser.add_argument("--benchmark-yolo", action="store_true")
    parser.add_argument("--benchmark-pipeline", action="store_true")
    parser.add_argument("--capture-backend", choices=("auto", "native", "mss"), default=None,
                        help="override capture backend for controlled comparisons")
    parser.add_argument("--core-only", action="store_true",
                        help="disable OCR/SAM/reasoner/overlay to isolate capture+YOLO")
    args = parser.parse_args(argv)
    selected = [args.benchmark_capture, args.benchmark_yolo, args.benchmark_pipeline]
    if sum(bool(item) for item in selected) != 1:
        parser.error("choose exactly one of --benchmark-capture, --benchmark-yolo, --benchmark-pipeline")
    root = args.root.resolve()
    config = _load_config(root)
    if args.capture_backend:
        config.setdefault("capture", {})["backend"] = args.capture_backend
    if args.core_only:
        config.setdefault("ocr", {})["enabled"] = False
        config.setdefault("sam", {})["enabled"] = False
        config.setdefault("reasoner", {})["enabled"] = False
        config.setdefault("output", {})["overlay"] = False
    try:
        if args.benchmark_capture:
            result = benchmark_capture(root, config, args.seconds)
        elif args.benchmark_yolo:
            result = benchmark_yolo(root, config, args.seconds, args.warmup)
        else:
            result = benchmark_pipeline(root, config, args.seconds)
    except Exception as exc:  # keep machine-readable failure details
        result = {"ok": False, "error": str(exc), "benchmark": "unknown"}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
