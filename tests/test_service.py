"""API tests. The OMR step is stubbed, since Audiveris isn't installed here."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from scoreshift.engine import ScoreShiftEngine  # noqa: E402
from scoreshift.pipeline import score as scoring  # noqa: E402
from scoreshift.service.app import create_app  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)

passed, failed = 0, 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))


class StubbedEngine(ScoreShiftEngine):
    """Everything real except the Audiveris call."""

    def transcribe(self, pages, job_id=None, dpi=300, log=None, cancel=None):
        job = self.load_score(FIX / "original.musicxml", job_id=job_id)
        job.pages = [p for p in pages if hasattr(p, "image")]
        job.omr_seconds = 1.0
        return job


engine = StubbedEngine(workspace=OUT / "service_work")
client = TestClient(create_app(engine))

print("\n== Health ==")
r = client.get("/v1/health")
check("health responds", r.status_code == 200, str(r.status_code))
body = r.json()
# Whether Audiveris is installed depends on the machine; what matters is that
# the API agrees with the engine about it and says why when it is missing.
truth = engine.capabilities()["omr"]
check("it reports the OMR engine honestly",
      body["omr"]["available"] is truth["available"]
      and bool(body["omr"]["detail"]),
      f"available={body['omr']['available']}  {body['omr']['detail'][:40]}")
check("status follows the engine",
      body["status"] == ("ok" if truth["available"] else "degraded"),
      body["status"])
check("it names the engraver", "Verovio" in body["engraver"] or
      "MuseScore" in body["engraver"], body["engraver"])

print("\n== Uploading pages ==")
files = [("files", (p.name, p.read_bytes(), "image/jpeg"))
         for p in (FIX / "photo_p1.jpg", FIX / "photo_p2.jpg")]
r = client.post("/v1/scores", files=files, data={"auto_crop": "true"})
check("upload accepted", r.status_code == 200, str(r.status_code))
summary = r.json()
job_id = summary.get("job_id", "")
check("a job id comes back", bool(job_id), job_id)
check("the written key is reported", summary["written_key"] == "D major",
      summary["written_key"])
check("both pages were cleaned", summary["pages"] == 2, str(summary["pages"]))
check("bars counted", summary["measures"] == 24, str(summary["measures"]))

r = client.post("/v1/scores", files=[])
check("an empty upload is rejected", r.status_code in (400, 422), str(r.status_code))

print("\n== Reading it back ==")
r = client.get(f"/v1/scores/{job_id}")
check("the job can be fetched", r.status_code == 200 and r.json()["job_id"] == job_id)
r = client.get("/v1/scores/does-not-exist")
check("an unknown job is a clean 404", r.status_code == 404, str(r.status_code))

print("\n== Transposing over HTTP ==")
r = client.post(f"/v1/scores/{job_id}/transpose", json={"target_key": "F major"})
check("transpose accepted", r.status_code == 200, r.text[:120])
result = r.json()
check("it reports the move", result["to_key"] == "F major"
      and result["semitones"] == 3,
      f"{result['from_key']} -> {result['to_key']} ({result['semitones']:+d})")

r = client.post(f"/v1/scores/{job_id}/transpose",
                json={"instrument": "B-flat instrument (trumpet, clarinet, tenor sax)"})
check("instrument presets work over HTTP", r.status_code == 200
      and r.json()["to_key"] == "E major", r.json().get("to_key", r.text[:80]))

r = client.post(f"/v1/scores/{job_id}/transpose", json={"semitones": -4})
check("half-step moves work", r.status_code == 200 and r.json()["semitones"] == -4,
      r.json().get("interval", ""))

r = client.post(f"/v1/scores/{job_id}/transpose", json={})
check("an empty request is refused politely", r.status_code == 422,
      r.json().get("detail", "")[:60])

print("\n== Getting the files ==")
client.post(f"/v1/scores/{job_id}/transpose", json={"target_key": "F major"})

r = client.get(f"/v1/scores/{job_id}/download", params={"format": "pdf"})
check("PDF downloads", r.status_code == 200 and r.content[:4] == b"%PDF",
      f"{len(r.content) // 1024} KB")
(OUT / "service.pdf").write_bytes(r.content)

r = client.get(f"/v1/scores/{job_id}/download", params={"format": "midi"})
check("MIDI downloads", r.status_code == 200 and r.content[:4] == b"MThd",
      f"{len(r.content)} bytes")

r = client.get(f"/v1/scores/{job_id}/download", params={"format": "musicxml"})
check("MusicXML downloads", r.status_code == 200 and b"score-partwise" in r.content,
      f"{len(r.content) // 1024} KB")

r = client.get(f"/v1/scores/{job_id}/preview", params={"pages": 2})
check("preview returns engraved pages", r.status_code == 200
      and len(r.json()["pages"]) >= 1,
      f"{len(r.json().get('pages', []))} page(s)")
check("preview SVG is renderable (no nested svg)",
      all(s.count("<svg") == 1 for s in r.json()["pages"]))

print("\n== Cleaning up ==")
r = client.delete(f"/v1/scores/{job_id}")
check("job deleted", r.status_code == 200, r.text[:60])
r = client.get(f"/v1/scores/{job_id}")
check("it is really gone", r.status_code == 404)

print("\n== The real engine refuses honestly without Audiveris ==")
plain = ScoreShiftEngine(workspace=OUT / "service_work2")
# Pretend the engine isn't installed, whatever this machine actually has, so
# the refusal path is tested the same way everywhere.
plain.settings.audiveris = lambda: None  # type: ignore[method-assign]
plain_client = TestClient(create_app(plain))
r = plain_client.post("/v1/scores", files=[
    ("files", ("photo_p1.jpg", (FIX / "photo_p1.jpg").read_bytes(), "image/jpeg"))])
check("missing OMR engine gives a 422 with an explanation",
      r.status_code == 422 and "Audiveris" in r.json().get("detail", ""),
      r.json().get("detail", r.text)[:70])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
