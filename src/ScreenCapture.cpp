#include "aceye/ScreenCapture.h"

#include <chrono>
#include <cstring>
#include <limits>
#include <iterator>
#include <new>
#include <sstream>
#include <thread>
#include <utility>

#if defined(_WIN32)

#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <Windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <wrl/client.h>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "ole32.lib")

namespace aceye {
namespace {

using Microsoft::WRL::ComPtr;

[[nodiscard]] std::string hresult_text(HRESULT hr) {
    std::ostringstream stream;
    stream << "HRESULT 0x" << std::hex << std::uppercase
           << static_cast<unsigned long>(hr);

    char buffer[512]{};
    const DWORD length = FormatMessageA(
        FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
        nullptr,
        static_cast<DWORD>(hr),
        0,
        buffer,
        static_cast<DWORD>(sizeof(buffer)),
        nullptr);
    if (length != 0) {
        std::string text(buffer, buffer + length);
        while (!text.empty() &&
               (text.back() == '\r' || text.back() == '\n' ||
                text.back() == ' ')) {
            text.pop_back();
        }
        stream << " (" << text << ')';
    }
    return stream.str();
}

[[nodiscard]] CaptureStatus status(
    CaptureStatusCode code,
    HRESULT hr = S_OK,
    std::string detail = {}) {
    if (detail.empty()) {
        detail = hresult_text(hr);
    } else if (FAILED(hr)) {
        detail += ": ";
        detail += hresult_text(hr);
    }
    return CaptureStatus{code, static_cast<std::int32_t>(hr), std::move(detail)};
}

[[nodiscard]] CaptureStatus status_without_hresult(
    CaptureStatusCode code,
    std::string message) {
    return CaptureStatus{code, 0, std::move(message)};
}

struct FrameReleaseGuard final {
    IDXGIOutputDuplication* duplication = nullptr;
    bool acquired = false;

    FrameReleaseGuard(IDXGIOutputDuplication* value, bool owns_frame) noexcept
        : duplication(value), acquired(owns_frame) {}

    ~FrameReleaseGuard() {
        if (acquired && duplication != nullptr) {
            duplication->ReleaseFrame();
        }
    }

    FrameReleaseGuard(const FrameReleaseGuard&) = delete;
    FrameReleaseGuard& operator=(const FrameReleaseGuard&) = delete;
};

} // namespace

struct ScreenCapture::Impl final {
    ComPtr<IDXGIFactory1> factory;
    ComPtr<IDXGIAdapter1> adapter;
    ComPtr<IDXGIOutput> output;
    ComPtr<IDXGIOutput1> output1;
    ComPtr<IDXGIOutputDuplication> duplication;
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    ComPtr<ID3D11Texture2D> staging;

    D3D11_TEXTURE2D_DESC staging_desc{};
    std::uint64_t sequence = 0;
    std::uint32_t output_index = 0;
    std::int32_t desktop_origin_x = 0;
    std::int32_t desktop_origin_y = 0;
    std::thread::id owner_thread{};
    bool initialized = false;
    bool com_initialized = false;

    [[nodiscard]] bool on_owner_thread() const noexcept {
        return owner_thread == std::this_thread::get_id();
    }

    void release_graphics() noexcept {
        // Release the context before the device. ComPtr handles null values,
        // and releasing in this order avoids a context retaining resources.
        staging.Reset();
        duplication.Reset();
        output1.Reset();
        output.Reset();
        context.Reset();
        device.Reset();
        adapter.Reset();
        factory.Reset();
        staging_desc = {};
        desktop_origin_x = 0;
        desktop_origin_y = 0;
        initialized = false;
        sequence = 0;
    }

    [[nodiscard]] CaptureStatus create_duplication() {
        duplication.Reset();
        if (!output1) {
            return status_without_hresult(
                CaptureStatusCode::duplication_creation_failed,
                "DXGI output interface is unavailable");
        }

        HRESULT hr = output1->DuplicateOutput(device.Get(), &duplication);
        if (FAILED(hr)) {
            return status(
                CaptureStatusCode::duplication_creation_failed,
                hr,
                "IDXGIOutput1::DuplicateOutput failed");
        }

        DXGI_OUTDUPL_DESC duplication_desc{};
        duplication->GetDesc(&duplication_desc);
        if (duplication_desc.ModeDesc.Format !=
            DXGI_FORMAT_B8G8R8A8_UNORM) {
            duplication.Reset();
            return status_without_hresult(
                CaptureStatusCode::unsupported,
                "Desktop duplication returned a non-BGRA8 format");
        }
        if (duplication_desc.Rotation != DXGI_MODE_ROTATION_IDENTITY &&
            duplication_desc.Rotation != DXGI_MODE_ROTATION_UNSPECIFIED) {
            duplication.Reset();
            return status_without_hresult(
                CaptureStatusCode::unsupported,
                "Rotated desktop outputs are not supported by the first capture path");
        }

        staging_desc = {};
        staging_desc.Width = duplication_desc.ModeDesc.Width;
        staging_desc.Height = duplication_desc.ModeDesc.Height;
        staging_desc.MipLevels = 1;
        staging_desc.ArraySize = 1;
        staging_desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
        staging_desc.SampleDesc.Count = 1;
        staging_desc.Usage = D3D11_USAGE_STAGING;
        staging_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;

        staging.Reset();
        hr = device->CreateTexture2D(&staging_desc, nullptr, &staging);
        if (FAILED(hr)) {
            duplication.Reset();
            return status(
                CaptureStatusCode::device_creation_failed,
                hr,
                "CreateTexture2D for the staging surface failed");
        }
        return status_without_hresult(CaptureStatusCode::ok, {});
    }
};

ScreenCapture::ScreenCapture() : impl_(std::make_unique<Impl>()) {}

ScreenCapture::~ScreenCapture() {
    shutdown();
}

CaptureStatus ScreenCapture::initialize(const ScreenCaptureConfig& config) {
    if (!impl_) {
        return status_without_hresult(
            CaptureStatusCode::out_of_memory,
            "ScreenCapture implementation is unavailable");
    }

    if (impl_->owner_thread != std::thread::id{} && !impl_->on_owner_thread()) {
        return status_without_hresult(
            CaptureStatusCode::invalid_argument,
            "ScreenCapture must be reinitialized on its owner thread");
    }

    shutdown();
    impl_->owner_thread = std::this_thread::get_id();
    impl_->output_index = config.output_index;

    const HRESULT com_hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(com_hr) && com_hr != RPC_E_CHANGED_MODE) {
        return status(
            CaptureStatusCode::com_initialization_failed,
            com_hr,
            "CoInitializeEx failed");
    }
    impl_->com_initialized = SUCCEEDED(com_hr);

    HRESULT hr = CreateDXGIFactory1(IID_PPV_ARGS(&impl_->factory));
    if (FAILED(hr)) {
        shutdown();
        return status(
            CaptureStatusCode::factory_creation_failed,
            hr,
            "CreateDXGIFactory1 failed");
    }

    std::uint32_t remaining_outputs = config.output_index;
    bool found_output = false;
    for (std::uint32_t adapter_index = 0;; ++adapter_index) {
        ComPtr<IDXGIAdapter1> candidate;
        hr = impl_->factory->EnumAdapters1(adapter_index, &candidate);
        if (hr == DXGI_ERROR_NOT_FOUND) {
            break;
        }
        if (FAILED(hr)) {
            shutdown();
            return status(
                CaptureStatusCode::adapter_not_found,
                hr,
                "IDXGIFactory1::EnumAdapters1 failed");
        }

        DXGI_ADAPTER_DESC1 adapter_desc{};
        if (FAILED(candidate->GetDesc1(&adapter_desc)) ||
            (adapter_desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) != 0) {
            continue;
        }

        for (std::uint32_t output_index = 0;; ++output_index) {
            ComPtr<IDXGIOutput> candidate_output;
            hr = candidate->EnumOutputs(output_index, &candidate_output);
            if (hr == DXGI_ERROR_NOT_FOUND) {
                break;
            }
            if (FAILED(hr)) {
                shutdown();
                return status(
                    CaptureStatusCode::output_not_found,
                    hr,
                    "IDXGIAdapter1::EnumOutputs failed");
            }
            DXGI_OUTPUT_DESC candidate_output_desc{};
            if (FAILED(candidate_output->GetDesc(&candidate_output_desc)) ||
                !candidate_output_desc.AttachedToDesktop) {
                continue;
            }
            if (remaining_outputs != 0) {
                --remaining_outputs;
                continue;
            }

            impl_->adapter = std::move(candidate);
            impl_->output = std::move(candidate_output);
            impl_->desktop_origin_x = candidate_output_desc.DesktopCoordinates.left;
            impl_->desktop_origin_y = candidate_output_desc.DesktopCoordinates.top;
            found_output = true;
            break;
        }
        if (found_output) {
            break;
        }
    }

    if (!found_output || !impl_->adapter || !impl_->output) {
        shutdown();
        return status_without_hresult(
            CaptureStatusCode::output_not_found,
            "Requested DXGI output was not found");
    }

    constexpr D3D_FEATURE_LEVEL requested_levels[] = {
        D3D_FEATURE_LEVEL_11_1,
        D3D_FEATURE_LEVEL_11_0,
        D3D_FEATURE_LEVEL_10_1,
        D3D_FEATURE_LEVEL_10_0,
    };
    D3D_FEATURE_LEVEL selected_level{};
    const UINT device_flags = D3D11_CREATE_DEVICE_BGRA_SUPPORT;
    hr = D3D11CreateDevice(
        impl_->adapter.Get(),
        D3D_DRIVER_TYPE_UNKNOWN,
        nullptr,
        device_flags,
        requested_levels,
        static_cast<UINT>(std::size(requested_levels)),
        D3D11_SDK_VERSION,
        &impl_->device,
        &selected_level,
        &impl_->context);
    if (FAILED(hr)) {
        shutdown();
        return status(
            CaptureStatusCode::device_creation_failed,
            hr,
            "D3D11CreateDevice failed");
    }

    hr = impl_->output.As(&impl_->output1);
    if (FAILED(hr)) {
        shutdown();
        return status(
            CaptureStatusCode::duplication_creation_failed,
            hr,
            "QueryInterface for IDXGIOutput1 failed");
    }

    CaptureStatus duplication_status = impl_->create_duplication();
    if (!duplication_status.succeeded()) {
        shutdown();
        return duplication_status;
    }

    impl_->initialized = true;
    return status_without_hresult(CaptureStatusCode::ok, {});
}

void ScreenCapture::shutdown() noexcept {
    if (!impl_) {
        return;
    }

    if (impl_->owner_thread != std::thread::id{} && !impl_->on_owner_thread()) {
        return;
    }

    impl_->release_graphics();
    if (impl_->com_initialized &&
        (impl_->owner_thread == std::thread::id{} ||
         impl_->owner_thread == std::this_thread::get_id())) {
        CoUninitialize();
    }
    impl_->com_initialized = false;
    impl_->owner_thread = {};
}

bool ScreenCapture::initialized() const noexcept {
    return impl_ != nullptr && impl_->initialized;
}

CaptureStatus ScreenCapture::capture(
    FrameBuffer& destination,
    std::uint32_t timeout_ms) {
    if (!impl_ || !impl_->initialized) {
        return status_without_hresult(
            CaptureStatusCode::not_initialized,
            "ScreenCapture has not been initialized");
    }
    if (!impl_->on_owner_thread()) {
        return status_without_hresult(
            CaptureStatusCode::invalid_argument,
            "capture must run on the ScreenCapture owner thread");
    }

    DXGI_OUTDUPL_FRAME_INFO frame_info{};
    ComPtr<IDXGIResource> resource;
    HRESULT hr = impl_->duplication->AcquireNextFrame(
        timeout_ms,
        &frame_info,
        &resource);
    if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
        return status_without_hresult(
            CaptureStatusCode::timeout,
            "No desktop frame became available before the timeout");
    }
    if (hr == DXGI_ERROR_ACCESS_LOST) {
        CaptureStatus recreate_status = impl_->create_duplication();
        if (!recreate_status.succeeded()) {
            impl_->initialized = false;
            return recreate_status;
        }
        return status(
            CaptureStatusCode::access_lost,
            hr,
            "Desktop duplication was reset after access was lost");
    }
    if (hr == DXGI_ERROR_DEVICE_REMOVED || hr == DXGI_ERROR_DEVICE_RESET) {
        impl_->initialized = false;
        return status(
            CaptureStatusCode::device_lost,
            hr,
            "D3D11 device was removed or reset");
    }
    if (FAILED(hr)) {
        if (hr == DXGI_ERROR_DEVICE_REMOVED || hr == DXGI_ERROR_DEVICE_RESET) {
            impl_->initialized = false;
            return status(
                CaptureStatusCode::device_lost,
                hr,
                "D3D11 device was removed or reset while mapping the frame");
        }
        return status(
            CaptureStatusCode::frame_acquire_failed,
            hr,
            "IDXGIOutputDuplication::AcquireNextFrame failed");
    }

    FrameReleaseGuard release_guard{impl_->duplication.Get(), true};
    if (!resource) {
        return status_without_hresult(
            CaptureStatusCode::timeout,
            "Desktop duplication returned a frame without an image resource");
    }
    ComPtr<ID3D11Texture2D> source_texture;
    hr = resource.As(&source_texture);
    if (FAILED(hr) || !source_texture) {
        return status(
            CaptureStatusCode::frame_copy_failed,
            hr,
            "Captured resource is not an ID3D11Texture2D");
    }

    D3D11_TEXTURE2D_DESC source_desc{};
    source_texture->GetDesc(&source_desc);
    if (source_desc.Format != DXGI_FORMAT_B8G8R8A8_UNORM ||
        source_desc.Width != impl_->staging_desc.Width ||
        source_desc.Height != impl_->staging_desc.Height) {
        return status_without_hresult(
            CaptureStatusCode::unsupported,
            "Captured frame dimensions or format changed unexpectedly");
    }

    impl_->context->CopyResource(impl_->staging.Get(), source_texture.Get());
    D3D11_MAPPED_SUBRESOURCE mapped{};
    hr = impl_->context->Map(
        impl_->staging.Get(),
        0,
        D3D11_MAP_READ,
        0,
        &mapped);
    if (FAILED(hr)) {
        if (hr == DXGI_ERROR_DEVICE_REMOVED || hr == DXGI_ERROR_DEVICE_RESET) {
            impl_->initialized = false;
            return status(
                CaptureStatusCode::device_lost,
                hr,
                "D3D11 device was removed or reset while mapping the frame");
        }
        return status(
            CaptureStatusCode::frame_copy_failed,
            hr,
            "ID3D11DeviceContext::Map failed");
    }

    const std::size_t row_bytes =
        static_cast<std::size_t>(impl_->staging_desc.Width) * 4U;
    const std::size_t total_bytes =
        row_bytes * static_cast<std::size_t>(impl_->staging_desc.Height);
    FrameBuffer candidate;
    try {
        candidate.sequence = ++impl_->sequence;
        candidate.width = impl_->staging_desc.Width;
        candidate.height = impl_->staging_desc.Height;
        candidate.desktop_origin_x = impl_->desktop_origin_x;
        candidate.desktop_origin_y = impl_->desktop_origin_y;
        candidate.captured_at = FrameBuffer::Clock::now();
        candidate.bgra.resize(total_bytes);
        const auto* source = static_cast<const std::uint8_t*>(mapped.pData);
        for (std::uint32_t row = 0; row < candidate.height; ++row) {
            std::memcpy(
                candidate.bgra.data() +
                    static_cast<std::size_t>(row) * row_bytes,
                source + static_cast<std::size_t>(row) * mapped.RowPitch,
                row_bytes);
        }
    } catch (const std::bad_alloc&) {
        impl_->context->Unmap(impl_->staging.Get(), 0);
        return status_without_hresult(
            CaptureStatusCode::out_of_memory,
            "Unable to allocate CPU frame storage");
    }
    impl_->context->Unmap(impl_->staging.Get(), 0);

    const HRESULT release_hr = impl_->duplication->ReleaseFrame();
    release_guard.acquired = false;
    if (FAILED(release_hr)) {
        return status(
            CaptureStatusCode::frame_acquire_failed,
            release_hr,
            "IDXGIOutputDuplication::ReleaseFrame failed");
    }

    destination = std::move(candidate);
    return status_without_hresult(CaptureStatusCode::ok, {});
}

} // namespace aceye

#else

namespace aceye {

struct ScreenCapture::Impl {};

ScreenCapture::ScreenCapture() : impl_(std::make_unique<Impl>()) {}
ScreenCapture::~ScreenCapture() = default;

CaptureStatus ScreenCapture::initialize(const ScreenCaptureConfig&) {
    return CaptureStatus{
        CaptureStatusCode::unsupported,
        0,
        "DXGI Desktop Duplication is available only on Windows"};
}

void ScreenCapture::shutdown() noexcept {}
bool ScreenCapture::initialized() const noexcept { return false; }

CaptureStatus ScreenCapture::capture(FrameBuffer&, std::uint32_t) {
    return CaptureStatus{
        CaptureStatusCode::unsupported,
        0,
        "DXGI Desktop Duplication is available only on Windows"};
}

} // namespace aceye

#endif
