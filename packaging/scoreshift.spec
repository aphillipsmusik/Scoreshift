# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for ScoreShift.

Builds a self-contained folder (dist/ScoreShift) holding ScoreShift.exe and
everything it needs - Python included.  Inno Setup then wraps that folder in
ScoreShift-Setup.exe.

    pyinstaller packaging/scoreshift.spec --noconfirm
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent          # noqa: F821 - SPECPATH is injected
ENTRY = str(ROOT / "scoreshift" / "__main__.py")
ICON = ROOT / "assets" / "scoreshift.ico"


def _slash(path: str) -> str:
    return path.replace("\\", "/")


# music21 ships a large score corpus we never touch - leave it out.
music21_data = [
    (src, dest) for src, dest in collect_data_files("music21")
    if "/corpus/" not in _slash(src) and not _slash(src).endswith("/corpus")
]

datas = []
datas += music21_data
datas += collect_data_files("verovio")          # Bravura/Leipzig fonts, etc.
if ICON.exists():
    datas.append((str(ICON), "assets"))

hiddenimports = ["scoreshift", "scoreshift.ui.main_window"]
hiddenimports += collect_submodules("scoreshift")
hiddenimports += collect_submodules("music21")  # music21 imports a lot lazily

# The API server is optional: only bundle it if it is installed at build time.
try:
    import fastapi  # noqa: F401
    import uvicorn  # noqa: F401
except ImportError:
    HAS_SERVER = False
else:
    HAS_SERVER = True
    hiddenimports += ["scoreshift.service", "scoreshift.service.app"]
    hiddenimports += collect_submodules("uvicorn")
    hiddenimports += ["fastapi", "pydantic", "multipart", "python_multipart"]

excludes = [
    "tkinter", "matplotlib", "scipy", "pandas", "IPython", "notebook",
    "PyQt5", "PyQt6", "pytest",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtQuick3D",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtTest",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSql",
]

a = Analysis(                                   # noqa: F821
    [ENTRY],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)                               # noqa: F821

exe = EXE(                                      # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ScoreShift",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                              # windowed; --server opens its own
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
)

coll = COLLECT(                                 # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ScoreShift",
)
