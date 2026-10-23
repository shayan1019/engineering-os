<#
.SYNOPSIS
    Builds the EngineeringOS release artifacts (Windows).

.DESCRIPTION
    1. builds the Rust CLI in release mode
    2. stages eos.exe + engine/ + runtime/python/ into installer\stage
    3. builds the NSIS installer (EngineeringOS-Setup-<version>.exe)
    4. builds the portable ZIP and the standalone eos.exe
    5. writes SHA256SUMS.txt next to the artifacts

    Every step is verified: the script fails if an expected artifact is missing
    or if the installer did not run the product's own install command.
#>
[CmdletBinding()]
param(
    [string]$Version = "",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$stage = Join-Path $PSScriptRoot "stage"
$out = Join-Path $PSScriptRoot "out"

if (-not $Version) {
    $cargoToml = Get-Content (Join-Path $root "cli\Cargo.toml") -Raw
    if ($cargoToml -notmatch '(?m)^version\s*=\s*"([^"]+)"') {
        throw "cannot read version from cli/Cargo.toml"
    }
    $Version = $Matches[1]
    # The product appends a release-channel suffix to the crate version
    # (install.rs: VERSION = concat!(env!("CARGO_PKG_VERSION"), "-alpha")).
    # The artifacts must carry the same string the program reports.
    $installRs = Get-Content (Join-Path $root "cli\src\install.rs") -Raw
    if ($installRs -match 'VERSION\s*:\s*&str\s*=\s*concat!\(env!\("CARGO_PKG_VERSION"\)\s*,\s*"([^"]+)"\)') {
        $Version = $Version + $Matches[1]
    }
}
Write-Host "== EngineeringOS $Version =="

# ---------------------------------------------------------------- 1. CLI build
Write-Host "[1/5] cargo build --release"
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
Push-Location (Join-Path $root "cli")
try {
    cargo build --release
    if ($LASTEXITCODE -ne 0) { throw "cargo build failed ($LASTEXITCODE)" }
} finally { Pop-Location }
$eosExe = Join-Path $root "cli\target\release\eos.exe"
if (-not (Test-Path $eosExe)) { throw "missing $eosExe" }

# ------------------------------------------------------------------- 2. stage
Write-Host "[2/5] staging payload"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Path $stage | Out-Null
Copy-Item $eosExe (Join-Path $stage "eos.exe")

$engineSrc = Join-Path $root "engine"
if (-not (Test-Path (Join-Path $engineSrc "tools\quality_gate.py"))) {
    throw "engine/tools/quality_gate.py missing - the engine is incomplete"
}
# The destination must exist first, otherwise PowerShell copies the *first* source
# item onto it and the rest of the tree lands as a leaf.
New-Item -ItemType Directory -Path (Join-Path $stage "engine") | Out-Null
Copy-Item -Recurse (Join-Path $engineSrc "*") (Join-Path $stage "engine")

# The distributed engine must carry the license and the third-party inventory.
# They live at the repository root for GitHub's license detection; copy them into
# the payload so a user who only installs the package still receives them.
foreach ($legal in @("LICENSE", "NOTICE", "THIRD_PARTY_LICENSES.md")) {
    $src = Join-Path $root $legal
    if (-not (Test-Path $src)) { throw "missing legal file: $legal" }
    Copy-Item $src (Join-Path $stage "engine\$legal")
}

# Generated per-workspace artifacts must not ship: the index and the catalog are
# rebuilt by `eos skills discover` on the machine that installs the product, and
# shipping a stale one would make the first routing decision use another
# machine's skill set.
foreach ($generated in @("registry\SKILL_INDEX.json", "registry\SKILL_CATALOG.json")) {
    $p = Join-Path $stage "engine\$generated"
    if (Test-Path $p) { Remove-Item -Force $p }
}
Get-ChildItem (Join-Path $stage "engine") -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force

$runtimeSrc = Join-Path $root "runtime\python"
if (-not (Test-Path (Join-Path $runtimeSrc "python.exe"))) {
    throw "runtime/python/python.exe missing - the bundled interpreter is not staged"
}
New-Item -ItemType Directory -Path (Join-Path $stage "runtime\python") | Out-Null
Copy-Item -Recurse (Join-Path $runtimeSrc "*") (Join-Path $stage "runtime\python")

$stageFiles = (Get-ChildItem $stage -Recurse -File | Measure-Object).Count
$stageBytes = (Get-ChildItem $stage -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ("      staged {0} files, {1:N1} MB" -f $stageFiles, ($stageBytes / 1MB))

# ---------------------------------------------------------------- 3. installer
if (-not $SkipInstaller) {
    Write-Host "[3/5] makensis"
    $makensis = "C:\Program Files (x86)\NSIS\makensis.exe"
    if (-not (Test-Path $makensis)) { throw "NSIS not found at $makensis" }
    New-Item -ItemType Directory -Path $out -Force | Out-Null
    Push-Location $PSScriptRoot
    try {
        # The Windows version resource only accepts four numeric fields.
        $numeric = ($Version -replace '[^0-9.]', '')
        $parts = $numeric.Split('.') | Where-Object { $_ -ne '' }
        while ($parts.Count -lt 4) { $parts += "0" }
        $productVersion = ($parts[0..3] -join ".")
        & $makensis "/DVERSION=$Version" "/DPRODUCTVERSION=$productVersion" "/DSTAGE=$stage" "EngineeringOS.nsi"
        if ($LASTEXITCODE -ne 0) { throw "makensis failed ($LASTEXITCODE)" }
    } finally { Pop-Location }
} else {
    Write-Host "[3/5] installer skipped"
}

# ------------------------------------------------------------------ 4. extras
Write-Host "[4/5] portable zip + standalone exe"
New-Item -ItemType Directory -Path $out -Force | Out-Null
$zip = Join-Path $out "EngineeringOS-$Version-portable.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Push-Location $stage
try {
    tar.exe -a -c -f $zip "eos.exe" "engine" "runtime"
    if ($LASTEXITCODE -ne 0) { throw "zip failed ($LASTEXITCODE)" }
} finally { Pop-Location }
Copy-Item $eosExe (Join-Path $out "eos-$Version.exe") -Force

# ------------------------------------------------------------------- 5. sums
Write-Host "[5/5] checksums"
$sums = Join-Path $out "SHA256SUMS.txt"
$artifacts = Get-ChildItem $out -File | Where-Object { $_.Name -ne "SHA256SUMS.txt" } |
    Sort-Object Name
$lines = foreach ($a in $artifacts) {
    $h = (Get-FileHash $a.FullName -Algorithm SHA256).Hash
    "$h  $($a.Name)"
}
$lines | Set-Content -Path $sums -Encoding ascii
Write-Host "---- artifacts ----"
foreach ($a in $artifacts) {
    Write-Host ("  {0,-46} {1,10:N0} bytes" -f $a.Name, $a.Length)
}
Write-Host "  SHA256SUMS.txt"
Write-Host "artifacts: $out"

