"""Webcam capture with automatic page detection and auto-shutter.

The detection logic here is deliberately free of any UI framework: it takes
frames in and returns a judgement about what it sees, so the same rules can
drive the Windows window today and an iOS camera view later.
"""
from __future__ import annotations

import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import pagedetect, staves

IS_WINDOWS = sys.platform.startswith("win")

DETECT_WIDTH = 640            # frames are shrunk to this before detection


class CameraError(RuntimeError):
    pass


@dataclass
class CameraInfo:
    index: int
    name: str

    def __str__(self) -> str:  # noqa: D105
        return self.name


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------

def list_cameras(max_probe: int = 6) -> list[CameraInfo]:
    """Cameras we can open, with friendly names where the OS gives them."""
    names: list[str] = []
    try:
        from PySide6.QtMultimedia import QMediaDevices  # noqa: PLC0415
        names = [d.description() for d in QMediaDevices.videoInputs()]
    except Exception:  # noqa: BLE001
        names = []

    found: list[CameraInfo] = []
    backend = cv2.CAP_DSHOW if IS_WINDOWS else cv2.CAP_ANY
    for i in range(max_probe):
        cap = cv2.VideoCapture(i, backend)
        ok = cap.isOpened()
        if ok:
            ok, _ = cap.read()
        cap.release()
        if ok:
            label = names[i] if i < len(names) else f"Camera {i}"
            found.append(CameraInfo(index=i, name=label))
        elif i < len(names):
            found.append(CameraInfo(index=i, name=names[i]))
    return found


class CameraStream:
    """A thin wrapper around one open camera."""

    def __init__(self, index: int = 0, width: int = 1920, height: int = 1080) -> None:
        backend = cv2.CAP_DSHOW if IS_WINDOWS else cv2.CAP_ANY
        self.capture = cv2.VideoCapture(index, backend)
        if not self.capture.isOpened():
            raise CameraError(
                f"Camera {index} could not be opened. Another program may be "
                "using it, or Windows camera permission is switched off "
                "(Settings > Privacy & security > Camera).")
        self.request_size(width, height)
        try:
            self.capture.set(cv2.CAP_PROP_AUTOFOCUS, 1)
        except Exception:  # noqa: BLE001
            pass

    # Detail is everything for reading music, so the biggest frame the
    # camera will give is usually the right one.
    SIZES = ((3840, 2160), (2560, 1440), (1920, 1080), (1280, 720), (640, 480))

    def best_size(self) -> tuple[int, int]:
        """The largest frame size this camera actually delivers."""
        current = self.size()
        for width, height in self.SIZES:
            got = self.request_size(width, height)
            if got[0] >= width * 0.95 and got[1] >= height * 0.95:
                return got
        self.request_size(*current)
        return current

    def zoom_base(self) -> float | None:
        """The driver's value for "not zoomed", if it has a zoom at all.

        Worth finding out, because a camera's own zoom and a crop of its
        picture are not the same thing: most webcams have a sensor larger
        than the frame they hand over, so zooming in the camera gives real
        extra detail, where cropping afterwards only throws pixels away.
        """
        try:
            base = float(self.capture.get(cv2.CAP_PROP_ZOOM))
        except Exception:  # noqa: BLE001
            return None
        if not base or base <= 0:
            return None
        # Setting it has to actually move the value, or the driver is only
        # being polite.
        probe = base * 2.0
        if not self.set_zoom(probe):
            return None
        try:
            moved = float(self.capture.get(cv2.CAP_PROP_ZOOM))
        except Exception:  # noqa: BLE001
            moved = base
        self.set_zoom(base)
        return base if abs(moved - base) > 0.01 else None

    def set_zoom(self, value: float) -> bool:
        try:
            return bool(self.capture.set(cv2.CAP_PROP_ZOOM, float(value)))
        except Exception:  # noqa: BLE001
            return False

    def request_size(self, width: int, height: int) -> tuple[int, int]:
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        return self.size()

    def size(self) -> tuple[int, int]:
        return (int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))

    def read(self) -> np.ndarray | None:
        ok, frame = self.capture.read()
        return frame if ok else None

    def release(self) -> None:
        try:
            self.capture.release()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "CameraStream":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


MAX_ZOOM = 4.0


def crop_zoom(frame: np.ndarray, factor: float,
              centre: tuple[float, float] = (0.5, 0.5)) -> np.ndarray:
    """A window onto part of the frame, at its own pixels.

    Nothing is enlarged - the result is simply smaller, which is the honest
    thing to do. What it buys is a view with nothing else in it: the desk, the
    tray, the keyboard and the mug stop being candidates for "the page", and
    the detector only has to choose between what you have framed.
    """
    if factor <= 1.001:
        return frame
    factor = min(float(factor), MAX_ZOOM)
    h, w = frame.shape[:2]
    cw, ch = max(64, int(w / factor)), max(64, int(h / factor))
    cx = min(max(centre[0], 0.0), 1.0) * w
    cy = min(max(centre[1], 0.0), 1.0) * h
    x0 = int(min(max(cx - cw / 2, 0), w - cw))
    y0 = int(min(max(cy - ch / 2, 0), h - ch))
    return frame[y0:y0 + ch, x0:x0 + cw]


# ---------------------------------------------------------------------------
# Judging a frame
# ---------------------------------------------------------------------------

@dataclass
class FrameCheck:
    quad: np.ndarray | None = None        # in FULL-frame coordinates
    coverage: float = 0.0                 # share of the frame's AREA
    fill: float = 0.0                     # how much of the frame's width/height
    cut_off: bool = False                 # an edge runs off the frame
    sharpness: float = 0.0
    brightness: float = 0.0
    motion: float = 1.0                   # corner movement, 0 = perfectly still
    good: bool = False                    # everything is right for a shot
    interline: float = 0.0                # px between staff lines, 0 = unknown
    readable: bool = True                 # are the notes big enough to read
    pages_in_shot: int = 1                # 2 when an open booklet is in frame
    hold_frames: int = 0                  # consecutive good frames so far
    countdown: float = 0.0                # seconds left before auto-capture
    message: str = "Point the camera at the page"
    state: str = "searching"              # searching | adjusting | holding | ready


@dataclass
class TrackerSettings:
    # A portrait page in a 16:9 frame can only ever cover about 40% of the
    # frame's AREA, so framing is judged by how much of the frame's width or
    # height the page spans, not by area.
    min_fill: float = 0.55
    min_coverage: float = 0.16
    track_area_ratio: float = 0.04        # keep tracking a page that is still far off
    edge_margin: float = 2.5              # pixels, on the detection-size frame
    min_sharpness: float = 22.0
    min_brightness: float = 55.0
    max_brightness: float = 248.0
    max_motion: float = 0.012             # fraction of the frame diagonal
    hold_seconds: float = 0.7             # how long it must stay still
    cooldown_seconds: float = 2.0         # pause after a shot
    require_readable: bool = True         # hold the shutter when it is hopeless
    hold_outline: float = 0.3             # seconds to keep a lost outline on screen
    min_interline_to_shoot: float = 5.0   # px; below this there is no music to read
    staff_check_every: float = 0.5        # seconds between staff measurements


class PageTracker:
    """Watches the live frames and says when the page is worth capturing."""

    def __init__(self, settings: TrackerSettings | None = None) -> None:
        self.settings = settings or TrackerSettings()
        self._recent: deque[np.ndarray] = deque(maxlen=6)
        self._good_since: float | None = None
        self._blocked_until: float = 0.0
        self._last_quad: np.ndarray | None = None
        self._interline: float = 0.0
        self._pages_in_shot: int = 1
        self._staff_checked: float = 0.0
        self._info: staves.StaffInfo | None = None
        self._gutter_in: bool = False
        self._smooth: np.ndarray | None = None
        self._held_quad: np.ndarray | None = None
        self._held_at: float = 0.0

    def reset(self) -> None:
        self._recent.clear()
        self._good_since = None
        self._last_quad = None
        self._interline = 0.0
        self._pages_in_shot = 1
        self._staff_checked = 0.0
        self._info = None
        self._gutter_in = False
        self._smooth = None
        self._held_quad = None
        self._held_at = 0.0

    def _staff_info(self, frame: np.ndarray) -> staves.StaffInfo:
        """Where the music is and how fine it is, refreshed now and then.

        This is the measurement that finds the page on a cluttered desk and
        says whether the notes are big enough to be worth a shot. Staff lines
        only survive at full resolution, which is far too slow to repeat on
        every frame, so it runs a few times a second - and once the music has
        been found, only over the patch of frame it was found in, which costs
        a fraction as much.
        """
        now = time.time()
        have = self._info is not None and self._info.found
        due = self.settings.staff_check_every if have else self.settings.staff_check_every * 3
        if self._info is not None and now - self._staff_checked < due:
            return self._info
        self._staff_checked = now

        info = None
        if have and self._info.box:
            x, y, w, h = self._info.box
            pad_x, pad_y = int(0.25 * w), int(0.25 * h)
            x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
            x1 = min(frame.shape[1], x + w + pad_x)
            y1 = min(frame.shape[0], y + h + pad_y)
            crop = frame[y0:y1, x0:x1]
            if crop.size:
                found = staves.analyse(crop)
                if found.found and found.box:
                    bx, by, bw, bh = found.box
                    # The crop's own coordinates have to be carried back to
                    # the frame, for the box and for every segment in it.
                    step = 1.0 / found.scale if found.scale else 1.0
                    shift = np.array([x0, y0, x0, y0], dtype=np.float32) * step
                    info = staves.StaffInfo(
                        lines=found.lines, interline=found.interline,
                        angle=found.angle, box=(bx + x0, by + y0, bw, bh),
                        scale=found.scale,
                        segments=(found.segments + shift).astype(np.int32)
                        if len(found.segments) else found.segments)
                    self._gutter_in = (
                        pagedetect.find_gutter(crop, found) is not None)
        if info is None:
            info = staves.analyse(frame)
            self._gutter_in = False
            if info.found and info.box:
                x, y, w, h = info.box
                crop = frame[max(0, y):y + h, max(0, x):x + w]
                if crop.size:
                    self._gutter_in = pagedetect.find_gutter(crop) is not None

        self._info = info
        self._interline = info.interline if info.found else 0.0
        self._pages_in_shot = 2 if (info.found and self._gutter_in) else 1
        return info

    def _follow(self, quad: np.ndarray | None, diagonal: float
                ) -> np.ndarray | None:
        """Keep the outline moving with the page rather than flickering.

        Detection is run from scratch on every frame, so its corners jitter by
        a pixel or two even on a page lying perfectly still, and it misses
        outright on the odd frame. Blending each new answer into the last - and
        holding the previous outline for a moment when a frame finds nothing -
        is the difference between an outline that tracks the page and one that
        twitches.
        """
        now = time.time()
        if quad is None:
            # A brief gap: keep showing where the page was.
            if (self._held_quad is not None
                    and now - self._held_at < self.settings.hold_outline):
                return self._held_quad
            self._smooth = None
            return None

        quad = pagedetect.order_quad(quad)
        if self._smooth is not None:
            moved = float(np.abs(quad - self._smooth).max())
            if moved < 0.12 * diagonal:
                quad = 0.45 * quad + 0.55 * self._smooth
        self._smooth = quad.astype(np.float32)
        self._held_quad = self._smooth
        self._held_at = now
        return self._smooth

    def block(self, seconds: float | None = None) -> None:
        """Stop auto-capture briefly, e.g. straight after taking a shot."""
        wait = self.settings.cooldown_seconds if seconds is None else seconds
        self._blocked_until = time.time() + wait
        self._good_since = None

    @staticmethod
    def detail_verdict(interline: float) -> str:
        """What the measured staff spacing means, in words."""
        if interline <= 0:
            return ""
        if interline >= staves.MIN_GOOD_INTERLINE:
            return f"Detail {interline:.0f} px - good"
        if interline >= staves.MIN_OPTICAL_INTERLINE:
            return f"Detail {interline:.0f} px - usable"
        if interline >= 6:
            return f"Detail {interline:.0f} px - rough, expect mistakes"
        return f"Detail {interline:.0f} px - too far to read"

    @property
    def cooling_down(self) -> bool:
        return time.time() < self._blocked_until

    def check(self, frame: np.ndarray) -> FrameCheck:
        s = self.settings
        h, w = frame.shape[:2]
        scale = DETECT_WIDTH / float(w) if w > DETECT_WIDTH else 1.0
        small = (cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                 if scale < 1.0 else frame)
        sh, sw = small.shape[:2]

        # The staff lines are measured on the full frame - they vanish in a
        # thumbnail - but the outline hunt runs on the shrunken copy, with
        # those findings rescaled to fit it. Full-frame detection on every
        # frame would cost more than the preview can afford.
        info = self._staff_info(frame).at_scale(scale)
        quad_small = pagedetect.detect_page(
            small, min_area_ratio=s.track_area_ratio, info=info)
        quad_small = self._follow(quad_small, float(np.hypot(sw, sh)))
        result = FrameCheck()

        if quad_small is None:
            self._good_since = None
            self._recent.clear()
            result.message = "Show the whole page in the frame"
            result.state = "searching"
            return result

        quad_small = pagedetect.order_quad(quad_small)
        result.quad = (quad_small / scale).astype(np.float32)
        area = abs(cv2.contourArea(quad_small.astype(np.float32)))
        result.coverage = float(area / (sw * sh))

        xs, ys = quad_small[:, 0], quad_small[:, 1]
        result.fill = float(max((xs.max() - xs.min()) / sw,
                                (ys.max() - ys.min()) / sh))
        m = s.edge_margin
        result.cut_off = bool(xs.min() <= m or ys.min() <= m
                              or xs.max() >= sw - m or ys.max() >= sh - m)

        result.interline = self._interline
        result.pages_in_shot = self._pages_in_shot
        result.readable = (self._interline <= 0
                           or self._interline >= staves.MIN_OPTICAL_INTERLINE)

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        mask = np.zeros((sh, sw), np.uint8)
        cv2.fillConvexPoly(mask, quad_small.astype(np.int32), 255)
        inner = cv2.erode(mask, np.ones((9, 9), np.uint8))
        if inner.sum() > 0:
            result.brightness = float(gray[inner > 0].mean())
            lap = cv2.Laplacian(gray, cv2.CV_64F)
            result.sharpness = float(lap[inner > 0].var())
        else:
            result.brightness = float(gray.mean())
            result.sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

        diag = float(np.hypot(sw, sh))
        if self._recent:
            prev = self._recent[-1]
            result.motion = float(np.abs(quad_small - prev).max() / diag)
            if result.motion > 3 * s.max_motion:
                # The page is being moved: the music has gone somewhere else,
                # so measure it again on the next frame rather than tracking
                # with an answer from where it used to be.
                self._staff_checked = 0.0
        self._recent.append(quad_small)

        # Say the single most useful thing.
        problem = None
        if result.cut_off:
            problem = "Move back - the edge of the page is cut off"
        elif result.fill < s.min_fill or result.coverage < s.min_coverage:
            problem = "Move closer - the page is small in the frame"
        elif result.brightness < s.min_brightness:
            problem = "Too dark - find more light"
        elif result.brightness > s.max_brightness:
            problem = "Too bright - the paper is blown out"
        elif result.sharpness < s.min_sharpness:
            problem = "Blurry - hold still while it focuses"
        elif (s.require_readable and 0 < self._interline
              < s.min_interline_to_shoot):
            # The one thing that cannot be fixed afterwards: detail that was
            # never in the picture at all.
            problem = (f"Too far - the staff lines are only "
                       f"{self._interline:.0f} px apart. Fill the frame with "
                       "one page.")
        elif result.motion > s.max_motion:
            problem = "Hold still"

        now = time.time()
        if problem is not None:
            self._good_since = None
            result.message = problem
            result.state = "adjusting"
            return result

        result.good = True
        if self.cooling_down:
            result.message = "Page captured - move to the next one"
            result.state = "adjusting"
            self._good_since = None
            return result

        if self._good_since is None:
            self._good_since = now
        held = now - self._good_since
        result.countdown = max(0.0, s.hold_seconds - held)
        result.hold_frames = len(self._recent)
        if result.countdown <= 0:
            result.state = "ready"
            result.message = ("Captured - two pages"
                              if result.pages_in_shot > 1 else "Captured")
        else:
            result.state = "holding"
            result.message = f"Hold still... {result.countdown:.1f}s"
        return result

    def should_capture(self, check: FrameCheck) -> bool:
        return check.state == "ready"


# ---------------------------------------------------------------------------
# Saving a capture
# ---------------------------------------------------------------------------

def save_frame(frame: np.ndarray, out_dir: Path, prefix: str = "camera") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{prefix}_{stamp}_{int(time.time() * 1000) % 1000:03d}.png"
    pagedetect.imwrite(path, frame)
    return path
