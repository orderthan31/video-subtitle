param([switch]$CollectOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$env:PYTHONPATH = "$projectRoot\packages\shared;$projectRoot\workers\media"
Push-Location $projectRoot
try {
    $pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = 'python' }
    if ($CollectOnly) {
        & $pythonPath -m media_worker.worker --collect-only
    } else {
        & $pythonPath -m media_worker.worker
    }
} finally {
    Pop-Location
}
