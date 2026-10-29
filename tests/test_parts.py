"""Per-part export: naming, splitting, the files themselves, and the API."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from music21 import converter, instrument, metadata, note, stream  # noqa: E402

from scoreshift.engine import (ExportRequest, ScoreShiftEngine,  # noqa: E402
                               TransposeRequest)
from scoreshift.pipeline import export as exporter  # noqa: E402

OUT = Path(__file__).resolve().parent / "out" / "parts"
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


def pitch_names(score) -> list[str]:
    return [str(n.pitch) for n in score.recurse().notes]


def make_part(name: str, pitches: list[str], inst=None) -> stream.Part:
    part = stream.Part()
    if inst is not None:
        part.insert(0, inst)
    if name:
        part.partName = name
    measure = stream.Measure(number=1)
    for p in pitches:
        measure.append(note.Note(p, quarterLength=1))
    part.append(measure)
    return part


def ensemble() -> stream.Score:
    """Three parts: one named on the page, one named only by its instrument,
    and one with no name at all - the three cases Audiveris produces."""
    sc = stream.Score()
    md = metadata.Metadata()
    md.title = "Fanfare"
    md.composer = "A. Phillips"
    sc.insert(0, md)
    sc.insert(0, make_part("Trumpet in Bb", ["C5", "D5", "E5", "F5"]))
    sc.insert(0, make_part("", ["G3", "A3", "B3", "C4"], instrument.Trombone()))
    sc.insert(0, make_part("", ["C3", "C3", "G2", "C3"]))
    return sc


# ---------------------------------------------------------------------------
print("\n== File names ==")
check("illegal characters go", exporter.safe_filename('Vln I: "solo"/B?') ==
      "Vln I solo B", exporter.safe_filename('Vln I: "solo"/B?'))
check("backslashes and pipes go", exporter.safe_filename(r"Horn\1|2") == "Horn 1 2",
      exporter.safe_filename(r"Horn\1|2"))
check("runs of space collapse", exporter.safe_filename("Oboe    II") == "Oboe II")
check("a trailing dot goes (Windows drops it silently)",
      exporter.safe_filename("Picc.") == "Picc")
check("an empty name falls back", exporter.safe_filename("***") == "Part")
check("a given fallback is used", exporter.safe_filename("", "Part 4") == "Part 4")
check("a very long name is cut", len(exporter.safe_filename("x" * 200)) == 60)
check("accents and sharps survive",
      exporter.safe_filename("Clarinete en Si♭ (José)") ==
      "Clarinete en Si♭ (José)")

# ---------------------------------------------------------------------------
print("\n== Part names ==")
sc = ensemble()
labels = exporter.part_labels(sc)
check("one label per part", len(labels) == 3, str(labels))
check("the page's own name is used", labels[0] == "Trumpet in Bb", labels[0])
check("an unnamed part falls back to the instrument",
      "Trombone" in labels[1], labels[1])
check("a part with nothing to go on is numbered",
      labels[2] == "Part 3", labels[2])
check("every label is distinct", len(set(labels)) == 3)

same = stream.Score()
same.insert(0, make_part("Violin", ["C4"]))
same.insert(0, make_part("Violin", ["E4"]))
same.insert(0, make_part("Viola", ["G3"]))
dupes = exporter.part_labels(same)
check("two parts with the same name are numbered",
      dupes[:2] == ["Violin 1", "Violin 2"], str(dupes))
check("a name that is already unique is left alone", dupes[2] == "Viola")
check("no two files would collide", len(set(dupes)) == 3)

# ---------------------------------------------------------------------------
print("\n== One part on its own ==")
single = exporter.score_for_part(sc, 0, "Trumpet in Bb")
check("it holds exactly one part", len(single.parts) == 1)
check("it holds only that part's notes",
      pitch_names(single) == ["C5", "D5", "E5", "F5"], str(pitch_names(single)))
check("the title comes along", single.metadata.title == "Fanfare",
      str(single.metadata.title))
check("the composer comes along", single.metadata.composer == "A. Phillips")
check("the part is named on the page", single.metadata.movementName ==
      "Trumpet in Bb", str(single.metadata.movementName))
check("the full score is untouched", len(sc.parts) == 3 and
      len(pitch_names(sc)) == 12, f"{len(pitch_names(sc))} notes")

try:
    exporter.score_for_part(sc, 9)
    check("asking for a part that isn't there raises", False)
except exporter.ExportError as exc:
    check("asking for a part that isn't there raises", True, str(exc)[:40])

# ---------------------------------------------------------------------------
print("\n== Saving the part files ==")
report = exporter.export_all(
    sc, OUT, "Fanfare in C",
    want_pdf=False, want_midi=True, want_parts=True, log=lambda m: None,
)
names = {p.name for p in report.written}
check("the full score is still saved", "Fanfare in C.mid" in names, str(names))
check("one MIDI per part", {"Fanfare in C - Trumpet in Bb.mid",
                            "Fanfare in C - Part 3.mid"} <= names, str(names))
check("four files in total", len(report.written) == 4, str(len(report.written)))
check("nothing was skipped", not report.skipped, "; ".join(report.skipped))
for p in report.written:
    check(f"{p.name} is a real file", p.exists() and p.stat().st_size > 50,
          f"{p.stat().st_size} bytes")

trumpet = converter.parse(str(OUT / "Fanfare in C - Trumpet in Bb.mid"))
check("the trumpet file has only the trumpet's notes",
      sorted(n.pitch.midi for n in trumpet.recurse().notes) ==
      sorted(note.Note(p).pitch.midi for p in ["C5", "D5", "E5", "F5"]),
      str(pitch_names(trumpet)))
bass = converter.parse(str(OUT / "Fanfare in C - Part 3.mid"))
check("the bass file has only the bass's notes",
      sorted(n.pitch.midi for n in bass.recurse().notes) ==
      sorted(note.Note(p).pitch.midi for p in ["C3", "C3", "G2", "C3"]),
      str(pitch_names(bass)))
check("the parts don't share notes",
      set(pitch_names(trumpet)).isdisjoint(set(pitch_names(bass))))

# ---------------------------------------------------------------------------
print("\n== Part files follow the key change ==")
engine = ScoreShiftEngine(workspace=OUT / "work")
xml = OUT / "ensemble.musicxml"
exporter.write_musicxml(sc, xml)
job = engine.load_score(xml)
result = engine.transpose(job, TransposeRequest(semitones=2))
check("the whole ensemble transposed", result.transpose is not None,
      f"{result.transpose.semitones} semitones" if result.transpose else "")
rep2 = engine.export(job, OUT / "up2", "Fanfare up a tone",
                     ExportRequest(pdf=False, midi=True, parts=True))
got = sorted(p.name for p in rep2.written)
check("part files written after transposing", len(got) == 4, ", ".join(got))
trumpet_up = OUT / "up2" / "Fanfare up a tone - Trumpet in Bb.mid"
check("the part files are named the same way after transposing",
      trumpet_up.exists(), ", ".join(got))
moved = converter.parse(str(trumpet_up))
check("a part file carries the new key, not the old",
      sorted(n.pitch.midi for n in moved.recurse().notes) ==
      sorted(note.Note(p).pitch.midi + 2 for p in ["C5", "D5", "E5", "F5"]),
      str(pitch_names(moved)))

# ---------------------------------------------------------------------------
print("\n== A one-part piece ==")
solo = stream.Score()
solo.insert(0, make_part("Piano", ["C4", "E4", "G4"]))
rep3 = exporter.export_all(solo, OUT / "solo", "Etude",
                           want_pdf=False, want_midi=True, want_parts=True)
check("only the one file is written", len(rep3.written) == 1,
      ", ".join(p.name for p in rep3.written))
check("and it says why there are no part files",
      any("only one part" in s for s in rep3.skipped), "; ".join(rep3.skipped))

print("\n== Parts asked for with nothing to put in them ==")
rep4 = exporter.export_all(sc, OUT / "empty", "Fanfare",
                           want_pdf=False, want_midi=False, want_parts=True)
check("it asks for a format rather than failing",
      any("tick PDF or MIDI" in s for s in rep4.skipped), "; ".join(rep4.skipped))

# ---------------------------------------------------------------------------
print("\n== Engraved part PDFs ==")
pdfs, problems = exporter.write_part_pdfs(sc, OUT / "pdfs", "Fanfare",
                                          None, lambda m: None)
check("one PDF per part", len(pdfs) == 3, ", ".join(p.name for p in pdfs))
for p in pdfs:
    check(f"{p.name} is a real PDF",
          p.stat().st_size > 1000 and p.read_bytes()[:4] == b"%PDF",
          f"{p.stat().st_size // 1024} KB")
if problems:
    print("  note: skipped ->", problems)

# ---------------------------------------------------------------------------
print("\n== Scores that arrive as files ==")
roundtrip = OUT / "formats"
roundtrip.mkdir(parents=True, exist_ok=True)
engine2 = ScoreShiftEngine(workspace=roundtrip / "work")
sources = {}
sources["musicxml"] = xml
midi_path = roundtrip / "ensemble.mid"
exporter.write_midi(sc, midi_path)
sources["midi"] = midi_path
for name, path in sources.items():
    try:
        job = engine2.load_score(path)
        ok = job.source_score is not None and job.info is not None
        check(f"a {name} file opens straight into a score", ok,
              f"{job.info.parts} part(s), {job.info.measures} bars" if ok else "")
    except Exception as exc:  # noqa: BLE001
        check(f"a {name} file opens straight into a score", False, str(exc)[:60])

job = engine2.load_score(xml)
engine2.transpose(job, TransposeRequest(semitones=2))
report = engine2.export(job, roundtrip / "out", "Opened and moved",
                        ExportRequest(pdf=False, midi=True, musicxml=True))
check("and can be transposed and saved without a single page being scanned",
      len(report.written) == 2, ", ".join(p.name for p in report.written))

print("\n== The API ==")
try:
    from fastapi.testclient import TestClient
except Exception as exc:  # noqa: BLE001 - no HTTP client for the test harness
    print("\n  SKIPPED: the API tests need a test client "
          "(pip install -r requirements-dev.txt)")
    print(f"  ({exc})")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

from scoreshift.service.app import create_app  # noqa: E402


class StubbedEngine(ScoreShiftEngine):
    def transcribe(self, pages, job_id=None, dpi=300, log=None, cancel=None):
        job = self.load_score(xml, job_id=job_id)
        job.pages = [p for p in pages if hasattr(p, "image")]
        return job


api = StubbedEngine(workspace=OUT / "api_work")
client = TestClient(create_app(api))
# Register a piece the way a phone would, by uploading a page.
fix = Path(__file__).resolve().parent / "fixtures" / "photo_p1.jpg"
r = client.post("/v1/scores",
                files=[("files", (fix.name, fix.read_bytes(), "image/jpeg"))])
check("upload accepted", r.status_code == 200, str(r.status_code))
job_id = r.json()["job_id"]

r = client.get(f"/v1/scores/{job_id}/parts")
check("the parts endpoint answers", r.status_code == 200, str(r.status_code))
listed = r.json().get("parts", [])
check("it lists all three parts", len(listed) == 3, str(listed))
check("with 1-based indices", [p["part"] for p in listed] == [1, 2, 3])
check("and the names the files would use",
      listed[0]["name"] == "Trumpet in Bb", str(listed[0]))

r = client.get(f"/v1/scores/{job_id}/download",
               params={"format": "midi", "part": 1, "name": "Fanfare"})
check("one part downloads", r.status_code == 200, str(r.status_code))
check("as MIDI", r.headers.get("content-type", "").startswith("audio/midi"),
      r.headers.get("content-type", ""))
from urllib.parse import unquote  # noqa: E402

disposition = unquote(r.headers.get("content-disposition", ""))
check("named after the part", "Trumpet in Bb.mid" in disposition, disposition)
check("and it is a real MIDI file", r.content[:4] == b"MThd", str(r.content[:4]))

r = client.get(f"/v1/scores/{job_id}/download",
               params={"format": "midi", "part": 9})
check("asking for a part that isn't there is a 404", r.status_code == 404,
      str(r.status_code))
r = client.get(f"/v1/scores/{job_id}/download",
               params={"format": "midi", "part": 0})
check("part 0 is a 404 too", r.status_code == 404, str(r.status_code))
r = client.get(f"/v1/scores/{job_id}/download", params={"format": "midi"})
check("the whole score still downloads", r.status_code == 200,
      str(r.status_code))
whole = converter.parseData(r.content, format="midi")
check("and it has every part in it", len(pitch_names(whole)) == 12,
      f"{len(pitch_names(whole))} notes")

# ---------------------------------------------------------------------------
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
