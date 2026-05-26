from __future__ import annotations

import math
import wave
from array import array
from pathlib import Path

from .ffmpeg_tools import TARGET_GAME_DBFS
from .project_tools import project_work_dir


def volume_reference_wav_path() -> Path:
    path = project_work_dir() / "volume_reference" / "fh6_game_volume_reference_48k_stereo.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def ensure_volume_reference_wav(path: Path | None = None) -> Path:
    """Create a short 48 kHz stereo PCM16 loudness reference if missing."""
    target = Path(path) if path is not None else volume_reference_wav_path()
    if target.exists() and target.is_file():
        return target

    samplerate = 48000
    duration_sec = 1.25
    frames = int(round(samplerate * duration_sec))
    fade_frames = int(round(samplerate * 0.015))
    freqs = (220.0, 440.0, 880.0)
    raw: list[float] = []
    for i in range(frames):
        t = i / samplerate
        value = sum(math.sin(2.0 * math.pi * f * t) for f in freqs) / len(freqs)
        if i < fade_frames:
            value *= i / max(1, fade_frames)
        elif i > frames - fade_frames:
            value *= max(0.0, (frames - i) / max(1, fade_frames))
        raw.append(value)

    rms = math.sqrt(sum(v * v for v in raw) / max(1, len(raw))) or 1.0
    target_rms = 10.0 ** (TARGET_GAME_DBFS / 20.0)
    scale = min(0.95, target_rms / rms)

    samples = array("h")
    for value in raw:
        sample = int(max(-32767, min(32767, round(value * scale * 32767))))
        samples.append(sample)
        samples.append(sample)

    with wave.open(str(target), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(samples.tobytes())
    return target
