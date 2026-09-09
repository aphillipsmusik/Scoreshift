"""Checking GitHub Releases for a newer ScoreShift, and installing it.

No Qt in here: the dialog in ``ui/update_dialog.py`` drives these functions,
and they are equally callable from a script or a test.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import __version__
from .config import IS_WINDOWS

DEFAULT_REPO = "aphillipsmusik/Scoreshift"
API = "https://api.github.com/repos/{repo}/releases/latest"
USER_AGENT = f"ScoreShift/{__version__} (+https://github.com/{DEFAULT_REPO})"
INSTALLER_PATTERN = re.compile(r"ScoreShift-Setup-.*\.exe$", re.IGNORECASE)
CHECKSUM_NAMES = ("SHA256SUMS.txt", "SHA256SUMS", "checksums.txt")

ProgressFn = Callable[[int, int], None]     # bytes so far, total (0 = unknown)
CancelFn = Callable[[], bool]


class UpdateError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' -> (1, 2, 3). Unparseable pieces become 0."""
    cleaned = (text or "").strip().lstrip("vV")
    cleaned = cleaned.split("+")[0].split("-")[0]
    parts: list[int] = []
    for chunk in cleaned.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(remote: str, local: str) -> bool:
    a, b = parse_version(remote), parse_version(local)
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    return a > b


# ---------------------------------------------------------------------------
# Talking to GitHub
# ---------------------------------------------------------------------------

@dataclass
class Release:
    tag: str = ""
    version: str = ""
    name: str = ""
    notes: str = ""
    html_url: str = ""
    published: str = ""
    installer_url: str = ""
    installer_name: str = ""
    installer_size: int = 0
    checksums_url: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def has_installer(self) -> bool:
        return bool(self.installer_url)

    @property
    def size_mb(self) -> float:
        return round(self.installer_size / (1024 * 1024), 1)


def _get(url: str, timeout: float = 15.0, accept: str = "application/vnd.github+json") -> bytes:
    request = urllib.request.Request(
        url, headers={"Accept": accept, "User-Agent": USER_AGENT})
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token and "api.github.com" in url:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_latest(repo: str = DEFAULT_REPO, timeout: float = 15.0) -> Release | None:
    """The newest published, non-prerelease release. None if there isn't one."""
    try:
        blob = _get(API.format(repo=repo), timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None                      # no releases published yet
        if exc.code in (403, 429):
            raise UpdateError(
                "GitHub is rate-limiting update checks right now. Try later."
            ) from exc
        raise UpdateError(f"GitHub returned {exc.code} checking for updates.") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"Could not reach GitHub: {exc.reason}") from exc
    except TimeoutError as exc:
        raise UpdateError("The update check timed out.") from exc

    try:
        data = json.loads(blob.decode("utf-8"))
    except ValueError as exc:
        raise UpdateError("GitHub sent something unreadable.") from exc

    release = Release(
        tag=data.get("tag_name", ""),
        version=data.get("tag_name", "").lstrip("vV"),
        name=data.get("name") or data.get("tag_name", ""),
        notes=(data.get("body") or "").strip(),
        html_url=data.get("html_url", ""),
        published=(data.get("published_at") or "")[:10],
        raw=data,
    )
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        if INSTALLER_PATTERN.search(name) and not release.installer_url:
            release.installer_url = asset.get("browser_download_url", "")
            release.installer_name = name
            release.installer_size = int(asset.get("size") or 0)
        elif name in CHECKSUM_NAMES and not release.checksums_url:
            release.checksums_url = asset.get("browser_download_url", "")
    return release


def expected_sha256(release: Release) -> str:
    """The published checksum for this release's installer, '' if none."""
    if not release.checksums_url or not release.installer_name:
        return ""
    try:
        text = _get(release.checksums_url, accept="text/plain").decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == release.installer_name:
            return parts[0].strip().lower()
    return ""


# ---------------------------------------------------------------------------
# Downloading and installing
# ---------------------------------------------------------------------------

def download(url: str, dest: Path, progress: ProgressFn | None = None,
             cancel: CancelFn | None = None, timeout: float = 60.0) -> str:
    """Stream a file down, returning its SHA-256."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with dest.open("wb") as handle:
                while True:
                    if cancel and cancel():
                        raise UpdateError("Download cancelled.")
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except UpdateError:
        dest.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise UpdateError(f"The download failed: {exc}") from exc
    return digest.hexdigest()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def launch_installer(installer: Path, silent: bool = True) -> None:
    """Start the downloaded installer. The app must exit straight after."""
    if not IS_WINDOWS:
        raise UpdateError("Installing an update only works on Windows.")
    if not installer.exists():
        raise UpdateError("The downloaded installer went missing.")
    args = [str(installer)]
    if silent:
        args += ["/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS", "/NORESTART"]
    try:
        subprocess.Popen(args, close_fds=True)
    except Exception as exc:  # noqa: BLE001
        raise UpdateError(f"Could not start the installer: {exc}") from exc


def running_frozen() -> bool:
    """True when this is the packaged app, not a source checkout."""
    return bool(getattr(sys, "frozen", False))


# ---------------------------------------------------------------------------
# The whole check, with throttling
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    release: Release | None = None
    available: bool = False
    current: str = __version__
    skipped_reason: str = ""


def check(repo: str = DEFAULT_REPO, current: str = __version__,
          timeout: float = 15.0) -> CheckResult:
    release = fetch_latest(repo, timeout=timeout)
    if release is None:
        return CheckResult(current=current, skipped_reason="No releases published yet.")
    return CheckResult(
        release=release,
        available=is_newer(release.version, current),
        current=current,
    )


def due_for_check(last_epoch: float, every_hours: float = 24.0) -> bool:
    if not last_epoch:
        return True
    return (time.time() - float(last_epoch)) >= every_hours * 3600
