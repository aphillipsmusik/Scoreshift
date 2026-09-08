"""Optical Music Recognition via the Audiveris engine.

Audiveris runs as a separate process in batch mode and writes MusicXML.
We hand it a single multi-page PDF built from the cleaned page images so
that it treats the pages as one continuous book.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
from PIL import Image

from ..config import IS_WINDOWS, probe_tool

LogFn = Callable[[str], None]
CancelFn = Callable[[], bool]

AUDIVERIS_DOWNLOAD = "https://github.com/Audiveris/audiveris/releases"


class OmrError(RuntimeError):
    pass


@dataclass
class OmrResult:
    musicxml: Path
    log: str
    seconds: float
    source_pdf: Path


# ---------------------------------------------------------------------------
# Input assembly
# ---------------------------------------------------------------------------

def images_to_pdf(images: Sequence[np.ndarray | str | Path], out_pdf: Path,
                  dpi: int = 300) -> Path:
    """Combine cleaned page images into one multi-page PDF for the engine."""
    if not images:
        raise OmrError("No pages to transcribe.")
    pils: list[Image.Image] = []
    for item in images:
        if isinstance(item, np.ndarray):
            arr = item
            if arr.ndim == 3:
                arr = arr[:, :, ::-1]           # BGR -> RGB
                im = Image.fromarray(arr).convert("L")
            else:
                im = Image.fromarray(arr)
        else:
            im = Image.open(str(item)).convert("L")
        pils.append(im)

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    first, rest = pils[0], pils[1:]
    first.save(
        str(out_pdf), "PDF", resolution=float(dpi),
        save_all=True, append_images=rest,
    )
    for im in pils:
        im.close()
    return out_pdf


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def engine_status(audiveris: Path | None) -> tuple[bool, str]:
    """Check that the OMR engine is present and launches."""
    if audiveris is None:
        return False, (
            "Audiveris was not found. Install it from\n"
            f"{AUDIVERIS_DOWNLOAD}\nor set the path in Settings."
        )
    ok, blob = probe_tool(audiveris, ["-help"], timeout=120)
    if not ok:
        return False, f"Audiveris could not be started:\n{blob}"
    if "audiveris" in blob.lower() or "-batch" in blob.lower() or "usage" in blob.lower():
        return True, blob.splitlines()[0] if blob else "Audiveris ready."
    return True, "Audiveris responded (output not recognised, but it ran)."


def _stream(proc: subprocess.Popen, log: LogFn | None, cancel: CancelFn | None,
            timeout: float) -> str:
    started = time.time()
    lines: list[str] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip()
        lines.append(line)
        if log and line:
            log(line)
        if cancel and cancel():
            proc.kill()
            raise OmrError("Transcription cancelled.")
        if time.time() - started > timeout:
            proc.kill()
            raise OmrError(f"Transcription timed out after {int(timeout)}s.")
    proc.wait(timeout=60)
    return "\n".join(lines)


def _find_export(out_dir: Path, since: float) -> Path | None:
    cands: list[Path] = []
    for pat in ("**/*.mxl", "**/*.musicxml", "**/*.xml"):
        for p in out_dir.glob(pat):
            if p.stat().st_mtime >= since - 5 and p.stat().st_size > 200:
                cands.append(p)
    if not cands:
        return None
    # Prefer a compressed whole-book export over per-sheet fragments.
    cands.sort(key=lambda p: (p.suffix != ".mxl", -p.stat().st_size))
    return cands[0]


def run_omr(
    audiveris: Path,
    input_file: Path,
    work_dir: Path,
    timeout: float = 900,
    log: LogFn | None = None,
    cancel: CancelFn | None = None,
) -> OmrResult:
    """Run Audiveris in batch mode and return the MusicXML it produced."""
    out_dir = work_dir / "omr_out"
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(audiveris),
        "-batch",
        "-export",
        "-output", str(out_dir),
        "--", str(input_file),
    ]
    if log:
        log("Running: " + " ".join(cmd))

    started = time.time()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(work_dir),
            creationflags=(subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0),
        )
    except FileNotFoundError as exc:
        raise OmrError(f"Could not launch Audiveris at {audiveris}: {exc}") from exc

    blob = _stream(proc, log, cancel, timeout)
    elapsed = time.time() - started

    export = _find_export(out_dir, started)
    if export is None:
        tail = "\n".join(blob.splitlines()[-25:])
        raise OmrError(
            "Audiveris finished but produced no MusicXML.\n"
            "This usually means the page was too blurry, too small, or not "
            "recognised as music.\n\nEngine output:\n" + tail
        )
    return OmrResult(musicxml=export, log=blob, seconds=elapsed,
                     source_pdf=input_file)


def transcribe(
    audiveris: Path,
    pages: Sequence[np.ndarray | str | Path],
    work_dir: Path,
    dpi: int = 300,
    timeout: float = 900,
    log: LogFn | None = None,
    cancel: CancelFn | None = None,
) -> OmrResult:
    """Cleaned pages -> MusicXML."""
    work_dir.mkdir(parents=True, exist_ok=True)
    pdf = work_dir / "scan_for_omr.pdf"
    if log:
        log(f"Assembling {len(pages)} page(s) into {pdf.name} at {dpi} DPI...")
    images_to_pdf(pages, pdf, dpi=dpi)
    return run_omr(audiveris, pdf, work_dir, timeout=timeout, log=log, cancel=cancel)
