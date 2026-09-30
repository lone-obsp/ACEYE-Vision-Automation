param([int]$Port = 8765)
$ErrorActionPreference = "Stop"
$root = if (Test-Path (Join-Path $PSScriptRoot "backend")) {
    (Resolve-Path $PSScriptRoot).Path
} else {
    (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = Join-Path $root "runtime\python.exe"
}
if (-not (Test-Path $python)) {
    throw "找不到项目 Python 运行时。请先运行 install_backend.ps1，或提供 runtime\python.exe。"
}
$env:PYTHONPATH = Join-Path $root "backend"
& $python (Join-Path $root "backend\run.py") --root $root --port $Port
