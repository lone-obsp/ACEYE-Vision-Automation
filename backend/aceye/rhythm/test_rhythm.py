from __future__ import annotations

import time
import unittest


class RhythmContractTests(unittest.TestCase):
    def test_muse_dash_emits_timestamped_dry_run_action(self) -> None:
        import numpy as np

        from .controller import InputScheduler, RhythmController
        from .muse_dash import MuseDashAdapter

        frame = np.zeros((240, 400, 3), dtype=np.uint8)
        frame[100:130, 50:90] = 255
        scheduler = InputScheduler(dry_run=True)
        try:
            result = RhythmController(MuseDashAdapter({"lanes": 4}), scheduler).process(frame)
            self.assertGreaterEqual(len(result["actions"]), 1)
            self.assertIn("target_ns", result["actions"][0])
            self.assertTrue(result["scheduler"]["queue_depth"] >= 1)
        finally:
            scheduler.close()

    def test_zero_sized_roi_means_full_frame(self) -> None:
        import numpy as np

        from .detector import RhythmDetector

        frame = np.zeros((240, 400, 3), dtype=np.uint8)
        frame[100:130, 50:90] = 255
        notes, _ = RhythmDetector().detect(frame, roi=(0, 0, 0, 0))
        self.assertGreaterEqual(len(notes), 1)

    def test_scheduler_dry_run_does_not_call_sink(self) -> None:
        called: list[str] = []
        from .controller import InputScheduler

        scheduler = InputScheduler(lambda key, duration: called.append(key), dry_run=True)
        try:
            scheduler.schedule_key("left", time.perf_counter_ns() + 2_000_000, 20)
            time.sleep(0.02)
            self.assertEqual(called, [])
            self.assertEqual(scheduler.metrics()["scheduled"], 1)
        finally:
            scheduler.close()

    def test_scheduler_live_mode_reports_real_input_count(self) -> None:
        called: list[str] = []
        from .controller import InputScheduler

        scheduler = InputScheduler(lambda key, duration: called.append(key), dry_run=False)
        try:
            scheduler.schedule_key("left", time.perf_counter_ns() + 2_000_000, 20)
            time.sleep(0.02)
            self.assertEqual(called, ["left"])
            self.assertEqual(scheduler.metrics()["real_input_count"], 1)
        finally:
            scheduler.close()

    def test_scheduler_emits_debug_log_for_real_input(self) -> None:
        logs: list[str] = []
        from .controller import InputScheduler

        scheduler = InputScheduler(
            lambda key, duration: None,
            dry_run=False,
            logger=lambda level, message: logs.append(message),
        )
        try:
            scheduler.schedule_key("right", time.perf_counter_ns() + 2_000_000, 20)
            time.sleep(0.02)
            self.assertTrue(any(message.startswith("scheduled: key=right") for message in logs))
            self.assertEqual(scheduler.metrics()["real_input_count"], 1)
        finally:
            scheduler.close()


if __name__ == "__main__":
    unittest.main()
