param([int]$Port = 5173)
$ErrorActionPreference = 'Stop'
$webRoot = Join-Path (Split-Path $PSScriptRoot -Parent) 'apps\web'
Push-Location $webRoot
try {
    & npm run dev -- --port $Port --strictPort
    if ($LASTEXITCODE -ne 0) { throw 'Web server exited unsuccessfully' }
} finally {
    Pop-Location
}
