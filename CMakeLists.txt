cmake_minimum_required(VERSION 3.24)

project(ACEYE VERSION 0.1.0 LANGUAGES CXX)

include(CTest)

if(NOT WIN32)
    message(WARNING "ACEYE is designed for Windows; the scaffold still builds on other platforms for smoke testing.")
endif()

add_executable(ACEYE
    src/main.cpp
    src/Architecture.cpp
    src/AceyeRuntime.cpp
    src/ScreenCapture.cpp
)

target_include_directories(ACEYE PRIVATE include)
target_compile_features(ACEYE PRIVATE cxx_std_20)

if(MSVC)
    target_compile_options(ACEYE PRIVATE /W4 /permissive- /utf-8)
    target_compile_definitions(ACEYE PRIVATE NOMINMAX UNICODE _UNICODE)
else()
    target_compile_options(ACEYE PRIVATE -Wall -Wextra -Wpedantic)
endif()

if(WIN32)
    target_link_libraries(ACEYE PRIVATE d3d11 dxgi ole32 user32 gdi32)
endif()

set_target_properties(ACEYE PROPERTIES
    CXX_EXTENSIONS OFF
)

if(BUILD_TESTING)
    add_test(NAME ACEYE.self_test COMMAND ACEYE --self-test)
    add_test(NAME ACEYE.architecture_test COMMAND ACEYE --architecture-test)
endif()
