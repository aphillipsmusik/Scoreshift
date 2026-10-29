# Changelog

## 1.8 — 2026-10-29

Frame the shot yourself.

- **Manual zoom in the camera view** - a slider, + and - buttons, the mouse
  wheel, or the + and - keys. Its job is to leave the detector nothing to be
  confused by: on a desk at 1x the page was 19% of the picture and the tray
  was most of the rest; at 2.5x the page is 69% of it and the tray is gone.
- **Drag the picture to move the framed window**, or use the arrow keys, so
  the page does not have to be centred under the camera. The window stops at
  the edge of the picture.
- Where the camera has a zoom of its own, that is used instead of cropping -
  it crops the sensor rather than the finished frame, so the page keeps its
  detail. The tooltip says which one is in use, and the detail reading tells
  you the truth either way: cropping removes distractions, it does not add
  pixels.
- **Zoom buttons on the page view too**, next to Rotate, with Fit to go back -
  the same thing the Ctrl+wheel gesture always did, where it can be seen.

## 1.7 — 2026-10-28

The window rebuilt as a dashboard.

- **What you have is on screen as figures**, not buried in a form: how many
  pages, what key they are going to, and how much detail the worst of them
  has, with a bar underneath showing how the pages divide up between good,
  usable, rough and too far to read.
- **Every page is a card** - its detail in pixels, a plain-words verdict, its
  size and tilt, a thumbnail, and a note when one picture held two pages. The
  card you pick is the page you are working on.
- **A navigation rail** down the left - Pages, Capture, Score, Save,
  Settings - replaces the toolbar, and the title tells you where you are.
- **A summary panel** gathers what was read: key, time signature and bars,
  parts, range, a bar per page showing which will read worst, and what is
  about to be written when you save.
- The controls panel gets out of the way by itself on a narrow window, and the
  whole dashboard scrolls, so the working area always keeps a usable height.

## 1.6 — 2026-10-28

Capturing happens in the window you are already looking at.

- **No second window.** Camera and scanner are a third view in the main
  window, beside Page and Score. The live feed, the pages you have taken and
  the controls are all in one place, and *Add to pages* drops them straight
  into the rail. The scanner's settings - paper source, DPI, colour, how many
  pages - moved into the same strip, so scanning no longer opens anything
  either.
- **The outline tracks the page as you move it.** Each frame's detection is
  blended into the last, so the corners follow the page instead of twitching
  a pixel at a time, and an outline that is lost for a frame or two is held on
  screen rather than flickering out. Moving the page makes it re-measure
  straight away instead of tracking from where the page used to be.
- Everything outside the page dims, the way a document scanner shows what it
  is about to keep, and a dashed line marks where a two-page shot will be cut
  apart.

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
