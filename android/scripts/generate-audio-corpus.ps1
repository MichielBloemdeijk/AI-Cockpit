param([Parameter(Mandatory=$true)][string]$OutputDirectory,
      [string]$ManifestPath = (Join-Path $PSScriptRoot 'audio-corpus-v1.json'))
$ErrorActionPreference='Stop'
$taskManifest=Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
$taskDestination=[IO.Path]::GetFullPath($OutputDirectory)
if ((Test-Path -LiteralPath $taskDestination) -and (Get-ChildItem -LiteralPath $taskDestination)) {
    throw 'Use a fresh corpus directory.'
}
New-Item -ItemType Directory -Path $taskDestination -Force | Out-Null
$taskSpeaker=New-Object -ComObject SAPI.SpVoice
$taskVoices=$taskSpeaker.GetVoices()
$taskEntries=@()
try {
    $taskIndex=0
    foreach($taskCase in $taskManifest.cases) {
        $taskSpeaker.Voice=$taskVoices.Item($taskIndex % $taskVoices.Count)
        $taskSpeaker.Rate=0
        foreach($taskPart in @('wake','command')) {
            $taskStream=New-Object -ComObject SAPI.SpFileStream
            $taskStream.Format.Type=18 # SAFT16kHz16BitMono
            $taskFile=Join-Path $taskDestination "$($taskCase.id)-$taskPart.wav"
            try {
                $taskStream.Open($taskFile,3,$false)
                $taskSpeaker.AudioOutputStream=$taskStream
                $taskText=if($taskPart -eq 'wake') {$taskManifest.wake} else {$taskCase.text}
                [void]$taskSpeaker.Speak($taskText)
            } finally { $taskStream.Close() }
            if ((Get-Item -LiteralPath $taskFile).Length -lt 3200) { throw "Speech renderer produced no usable PCM for $($taskCase.id)." }
        }
        $taskEntries+=@{id=$taskCase.id;voice=$taskSpeaker.Voice.GetDescription();rate=0}
        $taskIndex++
    }
    @{schema='jarvis-audio-render.v1';corpus='jarvis-audio-corpus.v1';renderer='Windows SAPI offline';samples=$taskEntries} |
        ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $taskDestination 'render.json') -Encoding utf8
} finally { [Runtime.InteropServices.Marshal]::ReleaseComObject($taskSpeaker) | Out-Null }
