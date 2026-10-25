# Changelog

## 1.4.1 — 2026-10-25

Individual parts, and a fix that would have broken new installs. (1.4 was
tagged but never published: its build stopped on the test-dependency problem
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
