#pragma once

#include "aceye/FrameBuffer.h"

#include <cstdint>
#include <string>
#include <vector>

namespace aceye {

struct PixelRect {
    std::int32_t x = 0;
    std::int32_t y = 0;
    std::int32_t width = 0;
    std::int32_t height = 0;
};

struct Detection {
    std::int32_t class_id = -1;
    std::string label;
    float confidence = 0.0F;
    PixelRect bounds;
};

struct TrackedObject {
    std::uint64_t track_id = 0;
    Detection detection;
};

struct OcrResult {
    PixelRect region;
    std::string text;
    float confidence = 0.0F;
};

struct MaskResult {
    std::uint64_t object_id = 0;
    // The concrete implementation may replace this with a GPU handle or RLE.
    // Keeping the contract opaque prevents the runtime graph from depending on
    // a particular segmentation library.
    std::vector<std::uint8_t> encoded_mask;
};

struct VisionEvent {
    std::string type;
    std::string description;
    float confidence = 0.0F;
};

struct VisionSnapshot {
    std::uint64_t frame_sequence = 0;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::int32_t desktop_origin_x = 0;
    std::int32_t desktop_origin_y = 0;
    std::vector<Detection> detections;
    std::vector<TrackedObject> tracks;
    std::vector<OcrResult> text;
    std::vector<MaskResult> masks;
    std::vector<VisionEvent> events;
};

enum class OverlayPrimitiveKind {
    rectangle,
    label,
    text,
};

struct OverlayPrimitive {
    OverlayPrimitiveKind kind = OverlayPrimitiveKind::rectangle;
    PixelRect bounds;
    std::string text;
    std::uint32_t color_rgba = 0x00FF00FFU;
    float thickness = 2.0F;
};

struct OverlayFrame {
    std::uint64_t frame_sequence = 0;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::int32_t desktop_origin_x = 0;
    std::int32_t desktop_origin_y = 0;
    std::vector<OverlayPrimitive> primitives;
};

struct ReasoningRequest {
    std::uint64_t frame_sequence = 0;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::int32_t desktop_origin_x = 0;
    std::int32_t desktop_origin_y = 0;
    VisionSnapshot vision;
    // This is the plain-text boundary sent to a VLM/LLM adapter. The native
    // runtime does not require a particular model or transport.
    std::string prompt_text;
};

enum class InputActionKind {
    mouse_move,
    mouse_button_down,
    mouse_button_up,
    key_down,
    key_up,
    text,
    wait,
};

struct InputAction {
    InputActionKind kind = InputActionKind::wait;
    std::int32_t x = 0;
    std::int32_t y = 0;
    std::uint16_t key_code = 0;
    std::uint32_t duration_ms = 0;
    std::string text;
};

struct ReasoningResult {
    std::string scene;
    std::string text_feedback;
    std::string raw_text;
    std::vector<InputAction> actions;
};

struct OutputPacket {
    std::uint64_t frame_sequence = 0;
    OverlayFrame overlay;
    std::string text_feedback;
    std::vector<InputAction> actions;
};

} // namespace aceye
