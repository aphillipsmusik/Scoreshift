"""End-to-end checks that don't need a display or the OMR engine."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scoreshift.pipeline import export as exporter  # noqa: E402
from scoreshift.pipeline import omr, pagedetect  # noqa: E402
from scoreshift.pipeline import score as scoring  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)

passed, failed = 0, 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))


# ---------------------------------------------------------------------------
print("\n== Page detection and clean-up ==")
photo = FIX / "photo_p1.jpg"
img = pagedetect.imread(photo)
quad = pagedetect.detect_page(img)
check("page outline found in a tilted photo", quad is not None)

if quad is not None:
    area = abs(cv2.contourArea(quad.astype(np.float32)))
    frac = area / (img.shape[0] * img.shape[1])
    check("outline covers a plausible share of the frame", 0.25 < frac < 0.85,
          f"{frac:.0%} of the frame")

result = pagedetect.process_page(photo)
cv2.imwrite(str(OUT / "cleaned_p1.png"), result.image)
check("clean-up produced a grayscale page", result.image.ndim == 2)
h, w = result.image.shape[:2]
check("aspect ratio looks like a portrait page", 1.15 < h / w < 1.6, f"{h}x{w}")
check("page is tall enough for OMR", h >= 1700, f"{h} px tall")

# Paper should be near-white and ink near-black after clean-up.
hist_bright = float((result.image > 225).mean())
hist_dark = float((result.image < 90).mean())
check("paper flattened to white", hist_bright > 0.6, f"{hist_bright:.0%} bright")
check("ink retained", 0.005 < hist_dark < 0.20, f"{hist_dark:.1%} dark")

# Staff lines should be horizontal afterwards.
residual = pagedetect.estimate_skew(result.image)
check("staves are level after deskew", abs(residual) < 0.6, f"{residual:+.2f} deg")

flat = pagedetect.process_page(FIX / "page_flat.png")
check("an already-flat scan is left uncropped", not flat.detected)

# ---------------------------------------------------------------------------
print("\n== Multi-page PDF hand-off to the OMR engine ==")
pages = [pagedetect.process_page(FIX / f"photo_p{i}.jpg").image for i in (1, 2)]
pdf = omr.images_to_pdf(pages, OUT / "for_omr.pdf", dpi=300)
check("PDF written for the engine", pdf.exists() and pdf.stat().st_size > 5000,
      f"{pdf.stat().st_size // 1024} KB")
try:
    from pypdf import PdfReader
    check("PDF has one page per scan", len(PdfReader(str(pdf)).pages) == 2)
except ImportError:
    print("  SKIP  pypdf not installed")

# ---------------------------------------------------------------------------
print("\n== Reading and describing a score ==")
sc = scoring.load(FIX / "original.musicxml")
info = scoring.inspect(sc)
check("key signature read", info.notated_key == "D major", info.notated_key)
check("harmonic analysis agrees", info.detected_key.startswith("D"), info.detected_key)
check("time signature read", info.time_signature == "4/4", info.time_signature)
check("both hands found", info.parts == 2, f"{info.parts} parts")
check("bars counted", info.measures >= 20, f"{info.measures} bars")
check("range reported", info.lowest == "G2" and info.highest == "A5",
      f"{info.lowest}-{info.highest}")

# ---------------------------------------------------------------------------
print("\n== Transposition ==")


def pitches(score) -> list[str]:
    out = []
    for n in score.recurse().notes:
        if n.isChord:
            out.extend(p.nameWithOctave for p in n.pitches)
        else:
            out.append(n.pitch.nameWithOctave)
    return out


orig_pitches = pitches(sc)

res = scoring.transpose_to_key(scoring.clone(sc), "F major")
check("D major -> F major names the move", res.to_key == "F major", res.to_key)
check("F is a minor third above D", res.semitones == 3,
      f"{res.interval_name} / {res.semitones} semitones")
new_info = scoring.inspect(res.score)
check("new key signature written", new_info.notated_key == "F major",
      new_info.notated_key)
check("note count unchanged", len(pitches(res.score)) == len(orig_pitches))
first_old, first_new = orig_pitches[0], pitches(res.score)[0]
check("first note moved correctly", first_old == "F#5" and first_new == "A5",
      f"{first_old} -> {first_new}")
check("spelling stays diatonic (no double accidentals)",
      not any("##" in p or "--" in p for p in pitches(res.score)))

res_down = scoring.transpose_to_key(scoring.clone(sc), "B- major", direction="down")
check("downward transposition goes down", res_down.semitones < 0,
      f"{res_down.semitones} semitones")

# The tricky one: a key that would need 7 sharps should be respelled.
res_cs = scoring.transpose_to_key(scoring.clone(sc), "C# major", simplify=True)
check("C# major respelled as Db major", res_cs.to_key == "Db major", res_cs.to_key)
res_cs_raw = scoring.transpose_to_key(scoring.clone(sc), "C# major", simplify=False)
check("without simplification C# major is kept", res_cs_raw.to_key == "C# major",
      res_cs_raw.to_key)

res_semi = scoring.transpose_by_interval(scoring.clone(sc), -5)
check("half-step transposition moves every note by the same amount",
      all(
          abs(scoring.note.Note(a).pitch.ps - scoring.note.Note(b).pitch.ps) == 5
          for a, b in zip(orig_pitches[:40], pitches(res_semi.score)[:40])
      ))

res_bflat = scoring.transpose_for_instrument(
    scoring.clone(sc), "B-flat instrument (trumpet, clarinet, tenor sax)")
check("B-flat part is written a major 2nd up", res_bflat.semitones == 2,
      f"{res_bflat.interval_name}")
check("concert D major becomes E major for a B-flat player",
      scoring.inspect(res_bflat.score).notated_key == "E major",
      scoring.inspect(res_bflat.score).notated_key)

res_oct = scoring.transpose_to_key(scoring.clone(sc), "F major", extra_octaves=-1)
check("octave shift applied on top of the key change",
      res_oct.semitones == 3 - 12, f"{res_oct.semitones}")

# ---------------------------------------------------------------------------
print("\n== Export ==")
report = exporter.export_all(
    res.score, OUT, "Test - F major",
    want_pdf=True, want_midi=True, want_musicxml=True,
    scan_images=pages, want_scan_pdf=True, musescore=None,
    log=lambda m: None,
)
names = {p.name for p in report.written}
check("MusicXML written", "Test - F major.musicxml" in names)
check("MIDI written", "Test - F major.mid" in names)
check("PDF written", "Test - F major.pdf" in names, report.engraver)
check("cleaned scan PDF written", "Test - F major - scan.pdf" in names)
for p in report.written:
    check(f"{p.name} is non-empty", p.stat().st_size > 500,
          f"{p.stat().st_size // 1024} KB")
if report.skipped:
    print("  note: skipped ->", report.skipped)

midi = OUT / "Test - F major.mid"
if midi.exists():
    from music21 import converter
    back = converter.parse(str(midi))
    midi_pitches = sorted(scoring.note.Note(p).pitch.midi for p in pitches(back))
    xml_pitches = sorted(scoring.note.Note(p).pitch.midi for p in pitches(res.score))
    check("MIDI carries every transposed note",
          midi_pitches == xml_pitches,
          f"{len(midi_pitches)} notes, {min(midi_pitches)}-{max(midi_pitches)}")
    check("MIDI is 3 semitones above the original",
          min(midi_pitches) - min(
              scoring.note.Note(p).pitch.midi for p in orig_pitches) == 3)

svgs = exporter.render_svg_pages(OUT / "Test - F major.musicxml")
check("preview pages engraved", len(svgs) >= 1, f"{len(svgs)} page(s)")
check("preview has no nested <svg> (Qt can render it)",
      all(s.count("<svg") == 1 for s in svgs))

# ---------------------------------------------------------------------------
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
