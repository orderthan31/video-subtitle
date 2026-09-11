param([string]$OutputPath = 'data/vocalization-fixtures/generated-speech.wav')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$destination = [IO.Path]::GetFullPath($OutputPath)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $speaker.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::NotSet,
        [System.Speech.Synthesis.VoiceAge]::NotSet, 0, [Globalization.CultureInfo]::GetCultureInfo('en-US'))
    $speaker.SetOutputToWaveFile($destination)
    $speaker.Speak('Hello. Welcome to the subtitle test. The weather is sunny today.')
} finally {
    $speaker.Dispose()
}
Write-Output "Generated fixed-text synthetic speech: $destination"
