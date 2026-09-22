# Runs the full local test suite: backend pytest (against a disposable Postgres
# test container, started automatically if it isn't already) and the frontend
# Playwright e2e suite. See CLAUDE.md's "Testing" section.
#
# Usage (from the repo root):
#   .\test.ps1
# If PowerShell blocks the script with an execution-policy error, run it instead as:
#   powershell -ExecutionPolicy Bypass -File .\test.ps1

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"

$containerName = "documentos-test-pg"
$testDbPort = if ($env:TEST_DB_PORT) { $env:TEST_DB_PORT } else { "15432" }

docker inspect $containerName *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Starting a disposable Postgres test container ($containerName) on port $testDbPort..." -ForegroundColor Cyan
    docker run -d --name $containerName `
        -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=documentos_test `
        -p "${testDbPort}:5432" postgres:16-alpine | Out-Null
    Start-Sleep -Seconds 3
}
else {
    $running = docker inspect -f '{{.State.Running}}' $containerName
    if ($running -ne "true") {
        Write-Host "Starting existing test Postgres container ($containerName)..." -ForegroundColor Cyan
        docker start $containerName | Out-Null
        Start-Sleep -Seconds 2
    }
}

$backendPython = Join-Path $backendDir "venv\Scripts\python.exe"
if (-not (Test-Path $backendPython)) {
    $backendPython = "python"
}

Write-Host ""
Write-Host "=== Backend tests (pytest) ===" -ForegroundColor Cyan
Push-Location $backendDir
try {
    $env:TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost:${testDbPort}/documentos_test"
    & $backendPython -m pytest
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "=== Frontend e2e tests (Playwright) ===" -ForegroundColor Cyan
Push-Location $frontendDir
try {
    npx playwright test
    if ($LASTEXITCODE -ne 0) { throw "Frontend e2e tests failed." }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "All tests passed." -ForegroundColor Green
