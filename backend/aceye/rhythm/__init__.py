"""High-frequency, pure-vision rhythm-game path.

Rhythm adapters are deliberately separate from the general YOLO pipeline. They
consume a latest ROI frame, use lightweight CV, and emit timestamped dry-run
actions through :class:`InputScheduler`.
"""

from .controller import InputScheduler, RhythmController
from .muse_dash import MuseDashAdapter
from .osu import OsuAdapter
from .runtime import LatestRoiFrameHub

__all__ = ["InputScheduler", "RhythmController", "MuseDashAdapter", "OsuAdapter", "LatestRoiFrameHub"]
