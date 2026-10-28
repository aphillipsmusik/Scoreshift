# Changelog

## 1.5 — 2026-10-28

The scanning half, rebuilt around the one thing on a page that nothing else
looks like: staff lines.

- **The page is found by its music.** The old detector hunted for the
  biggest, straightest, brightest four-sided thing in the picture - which on
  a real desk is the tray, the mat, the laptop or the whole frame, not the
  sheet lying on it. It now finds the staff lines first, grows out from them
  to the edge of the paper, and picks the tightest outline that still holds
  all the music. The live camera view does the same, so the outline you see
  is on the page.
- **Two pages in one shot become two pages.** An open booklet or two sheets
  side by side are split down the fold - which is found by looking for the
  column no staff crosses - and come out in reading order, each straightened
  on its own. *More options → Split two-page shots* turns it off.
- **It tells you when the notes are too small to read.** Staff spacing is
  measured in the camera's own pixels and reported live: *Detail 18 px -
  good*, down to *too far to read*. Pages whose staves are fine but small are
  enlarged and sharpened for the note reader; pages where the detail was
  never captured say so instead of quietly producing a bad transcription.
  The shutter holds when there is nothing readable in frame.
- **The camera opens at its best resolution** rather than a fixed 1080p, and
  the quality menu says which one it picked. Detail is the whole game for
  reading music: a full page across a 720p frame gives staff lines about 3 px
  apart, where the reader wants 14.
- Straightening now uses the staves themselves rather than a general-purpose
  edge estimate, which roughly halves the leftover tilt.
- 44 new checks covering all of this, including a page photographed on a
  large bright tray - the case that started it.

## 1.4.2 — 2026-10-25

Individual parts, and a fix that would have broken new installs. (1.4 and 1.4.1 were
tagged but never published: those builds stopped in CI, on the problems
fixed below.)

- **Each part in its own file.** Tick *Each of the N parts in its own file as
  well* under More options and, alongside the full score, ScoreShift saves one
  PDF and one MIDI per part — named after the parts on the page, so a trumpet
  player gets *Fanfare - Trumpet in Bb.pdf* to read from. The part's name is
  printed under the title, and the full score is still saved as before.
- Parts with no name on the page fall back to their instrument, then to their
  position, and two parts with the same name are numbered, so no two files ever
  collide. The option is only offered when a piece actually has more than one
  part.
- **The API serves parts too**, for the phone app later: `GET
  /v1/scores/{id}/parts` lists them, and `?part=2` on the download hands back
  that part alone as PDF, MIDI or MusicXML.
- The window smoke test prints key names with arrows in them without tripping
  over a Windows console's default encoding.
- The test suite no longer assumes the machine running it has no note-reading
  engine installed, so it passes on a machine with Audiveris as well as on a
  bare build runner.
- **Fixed:** staff-line deskew crashed on OpenCV 5, which pip now installs by
  default — every page clean-up would have failed in a build made from today's
  dependencies. It handles both OpenCV 4 and 5 now.

## 1.3 — 2026-09-12

A redesigned window, and updates that arrive on their own.

- **New interface.** Two panes — a page rail and a full-height canvas — with
  clean-up, key and save controls in a panel that slides in from the right
  (Ctrl+B). Dark throughout; the page and score previews keep white paper so the
  music still reads.
- The options most people never touch — binarize, transpose direction, octave
  shift, enharmonic spelling, MusicXML, scan PDF, file name, output folder — now
  sit behind **More options**.
- The top-bar button **becomes the next step**: Add pages → Transcribe → Change
  the key → Save files, with a hint beside it, and anything that can't act yet is
  disabled rather than failing when pressed.
- **Drop pages onto the window**, and an empty canvas that offers the three ways
  in rather than showing nothing.
- **Updates.** ScoreShift checks its GitHub releases once a day, shows what
  changed, and installs the new version on request — verifying the download
  against the SHA-256 published with the release before running it. Turn the
  check off in Settings.

## 1.2 — 2026-09-06

First public release.

- Capture pages from a **webcam** with live page detection and an auto-shutter
  that fires when the page is steady, sharp and well framed; space bar for a
  manual shot.
- **Scan** straight from a printer over WIA (flatbed or document feeder).
- Or drop in **photos** from a phone.
- Page clean-up: perspective correction, deskew taken from the staff lines
  themselves, flat-field lighting correction. Corners draggable by hand.
- Note reading with **Audiveris**, pages handed over as one continuous book.
- **Transposition** to a key, by half steps, or for a transposing instrument,
  with enharmonic simplification and diatonic spelling.
- Export **PDF**, **MIDI**, **MusicXML**, and the cleaned scan as a PDF.
- A JSON **API** (`ScoreShift.exe --server`) that runs the same engine, for the
  iOS client to come.
- Windows installer with Python bundled; offers to install Audiveris and Java.
