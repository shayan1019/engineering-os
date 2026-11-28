$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or !$env:RUNNER_TEMP) {
    throw 'Run the shell integration acceptance in the disposable GitHub Actions profile.'
}
$base = $env:RUNNER_TEMP
$directory = Join-Path $base ('eos-nexus-' + [Guid]::NewGuid().ToString('N'))
$homePath = Join-Path $env:USERPROFILE '.eos'
$launcher = Join-Path $homePath 'bin/eos.exe'
$menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'EngineeringOS'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
if ((Test-Path -LiteralPath $launcher) -or (Test-Path -LiteralPath $menu)) {
    throw 'The acceptance run requires a fresh EOS profile.'
}
if ($env:EOS_HOME) { throw 'Use the disposable Windows profile default EOS home.' }
$setup = Get-ChildItem (Join-Path $PSScriptRoot 'out') -Filter 'EngineeringOS-Setup-*.exe' | Select-Object -First 1
if (!$setup) { throw 'The Windows installer is missing.' }
Get-FileHash -LiteralPath $setup.FullName -Algorithm SHA256
function Run-Silent([string]$File, [string[]]$Arguments) {
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -WindowStyle Hidden -PassThru -Wait
    if ($process.ExitCode -ne 0) { throw "Installer command exited $($process.ExitCode)" }
}
Run-Silent $setup.FullName @('/S', "/D=$directory")
try {
    $verified = & $launcher nexus --print | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or !$verified.verified) { throw 'Installed NEXUS manifest verification failed.' }
    $shortcut = Join-Path $menu 'EOS NEXUS.lnk'
    if (!(Test-Path -LiteralPath $shortcut)) { throw 'Start Menu shortcut is missing.' }
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
    if ($link.TargetPath -ne $launcher -or $link.Arguments -ne 'nexus') { throw 'Start Menu shortcut points to an unexpected command.' }
    # Invoke the same registration method used by the desktop preference.
    $env:EOS_TEST_NEXUS_EXE = $verified.program
    & powershell.exe -NoProfile -NonInteractive -Command {
        $ErrorActionPreference = 'Stop'
        $assembly = [Reflection.Assembly]::LoadFrom($env:EOS_TEST_NEXUS_EXE)
        $method = $assembly.GetType('Eos.Nexus.Preferences', $true).GetMethod('ApplyStartup', [Reflection.BindingFlags]'NonPublic,Static')
        $method.Invoke($null, [object[]]@($true, (Join-Path $env:USERPROFILE '.eos'))) | Out-Null
    }
    if ($LASTEXITCODE) { throw 'Native NEXUS startup registration failed.' }
    $startup = Get-ItemPropertyValue -LiteralPath $runKey -Name 'EOS NEXUS'
    if ($startup -ne ('"{0}" nexus --startup --eos-home "{1}"' -f $launcher, $homePath)) { throw 'Unexpected startup command.' }
    $evidence = Join-Path $homePath 'evidence/installer-acceptance.txt'
    New-Item -ItemType Directory -Force (Split-Path $evidence) | Out-Null
    Set-Content -LiteralPath $evidence -Value 'EOS evidence preservation marker'
    Write-Output "Verified installed version $($verified.version) and Start Menu target."
} finally {
    Run-Silent (Join-Path $directory 'uninstall.exe') @('/S')
}
foreach ($item in @($launcher,(Join-Path $menu 'EOS NEXUS.lnk'),(Join-Path $directory 'EngineeringOS.cmd'))) {
    if (Test-Path -LiteralPath $item) { throw "Uninstall left $item behind." }
}
if (!(Test-Path -LiteralPath $evidence)) { throw 'Uninstall removed EOS evidence.' }
if (Get-ItemPropertyValue -LiteralPath $runKey -Name 'EOS NEXUS' -ErrorAction SilentlyContinue) { throw 'Uninstall left the owned startup registration.' }
Write-Output 'NSIS install, Start Menu and startup registration, uninstall and evidence retention passed.'
