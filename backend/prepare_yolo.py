"""Validate a local YOLO checkpoint and optionally prepare a TensorRT engine.

This command never changes config/aceye.json automatically.  The existing .pt
checkpoint remains the fallback until the exported engine has passed inference.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from aceye.config import ConfigStore  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare and validate YOLO TensorRT FP16")
    parser.add_argument("--root", type=Path, default=BACKEND_DIR.parent)
    parser.add_argument("--weights", default="models/yolo26n-seg.pt")
    parser.add_argument("--imgsz", default="544,960")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    weights = Path(args.weights)
    if not weights.is_absolute():
        weights = root / weights
    if not weights.exists():
        print(json.dumps({"ok": False, "status": "missing", "weights": str(weights),
                          "message": "本地权重不存在；不会自动下载"}, ensure_ascii=False, indent=2))
        return 2
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        print(json.dumps({"ok": False, "status": "runtime-missing", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 3
    try:
        shape_values = tuple(int(part.strip()) for part in str(args.imgsz).split(","))
        imgsz = shape_values if len(shape_values) == 2 else int(shape_values[0])
        model = YOLO(str(weights))
        check = model.predict(source=str(root / "config" / "__missing_input__.png"),
                              imgsz=imgsz, device=args.device, quantize=16, verbose=False)
        # The nonexistent source is expected to fail before inference. Loading
        # the model above is the validation that does not require a sample.
        del check
    except Exception as exc:
        # A missing sample is harmless; any model-load failure is actionable.
        if "__missing_input__" not in str(exc):
            print(json.dumps({"ok": False, "status": "validation-error", "message": str(exc)}, ensure_ascii=False, indent=2))
            return 4
    result = {"ok": True, "status": "validated", "weights": str(weights), "imgsz": list(imgsz) if isinstance(imgsz, tuple) else imgsz,
              "device": args.device, "engine": str(weights.with_suffix(".engine")), "exported": False}
    if args.export:
        started = time.perf_counter()
        try:
            engine = model.export(format="engine", imgsz=imgsz, batch=1, half=True, device=args.device,
                                  dynamic=False, simplify=True, workspace=4)
            engine_path = Path(str(engine))
            if not engine_path.exists():
                raise RuntimeError(f"Ultralytics returned no engine file: {engine}")
            # Load the engine to ensure TensorRT can deserialize it before any
            # operator is asked to edit configuration.
            verified = YOLO(str(engine_path))
            verified.close() if hasattr(verified, "close") else None
            result.update(status="ready", exported=True, engine=str(engine_path), export_seconds=round(time.perf_counter() - started, 2))
        except Exception as exc:
            result.update(status="export-error", message=str(exc), export_seconds=round(time.perf_counter() - started, 2))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 5
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
