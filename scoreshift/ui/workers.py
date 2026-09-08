"""Background jobs so the window never freezes."""
from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal


class Job(QThread):
    """Runs one callable off the GUI thread.

    The callable receives ``log`` and ``cancelled`` helpers so long tasks can
    report progress and stop early.
    """

    log = Signal(str)
    step = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[..., Any], parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def cancelled(self) -> bool:
        return self._cancel

    def run(self) -> None:  # noqa: D102
        try:
            result = self._fn(self.log.emit, self.cancelled, self.step.emit)
        except Exception as exc:  # noqa: BLE001
            self.log.emit(traceback.format_exc())
            self.failed.emit(str(exc) or exc.__class__.__name__)
            return
        self.done.emit(result)
