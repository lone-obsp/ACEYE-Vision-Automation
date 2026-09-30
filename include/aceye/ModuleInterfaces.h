#pragma once

#include "aceye/FrameBuffer.h"
#include "aceye/RuntimeTypes.h"

#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace aceye {

struct ModuleStatus {
    bool ok = true;
    std::string message;

    [[nodiscard]] explicit operator bool() const noexcept { return ok; }

    static ModuleStatus success() { return {true, {}}; }
    static ModuleStatus failure(std::string reason) {
        return {false, std::move(reason)};
    }
};

class IFrameSource {
public:
    virtual ~IFrameSource() = default;
    // The source owns its capture-thread affinity. A DXGI adapter should be
    // created, used, and destroyed on the same worker thread.
    virtual ModuleStatus capture(FrameBuffer& frame, std::uint32_t timeout_ms) = 0;
};

class IDetector {
public:
    virtual ~IDetector() = default;
    virtual ModuleStatus detect(const FrameBuffer& frame, VisionSnapshot& output) = 0;
};

class ITracker {
public:
    virtual ~ITracker() = default;
    virtual ModuleStatus update(const VisionSnapshot& input, VisionSnapshot& output) = 0;
};

class IOcrEngine {
public:
    virtual ~IOcrEngine() = default;
    virtual ModuleStatus read(const FrameBuffer& frame, VisionSnapshot& snapshot) = 0;
};

class ISegmenter {
public:
    virtual ~ISegmenter() = default;
    virtual ModuleStatus segment(const FrameBuffer& frame, VisionSnapshot& snapshot) = 0;
};

class IEventEngine {
public:
    virtual ~IEventEngine() = default;
    virtual ModuleStatus update(VisionSnapshot& snapshot) = 0;
};

class IReasoner {
public:
    virtual ~IReasoner() = default;
    virtual ModuleStatus understand(const ReasoningRequest& request,
                                    ReasoningResult& result) = 0;
};

class IOverlaySink {
public:
    virtual ~IOverlaySink() = default;
    // A later implementation can render this through a transparent layered
    // Win32 overlay without creating a video preview window. An empty frame
    // means that stale primitives must be cleared.
    virtual ModuleStatus publish(const OverlayFrame& frame) = 0;
};

class ITextSink {
public:
    virtual ~ITextSink() = default;
    virtual ModuleStatus publish(std::uint64_t frame_sequence,
                                 const std::string& text) = 0;
};

class IInputSink {
public:
    virtual ~IInputSink() = default;
    virtual ModuleStatus submit(const std::vector<InputAction>& actions) = 0;
};

// Dependency-injection bundle. Each member can be replaced by a real
// TensorRT, PaddleOCR, SAM, VLM, overlay, or SendInput adapter later.
struct RuntimeDependencies {
    std::unique_ptr<IDetector> detector;
    std::unique_ptr<ITracker> tracker;
    std::unique_ptr<IOcrEngine> ocr;
    std::unique_ptr<ISegmenter> segmenter;
    std::unique_ptr<IEventEngine> event_engine;
    std::unique_ptr<IReasoner> reasoner;
    std::unique_ptr<IOverlaySink> overlay;
    std::unique_ptr<ITextSink> text;
    std::unique_ptr<IInputSink> input;
};

// Buildable placeholders keep the complete graph testable before third-party
// model runtimes are installed.
class NullDetector final : public IDetector {
public:
    ModuleStatus detect(const FrameBuffer& frame, VisionSnapshot& output) override;
};

class NullTracker final : public ITracker {
public:
    ModuleStatus update(const VisionSnapshot& input, VisionSnapshot& output) override;
};

class NullOcrEngine final : public IOcrEngine {
public:
    ModuleStatus read(const FrameBuffer&, VisionSnapshot&) override;
};

class NullSegmenter final : public ISegmenter {
public:
    ModuleStatus segment(const FrameBuffer&, VisionSnapshot&) override;
};

class NullEventEngine final : public IEventEngine {
public:
    ModuleStatus update(VisionSnapshot&) override;
};

class EchoReasoner final : public IReasoner {
public:
    ModuleStatus understand(const ReasoningRequest& request,
                            ReasoningResult& result) override;
};

class MemoryOverlaySink final : public IOverlaySink {
public:
    ModuleStatus publish(const OverlayFrame& frame) override;
    [[nodiscard]] const OverlayFrame& latest() const noexcept { return latest_; }

private:
    OverlayFrame latest_;
};

class MemoryTextSink final : public ITextSink {
public:
    ModuleStatus publish(std::uint64_t frame_sequence,
                         const std::string& text) override;
    [[nodiscard]] std::uint64_t latest_sequence() const noexcept { return sequence_; }
    [[nodiscard]] const std::string& latest() const noexcept { return latest_; }

private:
    std::uint64_t sequence_ = 0;
    std::string latest_;
};

class DisabledInputSink final : public IInputSink {
public:
    ModuleStatus submit(const std::vector<InputAction>& actions) override;
};

} // namespace aceye
