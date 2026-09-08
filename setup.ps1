<#
    ScoreShift setup for Windows.

    Creates a private Python environment for the app, installs what it needs,
    and points you at the two free programs it leans on (Audiveris for
    reading notes, MuseScore for engraving PDFs).

    Run it by right-clicking this file and choosing "Run with PowerShell",
    or from a PowerShell window:
        powershell -ExecutionPolicy Bypass -File .\setup.ps1
#>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Say([string]$text, [string]$colour = "Gray") {
    Write-Host $text -ForegroundColor $colour
}

Say ""
Say "  ScoreShift setup" "Cyan"
Say "  ----------------" "Cyan"
Say ""

# ---------------------------------------------------------------------------
# 1. Find a Python the graphics libraries have wheels for (3.10 - 3.13).
# ---------------------------------------------------------------------------
Say "Looking for a suitable Python..." "White"

$python = $null
$preferred = @("3.13", "3.12", "3.11", "3.10")

if (Get-Command py -ErrorAction SilentlyContinue) {
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
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        $ver = (& $cmd.Source -c "import sys;print('%d.%d'%sys.version_info[:2])")
        if ($preferred -contains $ver) { $python = $cmd.Source }
    }
}

if (-not $python) {
    Say "No Python 3.10-3.13 found." "Yellow"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $answer = Read-Host "Install Python 3.13 now with winget? [Y/n]"
        if ($answer -eq "" -or $answer -match '^[Yy]') {
            winget install -e --id Python.Python.3.13 --accept-source-agreements --accept-package-agreements
            $guess = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
            if (Test-Path $guess) { $python = $guess }
        }
    }
}

if (-not $python) {
    Say "Please install Python 3.13 from https://www.python.org/downloads/ and run this again." "Red"
    Read-Host "Press Enter to close"
    exit 1
}
Say "Using $python" "Green"

# ---------------------------------------------------------------------------
# 2. Private environment for the app.
# ---------------------------------------------------------------------------
$venv = Join-Path $root ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Say "Creating the app's Python environment (one-off, ~1 minute)..." "White"
    & $python -m venv $venv
}

Say "Installing the libraries ScoreShift needs..." "White"
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Say "Installing the libraries failed. The messages above say why." "Red"
    Read-Host "Press Enter to close"
    exit 1
}
Say "Libraries installed." "Green"

# ---------------------------------------------------------------------------
# 3. Audiveris - the engine that reads the notes off the page.
# ---------------------------------------------------------------------------
Say ""
Say "Checking the note-reading engine (Audiveris)..." "White"

$audiveris = @(
    "$env:ProgramFiles\Audiveris\Audiveris.exe",
    "$env:ProgramFiles\Audiveris\bin\Audiveris.bat",
    "${env:ProgramFiles(x86)}\Audiveris\Audiveris.exe",
    "$env:LOCALAPPDATA\Programs\Audiveris\Audiveris.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($audiveris) {
    Say "Found: $audiveris" "Green"
} else {
    Say "Audiveris is not installed - ScoreShift needs it to read the notes." "Yellow"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $answer = Read-Host "Install it now with winget? [Y/n]"
        if ($answer -eq "" -or $answer -match '^[Yy]') {
            winget install -e --id audiveris.org.Audiveris --accept-source-agreements --accept-package-agreements
        }
    } else {
        Say "Download it from https://github.com/Audiveris/audiveris/releases" "Yellow"
    }
}

if (-not (Get-Command java -ErrorAction SilentlyContinue)) {
    Say "Java was not found. Audiveris needs Java 21 or newer." "Yellow"
    Say "Install it with:  winget install -e --id Microsoft.OpenJDK.21" "Yellow"
}

# ---------------------------------------------------------------------------
# 4. MuseScore - optional, for the nicest PDFs.
# ---------------------------------------------------------------------------
$musescore = Get-ChildItem "$env:ProgramFiles" -Filter "MuseScore*" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($musescore) {
    Say "MuseScore found - PDFs will be engraved with it." "Green"
} else {
    Say "MuseScore not found. PDFs will use the built-in Verovio engine, which is fine." "Gray"
}

# ---------------------------------------------------------------------------
# 5. Shortcut.
# ---------------------------------------------------------------------------
try {
    $shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "ScoreShift.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($shortcut)
    $link.TargetPath = Join-Path $root "ScoreShift.bat"
    $link.WorkingDirectory = $root
    $link.Description = "Scan sheet music and change its key"
    $link.Save()
    Say "Desktop shortcut created." "Green"
} catch {
    Say "Could not create the desktop shortcut (not a problem - use ScoreShift.bat)." "Gray"
}

Say ""
Say "Done. Start ScoreShift from the desktop shortcut or ScoreShift.bat." "Cyan"
Say ""
Read-Host "Press Enter to close"
