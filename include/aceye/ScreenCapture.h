#pragma once

#include "aceye/FrameBuffer.h"

#include <cstdint>
#include <memory>
#include <string>

namespace aceye {

// The capture object owns a D3D11 device and a DXGI output duplication. It is
// intentionally a single-thread-affine object: construct, initialize, use,
// and destroy it on the same thread. This is also the thread on which COM is
// initialized by initialize().
struct ScreenCaptureConfig {
    // Outputs are numbered across all hardware adapters in DXGI enumeration
    // order. Output zero is normally the primary monitor.
    std::uint32_t output_index = 0;
};

enum class CaptureStatusCode : std::uint8_t {
    ok,
    timeout,
    not_initialized,
    invalid_argument,
    unsupported,
    com_initialization_failed,
    factory_creation_failed,
    adapter_not_found,
    output_not_found,
    device_creation_failed,
    duplication_creation_failed,
    access_lost,
    device_lost,
    frame_acquire_failed,
    frame_copy_failed,
    out_of_memory,
};

struct CaptureStatus {
    CaptureStatusCode code = CaptureStatusCode::ok;
    // On Windows this contains the originating HRESULT. It is zero for
    // statuses that do not map to an HRESULT (for example, timeout).
    std::int32_t native_code = 0;
    std::string message;

    [[nodiscard]] bool succeeded() const noexcept {
        return code == CaptureStatusCode::ok;
    }
    [[nodiscard]] bool is_timeout() const noexcept {
        return code == CaptureStatusCode::timeout;
    }
};

class ScreenCapture final {
public:
    ScreenCapture();
    ~ScreenCapture();

    ScreenCapture(const ScreenCapture&) = delete;
    ScreenCapture& operator=(const ScreenCapture&) = delete;
    ScreenCapture(ScreenCapture&&) = delete;
    ScreenCapture& operator=(ScreenCapture&&) = delete;

    // Calling initialize more than once resets the previous duplication. The
    // call also initializes COM as MTA on the calling thread; if that thread
    // is already initialized as STA, the existing apartment is respected.
    [[nodiscard]] CaptureStatus initialize(
        const ScreenCaptureConfig& config = {});

    // Releases the duplication, D3D11 and COM resources. It is safe to call
    // repeatedly. shutdown() should be called from the same thread as
    // initialize()/capture().
    void shutdown() noexcept;

    [[nodiscard]] bool initialized() const noexcept;

    // Acquires the newest desktop frame and copies it to a CPU-visible BGRA
    // FrameBuffer. A timeout is normal for a quiet desktop. On timeout or an
    // error, destination is left unchanged. timeout_ms is the DXGI acquire
    // timeout and may be zero for a non-blocking poll.
    [[nodiscard]] CaptureStatus capture(
        FrameBuffer& destination,
        std::uint32_t timeout_ms = 16);

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace aceye

