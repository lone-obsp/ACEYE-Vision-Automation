from __future__ import annotations

from .calibration import CalibrationProfile
from .detector import RhythmDetector
from .timing import TimingEstimator
from .state import now_ns


class OsuAdapter:
    """Initial osu! playfield adapter using CV primitives and timing contracts."""

    name = "osu"

    def __init__(self, config: dict | None = None, *, calibration: CalibrationProfile | None = None) -> None:
        self.config = config or {}
        self.detector = RhythmDetector(1, int(self.config.get("threshold", 190)))
        self.timing = TimingEstimator(calibration, float(self.config.get("hit_y", 0.86)))

    def process(self, frame, captured_ns: int | None = None) -> dict:
        captured_ns = captured_ns or now_ns()
        notes, cv_ms = self.detector.detect(frame, roi=self.config.get("roi"))
        return {"game": "osu", "captured_ns": captured_ns, "notes": notes, "actions": [], "cv_latency_ms": cv_ms,
                "timing_latency_ms": 0.0, "status": "detection-only; action mapping requires calibrated playfield"}

