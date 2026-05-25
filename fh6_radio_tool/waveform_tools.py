from __future__ import annotations

import hashlib
import json
import math
import wave
from array import array
from pathlib import Path


DEFAULT_WAVEFORM_BINS = 1000


def _cache_key(path: Path, bins: int) -> str:
    p = Path(path)
    try:
        st = p.stat()
        raw = f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}|{bins}"
    except Exception:
        raw = f"{p}|{bins}"
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:32]


def waveform_cache_path(cache_root: Path, path: Path, bins: int = DEFAULT_WAVEFORM_BINS) -> Path:
    return Path(cache_root) / f"{_cache_key(path, bins)}.json"


def load_or_build_waveform(path: Path, cache_root: Path, bins: int = DEFAULT_WAVEFORM_BINS) -> dict[str, object]:
    path = Path(path)
    cache_root = Path(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_path = waveform_cache_path(cache_root, path, bins)
    try:
        if cache_path.exists():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                data.get("source_path") == str(path.resolve())
                and int(data.get("bins") or 0) == int(bins)
                and int(data.get("size") or -1) == int(path.stat().st_size)
                and int(data.get("mtime") or -1) == int(path.stat().st_mtime)
            ):
                return data
    except Exception:
        pass

    data = build_waveform(path, bins=bins)
    try:
        st = path.stat()
        data.update({
            "source_path": str(path.resolve()),
            "size": int(st.st_size),
            "mtime": int(st.st_mtime),
        })
        cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return data


def build_waveform(path: Path, bins: int = DEFAULT_WAVEFORM_BINS) -> dict[str, object]:
    path = Path(path)
    with wave.open(str(path), "rb") as wf:
        channels = int(wf.getnchannels())
        sample_width = int(wf.getsampwidth())
        samplerate = int(wf.getframerate())
        total_frames = int(wf.getnframes())
        if sample_width != 2:
            raise ValueError("Waveform preview supports 16-bit PCM WAV files only.")
        if total_frames <= 0:
            return {
                "peaks": [],
                "bins": 0,
                "total_frames": 0,
                "samplerate": samplerate,
                "channels": channels,
            }

        target_bins = max(1, min(int(bins), total_frames))
        frames_per_bin = max(1, int(math.ceil(total_frames / float(target_bins))))
        peaks: list[float] = []
        max_amp = float((1 << 15) - 1)

        for _ in range(target_bins):
            data = wf.readframes(frames_per_bin)
            if not data:
                break
            samples = array("h")
            samples.frombytes(data)
            if samples.itemsize != 2:
                raise ValueError("Unsupported platform sample size for waveform preview.")
            peak = 0
            for value in samples:
                av = abs(int(value))
                if av > peak:
                    peak = av
            peaks.append(min(1.0, peak / max_amp))

    return {
        "peaks": peaks,
        "bins": len(peaks),
        "total_frames": total_frames,
        "samplerate": samplerate,
        "channels": channels,
    }
