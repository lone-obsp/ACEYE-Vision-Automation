from __future__ import annotations

from time import perf_counter_ns
from typing import Any
import json
from pathlib import Path

from .state import RhythmNote, now_ns

# Legacy hue windows retained for older profiles only. The current Muse Dash
# strategy uses target appearance; judge-circle color does not identify enemies.
DEFAULT_HUE_RANGES: dict[str, tuple[float, float]] = {
    "blue": (185.0, 255.0),
    "purple": (255.0, 320.0),
}


class RhythmDetector:
    """Small CV detector. It intentionally has no dependency on YOLO.

    Compatible modes:

    * Legacy bright-blob mode (default): grayscale threshold + lane medians.
      Kept for osu! and offline benchmarks; single-frame behavior unchanged.
    * Color mode (when ``config["lane_bands"]`` is provided): hue/saturation
      filtered blob detection restricted to the two Muse Dash lane bands.
      Retained for older profiles; it is not used by the current Muse Dash profile.
    * Template mode: real recording crops, local color verification and lane
      geometry; rotating hold stars also require visible parallel staff rails.
    """

    def __init__(self, lanes: int = 4, threshold: int = 190, max_width: int = 800,
                 config: dict | None = None) -> None:
        self.lanes = max(1, int(lanes))
        self.threshold = int(threshold)
        self.max_width = max(160, int(max_width))
        cfg = dict(config or {})
        bands = cfg.get("lane_bands")
        self.lane_bands: list[tuple[float, float]] = []
        if isinstance(bands, (list, tuple)):
            for band in bands:
                if isinstance(band, (list, tuple)) and len(band) == 2:
                    lo, hi = float(band[0]), float(band[1])
                    if 0.0 <= lo < hi <= 1.0:
                        self.lane_bands.append((lo, hi))
        self.color_mode = bool(self.lane_bands)

        raw_ranges = cfg.get("hue_ranges") or {}
        self.hue_ranges: dict[str, tuple[float, float]] = dict(DEFAULT_HUE_RANGES)
        if isinstance(raw_ranges, dict):
            for name, rng in raw_ranges.items():
                if isinstance(rng, (list, tuple)) and len(rng) == 2:
                    self.hue_ranges[str(name)] = (float(rng[0]), float(rng[1]))

        lane_hues = cfg.get("lane_hues") or {}
        self.lane_hues: list[list[tuple[float, float]]] = []
        for index in range(len(self.lane_bands)):
            names = lane_hues.get(index, lane_hues.get(str(index)))
            if names is None:
                # 0 = up (blue), 1 = down (purple) by Muse Dash convention.
                names = ["blue"] if index == 0 else ["purple"]
            if isinstance(names, str):
                names = [names]
            windows = [self.hue_ranges[str(name)] for name in names if str(name) in self.hue_ranges]
            self.lane_hues.append(windows or [self.hue_ranges["blue"]])

        detector_cfg = cfg.get("detector") or {}
        if not isinstance(detector_cfg, dict):
            detector_cfg = {}
        self.min_sat = float(detector_cfg.get("min_sat", 0.32))
        self.min_val = float(detector_cfg.get("min_val", 0.30))
        self.min_area = int(detector_cfg.get("min_area", 6))  # downsampled pixels
        self.max_area_frac = float(detector_cfg.get("max_area_frac", 0.02))
        self.max_mask_frac = float(detector_cfg.get("max_mask_frac", 0.35))
        self.sample_width = int(detector_cfg.get("sample_width", 320))
        self.strategy = str(detector_cfg.get("strategy", "color"))
        self.template_cfg = detector_cfg
        self.templates = []
        if self.strategy == "templates":
            self._load_templates()

    # ------------------------------------------------------------------ API

    def detect(self, frame: Any, *, roi: tuple[int, int, int, int] | None = None) -> tuple[list[RhythmNote], float]:
        started = perf_counter_ns()
        try:
            import numpy as np
            image = np.asarray(frame)
            if self.strategy == "templates":
                notes = self._detect_templates(image)
            elif self.color_mode:
                notes = self._detect_color(np, image)
            else:
                notes = self._detect_bright(np, image, roi)
            return notes, (perf_counter_ns() - started) / 1_000_000.0
        except ImportError:
            return [], (perf_counter_ns() - started) / 1_000_000.0

    def _load_templates(self) -> None:
        """Small, local appearance samples from the supplied song; no download."""
        import cv2
        path = Path(__file__).parent / self.template_cfg.get("templates", "templates/muse_dash/manifest.json")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        factor = self.sample_width / float(manifest["reference_width"])
        for spec in manifest["templates"]:
            source = cv2.imread(str(path.parent / spec["file"]))
            if source is None:
                raise ValueError(f"Muse Dash template missing: {spec['file']}")
            for scale in self.template_cfg.get("scales", [0.90, 1.0, 1.10]):
                patch = cv2.resize(source, None, fx=factor * scale, fy=factor * scale,
                                   interpolation=cv2.INTER_AREA)
                self.templates.append((spec, cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY), patch))

    def _detect_templates(self, image) -> list[RhythmNote]:
        """Appearance + lane geometry. Enemy color never determines the lane."""
        import cv2
        import numpy as np
        if image.ndim != 3 or image.shape[2] < 3:
            return []
        fh, fw = image.shape[:2]
        scale = self.sample_width / fw
        small = cv2.resize(image[:, :, :3], (self.sample_width, round(fh * scale)),
                           interpolation=cv2.INTER_AREA)
        h, w = small.shape[:2]
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        search_left = int(w * float(self.template_cfg.get("search_left", 0.215)))
        search_right = int(w * float(self.template_cfg.get("search_right", 0.99)))
        candidates = []
        for spec, patch, color_patch in self.templates:
            # Most targets follow the ordinary lane bands. The recorded boss
            # gloves arc down into J; ground saws are F jumps, not J attacks.
            # Explicit per-appearance bands describe these observed exceptions.
            bands = spec.get("search_bands", [(i, lo, hi) for i, (lo, hi) in enumerate(self.lane_bands)])
            template_hsv = cv2.cvtColor(color_patch, cv2.COLOR_BGR2HSV)
            mask = template_hsv[:, :, 1] > 140
            for lane, lo, hi in bands:
                ph, pw = patch.shape[:2]
                # Bands constrain the target anchor, not its entire bounding box.
                top = max(0, int(lo * h) - ph // 2)
                bottom = min(h, int(hi * h) + ph // 2)
                left = max(0, search_left - pw // 2)
                area = gray[top:bottom, left:search_right]
                if area.shape[0] < ph or area.shape[1] < pw:
                    continue
                scores = cv2.matchTemplate(area, patch, cv2.TM_CCOEFF_NORMED)
                for _ in range(6):
                    _, score, _, loc = cv2.minMaxLoc(scores)
                    if score < float(spec.get("score", .72)):
                        break
                    xx, yy = loc
                    ax, ay = spec.get("anchor", [.5, .5])
                    cx, cy = left + xx + pw * ax, top + yy + ph * ay
                    scores[max(0, yy-ph//2):yy+ph//2+1, max(0, xx-pw//2):xx+pw//2+1] = -1
                    if not lo*h <= cy <= hi*h or cx < search_left:
                        continue
                    # Verify appearance color locally, after cheap gray search.
                    # This is per-template evidence, independent of UP/DOWN.
                    candidate = small[top+yy:top+yy+ph, left+xx:left+xx+pw]
                    color_score = float(cv2.matchTemplate(candidate, color_patch, cv2.TM_CCOEFF_NORMED)[0,0])
                    if color_score < float(spec.get("color_score", .60)):
                        continue
                    candidate_hsv = cv2.cvtColor(candidate, cv2.COLOR_BGR2HSV)
                    if int(mask.sum()) > 12:
                        delta = abs(candidate_hsv[:,:,0].astype('int16')-template_hsv[:,:,0].astype('int16'))
                        similar = (np.minimum(delta,180-delta) < 14) & (candidate_hsv[:,:,1] > 100)
                        if float(similar[mask].mean()) < .55:
                            continue
                    candidates.append((score, cx, cy, pw, ph, lane, spec))
        selected = self._hold_stars(cv2, np, small, scale)
        for score, x, y, pw, ph, lane, spec in sorted(candidates, key=lambda item: -item[0]):
            if any(lane == n.lane and abs(x/scale-n.x) < .42*max(pw/scale,n.width_px)
                   and abs(y/scale-n.y) < .6*max(ph/scale,n.height_px) for n in selected):
                continue
            kind = spec.get("kind", "tap")
            tail_x = None
            tail_visible = False
            if kind == "hold":
                staff = self._staff_span(cv2, np, small, x, y, pw, ph)
                if staff is None:
                    continue  # A star/particle without a musical staff is not a hold.
                start, end = staff
                if end - x >= pw * .8:
                    tail_x, tail_visible = end/scale, end < w - 6
                elif x - start >= pw * .8:
                    kind = "hold_tail"
                else:
                    continue
            selected.append(RhythmNote(x=x/scale, y=y/scale, lane=lane, kind=kind,
                confidence=float(score), width_px=pw/scale, height_px=ph/scale,
                appearance=str(spec["name"]), tail_x_px=tail_x, tail_visible=tail_visible))
        return selected

    @staticmethod
    def _staff_span(cv2, np, frame, x, y, width, height):
        """Require saturated parallel staff rails next to a star; return extent."""
        h, w = frame.shape[:2]
        y0, y1 = max(0, int(y-height*.4)), min(h, int(y+height*.4)+1)
        hsv = cv2.cvtColor(frame[y0:y1], cv2.COLOR_BGR2HSV)
        rails = ((hsv[:,:,0] >= 125) & (hsv[:,:,0] <= 170) &
                 (hsv[:,:,1] > 210) & (hsv[:,:,2] > 170)).astype('uint8')
        lines = cv2.morphologyEx(rails, cv2.MORPH_OPEN, np.ones((1, max(15,int(width*.65))),np.uint8))
        # Count distinct horizontal rails, not one thick patch of scenery.
        rows = np.diff(np.pad(lines.astype('int16'),((1,0),(0,0))),axis=0)
        rail_count = (rows == 1).sum(axis=0)
        if rail_count.max(initial=0) < 3:
            return None
        columns = (rail_count >= 2).astype('uint8').reshape(1,-1)
        columns = cv2.morphologyEx(columns, cv2.MORPH_CLOSE, np.ones((1,17),np.uint8))[0]
        runs = []
        edges = np.diff(np.r_[0,columns,0].astype('int16'))
        for a,b in zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)):
            if b-a >= width*.8 and a <= x+width*1.5 and b >= x-width*1.5:
                runs.append((int(a),int(b)))
        if not runs:
            return None
        return min(a for a,b in runs), max(b for a,b in runs)

    def _hold_stars(self, cv2, np, small, scale) -> list[RhythmNote]:
        """Rotating white star silhouette + connected staff, independent of rotation."""
        hsv = cv2.cvtColor(small,cv2.COLOR_BGR2HSV)
        mask = ((hsv[:,:,1] < 70)&(hsv[:,:,2] > 220)).astype('uint8')
        h,w = mask.shape
        notes=[]
        for lane,(lo,hi) in enumerate(self.lane_bands):
            band=mask.copy();band[:int((lo-.05)*h)]=0;band[int((hi+.05)*h):]=0
            band[:,:int(self.template_cfg.get('search_left',.215)*w)]=0
            count,_,stats,centers=cv2.connectedComponentsWithStats(band)
            for index in range(1,count):
                x,y,bw,bh,area=stats[index];cx,cy=centers[index]
                if not lo*h<=cy<=hi*h or not .035*w<=bw<=.10*w or not .07*h<=bh<=.19*h:
                    continue
                if not .45<=area/(bw*bh)<=.82 or not .65<=bw/bh<=1.45:
                    continue
                staff=self._staff_span(cv2,np,small,cx,cy,bw*1.6,bh*1.5)
                if staff is None:
                    continue
                a,b=staff
                kind='hold' if b-cx > bw*1.5 else 'hold_tail' if cx-a > bw*1.5 else ''
                if not kind:continue
                notes.append(RhythmNote(x=cx/scale,y=cy/scale,lane=lane,kind=kind,
                    confidence=.9,width_px=bw*1.6/scale,height_px=bh*1.5/scale,
                    appearance='hold_star',tail_x_px=b/scale if kind=='hold' else None,
                    tail_visible=b<w-6))
        return notes

    def staff_end(self, image, judge_x: float, judge_y: float) -> float | None:
        import cv2
        import numpy as np
        scale = self.sample_width / image.shape[1]
        small = cv2.resize(image[:,:,:3], (self.sample_width, round(image.shape[0]*scale)))
        h,w = small.shape[:2]
        span = self._staff_span(cv2,np,small,judge_x*w,judge_y*h,w*.085,h*.17)
        if span and span[0] < (judge_x+.06)*w and span[1] > (judge_x+.08)*w:
            return span[1]/scale
        return None

    # -------------------------------------------------------------- legacy

    def _detect_bright(self, np, image, roi) -> list[RhythmNote]:
        if image.ndim == 3:
            gray = image[:, :, :3].mean(axis=2)
        else:
            gray = image
        if roi and len(roi) == 4:
            x, y, width, height = (int(value) for value in roi)
            # A zero-sized profile ROI means "use the complete frame".
            if width > 0 and height > 0:
                gray = gray[y:y + height, x:x + width]
        stride = max(1, int((gray.shape[1] / max(160, int(getattr(self, "max_width", 800)))) + 0.999))
        sampled = gray[::stride, ::stride]
        mask = sampled >= self.threshold
        ys, xs = np.where(mask)
        if not len(xs):
            return []
        height, width = sampled.shape[:2]
        notes: list[RhythmNote] = []
        # Group bright pixels by lane and use robust medians. This is a
        # deterministic baseline, not a claim of game-specific accuracy.
        for lane in range(self.lanes):
            left, right = lane * width // self.lanes, (lane + 1) * width // self.lanes
            selected = (xs >= left) & (xs < right)
            if int(selected.sum()) < 8:
                continue
            notes.append(RhythmNote(float(np.median(xs[selected]) * stride), float(np.median(ys[selected]) * stride), lane=lane,
                                    confidence=min(1.0, float(selected.sum()) / 500.0)))
        return notes

    # --------------------------------------------------------------- color

    def _detect_color(self, np, image) -> list[RhythmNote]:
        """Hue-gated blob detection inside the configured lane bands."""
        if image.ndim != 3 or image.shape[2] < 3:
            return []
        # DXGI frames are BGRA; the shared hub publishes the first 3 channels (BGR).
        bgr = image[:, :, :3]
        frame_h, frame_w = int(bgr.shape[0]), int(bgr.shape[1])
        if frame_w < 8 or frame_h < 8:
            return []
        stride = max(1, int((frame_w / max(160, self.sample_width)) + 0.999))
        sub = bgr[::stride, ::stride].astype("float32") / 255.0
        b, g, r = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
        maxc = np.maximum(np.maximum(r, g), b)
        minc = np.minimum(np.minimum(r, g), b)
        delta = maxc - minc
        sat = np.where(maxc > 1e-6, delta / np.maximum(maxc, 1e-6), 0.0)
        dz = np.maximum(delta, 1e-6)
        hue = np.select(
            [maxc == r, maxc == g],
            [((g - b) / dz) % 6.0, ((b - r) / dz) + 2.0],
            default=((r - g) / dz) + 4.0,
        ) * 60.0
        chroma = (sat >= self.min_sat) & (maxc >= self.min_val)
        sub_h, sub_w = chroma.shape
        notes: list[RhythmNote] = []
        for lane, (band_lo, band_hi) in enumerate(self.lane_bands):
            row_lo = int(band_lo * frame_h / stride)
            row_hi = int((band_hi * frame_h + stride - 1) / stride)
            row_lo = max(0, min(row_lo, sub_h))
            row_hi = max(row_lo, min(row_hi, sub_h))
            if row_hi <= row_lo:
                continue
            lane_mask = np.zeros_like(chroma)
            for lo, hi in self.lane_hues[lane]:
                if lo <= hi:
                    lane_mask |= (hue >= lo) & (hue <= hi)
                else:  # wrapping range (e.g. 320..20 across red)
                    lane_mask |= (hue >= lo) | (hue <= hi)
            lane_mask &= chroma
            band = lane_mask[row_lo:row_hi]
            if int(band.sum()) > int(self.max_mask_frac * band.size):
                # A saturated color wash (background/scene) dominates this
                # lane band; skip it instead of chasing hundreds of blobs.
                continue
            for blob in self._components(band):
                count = len(blob)
                if count < self.min_area or count > self.max_area_frac * sub_w * sub_h:
                    continue  # particles (too small) or background (too large)
                ys = [point[0] for point in blob]
                xs = [point[1] for point in blob]
                cx = sum(xs) / count * stride
                cy = (sum(ys) / count + row_lo) * stride
                bw = (max(xs) - min(xs) + 1) * stride
                bh = (max(ys) - min(ys) + 1) * stride
                sat_mean = float(sat[row_lo:row_hi][tuple(zip(*blob))].mean()) if count else 0.0
                confidence = max(0.0, min(1.0, min(1.0, count / 60.0) * 0.6 + min(1.0, sat_mean) * 0.4))
                notes.append(RhythmNote(x=float(cx), y=float(cy), lane=lane, kind="tap",
                                        confidence=confidence, detected_ns=now_ns(),
                                        width_px=float(bw), height_px=float(bh)))
        return notes

    def _components(self, mask) -> list[list[tuple[int, int]]]:
        """4-connected components of a small boolean mask (pure Python BFS)."""
        height, width = mask.shape[0], mask.shape[1]
        visited = [[False] * width for _ in range(height)]
        mask_rows = [list(row) for row in mask]
        blobs: list[list[tuple[int, int]]] = []
        for y in range(height):
            row = mask_rows[y]
            vrow = visited[y]
            for x in range(width):
                if not row[x] or vrow[x]:
                    continue
                stack = [(y, x)]
                vrow[x] = True
                blob: list[tuple[int, int]] = []
                while stack:
                    cy, cx = stack.pop()
                    blob.append((cy, cx))
                    if cy > 0 and mask_rows[cy - 1][cx] and not visited[cy - 1][cx]:
                        visited[cy - 1][cx] = True
                        stack.append((cy - 1, cx))
                    if cy + 1 < height and mask_rows[cy + 1][cx] and not visited[cy + 1][cx]:
                        visited[cy + 1][cx] = True
                        stack.append((cy + 1, cx))
                    if cx > 0 and mask_rows[cy][cx - 1] and not visited[cy][cx - 1]:
                        visited[cy][cx - 1] = True
                        stack.append((cy, cx - 1))
                    if cx + 1 < width and mask_rows[cy][cx + 1] and not visited[cy][cx + 1]:
                        visited[cy][cx + 1] = True
                        stack.append((cy, cx + 1))
                blobs.append(blob)
        return blobs
