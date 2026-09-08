"""Choose a scanner and how to scan."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QLabel, QSpinBox, QVBoxLayout)

from ..config import Settings
from ..pipeline.scanner import ScannerInfo


class ScanDialog(QDialog):
    def __init__(self, devices: list[ScannerInfo], settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Scan pages")
        self.setMinimumWidth(460)
        self.devices = devices

        root = QVBoxLayout(self)
        form = QFormLayout()

        self.cmb_device = QComboBox()
        for d in devices:
            extras = []
            if d.has_feeder:
                extras.append("feeder")
            if d.has_flatbed:
                extras.append("flatbed")
            self.cmb_device.addItem(f"{d.name}" + (f"  ({', '.join(extras)})" if extras else ""))
        form.addRow("Scanner:", self.cmb_device)

        self.cmb_source = QComboBox()
        self.cmb_source.addItems(["Flatbed", "Feeder"])
        self.cmb_source.setCurrentText(settings.scan_source)
        form.addRow("Paper source:", self.cmb_source)

        self.spin_dpi = QSpinBox()
        self.spin_dpi.setRange(150, 1200)
        self.spin_dpi.setSingleStep(50)
        self.spin_dpi.setValue(settings.scan_dpi)
        self.spin_dpi.setSuffix(" DPI")
        form.addRow("Resolution:", self.spin_dpi)

        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["Grayscale", "Color", "Black and white"])
        self.cmb_mode.setCurrentText(settings.scan_mode)
        form.addRow("Colour:", self.cmb_mode)

        self.spin_pages = QSpinBox()
        self.spin_pages.setRange(1, 100)
        self.spin_pages.setValue(20)
        self.spin_pages.setToolTip("Only used with a document feeder.")
        form.addRow("Max pages from feeder:", self.spin_pages)

        root.addLayout(form)

        self.chk_native = QCheckBox("Use the scanner's own Windows dialog instead")
        self.chk_native.setToolTip(
            "Handy if your printer needs its own settings screen.")
        root.addWidget(self.chk_native)

        hint = QLabel("300 DPI grayscale is the sweet spot for reading notes. "
                      "Flatten the page against the glass and close the lid.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6b7280;")
        root.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Scan")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @property
    def device_id(self) -> str:
        idx = self.cmb_device.currentIndex()
        return self.devices[idx].device_id if 0 <= idx < len(self.devices) else ""

    def apply_to(self, settings: Settings) -> None:
        settings.scan_source = self.cmb_source.currentText()
        settings.scan_dpi = self.spin_dpi.value()
        settings.scan_mode = self.cmb_mode.currentText()
        settings.save()
