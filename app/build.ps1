# Build the Windows app: creates app\.venv if needed, then runs PyInstaller. Output: app\dist\jarvis-panel\jarvis-panel.exe
# Run from PowerShell:  .\build.ps1      (needs Python 3.12 from python.org on PATH)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment. Is Python installed?" }
}
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed." }
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean jarvis-panel.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
Write-Host ""
Write-Host "Built: $PSScriptRoot\dist\jarvis-panel\jarvis-panel.exe"
