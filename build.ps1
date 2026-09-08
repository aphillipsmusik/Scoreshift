<#
    Build ScoreShift into a Windows installer.

        powershell -ExecutionPolicy Bypass -File .\build.ps1

    Produces:
        dist\ScoreShift\ScoreShift.exe          the app, ready to run
        dist\ScoreShift-Setup-<version>.exe     the installer

    Options:
        -SkipInstaller     just build the exe folder, no Inno Setup step
        -Clean             wipe build/ and dist/ first
#>
param(
    [switch]$SkipInstaller,
    [switch]$Clean,
    [string]$Python = ""      # force a particular python.exe if you need to
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Say([string]$text, [string]$colour = "Gray") {
    Write-Host $text -ForegroundColor $colour
}

function Fail([string]$text) {
    Say $text "Red"
    Read-Host "Press Enter to close"
    exit 1
}

function Find-ISCC {
    # winget puts Inno Setup under LocalAppData on a per-user install and under
    # Program Files on a machine-wide one, so look in all of them.
    $roots = @("$env:ProgramFiles", "${env:ProgramFiles(x86)}",
               "$env:LOCALAPPDATA\Programs") | Where-Object { $_ -and (Test-Path $_) }
    foreach ($root in $roots) {
        $hit = Get-ChildItem -Path $root -Filter "ISCC.exe" -Recurse -Depth 3 `
            -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

Say ""
Say "  Building ScoreShift" "Cyan"
Say "  -------------------" "Cyan"

if ($Clean) {
    Say "Cleaning build/ and dist/..." "White"
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------------
# 1. Pick a Python to freeze with.
#
# 3.12 first, not the newest: PyInstaller's bytecode scanner crashes on some
# 3.13 point releases (a TypeError out of dis.findlabels while it walks
# music21). 3.10-3.12 are the safe ground, and all of them have PySide6 and
# OpenCV wheels. Pass -Python to override.
# ---------------------------------------------------------------------------
$python = $null
$preferred = @("3.12", "3.11", "3.10", "3.13")
if ($Python) {
    if (-not (Test-Path $Python)) { Fail "No python at $Python" }
    $python = $Python
}
if (-not $python -and (Get-Command py -ErrorAction SilentlyContinue)) {
    $installed = @{}
    foreach ($line in (& py -0p 2>$null)) {
        if ($line -match '-V:(\d+\.\d+)\s+\*?\s*(.+\.exe)') {
            $installed[$Matches[1]] = $Matches[2].Trim()
        }
    }
    foreach ($v in $preferred) {
        if ($installed.ContainsKey($v)) { $python = $installed[$v]; break }
    }
}
if (-not $python) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say "Installing Python 3.13..." "Yellow"
        winget install -e --id Python.Python.3.13 `
            --accept-source-agreements --accept-package-agreements
        $guess = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
        if (Test-Path $guess) { $python = $guess }
    }
}
if (-not $python) { Fail "Need Python 3.10-3.13. Install it and run this again." }
Say "Python: $python" "Green"

# ---------------------------------------------------------------------------
# 2. Build environment
# ---------------------------------------------------------------------------
$venv = Join-Path $root ".venv-build"
$vpy = Join-Path $venv "Scripts\python.exe"

# An environment left over from a different interpreter is worse than none.
if (Test-Path $vpy) {
    $want = (& $python -c "import sys;print('%d.%d'%sys.version_info[:2])")
    $have = (& $vpy -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null)
    if ($want -ne $have) {
        Say "Rebuilding the build environment ($have -> $want)..." "Yellow"
        Remove-Item -Recurse -Force $venv
    }
}
if (-not (Test-Path $vpy)) {
    Say "Creating the build environment..." "White"
    & $python -m venv $venv
}
Say "Installing build dependencies..." "White"
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install -r requirements.txt --quiet
& $vpy -m pip install -r requirements-server.txt --quiet
& $vpy -m pip install pyinstaller --quiet
if ($LASTEXITCODE -ne 0) { Fail "Installing the build dependencies failed." }

# ---------------------------------------------------------------------------
# 3. Icon, then the frozen app
# ---------------------------------------------------------------------------
Say "Drawing the app icon..." "White"
& $vpy tools\make_icon.py

Say "Freezing the app with PyInstaller (a few minutes)..." "White"
& $vpy -m PyInstaller packaging\scoreshift.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller failed - see the messages above." }

$exe = Join-Path $root "dist\ScoreShift\ScoreShift.exe"
if (-not (Test-Path $exe)) { Fail "PyInstaller finished but $exe is missing." }
$size = [math]::Round((Get-ChildItem "dist\ScoreShift" -Recurse |
    Measure-Object -Property Length -Sum).Sum / 1MB, 1)
Say "Built dist\ScoreShift ($size MB)" "Green"

# ---------------------------------------------------------------------------
# 4. Smoke test - does the frozen exe start and shut down cleanly?
# ---------------------------------------------------------------------------
# Start the API for a moment and ask it how it is. This exercises the real
# import chain - music21, Verovio, the engine - without needing a window, and
# without blocking on anything that could pop a dialog.
Say "Smoke-testing the frozen app..." "White"
$port = 8791
$probe = Start-Process -FilePath $exe `
    -ArgumentList "--server", "--host", "127.0.0.1", "--port", "$port" `
    -PassThru -WindowStyle Hidden -ErrorAction SilentlyContinue
$healthy = $false
foreach ($attempt in 1..30) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-RestMethod "http://127.0.0.1:$port/v1/health" -TimeoutSec 3
        if ($r.version) {
            Say "The frozen app runs (version $($r.version), engraver: $($r.engraver))." "Green"
            $healthy = $true
        }
        break
    } catch {
        if ($probe -and $probe.HasExited) { break }
    }
}
if ($probe -and -not $probe.HasExited) { Stop-Process -Id $probe.Id -Force -EA SilentlyContinue }
if (-not $healthy) {
    Say "  The smoke test did not get a reply. Try dist\ScoreShift\ScoreShift.exe by hand." "Yellow"
}

# ---------------------------------------------------------------------------
# 5. Installer
# ---------------------------------------------------------------------------
if ($SkipInstaller) {
    Say ""
    Say "Skipping the installer. Run dist\ScoreShift\ScoreShift.exe to try it." "Cyan"
    exit 0
}

$iscc = Find-ISCC

if (-not $iscc) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say "Installing Inno Setup (used to build the installer)..." "Yellow"
        winget install -e --id JRSoftware.InnoSetup `
            --accept-source-agreements --accept-package-agreements
        $iscc = Find-ISCC
    }
}
if (-not $iscc) {
    Say "Inno Setup was not found, so no installer was built." "Yellow"
    Say "The app itself is ready in dist\ScoreShift." "Yellow"
    exit 0
}

Say "Compiling the installer..." "White"
& $iscc "packaging\installer.iss"
if ($LASTEXITCODE -ne 0) { Fail "Inno Setup failed." }

$setup = Get-ChildItem "dist\ScoreShift-Setup-*.exe" | Sort-Object LastWriteTime |
    Select-Object -Last 1
Say ""
Say "Done." "Cyan"
Say "  App:       dist\ScoreShift\ScoreShift.exe" "Green"
if ($setup) {
    $mb = [math]::Round($setup.Length / 1MB, 1)
    Say "  Installer: dist\$($setup.Name)  ($mb MB)" "Green"
}
Say ""
