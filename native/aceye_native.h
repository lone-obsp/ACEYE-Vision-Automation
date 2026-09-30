#pragma once

// Stable C ABI used by the editable Python backend.  The native layer owns
// DXGI/D3D11 resources and the backend owns only the returned byte buffer.

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#  if defined(ACEYE_NATIVE_BUILD)
#    define ACEYE_NATIVE_API __declspec(dllexport)
#  else
#    define ACEYE_NATIVE_API __declspec(dllimport)
#  endif
#else
#  define ACEYE_NATIVE_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct AceyeFrameInfo {
    uint64_t sequence;
    uint32_t width;
    uint32_t height;
    int32_t desktop_origin_x;
    int32_t desktop_origin_y;
    uint32_t payload_bytes;
    int32_t status_code;
    int32_t native_code;
} AceyeFrameInfo;

enum {
    ACEYE_NATIVE_OK = 0,
    ACEYE_NATIVE_TIMEOUT = 1,
    ACEYE_NATIVE_INVALID_ARGUMENT = -1,
    ACEYE_NATIVE_NOT_INITIALIZED = -2,
    ACEYE_NATIVE_BUFFER_TOO_SMALL = -3,
    ACEYE_NATIVE_INTERNAL_ERROR = -4,
};

// Creates and initializes a capture object on the calling thread.  The same
// thread must call ACEYE_Grab and ACEYE_DestroyCapture, matching the
// single-thread-affine DXGI Desktop Duplication API.
ACEYE_NATIVE_API void* ACEYE_CreateCapture(uint32_t output_index,
                                            int32_t* status_code);

// Captures the newest desktop frame into caller-owned BGRA memory.  On a
// timeout or failure the destination buffer is left untouched and
// payload_bytes is zero.  If the buffer is too small, no partial frame is
// copied and payload_bytes contains the required size.
ACEYE_NATIVE_API int32_t ACEYE_Grab(void* handle,
                                    uint8_t* destination,
                                    size_t capacity,
                                    uint32_t timeout_ms,
                                    AceyeFrameInfo* info);

ACEYE_NATIVE_API void ACEYE_DestroyCapture(void* handle);

// Backend-facing aliases kept intentionally tiny so Python ctypes can bind
// them without defining the richer metadata struct above.  Return values are
// 0 for success, 1 for a normal timeout, and a negative value for errors.
ACEYE_NATIVE_API void* ac_capture_create(uint32_t output_index);
ACEYE_NATIVE_API int32_t ac_capture_grab(void* handle,
                                          uint8_t* destination,
                                          size_t capacity,
                                          uint32_t timeout_ms,
                                          uint32_t* width,
                                          uint32_t* height,
                                          int32_t* origin_x,
                                          int32_t* origin_y,
                                          uint64_t* sequence);
ACEYE_NATIVE_API void ac_capture_destroy(void* handle);

typedef struct AceyeOverlayConfig {
    int32_t desktop_origin_x;
    int32_t desktop_origin_y;
    uint32_t width;
    uint32_t height;
    uint8_t opacity;
    uint8_t reserved[3];
} AceyeOverlayConfig;

typedef struct AceyeOverlayBox {
    float x;
    float y;
    float width;
    float height;
    uint32_t argb;
    uint32_t stroke_width;
    wchar_t label[128];
} AceyeOverlayBox;

// Starts a click-through, top-most, no-focus transparent Win32 overlay.  The
// overlay is excluded from Desktop Duplication capture where supported.
ACEYE_NATIVE_API void* ACEYE_OverlayStart(const AceyeOverlayConfig* config);
ACEYE_NATIVE_API int32_t ACEYE_OverlayUpdate(void* handle,
                                              const AceyeOverlayBox* boxes,
                                              uint32_t count);
ACEYE_NATIVE_API void ACEYE_OverlayClear(void* handle);
ACEYE_NATIVE_API void ACEYE_OverlayStop(void* handle);

enum {
    ACEYE_INPUT_MOUSE_MOVE = 1,
    ACEYE_INPUT_MOUSE_LEFT_DOWN = 2,
    ACEYE_INPUT_MOUSE_LEFT_UP = 3,
    ACEYE_INPUT_MOUSE_RIGHT_DOWN = 4,
    ACEYE_INPUT_MOUSE_RIGHT_UP = 5,
    ACEYE_INPUT_KEY_DOWN = 6,
    ACEYE_INPUT_KEY_UP = 7,
    ACEYE_INPUT_TEXT = 8,
};

typedef struct AceyeInputAction {
    uint32_t kind;
    int32_t x;
    int32_t y;
    uint16_t virtual_key;
    uint16_t reserved;
    wchar_t text[128];
} AceyeInputAction;

// Sends a bounded list of mouse/keyboard actions through Win32 SendInput.
// Callers should keep the application-level automation gate disabled until a
// policy layer has explicitly approved the action list.
ACEYE_NATIVE_API int32_t ACEYE_SendInput(const AceyeInputAction* actions,
                                          uint32_t count);

#ifdef __cplusplus
} // extern "C"
#endif
