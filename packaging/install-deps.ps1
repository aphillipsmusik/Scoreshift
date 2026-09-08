<#
    Installs the two outside programs ScoreShift relies on, if they are missing:
      * Java 21    - Audiveris runs on it
      * Audiveris  - reads the notes off the page

    The installer runs this when you tick "Install the note-reading engine".
    It is safe to run again at any time.
#>

$ErrorActionPreference = "Continue"

function Say([string]$text, [string]$colour = "Gray") {
    Write-Host $text -ForegroundColor $colour
}

function Find-Audiveris {
    @(
        "$env:ProgramFiles\Audiveris\Audiveris.exe",
        "$env:ProgramFiles\Audiveris\bin\Audiveris.bat",
        "${env:ProgramFiles(x86)}\Audiveris\Audiveris.exe",
        "$env:LOCALAPPDATA\Programs\Audiveris\Audiveris.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}

Say ""
Say "  ScoreShift - checking the note-reading engine" "Cyan"
Say ""

$winget = Get-Command winget -ErrorAction SilentlyContinue

# --- Java -----------------------------------------------------------------
if (Get-Command java -ErrorAction SilentlyContinue) {
    Say "Java is already installed." "Green"
} elseif ($winget) {
    Say "Installing Java 21 (Audiveris needs it)..." "White"
    winget install -e --id Microsoft.OpenJDK.21 `
        --accept-source-agreements --accept-package-agreements
} else {
    Say "Java is missing. Install Microsoft OpenJDK 21 from adoptium.net." "Yellow"
}

# --- Audiveris ------------------------------------------------------------
$audiveris = Find-Audiveris
if ($audiveris) {
    Say "Audiveris is already installed: $audiveris" "Green"
} elseif ($winget) {
    Say "Installing Audiveris - roughly 300 MB, this takes a few minutes..." "White"
    winget install -e --id audiveris.org.Audiveris `
        --accept-source-agreements --accept-package-agreements
    $audiveris = Find-Audiveris
    if ($audiveris) {
        Say "Audiveris installed: $audiveris" "Green"
    } else {
        Say "Audiveris did not appear where expected. Open ScoreShift's" "Yellow"
        Say "Settings and point it at Audiveris.exe by hand." "Yellow"
    }
} else {
    Say "winget is not available on this PC." "Yellow"
    Say "Download Audiveris from https://github.com/Audiveris/audiveris/releases" "Yellow"
}

# --- MuseScore (optional) -------------------------------------------------
$musescore = Get-ChildItem "$env:ProgramFiles" -Filter "MuseScore*" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($musescore) {
    Say "MuseScore found - it will be used for the nicest PDFs." "Green"
} else {
    Say "MuseScore is not installed. PDFs will use the built-in engraver," "Gray"
    Say "which is fine. Install MuseScore later if you want to edit scores." "Gray"
}

Say ""
Say "Done." "Cyan"
Start-Sleep -Seconds 2
