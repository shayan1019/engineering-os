param(
    [Parameter(Mandatory = $true)][string] $PythonPath,
    [Parameter(Mandatory = $true)][string] $OutputDirectory,
    [ValidateRange(6, 120)][int] $Seconds = 10
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$runRoot = Join-Path $OutputDirectory ([Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
$runRoot = (Resolve-Path -LiteralPath $runRoot).Path
$program = ( & (Join-Path $PSScriptRoot 'build.ps1') -OutputPath (Join-Path $runRoot 'EOS.Nexus.exe') ).FullName
$python = (Resolve-Path -LiteralPath $PythonPath).Path
$observer = Join-Path $repo 'nexus\observer.py'
$engine = Join-Path $repo 'engine'

function Quote-Argument([string] $Value) {
    if ($Value.Contains('"') -or $Value.EndsWith('\')) {
        throw "Test argument cannot contain quotes or end in a trailing backslash."
    }
    return ('"' + $Value + '"')
}

function New-Nexus([string[]] $Arguments) {
    Start-Process -FilePath $program -ArgumentList ($Arguments | ForEach-Object { Quote-Argument $_ }) `
        -PassThru -WindowStyle Normal
}

function Close-Smoke($Process) {
    if (-not $Process.WaitForExit(($Seconds + 11) * 1000)) {
        $null = $Process.CloseMainWindow()
        throw "NEXUS smoke window did not exit after $Seconds seconds."
    }
    if ($Process.ExitCode -ne 0) { throw "NEXUS exit code $($Process.ExitCode)." }
}

function Read-Measurement([string] $FilePath) {
    Get-Content -LiteralPath $FilePath -Raw | ConvertFrom-Json
}

function Get-ShortHash([string] $Value) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $algorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($Value))
        return [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant().Substring(0, 24)
    }
    finally { $algorithm.Dispose() }
}

function Write-HostEvent([string] $LogPath, [hashtable] $Fields) {
    $Fields.schema = 1
    $Fields.ts = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
    if (-not $Fields.host) { $Fields.host = 'opencode' }
    $line = $Fields | ConvertTo-Json -Compress
    [IO.File]::AppendAllText($LogPath, "$line`n", [Text.UTF8Encoding]::new($false))
}

$emptyHome = Join-Path $runRoot 'empty'
New-Item -ItemType Directory -Path $emptyHome | Out-Null
$idle = Join-Path $runRoot 'idle.json'
$config = @('--python', $python, '--observer', $observer, '--engine', $engine)
$benchmark = @('--smoke-seconds', "$Seconds")

$blocked = New-Nexus ($config + @('--eos-home', $emptyHome, '--auto', '--session', 'idle-opt-out', '--host', 'opencode') +
    @('--smoke-seconds', '2', '--perf-output', (Join-Path $runRoot 'unexpected.json')))
Close-Smoke $blocked
if (Test-Path -LiteralPath (Join-Path $runRoot 'unexpected.json')) { throw 'Auto-open default opt-out failed.' }

$idleRun = New-Nexus ($config + @('--eos-home', $emptyHome, '--perf-output', $idle) + $benchmark)
Close-Smoke $idleRun
$idleMeasurement = Read-Measurement $idle
if ($idleMeasurement.mode -ne 'DISCONNECTED' -or $idleMeasurement.animatedFrames -ne 0) {
    throw 'Disconnected desktop rendered a live animation.'
}

$demoFile = Join-Path $runRoot 'demo.json'
$demo = New-Nexus ($config + @('--eos-home', $emptyHome, '--demo', '--perf-output', $demoFile,
    '--screenshot', (Join-Path $runRoot 'demo.png')) + $benchmark)
Close-Smoke $demo
$demoMeasurement = Read-Measurement $demoFile
if ($demoMeasurement.mode -ne 'DEMO' -or $demoMeasurement.animatedFrames -le 0) {
    throw 'Simulation must show its own mode and animate observed fake events only there.'
}

$homePath = Join-Path $runRoot 'test-eos'
$preferences = Join-Path $homePath 'nexus\preferences.json'
New-Item -ItemType Directory -Path (Split-Path $preferences -Parent) -Force | Out-Null
[IO.File]::WriteAllText($preferences, '{"autoOpen":true,"closeToTray":false,"retentionDays":1}')
$workspace = Join-Path $runRoot 'one'
$workspaceTwo = Join-Path $runRoot 'two'
New-Item -ItemType Directory -Path $workspace, $workspaceTwo | Out-Null
$workspaceRegistry = [ordered] @{ workspaces = @( @{ path = $workspaceTwo; name = 'Second project' } ) }
[IO.File]::WriteAllText((Join-Path $homePath 'workspaces.json'), ($workspaceRegistry | ConvertTo-Json -Depth 4))
$projectId = Get-ShortHash ($workspace.Replace('\', '/').ToLowerInvariant())
$otherId = Get-ShortHash ($workspaceTwo.Replace('\', '/').ToLowerInvariant())
$sessionName = 'desktop-smoke-opencode'
$targetLog = Join-Path $homePath "evidence\nexus\$projectId\$sessionName.jsonl"
$otherLog = Join-Path $homePath "evidence\nexus\$otherId\unrelated-claude.jsonl"
New-Item -ItemType Directory -Path (Split-Path $targetLog -Parent), (Split-Path $otherLog -Parent) -Force | Out-Null
Write-HostEvent $targetLog @{ kind = 'session.started' }
Write-HostEvent $targetLog @{ kind = 'agent.started'; agent = 'reviewer'; correlation = 'review-a' }
Write-HostEvent $targetLog @{ kind = 'skill.selected'; skill = 'code-quality' }
Write-HostEvent $targetLog @{ kind = 'security.denied'; severity = 'HIGH'; reason = 'omitted' }
Write-HostEvent $otherLog @{ kind = 'session.started'; host = 'claude' }
Write-HostEvent $otherLog @{ kind = 'tool.started'; host = 'claude'; tool = 'read' }

$autoFlags = @('--workspace', $workspace, '--auto', '--session', $sessionName, '--host', 'opencode')
$file = Join-Path $runRoot 'transport.json'
$transport = New-Nexus ($config + @('--eos-home', $homePath, '--perf-output', $file) + $benchmark + $autoFlags)
Start-Sleep -Milliseconds 2000
$duplicate = New-Nexus ($config + @('--eos-home', $homePath) + $autoFlags)
Close-Smoke $duplicate
if ($transport.HasExited) { throw 'Duplicate launch stopped the first instance.' }
Write-HostEvent $targetLog @{ kind = 'agent.completed'; agent = 'reviewer'; correlation = 'review-a' }
Close-Smoke $transport
$transportMeasurement = Read-Measurement $file
$projectedSession = Get-ShortHash ($projectId + "`0opencode`0" + $sessionName)
if ($transportMeasurement.mode -ne 'LIVE' -or $transportMeasurement.observedSessions -ne 2 -or
    $transportMeasurement.selectedSession -ne $projectedSession -or $transportMeasurement.selectedEvents -ne 5 -or
    $transportMeasurement.securityDenials -ne 1 -or $transportMeasurement.animatedFrames -le 0 -or
    $transportMeasurement.activeNodesAtExit -ne 0) {
    throw "Observer handshake, isolation, denial projection, or lifecycle rendering failed. Inspect $file."
}
$once = Join-Path $runRoot 'unexpected-duplicate.json'
$again = New-Nexus ($config + @('--eos-home', $homePath, '--smoke-seconds', '2', '--perf-output', $once) + $autoFlags)
Close-Smoke $again
if (Test-Path -LiteralPath $once) { throw 'Once-per-session preference did not persist across exit.' }
[ordered] @{
    directory = $runRoot
    optOut = 'PASS'
    demo = $demoMeasurement
    disconnected = $idleMeasurement
    authenticatedObserverWithTwoFixtureSessions = $transportMeasurement
    firstInstancePreserved = 'PASS'
    activationOnlyOnce = 'PASS'
} | ConvertTo-Json -Depth 5
