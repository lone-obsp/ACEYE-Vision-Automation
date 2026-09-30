param(
    [switch]$Models,
    [switch]$Ocr,
    [switch]$OcrGpu,
    [switch]$Sam,
    [switch]$Vlm,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$root = if (Test-Path (Join-Path $PSScriptRoot "backend")) {
    (Resolve-Path $PSScriptRoot).Path
} else {
    (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
$venv = Join-Path $root ".venv"
$pythonExe = Join-Path $venv "Scripts\python.exe"

function Write-Result([string]$Name, [bool]$Ok, [string]$Message = "") {
    $mark = if ($Ok) { "OK" } else { "FAILED" }
    $color = if ($Ok) { "Green" } else { "Yellow" }
    Write-Host ("{0,-14} {1,-8} {2}" -f $Name, $mark, $Message) -ForegroundColor $color
}

function Invoke-Pip([string]$Name, [string[]]$Arguments) {
    Write-Host "`n[$Name] $($Arguments -join ' ')" -ForegroundColor Cyan
    & $pythonExe -m pip @Arguments
    $ok = ($LASTEXITCODE -eq 0)
    Write-Result $Name $ok $(if ($ok) { "" } else { "保留其它模块；请查看上方 pip 输出" })
    return $ok
}

function Test-Import([string]$Name, [string]$ImportName) {
    & $pythonExe -c "import $ImportName" *> $null
    $ok = ($LASTEXITCODE -eq 0)
    Write-Result $Name $ok $(if ($ok) { "import $ImportName" } else { "缺少 $ImportName" })
    return $ok
}

if (-not (Test-Path $pythonExe)) {
    if (-not $Python) {
        foreach ($candidate in @("py -3.11", "py -3.12", "python")) {
            try {
                & cmd /c "$candidate --version" *> $null
                if ($LASTEXITCODE -eq 0) { $Python = $candidate; break }
            } catch {}
        }
    }
    if (-not $Python) { throw "找不到用于创建 .venv 的 Python 3.11/3.12。" }
    Write-Host "创建项目虚拟环境：$venv" -ForegroundColor Cyan
    & cmd /c "$Python -m venv `"$venv`""
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $pythonExe)) { throw "创建项目 .venv 失败。" }
}

Write-Host "ACEYE 模块化运行时安装器" -ForegroundColor White
Write-Host "root: $root"
Write-Host "python: $pythonExe"
& $pythonExe --version
Invoke-Pip "pip" @("install", "--upgrade", "pip") | Out-Null

$baseOk = Invoke-Pip "Base" @("install", "-r", (Join-Path $root "backend\requirements.txt"))
$torchOk = Test-Import "CUDA Torch" "torch"
$yoloOk = Test-Import "YOLO" "ultralytics"

if ($Models) {
    if (-not $torchOk) {
        Invoke-Pip "Torch CUDA" @("install", "torch", "torchvision", "torchaudio", "--index-url", "https://download.pytorch.org/whl/cu128") | Out-Null
        $torchOk = Test-Import "CUDA Torch" "torch"
    }
    if (-not $yoloOk) {
        Invoke-Pip "YOLO" @("install", "-r", (Join-Path $root "backend\aceye\adapters\requirements-models.txt")) | Out-Null
        $yoloOk = Test-Import "YOLO" "ultralytics"
    }
    Invoke-Pip "VLM runtime" @("install", "transformers", "accelerate", "bitsandbytes") | Out-Null
}

if ($Ocr -or $Models) {
    $ocrReq = if ($OcrGpu) { "requirements-ocr-gpu.txt" } else { "requirements-ocr-cpu.txt" }
    $ocrPath = Join-Path $root "backend\aceye\adapters\$ocrReq"
    $ocrOk = Invoke-Pip $(if ($OcrGpu) { "OCR GPU" } else { "OCR CPU" }) @("install", "-r", $ocrPath)
    if (-not $ocrOk -and $OcrGpu) {
        Write-Host "Windows RTX 50 Paddle GPU wheel不可用，自动尝试CPU fallback。" -ForegroundColor Yellow
        $ocrOk = Invoke-Pip "OCR CPU fallback" @("install", "-r", (Join-Path $root "backend\aceye\adapters\requirements-ocr-cpu.txt"))
    }
    Test-Import "OCR runtime" "paddleocr" | Out-Null
}

if ($Sam) {
    Write-Result "SAM" $false "仅创建按需适配器；请把SAM2.1 Tiny权重放入 models/ 后单独准备"
}
if ($Vlm) {
    Test-Import "VLM runtime" "transformers" | Out-Null
}

Write-Host "`n安装报告（模型权重不会静默下载）" -ForegroundColor White
Write-Result "Base" $baseOk
Write-Result "CUDA Torch" $torchOk
Write-Result "YOLO" $yoloOk
if (-not ($Ocr -or $Models)) { Write-Result "OCR" $false "未请求；可用 -Ocr 或 -Models" }
Write-Host "完成。所有 pip 均显式使用项目 .venv；可选模块失败不会回滚已成功模块。" -ForegroundColor Green
