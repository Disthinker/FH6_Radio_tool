from __future__ import annotations

import traceback
from typing import Callable, Any

from PySide6.QtCore import QObject, Signal, Slot


class BackgroundTask(QObject):
    """Small Qt worker used to keep long operations off the UI thread."""

    progress = Signal(int, str)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, func: Callable[[Callable[[int, str], None]], Any]):
        super().__init__()
        self._func = func

    @Slot()
    def run(self) -> None:
        try:
            def report(value: int, message: str = "") -> None:
                try:
                    self.progress.emit(max(0, min(100, int(value))), str(message or ""))
                except Exception:
                    pass

            result = self._func(report)
            self.succeeded.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            self.finished.emit()
