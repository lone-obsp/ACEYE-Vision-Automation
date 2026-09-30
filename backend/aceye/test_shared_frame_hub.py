from __future__ import annotations

import time
import unittest


class SharedFrameHubTests(unittest.TestCase):
    def test_latest_frame_is_fanned_out_without_consuming_it(self) -> None:
        import numpy as np

        from .capture import SharedFrameHub

        hub = SharedFrameHub()
        packet = {"frame": np.zeros((4, 5, 3), dtype=np.uint8), "sequence": 7}
        hub.publish(packet)
        first = hub.wait_next(0, timeout=0.05)
        second = hub.wait_next(0, timeout=0.05)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first["hub_sequence"], second["hub_sequence"])
        self.assertEqual(hub.snapshot()["published"], 1)

    def test_wait_next_returns_newest_sequence(self) -> None:
        from .capture import SharedFrameHub

        hub = SharedFrameHub()
        hub.publish({"frame": b"a"})
        first = hub.wait_next(0, timeout=0.05)
        self.assertIsNotNone(first)
        self.assertIsNone(hub.wait_next(first["hub_sequence"], timeout=0.01))
        hub.publish({"frame": b"b"})
        second = hub.wait_next(first["hub_sequence"], timeout=0.05)
        self.assertIsNotNone(second)
        self.assertGreater(second["hub_sequence"], first["hub_sequence"])


if __name__ == "__main__":
    unittest.main()
