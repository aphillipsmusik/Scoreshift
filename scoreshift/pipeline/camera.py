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

from . import pagedetect

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


class PageTracker:
    """Watches the live frames and says when the page is worth capturing."""

    def __init__(self, settings: TrackerSettings | None = None) -> None:
        self.settings = settings or TrackerSettings()
        self._recent: deque[np.ndarray] = deque(maxlen=6)
        self._good_since: float | None = None
        self._blocked_until: float = 0.0
        self._last_quad: np.ndarray | None = None

    def reset(self) -> None:
        self._recent.clear()
        self._good_since = None
        self._last_quad = None

    def block(self, seconds: float | None = None) -> None:
        """Stop auto-capture briefly, e.g. straight after taking a shot."""
        wait = self.settings.cooldown_seconds if seconds is None else seconds
        self._blocked_until = time.time() + wait
        self._good_since = None

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

        quad_small = pagedetect.detect_page(small, min_area_ratio=s.track_area_ratio)
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
            result.message = "Captured"
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
