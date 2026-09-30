param([ValidateSet("Debug", "Release")][string]$Configuration = "Release")
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$cmake = "C:\Program Files\Microsoft Visual Studio\18\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
if (-not (Test-Path $cmake)) { $cmake = "cmake" }
$build = Join-Path $root "native\out"
& $cmake -S (Join-Path $root "native") -B $build -G "Visual Studio 18 2026" -A x64
& $cmake --build $build --config $Configuration --parallel
Write-Host "Native capture/overlay/launcher built under native/out/bin/$Configuration" -ForegroundColor Green
