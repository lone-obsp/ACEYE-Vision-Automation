#pragma once

#include <chrono>
#include <cstdint>
#include <vector>

namespace aceye {

// CPU-visible frame metadata. The first DXGI implementation may wrap a GPU
// texture, but consumers should depend on this small hand-off type instead of
// the capture API.
struct FrameBuffer {
    using Clock = std::chrono::steady_clock;

    std::uint64_t sequence = 0;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    // Desktop-space origin is required when an overlay spans multiple
    // monitors. It is populated by the capture adapter when available.
    std::int32_t desktop_origin_x = 0;
    std::int32_t desktop_origin_y = 0;
    Clock::time_point captured_at{};
    std::vector<std::uint8_t> bgra;

    [[nodiscard]] bool empty() const noexcept {
        return width == 0 || height == 0 || bgra.empty();
    }
};

} // namespace aceye
