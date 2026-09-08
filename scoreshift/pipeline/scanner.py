"""Scanning straight from a printer/scanner using Windows Image Acquisition.

Everything here is Windows-only and degrades to a clear message elsewhere.
"""
from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

LogFn = Callable[[str], None]

IS_WINDOWS = sys.platform.startswith("win")

# WIA format GUIDs
FMT_PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"
FMT_JPEG = "{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}"

# WIA property IDs
WIA_IPS_CUR_INTENT = 6146
WIA_IPS_XRES = 6147
WIA_IPS_YRES = 6148
WIA_IPS_XPOS = 6149
WIA_IPS_YPOS = 6150
WIA_IPS_XEXTENT = 6151
WIA_IPS_YEXTENT = 6152
WIA_IPA_DATATYPE = 4103
WIA_IPA_DEPTH = 4104

WIA_DPS_DOCUMENT_HANDLING_SELECT = 3088
WIA_DPS_DOCUMENT_HANDLING_STATUS = 3087
WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES = 3086
WIA_DPS_PAGES = 3096
WIA_DPS_HORIZONTAL_BED_SIZE = 3074      # thousandths of an inch
WIA_DPS_VERTICAL_BED_SIZE = 3075

FEEDER = 0x001
FLATBED = 0x002
FEED_READY = 0x001

INTENTS = {"Color": 1, "Grayscale": 2, "Black and white": 4}


class ScannerError(RuntimeError):
    pass


@dataclass
class ScannerInfo:
    device_id: str
    name: str
    has_feeder: bool = False
    has_flatbed: bool = True


# ---------------------------------------------------------------------------
# COM plumbing
# ---------------------------------------------------------------------------

def _com():
    if not IS_WINDOWS:
        raise ScannerError(
            "Scanning from a printer works on Windows only. "
            "On this machine, use 'Add images...' instead."
        )
    try:
        import pythoncom  # noqa: PLC0415
        import win32com.client  # noqa: PLC0415
    except ImportError as exc:  # noqa: BLE001
        raise ScannerError(
            "Scanner support needs the pywin32 package.\n"
            "Install it with:  pip install pywin32"
        ) from exc
    pythoncom.CoInitialize()
    return win32com.client


def _prop(props, pid: int):
    for p in props:
        try:
            if int(p.PropertyID) == int(pid):
                return p
        except Exception:  # noqa: BLE001
            continue
    return None


def _get(props, pid: int, default=None):
    p = _prop(props, pid)
    if p is None:
        return default
    try:
        return p.Value
    except Exception:  # noqa: BLE001
        return default


def _set(props, pid: int, value, log: LogFn | None = None) -> bool:
    p = _prop(props, pid)
    if p is None:
        return False
    try:
        p.Value = value
        return True
    except Exception as exc:  # noqa: BLE001
        if log:
            log(f"  (scanner rejected setting {pid} = {value}: {exc})")
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_scanners() -> list[ScannerInfo]:
    client = _com()
    mgr = client.Dispatch("WIA.DeviceManager")
    found: list[ScannerInfo] = []
    for i in range(1, mgr.DeviceInfos.Count + 1):
        info = mgr.DeviceInfos(i)
        try:
            if int(info.Type) != 1:      # 1 = ScannerDeviceType
                continue
        except Exception:  # noqa: BLE001
            pass
        name = "Scanner"
        dev_id = ""
        try:
            name = str(_get(info.Properties, 7, None) or info.Properties("Name").Value)
        except Exception:  # noqa: BLE001
            pass
        try:
            dev_id = str(info.DeviceID)
        except Exception:  # noqa: BLE001
            continue
        caps = 0
        try:
            dev = info.Connect()
            caps = int(_get(dev.Properties, WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES, 0) or 0)
        except Exception:  # noqa: BLE001
            caps = 0
        found.append(ScannerInfo(
            device_id=dev_id, name=name,
            has_feeder=bool(caps & FEEDER), has_flatbed=bool(caps & FLATBED) or caps == 0,
        ))
    return found


def acquire(
    out_dir: Path,
    device_id: str | None = None,
    dpi: int = 300,
    mode: str = "Grayscale",
    source: str = "Flatbed",
    max_pages: int = 20,
    log: LogFn | None = None,
) -> list[Path]:
    """Scan one or more pages and return the saved image files."""
    client = _com()
    out_dir.mkdir(parents=True, exist_ok=True)

    mgr = client.Dispatch("WIA.DeviceManager")
    device = None
    if device_id:
        for i in range(1, mgr.DeviceInfos.Count + 1):
            info = mgr.DeviceInfos(i)
            try:
                if str(info.DeviceID) == device_id:
                    device = info.Connect()
                    break
            except Exception:  # noqa: BLE001
                continue
    if device is None:
        scanners = list_scanners()
        if not scanners:
            raise ScannerError(
                "No scanner was found.\n"
                "Check that the printer is switched on, connected, and that its "
                "Windows scanner driver (WIA) is installed."
            )
        for i in range(1, mgr.DeviceInfos.Count + 1):
            info = mgr.DeviceInfos(i)
            if str(info.DeviceID) == scanners[0].device_id:
                device = info.Connect()
                break
    if device is None:
        raise ScannerError("Could not connect to the scanner.")

    use_feeder = source.lower().startswith("feed")
    if use_feeder:
        _set(device.Properties, WIA_DPS_DOCUMENT_HANDLING_SELECT, FEEDER, log)
    else:
        _set(device.Properties, WIA_DPS_DOCUMENT_HANDLING_SELECT, FLATBED, log)

    bed_w = int(_get(device.Properties, WIA_DPS_HORIZONTAL_BED_SIZE, 8500) or 8500)
    bed_h = int(_get(device.Properties, WIA_DPS_VERTICAL_BED_SIZE, 11000) or 11000)

    saved: list[Path] = []
    page = 0
    while page < max_pages:
        page += 1
        item = device.Items(1)
        props = item.Properties
        _set(props, WIA_IPS_CUR_INTENT, INTENTS.get(mode, 2), log)
        _set(props, WIA_IPS_XRES, dpi, log)
        _set(props, WIA_IPS_YRES, dpi, log)
        _set(props, WIA_IPS_XPOS, 0, log)
        _set(props, WIA_IPS_YPOS, 0, log)
        _set(props, WIA_IPS_XEXTENT, int(bed_w * dpi / 1000), log)
        _set(props, WIA_IPS_YEXTENT, int(bed_h * dpi / 1000), log)
        if mode == "Grayscale":
            _set(props, WIA_IPA_DEPTH, 8, log)
        elif mode == "Color":
            _set(props, WIA_IPA_DEPTH, 24, log)

        if log:
            log(f"Scanning page {page} at {dpi} DPI ({mode}, {source})...")
        try:
            image = item.Transfer(FMT_PNG)
        except Exception as exc:  # noqa: BLE001
            if page == 1:
                raise ScannerError(f"The scan failed: {exc}") from exc
            if log:
                log(f"Feeder finished after {page - 1} page(s).")
            break

        path = out_dir / f"scan_{uuid.uuid4().hex[:8]}_p{page}.png"
        try:
            image.SaveFile(str(path))
        except Exception as exc:  # noqa: BLE001
            raise ScannerError(f"Could not save the scanned page: {exc}") from exc
        saved.append(path)
        if log:
            log(f"Saved {path.name}")

        if not use_feeder:
            break
        status = int(_get(device.Properties, WIA_DPS_DOCUMENT_HANDLING_STATUS, 0) or 0)
        if not (status & FEED_READY):
            if log:
                log("Feeder is empty.")
            break

    if not saved:
        raise ScannerError("The scanner returned no pages.")
    return saved


def acquire_with_dialog(out_dir: Path, log: LogFn | None = None) -> list[Path]:
    """Fall back to the scanner's own Windows dialog."""
    client = _com()
    out_dir.mkdir(parents=True, exist_ok=True)
    dialog = client.Dispatch("WIA.CommonDialog")
    try:
        image = dialog.ShowAcquireImage(1, 0, 0x00040000, FMT_PNG, False, True, False)
    except Exception as exc:  # noqa: BLE001
        raise ScannerError(f"The Windows scan dialog was cancelled or failed: {exc}") from exc
    if image is None:
        raise ScannerError("No page was scanned.")
    path = out_dir / f"scan_{uuid.uuid4().hex[:8]}.png"
    image.SaveFile(str(path))
    if log:
        log(f"Saved {path.name}")
    return [path]
