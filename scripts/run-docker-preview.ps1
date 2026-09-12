param(
    [ValidateSet('start', 'build', 'status', 'logs', 'check')]
    [string]$Action = 'status',
    [string]$StorageRoot = '',
    [int]$WebPort = 5177,
    [string]$WebOrigin = '',
    [ValidateSet('cpu', 'cuda')]
    [string]$VadDevice = 'cpu'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
if (-not $StorageRoot) { $StorageRoot = Join-Path $projectRoot 'data\user-preview\jobs' }
$dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
$docker = if ($dockerCommand) { $dockerCommand.Source } else { Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe' }
if (-not (Test-Path -LiteralPath $docker)) { throw 'Install and start Docker Desktop first.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env'))) { throw 'Missing root .env; configure your Gemini key first.' }
if ($WebPort -lt 1 -or $WebPort -gt 65535) { throw 'Invalid web port.' }
if ($WebOrigin -and $WebOrigin -notmatch '^https?://[^/]+$') { throw 'WebOrigin must be an HTTP(S) origin without a trailing slash.' }
if ($Action -eq 'start') {
    $native = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'media_worker\.worker|uvicorn app\.main:app'
    }
    if ($native) { throw 'Stop native API/worker processes before sharing their job directory with Docker.' }
}
$values = @{
    # Development preview must remain locked even when .env enables paid calls.
    PAID_LLM_ENABLED = 'false'
    VIDEO_HOST_JOBS_DIR = (Resolve-Path -LiteralPath $StorageRoot).Path
    DOCKER_WEB_PORT = "$WebPort"
    NVIDIA_VAD_DEVICE = $VadDevice
    WEB_CORS_ORIGINS = ((@("http://localhost:$WebPort", "http://127.0.0.1:$WebPort", $WebOrigin) | Where-Object { $_ }) -join ',')
}
$previous = @{}
foreach ($key in $values.Keys) {
    $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
    [Environment]::SetEnvironmentVariable($key, $values[$key], 'Process')
}
Push-Location $projectRoot
try {
    $compose = @('compose', '--env-file', '.env', '-f', 'docker-compose.yml', '-f', 'docker-compose.vad.yml',
        '-f', 'docker-compose.gpu.yml', '-f', 'docker-compose.host-data.yml')
    if ($VadDevice -eq 'cuda') { $compose += @('-f', 'docker-compose.vad-gpu.yml') }
    $compose += @('-f', 'docker-compose.preview.yml')
    $operation = @(switch ($Action) {
        'start' { @('up', '-d', '--no-build', '--wait') }
        'build' { @('build') }
        'status' { @('ps') }
        'logs' { @('logs', '--tail=100', 'worker') }
        'check' { @('exec', '-T', 'worker', 'python', 'scripts/check-container.py', '--gpu') }
    })
    & $docker @compose @operation
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
    foreach ($key in $previous.Keys) { [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process') }
}
