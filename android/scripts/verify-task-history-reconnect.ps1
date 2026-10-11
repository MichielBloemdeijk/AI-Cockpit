param(
    [string]$BackendUrl = 'http://127.0.0.1:8000',
    [string]$WebUrl = 'http://10.0.2.2:3000',
    [string]$Password = 'agenttest',
    [string]$TaskId = '',
    [switch]$CreateFixture,
    [string]$AdbPath = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
)

$ErrorActionPreference = 'Stop'
$package = 'nl.sander.aicockpit.jarvis'
if (-not (Test-Path -LiteralPath $AdbPath)) { throw "adb not found: $AdbPath" }

function Invoke-Adb {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    $output = & $AdbPath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "adb failed: $($Arguments -join ' ')" }
    return $output
}

function Get-UiNodes {
    for ($step = 0; $step -lt 6; $step++) {
        $dump = (Invoke-Adb @('shell', 'uiautomator', 'dump', '/sdcard/jarvis-reconnect.xml')) -join ''
        if ($dump -match 'UI hierchary dumped|UI hierarchy dumped') {
            $raw = (Invoke-Adb @('shell', 'cat', '/sdcard/jarvis-reconnect.xml')) -join ''
            if ($raw.StartsWith('<?xml')) {
                $document = [xml]$raw
                return $document.SelectNodes('//node')
            }
        }
        Start-Sleep -Milliseconds 500
    }
    throw 'Android accessibility tree is temporarily unavailable'
}

function Wait-Node([string]$Text, [int]$Seconds = 20) {
    for ($step = 0; $step -lt $Seconds; $step++) {
        try { $match = @(Get-UiNodes | Where-Object { $_.text -eq $Text }) | Select-Object -First 1 }
        catch { $match = $null }
        if ($match) { return $match }
        Start-Sleep -Seconds 1
    }
    throw "Android UI did not show '$Text' within $Seconds seconds"
}

function Tap-Node($Node) {
    if ($Node.bounds -notmatch '^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$') {
        throw "Invalid Android bounds: $($Node.bounds)"
    }
    $x = [int](($Matches[1] -as [int]) + (($Matches[3] -as [int]) - ($Matches[1] -as [int])) / 2)
    $y = [int](($Matches[2] -as [int]) + (($Matches[4] -as [int]) - ($Matches[2] -as [int])) / 2)
    Invoke-Adb @('shell', 'input', 'tap', "$x", "$y") | Out-Null
}

function Wait-OpenButton {
    for ($step = 0; $step -lt 6; $step++) {
        Invoke-Adb @('shell', 'input', 'swipe', '500', '1900', '500', '650', '350') | Out-Null
        $button = @(Get-UiNodes | Where-Object { $_.text -eq 'Open AI Cockpit' }) | Select-Object -First 1
        if ($button) { return $button }
        Start-Sleep -Seconds 1
    }
    throw 'Android Open AI Cockpit button is unavailable after scrolling'
}

$devices = (Invoke-Adb @('devices')) -join "`n"
if ($devices -notmatch 'emulator-\d+\s+device') { throw 'No ready Android emulator is connected' }
if (-not $TaskId -and -not $CreateFixture) {
    throw 'Supply -TaskId or explicitly opt in to -CreateFixture on a disposable backend'
}
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-RestMethod -Uri "$BackendUrl/api/auth/login" -Method Post -ContentType 'application/json' -Body (@{ password = $Password } | ConvertTo-Json) -WebSession $session | Out-Null
if (-not $TaskId) {
    $goal = "Agent Android reconnect fixture $([guid]::NewGuid().ToString('N'))"
    $created = Invoke-RestMethod -Uri "$BackendUrl/api/tasks" -Method Post -ContentType 'application/json' -Body (@{
        goal = $goal
        idempotency_key = "android-reconnect-$([guid]::NewGuid().ToString('N'))"
        origin_surface = 'tasks_ui'
    } | ConvertTo-Json) -WebSession $session
    $TaskId = $created.id
}
$before = Invoke-RestMethod -Uri "$BackendUrl/api/tasks/$TaskId" -WebSession $session
if (-not $before.id -or -not $before.goal) { throw 'Task fixture is unavailable' }
$runIdsBefore = @($before.runs | ForEach-Object { $_.id })
$attemptIdsBefore = @($before.attempts | ForEach-Object { $_.id })

Invoke-Adb @('shell', 'am', 'force-stop', $package) | Out-Null
Invoke-Adb @('shell', 'am', 'start', '-n', "$package/.MainActivity") | Out-Null
$null = Wait-Node 'AI Cockpit URL'
$urlInput = @(Get-UiNodes | Where-Object { $_.class -eq 'android.widget.EditText' }) | Select-Object -First 1
if (-not $urlInput) { throw 'Android server URL input is unavailable' }
if (-not $urlInput.text) {
    Tap-Node $urlInput
    Invoke-Adb @('shell', 'input', 'text', $WebUrl) | Out-Null
} elseif ($urlInput.text.TrimEnd('/') -ne $WebUrl.TrimEnd('/')) {
    throw "App is configured for $($urlInput.text), expected $WebUrl"
}
Tap-Node (Wait-OpenButton)

$signIn = @(Get-UiNodes | Where-Object { $_.text -eq 'Sign in' }) | Select-Object -First 1
if ($signIn) {
    $passwordInput = @(Get-UiNodes | Where-Object { $_.class -eq 'android.widget.EditText' -and $_.hint -eq 'Password' }) | Select-Object -First 1
    if (-not $passwordInput) { throw 'Android login password input is unavailable' }
    Tap-Node $passwordInput
    Invoke-Adb @('shell', 'input', 'text', $Password) | Out-Null
    Tap-Node (Wait-Node 'Sign in')
}

$tasksNav = @(Get-UiNodes | Where-Object { $_.text -eq 'Tasks' -and $_.bounds -match '\[(\d+),(\d+)\]\[(\d+),(\d+)\]' -and [int]$Matches[2] -gt 2000 }) | Select-Object -First 1
if (-not $tasksNav) { $tasksNav = Wait-Node 'Tasks' }
Tap-Node $tasksNav
Tap-Node (Wait-Node $before.goal)
Wait-Node $TaskId | Out-Null

Invoke-Adb @('shell', 'am', 'force-stop', $package) | Out-Null
Invoke-Adb @('shell', 'am', 'start', '-n', "$package/.MainActivity") | Out-Null
Tap-Node (Wait-OpenButton)
$tasksNav = @(Get-UiNodes | Where-Object { $_.text -eq 'Tasks' -and $_.bounds -match '\[(\d+),(\d+)\]\[(\d+),(\d+)\]' -and [int]$Matches[2] -gt 2000 }) | Select-Object -First 1
if (-not $tasksNav) { $tasksNav = Wait-Node 'Tasks' }
Tap-Node $tasksNav
Tap-Node (Wait-Node $before.goal)
Wait-Node $TaskId | Out-Null

$after = Invoke-RestMethod -Uri "$BackendUrl/api/tasks/$TaskId" -WebSession $session
$runIdsAfter = @($after.runs | ForEach-Object { $_.id })
$attemptIdsAfter = @($after.attempts | ForEach-Object { $_.id })
if (($runIdsBefore -join ',') -ne ($runIdsAfter -join ',') -or
    ($attemptIdsBefore -join ',') -ne ($attemptIdsAfter -join ',')) {
    throw 'Reconnect unexpectedly created a task run or attempt'
}
Write-Output "PASS Android reconnect: task=$TaskId runs=$($runIdsAfter.Count) attempts=$($attemptIdsAfter.Count)"
