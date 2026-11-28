param([string] $OutputPath = (Join-Path $PSScriptRoot 'bin\EOS.Nexus.exe'))

$ErrorActionPreference = 'Stop'
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$compiler = Join-Path $framework 'csc.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
    throw '.NET Framework 4.x x64 C# compiler is required to build EOS NEXUS.'
}

$OutputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputPath)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null
$sources = Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.cs' -File | Sort-Object Name
$icon = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\installer\eos.ico')).Path
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /codepage:65001 /warnaserror+ /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll "/out:$OutputPath" "/win32icon:$icon" $sources.FullName
if ($LASTEXITCODE -ne 0) {
    throw "NEXUS compilation failed with exit code $LASTEXITCODE."
}
Get-Item -LiteralPath $OutputPath
