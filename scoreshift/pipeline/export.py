"""Saving the transposed score: MusicXML, MIDI, engraved PDF, scan PDF."""
from __future__ import annotations

import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from music21 import stream

from ..config import IS_WINDOWS

LogFn = Callable[[str], None]

# A4 in tenths of a millimetre, the unit Verovio uses.
_VRV_OPTIONS = {
    "pageWidth": 2100,
    "pageHeight": 2970,
    "pageMarginTop": 100,
    "pageMarginBottom": 100,
    "pageMarginLeft": 100,
    "pageMarginRight": 100,
    "scale": 40,
    "adjustPageHeight": False,
    "footer": "none",
    "header": "auto",
    "breaks": "auto",
    "svgViewBox": True,
}


class ExportError(RuntimeError):
    pass


@dataclass
class ExportReport:
    written: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    engraver: str = ""


# ---------------------------------------------------------------------------
# Simple writers
# ---------------------------------------------------------------------------

def write_musicxml(sc: stream.Score, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        sc.write("musicxml", fp=str(path))
    except Exception as exc:  # noqa: BLE001
        raise ExportError(f"Could not write MusicXML: {exc}") from exc
    return path


def write_midi(sc: stream.Score, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        sc.write("midi", fp=str(path))
    except Exception as exc:  # noqa: BLE001
        raise ExportError(f"Could not write MIDI: {exc}") from exc
    return path


def write_scan_pdf(images: Sequence, path: Path, dpi: int = 300) -> Path:
    from .omr import images_to_pdf
    return images_to_pdf(images, path, dpi=dpi)


# ---------------------------------------------------------------------------
# Engraved PDF
# ---------------------------------------------------------------------------

def _pdf_via_musescore(musescore: Path, musicxml: Path, pdf: Path,
                       log: LogFn | None = None) -> bool:
    cmd = [str(musescore), "-o", str(pdf), str(musicxml)]
    if log:
        log("Engraving with MuseScore: " + " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            creationflags=(subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0),
        )
    except Exception as exc:  # noqa: BLE001
        if log:
            log(f"MuseScore failed to run: {exc}")
        return False
    if log and (proc.stdout or proc.stderr):
        log((proc.stdout or "") + (proc.stderr or ""))
    return pdf.exists() and pdf.stat().st_size > 1000


_SVG_NS = "http://www.w3.org/2000/svg"


def _viewport(elem, parent_w: float, parent_h: float) -> tuple[float, float]:
    def num(attr: str, pct_of: float) -> float:
        raw = elem.get(attr)
        if raw is None:
            return pct_of
        raw = raw.strip()
        if raw.endswith("%"):
            return pct_of * float(raw[:-1]) / 100.0
        for suffix in ("px", "pt", "mm", "cm", "in"):
            if raw.endswith(suffix):
                raw = raw[: -len(suffix)]
                break
        try:
            return float(raw)
        except ValueError:
            return pct_of

    return num("width", parent_w), num("height", parent_h)


def flatten_svg(svg_text: str) -> str:
    """Rewrite nested <svg> elements as <g transform=...>.

    Qt's SVG renderer follows SVG Tiny, which forbids nested <svg>; Verovio
    emits one for its 'definition-scale' layer.  Converting it to an
    equivalent group keeps the page renderable in the preview and in the
    PDF fallback.
    """
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    ET.register_namespace("", _SVG_NS)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        return svg_text

    def viewbox_of(elem) -> tuple[float, float, float, float] | None:
        vb = elem.get("viewBox")
        if not vb:
            return None
        parts = [float(x) for x in vb.replace(",", " ").split()]
        return tuple(parts) if len(parts) == 4 else None  # type: ignore[return-value]

    root_vb = viewbox_of(root)
    root_w, root_h = _viewport(root, root_vb[2] if root_vb else 1000.0,
                               root_vb[3] if root_vb else 1000.0)

    def walk(parent, vp_w: float, vp_h: float) -> None:
        for idx, child in enumerate(list(parent)):
            tag = child.tag.split("}")[-1]
            if tag == "svg":
                x = float(child.get("x", 0) or 0)
                y = float(child.get("y", 0) or 0)
                w, h = _viewport(child, vp_w, vp_h)
                vb = viewbox_of(child)
                if vb:
                    vbx, vby, vbw, vbh = vb
                    s = min(w / vbw, h / vbh) if vbw and vbh else 1.0
                    tx = x + (w - vbw * s) / 2.0
                    ty = y + (h - vbh * s) / 2.0
                    transform = (f"translate({tx:.6g},{ty:.6g}) scale({s:.10g}) "
                                 f"translate({-vbx:.6g},{-vby:.6g})")
                    inner_w, inner_h = vbw, vbh
                else:
                    transform = f"translate({x:.6g},{y:.6g})"
                    inner_w, inner_h = w, h

                group = ET.Element(f"{{{_SVG_NS}}}g")
                for key, value in child.attrib.items():
                    if key in ("x", "y", "width", "height", "viewBox",
                               "version", "overflow", "preserveAspectRatio"):
                        continue
                    group.set(key, value)
                existing = group.get("transform")
                group.set("transform", f"{existing} {transform}" if existing else transform)
                for sub in list(child):
                    group.append(sub)
                parent.remove(child)
                parent.insert(idx, group)
                walk(group, inner_w, inner_h)
            else:
                walk(child, vp_w, vp_h)

    walk(root, root_w, root_h)
    return ET.tostring(root, encoding="unicode")


_VRV_LOCK = threading.Lock()
_VRV_TOOLKIT = None


def _verovio_toolkit():
    """One shared Verovio toolkit.

    Building a fresh toolkit per call can lose the bundled music fonts
    (Bravura/Leipzig), so the instance is created once, with the resource
    path pinned to the installed package.
    """
    global _VRV_TOOLKIT  # noqa: PLW0603
    if _VRV_TOOLKIT is not None:
        return _VRV_TOOLKIT
    try:
        import verovio  # noqa: PLC0415
    except ImportError as exc:  # noqa: BLE001
        raise ExportError("Verovio is not installed.") from exc
    data_dir = Path(verovio.__file__).resolve().parent / "data"
    if data_dir.is_dir():
        try:
            verovio.setDefaultResourcePath(str(data_dir))
        except Exception:  # noqa: BLE001
            pass
    _VRV_TOOLKIT = verovio.toolkit()
    return _VRV_TOOLKIT


def render_svg_pages(musicxml: Path, max_pages: int = 40) -> list[str]:
    """Engrave MusicXML to SVG pages with Verovio (used for preview and PDF)."""
    with _VRV_LOCK:
        return _render_svg_pages_locked(musicxml, max_pages)


def _render_svg_pages_locked(musicxml: Path, max_pages: int) -> list[str]:
    tk = _verovio_toolkit()
    tk.setOptions(_VRV_OPTIONS)
    data = musicxml.read_bytes()
    if musicxml.suffix.lower() == ".mxl":
        ok = tk.loadZipFile(str(musicxml)) if hasattr(tk, "loadZipFile") else False
        if not ok:
            ok = tk.loadFile(str(musicxml))
    else:
        ok = tk.loadData(data.decode("utf-8", errors="replace"))
    if not ok:
        raise ExportError("Verovio could not read the score.")
    pages = min(tk.getPageCount(), max_pages)
    return [flatten_svg(tk.renderToSVG(i + 1)) for i in range(pages)]


def _pdf_via_verovio(musicxml: Path, pdf: Path, log: LogFn | None = None) -> bool:
    """Fallback engraver - Verovio for layout, Qt for the PDF container."""
    try:
        from PySide6.QtCore import QByteArray, QMarginsF  # noqa: PLC0415
        from PySide6.QtGui import (QGuiApplication, QPageSize,  # noqa: PLC0415
                                   QPainter, QPdfWriter)
        from PySide6.QtSvg import QSvgRenderer  # noqa: PLC0415
    except ImportError:
        return False

    # Qt needs an application object before it will lay out fonts.  Inside
    # ScoreShift one already exists; this covers headless/CLI use.
    if QGuiApplication.instance() is None:
        import sys as _sys  # noqa: PLC0415
        _sys.argv = _sys.argv or ["scoreshift"]
        globals()["_HEADLESS_QT_APP"] = QGuiApplication(_sys.argv[:1])

    try:
        svgs = render_svg_pages(musicxml)
    except ExportError as exc:
        if log:
            log(str(exc))
        return False
    if not svgs:
        return False

    if log:
        log(f"Engraving with the built-in Verovio engine ({len(svgs)} page(s)).")

    writer = QPdfWriter(str(pdf))
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))
    writer.setResolution(300)
    writer.setTitle(musicxml.stem)

    painter = QPainter()
    if not painter.begin(writer):
        return False
    try:
        for i, svg in enumerate(svgs):
            if i:
                writer.newPage()
            renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
            rect = painter.viewport()
            size = renderer.defaultSize()
            if size.width() > 0 and size.height() > 0:
                scale = min(rect.width() / size.width(), rect.height() / size.height())
                w, h = size.width() * scale, size.height() * scale
                target = rect.adjusted(
                    int((rect.width() - w) / 2), int((rect.height() - h) / 2),
                    -int((rect.width() - w) / 2), -int((rect.height() - h) / 2),
                )
                renderer.render(painter, target)
            else:
                renderer.render(painter)
    finally:
        painter.end()
    return pdf.exists() and pdf.stat().st_size > 1000


def write_pdf(sc: stream.Score, path: Path, musescore: Path | None = None,
              log: LogFn | None = None) -> tuple[Path, str]:
    """Engrave the score to PDF. Returns (path, engraver name)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        xml = Path(tmp) / "score.musicxml"
        write_musicxml(sc, xml)
        if musescore is not None and _pdf_via_musescore(musescore, xml, path, log):
            return path, "MuseScore"
        if _pdf_via_verovio(xml, path, log):
            return path, "Verovio"
    raise ExportError(
        "Could not produce a PDF. Install MuseScore (free) for best results, "
        "or check that Verovio is installed."
    )


# ---------------------------------------------------------------------------
# Individual parts
# ---------------------------------------------------------------------------

# Characters Windows will not accept in a file name, plus the ones that make
# a name awkward to type on a command line.
_BAD_FILENAME = set('<>:"/\\|?*') | {chr(c) for c in range(32)}


def safe_filename(text: str, fallback: str = "Part") -> str:
    """Turn a part name into something every filesystem will accept."""
    cleaned = "".join(" " if ch in _BAD_FILENAME else ch for ch in text)
    cleaned = " ".join(cleaned.split())          # collapse runs of whitespace
    cleaned = cleaned.strip(" .")                # Windows drops these anyway
    if len(cleaned) > 60:
        cleaned = cleaned[:60].rstrip(" .")
    return cleaned or fallback


def _raw_part_name(part, index: int) -> str:
    """The most human name a part can offer, best source first."""
    for candidate in (getattr(part, "partName", None),
                      getattr(part, "partAbbreviation", None)):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    try:
        instruments = part.getElementsByClass("Instrument")
        for inst in instruments:
            for attr in ("partName", "instrumentName", "bestName"):
                value = getattr(inst, attr, None)
                if callable(value):
                    value = value()
                if value and str(value).strip() and str(value).strip() != "None":
                    return str(value).strip()
    except Exception:  # noqa: BLE001  - naming must never break an export
        pass
    return f"Part {index + 1}"


def part_labels(sc: stream.Score) -> list[str]:
    """One distinct, filename-safe label per part, in score order.

    Audiveris names parts when the page does ("Violin I", "Piano"); when it
    cannot, or when two parts carry the same name, the label falls back to or
    is suffixed with the part's position, so no two files collide.
    """
    parts = list(sc.parts)
    raw = [safe_filename(_raw_part_name(p, i), f"Part {i + 1}")
           for i, p in enumerate(parts)]
    counts: dict[str, int] = {}
    for name in raw:
        key = name.casefold()
        counts[key] = counts.get(key, 0) + 1
    seen: dict[str, int] = {}
    labels: list[str] = []
    for i, name in enumerate(raw):
        key = name.casefold()
        if counts[key] > 1:
            seen[key] = seen.get(key, 0) + 1
            labels.append(f"{name} {seen[key]}")
        else:
            labels.append(name)
    return labels


def score_for_part(sc: stream.Score, index: int, label: str = "") -> stream.Score:
    """A one-part score, carrying the original's title and metadata.

    A copy, so the caller can hand it to a writer without the full score
    being touched.
    """
    import copy  # noqa: PLC0415

    from music21 import metadata as m21metadata  # noqa: PLC0415

    parts = list(sc.parts)
    if not 0 <= index < len(parts):
        raise ExportError(f"There is no part {index + 1} in this score.")
    out = stream.Score()
    source_md = getattr(sc, "metadata", None)
    md = copy.deepcopy(source_md) if source_md is not None else m21metadata.Metadata()
    if label:
        # MuseScore and Verovio print this under the title, which is exactly
        # where a player expects to see which part they are holding.
        md.movementName = label
    out.insert(0, md)
    out.insert(0, copy.deepcopy(parts[index]))
    return out


def write_part_midis(sc: stream.Score, out_dir: Path, basename: str,
                     log: LogFn | None = None) -> tuple[list[Path], list[str]]:
    """One .mid per part. Returns (written, problems)."""
    written: list[Path] = []
    problems: list[str] = []
    labels = part_labels(sc)
    for i, label in enumerate(labels):
        target = out_dir / f"{basename} - {label}.mid"
        try:
            write_midi(score_for_part(sc, i, label), target)
        except ExportError as exc:
            problems.append(f"MIDI for {label}: {exc}")
            continue
        written.append(target)
        if log:
            log(f"Wrote {target.name}")
    return written, problems


def write_part_pdfs(sc: stream.Score, out_dir: Path, basename: str,
                    musescore: Path | None = None,
                    log: LogFn | None = None) -> tuple[list[Path], list[str]]:
    """One engraved .pdf per part. Returns (written, problems)."""
    written: list[Path] = []
    problems: list[str] = []
    labels = part_labels(sc)
    for i, label in enumerate(labels):
        target = out_dir / f"{basename} - {label}.pdf"
        try:
            path, engraver = write_pdf(score_for_part(sc, i, label), target,
                                       musescore, log)
        except ExportError as exc:
            problems.append(f"PDF for {label}: {exc}")
            continue
        written.append(path)
        if log:
            log(f"Wrote {path.name} (engraved by {engraver})")
    return written, problems


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def export_all(
    sc: stream.Score,
    out_dir: Path,
    basename: str,
    want_pdf: bool = True,
    want_midi: bool = True,
    want_musicxml: bool = False,
    want_parts: bool = False,
    scan_images: Sequence | None = None,
    want_scan_pdf: bool = False,
    musescore: Path | None = None,
    log: LogFn | None = None,
) -> ExportReport:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = ExportReport()

    if want_musicxml:
        p = write_musicxml(sc, out_dir / f"{basename}.musicxml")
        report.written.append(p)
        if log:
            log(f"Wrote {p.name}")

    if want_midi:
        try:
            p = write_midi(sc, out_dir / f"{basename}.mid")
            report.written.append(p)
            if log:
                log(f"Wrote {p.name}")
        except ExportError as exc:
            report.skipped.append(f"MIDI: {exc}")

    if want_pdf:
        try:
            p, engraver = write_pdf(sc, out_dir / f"{basename}.pdf", musescore, log)
            report.written.append(p)
            report.engraver = engraver
            if log:
                log(f"Wrote {p.name} (engraved by {engraver})")
        except ExportError as exc:
            report.skipped.append(f"PDF: {exc}")

    if want_parts:
        count = len(list(sc.parts))
        if count < 2:
            report.skipped.append(
                "Individual parts: this score has only one part, so the files "
                "would duplicate the full score."
            )
        else:
            if log:
                log(f"Saving {count} individual parts")
            if want_midi:
                files, problems = write_part_midis(sc, out_dir, basename, log)
                report.written.extend(files)
                report.skipped.extend(problems)
            if want_pdf:
                files, problems = write_part_pdfs(sc, out_dir, basename,
                                                  musescore, log)
                report.written.extend(files)
                report.skipped.extend(problems)
            if not want_midi and not want_pdf:
                report.skipped.append(
                    "Individual parts: tick PDF or MIDI to say what the part "
                    "files should be."
                )

    if want_scan_pdf and scan_images:
        try:
            p = write_scan_pdf(scan_images, out_dir / f"{basename} - scan.pdf")
            report.written.append(p)
            if log:
                log(f"Wrote {p.name}")
        except Exception as exc:  # noqa: BLE001
            report.skipped.append(f"Scan PDF: {exc}")

    return report
