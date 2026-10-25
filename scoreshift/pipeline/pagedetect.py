"""Page detection, perspective correction, deskew and cleanup.

Turns a phone photo (or a raw scan) of a sheet of music into a flat,
high-contrast page image that an OMR engine can read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

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
# Detection
# ---------------------------------------------------------------------------

def detect_page(bgr: np.ndarray,
                min_area_ratio: float = MIN_PAGE_AREA_RATIO) -> np.ndarray | None:
    """Find the page outline. Returns 4x2 corners in original coords, or None.

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

    best, best_score = None, 0.0
    for q in candidates:
        s = _quad_score(q, frame_area, min_area_ratio)
        if s > best_score:
            best, best_score = q, s

    if best is None:
        return None

    # Reject "the whole frame" detections: already a flat scan, no crop needed.
    o = order_quad(best)
    margin = 0.012 * max(gray.shape[:2])
    hits_border = (
        o[:, 0].min() < margin
        and o[:, 1].min() < margin
        and o[:, 0].max() > gray.shape[1] - margin
        and o[:, 1].max() > gray.shape[0] - margin
    )
    if hits_border and best_score > 0.93:
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

def process_page(
    source: str | Path | np.ndarray,
    auto_crop: bool = True,
    deskew: bool = True,
    do_enhance: bool = True,
    binarize: bool = False,
    quad: np.ndarray | None = None,
) -> PageResult:
    """Full clean-up for one page.

    ``quad`` lets the UI pass corner points the user dragged by hand;
    when it is None and ``auto_crop`` is on, corners are detected.
    """
    bgr = imread(source) if not isinstance(source, np.ndarray) else source
    notes: list[str] = []

    used_quad = None
    if quad is not None:
        used_quad = order_quad(np.asarray(quad, dtype=np.float32))
        notes.append("Cropped to the corners you set.")
    elif auto_crop:
        used_quad = detect_page(bgr)
        if used_quad is None:
            notes.append("No page edge found - using the whole image.")
        else:
            notes.append("Page edges detected.")

    work = warp_quad(bgr, used_quad) if used_quad is not None else bgr.copy()

    skew = 0.0
    if deskew:
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

    return PageResult(image=work, quad=used_quad,
                      detected=used_quad is not None, skew_deg=skew, notes=notes)
