# Changelog

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
