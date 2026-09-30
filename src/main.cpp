#include "aceye/AceyeRuntime.h"
#include "aceye/LatestFrameQueue.h"
#include "aceye/ScreenCapture.h"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <exception>
#include <iostream>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

namespace {

int run_self_test() {
    aceye::LatestFrameQueue<std::uint64_t> queue;
    queue.publish(1);
    queue.publish(2);
    const auto value = queue.try_consume();
    if (!value || *value != 2 || queue.pending() != 0) {
        std::cerr << "LatestFrameQueue self-test failed\n";
        return 1;
    }

    aceye::AceyeRuntime runtime;
    runtime.start();
    for (std::uint64_t sequence = 1; sequence <= 3; ++sequence) {
        aceye::FrameBuffer frame;
        frame.sequence = sequence;
        frame.width = 1920;
        frame.height = 1080;
        frame.captured_at = aceye::FrameBuffer::Clock::now();
        frame.bgra.resize(4);
        runtime.submit_frame(std::move(frame));
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    runtime.stop();

    std::cout << "ACEYE self-test passed (vision="
              << runtime.metrics().vision_frames << ", output="
              << runtime.metrics().output_frames << ")\n";
    return 0;
}

int run_architecture_test() {
    aceye::RuntimeConfig config;
    config.automation_enabled = false;
    aceye::AceyeRuntime runtime(config);
    runtime.start();
    for (std::uint64_t sequence = 1; sequence <= 3; ++sequence) {
        aceye::FrameBuffer frame;
        frame.sequence = sequence;
        frame.width = 1920;
        frame.height = 1080;
        frame.captured_at = aceye::FrameBuffer::Clock::now();
        frame.bgra.resize(4);
        runtime.submit_frame(std::move(frame));
    }
    runtime.stop();
    const auto metrics = runtime.metrics();
    aceye::DisabledInputSink disabled_input;
    const std::vector<aceye::InputAction> sample_action{
        aceye::InputAction{aceye::InputActionKind::key_down, 0, 0, 0x1B, 0, {}}};
    if (metrics.frames_submitted == 0 || metrics.vision_frames == 0 ||
        metrics.reasoning_frames == 0 || metrics.output_frames == 0 ||
        disabled_input.submit(sample_action).ok) {
        std::cerr << "ACEYE architecture test failed: graph did not drain\n";
        return 1;
    }
    std::cout << "ACEYE architecture test passed (vision=" << metrics.vision_frames
              << ", reasoning=" << metrics.reasoning_frames
              << ", output=" << metrics.output_frames
              << ", automation=disabled)\n";
    return 0;
}

int run_capture_test(int requested_frames) {
#ifndef _WIN32
    static_cast<void>(requested_frames);
    std::cerr << "DXGI capture test is only available on Windows\n";
    return 1;
#else
    aceye::ScreenCapture capture;
    const auto initialize_status = capture.initialize();
    if (!initialize_status.succeeded()) {
        std::cerr << "Screen capture initialization failed: "
                  << initialize_status.message << "\n";
        return 1;
    }

    aceye::AceyeRuntime runtime;
    runtime.start();
    const auto started_at = aceye::FrameBuffer::Clock::now();
    const auto deadline = started_at + std::chrono::seconds(10);
    int captured_frames = 0;
    std::uint32_t captured_width = 0;
    std::uint32_t captured_height = 0;
    while (captured_frames < requested_frames &&
           aceye::FrameBuffer::Clock::now() < deadline) {
        aceye::FrameBuffer frame;
        const auto capture_status = capture.capture(frame, 100);
        if (!capture_status.succeeded()) {
            if (capture_status.code == aceye::CaptureStatusCode::access_lost) {
                continue;
            }
            if (!capture_status.is_timeout()) {
                std::cerr << "Screen capture failed: " << capture_status.message << "\n";
                runtime.stop();
                capture.shutdown();
                return 1;
            }
            if (aceye::FrameBuffer::Clock::now() >= deadline) {
                break;
            }
            continue;
        }
        captured_width = frame.width;
        captured_height = frame.height;
        runtime.submit_frame(std::move(frame));
        ++captured_frames;
        if (aceye::FrameBuffer::Clock::now() >= deadline) {
            break;
        }
    }
    // Let the asynchronous consumer observe the last published frame before
    // the short benchmark tears the pipeline down.
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    runtime.stop();
    const auto elapsed = std::chrono::duration<double>(
        aceye::FrameBuffer::Clock::now() - started_at).count();
    capture.shutdown();

    const double fps = elapsed > 0.0 ? static_cast<double>(captured_frames) / elapsed : 0.0;
    if (captured_frames == 0) {
        std::cerr << "ACEYE capture test received no desktop frames within 10 seconds\n";
        return 1;
    }
    std::cout << "ACEYE capture test received " << captured_frames << " frames, "
              << captured_width << "x" << captured_height << ", "
              << fps << " FPS, graph vision=" << runtime.metrics().vision_frames
              << ", reasoning=" << runtime.metrics().reasoning_frames
              << ", output=" << runtime.metrics().output_frames << "\n";
    return 0;
#endif
}

} // namespace

int main(int argc, char** argv) {
    if (argc > 1 && std::string_view(argv[1]) == "--self-test") {
        return run_self_test();
    }
    if (argc > 1 && std::string_view(argv[1]) == "--architecture-test") {
        return run_architecture_test();
    }
    if (argc > 1 && std::string_view(argv[1]) == "--capture-test") {
        int requested_frames = 120;
        if (argc > 2) {
            try {
                requested_frames = std::max(1, std::stoi(argv[2]));
            } catch (const std::exception&) {
                std::cerr << "Usage: ACEYE --capture-test [frame_count]\n";
                return 2;
            }
        }
        return run_capture_test(requested_frames);
    }
    std::cout << "ACEYE 0.1.0\n"
              << "Usage: ACEYE --self-test\n"
              << "       ACEYE --architecture-test\n"
              << "       ACEYE --capture-test [frame_count]\n";
    return 0;
}
