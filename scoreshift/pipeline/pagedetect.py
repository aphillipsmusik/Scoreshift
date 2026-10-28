"""Page detection, perspective correction, deskew and cleanup.

Turns a phone photo (or a raw scan) of a sheet of music into a flat,
high-contrast page image that an OMR engine can read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import staves

# A4 / Letter are both close to 1.29-1.41 tall:wide.  Anything wildly
# outside this range is probably not a page.
MIN_PAGE_AREA_RATIO = 0.12
DETECT_MAX_DIM = 1400
MIN_OUTPUT_HEIGHT = 1800
MAX_OUTPUT_HEIGHT = 4200


@dataclass
class PageResult:
    image: np.ndarray                      # cleaned, deskewed page (grayscale or BGR)
    quad: np.ndarray | None = None         # 4x2 corners in ORIGINAL image coords
    detected: bool = False
    skew_deg: float = 0.0
    notes: list[str] = field(default_factory=list)
    interline: float = 0.0                 # px between staff lines, 0 = unknown
    enlarged: float = 1.0                  # how much it was scaled up to be readable
    side: str = ""                         # "left"/"right" when a spread was split
    readable: bool = True                  # are the staves big enough to read


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def imread(path: str | Path) -> np.ndarray:
    """Read an image, tolerating non-ASCII paths on Windows."""
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not read image: {path}")
    return img


def imwrite(path: str | Path, img: np.ndarray) -> None:
    path = Path(path)
    ext = path.suffix or ".png"
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        raise ValueError(f"Could not encode image for {path}")
    buf.tofile(str(path))


# ---------------------------------------------------------------------------
# Corner helpers
# ---------------------------------------------------------------------------

def order_quad(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    out = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    out[0] = pts[np.argmin(s)]     # tl
    out[2] = pts[np.argmax(s)]     # br
    out[1] = pts[np.argmin(d)]     # tr
    out[3] = pts[np.argmax(d)]     # bl
    return out


def _quad_score(quad: np.ndarray, frame_area: float,
                min_area_ratio: float = MIN_PAGE_AREA_RATIO) -> float:
    """Prefer big, convex, roughly rectangular quads."""
    area = abs(cv2.contourArea(quad.astype(np.float32)))
    if area < frame_area * min_area_ratio:
        return -1.0
    if not cv2.isContourConvex(quad.astype(np.int32)):
        return -1.0
    o = order_quad(quad)
    widths = [np.linalg.norm(o[1] - o[0]), np.linalg.norm(o[2] - o[3])]
    heights = [np.linalg.norm(o[3] - o[0]), np.linalg.norm(o[2] - o[1])]
    if min(widths) < 1 or min(heights) < 1:
        return -1.0
    # penalise opposite sides of very different length (extreme perspective)
    balance = (min(widths) / max(widths)) * (min(heights) / max(heights))
    aspect = max(heights) / max(widths)
    aspect_ok = 1.0 if 0.55 <= aspect <= 2.2 else 0.35
    return (area / frame_area) * balance * aspect_ok


# ---------------------------------------------------------------------------
# Paper
# ---------------------------------------------------------------------------

def _mean_in(gray: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> float:
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(gray.shape[1], x1), min(gray.shape[0], y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return float(gray[y0:y1, x0:x1].mean())


def expand_to_paper(gray: np.ndarray, box: tuple[int, int, int, int],
                    margin: int = 0,
                    tolerance: float = 0.90,
                    max_grow: float = 0.35) -> tuple[int, int, int, int]:
    """Grow a box outwards from the music until the paper runs out.

    Starting from the staff lines - which are certainly on paper - the box
    first opens by ``margin`` on every side, so that a system the line finder
    clipped is still inside, and then each side creeps outwards a strip at a
    time, stopping when that strip is no longer as bright as the page. That
    finds the sheet without it having to be the biggest rectangle in the
    picture, which is exactly what goes wrong when a page is photographed on
    a desk: the desk, the tray and the laptop are all bigger and all more
    rectangular than a sheet of music.

    Where the page and whatever it is lying on are equally bright, growth
    stops at ``margin`` rather than wandering off across the furniture.
    """
    x, y, w, h = box
    region = gray[max(0, y):y + h, max(0, x):x + w]
    if region.size == 0:
        return box
    paper = float(np.percentile(region, 75))      # the page, not its ink
    if paper <= 0:
        return box

    x, y = x - margin, y - margin
    w, h = w + 2 * margin, h + 2 * margin
    step = max(2, int(0.006 * max(gray.shape[:2])))
    limit_x, limit_y = int(max_grow * w), int(max_grow * h)

    def strip_level(x0: int, y0: int, x1: int, y1: int) -> float:
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(gray.shape[1], x1), min(gray.shape[0], y1)
        if x1 <= x0 or y1 <= y0:
            return -1.0
        return float(np.median(gray[y0:y1, x0:x1]))

    def creep(side: str) -> None:
        """Walk one side outwards while the paper keeps going.

        Each strip is judged against the strip just inside it rather than
        against a fixed level, so a page lit brightly at one end and dimly at
        the other is followed all the way, while the step down at its edge -
        to a desk, a shadow, a dark mat - stops it at once.
        """
        nonlocal x, y, w, h
        grown = 0
        limit = limit_x if side in ("left", "right") else limit_y
        reference = -1.0
        while grown < limit:
            if side == "left":
                if x - step < 0:
                    return
                level = strip_level(x - step, y, x, y + h)
            elif side == "right":
                if x + w + step > gray.shape[1]:
                    return
                level = strip_level(x + w, y, x + w + step, y + h)
            elif side == "top":
                if y - step < 0:
                    return
                level = strip_level(x, y - step, x + w, y)
            else:
                if y + h + step > gray.shape[0]:
                    return
                level = strip_level(x, y + h, x + w, y + h + step)
            if level < 0:
                return
            if reference < 0:
                reference = max(level, paper * 0.5)
            # A sharp step down is an edge; a slow fade is just the light
            # falling off across the sheet. Neither may take us far below
            # what the page itself measured.
            if level < 0.93 * reference or level < 0.62 * paper:
                return
            reference = 0.7 * reference + 0.3 * level
            if side == "left":
                x -= step; w += step
            elif side == "right":
                w += step
            elif side == "top":
                y -= step; h += step
            else:
                h += step
            grown += step

    for side in ("left", "right", "top", "bottom"):
        creep(side)

    x, y = max(0, x), max(0, y)
    w = min(w, gray.shape[1] - x)
    h = min(h, gray.shape[0] - y)
    return int(x), int(y), int(w), int(h)


def quad_from_box(box: tuple[int, int, int, int], angle: float = 0.0) -> np.ndarray:
    """Box (plus the tilt the staves show) as four corners."""
    x, y, w, h = box
    centre = (x + w / 2.0, y + h / 2.0)
    rect = (centre, (float(w), float(h)), float(angle))
    return order_quad(cv2.boxPoints(rect).astype(np.float32))


def _staff_recall(quad: np.ndarray, cover: np.ndarray, scale: float) -> float:
    """How much of the music in the frame this quad actually contains."""
    total = float(np.count_nonzero(cover))
    if total <= 0:
        return 0.0
    mask = np.zeros(cover.shape, np.uint8)
    cv2.fillConvexPoly(mask, (quad * scale).astype(np.int32), 255)
    return float(np.count_nonzero(cover & (mask > 0))) / total


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _is_already_a_page(gray: np.ndarray) -> bool:
    """Is this a flat scan rather than a photograph of a page on something?

    A scan has paper right out to its edges; a photo has a desk, a shadow or a
    tray there. Cropping a scan can only lose music, so it is left alone.
    """
    h, w = gray.shape[:2]
    band = max(2, int(0.015 * max(h, w)))
    border = np.concatenate([
        gray[:band, :].ravel(), gray[-band:, :].ravel(),
        gray[:, :band].ravel(), gray[:, -band:].ravel(),
    ])
    brightest = float(np.percentile(gray, 95))
    edge = float(np.median(border))
    # Paper all the way to the edges, and actually bright - not a dim, even
    # background with a small page sitting in the middle of it.
    return edge > 110 and brightest > 0 and edge >= 0.88 * brightest


def detect_page(bgr: np.ndarray,
                min_area_ratio: float = MIN_PAGE_AREA_RATIO,
                info: "staves.StaffInfo | None" = None) -> np.ndarray | None:
    """Find the page outline. Returns 4x2 corners in original coords, or None.

    Two kinds of evidence, in order of how much they are trusted:

    1. **The music itself.** Staff lines are unmistakable and they are only
       ever on the page, so the patch of picture they occupy - grown outwards
       until the paper ends - is the page. This is what stops the detector
       choosing a bright tray, a laptop lid or the whole frame, all of which
       beat a sheet of paper at being large and rectangular.
    2. **Page-shaped outlines**, the classic approach, used when the staves
       are too small or too faint to see - typically because the camera is too
       far away, which the live view complains about separately.

    ``min_area_ratio`` is how much of the frame the page must fill to count.
    The live camera passes a smaller value so it can track a page that is
    still too far away and tell you to move closer.
    """
    h, w = bgr.shape[:2]
    scale = DETECT_MAX_DIM / max(h, w)
    if scale < 1.0:
        small = cv2.resize(bgr, None, fx=scale, fy=scale,
                           interpolation=cv2.INTER_AREA)
    else:
        scale = 1.0
        small = bgr.copy()

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 9, 60, 60)
    frame_area = float(gray.shape[0] * gray.shape[1])

    if _is_already_a_page(gray):
        return None

    # --- the music ------------------------------------------------------
    if info is None:
        info = staves.analyse(bgr)
    staff_quad = None
    cover_small = None
    if info.found and info.box:
        box = tuple(int(round(v * scale)) for v in info.box)
        # One staff height of slack, so a system the line finder clipped at
        # the top or bottom of the page is still inside the crop.
        margin = int(round(max(6.0 * info.interline * scale, 0.02 * box[2])))
        grown = expand_to_paper(gray, box, margin=margin)
        staff_quad = quad_from_box(grown, info.angle)
        cover_small = cv2.resize(
            staves.coverage_mask(
                (int(round(h / info.scale)), int(round(w / info.scale))),
                info.segments),
            (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_NEAREST)

    candidates: list[np.ndarray] = []

    # --- Strategy 1: edge map -> polygon approximation -------------------
    med = float(np.median(gray))
    lo = int(max(0, 0.66 * med))
    hi = int(min(255, 1.33 * med))
    edges = cv2.Canny(gray, lo, hi)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=1)
    edges = cv2.erode(edges, np.ones((3, 3), np.uint8), iterations=1)
    cnts, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:12]:
        peri = cv2.arcLength(c, True)
        for eps in (0.02, 0.03, 0.05):
            approx = cv2.approxPolyDP(c, eps * peri, True)
            if len(approx) == 4:
                candidates.append(approx.reshape(4, 2).astype(np.float32))
                break

    # --- Strategy 2: bright-page segmentation ----------------------------
    blur = cv2.GaussianBlur(gray, (0, 0), 3)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    cnts2, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(cnts2, key=cv2.contourArea, reverse=True)[:5]:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4:
            candidates.append(approx.reshape(4, 2).astype(np.float32))
        else:
            box = cv2.boxPoints(cv2.minAreaRect(c))
            candidates.append(box.astype(np.float32))

    # --- Strategy 3: the brightest thing in the picture ------------------
    # Paper is usually the brightest large surface, even when whatever it is
    # lying on is bright too - a tray, a laptop, a light desk - because it is
    # the thing the light is bouncing straight off.
    bright = (gray > np.percentile(gray, 88)).astype(np.uint8) * 255
    bright = cv2.morphologyEx(
        bright, cv2.MORPH_CLOSE,
        np.ones((max(9, int(0.02 * gray.shape[1])) | 1,) * 2, np.uint8))
    cnts3, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(cnts3, key=cv2.contourArea, reverse=True)[:4]:
        # A rectangle around the bright patch, not its exact silhouette: the
        # patch is a sheet of paper with its corners softened by shadow.
        candidates.append(cv2.boxPoints(cv2.minAreaRect(c)).astype(np.float32))

    scored: list[tuple[float, float, np.ndarray]] = []   # score, area, quad
    best, best_score = None, 0.0
    for q in candidates:
        score = _quad_score(q, frame_area, min_area_ratio)
        if score <= 0:
            continue
        area = abs(cv2.contourArea(q.astype(np.float32)))
        if cover_small is not None:
            recall = _staff_recall(q, cover_small, 1.0)
            density = recall / max(area / frame_area, 1e-6)
            # Containing the music matters; wrapping it tightly matters more.
            score *= 0.25 + recall * min(density, 4.0)
            scored.append((recall, area, q))
        if score > best_score:
            best, best_score = q, score

    if staff_quad is not None:
        staff_score = _quad_score(staff_quad, frame_area, min_area_ratio * 0.5)
        if staff_score > 0:
            staff_recall = _staff_recall(staff_quad, cover_small, 1.0)
            staff_area = abs(cv2.contourArea(staff_quad.astype(np.float32)))
            scored.append((staff_recall, staff_area, staff_quad))
            if best is None:
                best = staff_quad

        # When the music has been found, the page is the smallest shape that
        # still holds essentially all of it. Without this a desk, a tray or
        # the whole frame wins simply by being bigger and no less
        # rectangular - which is exactly how a page detector ends up
        # outlining the furniture.
        if scored:
            reachable = max(entry[0] for entry in scored)
            keep = max(0.85 * reachable, 0.6)
            holds_it_all = [entry for entry in scored if entry[0] >= keep]
            if holds_it_all:
                best = min(holds_it_all, key=lambda entry: entry[1])[2]

    if best is None:
        return None

    o = order_quad(best)
    margin = 0.012 * max(gray.shape[:2])
    hits_border = (
        o[:, 0].min() < margin
        and o[:, 1].min() < margin
        and o[:, 0].max() > gray.shape[1] - margin
        and o[:, 1].max() > gray.shape[0] - margin
    )
    covers = abs(cv2.contourArea(o.astype(np.float32))) / frame_area
    if hits_border and covers > 0.93:
        # Already a flat scan filling the frame: no crop needed.
        return None
    if covers > 0.92 and staff_quad is None:
        # Nothing page-like, nothing musical, and it swallows the picture:
        # this is the scene, not a page.
        return None

    return (o / scale).astype(np.float32)


# ---------------------------------------------------------------------------
# Warp
# ---------------------------------------------------------------------------

def warp_quad(bgr: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Flatten the quad to a rectangle, correcting perspective."""
    o = order_quad(quad)
    wa = np.linalg.norm(o[2] - o[3])
    wb = np.linalg.norm(o[1] - o[0])
    ha = np.linalg.norm(o[1] - o[2])
    hb = np.linalg.norm(o[0] - o[3])
    out_w = int(round(max(wa, wb)))
    out_h = int(round(max(ha, hb)))
    if out_w < 10 or out_h < 10:
        return bgr

    # Keep resolution sane for OMR: enough pixels per staff, not absurdly big.
    if out_h < MIN_OUTPUT_HEIGHT:
        f = MIN_OUTPUT_HEIGHT / out_h
        out_w, out_h = int(out_w * f), MIN_OUTPUT_HEIGHT
    elif out_h > MAX_OUTPUT_HEIGHT:
        f = MAX_OUTPUT_HEIGHT / out_h
        out_w, out_h = int(out_w * f), MAX_OUTPUT_HEIGHT

    dst = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
        dtype=np.float32,
    )
    m = cv2.getPerspectiveTransform(o, dst)
    return cv2.warpPerspective(
        bgr, m, (out_w, out_h), flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


# ---------------------------------------------------------------------------
# Two pages in one shot
# ---------------------------------------------------------------------------

MIN_SPREAD_ASPECT = 1.05          # wider than this and it might be two pages


def find_gutter(img: np.ndarray,
                info: "staves.StaffInfo | None" = None) -> int | None:
    """The x where two pages meet, or None if this is a single page.

    Looks for a column band in the middle of the picture that no staff
    crosses, with real music on both sides of it. An open booklet has one;
    a single page does not, however wide it is, because its staves run the
    whole way across.
    """
    h, w = img.shape[:2]
    if w < MIN_SPREAD_ASPECT * h:
        return None
    if info is None:
        info = staves.analyse(img)
    if not info.found:
        return None

    cover = staves.coverage_mask(
        (int(round(h / info.scale)), int(round(w / info.scale))), info.segments)
    profile = cover.sum(axis=0).astype(np.float32)
    if profile.max() <= 0:
        return None
    smooth = cv2.GaussianBlur(profile.reshape(1, -1), (0, 0), 3).ravel()
    cw = len(smooth)
    busy = float(np.median(smooth[smooth > 0])) if np.any(smooth > 0) else 0.0
    if busy <= 0:
        return None

    # Judge the dip against the music either side of it rather than against
    # the page as a whole: a few lines that bridge the fold - the edge of the
    # sheet, a shadow - keep the gutter from ever reading as truly empty.
    left_busy = float(np.median(smooth[int(0.05 * cw):int(0.30 * cw)]))
    right_busy = float(np.median(smooth[int(0.70 * cw):int(0.95 * cw)]))
    flank = min(left_busy, right_busy)
    if flank <= 0:
        return None
    threshold = min(0.45 * flank, 0.35 * busy)

    lo, hi = int(0.30 * cw), int(0.70 * cw)
    middle = smooth[lo:hi]
    quiet = middle < threshold
    if not quiet.any():
        return None

    # The widest quiet band in the middle of the picture.
    best_start, best_len, run_start = -1, 0, None
    for i, is_quiet in enumerate(quiet):
        if is_quiet and run_start is None:
            run_start = i
        elif not is_quiet and run_start is not None:
            if i - run_start > best_len:
                best_start, best_len = run_start, i - run_start
            run_start = None
    if run_start is not None and len(quiet) - run_start > best_len:
        best_start, best_len = run_start, len(quiet) - run_start
    if best_len < max(3, int(0.004 * cw)) or best_len > 0.3 * cw:
        return None

    split = lo + best_start + best_len // 2
    left_mass = float(smooth[:split].sum())
    right_mass = float(smooth[split:].sum())
    total = left_mass + right_mass
    if total <= 0:
        return None
    # Both halves must hold a real share of the music, or this is one page
    # with a wide margin rather than two pages.
    if min(left_mass, right_mass) / total < 0.28:
        return None
    return int(round(split * info.scale))


def split_spread(img: np.ndarray, info: "staves.StaffInfo | None" = None,
                 ) -> list[np.ndarray]:
    """One picture of two pages becomes two pictures, left page first."""
    gutter = find_gutter(img, info)
    if gutter is None:
        return [img]
    h, w = img.shape[:2]
    # Leave the fold itself out of both halves: it is shadow, not music.
    trim = max(2, int(0.004 * w))
    left = img[:, :max(1, gutter - trim)]
    right = img[:, min(w - 1, gutter + trim):]
    if left.shape[1] < 0.15 * w or right.shape[1] < 0.15 * w:
        return [img]
    return [left, right]


# ---------------------------------------------------------------------------
# Deskew (uses staff lines, the strongest horizontal feature on a score)
# ---------------------------------------------------------------------------

def estimate_skew(img: np.ndarray, max_deg: float = 12.0) -> float:
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    scale = 1200 / max(h, w)
    if scale < 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        h, w = gray.shape[:2]

    bw = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15
    )
    # Emphasise long horizontal runs = staff lines
    horiz = cv2.morphologyEx(
        bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 25), 1))
    )
    lines = cv2.HoughLinesP(
        horiz, 1, np.pi / 1800, threshold=100,
        minLineLength=int(w * 0.25), maxLineGap=12,
    )
    if lines is None or len(lines) < 3:
        return 0.0
    # OpenCV 4 hands back (N, 1, 4); OpenCV 5 hands back (N, 4).
    segments = np.asarray(lines).reshape(-1, 4)

    angles = []
    weights = []
    for x1, y1, x2, y2 in segments:
        dx, dy = float(x2 - x1), float(y2 - y1)
        if abs(dx) < 1:
            continue
        ang = np.degrees(np.arctan2(dy, dx))
        if abs(ang) <= max_deg:
            angles.append(ang)
            weights.append(abs(dx))
    if len(angles) < 3:
        return 0.0
    angles = np.array(angles)
    weights = np.array(weights)
    order = np.argsort(angles)
    angles, weights = angles[order], weights[order]
    cw = np.cumsum(weights)
    median = float(angles[np.searchsorted(cw, cw[-1] / 2.0)])
    return median if abs(median) > 0.06 else 0.0


def rotate(img: np.ndarray, angle_deg: float) -> np.ndarray:
    if abs(angle_deg) < 0.05:
        return img
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle_deg, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    border = 255 if img.ndim == 2 else (255, 255, 255)
    return cv2.warpAffine(img, m, (nw, nh), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=border)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def enhance(bgr: np.ndarray, binarize: bool = False) -> np.ndarray:
    """Flatten uneven lighting and lift contrast, the way a scanner would."""
    gray = bgr if bgr.ndim == 2 else cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    # Flat-field: divide out a heavily blurred copy = the page's lighting.
    k = max(31, (min(gray.shape[:2]) // 12) | 1)
    background = cv2.GaussianBlur(gray, (k, k), 0)
    background = np.maximum(background, 1)
    flat = cv2.divide(gray, background, scale=255)

    # Stretch so paper is white and ink is black without crushing thin lines.
    p_lo, p_hi = np.percentile(flat, (12, 99.2))
    if p_hi - p_lo < 10:
        p_lo, p_hi = float(flat.min()), float(max(flat.max(), flat.min() + 1))
    stretched = np.clip((flat.astype(np.float32) - p_lo) * (255.0 / (p_hi - p_lo)), 0, 255)
    out = stretched.astype(np.uint8)

    if binarize:
        out = cv2.adaptiveThreshold(
            out, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 12
        )
    return out


def trim_border(gray: np.ndarray, pad: int = 18) -> np.ndarray:
    """Shave off a dark scanner border / leftover background frame."""
    h, w = gray.shape[:2]
    g = gray if gray.ndim == 2 else cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    ink = (g < 200).astype(np.uint8)
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    cols = np.where(ink.sum(axis=0) > h * 0.002)[0]
    rows = np.where(ink.sum(axis=1) > w * 0.002)[0]
    if len(cols) < 2 or len(rows) < 2:
        return gray
    x0, x1 = max(0, cols[0] - pad), min(w, cols[-1] + pad)
    y0, y1 = max(0, rows[0] - pad), min(h, rows[-1] + pad)
    if (x1 - x0) < w * 0.4 or (y1 - y0) < h * 0.4:
        return gray
    return gray[y0:y1, x0:x1]


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------

def process_capture(
    source: str | Path | np.ndarray,
    auto_crop: bool = True,
    deskew: bool = True,
    do_enhance: bool = True,
    binarize: bool = False,
    quad: np.ndarray | None = None,
    split_spreads: bool = True,
    scale_to_read: bool = True,
) -> list[PageResult]:
    """Everything between a photograph and something an OMR engine can read.

    Returns one result per page: two when the picture holds an open booklet
    or two sheets side by side, one otherwise. ``quad`` lets the window pass
    corners the user dragged by hand, which are trusted over any detection.
    """
    bgr = imread(source) if not isinstance(source, np.ndarray) else source
    shared: list[str] = []

    # How fine the staves are in the camera's own pixels. This is the number
    # that decides whether the music can be read at all: everything later
    # only rearranges those pixels.
    original = staves.analyse(bgr)
    optical = original.interline

    used_quad = None
    if quad is not None:
        used_quad = order_quad(np.asarray(quad, dtype=np.float32))
        shared.append("Cropped to the corners you set.")
    elif auto_crop:
        used_quad = detect_page(bgr, info=original)
        if used_quad is None:
            shared.append("No page edge found - using the whole image.")
        else:
            shared.append("Page edges detected.")

    flat = warp_quad(bgr, used_quad) if used_quad is not None else bgr.copy()

    if optical > 0 and optical < staves.MIN_OPTICAL_INTERLINE:
        shared.append(
            f"The staff lines are only {optical:.0f} px apart in this "
            "picture. Fill more of the frame with the page - or take one "
            "page at a time - for a dependable reading.")

    halves: list[tuple[np.ndarray, str]] = [(flat, "")]
    if split_spreads:
        pieces = split_spread(flat)
        if len(pieces) == 2:
            halves = [(pieces[0], "left"), (pieces[1], "right")]
            shared.append("Two pages in one shot - saved as separate pages.")

    results: list[PageResult] = []
    for image, side in halves:
        notes = list(shared)
        work = image

        # One look at the staves tells us both how far the page is rotated
        # and how fine the printing is: the lines are the most reliable
        # horizontal thing on a page of music, and the only thing whose
        # spacing is fixed.
        page_staves = staves.analyse(work)

        skew = 0.0
        if deskew:
            skew = (page_staves.angle if page_staves.found
                    else estimate_skew(work))
            if abs(skew) > 12.0:
                skew = estimate_skew(work)
            if skew:
                work = rotate(work, skew)
                notes.append(f"Straightened by {skew:+.2f} degrees.")

        if do_enhance:
            work = enhance(work, binarize=binarize)
            if used_quad is None:
                work = trim_border(work)
        elif work.ndim == 3:
            work = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)

        interline = page_staves.interline
        if interline <= 0:
            interline = staves.analyse(work, bright_only=False).interline
        enlarged = 1.0
        if scale_to_read and interline > 0:
            work, enlarged = staves.scale_for_reading(work, interline)
        if work.shape[0] > MAX_OUTPUT_HEIGHT:
            shrink = MAX_OUTPUT_HEIGHT / work.shape[0]
            work = cv2.resize(work, None, fx=shrink, fy=shrink,
                              interpolation=cv2.INTER_AREA)
            enlarged *= shrink
        if enlarged > 1.01:
            notes.append(f"Enlarged {enlarged:.1f}x to give the note reader "
                         "pixels it can work with.")
        elif enlarged < 0.99:
            notes.append(f"Scaled to {enlarged:.2f}x - it was larger than the "
                         "note reader has any use for.")

        readable = optical <= 0 or optical >= staves.MIN_OPTICAL_INTERLINE
        results.append(PageResult(
            image=work, quad=used_quad, detected=used_quad is not None,
            skew_deg=skew, notes=notes, interline=optical,
            enlarged=enlarged, side=side, readable=readable))
    return results


def process_page(
    source: str | Path | np.ndarray,
    auto_crop: bool = True,
    deskew: bool = True,
    do_enhance: bool = True,
    binarize: bool = False,
    quad: np.ndarray | None = None,
) -> PageResult:
    """Clean up one page. The first page, if the picture holds two."""
    return process_capture(
        source, auto_crop=auto_crop, deskew=deskew, do_enhance=do_enhance,
        binarize=binarize, quad=quad, split_spreads=False)[0]
