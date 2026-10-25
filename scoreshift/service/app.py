"""The ScoreShift JSON API.

    uvicorn scoreshift.service.app:app --host 0.0.0.0 --port 8765

Endpoints (all under /v1):

    GET    /health                     is the engine up, what can it do
    POST   /scores                     upload page images -> transcribe
    GET    /scores/{id}                what we know about that piece
    POST   /scores/{id}/transpose      change the key
    GET    /scores/{id}/preview        engraved SVG pages
    GET    /scores/{id}/parts          the parts this piece has
    GET    /scores/{id}/download       pdf | midi | musicxml, whole or one part
    DELETE /scores/{id}                throw it away
"""
from __future__ import annotations

import hmac
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .. import __version__
from ..config import Settings, work_dir
from ..engine import (CleanOptions, EngineError, ExportRequest, ScoreJob,
                      ScoreShiftEngine, TransposeRequest, safe_name)
from ..pipeline import export as exporter

try:
    from fastapi import FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.responses import FileResponse, JSONResponse
    from pydantic import BaseModel, Field
except ImportError as exc:  # noqa: BLE001
    raise SystemExit(
        "The ScoreShift service needs a few extra packages:\n"
        "    pip install -r requirements-server.txt"
    ) from exc


_MEDIA = {"pdf": "application/pdf", "midi": "audio/midi",
          "musicxml": "application/vnd.recordare.musicxml+xml"}
_SUFFIX = {"pdf": "pdf", "midi": "mid", "musicxml": "musicxml"}

MAX_PAGES = 40
MAX_BYTES = 40 * 1024 * 1024
JOB_TTL_SECONDS = 6 * 60 * 60


# ---------------------------------------------------------------------------
# Job registry
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    job: ScoreJob
    created: float = field(default_factory=time.time)
    touched: float = field(default_factory=time.time)
    uploads: Path | None = None
    exports: dict[str, Path] = field(default_factory=dict)


class Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Entry] = {}

    def put(self, job: ScoreJob, uploads: Path | None = None) -> Entry:
        entry = Entry(job=job, uploads=uploads)
        with self._lock:
            self._items[job.job_id] = entry
        return entry

    def get(self, job_id: str) -> Entry:
        with self._lock:
            entry = self._items.get(job_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="No such score.")
        entry.touched = time.time()
        return entry

    def drop(self, job_id: str) -> None:
        with self._lock:
            entry = self._items.pop(job_id, None)
        if entry and entry.uploads:
            shutil.rmtree(entry.uploads, ignore_errors=True)

    def sweep(self) -> int:
        cutoff = time.time() - JOB_TTL_SECONDS
        with self._lock:
            stale = [k for k, v in self._items.items() if v.touched < cutoff]
        for job_id in stale:
            self.drop(job_id)
        return len(stale)


# ---------------------------------------------------------------------------
# Wire formats
# ---------------------------------------------------------------------------

class ScoreSummary(BaseModel):
    job_id: str
    written_key: str = ""
    detected_key: str = ""
    current_key: str = ""
    time_signature: str = ""
    parts: int = 0
    measures: int = 0
    notes: int = 0
    lowest: str = ""
    highest: str = ""
    pages: int = 0
    omr_seconds: float = 0.0
    warnings: list[str] = Field(default_factory=list)


class TransposeBody(BaseModel):
    target_key: str | None = None
    semitones: int | None = None
    instrument: str | None = None
    to_written: bool = True
    source_key: str | None = None
    direction: Literal["nearest", "up", "down"] = "nearest"
    octaves: int = 0
    simplify: bool = True


class TransposeSummary(BaseModel):
    job_id: str
    from_key: str
    to_key: str
    interval: str
    semitones: int
    notes: list[str] = Field(default_factory=list)


def summarise(entry: Entry) -> ScoreSummary:
    job = entry.job
    info = job.info
    return ScoreSummary(
        job_id=job.job_id,
        written_key=info.notated_key if info else "",
        detected_key=info.detected_key if info else "",
        current_key=job.key_now,
        time_signature=info.time_signature if info else "",
        parts=info.parts if info else 0,
        measures=info.measures if info else 0,
        notes=info.notes if info else 0,
        lowest=info.lowest if info else "",
        highest=info.highest if info else "",
        pages=len(job.pages),
        omr_seconds=round(job.omr_seconds, 1),
        warnings=list(info.warnings) if info else [],
    )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

def create_app(engine: ScoreShiftEngine | None = None) -> FastAPI:
    engine = engine or ScoreShiftEngine(Settings.load())
    registry = Registry()
    uploads_root = engine.workspace / "uploads"
    uploads_root.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="ScoreShift", version=__version__,
                  summary="Read sheet music from photographs and change its key.")
    app.state.engine = engine
    app.state.registry = registry

    # Optional shared secret. Set SCORESHIFT_API_KEY before exposing this
    # service beyond your own machine; clients then send X-API-Key.
    api_key = os.environ.get("SCORESHIFT_API_KEY", "").strip()
    if api_key:
        @app.middleware("http")
        async def require_key(request, call_next):  # noqa: ANN001, ANN202
            open_paths = ("/v1/health", "/docs", "/openapi.json", "/redoc")
            if not request.url.path.startswith(open_paths):
                sent = request.headers.get("x-api-key", "")
                if not hmac.compare_digest(sent, api_key):
                    return JSONResponse({"detail": "Bad or missing X-API-Key."},
                                        status_code=401)
            return await call_next(request)

    @app.get("/v1/health")
    def health() -> dict:
        caps = engine.capabilities()
        return {"status": "ok" if caps["omr"]["available"] else "degraded",
                "version": __version__, **caps}

    @app.post("/v1/scores", response_model=ScoreSummary)
    def create_score(
        files: list[UploadFile] = File(..., description="Page images, in order"),
        auto_crop: bool = Form(True),
        deskew: bool = Form(True),
        enhance: bool = Form(True),
        binarize: bool = Form(False),
    ) -> ScoreSummary:
        if not files:
            raise HTTPException(400, "Send at least one page image.")
        if len(files) > MAX_PAGES:
            raise HTTPException(413, f"Too many pages (limit {MAX_PAGES}).")

        job_id = uuid.uuid4().hex[:12]
        folder = uploads_root / job_id
        folder.mkdir(parents=True, exist_ok=True)
        saved: list[Path] = []
        try:
            for i, upload in enumerate(files, 1):
                blob = upload.file.read()
                if len(blob) > MAX_BYTES:
                    raise HTTPException(413, f"{upload.filename} is too large.")
                suffix = Path(upload.filename or f"page{i}.png").suffix or ".png"
                path = folder / f"page{i:02d}{suffix}"
                path.write_bytes(blob)
                saved.append(path)

            options = CleanOptions(auto_crop=auto_crop, deskew=deskew,
                                   enhance=enhance, binarize=binarize)
            cleaned = engine.clean_pages(saved, options)
            job = engine.transcribe(cleaned, job_id=job_id)
        except EngineError as exc:
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(422, str(exc)) from exc
        except HTTPException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        entry = registry.put(job, uploads=folder)
        return summarise(entry)

    @app.get("/v1/scores/{job_id}", response_model=ScoreSummary)
    def read_score(job_id: str) -> ScoreSummary:
        return summarise(registry.get(job_id))

    @app.post("/v1/scores/{job_id}/transpose", response_model=TransposeSummary)
    def transpose(job_id: str, body: TransposeBody) -> TransposeSummary:
        entry = registry.get(job_id)
        request = TransposeRequest(**body.model_dump())
        try:
            job = engine.transpose(entry.job, request)
        except EngineError as exc:
            raise HTTPException(422, str(exc)) from exc
        entry.exports.clear()
        result = job.transpose
        return TransposeSummary(
            job_id=job.job_id, from_key=result.from_key, to_key=result.to_key,
            interval=result.interval_name, semitones=result.semitones,
            notes=list(result.notes),
        )

    @app.get("/v1/scores/{job_id}/preview")
    def preview(job_id: str, pages: int = 8) -> JSONResponse:
        entry = registry.get(job_id)
        svgs = engine.preview_svg(entry.job, max_pages=max(1, min(pages, 24)))
        return JSONResponse({"job_id": job_id, "pages": svgs})

    @app.get("/v1/scores/{job_id}/parts")
    def parts(job_id: str) -> JSONResponse:
        """The parts of this piece, in score order, as ?part= indices."""
        entry = registry.get(job_id)
        job = entry.job
        sc = job.current_score or job.source_score
        if sc is None:
            raise HTTPException(409, "This piece has not been read yet.")
        labels = exporter.part_labels(sc)
        return JSONResponse({
            "job_id": job_id,
            "parts": [{"part": i + 1, "name": name}
                      for i, name in enumerate(labels)],
        })

    @app.get("/v1/scores/{job_id}/download")
    def download(job_id: str, format: Literal["pdf", "midi", "musicxml"] = "pdf",
                 name: str | None = None,
                 part: int | None = None) -> FileResponse:
        entry = registry.get(job_id)
        job = entry.job
        basename = safe_name(name or f"score-{job.job_id}"
                             + (f" in {job.key_now}" if job.key_now else ""))
        if part is not None:
            return _download_part(entry, basename, format, part)
        cached = entry.exports.get(format)
        if cached and cached.exists():
            return FileResponse(str(cached), filename=cached.name)

        out_dir = engine.workspace / "jobs" / job.job_id / "out"
        request = ExportRequest(pdf=format == "pdf", midi=format == "midi",
                                musicxml=format == "musicxml")
        try:
            report = engine.export(job, out_dir, basename, request)
        except EngineError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not report.written:
            detail = "; ".join(report.skipped) or "Nothing could be written."
            raise HTTPException(500, detail)
        path = report.written[0]
        entry.exports[format] = path
        return FileResponse(str(path), filename=path.name,
                            media_type=_MEDIA[format])

    def _download_part(entry: Entry, basename: str, format: str,
                       part: int) -> FileResponse:
        """One part on its own - what a player on a phone actually wants."""
        job = entry.job
        sc = job.current_score or job.source_score
        if sc is None:
            raise HTTPException(409, "This piece has not been read yet.")
        labels = exporter.part_labels(sc)
        if not 1 <= part <= len(labels):
            raise HTTPException(
                404, f"This piece has {len(labels)} part(s); there is no "
                     f"part {part}.")
        label = labels[part - 1]
        key = f"{format}:part{part}"
        cached = entry.exports.get(key)
        if cached and cached.exists():
            return FileResponse(str(cached), filename=cached.name,
                                media_type=_MEDIA[format])
        out_dir = engine.workspace / "jobs" / job.job_id / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        single = exporter.score_for_part(sc, part - 1, label)
        target = out_dir / f"{basename} - {label}.{_SUFFIX[format]}"
        try:
            if format == "midi":
                path = exporter.write_midi(single, target)
            elif format == "musicxml":
                path = exporter.write_musicxml(single, target)
            else:
                path, _ = exporter.write_pdf(single, target,
                                             engine.settings.musescore())
        except exporter.ExportError as exc:
            raise HTTPException(500, str(exc)) from exc
        entry.exports[key] = path
        return FileResponse(str(path), filename=path.name,
                            media_type=_MEDIA[format])

    @app.delete("/v1/scores/{job_id}")
    def delete_score(job_id: str) -> dict:
        entry = registry.get(job_id)
        engine.discard(entry.job)
        registry.drop(job_id)
        return {"deleted": job_id}

    @app.post("/v1/maintenance/sweep")
    def sweep() -> dict:
        return {"removed": registry.sweep()}

    return app


def __getattr__(name: str):
    """Build the app lazily so ``uvicorn scoreshift.service.app:app`` works."""
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
