$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$toolRoot = Join-Path $projectRoot '.tools'
New-Item -ItemType Directory -Force -Path $toolRoot | Out-Null
$archive = Join-Path $toolRoot 'ffmpeg-release-essentials.zip'
$url = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
$expected = ((Invoke-WebRequest "$url.sha256").Content.Trim() -split '\s+')[0]
if (-not (Test-Path -LiteralPath $archive)) {
    Invoke-WebRequest $url -OutFile $archive
}
$actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
if ($actual -ne $expected) { throw 'FFmpeg archive checksum mismatch' }
Expand-Archive -LiteralPath $archive -DestinationPath $toolRoot -Force
$binary = Get-ChildItem -LiteralPath $toolRoot -Filter ffmpeg.exe -Recurse | Select-Object -First 1
if (-not $binary) { throw 'FFmpeg executable missing' }
$env:FFMPEG_PATH = $binary.FullName
$env:FFPROBE_PATH = Join-Path $binary.Directory.FullName 'ffprobe.exe'
& $env:FFMPEG_PATH -version | Select-Object -First 1
& $env:FFPROBE_PATH -version | Select-Object -First 1
Write-Output "SHA256 verified: $actual"
