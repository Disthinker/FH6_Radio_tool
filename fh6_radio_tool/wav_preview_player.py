from __future__ import annotations

import wave
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSink


class WavPreviewPlayer(QObject):
    """Small PCM16 WAV player for sample-accurate preview/marker editing.

    QMediaPlayer may report or seek WAV duration inaccurately on some Windows
    backends.  For marker editing we need the slider to mean real WAV sample
    positions, so this class streams PCM frames directly through QAudioSink.
    """

    positionChanged = Signal(int)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path: Path | None = None
        self._wf: wave.Wave_read | None = None
        self._sink: QAudioSink | None = None
        self._device = None
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._pump)

        self._samplerate = 48000
        self._channels = 2
        self._sample_width = 2
        self._total_frames = 0
        self._position = 0
        self._playing = False
        self._paused = False
        self._eof = False
        self._drain_ticks = 0
        self._range_end: int | None = None
        self._loop_start: int | None = None
        self._loop_end: int | None = None
        self._loop_enabled = False

    @property
    def samplerate(self) -> int:
        return self._samplerate

    @property
    def total_frames(self) -> int:
        return self._total_frames

    def current_sample(self) -> int:
        return max(0, min(int(self._position), max(0, self._total_frames - 1)))

    def set_source(self, path: Path) -> None:
        self.stop(emit_position=False)
        self._path = Path(path)
        with wave.open(str(self._path), "rb") as wf:
            self._channels = int(wf.getnchannels())
            self._sample_width = int(wf.getsampwidth())
            self._samplerate = int(wf.getframerate())
            self._total_frames = int(wf.getnframes())

        if self._sample_width != 2:
            raise ValueError(f"试听播放器只支持 16-bit PCM WAV：{path}")

        self._position = 0
        self.clear_range()
        self.positionChanged.emit(0)

    def play(self) -> None:
        if self._path is None:
            return

        if self._playing and self._paused and self._sink is not None:
            self._sink.resume()
            self._paused = False
            self._timer.start()
            return

        if self._playing:
            return

        self._start_stream_at(self._position)

    def pause(self) -> None:
        if self._sink is not None and self._playing:
            self._sink.suspend()
            self._paused = True
        self._timer.stop()

    def stop(self, emit_position: bool = True) -> None:
        self._timer.stop()

        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception:
                pass
        self._sink = None
        self._device = None

        if self._wf is not None:
            try:
                self._wf.close()
            except Exception:
                pass
        self._wf = None

        self._playing = False
        self._paused = False
        self._eof = False
        self._drain_ticks = 0
        self._range_end = None
        self._loop_start = None
        self._loop_end = None
        self._loop_enabled = False
        self._position = 0

        if emit_position:
            self.positionChanged.emit(0)

    def seek_sample(self, sample: int) -> None:
        sample = max(0, min(int(sample), max(0, self._total_frames - 1)))
        was_playing = self._playing and not self._paused

        if self._playing:
            self._close_stream_only()

        self.clear_range()
        self._position = sample
        self.positionChanged.emit(self._position)

        if was_playing:
            self._start_stream_at(self._position)

    def _close_stream_only(self) -> None:
        self._timer.stop()

        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception:
                pass
        self._sink = None
        self._device = None

        if self._wf is not None:
            try:
                self._wf.close()
            except Exception:
                pass
        self._wf = None

        self._playing = False
        self._paused = False
        self._eof = False
        self._drain_ticks = 0

    def play_range(self, start_sample: int, end_sample: int | None = None, *, loop: bool = False, loop_start_sample: int | None = None) -> None:
        """Play a bounded sample range.  If loop=True, jump to loop_start_sample or start_sample at end_sample."""
        if self._path is None:
            return
        total_last = max(0, self._total_frames - 1)
        start = max(0, min(int(start_sample), total_last))
        end = None if end_sample is None else max(start + 1, min(int(end_sample), total_last))
        self._range_end = end
        loop_start = start if loop_start_sample is None else max(0, min(int(loop_start_sample), total_last))
        self._loop_start = loop_start
        self._loop_end = end
        self._loop_enabled = bool(loop and end is not None and end > loop_start)
        self._position = start
        self.positionChanged.emit(self._position)
        self._start_stream_at(self._position)

    def clear_range(self) -> None:
        self._range_end = None
        self._loop_start = None
        self._loop_end = None
        self._loop_enabled = False

    def _start_stream_at(self, sample: int) -> None:
        if self._path is None:
            return

        self._close_stream_only()

        self._wf = wave.open(str(self._path), "rb")
        self._wf.setpos(max(0, min(int(sample), max(0, self._total_frames - 1))))

        fmt = QAudioFormat()
        fmt.setSampleRate(self._samplerate)
        fmt.setChannelCount(self._channels)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

        self._sink = QAudioSink(fmt, self)
        # Keep a small but safe buffer.  Too large makes seeking feel delayed.
        self._sink.setBufferSize(max(4096, int(self._samplerate * self._channels * 2 * 0.20)))
        self._device = self._sink.start()

        self._playing = True
        self._paused = False
        self._eof = False
        self._drain_ticks = 0
        self._timer.start()
        self._pump()

    def _finish_after_drain(self) -> None:
        # Stop after allowing QAudioSink a short time to drain its internal buffer.
        self._drain_ticks += 1
        if self._sink is None:
            return

        # After about 0.5s at 20ms/tick, assume the last buffered audio played.
        if self._drain_ticks >= 25:
            last = max(0, self._total_frames - 1)
            self._close_stream_only()
            self._position = last
            self.positionChanged.emit(last)
            self.finished.emit()

    def _pump(self) -> None:
        if not self._playing or self._paused or self._sink is None or self._device is None or self._wf is None:
            return

        if self._eof:
            self._finish_after_drain()
            return

        frame_bytes = max(1, self._channels * self._sample_width)
        bytes_free = max(0, int(self._sink.bytesFree()))
        if bytes_free < frame_bytes:
            return

        # Cap each pump to keep UI responsive.
        frames_to_read = max(1, min(4096, bytes_free // frame_bytes))
        data = self._wf.readframes(frames_to_read)

        if not data:
            self._eof = True
            self.positionChanged.emit(max(0, self._total_frames - 1))
            return

        written = int(self._device.write(data))
        if written < 0:
            written = 0
        frames_written = written // frame_bytes
        self._position = max(0, min(self._position + frames_written, max(0, self._total_frames - 1)))
        self.positionChanged.emit(self._position)

        if self._range_end is not None and self._position >= self._range_end:
            if self._loop_enabled and self._loop_start is not None:
                self._close_stream_only()
                self._position = self._loop_start
                self.positionChanged.emit(self._position)
                self._start_stream_at(self._position)
                return
            self._eof = True
            return

        if self._position >= max(0, self._total_frames - 1):
            self._eof = True
