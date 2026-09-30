#include "aceye/ModuleInterfaces.h"

#include <sstream>

namespace aceye {

ModuleStatus NullDetector::detect(const FrameBuffer& frame, VisionSnapshot& output) {
    output = {};
    output.frame_sequence = frame.sequence;
    output.width = frame.width;
    output.height = frame.height;
    output.desktop_origin_x = frame.desktop_origin_x;
    output.desktop_origin_y = frame.desktop_origin_y;
    return ModuleStatus::success();
}

ModuleStatus NullTracker::update(const VisionSnapshot& input, VisionSnapshot& output) {
    output = input;
    output.tracks.clear();
    output.tracks.reserve(input.detections.size());
    for (std::size_t index = 0; index < input.detections.size(); ++index) {
        output.tracks.push_back(TrackedObject{
            static_cast<std::uint64_t>(index + 1), input.detections[index]});
    }
    return ModuleStatus::success();
}

ModuleStatus NullOcrEngine::read(const FrameBuffer&, VisionSnapshot&) {
    return ModuleStatus::success();
}

ModuleStatus NullSegmenter::segment(const FrameBuffer&, VisionSnapshot&) {
    return ModuleStatus::success();
}

ModuleStatus NullEventEngine::update(VisionSnapshot&) {
    return ModuleStatus::success();
}

ModuleStatus EchoReasoner::understand(const ReasoningRequest& request,
                                      ReasoningResult& result) {
    result = {};
    result.scene = "unclassified";
    std::ostringstream text;
    text << "frame=" << request.frame_sequence
         << "; detections=" << request.vision.detections.size()
         << "; tracks=" << request.vision.tracks.size()
         << "; ocr=" << request.vision.text.size();
    result.text_feedback = text.str();
    result.raw_text = request.prompt_text;
    return ModuleStatus::success();
}

ModuleStatus MemoryOverlaySink::publish(const OverlayFrame& frame) {
    latest_ = frame;
    return ModuleStatus::success();
}

ModuleStatus MemoryTextSink::publish(std::uint64_t frame_sequence,
                                     const std::string& text) {
    sequence_ = frame_sequence;
    latest_ = text;
    return ModuleStatus::success();
}

ModuleStatus DisabledInputSink::submit(const std::vector<InputAction>& actions) {
    if (actions.empty()) {
        return ModuleStatus::success();
    }
    return ModuleStatus::failure(
        "input automation is disabled; set automation_enabled=true and inject a validated sink");
}

} // namespace aceye
