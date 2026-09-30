cmake_minimum_required(VERSION 3.24)
project(ACEYE_NATIVE VERSION 0.1.0 LANGUAGES CXX)

if(NOT WIN32)
    message(FATAL_ERROR "ACEYE native integration currently targets Windows")
endif()

set(ACEYE_ROOT "${CMAKE_CURRENT_LIST_DIR}/..")

add_library(ACEYECapture SHARED
    aceye_native.cpp
    "${ACEYE_ROOT}/src/ScreenCapture.cpp"
)
target_include_directories(ACEYECapture PRIVATE
    "${CMAKE_CURRENT_LIST_DIR}"
    "${ACEYE_ROOT}/include"
)
target_compile_features(ACEYECapture PRIVATE cxx_std_20)
target_compile_definitions(ACEYECapture PRIVATE
    ACEYE_NATIVE_BUILD
    NOMINMAX
    UNICODE
    _UNICODE
)
target_compile_options(ACEYECapture PRIVATE /W4 /permissive- /utf-8)
target_link_libraries(ACEYECapture PRIVATE
    d3d11
    dxgi
    ole32
    user32
    gdi32
)
set_target_properties(ACEYECapture PROPERTIES
    OUTPUT_NAME "ACEYECapture"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib"
    ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib"
)

add_executable(ACEYELauncher WIN32
    "${CMAKE_CURRENT_LIST_DIR}/../launcher/main.cpp"
)
target_compile_features(ACEYELauncher PRIVATE cxx_std_20)
target_compile_definitions(ACEYELauncher PRIVATE
    UNICODE
    _UNICODE
    NOMINMAX
)
target_compile_options(ACEYELauncher PRIVATE /W4 /permissive- /utf-8)
target_link_libraries(ACEYELauncher PRIVATE user32 shell32)
set_target_properties(ACEYELauncher PROPERTIES
    OUTPUT_NAME "ACEYE"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)
