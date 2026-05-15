from __future__ import annotations

import math
import shutil
import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LoudnessMatchResult:
    source_path: Path
    reference_path: Path
    output_path: Path
    source_dbfs: float | None
    reference_dbfs: float | None
    requested_gain_db: float
    applied_gain_db: float
    peak_limited: bool
    status: str


def _open_pcm16(path: Path):
    wf = wave.open(str(path), "rb")
    if wf.getsampwidth() != 2:
        wf.close()
        raise ValueError(f"只支持 16-bit PCM WAV: {path}")
    return wf


def _iter_samples_from_bytes(data: bytes):
    for i in range(0, len(data) - 1, 2):
        v = data[i] | (data[i + 1] << 8)
        if v >= 32768:
            v -= 65536
        yield v


def dbfs_active_rms(path: Path, block_frames: int = 4096, silence_gate_db: float = -50.0) -> tuple[float | None, int]:
    """Streaming active RMS dBFS for PCM16 WAV."""
    active_sum_sq = 0.0
    active_count = 0
    full_sum_sq = 0.0
    full_count = 0
    gate_linear = 10.0 ** (silence_gate_db / 20.0) * 32768.0

    with _open_pcm16(path) as wf:
        while True:
            data = wf.readframes(block_frames)
            if not data:
                break

            block_sum_sq = 0.0
            block_count = 0
            for sample in _iter_samples_from_bytes(data):
                block_sum_sq += float(sample) * float(sample)
                block_count += 1

            if block_count == 0:
                continue

            full_sum_sq += block_sum_sq
            full_count += block_count

            block_rms = math.sqrt(block_sum_sq / block_count)
            if block_rms >= gate_linear:
                active_sum_sq += block_sum_sq
                active_count += block_count

    if active_count > 0:
        rms = math.sqrt(active_sum_sq / active_count)
        count = active_count
    elif full_count > 0:
        rms = math.sqrt(full_sum_sq / full_count)
        count = full_count
    else:
        return None, 0

    if rms <= 0:
        return None, count
    return 20.0 * math.log10(rms / 32768.0), count


def _peak_abs_pcm16(path: Path, block_frames: int = 8192) -> int:
    peak = 0
    with _open_pcm16(path) as wf:
        while True:
            data = wf.readframes(block_frames)
            if not data:
                break
            for sample in _iter_samples_from_bytes(data):
                if abs(sample) > peak:
                    peak = abs(sample)
    return peak


def _gain_chunk(data: bytes, multiplier: float) -> bytes:
    out = bytearray(len(data))
    j = 0
    for sample in _iter_samples_from_bytes(data):
        value = int(round(sample * multiplier))
        if value > 32767:
            value = 32767
        elif value < -32768:
            value = -32768
        if value < 0:
            value += 65536
        out[j] = value & 0xFF
        out[j + 1] = (value >> 8) & 0xFF
        j += 2
    return bytes(out[:j])


def apply_gain_pcm16(source: Path, output: Path, gain_db: float, block_frames: int = 8192) -> tuple[float, bool]:
    """Streaming gain application with simple peak protection."""
    peak = _peak_abs_pcm16(source)
    requested_multiplier = 10.0 ** (gain_db / 20.0)
    peak_limited = False

    if peak > 0 and peak * requested_multiplier > 32760:
        multiplier = 32760.0 / peak
        peak_limited = True
    else:
        multiplier = requested_multiplier

    applied_gain_db = 20.0 * math.log10(multiplier) if multiplier > 0 else -120.0

    output.parent.mkdir(parents=True, exist_ok=True)
    with _open_pcm16(source) as src:
        channels = src.getnchannels()
        samplerate = src.getframerate()
        with wave.open(str(output), "wb") as dst:
            dst.setnchannels(channels)
            dst.setsampwidth(2)
            dst.setframerate(samplerate)

            while True:
                data = src.readframes(block_frames)
                if not data:
                    break
                dst.writeframes(_gain_chunk(data, multiplier))

    return applied_gain_db, peak_limited


def match_loudness_to_reference(
    source: Path,
    reference: Path,
    output: Path,
    max_boost_db: float = 12.0,
    max_cut_db: float = 18.0,
) -> LoudnessMatchResult:
    """Match source active RMS to reference active RMS.

    This is a lightweight local RMS matcher, not a full EBU R128 loudnorm pass.
    It is enough for making replacement radio tracks roughly align with
    original extracted assets.
    """
    try:
        source_db, _ = dbfs_active_rms(source)
        reference_db, _ = dbfs_active_rms(reference)

        if source_db is None or reference_db is None:
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, output)
            return LoudnessMatchResult(source, reference, output, source_db, reference_db, 0.0, 0.0, False, "copied_no_loudness")

        requested = reference_db - source_db
        requested_clamped = max(-max_cut_db, min(max_boost_db, requested))
        applied, limited = apply_gain_pcm16(source, output, requested_clamped)

        return LoudnessMatchResult(
            source_path=source,
            reference_path=reference,
            output_path=output,
            source_dbfs=source_db,
            reference_dbfs=reference_db,
            requested_gain_db=requested,
            applied_gain_db=applied,
            peak_limited=limited,
            status="matched",
        )
    except Exception:
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output)
        return LoudnessMatchResult(source, reference, output, None, None, 0.0, 0.0, False, "copied_error")
