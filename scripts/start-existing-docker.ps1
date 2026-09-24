# Start the existing deployment without rebuilding or changing its configuration.
$ErrorActionPreference = 'Stop'
$docker = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'
$desktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
try {
    if (-not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath $desktop -WindowStyle Hidden
    }
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            & $docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $ready = $true
                break
            }
        } catch {
            # Windows PowerShell can surface daemon stderr as a terminating error.
        }
        Start-Sleep -Seconds 5
    }
    if (-not $ready) { throw 'Docker did not become ready within five minutes.' }
    & $docker start video-subtitle-api-1 video-subtitle-web-1 video-subtitle-worker-1
    if ($LASTEXITCODE -ne 0) { throw 'Could not start existing video-subtitle containers.' }
} catch {
    Write-Error $_
    exit 1
}
