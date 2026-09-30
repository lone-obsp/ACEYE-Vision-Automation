from __future__ import annotations

import heapq
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .state import RhythmNote, now_ns


@dataclass(order=True, slots=True)
class ScheduledKey:
    target_ns: int
    sequence: int
    key: str = field(compare=False)
    duration_ms: float = field(compare=False)
    dry_run: bool = field(compare=False, default=False)
    generation: int = field(compare=False, default=0)
    track_id: int | None = field(compare=False, default=None)


class InputScheduler:
    """High-resolution scheduler with explicit dry-run support and telemetry.

    Defensive gates added after the historical runaway (scheduled_actions /
    real_input_count climbing to 27817):

    * targets already deep in the past are dropped instead of fired instantly;
    * the queue is depth-capped so a detection storm can never grow it
      without bound.
    """

    def __init__(self, sink: Callable[[str, float], bool | None] | None = None, *, dry_run: bool = False,
                 logger: Callable[[str, str], None] | None = None, stale_tolerance_ms: float = 15.0,
                 max_queue: int = 128,
                 hold_extender: Callable[[str, int], bool] | None = None) -> None:
        self.sink = sink
        self.dry_run = bool(dry_run)
        self.logger = logger
        self.hold_extender = hold_extender
        self._hold_ends: dict[int, int] = {}
        self.stale_tolerance_ns = int(float(stale_tolerance_ms) * 1_000_000.0)
        self.max_queue = max(8, int(max_queue))
        self._queue: list[ScheduledKey] = []
        self._condition = threading.Condition()
        self._sequence = 0
        self._generation = 0
        self._stop = False
        self._jitter: list[float] = []
        self._executed = 0
        self._real_input_count = 0
        self._dropped_stale = 0
        self._dropped_overflow = 0
        self._last_error = ""
        self._thread = threading.Thread(target=self._run, name="ACEYE-RhythmInput", daemon=True)
        self._thread.start()

    @property
    def generation(self) -> int:
        with self._condition:
            return self._generation

    def schedule_key(self, key: str, target_qpc_time: int | float, duration_ms: float = 30.0,
                     *, generation: int | None = None, track_id: int | None = None) -> ScheduledKey | None:
        target_ns = int(target_qpc_time * 1_000_000_000) if isinstance(target_qpc_time, float) else int(target_qpc_time)
        with self._condition:
            if self._stop or (generation is not None and generation != self._generation):
                return None
            if target_ns < now_ns() - self.stale_tolerance_ns:
                self._dropped_stale += 1
                self._log("debug", f"dropped_stale: key={key} age={(now_ns() - target_ns) / 1_000_000.0:.2f} ms")
                return None  # stale hit: discard instead of firing late
            if len(self._queue) >= self.max_queue:
                self._dropped_overflow += 1
                self._log("warn", f"dropped_overflow: key={key} queue_depth={len(self._queue)}")
                return None
            self._sequence += 1
            item = ScheduledKey(target_ns, self._sequence, str(key), float(duration_ms), self.dry_run, self._generation, track_id)
            expired_holds = [identity for identity, end in self._hold_ends.items() if end < now_ns()]
            for identity in expired_holds:
                self._hold_ends.pop(identity, None)
            if track_id is not None:
                self._hold_ends[int(track_id)] = target_ns + int(float(duration_ms) * 1_000_000)
            heapq.heappush(self._queue, item)
            self._condition.notify()
            self._log("debug", f"scheduled: key={item.key} delay={(target_ns - now_ns()) / 1_000_000.0:.2f} ms")
            return item

    def extend_hold(self, track_id: int, key: str, end_ns: int, *, generation: int | None = None) -> bool:
        with self._condition:
            if self._stop or (generation is not None and generation != self._generation):
                return False
            track_id = int(track_id)
            if track_id not in self._hold_ends or int(end_ns) <= now_ns():
                return False
            self._hold_ends[track_id] = max(self._hold_ends[track_id], int(end_ns))
            if not self.dry_run and self.hold_extender:
                return bool(self.hold_extender(str(key), self._hold_ends[track_id]))
            return False

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._stop:
                    self._condition.wait(0.2)
                if self._stop and not self._queue:
                    return
                item = self._queue[0]
                remaining = item.target_ns - now_ns()
                if remaining > 1_000_000:
                    self._condition.wait(remaining / 1_000_000_000.0)
                    continue
                heapq.heappop(self._queue)
                # Keep cancellation and the bounded native call ordered: once
                # cancel_pending returns, no action from that generation can fire.
                if self._stop or item.generation != self._generation:
                    continue
                actual = now_ns()
                if actual - item.target_ns > self.stale_tolerance_ns:
                    self._dropped_stale += 1
                    self._log("debug", f"dropped_stale: key={item.key} execution_age={(actual - item.target_ns) / 1_000_000.0:.2f} ms")
                    continue
                self._jitter.append((actual - item.target_ns) / 1_000_000.0)
                if len(self._jitter) > 4096:
                    del self._jitter[:-2048]
                self._executed += 1
                if not item.dry_run and self.sink:
                    try:
                        duration_ms = item.duration_ms
                        if item.track_id is not None:
                            duration_ms = max(5.0, (self._hold_ends.get(item.track_id, item.target_ns + int(duration_ms * 1_000_000)) - actual) / 1_000_000.0)
                        sent = self.sink(item.key, duration_ms)
                        # Legacy fake sinks return None. The native runtime
                        # explicitly returns False when no key_down was sent.
                        if sent is not False:
                            self._real_input_count += 1
                    except Exception as exc:  # noqa: BLE001
                        self._last_error = str(exc)
                        self._log("error", f"real_input_error: key={item.key} error={exc}")

    def _log(self, level: str, message: str) -> None:
        if self.logger:
            try:
                self.logger(level, message)
            except Exception:
                pass

    def metrics(self) -> dict[str, float | int | str]:
        values = list(self._jitter)
        base = {"scheduled": self._sequence, "executed_actions": self._executed,
                "real_input_count": self._real_input_count,
                "dropped_stale": self._dropped_stale, "dropped_overflow": self._dropped_overflow,
                "queue_depth": len(self._queue), "last_error": self._last_error}
        if not values:
            return {**base, "jitter_ms_mean": 0.0, "jitter_ms_p95": 0.0}
        ordered = sorted(abs(value) for value in values)
        p95 = ordered[min(len(ordered) - 1, int(round(.95 * (len(ordered) - 1))))]
        return {**base, "jitter_ms_mean": sum(abs(value) for value in values) / len(values),
                "jitter_ms_p95": p95}

    def cancel_pending(self) -> None:
        with self._condition:
            self._generation += 1
            self._queue.clear()
            self._hold_ends.clear()
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._stop = True
            self._generation += 1
            self._queue.clear()
            self._hold_ends.clear()
            self._condition.notify_all()
        self._thread.join(timeout=1.0)


class RhythmController:
    """Adapter -> scheduler gate. Enforces one action per track and stale
    dropping before anything reaches the input scheduler.

    Action kinds:

    * ``tap``  - a single key press (normal targets);
    * ``hold`` - a single long key press (hold targets);
    * ``large``- a rate-limited burst of taps (large targets), hard-capped.
    """

    def __init__(self, adapter, scheduler: InputScheduler, *, dry_run: bool = False,
                 stale_ms: float = 100.0, horizon_ms: float = 1500.0, multi_hit_cap: int = 6,
                 max_frame_age_ms: float | None = None) -> None:
        self.adapter = adapter
        self.scheduler = scheduler
        self.dry_run = bool(dry_run)
        self.stale_ms = float(stale_ms)
        self.horizon_ms = float(horizon_ms)
        self.multi_hit_cap = max(1, int(multi_hit_cap))
        self._scheduled_tracks: set[int] = set()
        self._process_lock = threading.RLock()
        self.max_frame_age_ms = max_frame_age_ms

    def process(self, frame, captured_ns: int | None = None) -> dict:
        with self._process_lock:
            return self._process(frame, captured_ns)

    def _process(self, frame, captured_ns: int | None = None) -> dict:
        captured_ns = captured_ns or now_ns()
        generation = self.scheduler.generation
        result = self.adapter.process(frame, captured_ns)
        now = now_ns()
        if self.max_frame_age_ms is not None and (now - captured_ns) / 1_000_000.0 > self.max_frame_age_ms:
            result["dropped_frame_age"] = True
            result["actions"] = []
            result["scheduler"] = self.scheduler.metrics()
            return result
        dropped_stale = 0
        dropped_far = 0
        for action in result.get("actions", []):
            track_id = action.get("track_id")
            if track_id is not None:
                if track_id in self._scheduled_tracks:
                    continue  # second guard: one schedule per track, ever
                self._scheduled_tracks.add(track_id)
            target_ns = int(action.get("target_ns", 0) or 0)
            delta_ms = (target_ns - now) / 1_000_000.0
            if delta_ms < -self.stale_ms:
                dropped_stale += 1
                continue  # stale hit: discard
            if delta_ms > self.horizon_ms:
                dropped_far += 1
                continue  # unreasonably far future: discard
            self._dispatch_action(action, target_ns, generation)
        for update in result.get("hold_updates", []):
            self.scheduler.extend_hold(int(update["track_id"]), str(update["key"]), int(update["end_ns"]), generation=generation)
        if len(self._scheduled_tracks) > 4096:
            self._scheduled_tracks.clear()  # bound memory across very long runs
        result["dropped_stale"] = dropped_stale
        result["dropped_far"] = dropped_far
        result["scheduler"] = self.scheduler.metrics()
        return result

    def _dispatch_action(self, action: dict, target_ns: int, generation: int | None = None) -> None:
        key = str(action.get("key", ""))
        if not key:
            return
        kind = str(action.get("kind", "tap"))
        duration_ms = float(action.get("duration_ms", 30.0))
        if kind == "hold":
            self.scheduler.schedule_key(key, target_ns, max(duration_ms, 60.0), generation=generation,
                                        track_id=action.get("track_id"))
        elif kind == "large":
            count = min(int(action.get("hit_count", 4)), self.multi_hit_cap)
            interval_ms = float(action.get("hit_interval_ms", 90.0))
            for index in range(max(1, count)):
                self.scheduler.schedule_key(key, int(target_ns + index * interval_ms * 1_000_000.0), duration_ms, generation=generation)
        else:
            self.scheduler.schedule_key(key, target_ns, duration_ms, generation=generation)

    def reset(self) -> None:
        """Clear all track bookkeeping. Called on Rhythm start/stop so stale
        actions from a previous session can never leak into a new one."""
        with self._process_lock:
            self.scheduler.cancel_pending()
            self._scheduled_tracks.clear()
            adapter_reset = getattr(self.adapter, "reset", None)
            if callable(adapter_reset):
                try:
                    adapter_reset()
                except Exception:
                    pass
