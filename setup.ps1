$ErrorActionPreference = "Stop"

$pyExe = $null
foreach ($ver in @("-3.12", "-3.11", "-3.10")) {
    try {
        & py $ver --version *> $null
        if ($LASTEXITCODE -eq 0) {
            $pyExe = @("py", $ver)
            break
        }
    } catch {}
}
if (-not $pyExe) {
    $pyExe = @("python")
}

Write-Host "Creating Python virtual environment (.venv) using $($pyExe -join ' ')..."
if ($pyExe.Count -eq 2) {
    & $pyExe[0] $pyExe[1] -m venv .venv
} else {
    & $pyExe[0] -m venv .venv
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Installing dependencies..."
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Setup complete!" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Bind WinUSB to your endoscope (see driver\README_DRIVER.md)."
Write-Host "  2. Run .\run_camera.bat --list to verify detection."
Write-Host "  3. Run .\run_camera.bat to start the camera viewer."
