from __future__ import annotations

from dataclasses import asdict, dataclass, field
from time import perf_counter_ns
from typing import Any


def now_ns() -> int:
    """Monotonic timestamp used by capture, CV, timing and input layers."""
    return perf_counter_ns()


@dataclass(slots=True)
class RhythmNote:
    x: float
    y: float
    lane: int = 0
    kind: str = "note"
    confidence: float = 0.0
    detected_ns: int = field(default_factory=now_ns)
    target_ns: int | None = None
    track_id: int | None = None
    width_px: float = 0.0
    height_px: float = 0.0
    appearance: str = ""
    tail_x_px: float | None = None
    tail_visible: bool = False


@dataclass(slots=True)
class RhythmTrack:
    """Multi-frame track of a single lane target moving right -> left.

    A track is only promoted to ``confirmed`` after it has been observed for
    ``confirm_frames`` consecutive frames with a stable leftward velocity.
    Actions are emitted at most once per track (``scheduled`` flag), which is
    what prevents the historical per-frame duplicate input storm.
    """

    track_id: int
    lane: int = 0  # 0 = up lane (F), 1 = down lane (J)
    kind: str = "tap"  # tap | hold | large
    x: float = 0.0
    y: float = 0.0
    width_px: float = 0.0
    height_px: float = 0.0
    confidence: float = 0.0
    born_ns: int = field(default_factory=now_ns)
    last_seen_ns: int = field(default_factory=now_ns)
    hits: int = 0  # consecutive matched frames
    misses: int = 0  # consecutive unmatched frames
    samples: list[tuple[int, float]] = field(default_factory=list)  # (ns, x_px), newest last
    vx_px_s: float = 0.0  # smoothed horizontal velocity; negative = right -> left
    confirmed: bool = False
    scheduled: bool = False
    predicted_hit_ns: int | None = None
    appearance: str = ""
    tail_x_px: float | None = None
    tail_visible: bool = False
    hold_end_ns: int | None = None

    def push_sample(self, ns: int, x: float, cap: int = 12) -> None:
        self.samples.append((int(ns), float(x)))
        if len(self.samples) > max(2, int(cap)):
            del self.samples[0]

    def velocity_from_samples(self) -> float:
        """Robust horizontal velocity from the sample history (px/s, < 0 = leftward)."""
        if len(self.samples) < 2:
            return self.vx_px_s
        velocities: list[float] = []
        # A short recent window follows speed changes. Pairwise slopes with
        # >= one frame separation reduce animation/coordinate quantization noise.
        recent = self.samples[-6:]
        for i, (t0, x0) in enumerate(recent):
            for t1, x1 in recent[i+1:]:
                dt_ns = t1 - t0
                if dt_ns >= 25_000_000:
                    velocities.append((x1 - x0) * 1_000_000_000.0 / dt_ns)
        if not velocities:
            return self.vx_px_s
        velocities.sort()
        median = velocities[len(velocities) // 2]
        # Smooth toward the new estimate to damp single-frame noise.
        return float(self.vx_px_s * 0.4 + median * 0.6) if self.vx_px_s else float(median)

    def leftward_ratio(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        leftward = sum(1 for (t0, x0), (t1, x1) in zip(self.samples, self.samples[1:]) if x1 < x0)
        return leftward / max(1, len(self.samples) - 1)

    @property
    def age_ns(self) -> int:
        return now_ns() - self.born_ns

    def to_note(self) -> RhythmNote:
        return RhythmNote(x=self.x, y=self.y, lane=self.lane, kind=self.kind,
                          confidence=self.confidence, detected_ns=self.last_seen_ns,
                          track_id=self.track_id, target_ns=self.predicted_hit_ns,
                          width_px=self.width_px, height_px=self.height_px,
                          appearance=self.appearance, tail_x_px=self.tail_x_px, tail_visible=self.tail_visible)


@dataclass(slots=True)
class RhythmMetrics:
    capture_fps: float = 0.0
    capture_latency_ms: float = 0.0
    cv_latency_ms: float = 0.0
    timing_latency_ms: float = 0.0
    capture_to_input_ms: float = 0.0
    input_jitter_ms: float = 0.0
    dropped_frames: int = 0
    detected_notes: int = 0


@dataclass(slots=True)
class RhythmState:
    game: str
    frame_id: int = 0
    last_capture_ns: int = 0
    notes: list[RhythmNote] = field(default_factory=list)
    metrics: RhythmMetrics = field(default_factory=RhythmMetrics)
    events: list[dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "game": self.game,
            "frame_id": self.frame_id,
            "notes": [asdict(note) for note in self.notes],
            "metrics": asdict(self.metrics),
            "events": list(self.events[-50:]),
        }
