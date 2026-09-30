# ACEYE

ACEYE 是 Windows 原生实时屏幕视觉系统。当前版本包含可编辑 Python 后端、原生 DXGI/Overlay/SendInput 桥、无截图预览的中文交互控制台，以及 YOLO、OCR、SAM 和 Qwen3-VL 的懒加载适配器。模型权重由用户放入 `models/`，便于后续微调和替换。

## 在 Visual Studio 中打开

用 Visual Studio 2026 打开 `ACEYE.sln`，选择 `Debug | x64` 或 `Release | x64`。解决方案包含原生 `ACEYE.vcxproj`，同一目录也提供 `CMakeLists.txt` 和 `CMakePresets.json`。

Visual Studio Installer 需要安装 **使用 C++ 的桌面开发**、MSVC v145 工具集和 Windows 10/11 SDK。若缺少 C++ 构建工具，先按 `SETUP.md` 检查环境。

也可以使用 CMake：

```powershell
cmake --preset windows-vs2026
cmake --build --preset windows-vs2026-debug
```

## 运行和自检

```powershell
./out/build/windows-vs2026/Debug/ACEYE.exe --self-test
./out/build/windows-vs2026/Debug/ACEYE.exe --architecture-test
```

自检不需要 CUDA 或模型文件，会验证有限的最新帧队列和运行图的捕获、视觉、理解、输出边界。捕获阶段可以单独做一帧 DXGI 烟雾测试：

```powershell
./out/build/windows-vs2026/Debug/ACEYE.exe --capture-test 1
```

ACEYE 不打开实时预览窗口。运行时输出通过原生透明、点击穿透的屏幕 Overlay 和文本/模型通道发布；Overlay 只绘制结构化结果，不把整帧截图塞进前端。

## 一键运行和可编辑后端

```powershell
.\scripts\install_backend.ps1
.\scripts\run.ps1
```

或者构建后直接运行 `native/out/bin/Release/ACEYE.exe`。启动器会启动 `backend/run.py` 并打开控制台页面。后端源码在 `backend/`，页面在 `frontend/`，配置在 `config/aceye.json`；修改模型适配器或流水线策略不需要重新编译启动器。

安装可选模型运行时：

```powershell
.\scripts\install_backend.ps1 -Models
```

此命令只安装 Python 依赖，不隐式下载权重。把本地权重放到 `models/` 后，可在 UI 的“模块管线”中准备和检查模型。

## 自动化输入

视觉结果可被格式化为纯文本 `ReasoningRequest`，交给 VLM/LLM 生成结构化事件和候选鼠标、键盘动作。动作通过原生 `SendInput` 桥发送，配置中的 `automation.enabled` 默认是 `false` 且 `automation.dry_run` 默认是 `true`；只有同时显式打开自动化并关闭 dry-run 才会向系统注入输入。候选动作仍会先经过类型、范围和长度校验。

## 架构文档

完整的线程模型、队列策略、消息契约和输出通道见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。设计重点是“永远处理最新帧”：实时视频路径使用容量很小的最新值队列，理解层异步运行，落后帧直接丢弃而不是堆积延迟。

## 目录

```text
ACEYE/
  CMakeLists.txt              # Visual Studio/CMake 入口
  CMakePresets.json           # Windows Visual Studio 预设
  ACEYE.sln                  # Visual Studio 解决方案
  ACEYE.vcxproj              # 原生 x64 C++ 项目
  ARCHITECTURE.md             # 运行图、契约和线程边界
  SETUP.md                    # 工具链安装与检查
  tools/check_environment.ps1 # 本机工具链/GPU 检查
  include/aceye/              # 公共消息类型、接口和运行时声明
  src/                        # 原生入口与运行时实现
  backend/                    # 可编辑 Python 服务、流水线和模型适配器
  frontend/                   # 无构建步骤的交互式控制台
  native/                     # DXGI、透明 Overlay、SendInput 和启动器
  scripts/                    # 一键运行、环境安装和打包脚本
  config/                     # 默认运行配置
  third_party/README.md       # 计划接入的外部依赖
```

后续模块任务只需实现对应接口并注入运行图：先接入 TensorRT/YOLO，再接入 OCR、SAM、VLM/LLM、Overlay 和受控输入后端，不需要改动捕获和线程编排。
