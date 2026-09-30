#include "aceye/AceyeRuntime.h"

#include <chrono>
#include <iostream>
#include <optional>
#include <sstream>
#include <thread>
#include <utility>

namespace aceye {
namespace {

std::string make_prompt(const VisionSnapshot& snapshot) {
    std::ostringstream prompt;
    prompt << "根据屏幕视觉结果输出 JSON。\n"
           << "frame_sequence=" << snapshot.frame_sequence << "\n"
           << "screen=" << snapshot.width << "x" << snapshot.height << "\n"
           << "desktop_origin=" << snapshot.desktop_origin_x << ','
           << snapshot.desktop_origin_y << "\n"
           << "detections=" << snapshot.detections.size() << "\n"
           << "tracks=" << snapshot.tracks.size() << "\n"
           << "ocr_items=" << snapshot.text.size() << "\n"
           << "events=" << snapshot.events.size() << "\n"
           << "return JSON fields scene, event, risk, recommended_action; "
              "validate input actions before execution.";
    return prompt.str();
}

OverlayFrame make_overlay(const VisionSnapshot& snapshot) {
    OverlayFrame overlay;
    overlay.frame_sequence = snapshot.frame_sequence;
    overlay.width = snapshot.width;
    overlay.height = snapshot.height;
    overlay.desktop_origin_x = snapshot.desktop_origin_x;
    overlay.desktop_origin_y = snapshot.desktop_origin_y;
    overlay.primitives.reserve(snapshot.detections.size() + snapshot.text.size());

    for (const auto& detection : snapshot.detections) {
        overlay.primitives.push_back(OverlayPrimitive{
            OverlayPrimitiveKind::rectangle,
            detection.bounds,
            {},
            0x00FF00FFU,
            2.0F});
        overlay.primitives.push_back(OverlayPrimitive{
            OverlayPrimitiveKind::label,
            detection.bounds,
            detection.label,
            0x00FFFFFFU,
            1.0F});
    }
    for (const auto& item : snapshot.text) {
        overlay.primitives.push_back(OverlayPrimitive{
            OverlayPrimitiveKind::text,
            item.region,
            item.text,
            0xFFFF00FFU,
            1.0F});
    }
    return overlay;
}

} // namespace

AceyeRuntime::AceyeRuntime(RuntimeConfig config, RuntimeDependencies dependencies)
    : config_(std::move(config)),
      detector_(std::move(dependencies.detector)),
      tracker_(std::move(dependencies.tracker)),
      ocr_(std::move(dependencies.ocr)),
      segmenter_(std::move(dependencies.segmenter)),
      event_engine_(std::move(dependencies.event_engine)),
      reasoner_(std::move(dependencies.reasoner)),
      overlay_(std::move(dependencies.overlay)),
      text_(std::move(dependencies.text)),
      input_(std::move(dependencies.input)) {
    if (!detector_) detector_ = std::make_unique<NullDetector>();
    if (!tracker_) tracker_ = std::make_unique<NullTracker>();
    if (!ocr_) ocr_ = std::make_unique<NullOcrEngine>();
    if (!segmenter_) segmenter_ = std::make_unique<NullSegmenter>();
    if (!event_engine_) event_engine_ = std::make_unique<NullEventEngine>();
    if (!reasoner_) reasoner_ = std::make_unique<EchoReasoner>();
    if (!overlay_) overlay_ = std::make_unique<MemoryOverlaySink>();
    if (!text_) text_ = std::make_unique<MemoryTextSink>();
    if (!input_) input_ = std::make_unique<DisabledInputSink>();
}

AceyeRuntime::~AceyeRuntime() {
    stop();
}

void AceyeRuntime::start() {
    bool expected = false;
    if (!running_.compare_exchange_strong(expected, true)) {
        return;
    }
    vision_done_.store(false);
    reasoning_done_.store(false);
    vision_thread_ = std::thread(&AceyeRuntime::vision_loop, this);
    reasoning_thread_ = std::thread(&AceyeRuntime::reasoning_loop, this);
    output_thread_ = std::thread(&AceyeRuntime::output_loop, this);
}

void AceyeRuntime::stop() {
    running_.store(false);
    // Each loop drains its latest-value queue after the producer stops. Joining
    // in graph order keeps capture/vision/reasoning/output shutdown explicit.
    if (vision_thread_.joinable()) vision_thread_.join();
    if (reasoning_thread_.joinable()) reasoning_thread_.join();
    if (output_thread_.joinable()) output_thread_.join();
}

void AceyeRuntime::submit_frame(FrameBuffer frame) {
    capture_queue_.publish(std::move(frame));
    ++frames_submitted_;
}

RuntimeMetricsSnapshot AceyeRuntime::metrics() const noexcept {
    return RuntimeMetricsSnapshot{
        frames_submitted_.load(),
        vision_frames_.load(),
        reasoning_frames_.load(),
        output_frames_.load(),
        blocked_actions_.load()};
}

void AceyeRuntime::vision_loop() {
    while (running_.load() || capture_queue_.pending() != 0U) {
        auto frame = capture_queue_.try_consume();
        if (!frame) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            continue;
        }

        VisionSnapshot detected;
        if (!detector_->detect(*frame, detected)) {
            continue;
        }
        VisionSnapshot tracked;
        if (!tracker_->update(detected, tracked)) {
            continue;
        }

        const auto frame_number = tracked.frame_sequence;
        if (config_.ocr_every_n_frames == 0U ||
            frame_number % config_.ocr_every_n_frames == 0U) {
            static_cast<void>(ocr_->read(*frame, tracked));
        }
        if (config_.segment_every_n_frames != 0U &&
            frame_number % config_.segment_every_n_frames == 0U) {
            static_cast<void>(segmenter_->segment(*frame, tracked));
        }
        static_cast<void>(event_engine_->update(tracked));

        ReasoningRequest request;
        request.frame_sequence = tracked.frame_sequence;
        request.width = tracked.width;
        request.height = tracked.height;
        request.desktop_origin_x = tracked.desktop_origin_x;
        request.desktop_origin_y = tracked.desktop_origin_y;
        request.vision = std::move(tracked);
        request.prompt_text = make_prompt(request.vision);
        reasoning_queue_.publish(std::move(request));
        ++vision_frames_;
    }
    vision_done_.store(true);
}

void AceyeRuntime::reasoning_loop() {
    std::optional<ReasoningRequest> pending;
    auto next_allowed = std::chrono::steady_clock::now();
    while (running_.load() || !vision_done_.load() || pending.has_value() ||
           reasoning_queue_.pending() != 0U) {
        if (!pending) {
            pending = reasoning_queue_.try_consume();
        }
        if (!pending) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            continue;
        }

        const auto now = std::chrono::steady_clock::now();
        if (running_.load() && config_.reasoner_hz > 0.0 && now < next_allowed) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            continue;
        }

        ReasoningResult result;
        if (!reasoner_->understand(*pending, result)) {
            pending.reset();
            continue;
        }
        OutputPacket packet;
        packet.frame_sequence = pending->frame_sequence;
        packet.overlay = make_overlay(pending->vision);
        packet.text_feedback = result.text_feedback;
        packet.actions = std::move(result.actions);
        output_queue_.publish(std::move(packet));
        ++reasoning_frames_;
        pending.reset();
        if (config_.reasoner_hz > 0.0) {
            next_allowed = std::chrono::steady_clock::now() +
                           std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                               std::chrono::duration<double>(1.0 / config_.reasoner_hz));
        }
    }
    reasoning_done_.store(true);
}

void AceyeRuntime::output_loop() {
    while (running_.load() || !reasoning_done_.load() || output_queue_.pending() != 0U) {
        auto packet = output_queue_.try_consume();
        if (!packet) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            continue;
        }

        if (config_.overlay_enabled) {
            static_cast<void>(overlay_->publish(packet->overlay));
        }
        if (config_.text_feedback_enabled) {
            static_cast<void>(text_->publish(packet->frame_sequence, packet->text_feedback));
            if (config_.print_text_feedback && !packet->text_feedback.empty()) {
                std::cout << "[ACEYE text frame=" << packet->frame_sequence
                          << "] " << packet->text_feedback << '\n';
            }
        }
        if (config_.automation_enabled) {
            static_cast<void>(input_->submit(packet->actions));
        } else {
            blocked_actions_ += packet->actions.size();
        }
        ++output_frames_;
    }
}

} // namespace aceye
