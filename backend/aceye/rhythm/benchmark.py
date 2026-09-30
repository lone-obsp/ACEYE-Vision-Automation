"""Measured Rhythm Mode benchmark (no model download, dry-run input only)."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from ..capture import make_capture
from .controller import InputScheduler, RhythmController
from .muse_dash import MuseDashAdapter


def run(root: Path, seconds: float = 3.0, backend: str = "mss", target_fps: int = 120,
        roi: tuple[int, int, int, int] | None = None) -> dict:
    config = {"capture": {"backend": backend, "output": 0, "roi": roi}}
    capture = make_capture(root, config)
    scheduler = InputScheduler(dry_run=True)
    adapter = MuseDashAdapter({"lanes": 4})
    controller = RhythmController(adapter, scheduler, dry_run=True)
    capture_ms: list[float] = []
    cv_ms: list[float] = []
    total_ms: list[float] = []
    frames = 0
    dropped = 0
    try:
        capture.start()
        started = time.perf_counter()
        deadline = started + max(0.2, seconds)
        next_tick = started
        while time.perf_counter() < deadline:
            next_tick += 1.0 / max(1, target_fps)
            begin = time.perf_counter_ns()
            packet = capture.grab()
            capture_ms.append((time.perf_counter_ns() - begin) / 1_000_000.0)
            if packet is None:
                dropped += 1
            else:
                frames += 1
                result = controller.process(packet["frame"], int(packet.get("captured_at", time.monotonic()) * 1_000_000_000))
                cv_ms.append(float(result.get("cv_latency_ms", 0.0)))
                total_ms.append((time.perf_counter_ns() - begin) / 1_000_000.0)
            remaining = next_tick - time.perf_counter()
            if remaining > 0:
                time.sleep(remaining)
        elapsed = max(0.001, time.perf_counter() - started)
        metrics = scheduler.metrics()
        return {
            "ok": True, "benchmark": "rhythm", "backend": backend, "target_fps": target_fps,
            "seconds": round(elapsed, 3), "frames": frames, "dropped_frames": dropped,
            "capture_fps": round(frames / elapsed, 2),
            "capture_latency_ms": round(statistics.fmean(capture_ms), 3) if capture_ms else 0.0,
            "cv_latency_ms": round(statistics.fmean(cv_ms), 3) if cv_ms else 0.0,
            "capture_to_input_ms": round(statistics.fmean(total_ms), 3) if total_ms else 0.0,
            "input_scheduling_jitter_ms": metrics["jitter_ms_mean"],
            "input_scheduling_jitter_p95_ms": metrics["jitter_ms_p95"],
            "scheduled_actions": metrics["scheduled"],
            "dry_run": True,
        }
    finally:
        scheduler.close()
        capture.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="ACEYE Rhythm Mode benchmark")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--backend", choices=("mss", "auto", "native"), default="mss")
    parser.add_argument("--target-fps", type=int, default=120)
    parser.add_argument("--roi", default="", help="x,y,width,height relative to the selected monitor")
    args = parser.parse_args()
    roi = tuple(int(part.strip()) for part in args.roi.split(",")) if args.roi else None
    if roi is not None and len(roi) != 4:
        parser.error("--roi must be x,y,width,height")
    print(json.dumps(run(args.root.resolve(), args.seconds, args.backend, args.target_fps, roi), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
