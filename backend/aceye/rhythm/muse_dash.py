from __future__ import annotations

from .calibration import CalibrationProfile
from .detector import RhythmDetector
from .timing import TimingEstimator
from .state import RhythmNote, RhythmTrack, now_ns


class MuseDashAdapter:
    """Muse Dash CV adapter; output is timestamped and dry-run safe.

    Two modes:

    * Legacy mode (no ``judge`` config): the original single-frame
      four-lane behavior, kept for offline benchmarks and unit tests.
    * Tracking mode (``judge`` present, i.e. the real muse_dash.json
      profile): detections are fed into multi-frame tracks. A track only
      becomes actionable after ``confirm_frames`` consecutive sightings
      with a stable right -> left velocity, and emits at most ONE action
      for its whole lifetime. This is what stops the historical
      repeated-trigger storm (scheduled_actions / real_input_count 27817).
    """

    name = "muse_dash"

    def __init__(self, config: dict | None = None, *, calibration: CalibrationProfile | None = None) -> None:
        self.config = config or {}
        self.lanes = int(self.config.get("lanes", 4))
        self.keys = list(self.config.get("input_mapping", ["left", "right", "left", "right"]))
        judge = self.config.get("judge")
        self.tracking = isinstance(judge, dict) and "up" in judge and "down" in judge
        if self.tracking:
            self._init_tracking(judge, calibration)
        else:
            self.detector = RhythmDetector(self.lanes, int(self.config.get("threshold", 190)))
            self.timing = TimingEstimator(calibration, float(self.config.get("hit_y", 0.86)))

    # ------------------------------------------------------------ tracking

    def _init_tracking(self, judge: dict, calibration: CalibrationProfile | None) -> None:
        self.lanes = 2
        up = judge.get("up") or {}
        down = judge.get("down") or {}
        self.judge_x = [float((up.get("center") or [0.210, 0.347])[0]),
                        float((down.get("center") or [0.210, 0.625])[0])]
        self.judge_y = [float((up.get("center") or [0.210, 0.347])[1]),
                        float((down.get("center") or [0.210, 0.625])[1])]
        self.lane_keys = [str(up.get("action", "f")), str(down.get("action", "j"))]

        bands = self.config.get("lane_bands") or {}
        up_band = (bands.get("up") or [0.22, 0.475])
        down_band = (bands.get("down") or [0.50, 0.75])
        detector_cfg = {
            "lane_bands": [up_band, down_band],
            "hue_ranges": self.config.get("hue_ranges"),
            "lane_hues": {0: (self.config.get("lane_hues") or {}).get("up", ["blue"]),
                          1: (self.config.get("lane_hues") or {}).get("down", ["purple"])},
            "detector": self.config.get("detector") or {},
        }
        self.detector = RhythmDetector(2, int(self.config.get("threshold", 190)), config=detector_cfg)

        timing_cfg = self.config.get("timing") or {}
        if not isinstance(timing_cfg, dict):
            timing_cfg = {}
        self.timing = TimingEstimator(
            calibration,
            float(self.config.get("hit_y", 0.86)),
            hit_x=float(timing_cfg.get("hit_x", self.judge_x[0])),
            horizon_ms=float(timing_cfg.get("horizon_ms", 800.0)),
            stale_ms=float(timing_cfg.get("stale_ms", 60.0)),
            min_speed_px_s=float(timing_cfg.get("min_speed_px_s", 120.0)),
        )

        track_cfg = self.config.get("track") or {}
        if not isinstance(track_cfg, dict):
            track_cfg = {}
        self.confirm_frames = max(2, int(track_cfg.get("confirm_frames", 3)))
        self.min_speed_px_s = float(track_cfg.get("min_speed_px_s", 120.0))
        self.gate_px = float(track_cfg.get("gate_px", 90.0))
        self.expire_ms = float(track_cfg.get("expire_ms", 260.0))
        self.static_prune_ms = float(track_cfg.get("static_prune_ms", 450.0))
        self.max_tracks = max(2, int(track_cfg.get("max_tracks", 16)))
        self.history_cap = max(4, int(track_cfg.get("history", 12)))
        self.judge_dead_zone_px = float(track_cfg.get("judge_dead_zone_px", 40.0))
        self.new_track_margin = float(track_cfg.get("new_track_margin", .065))
        self.reference_width = float(track_cfg.get("reference_width", 2560))

        actions_cfg = self.config.get("actions") or {}
        if not isinstance(actions_cfg, dict):
            actions_cfg = {}
        self.tap_duration_ms = float(actions_cfg.get("tap_duration_ms", 30.0))
        self.hold_min_ms = float(actions_cfg.get("hold_min_ms", 150.0))
        self.hold_max_ms = float(actions_cfg.get("hold_max_ms", 1200.0))
        self.hold_aspect = float(actions_cfg.get("hold_aspect", 2.0))
        self.hold_min_width_frac = float(actions_cfg.get("hold_min_width_frac", 0.045))
        self.large_width_frac = float(actions_cfg.get("large_width_frac", 0.055))
        self.large_hit_count = max(1, int(actions_cfg.get("large_hit_count", 4)))
        self.large_hit_interval_ms = float(actions_cfg.get("large_hit_interval_ms", 90.0))

        self._tracks: dict[int, RhythmTrack] = {}
        self._next_track_id = 1
        self.confirmed_targets_total = 0

    def reset(self) -> None:
        """Drop every track. Called on Rhythm start/stop to kill stale state."""
        self._tracks.clear()
        self._next_track_id = 1
        self.confirmed_targets_total = 0

    # --------------------------------------------------------------- frame

    def process(self, frame, captured_ns: int | None = None, *, current_ns: int | None = None) -> dict:
        captured_ns = captured_ns or now_ns()
        if not self.tracking:
            return self._process_legacy(frame, captured_ns)

        notes, cv_ms = self.detector.detect(frame)
        shape = list(getattr(frame, "shape", [1, 1]))
        frame_h = int(shape[0]) if len(shape) >= 1 else 1
        frame_w = int(shape[1]) if len(shape) >= 2 else 1

        self._update_tracks(notes, captured_ns, frame_w)
        hold_updates = self._update_holds(frame, notes, captured_ns, frame_w, frame_h)
        timing_ms = 0.0
        actions: list[dict] = []
        scheduled_targets: dict[int, int] = {}
        for track in self._tracks.values():
            if not track.confirmed or track.scheduled or track.last_seen_ns != captured_ns:
                continue
            estimate = self.timing.estimate_crossing(
                x_px=track.x, vx_px_s=track.vx_px_s,
                hit_x_px=self.judge_x[track.lane] * frame_w,
                frame_ns=track.last_seen_ns, confidence=track.confidence, current_ns=current_ns)
            if estimate is None:
                continue  # stale or beyond horizon: keep the track, emit nothing
            timing_ms = max(timing_ms, estimate.latency_ms)
            track.predicted_hit_ns = int(estimate.target_ns)
            action = self._build_action(track, estimate)
            if action:
                track.scheduled = True  # one action per target, forever
                scheduled_targets[track.track_id] = int(estimate.target_ns)
                actions.append(action)
        visible: list[RhythmNote] = []
        for track in self._tracks.values():
            if not track.confirmed:
                continue
            note = track.to_note()
            if track.track_id in scheduled_targets:
                note.target_ns = scheduled_targets[track.track_id]
            visible.append(note)
        return {"game": "muse_dash", "captured_ns": captured_ns, "notes": visible, "actions": actions,
                "cv_latency_ms": cv_ms, "timing_latency_ms": timing_ms,
                "active_tracks": sum(1 for t in self._tracks.values() if t.confirmed),
                "total_tracks": len(self._tracks), "confirmed_targets": self.confirmed_targets_total,
                "hold_updates": hold_updates}

    # -------------------------------------------------------------- tracks

    def _update_tracks(self, notes: list[RhythmNote], t_ns: int, frame_w: int) -> None:
        # Detections parked on the judge line itself are the stationary judge
        # circles (blue/purple inner cores), not approaching targets.
        usable = [n for n in notes if n.kind != "hold_tail"]
        # Associate closest predicted pairs globally, with stable lane and
        # appearance. Nearby targets in different lanes remain independent.
        pairs = []
        for index, note in enumerate(usable):
            for tid, track in self._tracks.items():
                if track.lane != note.lane or track.kind != note.kind:
                    continue
                if track.appearance and note.appearance and track.appearance.split('_')[0] != note.appearance.split('_')[0]:
                    continue
                dt = max(0.0, (t_ns-track.last_seen_ns)/1e9)
                predicted = track.x + track.vx_px_s*dt
                gate = self.gate_px * frame_w/self.reference_width
                if not track.confirmed:
                    gate = max(gate, dt*2.5*frame_w)
                else:
                    gate = max(gate, abs(track.vx_px_s)*dt*.5)
                gate = min(gate, .10*frame_w)
                distance = abs(note.x-predicted)
                if distance <= gate:
                    pairs.append((distance,tid,index))
        matched_ids, matched_notes = set(), set()
        for _, tid, index in sorted(pairs):
            if tid in matched_ids or index in matched_notes:
                continue
            self._feed(self._tracks[tid],usable[index],t_ns,frame_w)
            matched_ids.add(tid); matched_notes.add(index)
        for index, note in enumerate(usable):
            if index in matched_notes or len(self._tracks) >= self.max_tracks:
                continue
            if note.x < (self.judge_x[note.lane]+self.new_track_margin)*frame_w:
                continue  # judge effects / targets first seen after the hit cannot become new tracks
            track = RhythmTrack(track_id=self._next_track_id, lane=note.lane, born_ns=t_ns)
            self._next_track_id += 1
            self._feed(track,note,t_ns,frame_w)
            self._tracks[track.track_id] = track
            matched_ids.add(track.track_id)

        expired: list[int] = []
        for track_id, track in self._tracks.items():
            if track_id in matched_ids:
                track.misses = 0
                continue
            track.misses += 1
            age_since_seen_ms = (t_ns - track.last_seen_ns) / 1_000_000.0
            until = max(track.predicted_hit_ns or 0, track.hold_end_ns or 0)
            if track.scheduled and t_ns < until + 200_000_000:
                continue  # retain identity across a brief effect/occlusion and through the hold
            if age_since_seen_ms > self.expire_ms:
                expired.append(track_id)  # lost target: drop the whole track
            elif not track.confirmed and not track.scheduled:
                age_ms = (t_ns - track.born_ns) / 1_000_000.0
                if age_ms > self.static_prune_ms and abs(track.vx_px_s) < self.min_speed_px_s:
                    expired.append(track_id)  # stationary clutter: judge ring, UI, character
        for track_id in expired:
            self._tracks.pop(track_id, None)

    def _feed(self, track: RhythmTrack, note: RhythmNote, t_ns: int, frame_w: int) -> None:
        track.x = float(note.x)
        track.y = float(note.y)
        track.width_px = float(note.width_px)
        track.height_px = float(note.height_px)
        track.confidence = float(note.confidence)
        track.appearance = note.appearance
        track.tail_x_px = note.tail_x_px
        track.tail_visible = note.tail_visible
        track.last_seen_ns = t_ns
        track.hits += 1
        track.push_sample(t_ns, note.x, cap=self.history_cap)
        track.vx_px_s = track.velocity_from_samples()
        track.kind = note.kind if note.kind in {"tap", "hold", "large", "dodge"} else "tap"
        if not track.confirmed and len(track.samples) >= self.confirm_frames:
            # Right -> left motion confirmation: enough frames, mostly
            # leftward samples, and a real leftward speed.
            if (track.leftward_ratio() >= 0.7 and track.vx_px_s <= -self.min_speed_px_s):
                track.confirmed = True
                self.confirmed_targets_total += 1

    def _update_holds(self, frame, notes, t_ns, frame_w, frame_h) -> list[dict]:
        """Track musical-staff tail, extending one held key without extra taps."""
        updates = []
        for track in self._tracks.values():
            if track.kind != "hold" or not track.confirmed:
                continue
            tail = track.tail_x_px if track.last_seen_ns == t_ns else None
            tails = [n for n in notes if n.lane == track.lane and n.kind == "hold_tail"]
            if tails:
                tail = min(n.x for n in tails)
            elif track.scheduled and (track.predicted_hit_ns or 0) <= t_ns:
                tail = self.detector.staff_end(frame, self.judge_x[track.lane], self.judge_y[track.lane])
            if tail is None or track.vx_px_s >= -self.min_speed_px_s:
                continue
            end = int(t_ns + (tail-self.judge_x[track.lane]*frame_w)/-track.vx_px_s*1e9
                      - self.timing.calibration.total_offset_ms*1e6)
            if track.predicted_hit_ns:
                end = min(end, track.predicted_hit_ns+int(self.hold_max_ms*1e6))
            if not track.scheduled or track.hold_end_ns is None or end > track.hold_end_ns + 30_000_000:
                track.hold_end_ns = end
                if track.scheduled:
                    updates.append({"track_id":track.track_id,"key":self.lane_keys[track.lane],"end_ns":end})
        return updates

    def _build_action(self, track: RhythmTrack, estimate) -> dict | None:
        key = self.lane_keys[track.lane] if 0 <= track.lane < len(self.lane_keys) else self.keys[0]
        action = {"key": key, "target_ns": int(estimate.target_ns), "lane": track.lane,
                  "track_id": track.track_id, "kind": track.kind,
                  "confidence": float(estimate.confidence), "vx_px_s": float(track.vx_px_s)}
        if track.kind == "hold":
            hold_ms = ((track.hold_end_ns or estimate.target_ns)-estimate.target_ns)/1e6
            action["duration_ms"] = float(min(self.hold_max_ms, max(self.hold_min_ms, hold_ms)))
        elif track.kind == "large":
            action["duration_ms"] = self.tap_duration_ms
            action["hit_count"] = int(self.large_hit_count)
            action["hit_interval_ms"] = float(self.large_hit_interval_ms)
        else:
            action["duration_ms"] = float(self.config.get("key_duration_ms", self.tap_duration_ms))
        return action

    # --------------------------------------------------------------- legacy

    def _process_legacy(self, frame, captured_ns: int) -> dict:
        notes, cv_ms = self.detector.detect(frame, roi=self.config.get("roi"))
        actions = []
        height = int(getattr(frame, "shape", [1, 1])[0])
        for note in notes:
            estimate = self.timing.estimate(note, velocity_px_s=float(self.config.get("note_velocity_px_s", 0.0)), frame_height=height)
            note.target_ns = estimate.target_ns
            actions.append({"key": self.keys[note.lane % len(self.keys)], "target_ns": estimate.target_ns,
                            "duration_ms": float(self.config.get("key_duration_ms", 30.0)), "lane": note.lane,
                            "confidence": estimate.confidence})
        return {"game": "muse_dash", "captured_ns": captured_ns, "notes": notes, "actions": actions,
                "cv_latency_ms": cv_ms, "timing_latency_ms": 0.0}
