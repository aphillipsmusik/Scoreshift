"""Application settings and external-tool discovery."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "ScoreShift"
IS_WINDOWS = sys.platform.startswith("win")


def app_data_dir() -> Path:
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_output_dir() -> Path:
    d = Path.home() / "Documents" / "ScoreShift"
    return d


def work_dir() -> Path:
    d = app_data_dir() / "work"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------
# Tool discovery
# --------------------------------------------------------------------------

_AUDIVERIS_CANDIDATES = [
    r"C:\Program Files\Audiveris\Audiveris.exe",
    r"C:\Program Files\Audiveris\bin\Audiveris.bat",
    r"C:\Program Files (x86)\Audiveris\Audiveris.exe",
]

_MUSESCORE_CANDIDATES = [
    r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
    r"C:\Program Files\MuseScore 4\bin\MuseScore4.com",
    r"C:\Program Files\MuseScore 3\bin\MuseScore3.exe",
    r"C:\Program Files (x86)\MuseScore 4\bin\MuseScore4.exe",
]


def _expand(patterns: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in patterns:
        out.append(Path(os.path.expandvars(p)))
    # User-local jpackage installs
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "Programs" / "Audiveris" / "Audiveris.exe")
        out.append(Path(local) / "Audiveris" / "Audiveris.exe")
    return out


def find_audiveris(explicit: str | None = None) -> Path | None:
    """Locate the Audiveris launcher (the OMR engine)."""
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
    for name in ("Audiveris", "audiveris", "Audiveris.bat", "audiveris.bat"):
        found = shutil.which(name)
        if found:
            return Path(found)
    for cand in _expand(_AUDIVERIS_CANDIDATES):
        if cand.exists():
            return cand
    # Bundled alongside the app (tools/audiveris/...)
    bundled = Path(__file__).resolve().parent.parent / "tools" / "audiveris"
    if bundled.exists():
        for pat in ("Audiveris.exe", "bin/Audiveris.bat", "bin/Audiveris"):
            c = bundled / pat
            if c.exists():
                return c
    return None


def find_musescore(explicit: str | None = None) -> Path | None:
    """Locate MuseScore, used to engrave MusicXML into PDF."""
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
    for name in ("MuseScore4", "MuseScore3", "mscore", "musescore", "musescore4"):
        found = shutil.which(name)
        if found:
            return Path(found)
    for cand in _expand(_MUSESCORE_CANDIDATES):
        if cand.exists():
            return cand
    return None


def find_java() -> Path | None:
    found = shutil.which("java")
    if found:
        return Path(found)
    jh = os.environ.get("JAVA_HOME")
    if jh:
        c = Path(jh) / "bin" / ("java.exe" if IS_WINDOWS else "java")
        if c.exists():
            return c
    return None


def probe_tool(path: Path, args: list[str], timeout: int = 60) -> tuple[bool, str]:
    """Run a tool with harmless args to confirm it launches."""
    try:
        proc = subprocess.run(
            [str(path), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=(subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0),
        )
        blob = (proc.stdout or "") + (proc.stderr or "")
        return True, blob.strip()[:2000]
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------
# Persisted settings
# --------------------------------------------------------------------------


@dataclass
class Settings:
    audiveris_path: str = ""
    musescore_path: str = ""
    output_dir: str = field(default_factory=lambda: str(default_output_dir()))
    scan_dpi: int = 300
    scan_mode: str = "Grayscale"          # Color | Grayscale | Black and white
    scan_source: str = "Flatbed"          # Flatbed | Feeder
    auto_crop: bool = True
    auto_deskew: bool = True
    enhance: bool = True
    keep_intermediates: bool = False
    last_input_dir: str = field(default_factory=lambda: str(Path.home()))
    omr_timeout_sec: int = 900
    # Updates
    auto_update_check: bool = True
    update_repo: str = "aphillipsmusik/Scoreshift"
    last_update_check: float = 0.0
    skipped_version: str = ""

    @classmethod
    def path(cls) -> Path:
        return app_data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        p = cls.path()
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                known = {f for f in cls.__dataclass_fields__}
                return cls(**{k: v for k, v in data.items() if k in known})
            except Exception:  # noqa: BLE001
                pass
        return cls()

    def save(self) -> None:
        try:
            self.path().write_text(
                json.dumps(asdict(self), indent=2), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass

    # Resolved tool paths -------------------------------------------------
    def audiveris(self) -> Path | None:
        return find_audiveris(self.audiveris_path or None)

    def musescore(self) -> Path | None:
        return find_musescore(self.musescore_path or None)
