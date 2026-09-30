#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace aceye {

// Runtime policy is deliberately independent of model libraries. Later tasks
// can load these values from JSON without changing the worker graph.
struct RuntimeConfig {
    std::uint32_t target_fps = 30;
    std::uint32_t ocr_every_n_frames = 6;
    std::uint32_t segment_every_n_frames = 0; // zero means event/ROI driven
    double reasoner_hz = 1.0;
    // The current hand-off is intentionally a one-slot latest-value queue.
    // This field documents the policy for a future configurable queue.
    std::size_t latest_queue_capacity = 1;

    bool overlay_enabled = true;
    bool text_feedback_enabled = true;
    bool automation_enabled = false;
    bool print_text_feedback = false;

    std::string config_path = "config/aceye.json";
};

} // namespace aceye
