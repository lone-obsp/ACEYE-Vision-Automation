$ErrorActionPreference = 'Continue'

Write-Host 'ACEYE toolchain check'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (Test-Path -LiteralPath $vswhere) {
    & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -format json
} else {
    Write-Warning 'vswhere.exe was not found.'
}

foreach ($commandName in @('cmake', 'nvidia-smi', 'nvcc')) {
    $command = Get-Command $commandName -ErrorAction SilentlyContinue
    if ($command) {
        Write-Host ("{0}: {1}" -f $commandName, $command.Source)
    } else {
        if ($commandName -eq 'cmake' -and (Test-Path -LiteralPath 'C:\Program Files\Microsoft Visual Studio\18\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe')) {
            Write-Host 'cmake: Visual Studio bundled CMake'
        } else {
            Write-Warning ("{0}: not found on PATH" -f $commandName)
        }
    }
}

if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    & nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
}
