# Starts the backend (uvicorn) and frontend (npm run dev) together, streaming
# both logs into this console. Stop both with Ctrl+C.
#
# Usage (from the repo root):
#   .\dev.ps1
# If PowerShell blocks the script with an execution-policy error, run it instead as:
#   powershell -ExecutionPolicy Bypass -File .\dev.ps1

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"

$backendPython = Join-Path $backendDir "venv\Scripts\python.exe"
if (-not (Test-Path $backendPython)) {
    $backendPython = "python"
}

# Routed through cmd /c so stdout+stderr merge into plain text at the OS level.
# Without this, Windows PowerShell wraps a native process's stderr lines (which is
# where Python/uvicorn's own logging goes) as ErrorRecord objects that skip the
# "[backend]" prefixing below and print with PowerShell's own error formatting instead.
$backendJob = Start-Job -Name "documentos-backend" -ScriptBlock {
    param($dir, $python)
    Set-Location $dir
    cmd /c "`"$python`" -m uvicorn main:app --reload --port 8000 2>&1"
} -ArgumentList $backendDir, $backendPython

$frontendJob = Start-Job -Name "documentos-frontend" -ScriptBlock {
    param($dir)
    Set-Location $dir
    cmd /c "npm run dev 2>&1"
} -ArgumentList $frontendDir

Write-Host ""
Write-Host "Backend:  http://localhost:8000" -ForegroundColor Cyan
Write-Host "Frontend: http://localhost:3000" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop both." -ForegroundColor Yellow
Write-Host ""

try {
    while ($true) {
        Receive-Job -Job $backendJob | ForEach-Object { Write-Host "[backend]  $_" -ForegroundColor Green }
        Receive-Job -Job $frontendJob | ForEach-Object { Write-Host "[frontend] $_" -ForegroundColor Magenta }

        if ($backendJob.State -ne "Running" -and $frontendJob.State -ne "Running") {
            Write-Host "Both processes exited." -ForegroundColor Yellow
            break
        }

        Start-Sleep -Milliseconds 300
    }
}
finally {
    Write-Host "Stopping backend and frontend..." -ForegroundColor Yellow
    Stop-Job $backendJob, $frontendJob -ErrorAction SilentlyContinue | Out-Null
    Receive-Job $backendJob, $frontendJob -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
    Remove-Job $backendJob, $frontendJob -Force -ErrorAction SilentlyContinue | Out-Null
}
