from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class WaveformWidget(QWidget):
    seekRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(86)
        self.setMouseTracking(True)
        self._peaks: list[float] = []
        self._total_samples = 0
        self._sample_rate = 48000
        self._position = 0
        self._markers: dict[str, int] = {}
        self._active_marker = ""
        self._message = "No waveform"

    def set_waveform(self, peaks: list[float], total_samples: int, sample_rate: int) -> None:
        self._peaks = [max(0.0, min(1.0, float(x))) for x in peaks]
        self._total_samples = max(0, int(total_samples))
        self._sample_rate = max(1, int(sample_rate or 48000))
        self._message = "" if self._peaks else "No waveform"
        self.update()

    def clear_waveform(self, message: str = "Waveform unavailable") -> None:
        self._peaks = []
        self._total_samples = 0
        self._position = 0
        self._markers = {}
        self._message = str(message or "Waveform unavailable")
        self.update()

    def set_position(self, sample: int) -> None:
        self._position = max(0, min(int(sample), max(0, self._total_samples - 1)))
        self.update()

    def set_markers(self, markers: dict[str, int], active_marker: str = "") -> None:
        self._markers = {str(k): int(v) for k, v in (markers or {}).items()}
        self._active_marker = str(active_marker or "")
        self.update()

    def _x_for_sample(self, sample: int) -> int:
        width = max(1, self.width() - 1)
        if self._total_samples <= 1:
            return 0
        return max(0, min(width, int(round(max(0, int(sample)) * width / float(self._total_samples - 1)))))

    def _sample_for_x(self, x: int) -> int:
        width = max(1, self.width() - 1)
        if self._total_samples <= 1:
            return 0
        ratio = max(0.0, min(1.0, float(x) / float(width)))
        return int(round(ratio * (self._total_samples - 1)))

    def _draw_region(self, painter: QPainter, start_name: str, end_name: str, color: QColor, active_color: QColor) -> None:
        start = int(self._markers.get(start_name, -1))
        end = int(self._markers.get(end_name, -1))
        if start < 0 or end < 0 or end <= start or self._total_samples <= 0:
            return
        active = self._active_marker in {start_name, end_name}
        x1 = self._x_for_sample(start)
        x2 = self._x_for_sample(end)
        painter.fillRect(min(x1, x2), 0, max(1, abs(x2 - x1)), self.height(), active_color if active else color)

    def _draw_marker_line(self, painter: QPainter, name: str, color: QColor) -> None:
        value = int(self._markers.get(name, -1))
        if value < 0 or self._total_samples <= 0:
            return
        x = self._x_for_sample(value)
        pen = QPen(color, 2 if self._active_marker == name else 1)
        painter.setPen(pen)
        painter.drawLine(x, 0, x, self.height())
        painter.drawText(x + 3, 13 if name == "TrackDrop" else 28, name)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(250, 250, 250))
        painter.setRenderHint(QPainter.Antialiasing, False)

        if not self._peaks:
            painter.setPen(QPen(QColor(110, 110, 110), 1))
            painter.drawText(self.rect(), Qt.AlignCenter, self._message)
            return

        self._draw_region(painter, "TrackLoopStart", "TrackLoopEnd", QColor(90, 160, 230, 42), QColor(90, 160, 230, 92))
        self._draw_region(painter, "PostRaceLoopStart", "PostRaceLoopEnd", QColor(65, 180, 120, 42), QColor(65, 180, 120, 92))

        mid = self.height() // 2
        painter.setPen(QPen(QColor(205, 205, 205), 1))
        painter.drawLine(0, mid, self.width(), mid)

        count = len(self._peaks)
        width = max(1, self.width())
        painter.setPen(QPen(QColor(70, 82, 96), 1))
        for i, peak in enumerate(self._peaks):
            x = int(i * width / float(max(1, count - 1)))
            h = max(1, int(float(peak) * (self.height() - 12) / 2.0))
            painter.drawLine(x, mid - h, x, mid + h)

        self._draw_marker_line(painter, "TrackDrop", QColor(214, 114, 22))
        self._draw_marker_line(painter, "PostDrop", QColor(150, 82, 190))

        x = self._x_for_sample(self._position)
        painter.setPen(QPen(QColor(220, 40, 40), 2))
        painter.drawLine(x, 0, x, self.height())

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.LeftButton and self._total_samples > 0:
            self.seekRequested.emit(self._sample_for_x(int(event.position().x())))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.buttons() & Qt.LeftButton and self._total_samples > 0:
            self.seekRequested.emit(self._sample_for_x(int(event.position().x())))
