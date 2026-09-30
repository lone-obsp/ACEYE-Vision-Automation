param(
    [ValidateSet("Debug", "Release")][string]$Configuration = "Release",
    [string]$PythonRuntime = ""
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$stageRoot = Join-Path $root "dist\package-stage"
$dist = Join-Path $stageRoot "ACEYE"
# Never remove dist\ACEYE: it may contain the developer .venv and local model
# weights used for real-device validation. Only the disposable staging tree is
# cleaned before creating a portable archive.
if (Test-Path $stageRoot) { Remove-Item -LiteralPath $stageRoot -Recurse -Force }
New-Item -ItemType Directory -Path $dist -Force | Out-Null
& (Join-Path $PSScriptRoot "build_native.ps1") -Configuration $Configuration
Copy-Item (Join-Path $root "backend") $dist -Recurse
Copy-Item (Join-Path $root "frontend") $dist -Recurse
Copy-Item (Join-Path $root "config") $dist -Recurse
Copy-Item (Join-Path $root "scripts\run.cmd") $dist
Copy-Item (Join-Path $root "scripts\run.ps1") $dist
Copy-Item (Join-Path $root "scripts\install_backend.ps1") $dist
Copy-Item (Join-Path $root "backend\requirements.txt") $dist
Copy-Item (Join-Path $root "backend\aceye\adapters\requirements-models.txt") $dist
$nativeBin = Join-Path $root "native\out\bin\$Configuration"
New-Item -ItemType Directory -Path (Join-Path $dist "native") -Force | Out-Null
Copy-Item (Join-Path $nativeBin "ACEYE.exe") $dist
Copy-Item (Join-Path $nativeBin "ACEYECapture.dll") (Join-Path $dist "native") -Force
if ($PythonRuntime) {
    Copy-Item $PythonRuntime (Join-Path $dist "runtime") -Recurse
} else {
    Write-Host "No Python runtime supplied; package requires .venv\Scripts\python.exe or runtime\python.exe." -ForegroundColor Yellow
}
# Source checkouts can contain bytecode from local validation; keep the portable
# package focused on editable source and avoid shipping machine-specific caches.
Get-ChildItem -LiteralPath $dist -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -LiteralPath $dist -Recurse -File -Filter "*.pyc" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue
$readme = @"
ACEYE portable package

Run ACEYE.exe for one-click startup. The editable backend is under backend/ and the UI is under frontend/.
If no embedded runtime is included, run install_backend.ps1 once, then use ACEYE.exe or run.cmd.
Model weights are intentionally not bundled; put local weights under models/ and edit config/aceye.json.
The package launcher never falls back to PATH/uv Python; provide runtime/ or .venv explicitly.
"@
Set-Content -LiteralPath (Join-Path $dist "PACKAGE-README.txt") -Value $readme -Encoding UTF8
$outputs = Join-Path $root "outputs"
New-Item -ItemType Directory -Path $outputs -Force | Out-Null
$archive = Join-Path $outputs ("ACEYE-package-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".zip")
Compress-Archive -Path (Join-Path $dist "*") -DestinationPath $archive -CompressionLevel Optimal
Write-Host "Package created: $archive" -ForegroundColor Green
