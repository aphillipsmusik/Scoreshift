<#
    Cut a ScoreShift release.

        powershell -ExecutionPolicy Bypass -File .\packaging\release.ps1 -Version 1.3

    Bumps the version in the two places that hold it, builds the installer,
    writes SHA256SUMS.txt (the updater checks downloads against it), then tags
    and publishes a GitHub release with both files attached.

    Needs the GitHub CLI signed in:  gh auth login
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Notes = "",
    [switch]$Draft,
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

function Say([string]$t, [string]$c = "Gray") { Write-Host $t -ForegroundColor $c }

if ($Version -notmatch '^\d+\.\d+(\.\d+)?$') {
    Say "Version should look like 1.3 or 1.3.1" "Red"; exit 1
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Say "The GitHub CLI is not installed. winget install -e --id GitHub.cli" "Red"; exit 1
}

Say ""
Say "  Releasing ScoreShift $Version" "Cyan"
Say ""

# 1. Version lives in exactly two files - keep them in step.
$init = "scoreshift\__init__.py"
(Get-Content $init -Raw) -replace '__version__ = "[^"]*"', "__version__ = `"$Version`"" |
    Set-Content $init -NoNewline
$iss = "packaging\installer.iss"
(Get-Content $iss -Raw) -replace '#define AppVersion   "[^"]*"', "#define AppVersion   `"$Version`"" |
    Set-Content $iss -NoNewline
Say "Version set to $Version in $init and $iss" "Green"

# 2. Build.
if (-not $NoBuild) {
    & powershell -ExecutionPolicy Bypass -NoProfile -File "$root\build.ps1"
    if ($LASTEXITCODE -ne 0) { Say "Build failed." "Red"; exit 1 }
}

$setup = "dist\ScoreShift-Setup-$Version.exe"
if (-not (Test-Path $setup)) { Say "Missing $setup" "Red"; exit 1 }

# 3. Checksums - this is what the in-app updater verifies against.
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
$sums = "dist\SHA256SUMS.txt"
"$hash  $(Split-Path $setup -Leaf)" | Set-Content $sums -Encoding ascii
Say "SHA256 $hash" "Green"

# 4. Commit, tag, publish.
& git add -A
& git commit -m "Release $Version" 2>$null | Out-Null
& git tag -a "v$Version" -m "ScoreShift $Version" -f
& git push
& git push origin "v$Version" -f

$notesArg = @()
if ($Notes) { $notesArg = @("--notes", $Notes) }
elseif (Test-Path "CHANGELOG.md") { $notesArg = @("--notes-file", "CHANGELOG.md") }
else { $notesArg = @("--generate-notes") }

$draftArg = @()
if ($Draft) { $draftArg = @("--draft") }

& gh release create "v$Version" $setup $sums --title "ScoreShift $Version" @notesArg @draftArg
if ($LASTEXITCODE -ne 0) { Say "gh release create failed." "Red"; exit 1 }

Say ""
Say "Published v$Version. Anyone running an older ScoreShift will be offered it." "Cyan"
