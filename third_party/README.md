# Planned runtime dependencies

The core target has no package-manager dependency. Add the GPU stack in stages so each component can be benchmarked independently:

1. Windows DXGI Desktop Duplication (capture; implemented in `ScreenCapture.cpp`, Windows SDK).
2. TensorRT + CUDA and an exported YOLO26n-seg engine (real-time detection).
3. ByteTrack implementation (tracking).
4. PaddleOCR (ROI-triggered OCR).
5. SAM 2.1 Tiny (on-demand masks).
6. Qwen3-VL-2B 4-bit (event-triggered scene understanding).

Keep heavyweight runtime setup outside the core CMake target until the first capture/inference benchmark establishes the memory budget for the RTX 5060 8 GB card.
