"""'A new version is available' - and the download that follows."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox,
                               QHBoxLayout, QLabel, QMessageBox, QProgressBar,
                               QPushButton, QTextBrowser, QVBoxLayout)

from .. import __version__
from ..config import work_dir
from .. import updater


class CheckThread(QThread):
    """Asks GitHub what the latest release is."""

    finished_ok = Signal(object)      # updater.CheckResult
    failed = Signal(str)

    def __init__(self, repo: str, parent=None) -> None:
        super().__init__(parent)
        self.repo = repo

    def run(self) -> None:  # noqa: D102
        try:
            self.finished_ok.emit(updater.check(self.repo, current=__version__))
        except updater.UpdateError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"The update check failed: {exc}")


class DownloadThread(QThread):
    progress = Signal(int, int)
    finished_ok = Signal(str)         # path on disk
    failed = Signal(str)

    def __init__(self, release: updater.Release, parent=None) -> None:
        super().__init__(parent)
        self.release = release
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:  # noqa: D102
        try:
            target = work_dir() / "updates" / (self.release.installer_name or "update.exe")
            expected = updater.expected_sha256(self.release)
            digest = updater.download(
                self.release.installer_url, target,
                progress=self.progress.emit, cancel=lambda: self._cancel,
            )
            if expected and digest.lower() != expected.lower():
                target.unlink(missing_ok=True)
                self.failed.emit(
                    "The download did not match the checksum published with the "
                    "release, so it was deleted rather than run.")
                return
            self.finished_ok.emit(str(target))
        except updater.UpdateError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"The download failed: {exc}")


class UpdateDialog(QDialog):
    """Shows what's new and installs it on request."""

    skip_requested = Signal(str)      # version the user chose to skip

    def __init__(self, release: updater.Release, parent=None) -> None:
        super().__init__(parent)
        self.release = release
        self.download_thread: DownloadThread | None = None
        self.setWindowTitle("ScoreShift update")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)

        headline = QLabel(
            f"<b>ScoreShift {release.version} is available.</b><br>"
            f"You have {__version__}.")
        headline.setTextFormat(Qt.RichText)
        root.addWidget(headline)

        detail = []
        if release.published:
            detail.append(f"Released {release.published}")
        if release.installer_size:
            detail.append(f"{release.size_mb} MB download")
        if detail:
            line = QLabel(" · ".join(detail))
            line.setStyleSheet("color:#6b7280;")
            root.addWidget(line)

        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(release.notes or "_No release notes._")
        notes.setMinimumHeight(220)
        root.addWidget(notes, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        root.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#6b7280;")
        root.addWidget(self.status)

        buttons = QHBoxLayout()
        self.btn_install = QPushButton("Download and install")
        self.btn_install.setDefault(True)
        self.btn_install.clicked.connect(self.start_download)
        self.btn_skip = QPushButton("Skip this version")
        self.btn_skip.clicked.connect(self._skip)
        self.btn_later = QPushButton("Later")
        self.btn_later.clicked.connect(self.reject)
        self.btn_page = QPushButton("Open release page")
        self.btn_page.clicked.connect(self._open_page)
        buttons.addWidget(self.btn_page)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_skip)
        buttons.addWidget(self.btn_later)
        buttons.addWidget(self.btn_install)
        root.addLayout(buttons)

        if not release.has_installer:
            self.btn_install.setEnabled(False)
            self.status.setText(
                "This release has no Windows installer attached - use the "
                "release page.")
        elif not updater.running_frozen():
            self.btn_install.setEnabled(False)
            self.status.setText(
                "You're running from source, so update with git rather than "
                "the installer.")

    # -- actions ---------------------------------------------------------
    def _open_page(self) -> None:
        from PySide6.QtGui import QDesktopServices  # noqa: PLC0415
        from PySide6.QtCore import QUrl  # noqa: PLC0415
        if self.release.html_url:
            QDesktopServices.openUrl(QUrl(self.release.html_url))

    def _skip(self) -> None:
        self.skip_requested.emit(self.release.version)
        self.reject()

    def start_download(self) -> None:
        self.btn_install.setEnabled(False)
        self.btn_skip.setEnabled(False)
        self.progress.show()
        self.progress.setValue(0)
        self.status.setText("Downloading...")

        self.download_thread = DownloadThread(self.release, self)
        self.download_thread.progress.connect(self._on_progress)
        self.download_thread.finished_ok.connect(self._on_downloaded)
        self.download_thread.failed.connect(self._on_failed)
        self.download_thread.start()

    def _on_progress(self, done: int, total: int) -> None:
        if total:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(done * 100 / total))
            self.status.setText(
                f"Downloading... {done / 1048576:.0f} of {total / 1048576:.0f} MB")
        else:
            self.progress.setRange(0, 0)

    def _on_downloaded(self, path: str) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.status.setText("Verified. Starting the installer...")
        try:
            updater.launch_installer(Path(path))
        except updater.UpdateError as exc:
            self._on_failed(str(exc))
            return
        QMessageBox.information(
            self, "Installing",
            "ScoreShift will close while the update installs, then reopen.")
        self.accept()
        QApplication.quit()

    def _on_failed(self, message: str) -> None:
        self.progress.hide()
        self.status.setText("")
        self.btn_install.setEnabled(True)
        self.btn_skip.setEnabled(True)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Update")
        box.setText(message)
        box.exec()

    def closeEvent(self, event):  # noqa: D102, N802
        if self.download_thread is not None and self.download_thread.isRunning():
            self.download_thread.cancel()
            self.download_thread.wait(3000)
        super().closeEvent(event)
