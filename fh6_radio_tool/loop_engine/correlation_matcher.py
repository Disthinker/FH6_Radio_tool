from __future__ import annotations

"""Internal loop matching engine for FH6 Radio Tool.

v2.2.1 note:
The first v2.2 preview searched long songs at original sample resolution.  That
can be billions of Python operations and freezes the Qt UI when run on the main
thread.  This module now performs the broad search on a decimated waveform and
only does a small local refinement around promising anchors.  The output is
still expressed in original sample positions for XML markers.
"""

import math
from array import array
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable
import wave

from ..wav_tools import read_wav_info

ProgressCallback = Callable[[int, str], None] | None


@dataclass(frozen=True)
class SmartMatchResult:
    sample: int
    score: float
    method: str
    window_samples: int
    search_radius_samples: int

    def to_json(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SmartLoopCandidate:
    loop_start: int
    loop_end: int
    score: float
    source: str = "internal_smart_match_fast"
    label: str = "Smart Match"
    details: dict | None = None

    def to_json(self) -> dict:
        return asdict(self)


def _emit(progress_callback: ProgressCallback, value: int, message: str) -> None:
    if progress_callback:
        progress_callback(value, message)


def _read_pcm16_mono_decimated(
    path: Path,
    target_points: int = 24000,
    progress_callback: ProgressCallback = None,
    progress_start: int = 0,
    progress_end: int = 25,
) -> tuple[list[float], int, int, float]:
    """Read a 16-bit PCM WAV as mono and decimate it to a bounded point count.

    Returns: (points, samplerate, original_frames, samples_per_point_scale).
    """
    info = read_wav_info(path)
    if info.bits_per_sample != 16:
        raise ValueError(f"Smart Match 当前只支持 16-bit PCM WAV: {path}")
    if info.frames <= 0:
        raise ValueError(f"空 WAV: {path}")

    stride = max(1, int(math.ceil(info.frames / max(1, target_points))))
    points: list[float] = []
    processed = 0
    last_report = -1

    with wave.open(str(path), "rb") as wf:
        channels = max(1, wf.getnchannels())
        frame_index = 0
        block_frames = 16384
        while True:
            raw = wf.readframes(block_frames)
            if not raw:
                break
            samples = array("h")
            samples.frombytes(raw)
            frame_count = len(samples) // channels
            for fi in range(frame_count):
                if frame_index % stride == 0:
                    base = fi * channels
                    s = 0.0
                    for ch in range(channels):
                        s += float(samples[base + ch])
                    points.append(s / channels / 32768.0)
                frame_index += 1
            processed += frame_count
            pct = progress_start + int((progress_end - progress_start) * min(processed, info.frames) / max(1, info.frames))
            if pct != last_report and pct % 2 == 0:
                last_report = pct
                _emit(progress_callback, pct, f"读取并降采样 WAV... {min(processed, info.frames)}/{info.frames} samples")

    if points:
        mean = sum(points) / len(points)
        # A light non-linear compression makes texture more important than peaks.
        points = [math.tanh((x - mean) * 2.0) for x in points]
    scale = info.frames / max(1, len(points))
    return points, int(info.samplerate), int(info.frames), float(scale)


def _read_pcm16_mono(path: Path, max_frames: int | None = None) -> tuple[list[float], int]:
    """Compatibility helper for manual Smart Match calls.

    This still reads the requested range at full resolution, so the UI never
    calls it for broad automatic analysis.
    """
    info = read_wav_info(path)
    if info.bits_per_sample != 16:
        raise ValueError(f"Smart Match 当前只支持 16-bit PCM WAV: {path}")
    frames_to_read = info.frames if max_frames is None else min(info.frames, max_frames)
    out: list[float] = []
    with wave.open(str(path), "rb") as wf:
        channels = max(1, wf.getnchannels())
        remaining = frames_to_read
        while remaining > 0:
            raw = wf.readframes(min(8192, remaining))
            if not raw:
                break
            samples = array("h")
            samples.frombytes(raw)
            frame_count = len(samples) // channels
            for i in range(frame_count):
                base = i * channels
                out.append(sum(float(samples[base + ch]) for ch in range(channels)) / channels / 32768.0)
            remaining -= frame_count
    if out:
        mean = sum(out) / len(out)
        out = [math.tanh((x - mean) * 2.2) for x in out]
    return out, info.samplerate


def _norm_corr(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 16:
        return 0.0
    ma = sum(a[:n]) / n
    mb = sum(b[:n]) / n
    num = 0.0
    da = 0.0
    db = 0.0
    for i in range(n):
        xa = a[i] - ma
        xb = b[i] - mb
        num += xa * xb
        da += xa * xa
        db += xb * xb
    if da <= 1e-12 or db <= 1e-12:
        return 0.0
    return num / math.sqrt(da * db)


def _window(data: list[float], start: int, length: int) -> list[float]:
    if not data:
        return []
    start = max(0, min(int(start), max(0, len(data) - 1)))
    end = max(start, min(len(data), start + int(length)))
    return data[start:end]


def _best_match(data: list[float], fingerprint_start: int, search_start: int, search_end: int, window_samples: int, step: int, method: str, radius: int) -> SmartMatchResult:
    fingerprint = _window(data, fingerprint_start, window_samples)
    if len(fingerprint) < max(16, window_samples // 3):
        raise ValueError("用于匹配的指纹窗口太短，请调整 marker 或 window 参数。")
    search_start = max(0, min(search_start, len(data) - len(fingerprint)))
    search_end = max(search_start + 1, min(search_end, len(data) - len(fingerprint)))
    step = max(1, int(step))
    best_sample = search_start
    best_score = -1.0
    for pos in range(search_start, search_end + 1, step):
        score = _norm_corr(fingerprint, _window(data, pos, len(fingerprint)))
        if score > best_score:
            best_score = score
            best_sample = pos
    fine_start = max(search_start, best_sample - step * 2)
    fine_end = min(search_end, best_sample + step * 2)
    for pos in range(fine_start, fine_end + 1):
        score = _norm_corr(fingerprint, _window(data, pos, len(fingerprint)))
        if score > best_score:
            best_score = score
            best_sample = pos
    return SmartMatchResult(int(best_sample), float(best_score), method, int(window_samples), int(radius))


def reverse_match_start(path: Path, loop_end_sample: int, approximate_start_sample: int, window_seconds: float = 1.0, search_radius_seconds: float = 4.0) -> SmartMatchResult:
    data, sr = _read_pcm16_mono(Path(path))
    win = max(256, int(window_seconds * sr))
    radius = max(1, int(search_radius_seconds * sr))
    fingerprint_start = max(0, int(loop_end_sample) - win)
    center = max(0, int(approximate_start_sample))
    return _best_match(data, fingerprint_start, center - radius, center + radius, win, max(1, sr // 100), "reverse_match_start", radius)


def forward_match_end(path: Path, loop_start_sample: int, approximate_end_sample: int, window_seconds: float = 1.0, search_radius_seconds: float = 4.0) -> SmartMatchResult:
    data, sr = _read_pcm16_mono(Path(path))
    win = max(256, int(window_seconds * sr))
    radius = max(1, int(search_radius_seconds * sr))
    fingerprint_start = max(0, int(loop_start_sample))
    center = max(0, int(approximate_end_sample))
    return _best_match(data, fingerprint_start, center - radius, center + radius, win, max(1, sr // 100), "forward_match_end", radius)


def _local_refine(data: list[float], s: int, e: int, win: int, start_radius: int, end_radius: int) -> tuple[int, int, float]:
    n = len(data)
    s0 = max(0, min(s, n - win - 1))
    e0 = max(s0 + win, min(e, n - win - 1))
    best_s = s0
    best_s_score = -1.0
    end_fp = _window(data, e0, win)
    for ss in range(max(0, s0 - start_radius), min(n - win - 1, s0 + start_radius) + 1):
        score = _norm_corr(_window(data, ss, win), end_fp)
        if score > best_s_score:
            best_s_score = score
            best_s = ss

    best_e = e0
    best_e_score = -1.0
    start_fp = _window(data, best_s, win)
    for ee in range(max(best_s + win, e0 - end_radius), min(n - win - 1, e0 + end_radius) + 1):
        score = _norm_corr(start_fp, _window(data, ee, win))
        if score > best_e_score:
            best_e_score = score
            best_e = ee
    return best_s, best_e, (best_s_score + best_e_score) / 2.0


def analyze_smart_loop_candidates(
    path: Path,
    top_n: int = 8,
    min_loop_seconds: float = 20.0,
    window_seconds: float = 1.25,
    progress_callback: ProgressCallback = None,
) -> list[SmartLoopCandidate]:
    """Generate loop candidates quickly and safely for GUI use.

    The search is deliberately approximate: it produces candidate ranges for
    preview and manual fine-tuning, not an unreviewed final answer.
    """
    path = Path(path)
    _emit(progress_callback, 3, "开始内置 Smart Match：读取 WAV 信息...")
    data, sr, frames, scale = _read_pcm16_mono_decimated(path, target_points=24000, progress_callback=progress_callback, progress_start=4, progress_end=24)
    n = len(data)
    if n < 200 or frames < sr * 8:
        return []

    win = max(24, min(max(32, int(window_seconds * sr / max(1.0, scale))), max(32, n // 80)))
    min_gap = max(int(min_loop_seconds * sr / max(1.0, scale)), win * 2)
    if min_gap >= n:
        min_gap = max(win * 2, int(n * 0.35))

    start_min = int(n * 0.03)
    start_max = int(n * 0.48)
    end_min = max(int(n * 0.52), start_min + min_gap)
    end_max = min(n - win - 1, int(n * 0.985))
    if start_max <= start_min or end_max <= end_min:
        return []

    target_starts = 80
    target_ends = 110
    start_step = max(1, (start_max - start_min) // target_starts)
    end_step = max(1, (end_max - end_min) // target_ends)
    starts = list(range(start_min, start_max, start_step))

    _emit(progress_callback, 26, f"粗筛候选：约 {len(starts)} × {target_ends} 次相关比较。")
    anchors: list[tuple[float, int, int]] = []
    for idx, s in enumerate(starts):
        fp = _window(data, s, win)
        if len(fp) < win // 2:
            continue
        best_e = None
        best_score = -1.0
        e0 = max(end_min, s + min_gap)
        for e in range(e0, end_max, end_step):
            score = _norm_corr(fp, _window(data, e, win))
            tail = max(0, n - e)
            tail_bonus = min(0.03, tail / max(1, n) * 0.06)
            length_bonus = min(0.05, (e - s) / max(1, n) * 0.06)
            score2 = score + tail_bonus + length_bonus
            if score2 > best_score:
                best_score = score2
                best_e = e
        if best_e is not None and best_score > 0.08:
            anchors.append((best_score, s, best_e))
        if idx % max(1, len(starts) // 20) == 0:
            pct = 26 + int(44 * idx / max(1, len(starts)))
            _emit(progress_callback, pct, f"粗筛候选 {idx + 1}/{len(starts)}...")

    anchors.sort(reverse=True, key=lambda x: x[0])
    _emit(progress_callback, 72, f"粗筛完成，准备精修前 {min(len(anchors), max(top_n * 5, 16))} 个锚点。")

    out: list[SmartLoopCandidate] = []
    refine_items = anchors[:max(top_n * 5, 16)]
    for idx, (coarse_score, s, e) in enumerate(refine_items):
        try:
            rs, re, refine_score = _local_refine(data, s, e, win, start_radius=max(2, start_step * 2), end_radius=max(2, end_step * 2))
            loop_start = max(0, min(frames - 1, int(round(rs * scale))))
            loop_end = max(loop_start + 1, min(frames - 1, int(round(re * scale))))
            score = (float(coarse_score) + float(refine_score)) / 2.0
        except Exception:
            loop_start = max(0, min(frames - 1, int(round(s * scale))))
            loop_end = max(loop_start + 1, min(frames - 1, int(round(e * scale))))
            score = float(coarse_score)
        if any(abs(loop_start - c.loop_start) < sr * 2 and abs(loop_end - c.loop_end) < sr * 2 for c in out):
            continue
        out.append(SmartLoopCandidate(
            loop_start,
            loop_end,
            score,
            details={
                "coarse_score": float(coarse_score),
                "decimated_window_points": int(win),
                "scale_samples_per_point": float(scale),
            },
        ))
        _emit(progress_callback, 72 + int(18 * (idx + 1) / max(1, len(refine_items))), f"精修候选 {idx + 1}/{len(refine_items)}...")
        if len(out) >= top_n:
            break

    _emit(progress_callback, 92, f"内置 Smart Match 得到 {len(out)} 个候选。")
    return out
