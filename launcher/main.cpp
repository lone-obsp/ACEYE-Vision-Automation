#define UNICODE
#define _UNICODE
#define NOMINMAX

#include <Windows.h>
#include <winhttp.h>

#include <filesystem>
#include <string>
#include <vector>

#pragma comment(lib, "winhttp.lib")

namespace {

std::wstring module_directory() {
    std::vector<wchar_t> buffer(MAX_PATH);
    for (;;) {
        const DWORD length = GetModuleFileNameW(nullptr, buffer.data(),
                                                 static_cast<DWORD>(buffer.size()));
        if (length == 0) {
            return {};
        }
        if (length + 1 < buffer.size()) {
            return std::filesystem::path(buffer.data(), buffer.data() + length)
                .parent_path()
                .wstring();
        }
        buffer.resize(buffer.size() * 2);
    }
}

// The launcher is also built into native/out/bin/<config>.  Walk upwards
// until the real installation root is found instead of assuming the
// executable lives beside backend/run.py.
std::filesystem::path find_install_root(const std::filesystem::path& start) {
    std::error_code error;
    auto current = std::filesystem::weakly_canonical(start, error);
    if (error) {
        current = start;
    }
    for (;;) {
        if (std::filesystem::exists(current / L"backend" / L"run.py") &&
            std::filesystem::exists(current / L"frontend")) {
            return current;
        }
        const auto parent = current.parent_path();
        if (parent.empty() || parent == current) {
            break;
        }
        current = parent;
    }
    return {};
}

std::wstring quote(const std::wstring& value) {
    std::wstring result = L"\"";
    for (const wchar_t character : value) {
        if (character == L'\"') {
            result += L"\\\"";
        } else {
            result += character;
        }
    }
    result += L"\"";
    return result;
}

std::wstring find_python(const std::filesystem::path& root) {
    const std::vector<std::filesystem::path> candidates = {
        root / L".venv" / L"Scripts" / L"pythonw.exe",
        root / L".venv" / L"Scripts" / L"python.exe",
        root / L"runtime" / L"pythonw.exe",
        root / L"runtime" / L"python.exe",
    };
    for (const auto& candidate : candidates) {
        if (std::filesystem::exists(candidate)) {
            return candidate.wstring();
        }
    }
    return {};
}

bool wait_for_health() {
    HINTERNET session = WinHttpOpen(L"ACEYE Launcher/1.0",
                                    WINHTTP_ACCESS_TYPE_NO_PROXY,
                                    WINHTTP_NO_PROXY_NAME,
                                    WINHTTP_NO_PROXY_BYPASS, 0);
    if (session == nullptr) {
        return false;
    }
    WinHttpSetTimeouts(session, 500, 500, 500, 500);
    HINTERNET connection = WinHttpConnect(session, L"127.0.0.1", 8765, 0);
    if (connection == nullptr) {
        WinHttpCloseHandle(session);
        return false;
    }
    bool ready = false;
    for (int attempt = 0; attempt < 50 && !ready; ++attempt) {
        HINTERNET request = WinHttpOpenRequest(connection, L"GET", L"/api/health",
                                               nullptr, WINHTTP_NO_REFERER,
                                               WINHTTP_DEFAULT_ACCEPT_TYPES,
                                               0);
        if (request != nullptr) {
            if (WinHttpSendRequest(request, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                                   WINHTTP_NO_REQUEST_DATA, 0, 0, 0) &&
                WinHttpReceiveResponse(request, nullptr)) {
                DWORD status = 0;
                DWORD status_size = sizeof(status);
                if (WinHttpQueryHeaders(request,
                                        WINHTTP_QUERY_STATUS_CODE |
                                            WINHTTP_QUERY_FLAG_NUMBER,
                                        WINHTTP_HEADER_NAME_BY_INDEX, &status,
                                        &status_size, WINHTTP_NO_HEADER_INDEX)) {
                    ready = status >= 200 && status < 300;
                }
            }
            WinHttpCloseHandle(request);
        }
        if (!ready) {
            Sleep(200);
        }
    }
    WinHttpCloseHandle(connection);
    WinHttpCloseHandle(session);
    return ready;
}

int show_error(const std::wstring& message) {
    MessageBoxW(nullptr, message.c_str(), L"ACEYE 启动失败",
                MB_OK | MB_ICONERROR | MB_SETFOREGROUND);
    return 1;
}

} // namespace

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR command_line, int) {
    const std::filesystem::path module_root = module_directory();
    const std::filesystem::path root = find_install_root(module_root);
    if (root.empty()) {
        return show_error(L"无法找到 ACEYE 安装目录。请确认 backend\\run.py 和 frontend 位于同一目录树中。");
    }
    const auto script = root / L"backend" / L"run.py";
    if (!std::filesystem::exists(script)) {
        return show_error(L"找不到 backend\\run.py。请确认后端文件已部署到 ACEYE 目录。");
    }
    const std::wstring python = find_python(root);
    if (python.empty()) {
        return show_error(L"找不到 Python 运行时。请放置 runtime\\pythonw.exe，或创建 .venv 环境。");
    }

    std::wstring command = quote(python) + L" " + quote(script) +
                           L" --desktop --root " + quote(root.wstring());
    if (command_line != nullptr && *command_line != L'\0') {
        command += L" ";
        command += command_line;
    }
    std::vector<wchar_t> mutable_command(command.begin(), command.end());
    mutable_command.push_back(L'\0');

    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    const DWORD flags = CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT;
    if (!CreateProcessW(python.c_str(), mutable_command.data(), nullptr, nullptr,
                        FALSE, flags, nullptr, root.wstring().c_str(), &startup,
                        &process)) {
        const DWORD error = GetLastError();
        return show_error(L"无法启动后端进程。Windows 错误码：" +
                          std::to_wstring(error));
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    // Give the backend a short head start and verify that the HTTP service is
    // alive before handing control to the browser.  The UI bootstrap obtains
    // the session token from the public health endpoint.
    wait_for_health();
    ShellExecuteW(nullptr, L"open", L"http://127.0.0.1:8765/", nullptr,
                  root.wstring().c_str(), SW_SHOWNORMAL);
    return 0;
}
