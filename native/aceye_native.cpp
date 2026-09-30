#include "aceye_native.h"

#include "aceye/FrameBuffer.h"
#include "aceye/ScreenCapture.h"

#include <algorithm>
#include <condition_variable>
#include <cwchar>
#include <mutex>
#include <new>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#if defined(_WIN32)
#  define NOMINMAX
#  include <Windows.h>
#endif

namespace {

struct CaptureHandle final {
    aceye::ScreenCapture capture;
    aceye::FrameBuffer frame;
};

void clear_info(AceyeFrameInfo* info) noexcept {
    if (info != nullptr) {
        *info = AceyeFrameInfo{};
    }
}

void set_status(AceyeFrameInfo* info,
                int32_t status,
                int32_t native_code = 0) noexcept {
    if (info != nullptr) {
        info->status_code = status;
        info->native_code = native_code;
    }
}

#if defined(_WIN32)

struct OverlayBoxInternal {
    RECT rect{};
    COLORREF color = RGB(0, 255, 0);
    UINT stroke = 2;
    std::wstring label;
};

struct OverlayHandle final {
    AceyeOverlayConfig config{};
    std::mutex mutex;
    std::condition_variable ready;
    std::vector<OverlayBoxInternal> boxes;
    std::thread thread;
    DWORD thread_id = 0;
    HWND window = nullptr;
    bool initialized = false;
    bool exited = false;
};

constexpr UINT kOverlayRepaint = WM_APP + 17;
constexpr wchar_t kOverlayClass[] = L"ACEYE.TransparentOverlay";

COLORREF argb_to_color(uint32_t argb) noexcept {
    return RGB((argb >> 16) & 0xffU, (argb >> 8) & 0xffU, argb & 0xffU);
}

LRESULT CALLBACK overlay_window_proc(HWND window,
                                     UINT message,
                                     WPARAM wparam,
                                     LPARAM lparam) {
    auto* handle = reinterpret_cast<OverlayHandle*>(
        GetWindowLongPtrW(window, GWLP_USERDATA));
    if (message == WM_NCCREATE) {
        const auto* create = reinterpret_cast<CREATESTRUCTW*>(lparam);
        handle = static_cast<OverlayHandle*>(create->lpCreateParams);
        SetWindowLongPtrW(window, GWLP_USERDATA,
                          reinterpret_cast<LONG_PTR>(handle));
        return TRUE;
    }
    if (message == kOverlayRepaint) {
        InvalidateRect(window, nullptr, FALSE);
        return 0;
    }
    if (message == WM_ERASEBKGND) {
        return 1;
    }
    if (message == WM_PAINT) {
        PAINTSTRUCT paint{};
        HDC dc = BeginPaint(window, &paint);
        RECT client{};
        GetClientRect(window, &client);

        // Black is the color key and therefore transparent.  The window is
        // layered so only the vector-like overlay strokes remain visible.
        HBRUSH background = CreateSolidBrush(RGB(0, 0, 0));
        FillRect(dc, &client, background);
        DeleteObject(background);

        std::vector<OverlayBoxInternal> boxes;
        if (handle != nullptr) {
            std::lock_guard lock(handle->mutex);
            boxes = handle->boxes;
        }
        SetBkMode(dc, TRANSPARENT);
        for (const auto& box : boxes) {
            HPEN pen = CreatePen(PS_SOLID,
                                 static_cast<int>(std::max<UINT>(1, box.stroke)),
                                 box.color);
            HGDIOBJ old_pen = SelectObject(dc, pen);
            HGDIOBJ old_brush = SelectObject(dc, GetStockObject(HOLLOW_BRUSH));
            Rectangle(dc, box.rect.left, box.rect.top, box.rect.right,
                      box.rect.bottom);
            SelectObject(dc, old_brush);
            SelectObject(dc, old_pen);
            DeleteObject(pen);
            if (!box.label.empty()) {
                SetTextColor(dc, box.color);
                TextOutW(dc, box.rect.left + 2, box.rect.top + 2,
                         box.label.c_str(), static_cast<int>(box.label.size()));
            }
        }
        EndPaint(window, &paint);
        return 0;
    }
    if (message == WM_NCHITTEST) {
        return HTTRANSPARENT;
    }
    if (message == WM_MOUSEACTIVATE) {
        return MA_NOACTIVATE;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

void overlay_thread_proc(OverlayHandle* handle) {
    handle->thread_id = GetCurrentThreadId();
    HINSTANCE module = GetModuleHandleW(nullptr);
    WNDCLASSEXW klass{};
    klass.cbSize = sizeof(klass);
    klass.hInstance = module;
    klass.lpfnWndProc = overlay_window_proc;
    klass.lpszClassName = kOverlayClass;
    klass.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    klass.hbrBackground = static_cast<HBRUSH>(GetStockObject(BLACK_BRUSH));
    RegisterClassExW(&klass);

    const DWORD ex_style = WS_EX_LAYERED | WS_EX_TRANSPARENT |
                           WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW;
    const DWORD style = WS_POPUP;
    const int width = static_cast<int>(handle->config.width);
    const int height = static_cast<int>(handle->config.height);
    HWND window = CreateWindowExW(
        ex_style, kOverlayClass, L"ACEYE Overlay", style,
        handle->config.desktop_origin_x, handle->config.desktop_origin_y,
        width, height, nullptr, nullptr, module, handle);
    if (window == nullptr) {
        {
            std::lock_guard lock(handle->mutex);
            handle->exited = true;
        }
        handle->ready.notify_one();
        return;
    }

    handle->window = window;
    SetLayeredWindowAttributes(window, RGB(0, 0, 0), 0, LWA_COLORKEY);
    SetWindowPos(window, HWND_TOPMOST, handle->config.desktop_origin_x,
                 handle->config.desktop_origin_y, width, height,
                 SWP_NOACTIVATE | SWP_SHOWWINDOW);
#ifndef WDA_EXCLUDEFROMCAPTURE
#  define WDA_EXCLUDEFROMCAPTURE 0x00000011
#endif
    SetWindowDisplayAffinity(window, WDA_EXCLUDEFROMCAPTURE);
    ShowWindow(window, SW_SHOWNOACTIVATE);

    {
        std::lock_guard lock(handle->mutex);
        handle->initialized = true;
    }
    handle->ready.notify_one();

    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    DestroyWindow(window);
    UnregisterClassW(kOverlayClass, module);
    {
        std::lock_guard lock(handle->mutex);
        handle->window = nullptr;
        handle->exited = true;
    }
    handle->ready.notify_one();
}

bool copy_overlay_box(const AceyeOverlayBox& source,
                      const AceyeOverlayConfig& config,
                      OverlayBoxInternal& target) {
    if (!(source.width >= 0.0f && source.height >= 0.0f)) {
        return false;
    }
    target.rect.left = static_cast<LONG>(source.x);
    target.rect.top = static_cast<LONG>(source.y);
    target.rect.right = static_cast<LONG>(source.x + source.width);
    target.rect.bottom = static_cast<LONG>(source.y + source.height);
    target.rect.left = std::clamp<LONG>(target.rect.left, 0,
                                        static_cast<LONG>(config.width));
    target.rect.top = std::clamp<LONG>(target.rect.top, 0,
                                       static_cast<LONG>(config.height));
    target.rect.right = std::clamp<LONG>(target.rect.right, 0,
                                         static_cast<LONG>(config.width));
    target.rect.bottom = std::clamp<LONG>(target.rect.bottom, 0,
                                          static_cast<LONG>(config.height));
    target.color = argb_to_color(source.argb);
    target.stroke = source.stroke_width == 0 ? 2 : source.stroke_width;
    target.label.assign(source.label,
                        source.label +
                            std::min<std::size_t>(128, wcsnlen(source.label, 128)));
    return true;
}

#endif // _WIN32

} // namespace

extern "C" ACEYE_NATIVE_API void* ACEYE_CreateCapture(
    uint32_t output_index,
    int32_t* status_code) {
    if (status_code != nullptr) {
        *status_code = ACEYE_NATIVE_INTERNAL_ERROR;
    }
    auto* handle = new (std::nothrow) CaptureHandle{};
    if (handle == nullptr) {
        return nullptr;
    }
    const aceye::CaptureStatus status =
        handle->capture.initialize(aceye::ScreenCaptureConfig{output_index});
    if (!status.succeeded()) {
        if (status_code != nullptr) {
            *status_code = static_cast<int32_t>(status.code);
        }
        delete handle;
        return nullptr;
    }
    if (status_code != nullptr) {
        *status_code = ACEYE_NATIVE_OK;
    }
    return handle;
}

extern "C" ACEYE_NATIVE_API int32_t ACEYE_Grab(
    void* opaque_handle,
    uint8_t* destination,
    size_t capacity,
    uint32_t timeout_ms,
    AceyeFrameInfo* info) {
    clear_info(info);
    if (opaque_handle == nullptr || info == nullptr) {
        set_status(info, ACEYE_NATIVE_INVALID_ARGUMENT);
        return ACEYE_NATIVE_INVALID_ARGUMENT;
    }
    auto* handle = static_cast<CaptureHandle*>(opaque_handle);
    const aceye::CaptureStatus status = handle->capture.capture(
        handle->frame, timeout_ms);
    if (status.is_timeout()) {
        set_status(info, ACEYE_NATIVE_TIMEOUT, status.native_code);
        return ACEYE_NATIVE_TIMEOUT;
    }
    if (!status.succeeded()) {
        set_status(info, static_cast<int32_t>(status.code), status.native_code);
        return static_cast<int32_t>(status.code);
    }
    const size_t bytes = handle->frame.bgra.size();
    info->sequence = handle->frame.sequence;
    info->width = handle->frame.width;
    info->height = handle->frame.height;
    info->desktop_origin_x = handle->frame.desktop_origin_x;
    info->desktop_origin_y = handle->frame.desktop_origin_y;
    info->payload_bytes = static_cast<uint32_t>(bytes);
    if (destination == nullptr || capacity < bytes) {
        info->status_code = ACEYE_NATIVE_BUFFER_TOO_SMALL;
        return ACEYE_NATIVE_BUFFER_TOO_SMALL;
    }
    std::copy(handle->frame.bgra.begin(), handle->frame.bgra.end(), destination);
    info->status_code = ACEYE_NATIVE_OK;
    return ACEYE_NATIVE_OK;
}

extern "C" ACEYE_NATIVE_API void ACEYE_DestroyCapture(void* opaque_handle) {
    delete static_cast<CaptureHandle*>(opaque_handle);
}

extern "C" ACEYE_NATIVE_API void* ac_capture_create(uint32_t output_index) {
    int32_t status = ACEYE_NATIVE_INTERNAL_ERROR;
    return ACEYE_CreateCapture(output_index, &status);
}

extern "C" ACEYE_NATIVE_API int32_t ac_capture_grab(
    void* opaque_handle,
    uint8_t* destination,
    size_t capacity,
    uint32_t timeout_ms,
    uint32_t* width,
    uint32_t* height,
    int32_t* origin_x,
    int32_t* origin_y,
    uint64_t* sequence) {
    AceyeFrameInfo info{};
    const int32_t status = ACEYE_Grab(opaque_handle, destination, capacity,
                                       timeout_ms, &info);
    if (width != nullptr) {
        *width = info.width;
    }
    if (height != nullptr) {
        *height = info.height;
    }
    if (origin_x != nullptr) {
        *origin_x = info.desktop_origin_x;
    }
    if (origin_y != nullptr) {
        *origin_y = info.desktop_origin_y;
    }
    if (sequence != nullptr) {
        *sequence = info.sequence;
    }
    return status == ACEYE_NATIVE_OK
               ? 0
               : (status == ACEYE_NATIVE_TIMEOUT ? 1 : status);
}

extern "C" ACEYE_NATIVE_API void ac_capture_destroy(void* opaque_handle) {
    ACEYE_DestroyCapture(opaque_handle);
}

#if defined(_WIN32)

extern "C" ACEYE_NATIVE_API void* ACEYE_OverlayStart(
    const AceyeOverlayConfig* config) {
    if (config == nullptr || config->width == 0 || config->height == 0) {
        return nullptr;
    }
    auto* handle = new (std::nothrow) OverlayHandle{};
    if (handle == nullptr) {
        return nullptr;
    }
    handle->config = *config;
    try {
        handle->thread = std::thread(overlay_thread_proc, handle);
    } catch (...) {
        delete handle;
        return nullptr;
    }
    {
        std::unique_lock lock(handle->mutex);
        handle->ready.wait(lock, [&] {
            return handle->initialized || handle->exited;
        });
    }
    if (!handle->initialized) {
        if (handle->thread.joinable()) {
            handle->thread.join();
        }
        delete handle;
        return nullptr;
    }
    return handle;
}

extern "C" ACEYE_NATIVE_API int32_t ACEYE_OverlayUpdate(
    void* opaque_handle,
    const AceyeOverlayBox* boxes,
    uint32_t count) {
    if (opaque_handle == nullptr || (count != 0 && boxes == nullptr)) {
        return ACEYE_NATIVE_INVALID_ARGUMENT;
    }
    if (count > 4096) {
        return ACEYE_NATIVE_INVALID_ARGUMENT;
    }
    auto* handle = static_cast<OverlayHandle*>(opaque_handle);
    std::vector<OverlayBoxInternal> next;
    next.reserve(count);
    for (uint32_t index = 0; index < count; ++index) {
        OverlayBoxInternal value;
        if (!copy_overlay_box(boxes[index], handle->config, value)) {
            return ACEYE_NATIVE_INVALID_ARGUMENT;
        }
        next.push_back(std::move(value));
    }
    {
        std::lock_guard lock(handle->mutex);
        if (handle->window == nullptr) {
            return ACEYE_NATIVE_INTERNAL_ERROR;
        }
        handle->boxes = std::move(next);
        PostMessageW(handle->window, kOverlayRepaint, 0, 0);
    }
    return ACEYE_NATIVE_OK;
}

extern "C" ACEYE_NATIVE_API void ACEYE_OverlayClear(void* opaque_handle) {
    auto* handle = static_cast<OverlayHandle*>(opaque_handle);
    if (handle == nullptr) {
        return;
    }
    std::lock_guard lock(handle->mutex);
    handle->boxes.clear();
    if (handle->window != nullptr) {
        PostMessageW(handle->window, kOverlayRepaint, 0, 0);
    }
}

extern "C" ACEYE_NATIVE_API void ACEYE_OverlayStop(void* opaque_handle) {
    auto* handle = static_cast<OverlayHandle*>(opaque_handle);
    if (handle == nullptr) {
        return;
    }
    if (handle->thread_id != 0) {
        PostThreadMessageW(handle->thread_id, WM_QUIT, 0, 0);
    }
    if (handle->thread.joinable()) {
        handle->thread.join();
    }
    delete handle;
}

extern "C" ACEYE_NATIVE_API int32_t ACEYE_SendInput(
    const AceyeInputAction* actions,
    uint32_t count) {
    if (count != 0 && actions == nullptr) {
        return ACEYE_NATIVE_INVALID_ARGUMENT;
    }
    if (count > 256) {
        return ACEYE_NATIVE_INVALID_ARGUMENT;
    }
    std::vector<INPUT> inputs;
    inputs.reserve(count * 2U);
    const int virtual_width = GetSystemMetrics(SM_CXVIRTUALSCREEN);
    const int virtual_height = GetSystemMetrics(SM_CYVIRTUALSCREEN);
    const int virtual_left = GetSystemMetrics(SM_XVIRTUALSCREEN);
    const int virtual_top = GetSystemMetrics(SM_YVIRTUALSCREEN);
    for (uint32_t index = 0; index < count; ++index) {
        const auto& action = actions[index];
        INPUT input{};
        input.type = INPUT_MOUSE;
        switch (action.kind) {
        case ACEYE_INPUT_MOUSE_MOVE:
            input.mi.dx = virtual_width == 0 ? 0
                                             : (action.x - virtual_left) * 65535 /
                                                   std::max(1, virtual_width - 1);
            input.mi.dy = virtual_height == 0 ? 0
                                              : (action.y - virtual_top) * 65535 /
                                                    std::max(1, virtual_height - 1);
            input.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE |
                               MOUSEEVENTF_VIRTUALDESK;
            inputs.push_back(input);
            break;
        case ACEYE_INPUT_MOUSE_LEFT_DOWN:
            input.mi.dwFlags = MOUSEEVENTF_LEFTDOWN;
            inputs.push_back(input);
            break;
        case ACEYE_INPUT_MOUSE_LEFT_UP:
            input.mi.dwFlags = MOUSEEVENTF_LEFTUP;
            inputs.push_back(input);
            break;
        case ACEYE_INPUT_MOUSE_RIGHT_DOWN:
            input.mi.dwFlags = MOUSEEVENTF_RIGHTDOWN;
            inputs.push_back(input);
            break;
        case ACEYE_INPUT_MOUSE_RIGHT_UP:
            input.mi.dwFlags = MOUSEEVENTF_RIGHTUP;
            inputs.push_back(input);
            break;
        case ACEYE_INPUT_KEY_DOWN:
        case ACEYE_INPUT_KEY_UP:
            input.type = INPUT_KEYBOARD;
            input.ki.wVk = action.virtual_key;
            input.ki.dwFlags = action.kind == ACEYE_INPUT_KEY_UP
                                   ? KEYEVENTF_KEYUP
                                   : 0;
            inputs.push_back(input);
            break;
        case ACEYE_INPUT_TEXT:
            for (std::size_t text_index = 0; text_index < 128; ++text_index) {
                const wchar_t character = action.text[text_index];
                if (character == L'\0') {
                    break;
                }
                INPUT down{};
                down.type = INPUT_KEYBOARD;
                down.ki.dwFlags = KEYEVENTF_UNICODE;
                down.ki.wScan = static_cast<WORD>(character);
                INPUT up = down;
                up.ki.dwFlags |= KEYEVENTF_KEYUP;
                inputs.push_back(down);
                inputs.push_back(up);
            }
            break;
        default:
            return ACEYE_NATIVE_INVALID_ARGUMENT;
        }
    }
    if (inputs.empty()) {
        return ACEYE_NATIVE_OK;
    }
    const UINT sent = SendInput(static_cast<UINT>(inputs.size()), inputs.data(),
                                 sizeof(INPUT));
    return sent == inputs.size() ? ACEYE_NATIVE_OK : ACEYE_NATIVE_INTERNAL_ERROR;
}

#else

extern "C" ACEYE_NATIVE_API void* ACEYE_OverlayStart(
    const AceyeOverlayConfig*) {
    return nullptr;
}
extern "C" ACEYE_NATIVE_API int32_t ACEYE_OverlayUpdate(
    void*, const AceyeOverlayBox*, uint32_t) {
    return ACEYE_NATIVE_INTERNAL_ERROR;
}
extern "C" ACEYE_NATIVE_API void ACEYE_OverlayClear(void*) {}
extern "C" ACEYE_NATIVE_API void ACEYE_OverlayStop(void*) {}
extern "C" ACEYE_NATIVE_API int32_t ACEYE_SendInput(
    const AceyeInputAction*, uint32_t) {
    return ACEYE_NATIVE_INTERNAL_ERROR;
}

#endif
