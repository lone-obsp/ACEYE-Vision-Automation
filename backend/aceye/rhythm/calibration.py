from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class CalibrationProfile:
    capture_latency_ms: float = 0.0
    processing_latency_ms: float = 0.0
    display_latency_offset_ms: float = 0.0
    input_delivery_offset_ms: float = 0.0
    user_game_offset_ms: float = 0.0

    @property
    def total_offset_ms(self) -> float:
        return (self.capture_latency_ms + self.processing_latency_ms +
                self.display_latency_offset_ms + self.input_delivery_offset_ms +
                self.user_game_offset_ms)


def load_profile(path: Path) -> CalibrationProfile:
    if not path.exists():
        return CalibrationProfile()
    data = json.loads(path.read_text(encoding="utf-8"))
    return CalibrationProfile(**{key: float(data.get(key, 0.0)) for key in asdict(CalibrationProfile())})


def save_profile(path: Path, profile: CalibrationProfile) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(profile), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

