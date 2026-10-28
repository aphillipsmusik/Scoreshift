"""The scanning half: finding the page, splitting spreads, keeping detail.

These are the checks behind three promises - that the page is found even when
it is lying on something bigger and brighter than itself, that a picture of
two pages becomes two pages, and that ScoreShift says so when the notes are
too small to read rather than quietly handing the reader mush.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scoreshift.engine import CleanOptions, ScoreShiftEngine  # noqa: E402
from scoreshift.pipeline import pagedetect, staves  # noqa: E402
from scoreshift.pipeline.camera import PageTracker, TrackerSettings  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
OUT = Path(__file__).resolve().parent / "out" / "scanner"
OUT.mkdir(parents=True, exist_ok=True)

passed, failed = 0, 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))


def area_fraction(quad: np.ndarray, img: np.ndarray) -> float:
    return float(abs(cv2.contourArea(quad.astype(np.float32)))
                 / (img.shape[0] * img.shape[1]))


def music_inside(quad: np.ndarray, img: np.ndarray) -> float:
    """What share of the staff lines in the picture the quad contains."""
    info = staves.analyse(img)
    if not info.found:
        return 0.0
    h, w = img.shape[:2]
    cover = staves.coverage_mask(
        (int(round(h / info.scale)), int(round(w / info.scale))), info.segments)
    cover = cv2.resize(cover, (w, h), interpolation=cv2.INTER_NEAREST)
    mask = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(mask, quad.astype(np.int32), 255)
    total = float(np.count_nonzero(cover))
    if total <= 0:
        return 0.0
    return float(np.count_nonzero(cover & (mask > 0))) / total


def black_blobs(img: np.ndarray, region) -> int:
    """Count the marker squares in a corner of a cleaned page."""
    y0, y1, x0, x1 = region
    patch = img[y0:y1, x0:x1]
    if patch.size == 0:
        return 0
    dark = (patch < 110).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(dark, 8)
    big = [i for i in range(1, count)
           if stats[i, cv2.CC_STAT_AREA] > 0.004 * patch.size]
    return len(big)


# ---------------------------------------------------------------------------
print("\n== Reading the staff lines ==")
flat = cv2.imread(str(FIX / "page_flat.png"))
info = staves.analyse(flat)
check("staff lines found on an engraved page", info.found, f"{info.lines} lines")
check("their spacing is measured", 12 <= info.interline <= 24,
      f"{info.interline:.1f} px between lines")
check("a flat page reads as level", abs(info.angle) < 0.5,
      f"{info.angle:+.2f} deg")
check("the music is located on the page", info.box is not None
      and info.box[2] > 0.5 * flat.shape[1], str(info.box))

rotated = pagedetect.rotate(flat, -4.0)
spun = staves.analyse(rotated)
check("a rotated page reports its tilt", abs(spun.angle - 4.0) < 1.2,
      f"{spun.angle:+.2f} deg, expected about +4")
check("rotating does not change the spacing",
      abs(spun.interline - info.interline) < 2.0,
      f"{spun.interline:.1f} vs {info.interline:.1f}")

blank = np.full((900, 700, 3), 235, np.uint8)
check("blank paper has no staves", not staves.analyse(blank).found)

# ---------------------------------------------------------------------------
print("\n== Scaling so the notes can be read ==")
small_img = cv2.resize(flat, None, fx=0.35, fy=0.35, interpolation=cv2.INTER_AREA)
fine = staves.analyse(small_img)
check("a shrunken page has staves too close together", fine.too_small_to_read,
      f"{fine.interline:.1f} px")
grown, factor = staves.scale_for_reading(small_img, fine.interline)
check("it is enlarged", factor > 1.2, f"{factor:.2f}x")
after = staves.analyse(grown)
check("to about the spacing the reader wants",
      abs(after.interline - staves.TARGET_INTERLINE) < 6,
      f"{after.interline:.1f} px")
same, factor2 = staves.scale_for_reading(flat, info.interline)
check("a page that is already fine is left alone", factor2 == 1.0
      and same.shape == flat.shape, f"{factor2:.2f}x")
check("an unknown spacing changes nothing",
      staves.scale_for_reading(flat, 0.0)[1] == 1.0)

# ---------------------------------------------------------------------------
print("\n== Finding the page on a crowded desk ==")
tray = cv2.imread(str(FIX / "photo_on_tray.jpg"))
quad = pagedetect.detect_page(tray)
check("a page on a big bright tray is found", quad is not None)
if quad is not None:
    frac = area_fraction(quad, tray)
    check("it is the page, not the tray or the whole picture", frac < 0.45,
          f"{frac:.0%} of the frame")
    held = music_inside(quad, tray)
    check("and it holds the music", held > 0.95, f"{held:.0%} of the staff lines")
    vis = tray.copy()
    cv2.polylines(vis, [quad.astype(int)], True, (0, 0, 255), 4)
    cv2.imwrite(str(OUT / "tray_detected.png"), vis)

tilted = cv2.imread(str(FIX / "photo_p1.jpg"))
quad2 = pagedetect.detect_page(tilted)
check("a tilted photo still works", quad2 is not None
      and 0.2 < area_fraction(quad2, tilted) < 0.85,
      "" if quad2 is None else f"{area_fraction(quad2, tilted):.0%}")
check("a flat scan is left uncropped",
      pagedetect.detect_page(flat) is None)

# ---------------------------------------------------------------------------
print("\n== Two pages in one shot ==")
spread_flat = cv2.imread(str(FIX / "spread_flat.png"))
gutter = pagedetect.find_gutter(spread_flat)
check("the fold is found", gutter is not None,
      f"x={gutter} of {spread_flat.shape[1]}")
if gutter is not None:
    middle = spread_flat.shape[1] / 2
    check("and it is down the middle", abs(gutter - middle) < 0.08 * middle,
          f"{gutter} vs {middle:.0f}")
check("a single page has no fold", pagedetect.find_gutter(flat) is None)

halves = pagedetect.split_spread(spread_flat)
check("a spread splits in two", len(halves) == 2,
      ", ".join(f"{h.shape[1]}x{h.shape[0]}" for h in halves))
check("a single page does not", len(pagedetect.split_spread(flat)) == 1)
if len(halves) == 2:
    check("the halves are about the same width",
          abs(halves[0].shape[1] - halves[1].shape[1]) < 0.1 * halves[0].shape[1])
    check("no music is lost at the fold",
          all(staves.analyse(h).found for h in halves))

pages = pagedetect.process_capture(FIX / "photo_spread.jpg")
check("a photographed spread cleans up into two pages", len(pages) == 2,
      f"{len(pages)} page(s)")
if len(pages) == 2:
    check("they are labelled left and right",
          [p.side for p in pages] == ["left", "right"],
          str([p.side for p in pages]))
    check("both say where they came from",
          all("Two pages in one shot" in " ".join(p.notes) for p in pages))
    for i, page in enumerate(pages, 1):
        cv2.imwrite(str(OUT / f"spread_page{i}.png"), page.image)
    # One marker block on the left page, two on the right.
    marks = []
    for page in pages:
        h, w = page.image.shape[:2]
        marks.append(black_blobs(page.image,
                                 (int(h * 0.82), h, 0, int(w * 0.35))))
    check("and they are in reading order, left page first",
          marks == [1, 2], f"marker blocks found: {marks}")
    check("each half still reads as music",
          all(staves.analyse(p.image, bright_only=False).found for p in pages))

# ---------------------------------------------------------------------------
print("\n== Saying when the notes are too small ==")
pages = pagedetect.process_capture(FIX / "photo_on_tray.jpg")
page = pages[0]
check("a page small in the frame is flagged", not page.readable,
      f"{page.interline:.0f} px between staff lines")
check("and the advice is specific",
      any("Fill more of the frame" in n for n in page.notes),
      "; ".join(page.notes)[:90])
check("it was enlarged anyway, to give the reader something",
      page.enlarged > 1.0, f"{page.enlarged:.2f}x")
check("the enlargement is capped",
      page.image.shape[0] <= pagedetect.MAX_OUTPUT_HEIGHT,
      f"{page.image.shape[1]}x{page.image.shape[0]}")

# ---------------------------------------------------------------------------
print("\n== What the live view says ==")
settings = TrackerSettings(hold_seconds=0.1, cooldown_seconds=0.2)
tracker = PageTracker(settings)
frame = cv2.resize(cv2.imread(str(FIX / "photo_on_tray.jpg")), (1920, 1080))
result = tracker.check(frame)
result = tracker.check(frame)
check("the live view measures the detail too", result.interline > 0,
      f"{result.interline:.0f} px")
check("it outlines the page, not the tray",
      result.quad is not None and area_fraction(result.quad, frame) < 0.5,
      "" if result.quad is None else f"{area_fraction(result.quad, frame):.0%}")
check("a page too far away is not called readable", not result.readable)
check("the verdict is in plain words",
      "px" in PageTracker.detail_verdict(result.interline),
      PageTracker.detail_verdict(result.interline))
check("good detail reads as good",
      "good" in PageTracker.detail_verdict(20.0),
      PageTracker.detail_verdict(20.0))
check("nothing measured, nothing said", PageTracker.detail_verdict(0.0) == "")

# ---------------------------------------------------------------------------
print("\n== Through the engine ==")
engine = ScoreShiftEngine(workspace=OUT / "work")
cleaned = engine.clean_pages([FIX / "photo_spread.jpg"], CleanOptions(),
                             log=lambda m: None)
check("one picture of a spread becomes two pages", len(cleaned) == 2,
      f"{len(cleaned)} page(s)")
check("each knows which side it was", [c.side for c in cleaned] == ["left", "right"])
check("and carries the detail measurement",
      all(c.interline > 0 for c in cleaned),
      ", ".join(f"{c.interline:.0f}px" for c in cleaned))
single = engine.clean_pages([FIX / "photo_p1.jpg"], CleanOptions(),
                            log=lambda m: None)
check("a single page stays one page", len(single) == 1)
off = engine.clean_pages([FIX / "photo_spread.jpg"],
                         CleanOptions(split_spreads=False), log=lambda m: None)
check("splitting can be switched off", len(off) == 1)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
