@echo off
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%backend" set "ROOT=%~dp0.."
if exist "%ROOT%\.venv\Scripts\python.exe" (set "PYTHON=%ROOT%\.venv\Scripts\python.exe") else if exist "%ROOT%\runtime\python.exe" (set "PYTHON=%ROOT%\runtime\python.exe") else (
  echo ACEYE Python runtime not found. Run install_backend.ps1 first or provide runtime\python.exe.
  exit /b 1
)
set "PYTHONPATH=%ROOT%\backend"
"%PYTHON%" "%ROOT%\backend\run.py" --root "%ROOT%" %*
endlocal
