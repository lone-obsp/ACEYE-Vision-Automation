param(
    [ValidateSet("capture", "yolo", "pipeline")]
    [string]$Benchmark = "pipeline",
    [double]$Seconds = 5,
    [int]$Warmup = 3
)

$ErrorActionPreference = "Stop"
$root = if (Test-Path (Join-Path $PSScriptRoot "backend")) {
    (Resolve-Path $PSScriptRoot).Path
} else {
    (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = Join-Path $root "runtime\python.exe"
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "找不到项目 Python 运行时：$root\.venv\Scripts\python.exe"
}
$flag = switch ($Benchmark) {
    "capture" { "--benchmark-capture" }
    "yolo" { "--benchmark-yolo" }
    default { "--benchmark-pipeline" }
}
$env:PYTHONPATH = Join-Path $root "backend"
& $python (Join-Path $root "backend\benchmark.py") --root $root --seconds $Seconds --warmup $Warmup $flag
exit $LASTEXITCODE
