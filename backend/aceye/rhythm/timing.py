from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter_ns

from .calibration import CalibrationProfile
from .state import RhythmNote, now_ns


@dataclass(slots=True)
class TimingEstimate:
    target_ns: int
    confidence: float
    latency_ms: float


class TimingEstimator:
    """Estimate impact time from motion without invoking a VLM.

    ``estimate`` keeps the legacy vertical-motion contract used by the osu!
    adapter and offline benchmarks.

    ``estimate_crossing`` is the Muse Dash path: given a tracked target
    moving right -> left with velocity ``vx_px_s`` (< 0), predict when its
    center reaches the judge line ``hit_x_px``. It hard-fails (returns
    ``None``) for stale predictions (already deep in the past — the source
    of the historical -40306 ms "predicted hit") and for predictions beyond
    the scheduling horizon (wait for a better velocity estimate instead of
    scheduling garbage far in the future).
    """

    def __init__(self, calibration: CalibrationProfile | None = None, hit_y: float = 0.86, *,
                 hit_x: float = 0.210, horizon_ms: float = 800.0, stale_ms: float = 60.0,
                 min_speed_px_s: float = 120.0) -> None:
        self.calibration = calibration or CalibrationProfile()
        self.hit_y = float(hit_y)
        self.hit_x = float(hit_x)
        self.horizon_ms = float(horizon_ms)
        self.stale_ms = float(stale_ms)
        self.min_speed_px_s = float(min_speed_px_s)

    # ------------------------------------------------------------- legacy

    def estimate(self, note: RhythmNote, *, velocity_px_s: float = 0.0, frame_height: int = 1) -> TimingEstimate:
        started = perf_counter_ns()
        distance = max(0.0, float(frame_height) * self.hit_y - float(note.y))
        seconds = distance / velocity_px_s if velocity_px_s > 1.0 else 0.08
        target = note.detected_ns + int(max(0.0, seconds * 1000.0 - self.calibration.total_offset_ms) * 1_000_000)
        confidence = max(0.0, min(1.0, note.confidence * (1.0 if velocity_px_s > 1.0 else 0.5)))
        return TimingEstimate(target, confidence, (perf_counter_ns() - started) / 1_000_000.0)

    # ------------------------------------------------------ crossing path

    def estimate_crossing(self, *, x_px: float, vx_px_s: float, hit_x_px: float, frame_ns: int,
                          confidence: float = 1.0, current_ns: int | None = None) -> TimingEstimate | None:
        """Predict the judge-line crossing time of a leftward-moving target.

        Returns ``None`` (discard, do not schedule) when the target is not
        moving left fast enough, when the prediction is stale, or when it is
        still beyond the scheduling horizon.
        """
        started = perf_counter_ns()
        if vx_px_s >= -self.min_speed_px_s:
            return None
        distance = float(x_px) - float(hit_x_px)  # > 0: still right of the judge line
        seconds = distance / -vx_px_s
        target_ns = int(frame_ns + seconds * 1_000_000_000.0
                        - self.calibration.total_offset_ms * 1_000_000.0)
        now = now_ns() if current_ns is None else current_ns
        if target_ns < now - int(self.stale_ms * 1_000_000.0):
            return None  # stale hit: already far in the past -> drop it
        if target_ns > now + int(self.horizon_ms * 1_000_000.0):
            return None  # beyond horizon: wait for more frames / better vx
        return TimingEstimate(target_ns, max(0.0, min(1.0, float(confidence))),
                              (perf_counter_ns() - started) / 1_000_000.0)
