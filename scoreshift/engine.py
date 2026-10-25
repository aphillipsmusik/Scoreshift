"""The headless ScoreShift engine.

Everything the app actually *does* lives here: clean up page images, read the
notes, change the key, write the files.  There is no Qt and no user interface
in this module, on purpose - the Windows app drives it in-process, and the
HTTP service in ``scoreshift.service`` drives the very same object so an iOS
client can use the identical pipeline over the network.

    engine = ScoreShiftEngine()
    pages  = engine.clean_pages([...image paths...])
    job    = engine.transcribe(pages)
    job    = engine.transpose(job, TransposeRequest(target_key="F major"))
    files  = engine.export(job, out_dir, "My piece in F")
"""
from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np

from .config import Settings, work_dir
from .pipeline import export as exporter
from .pipeline import omr, pagedetect
from .pipeline import score as scoring

LogFn = Callable[[str], None]
CancelFn = Callable[[], bool]


def _noop(_: str) -> None:
    return None


def _never() -> bool:
    return False


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass
class CleanOptions:
    auto_crop: bool = True
    deskew: bool = True
    enhance: bool = True
    binarize: bool = False

    def as_kwargs(self) -> dict:
        return dict(auto_crop=self.auto_crop, deskew=self.deskew,
                    do_enhance=self.enhance, binarize=self.binarize)


@dataclass
class CleanedPage:
    image: np.ndarray
    source: Path | None = None
    detected: bool = False
    skew_deg: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass
class TransposeRequest:
    """One of ``target_key``, ``semitones`` or ``instrument`` decides the move."""
    target_key: str | None = None
    semitones: int | None = None
    instrument: str | None = None
    to_written: bool = True
    source_key: str | None = None
    direction: str = "nearest"
    octaves: int = 0
    simplify: bool = True


@dataclass
class ExportRequest:
    pdf: bool = True
    midi: bool = True
    musicxml: bool = False
    scan_pdf: bool = False
    parts: bool = False        # also one file per part, for PDF and/or MIDI


@dataclass
class ScoreJob:
    """A piece of music moving through the pipeline."""
    job_id: str
    pages: list[CleanedPage] = field(default_factory=list)
    musicxml: Path | None = None
    source_score: object | None = None       # music21 Score, original key
    current_score: object | None = None      # music21 Score, after transposing
    info: scoring.ScoreInfo | None = None
    transpose: scoring.TransposeResult | None = None
    omr_seconds: float = 0.0
    log: list[str] = field(default_factory=list)

    @property
    def key_now(self) -> str:
        if self.transpose is not None:
            return self.transpose.to_key
        return self.info.best_key if self.info else ""


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class EngineError(RuntimeError):
    pass


class ScoreShiftEngine:
    def __init__(self, settings: Settings | None = None,
                 workspace: Path | None = None) -> None:
        self.settings = settings or Settings.load()
        self.workspace = Path(workspace) if workspace else work_dir()
        self.workspace.mkdir(parents=True, exist_ok=True)

    # -- capabilities ----------------------------------------------------
    def engine_ready(self) -> tuple[bool, str]:
        return omr.engine_status(self.settings.audiveris())

    def capabilities(self) -> dict:
        audiveris = self.settings.audiveris()
        musescore = self.settings.musescore()
        ok, message = omr.engine_status(audiveris)
        return {
            "omr": {"available": ok, "path": str(audiveris) if audiveris else None,
                    "detail": message.splitlines()[0] if message else ""},
            "engraver": "MuseScore" if musescore else "Verovio (built in)",
            "musescore": str(musescore) if musescore else None,
        }

    # -- 1. clean --------------------------------------------------------
    def clean_pages(
        self,
        sources: Sequence[str | Path | np.ndarray],
        options: CleanOptions | None = None,
        quads: Sequence[np.ndarray | None] | None = None,
        log: LogFn = _noop,
        cancel: CancelFn = _never,
    ) -> list[CleanedPage]:
        opts = options or CleanOptions()
        out: list[CleanedPage] = []
        for i, src in enumerate(sources):
            if cancel():
                raise EngineError("Cancelled.")
            quad = quads[i] if quads and i < len(quads) else None
            result = pagedetect.process_page(src, quad=quad, **opts.as_kwargs())
            name = Path(src).name if not isinstance(src, np.ndarray) else f"page {i + 1}"
            for note in result.notes:
                log(f"{name}: {note}")
            out.append(CleanedPage(
                image=result.image,
                source=Path(src) if not isinstance(src, np.ndarray) else None,
                detected=result.detected, skew_deg=result.skew_deg,
                notes=result.notes,
            ))
        return out

    # -- 2. read ---------------------------------------------------------
    def transcribe(
        self,
        pages: Sequence[CleanedPage | np.ndarray],
        job_id: str | None = None,
        dpi: int = 300,
        log: LogFn = _noop,
        cancel: CancelFn = _never,
    ) -> ScoreJob:
        audiveris = self.settings.audiveris()
        if audiveris is None:
            raise EngineError(
                "Audiveris (the note-reading engine) was not found. Install it, "
                "or set its location in Settings.")
        images = [p.image if isinstance(p, CleanedPage) else p for p in pages]
        if not images:
            raise EngineError("There are no pages to read.")

        job_id = job_id or uuid.uuid4().hex[:12]
        job_dir = self.workspace / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        result = omr.transcribe(
            audiveris, images, job_dir, dpi=dpi,
            timeout=self.settings.omr_timeout_sec, log=log, cancel=cancel,
        )
        sc = scoring.load(result.musicxml)
        info = scoring.inspect(sc)
        job = ScoreJob(
            job_id=job_id,
            pages=[p for p in pages if isinstance(p, CleanedPage)],
            musicxml=result.musicxml, source_score=sc, current_score=sc,
            info=info, omr_seconds=result.seconds,
        )
        return job

    def load_score(self, path: str | Path, job_id: str | None = None) -> ScoreJob:
        """Skip the camera and start from a MusicXML/MIDI file."""
        sc = scoring.load(path)
        return ScoreJob(
            job_id=job_id or uuid.uuid4().hex[:12],
            musicxml=Path(path), source_score=sc, current_score=sc,
            info=scoring.inspect(sc),
        )

    # -- 3. transpose ----------------------------------------------------
    def transpose(self, job: ScoreJob, request: TransposeRequest,
                  log: LogFn = _noop) -> ScoreJob:
        if job.source_score is None:
            raise EngineError("This job has no score to transpose yet.")
        source = scoring.clone(job.source_score)

        if request.instrument:
            result = scoring.transpose_for_instrument(
                source, request.instrument, to_written=request.to_written,
                simplify=request.simplify)
            if request.octaves:
                result.score = result.score.transpose(
                    scoring.interval.Interval(12 * request.octaves), inPlace=False)
                result.semitones += 12 * request.octaves
        elif request.semitones is not None:
            result = scoring.transpose_by_interval(
                source, request.semitones + 12 * request.octaves,
                simplify=request.simplify)
        elif request.target_key:
            result = scoring.transpose_to_key(
                source, request.target_key, source_key=request.source_key,
                direction=request.direction, simplify=request.simplify,
                extra_octaves=request.octaves)
        else:
            raise EngineError("Say which key, interval or instrument to transpose to.")

        log(f"{result.from_key} -> {result.to_key} ({result.interval_name}, "
            f"{result.semitones:+d} half steps)")
        for note in result.notes:
            log(note)
        job.current_score = result.score
        job.transpose = result
        return job

    # -- 4. save ---------------------------------------------------------
    def export(
        self,
        job: ScoreJob,
        out_dir: str | Path,
        basename: str,
        request: ExportRequest | None = None,
        log: LogFn = _noop,
    ) -> exporter.ExportReport:
        req = request or ExportRequest()
        sc = job.current_score or job.source_score
        if sc is None and not req.scan_pdf:
            raise EngineError("There is nothing to save yet.")
        return exporter.export_all(
            sc, Path(out_dir), safe_name(basename),
            want_pdf=req.pdf, want_midi=req.midi, want_musicxml=req.musicxml,
            want_parts=req.parts,
            scan_images=[p.image for p in job.pages],
            want_scan_pdf=req.scan_pdf,
            musescore=self.settings.musescore(), log=log,
        )

    # -- preview ---------------------------------------------------------
    def preview_svg(self, job: ScoreJob, max_pages: int = 12) -> list[str]:
        sc = job.current_score or job.source_score
        if sc is None:
            return []
        tmp = self.workspace / f"preview_{job.job_id}.musicxml"
        exporter.write_musicxml(sc, tmp)
        return exporter.render_svg_pages(tmp, max_pages=max_pages)

    # -- housekeeping ----------------------------------------------------
    def discard(self, job: ScoreJob) -> None:
        job_dir = self.workspace / "jobs" / job.job_id
        shutil.rmtree(job_dir, ignore_errors=True)


def safe_name(text: str) -> str:
    out = (text or "Score").strip()
    for ch in '<>:"/\\|?*':
        out = out.replace(ch, "-")
    return out or "Score"
