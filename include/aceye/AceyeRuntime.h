#pragma once

#include "aceye/Config.h"
#include "aceye/LatestFrameQueue.h"
#include "aceye/ModuleInterfaces.h"

#include <atomic>
#include <cstdint>
#include <memory>
#include <string>
#include <thread>

namespace aceye {

struct RuntimeMetricsSnapshot {
    std::uint64_t frames_submitted = 0;
    std::uint64_t vision_frames = 0;
    std::uint64_t reasoning_frames = 0;
    std::uint64_t output_frames = 0;
    std::uint64_t blocked_actions = 0;
};

class AceyeRuntime final {
public:
    explicit AceyeRuntime(RuntimeConfig config = {},
                          RuntimeDependencies dependencies = {});
    ~AceyeRuntime();

    AceyeRuntime(const AceyeRuntime&) = delete;
    AceyeRuntime& operator=(const AceyeRuntime&) = delete;

    void start();
    void stop();
    [[nodiscard]] bool running() const noexcept { return running_.load(); }

    // Capture owns the frame cadence. This hand-off always keeps only the
    // newest pending frame so a slow model cannot build an unbounded backlog.
    void submit_frame(FrameBuffer frame);

    [[nodiscard]] RuntimeMetricsSnapshot metrics() const noexcept;

private:
    void vision_loop();
    void reasoning_loop();
    void output_loop();

    RuntimeConfig config_;
    std::unique_ptr<IDetector> detector_;
    std::unique_ptr<ITracker> tracker_;
    std::unique_ptr<IOcrEngine> ocr_;
    std::unique_ptr<ISegmenter> segmenter_;
    std::unique_ptr<IEventEngine> event_engine_;
    std::unique_ptr<IReasoner> reasoner_;
    std::unique_ptr<IOverlaySink> overlay_;
    std::unique_ptr<ITextSink> text_;
    std::unique_ptr<IInputSink> input_;

    LatestFrameQueue<FrameBuffer> capture_queue_;
    LatestFrameQueue<ReasoningRequest> reasoning_queue_;
    LatestFrameQueue<OutputPacket> output_queue_;

    std::atomic<bool> running_{false};
    std::atomic<bool> vision_done_{true};
    std::atomic<bool> reasoning_done_{true};
    std::atomic<std::uint64_t> frames_submitted_{0};
    std::atomic<std::uint64_t> vision_frames_{0};
    std::atomic<std::uint64_t> reasoning_frames_{0};
    std::atomic<std::uint64_t> output_frames_{0};
    std::atomic<std::uint64_t> blocked_actions_{0};

    std::thread vision_thread_;
    std::thread reasoning_thread_;
    std::thread output_thread_;
};

} // namespace aceye
