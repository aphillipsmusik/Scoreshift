"""Auto-shutter logic, tested against a synthetic camera feed."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scoreshift.pipeline import pagedetect  # noqa: E402
from scoreshift.pipeline.camera import (FrameCheck, PageTracker,  # noqa: E402
                                        TrackerSettings)

FIX = Path(__file__).resolve().parent / "fixtures"
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)

FRAME_W, FRAME_H = 1280, 720
PAGE = cv2.imread(str(FIX / "page_flat.png"), cv2.IMREAD_COLOR)

passed, failed = 0, 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))


def frame(scale: float = 0.82, dx: float = 0.0, dy: float = 0.0,
          tilt: float = 0.0, blur: float = 0.0, gain: float = 1.0) -> np.ndarray:
    """One synthetic camera frame: the page held in front of a desk."""
    bg = np.full((FRAME_H, FRAME_W, 3), (58, 74, 96), np.uint8)
    ph, pw = PAGE.shape[:2]
    target_h = FRAME_H * scale
    f = target_h / ph
    page = cv2.resize(PAGE, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    ph, pw = page.shape[:2]

    cx, cy = FRAME_W / 2 + dx, FRAME_H / 2 + dy
    src = np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]])
    dst = np.float32([
        [cx - pw / 2 + tilt, cy - ph / 2],
        [cx + pw / 2, cy - ph / 2 + tilt],
        [cx + pw / 2 - tilt, cy + ph / 2],
        [cx - pw / 2, cy + ph / 2 - tilt],
    ])
    m = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(page, m, (FRAME_W, FRAME_H), borderValue=(0, 0, 0))
    mask = cv2.warpPerspective(np.full((ph, pw), 255, np.uint8), m, (FRAME_W, FRAME_H))
    out = bg.copy()
    out[mask > 10] = warped[mask > 10]
    if blur > 0:
        out = cv2.GaussianBlur(out, (0, 0), blur)
    if gain != 1.0:
        out = np.clip(out.astype(np.float32) * gain, 0, 255).astype(np.uint8)
    return out


def run(tracker: PageTracker, frames, pause: float = 0.05) -> list[FrameCheck]:
    checks = []
    for f in frames:
        checks.append(tracker.check(f))
        time.sleep(pause)
    return checks


settings = TrackerSettings(hold_seconds=0.2, cooldown_seconds=0.6)

print("\n== Nothing to see ==")
tracker = PageTracker(settings)
empty = np.full((FRAME_H, FRAME_W, 3), (58, 74, 96), np.uint8)
empty = np.clip(empty + np.random.default_rng(3).normal(0, 6, empty.shape), 0, 255
                ).astype(np.uint8)
result = tracker.check(empty)
check("blank desk does not look like a page", result.state == "searching",
      result.message)
check("no shot is taken", not tracker.should_capture(result))

print("\n== Page too small ==")
tracker = PageTracker(settings)
result = run(tracker, [frame(scale=0.25) for _ in range(4)])[-1]
check("a distant page is not accepted", result.state != "ready",
      f"{result.message} (fill {result.fill:.0%})")
result_mid = run(PageTracker(settings), [frame(scale=0.42) for _ in range(4)])[-1]
check("a half-frame page asks you to move closer",
      "closer" in result_mid.message.lower(),
      f"{result_mid.message} (fill {result_mid.fill:.0%})")
check("no shot while too far", not tracker.should_capture(result))

print("\n== Page too close ==")
tracker = PageTracker(settings)
result = run(tracker, [frame(scale=1.6) for _ in range(4)])[-1]
check("over-filled frame asks you to move back",
      result.state != "ready", f"{result.message} (coverage {result.coverage:.0%})")

print("\n== Out of focus ==")
tracker = PageTracker(settings)
result = run(tracker, [frame(blur=4.5) for _ in range(4)])[-1]
check("blur is caught", result.state != "ready",
      f"{result.message} (sharpness {result.sharpness:.0f})")

print("\n== Too dark ==")
tracker = PageTracker(settings)
result = run(tracker, [frame(gain=0.16) for _ in range(4)])[-1]
check("darkness is caught", result.state != "ready",
      f"{result.message} (brightness {result.brightness:.0f})")

print("\n== Moving hands ==")
tracker = PageTracker(settings)
rng = np.random.default_rng(11)
shaky = [frame(dx=float(rng.uniform(-70, 70)), dy=float(rng.uniform(-70, 70)))
         for _ in range(6)]
checks = run(tracker, shaky)
check("a moving page never reaches ready",
      not any(c.state == "ready" for c in checks),
      "; ".join(sorted({c.message for c in checks}))[:80])

print("\n== Held steady ==")
tracker = PageTracker(settings)
steady = [frame(dx=1.0 if i % 2 else 0.0) for i in range(14)]
checks = run(tracker, steady)
states = [c.state for c in checks]
check("it settles and fires", "ready" in states,
      " -> ".join(dict.fromkeys(states)))
fire_at = states.index("ready") if "ready" in states else -1
check("it waits before firing", fire_at >= 2, f"fired on frame {fire_at + 1}")
check("a countdown is shown first",
      any(c.state == "holding" and c.countdown > 0 for c in checks))

print("\n== After a shot ==")
tracker.block()
after = run(tracker, [frame() for _ in range(4)])
check("cooldown stops an instant second shot of the same page",
      not any(c.state == "ready" for c in after), after[-1].message)
time.sleep(settings.cooldown_seconds)
again = run(tracker, [frame() for _ in range(10)])
check("it re-arms once the cooldown passes",
      any(c.state == "ready" for c in again))

print("\n== The captured corners are usable ==")
tracker = PageTracker(settings)
live = frame(tilt=26.0)
result = run(tracker, [live] * 8)[-1]
check("corners found on a tilted page", result.quad is not None)
if result.quad is not None:
    inside = all(-5 <= x <= FRAME_W + 5 and -5 <= y <= FRAME_H + 5
                 for x, y in result.quad)
    check("corners are in full-frame coordinates", inside,
          str([tuple(int(v) for v in p) for p in result.quad]))
    page = pagedetect.process_page(live, quad=result.quad)
    cv2.imwrite(str(OUT / "camera_capture.png"), page.image)
    h, w = page.image.shape[:2]
    check("the auto-shutter crop is a clean portrait page",
          1.15 < h / w < 1.65 and float((page.image > 225).mean()) > 0.55,
          f"{w}x{h}, {float((page.image > 225).mean()):.0%} white")

print("\n== The capture window ==")
from PySide6.QtWidgets import QApplication  # noqa: E402

from scoreshift.ui import camera_dialog as cd  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
cd.cam.list_cameras = lambda *a, **k: [cd.cam.CameraInfo(0, "Synthetic test camera")]


class OfflineWorker(cd.CameraWorker):
    """Never touches real hardware; frames are pushed in by the test."""

    def run(self) -> None:
        return


cd.CameraWorker = OfflineWorker
dlg = cd.CameraDialog()
dlg.resize(1000, 800)
dlg.show()
app.processEvents()
check("the capture window opens", dlg.isVisible())
check("the camera list is populated", dlg.cmb_camera.count() == 1,
      dlg.cmb_camera.currentText())

feed = PageTracker(settings)
for f in [frame(scale=0.40)] * 3 + [frame()] * 10:
    dlg.on_frame(f, feed.check(f))
    app.processEvents()
    time.sleep(0.03)
check("the live status text updates", bool(dlg.status.text()), dlg.status.text())
dlg.on_capture(frame())
dlg.on_capture(frame(tilt=18))
app.processEvents()
check("captured pages appear in the strip", dlg.strip.count() == 2,
      dlg.lbl_count.text())
check("captures are written to disk",
      all(p.exists() for p in dlg.captured_paths))
dlg.grab().save(str(OUT / "camera_window.png"))
dlg.remove_selected() if dlg.strip.setCurrentRow(0) is None else None
check("removing a page works", dlg.strip.count() == 1, dlg.lbl_count.text())
dlg.reject()
check("cancelling cleans up its files",
      not any(p.exists() for p in dlg.captured_paths))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
