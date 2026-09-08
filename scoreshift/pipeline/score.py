"""Score inspection and transposition, built on music21."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from music21 import converter, interval, key, meter, note, pitch, stream, tempo

# Keys offered in the UI, in circle-of-fifths order.
MAJOR_KEYS = ["C", "G", "D", "A", "E", "B", "F#", "C#",
              "F", "Bb", "Eb", "Ab", "Db", "Gb", "Cb"]
MINOR_KEYS = ["a", "e", "b", "f#", "c#", "g#", "d#", "a#",
              "d", "g", "c", "f", "bb", "eb", "ab"]

# name -> written interval away from concert pitch
INSTRUMENT_PRESETS: dict[str, str] = {
    "Concert pitch (no change)": "P1",
    "B-flat instrument (trumpet, clarinet, tenor sax)": "M2",
    "E-flat instrument (alto sax, alto clarinet)": "M6",
    "F instrument (french horn, english horn)": "P5",
    "A clarinet": "m3",
    "Bass clef 8vb (double bass, guitar)": "P8",
}


class ScoreError(RuntimeError):
    pass


@dataclass
class ScoreInfo:
    detected_key: str = ""            # from harmonic analysis
    notated_key: str = ""             # from the written key signature
    sharps: int | None = None
    time_signature: str = ""
    parts: int = 0
    measures: int = 0
    notes: int = 0
    tempo_bpm: float | None = None
    lowest: str = ""
    highest: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def best_key(self) -> str:
        return self.notated_key or self.detected_key


# ---------------------------------------------------------------------------
# Loading / inspection
# ---------------------------------------------------------------------------

def load(path: str | Path) -> stream.Score:
    try:
        sc = converter.parse(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ScoreError(f"Could not read the score file: {exc}") from exc
    if isinstance(sc, stream.Opus) and len(sc.scores):
        sc = sc.scores[0]
    if not isinstance(sc, stream.Score):
        holder = stream.Score()
        holder.append(sc)
        sc = holder
    return sc


def _first_key_signature(sc: stream.Score) -> key.KeySignature | None:
    for el in sc.recurse().getElementsByClass(key.KeySignature):
        return el
    return None


def inspect(sc: stream.Score) -> ScoreInfo:
    info = ScoreInfo()
    ks = _first_key_signature(sc)
    if ks is not None:
        info.sharps = ks.sharps
        k = ks if isinstance(ks, key.Key) else ks.asKey("major")
        info.notated_key = key_display(k)
    try:
        info.detected_key = key_display(sc.analyze("key"))
    except Exception:  # noqa: BLE001
        info.warnings.append("Could not analyse the key from the notes.")

    ts = sc.recurse().getElementsByClass(meter.TimeSignature)
    if ts:
        info.time_signature = ts[0].ratioString
    mm = sc.recurse().getElementsByClass(tempo.MetronomeMark)
    if mm:
        try:
            info.tempo_bpm = float(mm[0].getQuarterBPM())
        except Exception:  # noqa: BLE001
            pass

    parts = list(sc.parts) if hasattr(sc, "parts") else []
    info.parts = len(parts)
    try:
        info.measures = len(parts[0].getElementsByClass(stream.Measure)) if parts else 0
    except Exception:  # noqa: BLE001
        info.measures = 0

    pitches = [n.pitch for n in sc.recurse().notes if isinstance(n, note.Note)]
    for ch in sc.recurse().getElementsByClass("Chord"):
        pitches.extend(ch.pitches)
    info.notes = len(list(sc.recurse().notes))
    if pitches:
        lo = min(pitches, key=lambda p: p.ps)
        hi = max(pitches, key=lambda p: p.ps)
        info.lowest = lo.nameWithOctave.replace("-", "b")
        info.highest = hi.nameWithOctave.replace("-", "b")
    if info.notes == 0:
        info.warnings.append("No notes were found in this score.")
    return info


# ---------------------------------------------------------------------------
# Transposition
# ---------------------------------------------------------------------------

def _key_obj(name: str) -> key.Key:
    """'Bb' / 'bb' / 'F# minor' -> music21 Key."""
    txt = name.strip()
    mode = None
    low = txt.lower()
    if low.endswith(" minor") or low.endswith("m"):
        mode = "minor"
        txt = txt[: -6] if low.endswith(" minor") else txt[:-1]
    elif low.endswith(" major"):
        mode = "major"
        txt = txt[:-6]
    txt = txt.strip()
    tonic = txt[0].upper() + txt[1:].replace("b", "-")
    if mode is None:
        mode = "minor" if txt[0].islower() else "major"
    return key.Key(tonic, mode)


def key_display(k: key.Key) -> str:
    return f"{k.tonic.name.replace('-', 'b')} {k.mode}"


def interval_between_keys(src: key.Key, dst: key.Key,
                          direction: str = "nearest") -> interval.Interval:
    """Spelled interval from one tonic to another, in the chosen direction.

    Building the interval from two *spelled* pitches is what keeps the
    result diatonically correct (D -> F is a minor third, not 3 semitones).
    """
    start = note.Note(src.tonic.name + "4")
    ranges = {
        "up": (0, 11),
        "down": (-11, 0),
        "nearest": (-6, 6),
    }
    lo, hi = ranges.get(direction, ranges["nearest"])
    best: interval.Interval | None = None
    for octv in (2, 3, 4, 5, 6):
        end = note.Note(dst.tonic.name + str(octv))
        itv = interval.Interval(noteStart=start, noteEnd=end)
        s = itv.semitones
        if not (lo <= s <= hi):
            continue
        if best is None or abs(s) < abs(best.semitones):
            best = itv
    if best is None:  # e.g. identical tonics with direction="up"
        best = interval.Interval(
            noteStart=start, noteEnd=note.Note(dst.tonic.name + "4")
        )
    return best


def _shift_octaves(base: interval.Interval, octaves: int) -> interval.Interval:
    start = note.Note("C4")
    end = start.transpose(base)
    end = end.transpose(interval.Interval(12 * octaves))
    return interval.Interval(noteStart=start, noteEnd=end)


def enharmonically_simpler(k: key.Key) -> key.Key | None:
    """Db major instead of C# major, etc.  None when the key is already fine."""
    try:
        sharps = k.sharps
    except Exception:  # noqa: BLE001
        return None
    if sharps is None or abs(sharps) <= 6:
        return None
    alt_tonic = k.tonic.getEnharmonic()
    try:
        alt = key.Key(alt_tonic.name, k.mode)
    except Exception:  # noqa: BLE001
        return None
    if alt.sharps is not None and abs(alt.sharps) < abs(sharps):
        return alt
    return None


def _clean_double_accidentals(sc: stream.Score) -> int:
    """Respell only the notes that ended up with double sharps/flats."""
    fixed = 0
    for n in sc.recurse().notes:
        plist = [n.pitch] if isinstance(n, note.Note) else list(n.pitches)
        for p in plist:
            if p.accidental is not None and abs(p.accidental.alter) >= 2:
                simple = p.simplifyEnharmonic(inPlace=False, mostCommon=True)
                p.step, p.accidental, p.octave = simple.step, simple.accidental, simple.octave
                fixed += 1
    return fixed


@dataclass
class TransposeResult:
    score: stream.Score
    interval_name: str
    semitones: int
    from_key: str
    to_key: str
    respelled: int = 0
    notes: list[str] = field(default_factory=list)


def transpose_to_key(
    sc: stream.Score,
    target_key: str,
    source_key: str | None = None,
    direction: str = "nearest",
    simplify: bool = True,
    extra_octaves: int = 0,
) -> TransposeResult:
    """Move the whole score into ``target_key``."""
    info = inspect(sc)
    src_name = source_key or info.best_key
    if not src_name:
        raise ScoreError("The source key could not be determined - "
                         "pick one manually or transpose by interval instead.")
    src = _key_obj(src_name)
    dst = _key_obj(target_key)
    notes: list[str] = []

    if simplify:
        simpler = enharmonically_simpler(dst)
        if simpler is not None:
            notes.append(f"Wrote {key_display(simpler)} instead of "
                         f"{key_display(dst)} - same sounds, fewer accidentals.")
            dst = simpler

    itv = interval_between_keys(src, dst, direction=direction)
    if extra_octaves:
        itv = _shift_octaves(itv, extra_octaves)

    out = sc.transpose(itv, inPlace=False)
    if out is None:
        raise ScoreError("Transposition failed.")
    respelled = _clean_double_accidentals(out) if simplify else 0
    if respelled:
        notes.append(f"Respelled {respelled} note(s) that landed on double accidentals.")

    return TransposeResult(
        score=out, interval_name=itv.niceName, semitones=int(itv.semitones),
        from_key=key_display(src), to_key=key_display(dst),
        respelled=respelled, notes=notes,
    )


def transpose_by_interval(
    sc: stream.Score,
    semitones: int,
    prefer: str = "auto",
    simplify: bool = True,
) -> TransposeResult:
    """Move the score by a fixed number of half steps."""
    info = inspect(sc)
    itv = interval.Interval(int(semitones))
    out = sc.transpose(itv, inPlace=False)
    if out is None:
        raise ScoreError("Transposition failed.")
    respelled = _clean_double_accidentals(out) if simplify else 0
    new_info = inspect(out)
    return TransposeResult(
        score=out, interval_name=itv.niceName, semitones=int(semitones),
        from_key=info.best_key or "?", to_key=new_info.best_key or "?",
        respelled=respelled,
    )


def transpose_for_instrument(
    sc: stream.Score,
    preset: str,
    to_written: bool = True,
    simplify: bool = True,
) -> TransposeResult:
    """Concert pitch <-> a transposing instrument's written part."""
    name = INSTRUMENT_PRESETS.get(preset, "P1")
    itv = interval.Interval(name)
    if not to_written:
        itv = itv.reverse()
    info = inspect(sc)
    out = sc.transpose(itv, inPlace=False)
    if out is None:
        raise ScoreError("Transposition failed.")
    respelled = _clean_double_accidentals(out) if simplify else 0
    new_info = inspect(out)
    return TransposeResult(
        score=out, interval_name=itv.niceName, semitones=int(itv.semitones),
        from_key=info.best_key or "?", to_key=new_info.best_key or "?",
        respelled=respelled,
    )


def clone(sc: stream.Score) -> stream.Score:
    return copy.deepcopy(sc)
