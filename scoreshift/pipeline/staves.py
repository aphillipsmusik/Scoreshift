"""Finding staff lines, and what they tell us about a picture of music.

Staff lines are the one thing on a page of music that nothing else on a desk
looks like: a dozen long, thin, parallel, evenly-spaced dark lines. Nearly
everything the scanner needs to know can be read off them:

* **where the music is** - the lines cluster on the page and nowhere else, so
  they find the page even when it is lying on something bright and rectangular
  that would otherwise win a "biggest quad" contest;
* **how far it is rotated** - their shared angle;
* **where two pages meet** - a column with no lines crossing it;
* **whether the notes are big enough to read** - the gap between adjacent
  lines, which is the unit Audiveris measures everything else in.

Everything here works on a grayscale image and is cheap enough to run on every
frame of a camera preview.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

# Audiveris sizes its symbols relative to the staff interline distance; below
# about 14 px it starts missing things, and there is nothing to gain above
# roughly 40. 20 is comfortably inside its happy range.
TARGET_INTERLINE = 20.0
MIN_GOOD_INTERLINE = 14.0
# Measured in the camera's own pixels, before anything is enlarged. Below
# this the detail simply is not in the photograph, and enlarging it only
# makes bigger, blurrier notes.
MIN_OPTICAL_INTERLINE = 12.0
MAX_USEFUL_INTERLINE = 44.0
MAX_UPSCALE = 3.5

ANALYSIS_WIDTH = 2000          # staff lines vanish if we look at them smaller than this
MAX_TILT_DEG = 20.0


@dataclass
class StaffInfo:
    """What the staff lines in a picture say about it."""
    lines: int = 0                       # how many staff-line segments
    interline: float = 0.0               # px between adjacent lines
    angle: float = 0.0                   # degrees, + = clockwise on screen
    box: tuple[int, int, int, int] | None = None   # x, y, w, h of the music
    scale: float = 1.0                   # analysis px -> source px
    segments: np.ndarray = field(default_factory=lambda: np.zeros((0, 4)))

    @property
    def found(self) -> bool:
        # Two staves of five lines is the least a page of music can show.
        return self.lines >= 8 and self.interline > 0

    @property
    def too_small_to_read(self) -> bool:
        return self.found and self.interline < MIN_GOOD_INTERLINE

    def at_scale(self, factor: float) -> "StaffInfo":
        """The same findings, measured on a picture ``factor`` times the size.

        Lets an expensive analysis of a full-resolution frame be reused
        against a cheap, shrunken copy of it.
        """
        if factor == 1.0:
            return self
        return StaffInfo(
            lines=self.lines,
            interline=self.interline * factor,
            angle=self.angle,
            box=tuple(int(round(v * factor)) for v in self.box) if self.box else None,
            # segments stay in analysis pixels, so the step out to source
            # pixels grows with the picture
            scale=self.scale * factor,
            segments=self.segments,
        )


# ---------------------------------------------------------------------------
# Ink
# ---------------------------------------------------------------------------

def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def ink_mask(img: np.ndarray, bright_only: bool = True) -> np.ndarray:
    """Pixels that are dark *relative to the paper around them*.

    Dividing by a heavily blurred copy makes this independent of how the
    picture is lit, which matters: under a warm desk lamp the paper is not
    white and a global threshold picks the lamp's favourites rather than the
    ink. ``bright_only`` additionally throws away marks whose surroundings are
    dark - a black mouse mat is not paper, however much detail it has.
    """
    gray = to_gray(img).astype(np.float32)
    background = cv2.GaussianBlur(gray, (0, 0), max(3.0, gray.shape[1] / 48))
    relative = gray / np.maximum(background, 1.0)
    mask = relative < 0.90
    if bright_only:
        paper_level = float(np.percentile(gray, 98))
        mask &= background > 0.55 * paper_level
    return (mask * 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Staff lines
# ---------------------------------------------------------------------------

def staff_segments(ink: np.ndarray, min_len_frac: float = 0.035,
                   max_tilt: float = MAX_TILT_DEG) -> np.ndarray:
    """Near-horizontal line segments in an ink mask, as (N, 4) x1 y1 x2 y2."""
    width = ink.shape[1]
    min_len = max(12, int(min_len_frac * width))
    lines = cv2.HoughLinesP(ink, 1, np.pi / 720,
                            threshold=max(18, min_len // 2),
                            minLineLength=min_len, maxLineGap=4)
    if lines is None:
        return np.zeros((0, 4), dtype=np.int32)
    # OpenCV 4 returns (N, 1, 4); OpenCV 5 returns (N, 4).
    segs = np.asarray(lines).reshape(-1, 4)
    dx = segs[:, 2].astype(np.float32) - segs[:, 0]
    dy = segs[:, 3].astype(np.float32) - segs[:, 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        angles = np.degrees(np.arctan2(dy, np.where(np.abs(dx) < 1, np.nan, dx)))
    keep = np.isfinite(angles) & (np.abs(angles) <= max_tilt)
    return segs[keep].astype(np.int32)


def segment_angle(segs: np.ndarray) -> float:
    """The tilt the segments agree on, long segments counting for more."""
    if len(segs) == 0:
        return 0.0
    dx = segs[:, 2].astype(np.float64) - segs[:, 0]
    dy = segs[:, 3].astype(np.float64) - segs[:, 1]
    lengths = np.hypot(dx, dy)
    angles = np.degrees(np.arctan2(dy, dx))
    order = np.argsort(angles)
    angles, lengths = angles[order], lengths[order]
    cumulative = np.cumsum(lengths)
    if cumulative[-1] <= 0:
        return 0.0
    middle = np.searchsorted(cumulative, cumulative[-1] / 2.0)
    return float(angles[min(middle, len(angles) - 1)])


def coverage_mask(shape: tuple[int, int], segs: np.ndarray,
                  thickness: int = 3) -> np.ndarray:
    cover = np.zeros(shape[:2], np.uint8)
    for x1, y1, x2, y2 in segs:
        cv2.line(cover, (int(x1), int(y1)), (int(x2), int(y2)), 255, thickness)
    return cover


def measure_interline(ink: np.ndarray, box: tuple[int, int, int, int] | None = None,
                      samples: int = 120) -> float:
    """The distance between adjacent staff lines, in pixels.

    Measured the way OMR engines do it: walk down sampled columns of the ink
    mask, record the length of every run of ink and every gap between runs,
    and take the *most common* value of each. A page of music is built from
    one repeated distance - four identical gaps inside every staff, on every
    system - so the mode is sharp, where a median would be dragged down by
    note heads and text. Interline is one gap plus one line thickness.
    """
    x, y, w, h = box if box else (0, 0, ink.shape[1], ink.shape[0])
    x, y = max(0, x), max(0, y)
    region = ink[y:y + h, x:x + w]
    if region.size == 0 or region.shape[0] < 12:
        return 0.0

    limit = max(4, region.shape[0] // 6)
    gap_hist = np.zeros(limit + 1, dtype=np.int64)
    ink_hist = np.zeros(limit + 1, dtype=np.int64)
    columns = np.linspace(0, region.shape[1] - 1,
                          min(samples, region.shape[1])).astype(int)
    for cx in columns:
        column = region[:, cx] > 0
        if column.sum() < 3:
            continue
        # Run-length encode the column.
        changes = np.flatnonzero(np.diff(column.astype(np.int8))) + 1
        bounds = np.concatenate(([0], changes, [len(column)]))
        lengths = np.diff(bounds)
        is_ink = column[bounds[:-1]]
        for length, inked in zip(lengths, is_ink):
            if 1 <= length <= limit:
                (ink_hist if inked else gap_hist)[length] += 1

    if gap_hist.sum() < 12:
        return 0.0
    # Ignore single-pixel gaps: speckle, not staves.
    gap_hist[:2] = 0
    if gap_hist.sum() < 12:
        return 0.0
    gap = int(np.argmax(gap_hist))
    thickness = int(np.argmax(ink_hist)) if ink_hist.sum() else 1
    thickness = max(1, min(thickness, max(1, gap // 2)))
    return float(gap + thickness)


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------

def analyse(img: np.ndarray, bright_only: bool = True) -> StaffInfo:
    """Find the staff lines in a picture and what they imply."""
    gray = to_gray(img)
    h, w = gray.shape[:2]
    scale = ANALYSIS_WIDTH / float(w) if w > ANALYSIS_WIDTH else 1.0
    small = (cv2.resize(gray, None, fx=scale, fy=scale,
                        interpolation=cv2.INTER_AREA) if scale < 1.0 else gray)

    ink = ink_mask(small, bright_only=bright_only)
    segs = staff_segments(ink)
    if len(segs) == 0:
        return StaffInfo(scale=1.0 / scale if scale else 1.0)

    cover = coverage_mask(small.shape, segs)
    box = music_box(cover, segs)
    if box is not None:
        # Keep only the lines that belong to the music, dropping the shelf
        # edges, cables and keyboard rims that are also horizontal. Angle and
        # spacing are then measured on staves alone.
        segs = segments_inside(segs, box)
    interline = measure_interline(ink, box)
    return StaffInfo(
        lines=len(segs),
        interline=interline / scale if scale else interline,
        angle=segment_angle(segs),
        box=tuple(int(round(v / scale)) for v in box) if box else None,
        scale=1.0 / scale if scale else 1.0,
        segments=segs,
    )


def segments_inside(segs: np.ndarray, box: tuple[int, int, int, int],
                    slack: float = 0.04) -> np.ndarray:
    """Those segments whose middle falls inside the box."""
    if len(segs) == 0:
        return segs
    x, y, w, h = box
    pad_x, pad_y = slack * w, slack * h
    mx = (segs[:, 0] + segs[:, 2]) / 2.0
    my = (segs[:, 1] + segs[:, 3]) / 2.0
    keep = ((mx >= x - pad_x) & (mx <= x + w + pad_x)
            & (my >= y - pad_y) & (my <= y + h + pad_y))
    return segs[keep]


def music_box(cover: np.ndarray, segs: np.ndarray) -> tuple[int, int, int, int] | None:
    """The patch of the picture the staff lines live in.

    Staff lines are blurred together into regions and the busiest region wins,
    so a few stray horizontal edges elsewhere in the picture - a shelf, a
    keyboard, the edge of a laptop - cannot drag the answer away from the
    music.
    """
    if len(segs) == 0:
        return None
    h, w = cover.shape[:2]
    # Wide enough to join the systems down a page into one region, narrow
    # enough not to reach across a desk to the next bright thing.
    span = max(9, int(0.10 * w)) | 1
    density = cv2.blur((cover > 0).astype(np.float32), (span, span))
    peak = float(density.max())
    if peak <= 0:
        return None
    blob = (density > 0.18 * peak).astype(np.uint8) * 255
    count, labels, stats, _ = cv2.connectedComponentsWithStats(blob, 8)
    if count < 2:
        return None
    best_label, best_mass = 0, -1.0
    for label in range(1, count):
        mass = float(np.count_nonzero((labels == label) & (cover > 0)))
        if mass > best_mass:
            best_label, best_mass = label, mass
    inside = (labels == best_label) & (cover > 0)
    ys, xs = np.nonzero(inside)
    if len(xs) == 0:
        return None
    pad = max(2, int(0.01 * w))
    x0, x1 = max(0, xs.min() - pad), min(w - 1, xs.max() + pad)
    y0, y1 = max(0, ys.min() - pad), min(h - 1, ys.max() + pad)
    return int(x0), int(y0), int(x1 - x0 + 1), int(y1 - y0 + 1)


# ---------------------------------------------------------------------------
# Scaling for the note reader
# ---------------------------------------------------------------------------

def scale_for_reading(img: np.ndarray, interline: float,
                      target: float = TARGET_INTERLINE,
                      max_factor: float = MAX_UPSCALE) -> tuple[np.ndarray, float]:
    """Enlarge a page whose staves are too fine for the note reader.

    Returns the image and the factor applied (1.0 when nothing was needed).
    Enlarging cannot invent detail, but it does let the reader's own filters
    resolve note heads and stems that otherwise fall between pixels, and it is
    the difference between a page being read and being refused.
    """
    if interline <= 0:
        return img, 1.0
    if interline >= MIN_GOOD_INTERLINE:
        if interline <= MAX_USEFUL_INTERLINE:
            return img, 1.0
        factor = MAX_USEFUL_INTERLINE / interline      # pointlessly large
    else:
        factor = min(target / interline, max_factor)
    if 0.95 < factor < 1.05:
        return img, 1.0

    interpolation = cv2.INTER_CUBIC if factor > 1 else cv2.INTER_AREA
    out = cv2.resize(img, None, fx=factor, fy=factor, interpolation=interpolation)
    if factor > 1:
        # Enlarging softens edges; put the bite back so thin lines stay thin.
        blurred = cv2.GaussianBlur(out, (0, 0), 1.2)
        out = cv2.addWeighted(out, 1.55, blurred, -0.55, 0)
    return out, float(factor)
