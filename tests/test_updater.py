"""Update checking and downloading, against a local stand-in for GitHub."""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import socketserver
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scoreshift import updater  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)
SERVE = OUT / "fake_github"
SERVE.mkdir(exist_ok=True)

passed, failed = 0, 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))


# ---------------------------------------------------------------------------
print("\n== Comparing versions ==")
cases = [
    ("1.3", "1.2", True), ("1.2", "1.2", False), ("1.2", "1.3", False),
    ("v1.10", "1.9", True), ("2.0", "1.99", True), ("1.2.1", "1.2", True),
    ("1.2", "1.2.1", False), ("v1.2.0", "1.2", False),
]
for remote, local, want in cases:
    check(f"{remote} newer than {local} = {want}",
          updater.is_newer(remote, local) is want)
check("junk versions do not crash", updater.is_newer("", "1.2") is False)

# ---------------------------------------------------------------------------
print("\n== Serving a pretend release ==")
INSTALLER = SERVE / "ScoreShift-Setup-9.9.exe"
INSTALLER.write_bytes(b"MZ" + os.urandom(3 * 1024 * 1024))
REAL_SHA = hashlib.sha256(INSTALLER.read_bytes()).hexdigest()
(SERVE / "SHA256SUMS.txt").write_text(
    f"{REAL_SHA}  ScoreShift-Setup-9.9.exe\n"
    "0000000000000000000000000000000000000000000000000000000000000000  other.zip\n")

def build_release_json(PORT):
    return {
        "tag_name": "v9.9",
        "name": "ScoreShift 9.9",
        "body": "## What's new\n- Faster page detection\n- Fixed a crash",
        "html_url": f"http://127.0.0.1:{PORT}/release",
        "published_at": "2026-09-08T12:00:00Z",
        "assets": [
            {"name": "SHA256SUMS.txt", "size": 200,
             "browser_download_url": f"http://127.0.0.1:{PORT}/SHA256SUMS.txt"},
            {"name": "ScoreShift-Setup-9.9.exe", "size": INSTALLER.stat().st_size,
             "browser_download_url": f"http://127.0.0.1:{PORT}/ScoreShift-Setup-9.9.exe"},
            {"name": "source.zip", "size": 10,
             "browser_download_url": f"http://127.0.0.1:{PORT}/source.zip"},
        ],
    }


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SERVE), **kwargs)

    def log_message(self, *args):  # quiet
        return

    def handle_error(self, *args):
        # The cancel test hangs up mid-transfer on purpose.
        return


socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.3)
(SERVE / "latest.json").write_text(json.dumps(build_release_json(PORT)))
(SERVE / "empty.json").write_text(json.dumps({"tag_name": "v0.1", "assets": []}))

base = f"http://127.0.0.1:{PORT}"
updater.API = base + "/{repo}.json"          # point the checker at our server

release = updater.fetch_latest("latest")
check("release fetched", release is not None)
check("tag and version read", release.tag == "v9.9" and release.version == "9.9",
      f"{release.tag} -> {release.version}")
check("release notes read", "Faster page detection" in release.notes)
check("publish date read", release.published == "2026-09-08", release.published)
check("the installer asset is the one picked",
      release.installer_name == "ScoreShift-Setup-9.9.exe", release.installer_name)
check("size reported in MB", release.size_mb == 3.0, f"{release.size_mb} MB")
check("checksums asset found", release.checksums_url.endswith("SHA256SUMS.txt"))
check("a release with no installer is handled",
      updater.fetch_latest("empty").has_installer is False)

print("\n== The checksum line for our file ==")
check("the right hash is picked out of the file",
      updater.expected_sha256(release) == REAL_SHA,
      updater.expected_sha256(release)[:16] + "...")

print("\n== Downloading ==")
seen: list[tuple[int, int]] = []
target = OUT / "downloaded-setup.exe"
digest = updater.download(release.installer_url, target, progress=lambda a, b: seen.append((a, b)))
check("file arrived", target.exists() and target.stat().st_size == INSTALLER.stat().st_size,
      f"{target.stat().st_size // 1024} KB")
check("hash matches what was published", digest == REAL_SHA)
check("progress was reported", len(seen) >= 2 and seen[-1][0] == seen[-1][1],
      f"{len(seen)} updates, last {seen[-1]}")
check("hashing the file again agrees", updater.sha256_of(target) == REAL_SHA)

print("\n== A tampered download is refused ==")
(SERVE / "SHA256SUMS.txt").write_text("dead" * 16 + "  ScoreShift-Setup-9.9.exe\n")
bad_release = updater.fetch_latest("latest")
expected = updater.expected_sha256(bad_release)
check("the published hash now disagrees with the file", expected != REAL_SHA)
check("a mismatch is detectable before running anything",
      updater.sha256_of(target) != expected)

print("\n== Cancelling ==")
cancelled = OUT / "cancelled.exe"
try:
    updater.download(release.installer_url, cancelled, cancel=lambda: True)
    check("cancel raises", False)
except updater.UpdateError as exc:
    check("cancel raises", "cancel" in str(exc).lower(), str(exc))
check("a cancelled download leaves no file behind", not cancelled.exists())

print("\n== When GitHub is unhappy ==")
updater.API = base + "/does-not-exist-{repo}.json"
check("a missing releases endpoint means 'no releases yet'",
      updater.fetch_latest("x") is None)
updater.API = "http://127.0.0.1:9/{repo}"     # nothing listening
try:
    updater.fetch_latest("x", timeout=2)
    check("an unreachable GitHub is reported clearly", False)
except updater.UpdateError as exc:
    check("an unreachable GitHub is reported clearly", "reach" in str(exc).lower(),
          str(exc)[:60])

print("\n== Throttling ==")
check("first ever check is due", updater.due_for_check(0.0))
check("a check an hour ago is not due", not updater.due_for_check(time.time() - 3600))
check("a check two days ago is due", updater.due_for_check(time.time() - 48 * 3600))

httpd.shutdown()
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
