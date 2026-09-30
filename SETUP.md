# ACEYE 环境准备

ACEYE 需要 Visual Studio 2026 的原生 C++ 工具链。若 Visual Studio Installer 中还没有这些组件，请对 Community 2026 选择“修改”，勾选：

- 使用 C++ 的桌面开发
- MSVC v145 x64/x86 生成工具
- Windows 10/11 SDK
- CMake tools for Windows（使用 CMake 工作流时需要）

安装完成后重新打开 `ACEYE.sln`，选择 `Debug | x64`，按 `Ctrl+Shift+B` 生成。也可以在 ACEYE 目录中运行：

```powershell
cmake --preset windows-vs2026
cmake --build --preset windows-vs2026-debug
.\out\build\windows-vs2026\Debug\ACEYE.exe --self-test
.\out\build\windows-vs2026\Debug\ACEYE.exe --architecture-test
.\out\build\windows-vs2026\Debug\ACEYE.exe --capture-test 1
```

如果只想先验证 Visual Studio 组件，可以运行：

```powershell
vswhere.exe -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64
```

命令返回安装实例后，才具备编译这个原生 C++ 项目的工具链。当前架构包含 DXGI 屏幕捕获、最新帧队列和模块化运行图；CUDA、TensorRT、YOLO、OCR 和 VLM 依赖会由后续模块任务接入。ACEYE 不启动实时预览窗口，运行结果通过透明 Overlay 和文本通道输出。
