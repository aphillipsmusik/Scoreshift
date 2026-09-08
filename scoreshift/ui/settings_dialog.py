"""Settings: where the OMR engine and the engraver live, and scan defaults."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSpinBox, QVBoxLayout)

from ..config import Settings, find_audiveris, find_musescore
from ..pipeline.omr import AUDIVERIS_DOWNLOAD, engine_status


class PathRow(QHBoxLayout):
    def __init__(self, value: str, placeholder: str, on_check) -> None:
        super().__init__()
        self.edit = QLineEdit(value)
        self.edit.setPlaceholderText(placeholder)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        check = QPushButton("Check")
        check.clicked.connect(lambda: on_check(self.edit.text().strip()))
        self.addWidget(self.edit, 1)
        self.addWidget(browse)
        self.addWidget(check)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            None, "Select program", self.edit.text() or str(Path.home()),
            "Programs (*.exe *.bat *.cmd);;All files (*)")
        if path:
            self.edit.setText(path)


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("ScoreShift settings")
        self.setMinimumWidth(640)

        root = QVBoxLayout(self)

        tools = QGroupBox("Programs ScoreShift uses")
        form = QFormLayout(tools)
        auto_a = find_audiveris()
        auto_m = find_musescore()
        self.audiveris = PathRow(settings.audiveris_path,
                                 str(auto_a) if auto_a else "Not found - install Audiveris",
                                 self._check_audiveris)
        self.musescore = PathRow(settings.musescore_path,
                                 str(auto_m) if auto_m else "Optional - improves PDF quality",
                                 self._check_musescore)
        form.addRow("Audiveris (note reading):", self.audiveris)
        form.addRow("MuseScore (PDF engraving):", self.musescore)
        hint = QLabel(
            "Leave blank to auto-detect. Audiveris is the free engine that reads "
            f"the notes off the page - <a href='{AUDIVERIS_DOWNLOAD}'>download it here</a>. "
            "Without MuseScore, PDFs are engraved by the built-in Verovio engine.")
        hint.setWordWrap(True)
        hint.setOpenExternalLinks(True)
        hint.setTextFormat(Qt.RichText)
        form.addRow(hint)
        root.addWidget(tools)

        scan = QGroupBox("Scanning defaults")
        sform = QFormLayout(scan)
        self.dpi = QSpinBox()
        self.dpi.setRange(150, 1200)
        self.dpi.setSingleStep(50)
        self.dpi.setValue(settings.scan_dpi)
        self.dpi.setSuffix(" DPI")
        self.mode = QComboBox()
        self.mode.addItems(["Grayscale", "Color", "Black and white"])
        self.mode.setCurrentText(settings.scan_mode)
        self.source = QComboBox()
        self.source.addItems(["Flatbed", "Feeder"])
        self.source.setCurrentText(settings.scan_source)
        sform.addRow("Resolution:", self.dpi)
        sform.addRow("Colour mode:", self.mode)
        sform.addRow("Paper source:", self.source)
        sform.addRow(QLabel("300 DPI grayscale reads best. Higher is slower, "
                            "not more accurate."))
        root.addWidget(scan)

        out = QGroupBox("Output")
        oform = QFormLayout(out)
        self.out_dir = QLineEdit(settings.output_dir)
        pick = QPushButton("Choose folder...")
        pick.clicked.connect(self._pick_dir)
        row = QHBoxLayout()
        row.addWidget(self.out_dir, 1)
        row.addWidget(pick)
        oform.addRow("Save files to:", row)
        self.timeout = QSpinBox()
        self.timeout.setRange(60, 7200)
        self.timeout.setSingleStep(60)
        self.timeout.setValue(settings.omr_timeout_sec)
        self.timeout.setSuffix(" seconds")
        oform.addRow("Give up transcribing after:", self.timeout)
        self.keep = QCheckBox("Keep intermediate files (for troubleshooting)")
        self.keep.setChecked(settings.keep_intermediates)
        oform.addRow(self.keep)
        root.addWidget(out)

        updates = QGroupBox("Updates")
        uform = QFormLayout(updates)
        self.auto_update = QCheckBox("Look for a new version when ScoreShift starts")
        self.auto_update.setChecked(settings.auto_update_check)
        self.auto_update.setToolTip(
            "One quiet check against the GitHub releases page, at most once a day.")
        uform.addRow(self.auto_update)
        self.repo = QLineEdit(settings.update_repo)
        self.repo.setPlaceholderText("owner/repository")
        uform.addRow("Release repository:", self.repo)
        if settings.skipped_version:
            forget = QPushButton(f"Stop skipping version {settings.skipped_version}")
            forget.clicked.connect(self._unskip)
            uform.addRow(forget)
        root.addWidget(updates)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # -- checks ----------------------------------------------------------
    def _check_audiveris(self, text: str) -> None:
        path = find_audiveris(text or None)
        ok, message = engine_status(path)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information if ok else QMessageBox.Warning)
        box.setWindowTitle("Audiveris")
        box.setText(("Found and working." if ok else "Not usable yet.")
                    + (f"\n\n{path}" if path else ""))
        box.setDetailedText(message)
        box.exec()

    def _check_musescore(self, text: str) -> None:
        path = find_musescore(text or None)
        box = QMessageBox(self)
        if path:
            box.setIcon(QMessageBox.Information)
            box.setText(f"MuseScore found:\n{path}")
        else:
            box.setIcon(QMessageBox.Information)
            box.setText("MuseScore not found. PDFs will be engraved by the "
                        "built-in Verovio engine instead, which is fine for "
                        "most scores.")
        box.setWindowTitle("MuseScore")
        box.exec()

    def _unskip(self) -> None:
        self.settings.skipped_version = ""
        self.settings.save()
        QMessageBox.information(self, "Updates",
                                "That version will be offered again.")

    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose output folder",
                                             self.out_dir.text())
        if d:
            self.out_dir.setText(d)

    # -- result ----------------------------------------------------------
    def apply_to(self, settings: Settings) -> None:
        settings.audiveris_path = self.audiveris.edit.text().strip()
        settings.musescore_path = self.musescore.edit.text().strip()
        settings.scan_dpi = self.dpi.value()
        settings.scan_mode = self.mode.currentText()
        settings.scan_source = self.source.currentText()
        settings.output_dir = self.out_dir.text().strip() or settings.output_dir
        settings.omr_timeout_sec = self.timeout.value()
        settings.keep_intermediates = self.keep.isChecked()
        settings.auto_update_check = self.auto_update.isChecked()
        settings.update_repo = self.repo.text().strip() or settings.update_repo
        settings.save()
