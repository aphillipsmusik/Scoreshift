"""Build a fake 'photo of sheet music' so the pipeline can be tested headlessly."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from music21 import (bar, chord, clef, key, layout, meter, note,  # noqa: E402
                     stream, tempo)

OUT = Path(__file__).resolve().parent / "fixtures"
OUT.mkdir(exist_ok=True)


def build_score() -> stream.Score:
    """A short two-hand piano piece in D major."""
    sc = stream.Score()
    rh = stream.Part(id="RH")
    lh = stream.Part(id="LH")
    rh.append(clef.TrebleClef())
    lh.append(clef.BassClef())
    for p in (rh, lh):
        p.append(key.KeySignature(2))       # D major
        p.append(meter.TimeSignature("4/4"))
    rh.append(tempo.MetronomeMark(number=96))

    phrase_a = [
        ("F#5", 1.0), ("E5", 1.0), ("D5", 1.0), ("E5", 1.0),
        ("F#5", 1.0), ("F#5", 1.0), ("F#5", 2.0),
        ("E5", 1.0), ("E5", 1.0), ("E5", 2.0),
        ("F#5", 1.0), ("A5", 1.0), ("A5", 2.0),
    ]
    phrase_b = [
        ("F#5", 0.5), ("E5", 0.5), ("D5", 1.0), ("E5", 1.0), ("F#5", 1.0),
        ("G5", 1.0), ("F#5", 1.0), ("E5", 1.0), ("D5", 1.0),
        ("C#5", 1.0), ("E5", 1.0), ("A4", 2.0),
        ("D5", 2.0), ("D5", 2.0),
    ]
    harmony_a = [
        ["D3", "A3"], ["D3", "A3"], ["D3", "A3"], ["A2", "E3"],
        ["A2", "E3"], ["D3", "A3"], ["G2", "D3"], ["A2", "E3"],
    ]
    harmony_b = [
        ["D3", "A3"], ["B2", "F#3"], ["G2", "D3"], ["A2", "E3"],
        ["D3", "F#3"], ["G2", "B2"], ["A2", "C#3"], ["D3", "A3"],
    ]

    for _ in range(3):                      # ~24 bars, several systems
        for name, dur in phrase_a + phrase_b:
            rh.append(note.Note(name, quarterLength=dur))
        for pitches in harmony_a + harmony_b:
            lh.append(chord.Chord(pitches, quarterLength=2.0))

    rh.append(bar.Barline("final"))
    lh.append(bar.Barline("final"))
    sc.insert(0, rh)
    sc.insert(0, lh)
    sc.insert(0, layout.StaffGroup([rh, lh], name="Piano",
                                   symbol="brace", barTogether=True))
    return sc.makeNotation()


def musicxml_to_png(xml_path: Path, png_path: Path) -> Path:
    """Engrave with Verovio, rasterise with Qt (no display needed)."""
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    from scoreshift.pipeline.export import render_svg_pages

    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    svgs = render_svg_pages(xml_path)
    svg = svgs[0]
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    size = renderer.defaultSize()
    scale = 2000 / size.width()
    img = QImage(int(size.width() * scale), int(size.height() * scale),
                 QImage.Format_RGB32)
    img.fill(QColor("white"))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    img.save(str(png_path))
    return png_path


def fake_photo(page_png: Path, out_path: Path, seed: int = 7) -> Path:
    """Put the page on a desk, at an angle, under uneven light."""
    rng = np.random.default_rng(seed)
    page = cv2.imread(str(page_png), cv2.IMREAD_COLOR)
    ph, pw = page.shape[:2]
    scale = 1500 / max(ph, pw)
    page = cv2.resize(page, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ph, pw = page.shape[:2]

    # Desk background with wood-ish noise
    bg_h, bg_w = int(ph * 1.35), int(pw * 1.45)
    bg = np.zeros((bg_h, bg_w, 3), np.uint8)
    bg[:, :] = (58, 74, 96)
    grain = rng.normal(0, 9, (bg_h, bg_w, 1)).astype(np.int16)
    bg = np.clip(bg.astype(np.int16) + grain, 0, 255).astype(np.uint8)

    # Perspective: tilt the page
    margin_x, margin_y = (bg_w - pw) // 2, (bg_h - ph) // 2
    src = np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]])
    jitter = lambda a: float(rng.uniform(-a, a))  # noqa: E731
    dst = np.float32([
        [margin_x + jitter(45) + 40, margin_y + jitter(35) + 20],
        [margin_x + pw + jitter(45) - 30, margin_y + jitter(35) + 55],
        [margin_x + pw + jitter(45) - 55, margin_y + ph + jitter(35) - 25],
        [margin_x + jitter(45) + 15, margin_y + ph + jitter(35) - 45],
    ])
    m = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(page, m, (bg_w, bg_h), borderValue=(0, 0, 0))
    mask = cv2.warpPerspective(np.full((ph, pw), 255, np.uint8), m, (bg_w, bg_h))

    # Soft drop shadow
    shadow = cv2.GaussianBlur(mask, (0, 0), 25)
    bg = (bg.astype(np.float32) * (1 - 0.35 * (shadow[..., None] / 255.0))).astype(np.uint8)

    composite = bg.copy()
    composite[mask > 10] = warped[mask > 10]

    # Uneven lighting: bright top-left falling off to the bottom-right
    yy, xx = np.mgrid[0:bg_h, 0:bg_w].astype(np.float32)
    light = 1.18 - 0.45 * ((xx / bg_w) * 0.6 + (yy / bg_h) * 0.7)
    composite = np.clip(composite.astype(np.float32) * light[..., None], 0, 255)
    composite = composite.astype(np.uint8)

    # Camera softness + sensor noise
    composite = cv2.GaussianBlur(composite, (3, 3), 0.8)
    composite = np.clip(
        composite.astype(np.int16) + rng.normal(0, 3.5, composite.shape).astype(np.int16),
        0, 255).astype(np.uint8)

    cv2.imwrite(str(out_path), composite)
    return out_path


def main() -> None:
    sc = build_score()
    xml = OUT / "original.musicxml"
    sc.write("musicxml", fp=str(xml))
    print("wrote", xml)
    png = musicxml_to_png(xml, OUT / "page_flat.png")
    print("wrote", png)
    for i, seed in enumerate((7, 21), start=1):
        photo = fake_photo(png, OUT / f"photo_p{i}.jpg", seed=seed)
        print("wrote", photo)


if __name__ == "__main__":
    main()
