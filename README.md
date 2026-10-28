# ScoreShift

A Windows document scanner built for sheet music. Point a camera at a page, scan
it on your printer, or drop in photos — ScoreShift finds the page and flattens
it, reads the notes, and saves the music **in whatever key you want**, as a
printable PDF and a MIDI file.

## What it does

1. **Get the pages in**, three ways — all inside the main window, under
   **Capture**:
   - **Camera** — hold a page in front of a webcam. The outline follows the
     page as you move it, everything outside it dims, and it shoots by itself
     once the page is steady, sharp and well framed. Space bar takes a shot
     whenever you want one.
   - **Scan** — straight from your printer (flatbed or document feeder, any DPI).
   - **Add images** — photos from your phone, or any image files.
2. **Clean up.** The page is found by its staff lines - the one thing on a
   desk that nothing else looks like - so a sheet lying on a tray, a mat or a
   laptop is still the thing that gets cropped. Perspective corrected, staves
   straightened off those same lines, lighting evened out so the paper is
   white and the ink is black. An open booklet, or two sheets side by side,
   is **split into two pages** down the fold. The four corners are draggable
   if the automatic guess is off.
3. **Read the notes.** Audiveris, a free offline OMR engine, turns the cleaned
   pages into MusicXML.
4. **Change the key.** A target key, a number of half steps, or an instrument
   (B♭ trumpet, E♭ alto sax, F horn…). It respells enharmonically when that makes
   the part easier to read — D♭ major instead of C♯ major.
5. **Save.** Engraved PDF, MIDI, MusicXML, and optionally the cleaned scan as its
   own PDF. For anything with more than one part, *Each of the N parts in its own
   file as well* adds a PDF and a MIDI per part — *Fanfare - Trumpet in Bb.pdf* —
   named after the parts on the page.

## Installing

Download the latest **ScoreShift-Setup-x.y.exe** from
[Releases](https://github.com/aphillipsmusik/Scoreshift/releases/latest) and run
it. Python is inside the installer; there is nothing to set up first. Leave
*"Install the note-reading engine"* ticked on the components page and it will
fetch Audiveris and Java for you (about 300 MB, a few minutes, one Windows
permission prompt).

Then start it from the Start menu or the desktop shortcut.

### Updates

ScoreShift checks this repository's releases once a day when it starts. When
there's a newer version it shows what changed and offers to install it: one
click downloads the installer, checks it against the SHA-256 published with the
release, runs it and restarts. *Help → Check for updates* does it on demand, and
Settings has a switch to turn the automatic check off.

### Building the installer yourself

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

It finds or installs a Python it can use, creates a build environment, freezes
the app with PyInstaller, and wraps it with Inno Setup (installing that too, if
needed). Output lands in `dist\`:

```
dist\ScoreShift\ScoreShift.exe          the app, runnable as-is
dist\ScoreShift-Setup-1.7.exe         the installer
```

Add `-SkipInstaller` to stop after the exe, or `-Clean` to start fresh.

### Running from source instead

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1   # one-off
.\ScoreShift.bat
```

### The pieces it uses

| Program | Needed? | What for |
|---|---|---|
| Audiveris | Yes | Reading notes off the page |
| Java 21+ | Yes | Audiveris runs on Java |
| MuseScore 4 | Optional | Nicer PDF engraving; otherwise the built-in Verovio engine |

## Using it

**How much detail you need.** Reading music is a matter of resolution: the
note reader wants about 14 pixels between staff lines, and the camera window
measures and reports exactly that - *Detail 18 px - good*, down to *too far to
read*. A whole page across a 720p frame gives about 3 px and 1080p about 6; a
flatbed scan at 300 DPI gives about 20, and a phone photo of a single page
about 17. So the **scanner is the reliable route**, a phone photo is good, and
a webcam wants one page filling the frame. ScoreShift enlarges fine staves for
the reader, but it cannot invent detail the camera never caught, and it says
so rather than pretending.

**Camera.** Fill most of the frame with the page, keep the whole page in view,
and hold still — the outline turns green and it fires. It won't shoot a blurry,
dark or half-cut page, and it waits a couple of seconds before it will take
another, so you can move on to the next page without getting five copies of the
last one. The status line under the picture always tells you the one thing to
fix. Good light and a plain darker surface under the page help most.

**Scanning.** 300 DPI grayscale is the sweet spot. Higher is slower without
being more accurate.

**Two pages at once.** Photograph an open booklet and ScoreShift saves two
pages, left then right - the rail marks that row *2 pages*. Worth doing on the
flatbed or with plenty of resolution: a spread shares the frame between two
pages, so each gets half the detail. *More options -> Split two-page shots*
turns it off.

**Multi-page pieces.** Add every page in order (Move up / Move down to fix the
order) before you transcribe — the pages are read as one continuous piece, so
repeats and ties carry across page breaks.

**Parts.** The part files come from the parts Audiveris found, so they are only
as good as the page: on a tight piano score where two hands share a staff,
expect one part, not two. Check the part count in the summary line before you
print a set.

**Check the transcription.** OMR is very good, not perfect. On dense or
handwritten music, expect to fix a few things: tick *MusicXML* on export and
open the file in MuseScore before you print.

**Key changes.** *Original key* is filled in from what's written on the page;
override it if the piece is modal or the analysis got it wrong. *Direction*
decides whether the music moves up or down to reach the new key — *Nearest*
keeps it closest to the original range. *Then shift* moves whole octaves if the
result sits badly for the student's hands.

## The API (for the iPhone app later)

The same engine runs as a small JSON service, so a phone can send photographs
and get back a transposed PDF:

```
ScoreShift.exe --server --host 0.0.0.0 --port 8765
```

(Also on the Start menu as *ScoreShift API server*.) Interactive docs at
`/docs`. `GET /v1/scores/{id}/parts` lists the parts, and `?part=2` on the
download returns that part on its own. Set the `SCORESHIFT_API_KEY` environment variable before exposing it
beyond your own machine. **PORTING-iOS.md** has the full plan — what runs on the
phone, what has to stay on a server, and the auto-shutter thresholds worth
carrying over.

## Layout

```
scoreshift/
  engine.py            the whole pipeline, with no UI attached
  config.py            settings, and finding Audiveris / MuseScore
  pipeline/
    pagedetect.py      page finding, perspective, deskew, spreads, clean-up
    staves.py          staff lines: where the music is, how fine, how tilted
    camera.py          live page tracking and the auto-shutter rules
    scanner.py         Windows Image Acquisition (printers and scanners)
    omr.py             Audiveris batch runs, pages -> MusicXML
    score.py           key detection and transposition (music21)
    export.py          PDF / MIDI / MusicXML / scan-PDF writing
  ui/                  Qt window: dashboard shell, crop editor, capture pane
    dash.py            nav rail, headline figures, page cards, panels
    theme.py           the dark palette and the app stylesheet
    icons.py           line icons drawn at runtime, no image files
    widgets.py         the shared buttons, cards and the slide-over panel
  service/             the JSON API
  updater.py           checking GitHub Releases, downloading, verifying
packaging/             PyInstaller spec, Inno Setup script, release script
.github/workflows/     tag a version, get an installer attached to the release
tests/                 229 checks, none of which need a display or a camera
```

Run the tests (from a source checkout; the API tests also want
`pip install -r requirements-dev.txt`):

```
.venv\Scripts\python tests\make_fixture.py     # builds a synthetic photo
.venv\Scripts\python tests\test_pipeline.py    # 41 checks
.venv\Scripts\python tests\test_camera.py      # 29 checks
.venv\Scripts\python tests\test_service.py     # 25 checks
.venv\Scripts\python tests\test_updater.py   # 31 checks
.venv\Scripts\python tests\test_parts.py     # 58 checks
.venv\Scripts\python tests\test_scanner.py   # 44 checks
.venv\Scripts\python tests\test_ui.py          # window smoke test
```

## Releasing

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\release.ps1 -Version 1.7
```

Sets the version in both places that hold it, builds, writes `SHA256SUMS.txt`,
commits, tags and pushes. If a signed-in GitHub CLI is around it publishes the
release with the installer attached; if not, the pushed tag triggers the
workflow, which builds on a Windows runner and attaches the installer itself.
Either way the release ends up with the two files the in-app updater looks for.

## Credits

Note reading by [Audiveris](https://github.com/Audiveris/audiveris). Music
handling by [music21](https://www.music21.org/). Engraving by
[MuseScore](https://musescore.org) or [Verovio](https://www.verovio.org/).
Interface built with Qt (PySide6); image work with OpenCV.

ScoreShift itself is MIT licensed — see [LICENSE](LICENSE), which also lists the
licences of everything it bundles or calls.
